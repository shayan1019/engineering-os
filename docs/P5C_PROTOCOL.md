# P5-C — Independent Model Benchmark Protocol

Status: **BLOCKED** on model access (provider returned HTTP 402 on 2026-11-20).
This document defines the protocol and the case format. No model result is
claimed until a provider is reachable.

## 1. What P5-C must measure

The question is not "can the model run tests". It is:

> Given a repository with a real defect and no hint about its location, does the
> agent find it, fix it without breaking anything, and prove the fix?

P5-B measures runtime invariants. P5-C measures the engineering agent.

## 2. Case format

Each case is a directory plus a manifest:

```
case/
  repo/            the project the agent receives
  oracle/          hidden tests, never visible to the agent
  case.json        id, category, broken_expectation, fixed_expectation
```

`case.json` is **not** mounted into the agent workspace. The runner copies only
`repo/` and passes a task in natural language.

```json
{
  "id": "authz-003",
  "category": "security",
  "task": "The service rejects valid requests and accepts some invalid ones. Make the test suite pass.",
  "broken": { "cmd": ["python", "oracle/run.py"], "expect_exit": 1 },
  "fixed":  { "cmd": ["python", "oracle/run.py"], "expect_exit": 0 },
  "hidden_tests": ["oracle/test_authz.py"],
  "critical": true
}
```

The prompt never names the file, function, line or defect class.

## 3. Case set

| Category | Broken fixture | Oracle |
| --- | --- | --- |
| web/frontend | a state update that drops a concurrent change | DOM/state assertions |
| backend/API | a response schema drift | contract test |
| python | an off-by-one boundary | unit + boundary tests |
| desktop/mobile | a lifecycle callback that runs twice | event-count oracle |
| security | missing authorization check | allow/deny matrix |
| math | wrong formula and unit mismatch | numeric oracle |
| cross-process | two workers claim one job | single-owner oracle |
| multi-project | a change in project A breaks B | cross-project contract test |
| new project | an empty project with a required behaviour | acceptance test |

Each case ships a healthy control variant. A run must pass both the broken and
the healthy case to count.

## 4. Runner

```
p5c run --provider <id> --model <id> --case <id> [--repeat N]
```

For each case and repeat:

1. Copy `repo/` to an isolated directory with its own EOS workspace.
2. Run the broken oracle; it must fail. Otherwise the case is invalid.
3. Start the agent with the natural-language task only.
4. Let the agent work under the EOS guard (no manual EOS commands are given).
5. Run the fixed oracle and the healthy control.
6. Record tool evidence from `.agents/evidence/tools/*.jsonl`.
7. Record the model probe artifact (tokens, cost, duration).
8. Destroy the directory.

## 5. Metrics

| Metric | Definition |
| --- | --- |
| true positive | intended defect found and fixed; oracle passes |
| false positive | the agent reports or "fixes" a defect that is not in the case |
| false PASS | the agent claims success while the oracle still fails |
| false BLOCK | the agent refuses a healthy control |
| missed critical | a `critical: true` case is not fixed |
| regression rate | healthy tests fail after the change |
| tool use | write/test/read/gate events per case |
| skill use | router-selected skill actually read |
| context retention | map/work-state evidence used after a session restart |
| cost | tokens, USD, wall time per case |
| reruns | attempts needed before the oracle passes |

A result is **BLOCKED**, never PASS, when the provider is unreachable or the
model returns no tool-capable output.

## 6. Anti-cheat

- Case ids and file names are randomised per run.
- The prompt contains no stack trace, file path or line number.
- The judge runs the oracle, not the agent's claim.
- The agent may not edit `oracle/`; the runner mounts it read-only after the
  agent finishes.
- A case with a suspiciously fast pass is re-run in a fresh directory.
- The same case is run against a healthy control; a detector that always says
  "broken" fails the control.

## 7. Current blocker

Fable 5.2 and Astra 6.2 probes both returned HTTP 402 on 2026-11-20. The
protocol and fixtures can be prepared, but no detection rate, cost or false
PASS number may be published until a provider responds.
