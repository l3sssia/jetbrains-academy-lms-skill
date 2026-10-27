"""Offline tests for the ja_lms client. No network, no real credentials."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "jetbrains-academy-lms" / "scripts"))

from ja_lms import (  # noqa: E402
    AssignmentFormError,
    AssignmentService,
    AuthExpiredError,
    ChainCredentialProvider,
    Credentials,
    EnvCredentialProvider,
    FileCredentialProvider,
    LMSClient,
    LMSError,
    Response,
    StaticCredentialProvider,
)
from ja_lms.forms import parse_form  # noqa: E402

BASE = "https://lms.example.test"
FORM_HTML = (HERE / "fixtures" / "assignment_form.html").read_text(encoding="utf-8")
COURSES_HTML = '<a href="/courses/2030-autumn/5-intro-to-testing/">Intro</a>'
TEACHING_HTML = (
    "<div data-props='{&quot;props&quot;: {&quot;courseOptions&quot;: [{&quot;label&quot;: "
    "&quot;Intro to Testing&quot;, &quot;value&quot;: 5}], &quot;csrfToken&quot;: &quot;SECRET&quot;, "
    "&quot;statusOptions&quot;: [{&quot;label&quot;: &quot;Completed&quot;, &quot;value&quot;: "
    "&quot;completed&quot;}]}, &quot;state&quot;: {&quot;course&quot;: 5}}'></div>"
)
ASSIGNMENT_PAGE = '<a href="/courses/2030-autumn/5-intro-to-testing/assignments/42/edit">Edit</a>'
LOGIN_PAGE = '<form method="post"><input name="username"><input name="password" type="password"></form>'


def html(body, status=200, headers=()):
    return Response(status, [("Content-Type", "text/html; charset=utf-8"), *headers], body.encode())


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes  # {(method, path): Response | [Response, ...]}
        self.calls = []

    def __call__(self, method, url, headers, body):
        path = url[len(BASE):].split("?", 1)[0]
        self.calls.append({"method": method, "path": path, "url": url, "headers": dict(headers), "body": body})
        route = self.routes[(method, path)]
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route


def make_client(routes, **kwargs):
    creds = Credentials("sess-1", "csrf-1", BASE)
    provider = kwargs.pop("provider", StaticCredentialProvider(creds))
    transport = FakeTransport(routes)
    return LMSClient(provider, transport=transport, sleep=lambda s: None, **kwargs), transport


class CredentialTests(unittest.TestCase):
    def test_repr_hides_secrets(self):
        c = Credentials("topsecret", "alsosecret")
        self.assertNotIn("topsecret", repr(c))
        self.assertNotIn("alsosecret", str(c))

    def test_file_provider_roundtrip_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = FileCredentialProvider(Path(d) / "sub" / "credentials.json")
            self.assertIsNone(p.load())
            p.save(Credentials("s", "c", BASE + "/"))
            self.assertEqual(stat.S_IMODE(os.stat(p.path).st_mode), 0o600)
            self.assertEqual(p.load(), Credentials("s", "c", BASE))
            self.assertTrue(p.clear())

    def test_chain_saves_back_to_source_only(self):
        with tempfile.TemporaryDirectory() as d:
            file_p = FileCredentialProvider(Path(d) / "c.json")
            env = EnvCredentialProvider({"JA_LMS_SESSIONID": "s", "JA_LMS_CSRFTOKEN": "c"})
            chain = ChainCredentialProvider([env, file_p])
            self.assertEqual(chain.load().sessionid, "s")
            with self.assertRaises(NotImplementedError):
                chain.save(Credentials("s2", "c2"))
            self.assertFalse(file_p.path.exists())


class ClientTests(unittest.TestCase):
    def test_headers_and_json(self):
        client, t = make_client({("GET", "/api/v1/teaching/courses/5/assignments/"): Response(200, [], b'[{"id": 1}]')})
        self.assertEqual(client.assignments(5), [{"id": 1}])
        h = t.calls[0]["headers"]
        self.assertEqual(h["X-CSRFToken"], "csrf-1")
        self.assertIn("sessionid=sess-1", h["Cookie"])

    def test_personal_assignments_query(self):
        client, t = make_client({("GET", "/api/v1/teaching/courses/5/personal-assignments/"): Response(200, [], b"[]")})
        client.personal_assignments(5, [1, 2])
        self.assertTrue(t.calls[0]["url"].endswith("?assignments=1%2C2"))

    def test_expired_session_without_hook_raises(self):
        client, _ = make_client({("GET", "/teaching/assignments/"): Response(302, [("Location", "/login/?next=/teaching/")], b"")})
        with self.assertRaises(AuthExpiredError):
            client.whoami_ok()

    def test_login_page_counts_as_expired(self):
        client, _ = make_client({("GET", "/teaching/assignments/"): html(LOGIN_PAGE)})
        with self.assertRaises(AuthExpiredError):
            client.whoami_ok()

    def test_expired_session_refreshes_saves_and_retries(self):
        provider = StaticCredentialProvider(Credentials("old", "old", BASE))
        fresh = Credentials("new-s", "new-c", BASE)
        client, t = make_client(
            {("GET", "/teaching/assignments/"): [Response(403, [], b""), html("ok")]},
            provider=provider,
            on_auth_expired=lambda c: fresh,
        )
        self.assertTrue(client.whoami_ok())
        self.assertEqual(provider.load(), fresh)
        self.assertIn("sessionid=new-s", t.calls[1]["headers"]["Cookie"])

    def test_rotated_cookies_are_persisted(self):
        provider = StaticCredentialProvider(Credentials("s", "c", BASE))
        client, _ = make_client(
            {("GET", "/teaching/assignments/"): html("ok", headers=[("Set-Cookie", "sessionid=rotated; Path=/; HttpOnly")])},
            provider=provider,
        )
        client.whoami_ok()
        self.assertEqual(provider.load().sessionid, "rotated")
        self.assertEqual(provider.load().csrftoken, "c")

    def test_retries_get_on_429(self):
        client, t = make_client({("GET", "/api/v1/teaching/courses/5/enrollments/"): [Response(429, [("Retry-After", "1")], b""), Response(200, [], b"[]")]})
        self.assertEqual(client.enrollments(5), [])
        self.assertEqual(len(t.calls), 2)

    def test_refuses_foreign_origin(self):
        client, _ = make_client({})
        with self.assertRaises(LMSError):
            client.request("GET", "https://evil.example/steal")

    def test_courses_drop_embedded_csrf(self):
        client, _ = make_client({("GET", "/teaching/assignments/"): html(TEACHING_HTML), ("GET", "/teaching/courses/"): html(COURSES_HTML)})
        courses = client.courses()
        self.assertEqual((courses[0].id, courses[0].path), (5, "/courses/2030-autumn/5-intro-to-testing/"))
        self.assertNotIn("csrfToken", client.teaching_props())
        self.assertEqual(client.status_options()[0]["value"], "completed")


class FormTests(unittest.TestCase):
    def test_parse_matches_browser_submission(self):
        form = parse_form(FORM_HTML, has_field="assignment-title")
        payload = dict(form.payload())
        self.assertEqual(payload["assignment-text"], "Solve & submit.")
        self.assertEqual(payload["assignment-submission_type"], "github")
        self.assertEqual(payload["responsible-teacher-1-active"], "on")
        self.assertNotIn("responsible-teacher-2-active", payload)
        self.assertNotIn("bucket-__prefix__-student_groups", payload)
        self.assertNotIn("fill_all_sg", payload)
        self.assertEqual(payload["save"], "Save")
        self.assertEqual(form.file_fields, ["assignment-attachments"])


class AssignmentTests(unittest.TestCase):
    def routes(self, post_response):
        return {
            ("GET", "/teaching/assignments/42/"): html(ASSIGNMENT_PAGE),
            ("GET", "/teaching/courses/"): html(COURSES_HTML),
            ("GET", "/courses/2030-autumn/5-intro-to-testing/assignments/42/edit"): html(FORM_HTML),
            ("GET", "/courses/2030-autumn/5-intro-to-testing/assignments/add"): html(FORM_HTML),
            ("POST", "/courses/2030-autumn/5-intro-to-testing/assignments/42/edit"): post_response,
            ("POST", "/courses/2030-autumn/5-intro-to-testing/assignments/add"): post_response,
        }

    def test_update_plan_diff_and_apply(self):
        client, t = make_client(self.routes(Response(302, [("Location", "/teaching/assignments/42/")], b"")))
        svc = AssignmentService(client)
        plan = svc.plan_update(42, {"weight": "0.5", "deadline_at": "2030-10-15 18:00", "responsible_teachers": [2]})
        diff = {name: after for name, _, after in plan.diff()}
        self.assertEqual(
            diff,
            {
                "assignment-weight": "0.5",
                "assignment-deadline_at_0": "15.10.2030",
                "assignment-deadline_at_1": "18:00",
                "responsible-teacher-2-active": "on",
                "responsible-teacher-1-active": None,
            },
        )
        self.assertNotIn("FAKE-FORM-TOKEN", plan.describe())
        self.assertFalse(any(c["method"] == "POST" for c in t.calls))  # planning never writes

        result = svc.apply(plan)
        self.assertEqual(result.assignment_id, 42)
        post = t.calls[-1]
        self.assertEqual(post["method"], "POST")
        self.assertTrue(post["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(post["headers"]["Origin"], BASE)
        self.assertTrue(post["headers"]["Referer"].endswith("/assignments/42/edit"))
        body = post["body"].decode()
        self.assertIn('name="csrfmiddlewaretoken"\r\n\r\nFAKE-FORM-TOKEN', body)
        self.assertIn('name="assignment-title"\r\n\r\nHW 1', body)  # untouched field echoed
        self.assertIn('name="assignment-attachments"; filename=""', body)

    def test_create_returns_new_id(self):
        client, _ = make_client(self.routes(Response(302, [("Location", "/teaching/assignments/99/")], b"")))
        svc = AssignmentService(client)
        plan = svc.plan_create(5, {"title": "Quiz", "deadline_at": "2030-11-01 12:00", "submission_type": "other"})
        self.assertEqual(svc.apply(plan).assignment_id, 99)

    def test_create_requires_title_and_deadline(self):
        client, _ = make_client(self.routes(None))
        with self.assertRaises(AssignmentFormError):
            AssignmentService(client).plan_create(5, {"title": "x"})

    def test_validation_against_form_choices(self):
        client, _ = make_client(self.routes(None))
        svc = AssignmentService(client)
        for bad in (
            {"submission_type": "carrier-pigeon"},
            {"weight": "2"},
            {"time_zone": "Mars/Olympus"},
            {"responsible_teachers": [9]},
            {"deadline_at": "next friday"},
            {"github_template_repository": "not a repo"},
            {"colour": "red"},
        ):
            with self.subTest(bad=bad), self.assertRaises(AssignmentFormError):
                svc.plan_update(42, bad)

    def test_form_rerender_surfaces_errors(self):
        rerender = html(FORM_HTML.replace("<form method", '<ul class="errorlist"><li>Deadline is required.</li></ul><form method'))
        client, _ = make_client(self.routes(rerender))
        svc = AssignmentService(client)
        with self.assertRaises(AssignmentFormError) as ctx:
            svc.apply(svc.plan_update(42, {"title": "x"}))
        self.assertIn("Deadline is required.", ctx.exception.errors)

    def test_read_friendly_values(self):
        client, _ = make_client(self.routes(None))
        values = AssignmentService(client).read(42)
        self.assertEqual(values["deadline_at"], "2030-10-08 23:59")
        self.assertEqual(values["responsible_teachers"], [1])
        self.assertEqual(values["github_grading_mode"], "sync")

    def test_choices_include_teacher_labels(self):
        client, _ = make_client(self.routes(None))
        choices = AssignmentService(client).choices(assignment_id=42)
        self.assertIn(("2", "Teacher Beta"), choices["responsible_teachers"])
        self.assertNotIn("bucket-__prefix__-student_groups", choices)


class CliTests(unittest.TestCase):
    def test_flags_map_to_friendly_fields(self):
        from ja_lms.cli import _collect_changes, _parse_raw, build_parser

        args = build_parser().parse_args(
            ["assignment", "edit", "42", "--deadline", "2030-10-15 18:00", "--weight", "0.5", "--raw", "bucket-TOTAL_FORMS=0"]
        )
        self.assertEqual(_collect_changes(args), {"deadline_at": "2030-10-15 18:00", "weight": "0.5"})
        self.assertEqual(_parse_raw(args.raw), {"bucket-TOTAL_FORMS": "0"})
        self.assertFalse(args.apply)


if __name__ == "__main__":
    unittest.main()
