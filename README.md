# JetBrains Academy LMS Skill

A skill for building apps, scripts, and autograders that interact with the JetBrains
Academy teaching LMS. It works with Claude Code (as a plugin or a personal skill) and
with Codex.

## What's in it

- `skills/jetbrains-academy-lms/SKILL.md` — the skill itself.
- `.../references/github-assignments.md` — LMS-native GitHub assignments: template
  repositories, student forks, the `grade` `repository_dispatch` payload, and the
  `grading-results` / `results.json` grader contract.
- `.../references/api-reference.md` — reverse-engineered teaching API: assignments,
  enrollments, personal assignments, score audit log, review form.

## Install

### Claude Code — as a plugin

```
/plugin marketplace add l3sssia/jetbrains-academy-lms-skill
/plugin install jetbrains-academy-lms@jetbrains-academy-lms-skill
```

### Claude Code — as a personal or project skill

```sh
git clone https://github.com/l3sssia/jetbrains-academy-lms-skill.git /tmp/ja-lms
cp -r /tmp/ja-lms/skills/jetbrains-academy-lms ~/.claude/skills/
```

Use `.claude/skills/` inside a repository instead to scope it to one project.

### Codex

```sh
git clone https://github.com/l3sssia/jetbrains-academy-lms-skill.git /tmp/ja-lms
cp -r /tmp/ja-lms/skills/jetbrains-academy-lms ~/.codex/skills/
```

## Disclaimer

The repository owner is **not responsible for this skill/plugin, the JetBrains Academy LMS, its API, or any associated service**. The teaching API reference is based on reverse-engineered, observed behavior, and this skill is not affiliated with or endorsed by JetBrains.

Use it only with appropriate authorization and verify behavior in your own environment.
