# JetBrains Academy LMS Skill

A skill for building apps, scripts, and autograders that interact with the JetBrains
Academy teaching LMS. It works with Claude Code and Codex.

## What's in it

- `skills/jetbrains-academy-lms/SKILL.md` — the skill itself.
- `.../references/github-assignments.md` — LMS-native GitHub assignments: template
  repositories, student forks, the `grade` `repository_dispatch` payload, and the
  `grading-results` / `results.json` grader contract.
- `.../references/api-reference.md` — reverse-engineered teaching API: authentication,
  course discovery, assignments, enrollments, personal assignments, score audit log,
  assignment add/edit forms, review form.
- `.../references/client.md` and `.../scripts/` — `ja_lms`, a dependency-free Python
  client and `ja-lms` CLI: pluggable session credentials with update/refresh, read
  endpoints, and dry-run-first assignment add/edit.

## Using the client

Python 3.8+, standard library only. Nothing to install. Run it from the installed
skill (`~/.claude/skills/jetbrains-academy-lms/scripts`, see [Install](#install)) or
from `skills/jetbrains-academy-lms/scripts` in a clone:

```sh
cd ~/.claude/skills/jetbrains-academy-lms/scripts
./ja-lms auth set                       # paste sessionid/csrftoken from your browser (hidden input)
./ja-lms courses
./ja-lms assignments 42
./ja-lms assignment edit 208 --deadline "2030-10-10 23:59"           # dry run: shows the diff
./ja-lms assignment edit 208 --deadline "2030-10-10 23:59" --apply   # submits
```

Credentials are never stored in this repository. They come from `JA_LMS_SESSIONID` /
`JA_LMS_CSRFTOKEN`, or from `~/.config/ja-lms/credentials.json` (mode 600), or from a
provider you plug in. See [`references/client.md`](skills/jetbrains-academy-lms/references/client.md).

Run the offline tests with `python -m unittest discover -s tests`.

## Install

The skill is the `skills/jetbrains-academy-lms` folder: `SKILL.md`, `references/`,
and `scripts/` (the client). Copy the whole folder into your agent's skills
directory.

### Claude Code

Personal skill (available in every project):

```sh
git clone --depth 1 https://github.com/l3sssia/jetbrains-academy-lms-skill.git
mkdir -p ~/.claude/skills
cp -r jetbrains-academy-lms-skill/skills/jetbrains-academy-lms ~/.claude/skills/
```

Project skill (only inside one repository): run the same from the repository root,
copying into `.claude/skills/` instead of `~/.claude/skills/`.

Restart Claude Code; the skill shows up as `jetbrains-academy-lms`.

### Codex

```sh
git clone --depth 1 https://github.com/l3sssia/jetbrains-academy-lms-skill.git
mkdir -p ~/.codex/skills
cp -r jetbrains-academy-lms-skill/skills/jetbrains-academy-lms ~/.codex/skills/
```

### Updating

Pull the clone and copy the folder again (`git -C jetbrains-academy-lms-skill pull`,
then the same `cp -r`). Your LMS credentials live in `~/.config/ja-lms/` and are not
touched by reinstalling.

## Disclaimer

The repository owner is **not responsible for this skill, the JetBrains Academy LMS, its API, or any associated service**. The teaching API reference is based on reverse-engineered, observed behavior, and this skill is not affiliated with or endorsed by JetBrains.

Use it only with appropriate authorization and verify behavior in your own environment.
