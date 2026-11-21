# EOS 0.3.3 — Continuous Project Awareness & Enforcement

Date: 2026-11-21
Base: `eos/persistent-0.3.2` @ `45561e4f797f`
Decision: `edr-20261121T075227-918867` (STANDARD)

## 1. Source-audit findings fixed

| Finding | Fix |
| --- | --- |
| activation plugin cut at 2400 while Python produced 6000 | `MAX_BLOCK_CHARS = 6000` in both |
| activation failure returned an empty context | `unavailableBlock()` renders `status="UNAVAILABLE"`; the session is told not to assume EOS is active |
| activation cache keyed only by task | key includes session and `.agents/state/context.epoch`; a post-write touch changes the epoch |
| compaction reused the old block | `compaction` hook clears the session cache |
| preflight received an empty session | plugin passes the real `sessionID`; `eos_activate.py --session` forwards it |
| project map only resolved registered profiles | `--project-path` and `PROJECTS.local.json`/`.eos/projects.json` resolution |
| new files in a dirty tree were missed | `verify` rescans the source set; new/deleted files are STALE reasons |
| map had no parser abstraction | Python AST, JS/TS regex, MQL lexical adapters with `parser` and `coverage_state` |
| map CURRENT was confused with full knowledge | `coverage_state: FULL|PARTIAL|NONE` is separate from freshness |
| guard checked only an EDR | `write_gate.py` checks project, map, Git/dirty, EDR, session and work ownership |
| `start --new` could replace an open task | it now requires same session or `--takeover --reason` |
| EDR had no session owner | `session_id` is recorded; another session's decision is refused |

## 2. Post-write refresh

The guard calls `post_write.py touch` after an allowed write:

- adds the file to `dirty_files` in the map;
- re-seals the map (so it is STALE until refreshed);
- bumps `.agents/state/context.epoch`, invalidating the activation cache.

`post_write.py refresh` (and the next preflight) recomputes the map and clears
the dirty set. A risky write cannot proceed on a STALE map; an ATOMIC write may,
and remains marked dirty.

## 3. Write gate policy

| Map | Decision | Result |
| --- | --- | --- |
| INVALID | any | BLOCKED |
| MISSING/STALE | ATOMIC | allowed, marked dirty |
| MISSING/STALE | STANDARD/CRITICAL | BLOCKED until refresh |
| CURRENT | valid EDR | allowed |
| CURRENT | none | needs_decision (guard prepares an ATOMIC EDR) |
| any | another session's EDR | BLOCKED |
| any | another session's open task | BLOCKED |

This is the missing mechanical link between "a map exists" and "the map is used".

## 4. Live host evidence

1. A live write was attempted while `ses_live033` owned the open work task by a
   different host session: **DENY** with "work task is owned by session
   ses_live033".
2. After closing that task, the same request produced:
   - file `engineering-os/.agents/evidence/auto-033b.txt` created (`OK`);
   - decision `edr-20261121T080955-ae8f91` **APPROVED_FOR_EDIT, ATOMIC**;
   - map **STALE** with reason `post-write dirty files: .agents/evidence/auto-033b.txt`.
3. `post_write refresh` restored **CURRENT**.

This is the first end-to-end proof of: session ownership -> automatic EDR ->
write -> post-write dirty -> refresh.

## 5. Test results

| Suite | Result |
| --- | --- |
| `project_map` | **8/8** |
| `work_state` | **6/6** |
| `preflight` | **4/4** |
| `post_write` | **3/3** |
| `write_gate` | **4/4** |
| `auto_decision` | **8/8** |
| `vnext_dev` | **PASS 13/13** |
| `vnext` | **PASS 5/5** |
| `regression` | **PASS 4/4** |

## 6. Remaining gaps

| Item | Status |
| --- | --- |
| Activation failure block live test | code path exists; not exercised against a deliberately broken engine |
| Per-file incremental map cache | changed-file dirty + full rebuild on refresh; no per-module cache |
| Graphify integration | provider is not installed; `graphify=MISSING` |
| Fable 5.2 + Astra 6.2 live team | BLOCKED: provider 402 |
| Codex / Claude Code / BLACKBOX live Always-On | NOT TESTED |
| P5 scenario benchmark / P6 metrics | NOT BUILT |
| BigHero full-suite baseline | PENDING; `unit` gate BLOCKED |
| Untracked `engineering-os/.agents/selftest-map` | test artifact from an earlier preflight run; not committed |

## 7. Evidence

- `.agents/evidence/map/eos/PROJECT_MAP.json`
- `.agents/evidence/tools/ses_e02d9565fffe9eAZChfWow1Bq7.jsonl` (ownership DENY)
- `.agents/evidence/tools/ses_*.jsonl` (auto ATOMIC + post-write)
- `.agents/evidence/decisions/eos/edr-20261121T080955-ae8f91.json`
- `.agents/evidence/gates/eos/gate-vnext_dev-*.json`
