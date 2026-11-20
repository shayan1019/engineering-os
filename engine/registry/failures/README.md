# Failure baselines

A differential suite needs a measured baseline. The baseline is a JSON file
written by `pytest_delta.py --update-baseline`; it is never generated
implicitly and never committed without a review of the failure list.

## Status

| Suite | Baseline | State |
| --- | --- | --- |
| `bighero/unit` | `bighero-unit.json` | **PENDING MEASUREMENT** |

The 2026-10-19 measurement recorded 4138 passed, 17 failed, 2 skipped in 813 s.
Those 17 are real and are not hidden. Their node IDs are not recorded anywhere:
the old gate artifact only stored a command exit code, not a JUnit report, so
the list cannot be reconstructed. Inventing 17 IDs from the pytest cache would
be a fabricated baseline.

Until a full run has produced the baseline, `bighero/unit` returns **BLOCKED**
(exit code 2), not FAIL and not PASS. That is deliberate: a gate that cannot
answer must not look like a verdict.

## Produce the baseline

Run from `6.0_final_merge` with the project interpreter, once, deliberately:

```
.venv-win\Scripts\python.exe <engine>\tools\pytest_delta.py ^
  --project bighero --suite unit ^
  --baseline <workspace>\.agents\registry\failures\bighero-unit.json ^
  --update-baseline -- python_ai\tests -q --no-header -p no:cacheprovider
```

Then review the file:

- every entry in `failures` must be a defect that pre-dates the change under
  review, and the reason must be known;
- `identity` must match the interpreter and pytest that will run the gate;
- commit the reviewed file.

After that, every run reports:

- `NEW FAILURES` — not in the baseline: a regression, FAIL;
- `STILL FAILING` — known: recorded, does not block;
- `FIXED` — no longer failing: update the baseline after review;
- `DISAPPEARED TESTS` — a baseline test is gone: coverage loss, FAIL.

## Do not

- Do not hand-write a baseline.
- Do not copy a baseline from another machine or interpreter.
- Do not mark a new failure as known to make the gate green.
