# JetBrains Academy teaching LMS: observed API reference

This is a reverse-engineered reference based on browser captures. It is not an official JetBrains Academy API specification. Base URL: `https://lms.academy.jetbrains.org`.

## Authentication and safety

The teaching UI uses an authenticated browser/session and Django CSRF protection. The captures identified `csrftoken` as the CSRF cookie name. Use a user-authorized authenticated context for requests; do not expose or persist cookies or CSRF values. Authentication, authorization, pagination, rate limits, and complete write-response schemas were not established.

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

This connects an LMS assignment to a student and submission state. Only `not_submitted` was observed; treat other statuses as open-ended.

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

For assignments with `solutionFormat: "github"`, repository-level grades may come from GitHub Classroom rather than this LMS. A separately observed GitHub Classroom endpoint was `GET /assignments/{github_classroom_assignment_id}/grades`, with fields such as `github_username`, `roster_identifier`, `points_awarded`, and `points_available`; it is not an LMS endpoint.
