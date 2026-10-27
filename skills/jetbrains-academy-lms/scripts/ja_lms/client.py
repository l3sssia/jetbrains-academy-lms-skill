"""HTTP client for the JetBrains Academy teaching LMS.

Standard library only. The client:

* sends the browser-session cookies from a pluggable ``CredentialProvider``;
* adds Django CSRF headers (``X-CSRFToken``, ``Referer``, ``Origin``);
* detects an expired session (redirect to ``/login/``, 401/403, or a login
  page) and calls an ``on_auth_expired`` hook that may return fresh
  credentials, which are saved and the request retried once;
* persists cookies the server rotates via ``Set-Cookie`` back to the provider
  that supplied them (read-only providers are left alone);
* retries idempotent GETs on 429/502/503/504, honoring ``Retry-After``.

The transport is injectable for tests or for a host application that already
has its own HTTP stack.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from .credentials import CredentialProvider, Credentials, CredentialsError, default_provider

USER_AGENT = "ja-lms-client/0.3 (+https://github.com/l3sssia/jetbrains-academy-lms-skill)"
RETRYABLE = {429, 502, 503, 504}


class LMSError(Exception):
    """Base error for LMS client failures."""


class AuthExpiredError(CredentialsError):
    """The LMS rejected the session; fresh browser cookies are needed."""


class LMSHTTPError(LMSError):
    def __init__(self, method: str, url: str, status: int, body: str = "") -> None:
        self.method, self.url, self.status, self.body = method, url, status, body
        super().__init__(f"{method} {_strip_query(url)} -> HTTP {status}")


@dataclass
class Response:
    status: int
    headers: List[tuple]  # (name, value), repeated names allowed
    body: bytes
    url: str = ""

    def header(self, name: str) -> Optional[str]:
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return None

    def header_all(self, name: str) -> List[str]:
        name = name.lower()
        return [v for k, v in self.headers if k.lower() == name]

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


Transport = Callable[[str, str, Mapping[str, str], Optional[bytes]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # surface 3xx to the caller
        return None


def urllib_transport(timeout: float = 30.0) -> Transport:
    opener = urllib.request.build_opener(_NoRedirect)

    def send(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> Response:
        req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
        try:
            with opener.open(req, timeout=timeout) as resp:
                return Response(resp.status, list(resp.headers.items()), resp.read(), resp.geturl())
        except urllib.error.HTTPError as err:  # includes unfollowed 3xx
            return Response(err.code, list(err.headers.items()), err.read() or b"", url)
        except urllib.error.URLError as err:
            raise LMSError(f"{method} {_strip_query(url)} failed: {err.reason}") from None

    return send


AuthExpiredHook = Callable[["LMSClient"], Optional[Credentials]]


@dataclass
class Course:
    id: int
    title: str
    path: Optional[str] = None  # e.g. /courses/<term>/<id>-<slug>/


class LMSClient:
    def __init__(
        self,
        provider: Optional[CredentialProvider] = None,
        *,
        credentials: Optional[Credentials] = None,
        transport: Optional[Transport] = None,
        on_auth_expired: Optional[AuthExpiredHook] = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider if provider is not None else default_provider()
        self._credentials = credentials
        self.transport = transport or urllib_transport()
        self.on_auth_expired = on_auth_expired
        self.max_retries = max_retries
        self._sleep = sleep

    # -- credentials -------------------------------------------------------

    @property
    def credentials(self) -> Credentials:
        if self._credentials is None:
            loaded = self.provider.load()
            if loaded is None:
                raise CredentialsError(
                    "no LMS credentials configured; run `ja-lms auth set` or set "
                    "JA_LMS_SESSIONID and JA_LMS_CSRFTOKEN"
                )
            self._credentials = loaded
        return self._credentials

    @property
    def base_url(self) -> str:
        return self.credentials.base_url

    def update_credentials(self, credentials: Credentials, *, persist: bool = True) -> None:
        """Replace the active credentials, optionally saving them."""
        self._credentials = credentials
        if persist:
            try:
                self.provider.save(credentials)
            except NotImplementedError:
                pass

    # -- low level ---------------------------------------------------------

    def url(self, path: str, params: Optional[Mapping[str, object]] = None) -> str:
        url = path if path.startswith("http") else self.base_url + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, object]] = None,
        body: Optional[bytes] = None,
        headers: Optional[Mapping[str, str]] = None,
        referer: Optional[str] = None,
        allow_redirect: bool = False,
    ) -> Response:
        method = method.upper()
        url = self.url(path, params)
        if not url.startswith(self.base_url + "/"):
            raise LMSError("refusing to send LMS credentials to a different origin")
        refreshed = False
        attempt = 0
        while True:
            creds = self.credentials
            hdrs = {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Cookie": f"csrftoken={creds.csrftoken}; sessionid={creds.sessionid}",
                "X-CSRFToken": creds.csrftoken,
                "Referer": referer or creds.base_url + "/teaching/",
            }
            if method not in ("GET", "HEAD"):
                hdrs["Origin"] = creds.base_url
            hdrs.update(headers or {})
            resp = self.transport(method, url, hdrs, body)
            self._absorb_cookies(resp)

            if self._is_auth_failure(resp, allow_redirect):
                if refreshed or self.on_auth_expired is None:
                    raise AuthExpiredError(
                        "LMS session expired or was rejected; copy fresh `sessionid` and "
                        "`csrftoken` cookies from a logged-in browser and run `ja-lms auth set`"
                    )
                fresh = self.on_auth_expired(self)
                if fresh is None:
                    raise AuthExpiredError("LMS session expired and no new credentials were provided")
                self.update_credentials(fresh)
                refreshed = True
                continue

            if method == "GET" and resp.status in RETRYABLE and attempt < self.max_retries:
                attempt += 1
                self._sleep(_retry_after(resp, attempt))
                continue
            return resp

    def get_json(self, path: str, params: Optional[Mapping[str, object]] = None):
        resp = self.request("GET", path, params=params)
        if resp.status != 200:
            raise LMSHTTPError("GET", resp.url or path, resp.status, resp.text[:500])
        try:
            return resp.json()
        except ValueError:
            raise LMSError(f"GET {path} did not return JSON") from None

    def get_html(self, path: str) -> str:
        resp = self.request("GET", path)
        if resp.status != 200:
            raise LMSHTTPError("GET", path, resp.status)
        return resp.text

    def _is_auth_failure(self, resp: Response, allow_redirect: bool) -> bool:
        if resp.status in (401, 403):
            # A CSRF failure is also 403; both mean the cookies are unusable.
            return True
        if 300 <= resp.status < 400:
            location = resp.header("Location") or ""
            return "/login" in urllib.parse.urlsplit(location).path
        if resp.status == 200 and not allow_redirect:
            ctype = resp.header("Content-Type") or ""
            if "text/html" in ctype and b'name="password"' in resp.body and b'name="username"' in resp.body:
                return True
        return False

    def _absorb_cookies(self, resp: Response) -> None:
        jar = SimpleCookie()
        for raw in resp.header_all("Set-Cookie"):
            try:
                jar.load(raw)
            except Exception:
                continue
        session = jar.get("sessionid")
        csrf = jar.get("csrftoken")
        new_session = session.value if session is not None and session.value else None
        new_csrf = csrf.value if csrf is not None and csrf.value else None
        if not (new_session or new_csrf):
            return
        current = self.credentials
        updated = current.with_cookies(new_session, new_csrf)
        if updated != current:
            self.update_credentials(updated)

    # -- read API ----------------------------------------------------------

    def courses(self) -> List[Course]:
        """Courses the current user teaches, with their public URL path."""
        props = self.teaching_props()
        options = props.get("courseOptions") or []
        paths = self.course_paths()
        return [Course(int(o["value"]), str(o.get("label", "")), paths.get(int(o["value"]))) for o in options]

    def teaching_props(self) -> dict:
        """The JSON props the teaching assignments page renders into the DOM
        (course options, teachers, status options, time zone). The embedded
        ``csrfToken`` is dropped."""
        page = self.get_html("/teaching/assignments/")
        for m in re.finditer(r"data-[a-z-]+='([^']*)'", page):
            try:
                data = json.loads(html_lib.unescape(m.group(1)))
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("props"), dict):
                props = dict(data["props"])
                props.pop("csrfToken", None)
                props["state"] = data.get("state", {})
                return props
        return {}

    def course_paths(self) -> Dict[int, str]:
        page = self.get_html("/teaching/courses/")
        found: Dict[int, str] = {}
        for term, cid, slug in re.findall(r'href="/courses/([^/"]+)/(\d+)-([^/"]+)/"', page):
            found.setdefault(int(cid), f"/courses/{term}/{cid}-{slug}/")
        return found

    def course_path(self, course_id: int) -> str:
        path = self.course_paths().get(int(course_id))
        if not path:
            raise LMSError(f"course {course_id} not found among courses you teach")
        return path

    def status_options(self) -> List[dict]:
        return list(self.teaching_props().get("statusOptions") or [])

    def assignments(self, course_id: int) -> list:
        return self.get_json(f"/api/v1/teaching/courses/{int(course_id)}/assignments/")

    def enrollments(self, course_id: int) -> list:
        return self.get_json(f"/api/v1/teaching/courses/{int(course_id)}/enrollments/")

    def personal_assignments(self, course_id: int, assignment_ids: Iterable[int] = ()) -> list:
        ids = ",".join(str(int(a)) for a in assignment_ids)
        return self.get_json(
            f"/api/v1/teaching/courses/{int(course_id)}/personal-assignments/",
            {"assignments": ids},
        )

    def score_audit_log(self, personal_assignment_id: int) -> dict:
        return self.get_json(f"/teaching/api/v1/personal-assignments/{int(personal_assignment_id)}/score-audit-log/")

    def whoami_ok(self) -> bool:
        """Cheap authenticated probe; raises ``AuthExpiredError`` if rejected."""
        self.get_html("/teaching/assignments/")
        return True


def _retry_after(resp: Response, attempt: int) -> float:
    value = resp.header("Retry-After")
    try:
        return min(float(value), 60.0) if value else min(2.0 ** attempt, 30.0)
    except ValueError:
        return min(2.0 ** attempt, 30.0)


def _strip_query(url: str) -> str:
    return url.split("?", 1)[0]
