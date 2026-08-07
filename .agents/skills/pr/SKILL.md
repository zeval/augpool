---
name: pr
description: Publishes a verified augpool feature branch as a GitHub pull request without writing to the default branch. Use when the user asks to open, publish, or prepare a pull request.
---

# Pull Request

## Related skill

Use `commit` to review, stage, and create the Conventional Commit before
publishing the branch.

## Safety gate

All changes go through a PR. Never commit on or push directly to `main` or
`master`. Never bypass branch protection, force-push, or merge without explicit
user instruction.

```bash
git status --short --branch
git branch --show-current
git remote get-url origin
```

If the current branch is a default branch, create a descriptive feature branch
before committing. Push only the checked-out branch:

```bash
git push -u origin HEAD
```

Never run `git push origin main` or an equivalent refspec.

## Preflight

1. Read `AGENTS.md`, current diff, and `.github/pull_request_template.md`.
2. Confirm diff contains only intended work and no credentials or session artifacts.
3. Run task-specific checks. For code changes, run focused tests and
   `python3 -m pytest -q`; for docs/harness changes, run structural checks and
   `git diff --check`.
4. Use the `commit` skill to create a verified Conventional Commit.
5. Confirm branch is ahead of the default branch and remote contains local `HEAD`.

Do not publish with uncommitted changes or known failing required checks. Report a
blocker instead of weakening validation.

## Build PR body

Use `.github/pull_request_template.md`; do not compose a substitute. Fill every
required section from final diff and actual results. Remove HTML instructions,
placeholders, and unused optional sections. Keep checklist items unchecked for
reviewer confirmation. Do not add agent/tool attribution or invented issue links.

Use a temporary body file so multiline Markdown stays intact:

```bash
gh pr create \
  --title "type(scope): short summary" \
  --body-file /tmp/augpool-pr-body.md
```

PR title becomes squash-merge history, so it must use the same Conventional
Commit format as the branch commit.

## After creation

- Read PR URL and body back; verify target is `main`, sections render, and no
  placeholder or credential appears.
- Report URL, commit, checks run, and any checks still pending.
- Do not merge the PR. Maintainer review and branch protection own that step.
- If CI fails, reproduce the exact failure locally before editing. Use `debug`
  for diagnosis and `tdd` for behavior fixes.

## Branch protection expectation

GitHub must require a pull request for `main`, enforce the rule for administrators,
and disallow force pushes and deletion. Repository guidance controls agents;
GitHub protection provides server-side enforcement.
