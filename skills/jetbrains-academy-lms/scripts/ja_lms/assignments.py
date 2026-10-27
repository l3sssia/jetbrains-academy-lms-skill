"""Create and edit LMS assignments through the teacher's add/edit forms.

There is no JSON write API for assignments; the UI posts a server-rendered
Django multipart form. Both operations follow the same safe pattern:

1. ``plan_create`` / ``plan_update`` GET the form, parse what a browser would
   submit (current values, CSRF token, formsets), apply the requested changes,
   validate them against the form's own choices, and return a ``Plan`` with a
   human-readable diff. Nothing is sent.
2. ``apply`` POSTs the plan. A ``302`` to ``/teaching/assignments/<id>/`` is
   success; a ``200`` re-render means validation failed and the form's error
   messages are returned.

Because the form is full-replace, untouched fields are always echoed back.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Mapping, Optional, Tuple, Union

from .client import LMSClient, LMSError, LMSHTTPError
from .forms import HTMLForm, encode_multipart, extract_form_errors, parse_form

PREFIX = "assignment-"
CSRF_FIELD = "csrfmiddlewaretoken"
DATE_FORMAT = "%d.%m.%Y"  # the form's "dd.mm.yyyy" placeholder
TIME_FORMAT = "%H:%M"  # the form's "hh:mm" placeholder

# Friendly name -> form field. Datetimes expand to the split _0 (date) / _1 (time) widgets.
SIMPLE_FIELDS = {
    "title": "assignment-title",
    "text": "assignment-text",
    "submission_type": "assignment-submission_type",
    "maximum_score": "assignment-maximum_score",
    "weight": "assignment-weight",
    "ttc": "assignment-ttc",
    "time_zone": "assignment-time_zone",
    "jba_course_id": "assignment-jba_course_id",
    "github_template_repository": "assignment-github_template_repository",
    "github_grader_repository": "assignment-github_grader_repository",
    "github_grading_mode": "assignment-github_grading_mode",
    "assignee_mode": "assignment-assignee_mode",
}
DATETIME_FIELDS = {"opens_at": "assignment-opens_at", "deadline_at": "assignment-deadline_at"}
LIST_FIELDS = {"responsible_teachers"}
FRIENDLY_FIELDS = sorted(set(SIMPLE_FIELDS) | set(DATETIME_FIELDS) | LIST_FIELDS)

RESPONSIBLE_TEACHER = re.compile(r"^responsible-teacher-(\d+)-active$")
ASSIGNMENT_LOCATION = re.compile(r"/teaching/assignments/(\d+)/?$")

ChangeValue = Union[str, int, float, Decimal, dt.datetime, Iterable[int], None]


class AssignmentFormError(LMSError):
    def __init__(self, message: str, errors: Optional[List[str]] = None) -> None:
        self.errors = errors or []
        super().__init__(message + ("".join(f"\n  - {e}" for e in self.errors)))


@dataclass
class Plan:
    action: str  # "create" | "update"
    form_url: str  # absolute path of the add/edit page
    form: HTMLForm
    original: Dict[str, object]
    assignment_id: Optional[int] = None
    course_id: Optional[int] = None

    def payload(self) -> List[Tuple[str, str]]:
        return self.form.payload()

    def diff(self) -> List[Tuple[str, object, object]]:
        """``(field, before, after)`` for every changed field, CSRF excluded."""
        changes = []
        for name in list(self.original) + [n for n in self.form.fields if n not in self.original]:
            if name == CSRF_FIELD:
                continue
            before, after = self.original.get(name), self.form.fields.get(name)
            if _norm(before) != _norm(after):
                changes.append((name, before, after))
        return changes

    def describe(self) -> str:
        head = (
            f"Create assignment in course {self.course_id}"
            if self.action == "create"
            else f"Update assignment {self.assignment_id}"
        )
        lines = [f"{head} ({self.form_url})"]
        diff = self.diff()
        if not diff:
            lines.append("  (no changes)")
        for name, before, after in diff:
            lines.append(f"  {name}: {_show(before)} -> {_show(after)}")
        return "\n".join(lines)


@dataclass
class ApplyResult:
    assignment_id: Optional[int]
    location: Optional[str]
    status: int
    changes: List[Tuple[str, object, object]] = field(default_factory=list)


class AssignmentService:
    def __init__(self, client: LMSClient) -> None:
        self.client = client

    # -- locating forms ------------------------------------------------------

    def add_form_path(self, course_id: int) -> str:
        return self.client.course_path(course_id) + "assignments/add"

    def edit_form_path(self, assignment_id: int) -> str:
        """Resolve the edit URL from the teacher's assignment page, which links
        to ``/courses/<term>/<id>-<slug>/assignments/<assignment_id>/edit``."""
        aid = int(assignment_id)
        page = self.client.get_html(f"/teaching/assignments/{aid}/")
        m = re.search(rf'href="(/courses/[^"]+/assignments/{aid}/edit)"', page)
        if not m:
            raise LMSError(f"no edit link for assignment {aid}; you may lack permission to edit it")
        return m.group(1)

    def _load_form(self, path: str) -> HTMLForm:
        page = self.client.get_html(path)
        try:
            return parse_form(page, has_field=SIMPLE_FIELDS["title"])
        except ValueError:
            raise LMSError(f"assignment form not found at {path}") from None

    def read(self, assignment_id: int) -> Dict[str, object]:
        """Current values of an assignment's edit form, in friendly names."""
        return friendly_values(self._load_form(self.edit_form_path(assignment_id)))

    def choices(self, *, course_id: Optional[int] = None, assignment_id: Optional[int] = None) -> Dict[str, List[Tuple[str, str]]]:
        """Allowed values for every select on the add/edit form."""
        path = self.edit_form_path(assignment_id) if assignment_id else self.add_form_path(course_id)
        form = self._load_form(path)
        out = {k: v.options for k, v in form.selects.items() if "__prefix__" not in k}
        teachers = sorted(int(m.group(1)) for n in form.checkboxes if (m := RESPONSIBLE_TEACHER.match(n)))
        if teachers:
            out["responsible_teachers"] = [(str(t), _teacher_label(form, t)) for t in teachers]
        return out

    # -- planning ------------------------------------------------------------

    def plan_create(self, course_id: int, changes: Mapping[str, ChangeValue], raw: Optional[Mapping[str, str]] = None) -> Plan:
        missing = [k for k in ("title", "deadline_at") if changes.get(k) in (None, "")]
        if missing:
            raise AssignmentFormError("required for a new assignment: " + ", ".join(missing))
        path = self.add_form_path(course_id)
        form = self._load_form(path)
        plan = Plan("create", path, form, dict(form.fields), course_id=int(course_id))
        apply_changes(form, changes, raw or {})
        return plan

    def plan_update(self, assignment_id: int, changes: Mapping[str, ChangeValue], raw: Optional[Mapping[str, str]] = None) -> Plan:
        path = self.edit_form_path(assignment_id)
        form = self._load_form(path)
        plan = Plan("update", path, form, dict(form.fields), assignment_id=int(assignment_id))
        apply_changes(form, changes, raw or {})
        return plan

    # -- applying ------------------------------------------------------------

    def apply(self, plan: Plan) -> ApplyResult:
        if CSRF_FIELD not in plan.form.fields:
            raise LMSError("form has no CSRF token; refusing to submit")
        body, ctype = encode_multipart(plan.payload(), plan.form.file_fields)
        resp = self.client.request(
            "POST",
            plan.form_url,
            body=body,
            headers={"Content-Type": ctype, "Accept": "text/html,application/xhtml+xml"},
            referer=self.client.url(plan.form_url),
        )
        if 300 <= resp.status < 400:
            location = resp.header("Location") or ""
            m = ASSIGNMENT_LOCATION.search(location.split("?", 1)[0])
            aid = int(m.group(1)) if m else plan.assignment_id
            return ApplyResult(aid, location, resp.status, plan.diff())
        if resp.status == 200:
            raise AssignmentFormError(
                "the LMS rejected the form (validation failed)", extract_form_errors(resp.text)
            )
        raise LMSHTTPError("POST", plan.form_url, resp.status)


# -- change application --------------------------------------------------------


def apply_changes(form: HTMLForm, changes: Mapping[str, ChangeValue], raw: Mapping[str, str]) -> None:
    unknown = sorted(set(changes) - set(FRIENDLY_FIELDS))
    if unknown:
        raise AssignmentFormError(
            "unknown field(s): " + ", ".join(unknown) + "; known: " + ", ".join(FRIENDLY_FIELDS)
        )
    for key, value in changes.items():
        if value is None:
            continue
        if key in SIMPLE_FIELDS:
            _set_field(form, SIMPLE_FIELDS[key], _coerce(key, value))
        elif key in DATETIME_FIELDS:
            date_s, time_s = _split_datetime(key, value)
            base = DATETIME_FIELDS[key]
            _set_field(form, base + "_0", date_s)
            _set_field(form, base + "_1", time_s)
        elif key == "responsible_teachers":
            _set_responsible_teachers(form, value)
    for name, value in raw.items():
        if name == CSRF_FIELD:
            raise AssignmentFormError("the CSRF field cannot be overridden")
        if name not in form.fields and name not in form.selects:
            raise AssignmentFormError(f"form has no field {name!r}")
        _set_field(form, name, value)


def _set_field(form: HTMLForm, name: str, value) -> None:
    if name not in form.fields and name not in form.selects:
        raise AssignmentFormError(f"form has no field {name!r}")
    select = form.selects.get(name)
    if select is not None:
        allowed = [v for v, _ in select.options]
        values = value if isinstance(value, list) else [value]
        bad = [v for v in values if str(v) not in allowed]
        if bad:
            raise AssignmentFormError(
                f"invalid value(s) {bad} for {name}; allowed: "
                + ", ".join(f"{v} ({label})" for v, label in select.options if v)
            )
        if select.multiple:
            form.fields[name] = [str(v) for v in values]
            return
    if name in form.checkboxes:
        form.fields[name] = form.checkboxes[name] if _truthy(value) else None
        return
    form.fields[name] = str(value)


def _set_responsible_teachers(form: HTMLForm, value) -> None:
    if isinstance(value, str):
        wanted = {int(v) for v in re.split(r"[,\s]+", value) if v}
    else:
        wanted = {int(v) for v in value}
    available = {int(m.group(1)): n for n in form.checkboxes if (m := RESPONSIBLE_TEACHER.match(n))}
    unknown = sorted(wanted - set(available))
    if unknown:
        raise AssignmentFormError(f"teacher id(s) {unknown} are not course teachers; available: {sorted(available)}")
    for tid, name in available.items():
        form.fields[name] = form.checkboxes[name] if tid in wanted else None


def _coerce(key: str, value) -> str:
    if key in ("maximum_score", "weight"):
        try:
            num = Decimal(str(value))
        except InvalidOperation:
            raise AssignmentFormError(f"{key} must be a number, got {value!r}") from None
        if num < 0:
            raise AssignmentFormError(f"{key} must not be negative")
        if key == "weight" and num > 1:
            raise AssignmentFormError("weight must be between 0 and 1 (the form's own bounds)")
        return format(num.normalize(), "f") if num == num.to_integral() else str(num)
    if key == "ttc" and value != "" and not re.fullmatch(r"\d+:[0-5]\d", str(value)):
        raise AssignmentFormError("ttc must be hours:minutes, e.g. 4:30")
    if key == "jba_course_id" and value != "" and not str(value).isdigit():
        raise AssignmentFormError("jba_course_id must be an integer")
    if key in ("github_template_repository", "github_grader_repository") and value != "":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(value)):
            raise AssignmentFormError(f"{key} must look like owner/repo")
    return str(value)


def _split_datetime(key: str, value) -> Tuple[str, str]:
    """Accept a naive ``datetime`` or ``YYYY-MM-DD[ T]HH:MM`` (or the form's
    own ``dd.mm.yyyy HH:MM``). The wall-clock time is interpreted in the
    assignment's ``time_zone`` field by the LMS, so aware datetimes are
    rejected rather than silently converted."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            raise AssignmentFormError(f"{key}: pass a naive local time; set time_zone separately")
        moment = value
    else:
        text = str(value).strip()
        moment = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M"):
            try:
                moment = dt.datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if moment is None:
            raise AssignmentFormError(f"{key}: expected 'YYYY-MM-DD HH:MM', got {text!r}")
    return moment.strftime(DATE_FORMAT), moment.strftime(TIME_FORMAT)


def friendly_values(form: HTMLForm) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for key, name in SIMPLE_FIELDS.items():
        if name in form.fields:
            out[key] = form.fields[name]
    for key, base in DATETIME_FIELDS.items():
        d, t = form.fields.get(base + "_0"), form.fields.get(base + "_1")
        if d:
            try:
                out[key] = dt.datetime.strptime(f"{d} {t or '00:00'}", f"{DATE_FORMAT} {TIME_FORMAT}").strftime("%Y-%m-%d %H:%M")
            except ValueError:
                out[key] = f"{d} {t}".strip()
    teachers = [int(m.group(1)) for n, v in form.fields.items() if v and (m := RESPONSIBLE_TEACHER.match(n))]
    if teachers:
        out["responsible_teachers"] = sorted(teachers)
    return out


def _teacher_label(form: HTMLForm, teacher_id: int) -> str:
    for name, info in form.selects.items():
        for value, label in info.options:
            if value == str(teacher_id) and name.startswith("student-group-assignee-"):
                return label
    return ""


def _truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def _norm(value):
    if value is None or value == []:
        return None
    return value


def _show(value) -> str:
    if value is None or value == []:
        return "∅"
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "...'"
