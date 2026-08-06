---
name: tdd
description: Implements augpool behavior changes with strict Red-Green-Refactor and hermetic pytest coverage. Use for bug fixes, new features, refactors, or any production-code change that should preserve a tested contract.
---

# Test-Driven Development

## Iron rule

Do not write production code before a test fails for the expected behavioral
reason. If implementation came first, remove it and restart from the test.

Skip this workflow for documentation-only, agent-harness, generated, or inert
configuration changes. Still validate their structure and diff.

## Choose test scope

- CLI parsing/output: `tests/test_cli.py`.
- Session validation, blobs, backups, atomic writes: `tests/test_session_io.py`.
- Ranking and cooldown behavior: `tests/test_select.py`.
- Analytics mapping/cache/network fallbacks: `tests/test_analytics.py`.
- Child execution, protocol mode, failover: `tests/test_runner.py`.
- Shared temporary homes and accounts: `tests/conftest.py`.

Prefer behavior and state assertions over internal call assertions. Mock only
external network, time, or process boundaries when a deterministic fake is clearer.

## Red

1. Name one missing or broken behavior.
2. Add the smallest regression test with clear inputs and expected output/state.
3. Run its exact node and confirm expected assertion failure, not setup/import failure.
4. If it passes, improve the test until it proves the missing behavior.

```bash
python3 -m pytest -q tests/test_select.py::test_descriptive_name
```

## Green

Implement only enough production code to pass the failing test. Do not bundle
unrelated cleanup or speculative cases. Re-run the exact test and record result.

## Refactor

Improve names, duplication, and control flow without changing behavior. Keep tests
readable as specifications. Re-run focused tests after every behavior-affecting edit.

## Hermetic test rules

- Use fake tokens, `tmp_path`, and injected roots; never touch `~/.augpool` or
  `~/.augment`.
- Inject HTTP functions and clocks. Do not use real analytics requests.
- Use deterministic synchronization for locking/concurrency; avoid timing sleeps.
- Assert file modes and atomic behavior when credential writes change.
- Cover failure paths without exposing secret values in assertion messages.

## Final verification

Run affected files, then full suite and diff check:

```bash
python3 -m pytest -q tests/test_<area>.py
python3 -m pytest -q
git diff --check
```

Report exact commands and outcomes. Never claim tests not run. A fix without a
regression test is incomplete unless behavior is genuinely untestable and the
reason is stated.
