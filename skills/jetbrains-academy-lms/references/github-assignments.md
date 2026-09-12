# JetBrains Academy LMS: GitHub assignments and automatic grading

This describes the LMS-native GitHub integration used by assignments whose
`solutionFormat` is `github`. Unlike [the observed API reference](api-reference.md),
this behavior is documented by the LMS itself rather than reverse-engineered, so the
setup steps, dispatch payload, and results schema below can be relied on.

This is **not** GitHub Classroom. The LMS forks a template repository for each student
and dispatches a workflow in a grader repository; grades flow back into the LMS.

## One-time setup for a course

1. Create a GitHub organization. One organization per course is recommended.
2. Connect the GitHub account, then click **Configure** to install the LMS app in the
   organization, from the *Connected accounts* section of the LMS user settings page
   (`/users/{user_id}/#connected-accounts`).
3. In the organization's *Settings → Member privileges*, enable
   **Allow forking of private repositories**. Student repositories are forks of the
   template, so this is required.
4. Create a template repository. Each student gets their own fork of it to work in.
   **Each assignment needs its own template repository.**
5. For automatic grading, create a grader repository.

Constraint: the grader repository must live in the **same organization** as the
template repository.

## Grader workflow contract

The grader workflow must be triggered by `repository_dispatch` with the event type
`grade`. The LMS sends these fields in `github.event.client_payload`:

| Field | Meaning |
| --- | --- |
| `lms_run_id` | UUID of the LMS grading run. Links the run to the student assignment. |
| `template_repo_name` | Template repository name for the assignment being graded, without the organization prefix. Use it to distinguish assignments when one grader repo serves several. |
| `student_repo_name` | Student repository name to grade, without the organization prefix. |
| `student_repo_ref` | Commit hash to check out the student repository at. |
| `repo_token` | Token with read permission for the template and student repositories. Use it to check out the student repository. |

Two hard requirements:

- The workflow's `run-name` **must end with** the `lms_run_id` value, or the LMS cannot
  match the run to the student assignment.
- The workflow must **complete successfully** and upload an artifact named
  **`grading-results`** containing a `results.json` file.

Check out the student repository at `student_repo_ref` exactly — grading a different
ref grades the wrong submission.

## `results.json` schema

```json
{
  "score": 8.5,
  "max_score": 10,
  "subtasks": [
    { "name": "Subtask name", "points": 4.5, "max_points": 5 }
  ]
}
```

- `score` — decimal; the final assignment score.
- `max_score` — integer; the maximum score. It **must be equal for every grader
  invocation of the same assignment**. If it differs from the assignment's max score in
  the LMS, the assignment max score is updated to match — so an inconsistent value
  silently rewrites the assignment.
- `subtasks` — optional. Each entry has `name`, decimal `points`, and decimal
  `max_points`. When present, the data appears in the LMS grading-results comment.
  It does **not** affect the assignment score.
- Unknown fields in the results file are ignored.

## Reference workflow

```yaml
name: Grade assignment
run-name: ${{ github.event.client_payload.lms_run_id }}

on:
  repository_dispatch:
    types: [grade]

jobs:
  grade:
    runs-on: ubuntu-latest
    steps:
      - name: Check out grader
        uses: actions/checkout@v7

      - name: Check out student repository
        uses: actions/checkout@v7
        with:
          repository: ${{ github.repository_owner }}/${{ github.event.client_payload.student_repo_name }}
          ref: ${{ github.event.client_payload.student_repo_ref }}
          token: ${{ github.event.client_payload.repo_token }}
          path: student
          # Don't store the token on disk
          persist-credentials: false

      - name: Grade assignment
        run: python grade.py

      - name: Upload grading results
        uses: actions/upload-artifact@v4
        with:
          name: grading-results
          path: results.json
```

If `run-name` must carry extra context, keep `lms_run_id` last, for example
`run-name: Grade ${{ github.event.client_payload.student_repo_name }} ${{ github.event.client_payload.lms_run_id }}`.

## Handling `repo_token` safely

`repo_token` is a short-lived credential delivered in the dispatch payload:

- Pass it only to the checkout step (or a single authenticated fetch). Never echo it,
  write it to a file, or include it in logs, artifacts, or error messages.
- Keep `persist-credentials: false` on the student checkout so it is not written to
  `.git/config`.
- Do not upload the student working tree as an artifact without checking that no
  credential material was written into it.

## Writing the grader

- Fail the *grading logic*, not the *workflow*: a student whose code does not compile
  should produce `score: 0`, not a failed job. A failed workflow yields no artifact, so
  the LMS receives no grade at all. Wrap the grading step so it always writes
  `results.json` and exits 0.
- Treat student code as untrusted input. It runs in the grader job with access to the
  dispatch payload, so avoid exposing organization secrets to that job, and prefer
  running student code with the minimum permissions the checks need.
- Clamp `score` to `[0, max_score]` and keep `max_score` a constant in the grader, not
  something derived from which tests happened to run.
- Use `template_repo_name` to select the right test suite when one grader repository
  serves multiple assignments; fail loudly (with a written `results.json` scoring 0 and
  a clear subtask name) on an unrecognized template rather than grading against the
  wrong suite.
- Keep `subtasks` names stable across runs so students can compare feedback between
  submissions.
