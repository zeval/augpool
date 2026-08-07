---
name: commit
description: Creates focused augpool commits with staged-diff review and Conventional Commit messages. Use when the user asks to commit, save, checkpoint, prepare a PR, or choose a commit or PR title.
---

# Commit

## Safety gate

Never commit on `main` or `master`. Work on a feature branch and preserve unrelated
changes. Do not amend published history or force-push unless the user explicitly
requests that rewrite.

```bash
git status --short --branch
git branch --show-current
git diff
git diff --cached
```

## Workflow

1. Read `AGENTS.md` and the task-specific validation requirements.
2. Confirm every changed file belongs to the requested intent.
3. Run focused checks before staging. Do not commit known failures.
4. Stage explicit paths; avoid sweeping unrelated files into the commit.
5. Inspect `git diff --cached --check`, `--stat`, and the full staged diff.
6. Check the staged diff for credentials, session data, and generated artifacts.
7. Choose one Conventional Commit message matching the primary intent.
8. Commit, then verify `git status` and `git show --stat --oneline HEAD`.

## Message format

```text
<type>[optional scope][!]: <imperative summary>

[optional body explaining why and observable impact]

[optional footer(s)]
```

Use lowercase types:

- `feat`: new user-facing capability.
- `fix`: bug correction.
- `docs`: documentation only.
- `test`: test-only change.
- `refactor`: behavior-preserving code restructuring.
- `perf`: performance improvement.
- `build`: packaging, build tools, or dependencies.
- `ci`: GitHub Actions or automation.
- `style`: formatting with no behavior change.
- `chore`: maintenance not covered above.
- `revert`: revert an earlier commit; identify it in the body/footer.

A scope is a short code-area noun such as `cli`, `runner`, `session`, or
`analytics`. Omit it when the change is repository-wide. Keep the subject
imperative, specific, concise, and without a trailing period.

## Breaking changes

Append `!` before `:` and describe migration impact in a footer:

```text
feat(session)!: reject legacy share blobs

BREAKING CHANGE: import now accepts only version 2 share blobs.
```

## Examples

```text
feat(cli): add JSON output to status
fix(runner): fail over after HTTP 429
docs: explain stable PATH installation
ci: test supported Python versions
```

Never use `WIP`, `updates`, `fix stuff`, or generated tool-attribution footers.
PR titles follow the same format because squash merge turns the title into `main`
history.
