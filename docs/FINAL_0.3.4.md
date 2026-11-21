# EOS 0.3.4 — Awareness Enforcement, Real Incremental Map, and Concurrency

Date: 2026-11-21
Base: `eos/awareness-0.3.3-testfix` @ `0d134f7560a5`
Decision: `edr-20261121T083412-0ee9ad` (STANDARD)
Supporting: `edr-20261121T085539-5ee942` (impact text cache)

This release closes the six source-level defects reported after 0.3.3 and
replaces the "dirty until someone refreshes" model with a real post-write
incremental update.

## 1. Reported defects

| Defect | 0.3.3 behaviour | 0.3.4 behaviour | Test |
| --- | --- | --- | --- |
| ATOMIC allowed on a stale map | `policy` returned `allow` for `ATOMIC` + `STALE` | every tier is refused while the map is `MISSING`/`STALE`/`INVALID` | `write_gate` "policy blocks every tier on a stale map" |
| Dirty marked before the write | `execute.before` called `post_write touch` | the allowed path is queued; `execute.after` dequeues it and only then touches | guard contract "an allowed write touches the map only after the write executes"; "a denied write never touches the map" |
| Corrupt work state swallowed | `except SystemExit: pass` | `SystemExit`, `ValueError` and `OSError` become a `BLOCKED` verdict with the reason | `write_gate` "a corrupt work state blocks instead of being swallowed" |
| Git branch changes ignored | `verify` compared HEAD and dirty only | branch name is compared and reported as `git branch changed` | `project_map` "a Git branch change makes the map STALE" |
| Partial parsers counted as FULL | JS/MQL modules carried `parse: OK`; `coverage_state` could be `FULL` | non-AST adapters are always `parse: PARTIAL`; `FULL` requires pure AST, no unknowns, no unsupported files and a used topology | `project_map` "a JS adapter is reported PARTIAL, never a full parse" |
| No real concurrency lock | ownership was a field comparison with no lease | `acquire_lock` is an `O_CREAT|O_EXCL` lease with TTL, held for the whole read-modify-write of `start`/`update`/`close` | `work_state` "a live lease blocks another session and a stale lease is reclaimed"; "two real processes cannot both become the writer" |

## 2. Two further defects found during the work

| Defect | Fix | Evidence |
| --- | --- | --- |
| `impact.read_text` cached by path only, so an in-process incremental update re-parsed the previous bytes | cache key is `(path, mtime_ns, size)` | the post-write selftest reads the new `def added()` |
| Agents could write their own evidence and decisions, and could name evidence as an edit scope | `write_gate` refuses `.agents/evidence`, `.agents/state`, `.opencode/plugin`, `.eos`; `auto_decision` refuses the same as a scope | `write_gate` "control-plane evidence paths are refused"; `auto_decision` "control-plane evidence cannot be an edit scope" |

## 3. Incremental map

`post_write touch` now calls `project_map.update_files`:

- parses only the changed files (Python AST, JS regex, MQL lexical);
- recomputes import edges from the stored module table;
- recomputes contracts and tests for the changed files;
- deletes removed files from sources and modules;
- **skips** files that are inside the project but are not sources (evidence,
  artifacts, logs); they bump the context epoch and never pollute the map;
- re-seals the map, so the next `write_gate` sees `CURRENT`;
- falls back to `mark_dirty` only when the map cannot be updated.

A failed write produces no touch: the queue entry is consumed by
`execute.after`, which the host does not run for a denied or failed call.

## 4. Live host evidence

Session `ses_e02a49094ffekpiZ1UfugIZPPB`:

```
permission write ... auto-034.txt  ALLOW  decision=edr-20261121T090645-88688f  reason="auto ATOMIC decision"
post-write        ... auto-034.txt  TOUCHED  map_state=""
```

The file exists, the map stayed `CURRENT` immediately after the write, and no
`post-write` record exists for the denied writes from the 0.3.3 run.

## 5. Gates

| Suite | Result |
| --- | --- |
| `project_map` | 10/10 |
| `work_state` | 8/8 |
| `preflight` | 4/4 |
| `post_write` | 4/4 |
| `write_gate` | 7/7 |
| `auto_decision` | 9/9 |
| guard contract | 12/12 |
| activation contract | 20/20 |
| `vnext_dev` | PASS 14/14 |
| `vnext` | PASS 5/5 |
| `regression` | PASS 4/4 |
| `deploy` | PASS 2/2 |

## 6. Not done

| Item | Status |
| --- | --- |
| Live stale-map write denial | policy and fixture only; a live stale write requires an out-of-band source edit, which was not produced |
| P5 universal benchmark | NOT BUILT |
| P6 quality metrics | NOT BUILT |
| Fable 5.2 + Astra 6.2 live team | BLOCKED (provider 402) |
| Codex / Claude Code / BLACKBOX always-on | NOT TESTED on this machine |
| Graphify topology | MISSING (provider not installed) |
| BigHero full suite | PENDING (`unit` gate BLOCKED) |
| Rename detection | a rename is handled as delete + add on the next scan, not as a rename event |

## 7. Evidence

- `.agents/evidence/gates/eos/gate-vnext_dev-20261121T090356Z.json` (14/14, before deploy)
- `.agents/evidence/gates/eos/gate-vnext-20261121T090437Z.json`
- `.agents/evidence/gates/eos/gate-regression-20261121T090454Z.json`
- `.agents/evidence/gates/eos/gate-deploy-20261121T090501Z.json`
- `.agents/evidence/tools/ses_e02a49094ffekpiZ1UfugIZPPB.jsonl` (live auto ATOMIC + post-write)
- `.agents/evidence/map/eos/PROJECT_MAP.json`
