---
name: debug
description: Diagnoses augpool failures with focused, secret-safe evidence before proposing or implementing a fix. Use when behavior is unexpected, tests or CLI commands fail, or root-cause evidence is requested.
---

# Debug

## Quick start

1. Classify the symptom.
2. Reproduce it with the cheapest faithful path.
3. Trace backward from the failure to the owning code.
4. State root cause and evidence.
5. If a fix is requested, use the `tdd` skill.
6. Remove temporary artifacts and instrumentation.

Diagnosis alone does not authorize production changes.

## Triage

| Symptom | Start with |
|---|---|
| Argument parsing, output, exit code | `cli.py`, `tests/test_cli.py` |
| Missing/wrong account or files | `pool.py`, `session_io.py`, `paths.py` |
| Wrong account selected | `select.py`, `state.py`, selection tests |
| Usage refresh/cache error | `analytics.py`, analytics tests, injected HTTP fake |
| Child exit or failover error | `runner.py`, runner tests, disposable child process |

Run the smallest existing test first:

```bash
python3 -m pytest -q tests/test_runner.py -k failover
```

If no test covers the symptom, create a temporary minimal reproduction outside
production code. Convert it into a failing regression test only when fixing.

## Evidence rules

- Capture the exact command, exit code, error, and smallest relevant state transition.
- Use `tmp_path` or a temporary `AUGPOOL_HOME`; never inspect or mutate real user
  credentials when a fake session reproduces the path.
- Stub network calls through existing injected functions. Do not spend real credits
  or call analytics with live tokens without explicit authorization.
- Trace data across boundaries: CLI input -> pool/state -> selection -> runner/output.
- Prefer deterministic clocks, fakes, and subprocesses over sleeps or broad mocks.

## Secret safety

Never print token values, share blobs, complete session JSON, or auth environment
variables. Redact by field name and location. If a command unexpectedly emits a
secret, do not repeat it in the report; identify the source and advise rotation.

Temporary logging must exclude secrets and be removed before delivery. Keep new
persistent logging only when the user requested it and it has ongoing value.

## Root-cause report

Report:

- symptom and affected path;
- faithful reproduction command or test;
- evidence linking failure to owning code;
- root cause, or strongest hypothesis plus remaining unknown;
- proposed regression test and fix boundary;
- cleanup performed.

Do not call a guess a root cause. Reproduce locally before changing code whenever
the failure is deterministic.
