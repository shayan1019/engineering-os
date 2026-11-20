# EOS 0.3.2 — Persistent Project Intelligence & Session Continuity

Date: 2026-11-21
Base: `eos/unchief-0.3.1` @ `a744e63c6538`
Decision: `edr-20261120T224712-005632` (STANDARD)

## 1. Persistent project map

`project_map.py` stores one map per project at
`.agents/evidence/map/<project>/PROJECT_MAP.json`.

Content:

- every source file with SHA-256 and size;
- Python modules, imports, definitions and resolved import edges;
- test files, API/route/schema contracts, topology components;
- coverage, confidence, UNKNOWN;
- Git HEAD, branch and dirty flag;
- a sealed `map_sha256`.

States: `CURRENT`, `STALE`, `MISSING`, `INVALID`.

- `MISSING` → created from real files on preflight.
- `STALE` → incremental update from source hashes on preflight.
- `INVALID` → **BLOCKED**; a corrupt map is never trusted or silently replaced.

Live map for EOS itself:

```
sources: 1951  modules: 45  edges: 54  tests: 2  contracts: 60
git: eos/unchief-0.3.1 @ a744e63c65 dirty=True
coverage: ast=FULL, lexical=PARTIAL, topology=MISSING, graphify=MISSING
```

The map is a persisted snapshot of the same `impact.py` evidence, not a second
truth. Parser-less languages stay `PARTIAL`; MQL is never `FULL`.

## 2. Work state and recovery

`work_state.py` keeps:

```
.agents/evidence/work/<project>/state.json
.agents/evidence/work/<project>/events.jsonl
```

`state.json` carries task, status (`planned/in_progress/blocked/verified/closed`),
session, owner, active decision and challenge, changed files, tests, open
errors, next safe action, map hash and Git state.

`events.jsonl` is append-only and hash-chained; each event carries the state
snapshot it produced. `recover --apply` restores the last valid snapshot after a
corrupt `state.json`. A broken chain is **BLOCKED**.

Concurrent sessions: a different session cannot update or close a task owned by
another session without `--takeover` and a reason. This is tested.

## 3. Preflight

`preflight.py run` answers, before any write:

1. is the path inside the EOS scope (real path);
2. which project owns it;
3. is the map current, and if not, build/update it;
4. does Git HEAD / dirty state match the map;
5. what work is open and what must not regress;
6. is this a continuation or a new task;
7. risk and impact evidence;
8. selected agents and skills;
9. is there an approved EDR, or will the write gate prepare one;
10. the next safe action.

`eos_activate.py` now calls preflight and injects a 6,000-character bounded block
with `map`, `work`, `edr` and blockers. It does not load the repository into the
prompt; it loads the map excerpt and the active state.

## 4. 0.3.1 risks fixed

| Risk | Fix | Test |
| --- | --- | --- |
| `sil` matched inside `nasil` | high-risk words require a word start (`(?<!\w)`) | `nasil` ATOMIC, `silmek` CRITICAL |
| Impact failure still allowed ATOMIC | `impact_required` forces at least STANDARD | `impact failure prevents ATOMIC` |
| `safe_scope` used `abspath`, not real path | `realpath` + real workspace comparison | symlink escape **BLOCKED** |
| Multi-file reuse checked only the first file | every scope path must resolve to the same approved decision | one-file decision not reused for two |
| Workspace root marker swallowed child projects | child `.eos/project.json` and profiles win | decision 17/17 |
| Stale host role permissions | `agent_install` regenerates chief as `subagent` | `runtime_permissions` 13/0 |

## 5. Test results

| Suite | Result |
| --- | --- |
| `project_map selftest` | **5/5** |
| `work_state selftest` | **5/5** |
| `preflight selftest` | **4/4** |
| `auto_decision selftest` | **8/8** |
| `vnext_dev` | **PASS 11/11** |
| `vnext` | **PASS 5/5** |
| `regression` | **PASS 4/4** |
| Router | **20/20** |
| Guard contract | **10/10** |

Live continuity: `work_state start` → `resume` returns the open task; `preflight`
reports `map=CURRENT`, `work=planned`, `edr=APPROVED_FOR_EDIT`,
`continuation=resume`.

## 6. Remaining blockers

| Item | Status |
| --- | --- |
| Fable 5.2 + Astra 6.2 live team | BLOCKED: provider 402 |
| Codex / Claude Code / BLACKBOX live Always-On | NOT TESTED on this machine |
| P5 cross-project scenario benchmark | NOT BUILT |
| P6 metrics loop | NOT BUILT |
| BigHero full-suite baseline | PENDING; `unit` gate BLOCKED |
| Map incremental granularity | full recompute with hash check; per-file caching is a later optimisation |

## 7. Evidence

- `.agents/evidence/map/eos/PROJECT_MAP.json`
- `.agents/evidence/work/eos/state.json` and `events.jsonl`
- `.agents/evidence/gates/eos/gate-vnext_dev-20261120T230017Z.json`
- `.agents/evidence/gates/eos/gate-vnext-20261120T230041Z.json`
- `.agents/evidence/gates/eos/gate-regression-20261120T230039Z.json`
