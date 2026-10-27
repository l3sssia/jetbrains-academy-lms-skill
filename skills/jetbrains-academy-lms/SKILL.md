---
name: jetbrains-academy-lms
description: Build apps, scripts, or GitHub autograders that read or operate the JetBrains Academy teaching LMS, including creating and editing course assignments, enrollments, submissions, gradebook workflows, session-credential management, and repository_dispatch grader workflows for GitHub assignments.
---

# JetBrains Academy LMS

Use this skill for tools that integrate with the JetBrains Academy teaching LMS.

Three references cover the integrations, and they have very different reliability:

- [GitHub assignments and automatic grading](references/github-assignments.md) —
  the LMS-native GitHub integration: template repositories, student forks, and grader
  workflows triggered by `repository_dispatch`. This behavior is documented by the LMS,
  so the setup steps, dispatch payload, and results schema can be relied on. Read this
  before writing a grader workflow or a `results.json` producer.
- [Observed LMS API reference](references/api-reference.md) — authentication and
  CSRF, course discovery, assignments, enrollments, personal assignments, score audit
  log, the assignment add/edit forms, and the review form. This is
  reverse-engineered from browser traffic, not an official or stable public API. Read it
  before implementing any LMS HTTP request.
- [`ja_lms` client](references/client.md) — a bundled, dependency-free Python client
  and `ja-lms` CLI in [`scripts/`](scripts): pluggable session credentials with
  update/refresh, course discovery, read endpoints, and dry-run-first assignment
  add/edit. Prefer it over hand-written requests. Read it before managing
  credentials or changing assignments.

## Working approach

- Use an authenticated session supplied or authorized by the user. The LMS login is reCAPTCHA-protected, so scripts reuse browser session cookies through a credential provider (`ja-lms auth set`, environment variables, or a custom provider). Never ask for cookies in chat, and never commit, log, print, or pass them on a command line. Do not retain or reuse captured HAR files.
- Discover course, assignment, enrollment, and personal-assignment IDs dynamically. Never build production workflows around example IDs.
- Treat reads and mutations differently. Verify the target course, assignment, and student before a write; show the intended change and obtain user authorization immediately before a mutating LMS request.
- For state-changing calls, preserve Django CSRF behavior and the content type observed in the reference. Do not assume undocumented write endpoints or response shapes.
- Build against observable fields and handle missing fields, pagination, permissions, authentication failure, rate limits, and unexpected status values gracefully. The observed `not_submitted` status is not a complete enum.

## GitHub assignments

When an assignment's `solutionFormat` is `github`, the LMS itself owns the GitHub
workflow: it forks a per-assignment template repository for each student and dispatches
a `grade` event to a grader repository in the same organization. Grades come back from
that grader, not from a separate service.

- Do not conflate this with GitHub Classroom. GitHub Classroom is a distinct product
  with its own grade export; use it only when the user has explicitly authorized that
  separate workflow, and keep its data separate from LMS data.
- A grader workflow's `run-name` must end with `lms_run_id`, and the run must finish
  successfully with a `grading-results` artifact containing `results.json`. Missing
  either one means the submission receives no grade.
- Keep `max_score` constant across every invocation for an assignment. A differing value
  rewrites the assignment's maximum score in the LMS.
- Treat `repo_token` from the dispatch payload as a live credential: pass it only to the
  checkout, keep `persist-credentials: false`, and never write or log it.
- Treat student repository contents as untrusted input executing inside the grader job.

## Adding and editing assignments

Assignment writes go through the teacher's Django add/edit form, which is
full-replace: an omitted field is blanked. Use `AssignmentService` (or
`ja-lms assignment add|edit`), which reads the live form, changes only the requested
fields, and validates them against the form's own choices.

- Plan first (`plan_create` / `plan_update`, or the CLI without `--apply`), show the
  user the diff, and apply only after they authorize that exact change.
- Read IDs, teacher IDs, and allowed values at runtime (`ja-lms courses`,
  `ja-lms assignment choices`); never reuse example values.
- Datetimes are wall-clock times in the assignment's `time_zone` field.
- For a GitHub assignment, keep `maximum_score` equal to the grader's `max_score`.

## Implementation boundaries

Prefer read-only dashboards, exports, and dry-run modes by default. Make mutation-capable scripts require explicit inputs for the course, assignment, student, and requested change, and return the server response without inventing success semantics.

The submission form POST is only partially observed. Do not automate review submission or file upload unless the user explicitly requests it and the resulting form behavior is verified in that authenticated context.

## Reference source

The GitHub assignment reference records LMS-provided documentation for the grading
integration. The read endpoints in the API reference were first derived from a shared
conversation describing browser captures. The login, course discovery, and assignment
add/edit behavior was taken from a teacher-session browser capture; credentials and
personal data from that capture were not recorded. All of it is observed behavior,
not a published API.
