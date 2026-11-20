# EOS vNext 0.3.0 — P0–P3 evidence report

Date: 2026-11-20/21
Branch: `engineering-os` @ `0.3.0-dev`
Scope delivered: P0 (reality probe), P1 (decision enforcement), P2 (system awareness),
P3 (typed team runtime + model policy). P4–P6 are not in this report.

Everything below was executed on this machine. A claim without an artifact path is
marked as a limitation, not as done.

## 1. P0 — reality probe

| Fact | Evidence |
| --- | --- |
| Host is OpenCode 2.2.5 | `opencode --version` |
| Product tree HEAD before work | `9d63f95` (0.2.5 records) |
| Installed CLI 0.2.6 engine is byte-identical to the product engine | SHA-256 of `quality_gate.py` in `~/.eos/versions/0.2.6-alpha/engine` and `engineering-os/engine` |
| Version markers disagreed | `.eos/project.json` 0.2.0, `.agents/.eos-install.json` 0.2.5, `eos.exe` 0.2.6 |
| `ctx.tool.hook("execute.before")` exists and throwing blocks a write | live probe: write refused, target file absent |
| `ctx.permission.hook("evaluate")` exists and `input.effect="deny"` blocks a shell write | live probe: `Set-Content` refused, file absent |
| `execute.before` for `write` and `shell` carries **no args** | live probe log: `Object.keys(args) == []` |
| `permission.evaluate` carries the command in `resources` | live probe: `{"action":"shell","resources":["Set-Content ..."]}` |
| Both premium models are listed | `opencode models`: `opencode/claude-fable-5-2`, `opencode/gpt-6.2-astra` |
| Listing is not invocation | no authenticated invoke was run; `MODEL_POLICY.json` is PROVISIONAL |

Consequence: path/command enforcement is implemented in the permission hook, not in
`execute.before`. A guard that only hooks `execute.before` would deny every write
without a path (observed) and cannot inspect shell at all.

## 2. P1 — Engineering Decision Record

`engine/tools/decision.py`, `engine/tools/challenge.py`.

State machine: `DRAFT -> REVIEWED -> APPROVED_FOR_EDIT -> IMPLEMENTED -> VERIFIED -> CLOSED`,
with `BLOCKED`/`FAILED` side states. Two gates:

- **write gate**: `require-edit --path` must find an APPROVED_FOR_EDIT or IMPLEMENTED
  record whose scope covers the path, whose hash is valid, and which is not stale.
- **promotion gate**: `promote.py` now requires `--decision <id>`; the decision must be
  CLOSED, its gate must be a verified PASS, and the supplied artifact must hash to the
  one the decision was verified against.

Mechanical proofs (all real, no mocks):

| Test | Result |
| --- | --- |
| `decision.py selftest` | 14/14 |
| `challenge.py selftest` | 6/6 (including project-id traversal rejection) |
| `_fixture_gate` forged PASS with a changed command record | refused |
| decision edited outside the tool | hash mismatch, refused |
| gate artifact edited after verification | refused |
| promotion with a tampered decision | refused |
| live host write without an EDR | denied, file absent |
| live host write with an ATOMIC EDR | allowed, file created with the expected content |
| live shell write (`Set-Content`) | denied, file absent |
| live read-only shell (`Get-Content`) | allowed |

Live host evidence: `.agents/evidence/tools/*.jsonl` (capability, permission and
after records). The guard was observed to hot-reload after a file edit: a
backslash-path `decision.py` command that the old allowlist denied succeeded after
the fix.

### Guard hardening found during the work

1. `decision.py open/approve` was not allowlisted, so the guard locked out the tool
   that creates the authorisation. Fixed: EOS state tools are allowlisted.
2. `impact.py --out`, `pytest_delta --baseline`, `team.py run`, `model_probe invoke`
   can write or execute arbitrary targets. They are **not** in the shell allowlist.
3. `;`, `&&`, `||`, backticks and `$(` are denied outside quotes; one command per call.
4. `->` and `2>&1` no longer look like redirection.

`node --test engine/host-plugins/opencode/eos-guard.test.mjs`: 10/10.

## 3. P2 — impact and system awareness

`engine/tools/impact.py` + `engine/registry/topology/`.

- Python AST import graph (HIGH), lexical symbol references (MEDIUM, only symbols
  defined once in the project), JS relative imports (MEDIUM), MQL lexical only (LOW,
  PARTIAL).
- Output lists changed files, edges, tests, contracts, components, coverage and
  `unknowns`. It never drops an unknown.
- `CROSS_PROCESS` is raised when changed files belong to components with different
  `process` values.

BigHero topology (`engine/registry/topology/bighero.json`) is read-only and evidence-backed:

| Component | Process | Port | Evidence |
| --- | --- | --- | --- |
| `trading_api` | `python_ai/app.py` (uvicorn) | 127.0.0.1:8000 | `app.py:609` |
| `observability_web` | `tools/observability_web.py` | 127.0.0.1:8765 | `observability_web.py:560` |
| `supervisor` | `tools/server_watchdog.py` | none | `AGENTS.md` O1.3/O1.4 |
| `launcher` | `tools/bighero_launcher.py` | none | `bighero_launcher.py:43-49,142` |
| `scheduler` | `-m research.counterfactual_scheduler` | none | `bighero_launcher.py:142` |
| `mt5_ea` | MetaTrader terminal | none | `Modules/PythonDecisionBridge.mqh` |

Finding that contradicts a documented assumption: the observability process is not
purely read-only. `observability/position_projection.py:17,35` writes
`.local_position_projection.db` (a display-only projection, revision-guarded). The
topology records this instead of repeating "GET-only".

`impact.py --project bighero --files python_ai/app.py --files python_ai/observability/position_projection.py`
returns `cross_process: true`, components `trading_api` + `observability_web`, and the
unknown "an in-memory object shared between them is an invalid assumption".

## 4. P3 — typed team runtime and model policy

`engine/tools/team.py`, `engine/registry/MODEL_POLICY.json`.

- Risk selects the team: ATOMIC 2, STANDARD 4, CRITICAL 5–6 nodes.
- Each node has a role, agent, model, family, dependency list and prompt file.
- A worker returns one JSON object; a result that fails the schema fails the node.
- `merge` writes findings into a challenge ledger; open blockers keep the decision
  open (proved end-to-end in `team.py selftest` 5/5).
- Family diversity is enforced for CRITICAL: architect vs logic-reviewer and
  implementer vs verifier must differ.
- `MODEL_POLICY.json` is PROVISIONAL: model IDs were listed, not benchmarked. A plan
  refuses to staff without `--allow-provisional` or a measured scorecard.
- Four read-only roles were added and deployed: `researcher` (the only role with web,
  external content untrusted), `dependency-analyst`, `logic-reviewer`, `bug-hunter`.
  `runtime_permissions.py check-contracts`: 13 agents, 0 problems.

## 5. Differential regression

`engine/tools/pytest_delta.py` turns an all-or-nothing pytest suite into a delta:

- no new failure and no disappeared test -> PASS (known failures recorded);
- a failure not in the baseline -> FAIL (new regression);
- a baseline test that is gone -> FAIL (coverage loss);
- no baseline -> BLOCKED (exit 2), never a fake green.

`pytest_delta.py selftest` 5/5. `quality_gate.py` now maps a required command that
exits 2 to BLOCKED instead of FAIL; `eos/blocked` fixture proves it.

The BigHero `unit` suite now uses this wrapper against
`.agents/registry/failures/bighero-unit.json`. **That baseline is not produced yet**:
the 2026-10-19 run recorded 17 failures but stored only a command exit code, not the
JUnit node IDs. A full re-measurement was started and stopped after 25 minutes at
10% (estimated 4+ hours); inventing 17 IDs from the pytest cache would be a fabricated
baseline. The suite therefore returns BLOCKED until the documented command in
`engine/registry/failures/README.md` is run deliberately.

## 6. What is not done / remaining risks

| Item | Status |
| --- | --- |
| BigHero full-suite failure baseline | PENDING measurement; gate BLOCKED |
| Scenario bench (6 planted defects) | not built (P5) |
| `eos ship` / automatic branch push | not built (P5); direct `git add/commit/push` remains denied |
| Skill **usage** evidence (read events) | P4 |
| Tool execution evidence beyond the guard recorder | partial (P4) |
| Test adequacy / mutation | not built (P5) |
| Model benchmark and role scores | not measured; policy PROVISIONAL |
| Cloud topology (Contabo/PostgreSQL) | not inspected; do not infer from local |
| MQL parser | absent; MQL impact PARTIAL |
| Deployment of the multi-plugin `plugin_install.py` to `.agents` | product updated; the deployed copy is the older single-plugin installer until the next local install |
| Git commit/push | not performed; direct git writes are denied by design |

## 7. Evidence index

- `.agents/evidence/gates/eos/gate-vnext-20261120T212102Z.json` — deployed vNext gate, PASS
- `/Users/...` is never used; all paths are workspace-relative
- `.agents/evidence/decisions/eos/edr-20261120T211151-1fbe13.json` — guard fix (ATOMIC)
- `.agents/evidence/decisions/eos/edr-20261120T211636-d9915b.json` — hardening + report (STANDARD)
- `.agents/evidence/tools/*.jsonl` — live host permission/after records
- `~/.eos/evidence/models/model-probe-*.json` — model list evidence
- `engine/registry/topology/bighero.json` — read-only topology
- `engine/registry/failures/README.md` — baseline procedure and pending state
