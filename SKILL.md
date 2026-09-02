---
name: jetbrains-academy-lms
description: Build apps or scripts that read or operate the JetBrains Academy teaching LMS, including course assignments, enrollments, submissions, and gradebook workflows.
---

# JetBrains Academy LMS

Use this skill for tools that integrate with the JetBrains Academy teaching LMS. The available reference is reverse-engineered from observed browser traffic, not an official or stable public API. Read [the API reference](references/api-reference.md) before implementing LMS requests.

## Working approach

- Use an authenticated session supplied or authorized by the user. Do not request, retain, log, or reuse captured session cookies, CSRF tokens, or HAR files.
- Discover course, assignment, enrollment, and personal-assignment IDs dynamically. Never build production workflows around example IDs.
- Treat reads and mutations differently. Verify the target course, assignment, and student before a write; show the intended change and obtain user authorization immediately before a mutating LMS request.
- For state-changing calls, preserve Django CSRF behavior and the content type observed in the reference. Do not assume undocumented write endpoints or response shapes.
- Build against observable fields and handle missing fields, pagination, permissions, authentication failure, rate limits, and unexpected status values gracefully. The observed `not_submitted` status is not a complete enum.
- Keep JetBrains LMS data separate from GitHub Classroom data. When `solutionFormat` is `github`, use the LMS for assignments and student records; use GitHub Classroom only when the user has authorized its separate grade workflow.

## Implementation boundaries

Prefer read-only dashboards, exports, and dry-run modes by default. Make mutation-capable scripts require explicit inputs for the course, assignment, student, and requested change, and return the server response without inventing success semantics.

The submission form POST is only partially observed. Do not automate review submission or file upload unless the user explicitly requests it and the resulting form behavior is verified in that authenticated context.

## Reference source

This skill was derived from a shared ChatGPT conversation containing three uploaded artifacts and a synthesized API reference. The artifacts themselves were not available for extraction; the reference records only behavior reported as observed.
