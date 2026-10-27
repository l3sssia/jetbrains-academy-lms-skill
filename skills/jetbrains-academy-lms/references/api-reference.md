# JetBrains Academy teaching LMS: observed API reference

This is a reverse-engineered reference based on browser captures. It is not an official JetBrains Academy API specification. Base URL: `https://lms.academy.jetbrains.org`.

For a ready-made client that implements everything below, see [the `ja_lms` client](client.md).

## Authentication and safety

The teaching UI uses an authenticated browser session and Django CSRF protection.

- Cookies: `sessionid` (HttpOnly, observed `Max-Age` 14 days) and `csrftoken`
  (observed `Max-Age` about one year). Both are reissued by a successful login.
- `POST /login/` is a form (`csrfmiddlewaretoken`, `username`, `password`) that also
  requires a `g-recaptcha-response`, so it cannot be scripted. Reuse a logged-in
  browser session instead; see [credentials](client.md#credentials).
- JSON API calls send the `X-CSRFToken` header. Form POSTs send the form's
  `csrfmiddlewaretoken` field plus same-origin `Origin` and `Referer` headers, which
  Django's CSRF check requires over HTTPS.
- An unauthenticated request is redirected to `/login/`.

Do not expose or persist cookies or CSRF values anywhere but a private credentials
store. Authorization rules, pagination, rate limits, and complete write-response
schemas were not established.

## Discovering courses

- `GET /teaching/assignments/` renders a JSON props blob in a `data-*` attribute with
  `courseOptions` (`[{label, value: course_id}]`), `courseTeachers`, `courseGroups`,
  `statusOptions`, `timeZone`, and `state.course`. It also embeds a `csrfToken`; do
  not log or store it.
- `GET /teaching/courses/` links each taught course as
  `/courses/{term}/{course_id}-{slug}/` (for example
  `/courses/2030-autumn/42-intro-to-testing/`). The numeric prefix is the API
  `course_id`. Course-scoped pages (assignment add/edit) live under this path.

Observed `statusOptions` values for personal assignments: `not_submitted`,
`on_checking`, `need_fixes`, `completed`. Read them at runtime rather than
hard-coding them.

## Read endpoints

### Assignments in a course

`GET /api/v1/teaching/courses/{course_id}/assignments/`

Observed assignment fields include:

```json
{
  "id": 158,
  "deadlineAt": "2027-01-01T17:00:00Z",
  "title": "HW 0: Test",
  "maximumScore": 1,
  "weight": "0.00",
  "ttc": null,
  "solutionFormat": "github"
}
```

### Course enrollments

`GET /api/v1/teaching/courses/{course_id}/enrollments/`

Observed shape:

```json
{
  "id": 675,
  "grade": 0,
  "studentGroupId": 58,
  "student": { "id": 383, "firstName": "Dóra", "lastName": "Horváth" },
  "studentProfileId": 392
}
```

### Personal assignments for one LMS assignment

`GET /api/v1/teaching/courses/{course_id}/personal-assignments/?assignments={assignment_id}`

Observed shape:

```json
{
  "id": 4815,
  "assignmentId": 158,
  "score": null,
  "status": "not_submitted",
  "student": { "id": 383, "firstName": "Dóra", "lastName": "Horváth", "username": "Hora" },
  "assignee": null,
  "solutionAt": null
}
```

This connects an LMS assignment to a student and submission state. See the `statusOptions` under [Discovering courses](#discovering-courses) for the observed statuses; treat the set as open-ended. The UI was also observed sending `assignments=` empty, which returned personal assignments across the course; a comma-separated list of several IDs was not verified.

### Score audit log

`GET /teaching/api/v1/personal-assignments/{personal_assignment_id}/score-audit-log/`

The UI expects an object with `edges` and `sources`; an edge may contain `createdAt`, `scoreOld`, `scoreNew`, `changedBy`, and `source`.

### Submission page

`GET /teaching/assignments/submissions/{personal_assignment_id}/`

The page renders a Django multipart review form.

## Observed mutation

### Set or clear a submission assignee

`PUT /api/v1/teaching/courses/{course_id}/assignments/{assignment_id}/students/{student_id}/assignee`

Content type: `application/x-www-form-urlencoded`. Send `assignee={assignee_id}` to set the assignee; the UI sends an empty value to clear it. The UI reads an `assignee` field from the JSON response, but the full response schema is unconfirmed.

Verify the student and assignment before performing this request.

## Assignment add and edit forms

There is no JSON write API for assignments. The teacher UI posts a server-rendered
Django form, observed working for both create and edit.

- Add: `GET`/`POST /courses/{term}/{course_id}-{slug}/assignments/add`
- Edit: `GET`/`POST /courses/{term}/{course_id}-{slug}/assignments/{assignment_id}/edit`.
  The teacher assignment page `GET /teaching/assignments/{assignment_id}/` links to it
  (and to `.../delete`, which was not exercised).

Content type: `multipart/form-data`. Success is `302` with
`Location: /teaching/assignments/{assignment_id}/`, which also yields a new
assignment's ID. A validation failure re-renders the form.

**The POST is full-replace.** Always GET the form, keep every successful control as a
browser would submit it, change only the intended fields, and post the whole set back.
A partial POST would blank the omitted fields.

| Field | Observed widget and values |
| --- | --- |
| `csrfmiddlewaretoken` | hidden |
| `assignment-title` | text, required |
| `assignment-text` | textarea, required |
| `assignment-attachments` | file; the browser sends an empty part (`filename=""`) when untouched |
| `assignment-attachments-clear` | hidden, empty |
| `assignment-submission_type` | select, required: `online` (Online Submission), `jba` (JetBrains Academy Course), `github` (GitHub), `penalty` (Penalty), `other` (No Submission) |
| `assignment-maximum_score` | number, required |
| `assignment-weight` | number, required, `min=0 max=1 step=0.01` |
| `assignment-ttc` | text `hours:minutes`, optional |
| `assignment-opens_at_0` / `_1` | date `dd.mm.yyyy` / time `hh:mm`, required |
| `assignment-deadline_at_0` / `_1` | date `dd.mm.yyyy` / time `hh:mm`, required |
| `assignment-time_zone` | select of IANA names; times are wall-clock in this zone |
| `assignment-jba_course_id` | number, optional |
| `assignment-github_template_repository` | text `owner/repo` |
| `assignment-github_grader_repository` | text `owner/repo` |
| `assignment-github_grading_mode` | select: `manual`, `sync`, `once` |
| `assignment-assignee_mode` | select: `off` (Without a responsible person), `manual` (Choose from the list), `sg_default` (Student Group - Default), `sg_custom` (Student Group - Custom) |
| `responsible-teacher-{teacher_id}-active` | checkbox per course teacher, sent as `on` only when ticked |
| `student-group-assignee-{group_id}-teacher` | select of teacher IDs, empty for none |
| `bucket-TOTAL_FORMS`, `bucket-INITIAL_FORMS`, `bucket-MIN_NUM_FORMS`, `bucket-MAX_NUM_FORMS` | Django formset management fields |
| `bucket-{n}-student_groups`, `bucket-{n}-teachers` | multi-selects; `bucket-__prefix__-*` is the empty template and is not submitted |
| `save` | submit, value `Save` |

Teacher and group IDs are course-specific; read them from the form rather than
hard-coding them.

## Review form: partially observed only

`POST /teaching/assignments/submissions/{personal_assignment_id}/`

Content type: `multipart/form-data`. Observed form fields:

`review-text`, `review-attached_file`, `review-attached_file-clear`, `review-score`, `review-score_old`, `review-status`, `review-status_old`, `save`, `save-draft`.

The captured form allowed decimal `review-score` values from `0` to the assignment maximum with `step=0.01`. No successful POST was captured, so do not assume the redirect, response, or field requirements.

## Gradebook traversal

1. List assignments for the course.
2. List enrollments for the course.
3. For each assignment, list matching personal assignments.
4. Use each personal-assignment ID to open the submission page or score-audit log.

For assignments with `solutionFormat: "github"`, grading normally runs through the
LMS-native GitHub integration: a per-assignment template repository, a student fork, and
a grader workflow dispatched with a `grade` `repository_dispatch` event. See
[GitHub assignments and automatic grading](github-assignments.md) for that contract.

GitHub Classroom is a separate product and not part of this LMS. A separately observed GitHub Classroom endpoint was `GET /assignments/{github_classroom_assignment_id}/grades`, with fields such as `github_username`, `roster_identifier`, `points_awarded`, and `points_available`; it is not an LMS endpoint, and should only be used when the user has explicitly asked for a GitHub Classroom workflow.
