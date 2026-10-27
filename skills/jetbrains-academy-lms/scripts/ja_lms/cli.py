"""Command line interface: ``ja-lms`` (or ``python -m ja_lms``).

Mutating commands are dry runs unless ``--apply`` is given.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Dict, List, Optional

from . import __version__
from .assignments import FRIENDLY_FIELDS, AssignmentFormError, AssignmentService
from .client import LMSClient, LMSError
from .credentials import (
    DEFAULT_BASE_URL,
    ChainCredentialProvider,
    Credentials,
    CredentialsError,
    EnvCredentialProvider,
    FileCredentialProvider,
)

COOKIE_HELP = (
    "Log in to the LMS in a browser, open DevTools > Application/Storage > Cookies "
    "for the LMS origin, and copy the values of `sessionid` and `csrftoken`."
)


def _prompt_credentials(base_url: str) -> Optional[Credentials]:
    if not sys.stdin.isatty():
        return None
    print("LMS session expired or missing. " + COOKIE_HELP, file=sys.stderr)
    sessionid = getpass.getpass("sessionid: ").strip()
    csrftoken = getpass.getpass("csrftoken: ").strip()
    if not sessionid or not csrftoken:
        return None
    return Credentials(sessionid, csrftoken, base_url)


def build_client(args) -> LMSClient:
    file_provider = FileCredentialProvider(args.credentials) if args.credentials else FileCredentialProvider()
    provider = ChainCredentialProvider([EnvCredentialProvider(), file_provider])

    def on_expired(client: LMSClient):
        if args.no_prompt:
            return None
        return _prompt_credentials(client.base_url)

    return LMSClient(provider, on_auth_expired=on_expired)


# -- auth -----------------------------------------------------------------------


def cmd_auth_set(args) -> int:
    provider = FileCredentialProvider(args.credentials) if args.credentials else FileCredentialProvider()
    if args.from_env:
        creds = EnvCredentialProvider().load()
        if creds is None:
            print("JA_LMS_SESSIONID / JA_LMS_CSRFTOKEN are not set", file=sys.stderr)
            return 2
        if args.base_url:
            creds = Credentials(creds.sessionid, creds.csrftoken, args.base_url)
    else:
        print(COOKIE_HELP, file=sys.stderr)
        sessionid = getpass.getpass("sessionid: ").strip()
        csrftoken = getpass.getpass("csrftoken: ").strip()
        creds = Credentials(sessionid, csrftoken, args.base_url or DEFAULT_BASE_URL)
    if not args.no_verify:
        client = LMSClient(provider, credentials=creds)
        client.whoami_ok()
        creds = client.credentials  # may include rotated cookies
    provider.save(creds)
    print(f"Saved credentials for {creds.base_url} to {provider.path} (mode 600).")
    return 0


def cmd_auth_status(args) -> int:
    env = EnvCredentialProvider().load()
    file_provider = FileCredentialProvider(args.credentials) if args.credentials else FileCredentialProvider()
    source = "environment" if env else (str(file_provider.path) if file_provider.load() else None)
    if source is None:
        print("No credentials configured. Run `ja-lms auth set`.")
        return 1
    args.no_prompt = True
    client = build_client(args)
    try:
        client.whoami_ok()
    except CredentialsError as exc:
        print(f"Credentials from {source} for {client.base_url}: REJECTED ({exc})")
        return 1
    print(f"Credentials from {source} for {client.base_url}: OK")
    return 0


def cmd_auth_clear(args) -> int:
    provider = FileCredentialProvider(args.credentials) if args.credentials else FileCredentialProvider()
    print("Removed " + str(provider.path) if provider.clear() else "Nothing to remove.")
    return 0


# -- reads ------------------------------------------------------------------------


def _out(args, data, table_rows=None, header=None) -> None:
    if args.json or table_rows is None:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
        return
    if header:
        print("\t".join(header))
    for row in table_rows:
        print("\t".join("" if v is None else str(v) for v in row))


def cmd_courses(args) -> int:
    courses = build_client(args).courses()
    _out(args, [c.__dict__ for c in courses], [(c.id, c.title, c.path) for c in courses], ["id", "title", "path"])
    return 0


def cmd_assignments(args) -> int:
    rows = build_client(args).assignments(args.course_id)
    _out(
        args,
        rows,
        [(a.get("id"), a.get("solutionFormat"), a.get("maximumScore"), a.get("weight"), a.get("deadlineAt"), a.get("title")) for a in rows],
        ["id", "format", "max", "weight", "deadline", "title"],
    )
    return 0


def cmd_assignment_show(args) -> int:
    svc = AssignmentService(build_client(args))
    _out(args, svc.read(args.assignment_id))
    return 0


def cmd_assignment_choices(args) -> int:
    if (args.course is None) == (args.assignment is None):
        print("pass exactly one of --course or --assignment", file=sys.stderr)
        return 2
    svc = AssignmentService(build_client(args))
    choices = svc.choices(course_id=args.course, assignment_id=args.assignment)
    if args.json:
        _out(args, choices)
        return 0
    for name, options in choices.items():
        print(name)
        for value, label in options:
            if value:
                print(f"  {value}\t{label}")
    return 0


# -- writes -----------------------------------------------------------------------


def _collect_changes(args) -> Dict[str, object]:
    changes = {k: getattr(args, k) for k in FRIENDLY_FIELDS if getattr(args, k, None) is not None}
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as fh:
            changes["text"] = fh.read()
    return changes


def _parse_raw(pairs: List[str]) -> Dict[str, str]:
    raw = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--raw expects NAME=VALUE, got {pair!r}")
        name, value = pair.split("=", 1)
        raw[name] = value
    return raw


def _run_plan(args, svc: AssignmentService, plan) -> int:
    print(plan.describe())
    if not args.apply:
        print("\nDry run: nothing was sent. Re-run with --apply to submit.")
        return 0
    if not plan.diff():
        print("Nothing to change.")
        return 0
    result = svc.apply(plan)
    print(f"\nSubmitted: HTTP {result.status}, assignment id {result.assignment_id}, redirect {result.location}")
    return 0


def cmd_assignment_add(args) -> int:
    svc = AssignmentService(build_client(args))
    plan = svc.plan_create(args.course_id, _collect_changes(args), _parse_raw(args.raw))
    return _run_plan(args, svc, plan)


def cmd_assignment_edit(args) -> int:
    svc = AssignmentService(build_client(args))
    plan = svc.plan_update(args.assignment_id, _collect_changes(args), _parse_raw(args.raw))
    return _run_plan(args, svc, plan)


def _add_field_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("assignment fields (unspecified fields keep their current/default values)")
    g.add_argument("--title")
    g.add_argument("--text", help="description (Markdown/HTML as the LMS editor accepts)")
    g.add_argument("--text-file", help="read the description from a file")
    g.add_argument("--submission-type", dest="submission_type", help="e.g. online, jba, github, penalty, other")
    g.add_argument("--maximum-score", dest="maximum_score")
    g.add_argument("--weight", help="0..1")
    g.add_argument("--ttc", help="time to complete, hours:minutes")
    g.add_argument("--opens-at", dest="opens_at", help="'YYYY-MM-DD HH:MM' in --time-zone")
    g.add_argument("--deadline-at", "--deadline", dest="deadline_at", help="'YYYY-MM-DD HH:MM' in --time-zone")
    g.add_argument("--time-zone", dest="time_zone", help="IANA name offered by the form, e.g. UTC")
    g.add_argument("--jba-course-id", dest="jba_course_id")
    g.add_argument("--github-template-repository", dest="github_template_repository", help="owner/repo")
    g.add_argument("--github-grader-repository", dest="github_grader_repository", help="owner/repo")
    g.add_argument("--github-grading-mode", dest="github_grading_mode", help="manual, sync, once")
    g.add_argument("--assignee-mode", dest="assignee_mode", help="off, manual, sg_default, sg_custom")
    g.add_argument("--responsible-teachers", dest="responsible_teachers", help="comma-separated teacher ids")
    g.add_argument("--raw", action="append", metavar="NAME=VALUE", help="set any other form field by its raw name")
    p.add_argument("--apply", action="store_true", help="actually submit (default: dry run)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ja-lms", description="JetBrains Academy LMS teaching client")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--credentials", default=os.environ.get("JA_LMS_CREDENTIALS"), help="credentials file path")
    p.add_argument("--no-prompt", action="store_true", help="never prompt for new cookies when the session expires")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="manage session credentials").add_subparsers(dest="auth_cmd", required=True)
    s = auth.add_parser("set", help="store or update session cookies (prompted, never echoed)")
    s.add_argument("--base-url", help=f"LMS origin (default {DEFAULT_BASE_URL})")
    s.add_argument("--from-env", action="store_true", help="copy JA_LMS_SESSIONID/JA_LMS_CSRFTOKEN into the file")
    s.add_argument("--no-verify", action="store_true", help="save without a test request")
    s.set_defaults(func=cmd_auth_set)
    auth.add_parser("status", help="check that stored credentials work").set_defaults(func=cmd_auth_status)
    auth.add_parser("clear", help="delete the credentials file").set_defaults(func=cmd_auth_clear)

    sub.add_parser("courses", help="courses you teach").set_defaults(func=cmd_courses)
    a = sub.add_parser("assignments", help="assignments in a course")
    a.add_argument("course_id", type=int)
    a.set_defaults(func=cmd_assignments)

    asg = sub.add_parser("assignment", help="inspect, add, or edit one assignment").add_subparsers(dest="asg_cmd", required=True)
    s = asg.add_parser("show", help="current form values of an assignment")
    s.add_argument("assignment_id", type=int)
    s.set_defaults(func=cmd_assignment_show)
    s = asg.add_parser("choices", help="allowed values for the form's selects")
    s.add_argument("--course", type=int)
    s.add_argument("--assignment", type=int)
    s.set_defaults(func=cmd_assignment_choices)
    s = asg.add_parser("add", help="create an assignment (dry run unless --apply)")
    s.add_argument("course_id", type=int)
    _add_field_options(s)
    s.set_defaults(func=cmd_assignment_add)
    s = asg.add_parser("edit", help="edit an assignment (dry run unless --apply)")
    s.add_argument("assignment_id", type=int)
    _add_field_options(s)
    s.set_defaults(func=cmd_assignment_edit)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AssignmentFormError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (CredentialsError, LMSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
