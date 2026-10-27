# `ja_lms` client: credentials, reads, and assignment add/edit

A standard-library-only Python (3.8+) client lives in [`scripts/ja_lms`](../scripts/ja_lms).
Use it instead of hand-writing LMS requests: it already handles session cookies,
CSRF, expired sessions, and the full-replace assignment forms. It is unofficial and
built on [observed behavior](api-reference.md).

Run it without installing anything:

```sh
skills/jetbrains-academy-lms/scripts/ja-lms --help
# or
PYTHONPATH=skills/jetbrains-academy-lms/scripts python -m ja_lms --help
```

## Credentials

The LMS login form is protected by reCAPTCHA, so scripts cannot log in with a
username and password. The client reuses a logged-in **browser session**: the
`sessionid` and `csrftoken` cookies (DevTools → Application/Storage → Cookies for the
LMS origin). Observed lifetimes: `sessionid` about 14 days, `csrftoken` about a year.

Resolution order (`default_provider()`):

1. Environment: `JA_LMS_SESSIONID`, `JA_LMS_CSRFTOKEN`, optional `JA_LMS_BASE_URL`.
   Read-only; intended for CI secrets.
2. File: `$JA_LMS_CREDENTIALS`, else `$XDG_CONFIG_HOME/ja-lms/credentials.json`
   (`~/.config/ja-lms/credentials.json`). Written atomically with mode `0600`.

Updating credentials:

- `ja-lms auth set` prompts for both cookies without echoing them, verifies them with
  one read request, and saves them to the file. `--from-env` copies them from the
  environment variables instead; `--base-url` targets another LMS instance.
- `ja-lms auth status` reports where credentials came from and whether the LMS
  accepts them. It never prints their values. `ja-lms auth clear` deletes the file.
- When a request is rejected (redirect to `/login/`, 401/403, or a login page), the
  client calls its `on_auth_expired` hook. The CLI prompts for fresh cookies on a
  TTY, saves them, and retries the request once. Pass `--no-prompt` in automation.
- Cookies the server rotates via `Set-Cookie` are saved back to the provider that
  supplied them. Read-only sources such as the environment are never copied to disk.

Agent rules: never ask the user to paste cookies into the chat, never write them to the
repository, a log, or command-line arguments (they would land in shell history), and
never print them. Point the user at `ja-lms auth set`, which they run themselves.

### Plugging in another credential source

Implement `load() -> Credentials | None` and `save(credentials)` (raise
`NotImplementedError` if read-only), then pass it to `LMSClient`:

```python
from ja_lms import ChainCredentialProvider, Credentials, EnvCredentialProvider, LMSClient

class KeyringProvider:
    def load(self):
        import keyring
        s, c = keyring.get_password("ja-lms", "sessionid"), keyring.get_password("ja-lms", "csrftoken")
        return Credentials(s, c) if s and c else None

    def save(self, creds):
        import keyring
        keyring.set_password("ja-lms", "sessionid", creds.sessionid)
        keyring.set_password("ja-lms", "csrftoken", creds.csrftoken)

client = LMSClient(
    ChainCredentialProvider([EnvCredentialProvider(), KeyringProvider()]),
    on_auth_expired=lambda client: None,  # or return fresh Credentials from your UI
)
```

`StaticCredentialProvider` holds credentials in memory for host applications. The
HTTP transport is injectable too (`LMSClient(transport=...)`): a callable
`(method, url, headers, body) -> Response`. The client refuses to send cookies to any
origin other than the credentials' `base_url`.

## Reads

| CLI | Python | Source |
| --- | --- | --- |
| `ja-lms courses` | `client.courses()` | `/teaching/assignments/` props + `/teaching/courses/` links |
| `ja-lms assignments COURSE_ID` | `client.assignments(course_id)` | JSON API |
| — | `client.enrollments(course_id)` | JSON API |
| — | `client.personal_assignments(course_id, [assignment_id, ...])` | JSON API |
| — | `client.score_audit_log(personal_assignment_id)` | JSON API |
| — | `client.status_options()` | teaching page props |
| `ja-lms assignment show ID` | `AssignmentService(client).read(id)` | edit form |
| `ja-lms assignment choices --course ID` / `--assignment ID` | `.choices(...)` | add/edit form selects |

Add `--json` before the subcommand for machine-readable output (`ja-lms --json courses`).

## Adding and editing assignments

Every write is a two-step plan → apply. Planning only performs GETs; nothing changes
until `apply` (CLI: `--apply`).

```sh
# Dry run: prints the field-level diff, sends nothing
ja-lms assignment add 42 --title "HW 4: Recursion" --submission-type github \
  --maximum-score 10 --weight 1 --opens-at "2030-10-01 09:00" --deadline "2030-10-08 23:59" \
  --time-zone UTC --github-template-repository example-org/hw-04 \
  --github-grader-repository example-org/lms-grading --text-file hw4.md

# Submit after the user has reviewed the diff
ja-lms assignment add 42 ... --apply

# Edit: only the given fields change; everything else is echoed back unchanged
ja-lms assignment edit 208 --deadline "2030-10-10 23:59"
ja-lms assignment edit 208 --deadline "2030-10-10 23:59" --apply
```

```python
from ja_lms import AssignmentService, LMSClient

svc = AssignmentService(LMSClient())
plan = svc.plan_update(208, {"deadline_at": "2030-10-10 23:59", "weight": "0.5"})
print(plan.describe())          # show the user, get authorization
result = svc.apply(plan)        # ApplyResult(assignment_id=208, location=..., status=302)
```

Friendly fields (CLI flag = field name with `-`):

| Field | Form field | Notes |
| --- | --- | --- |
| `title` | `assignment-title` | required |
| `text` | `assignment-text` | required by the form; CLI also takes `--text-file` |
| `submission_type` | `assignment-submission_type` | observed: `online`, `jba`, `github`, `penalty`, `other` (No Submission) |
| `maximum_score` | `assignment-maximum_score` | for GitHub assignments keep it equal to the grader's `max_score` |
| `weight` | `assignment-weight` | `0`–`1`, step `0.01` |
| `ttc` | `assignment-ttc` | time to complete, `hours:minutes`, optional |
| `opens_at`, `deadline_at` | `assignment-{opens_at,deadline_at}_{0,1}` | pass `YYYY-MM-DD HH:MM`; sent as `dd.mm.yyyy` + `HH:MM` wall-clock time in `time_zone` |
| `time_zone` | `assignment-time_zone` | IANA name from the form's options |
| `jba_course_id` | `assignment-jba_course_id` | for `jba` submissions |
| `github_template_repository`, `github_grader_repository` | `assignment-github_*_repository` | `owner/repo` |
| `github_grading_mode` | `assignment-github_grading_mode` | observed: `manual`, `sync`, `once` |
| `assignee_mode` | `assignment-assignee_mode` | observed: `off`, `manual`, `sg_default`, `sg_custom` |
| `responsible_teachers` | `responsible-teacher-{id}-active` checkboxes | full list of teacher IDs to tick; see `assignment choices` |

Anything else (student-group assignee selects, `bucket-*` formset fields) can be set
by raw form name with `--raw NAME=VALUE` / `plan_update(id, {}, raw={...})`; the name
must exist in the form and select values must be among its options. The CSRF field
cannot be overridden.

Values are validated against the live form before anything is sent: unknown fields,
select values outside the offered options, a weight above 1, malformed dates or
`owner/repo` values all fail at plan time. On submit, a `302` to
`/teaching/assignments/<id>/` is success and yields the assignment ID (the new ID for
an add). A `200` means the LMS re-rendered the form with validation errors, which are
raised as `AssignmentFormError.errors`.

Not supported: attachment upload (the client sends the empty file part a browser sends
for an untouched file input, exactly as in the observed browser edit) and deletion
(`/courses/<term>/<id>-<slug>/assignments/<id>/delete` exists but was not exercised).
