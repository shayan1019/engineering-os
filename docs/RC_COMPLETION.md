# RC Completion — Evidence Regressions and Executable P5-C

Date: 2026-11-21
Base: `eos/rc-evidence-0.6.2` @ `e2e037dbba12`
Decision: `edr-20261121T110952-4dcc80` (STANDARD)

## 1. Evidence regression audit (red → green)

Five gaps were added as negative tests **before** the fix. The first run was red
(4 failing cases); after the fix the P6 selftest is **22/22**.

| # | Finding | Red evidence | Fix |
| --- | --- | --- | --- |
| 1 | `corrupt`/`wrong_schema`/`unsealed`/`unverifiable` P5 beside a valid one still produced VERIFIED | isolated `verdict_of` case returned VERIFIED | verdict treats those states as PARTIAL |
| 2 | A suite failure became inactive when another suite ran later | alpha FAIL at T03 was inactive because beta ran at T04 | activity is computed per `(suite, failure, baseline)` against that suite's latest run |
| 3 | An older benchmark version blocked the delta between current runs | `comparable:false`, empty delta | historical versions are listed but do not block current-version deltas |
| 4 | Two artifacts with the same `run_id` and different content were silently a copy | `conflicts` was absent | same `run_id` + different `content_sha256` → `CONFLICT`, verdict PARTIAL |
| 5 | A changed gate JSON `project` field was not compared to the log header | state `ok` | log `project` is compared like suite and verdict |

Also audited: P5 `content_sha256` is recomputed; gate argv/rc/timeout are
compared block-by-block; legacy artifacts stay visible and are excluded from
trends; active proposals require the failure to be present in the latest run of
its own suite; provenance remains **UNSIGNED** and `trusted_provenance:false`.

P6 report after the fix: **VERIFIED, evidence=OK, provenance=UNSIGNED,
22/22 selftest**.

## 2. Executable P5-C runner

`engine/tools/p5c_runner.py validate` creates three isolated fixtures
(mathematics, authorization, backend contract) plus a timeout case:

- the oracle is written **outside** the repository and `oracle_in_repo` is
  asserted false for both variants;
- the broken variant must fail, the fixed variant must pass;
- a slow oracle is killed at 0.4 s and reported as `timed_out`, not PASS;
- the artifact is sealed and carries host/source metadata;
- the model verdict is separate: `BLOCKED`, `result_claimed:false`.

Result: **4/4 mechanical PASS**, model **BLOCKED**. `run --model <id>` exits 3
with BLOCKED; it does not invent a result.

Not built: the model call itself (no provider), tool/skill evidence capture
during an agent run, session-resume measurement, token/cost accounting. Those
require a model run.

## 3. P6 improvement experiment

The evidence regression was used as the controlled experiment:

| Stage | Measurement |
| --- | --- |
| Baseline | P6 selftest 17/17, five latent gaps not covered |
| Hypothesis | the verdict/activity/dedup rules are wrong, not the artifacts |
| Negative test | 5 new cases; run was **red** (4 failures: suite activity, project field, run_id conflict, delta) |
| Fix | suite-scoped activity, version-scoped delta, conflict state, project check, invalid-state verdict |
| Same benchmark | P6 selftest **22/22** |
| Regression | `vnext_dev`, `vnext`, `regression` gates re-run |

No routing, policy or security rule was changed automatically.

## 4. Real project pilots

| Project | Stack | Result |
| --- | --- | --- |
| AURA | Python | 72 sources, 229 edges |
| vpniran | Python | 330 sources, 336 edges |
| **indicator** | **Pine Script** | **0 sources** — `.pine` has no adapter |

The third pilot is a genuine finding: EOS does **not** discover Pine Script
sources, so a real non-Python project maps as empty. The claim "universal
project discovery" is therefore **FAIL** for Pine until an adapter exists.
No project file was written.

## 5. Always-On host test

**NOT_TESTED.** This environment has no harness that opens a fresh OpenCode
session in a new directory with tools. The discovery mechanism is unit-tested
(`auto-<folder>`), but the user journey (new folder, no EOS command, activation,
map, EDR, write denial, post-write refresh, resume) is not proven live.

## 6. Verdict

| Criterion | Status |
| --- | --- |
| P6 does not VERIFY with invalid evidence | **PASS** |
| Ongoing failure is not falsely inactive | **PASS** |
| Copy vs `run_id` conflict separated | **PASS** |
| P5-C runner runs from the CLI | **PASS** |
| Broken/healthy oracle validated | **PASS** |
| Mechanical runner PASS without a model | **PASS** |
| New OpenCode session, no command | **NOT_TESTED** |
| Non-Python real project pilot | **FAIL (Pine: 0 sources)** |
| P6 measured improvement experiment | **PASS** |
| P5-C model benchmark | **BLOCKED (402)** |
| Codex / Claude / BLACKBOX host | **NOT_TESTED** |

## 7. Evidence

- `.agents/evidence/gates/eos/gate-p6-20261121T111449Z.json` (selftest 22/22)
- `.agents/evidence/gates/eos/gate-p5c-20261121T111618Z.json` (4/4 mechanical)
- `.agents/evidence/p5c/validate-20261121T111618Z.json`
- `.agents/evidence/map/indicator/PROJECT_MAP.json` (0 sources)
