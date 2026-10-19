# Graphify Integration Tests

*Acceptance record for the Fable 5.1 + Graphify provider integration.
Checklist items are verified with real filesystem/command evidence per
`core/completion-model.md` — "created" is not "working". Pilot evidence
(BigHero, 2026-09-23) is quoted inline with the command that produced it.*

## Pilot evidence (BigHero `6.0_final_merge`, graphify 0.9.66)

- Install: `graphify install --project --platform agents` wrote ONLY
  `.agents/skills/graphify/` (SKILL.md + references + version); `AGENTS.md`
  untouched (pre-existing `CLAUDE.md` session-memory edit is from a parallel
  live session, not the installer — content is trading-session notes).
- Build: `graphify extract . --code-only` → 862 code files, 14200 nodes,
  31792 edges, 693 communities, ~68 s, fully offline.
- Confidence split: EXTRACTED 26640 / INFERRED 5152 (native tags preserved).
- MQL5 coverage: **0 nodes** with `.mq5`/`.mqh` source → explicit PARTIAL.
- Query: `graphify query "manage endpoint experience_memory write path"`
  finds `manage_endpoint()` (`api/routes/manage.py:555`) + `ExperienceMemory`
  (`ai_native/experience_memory.py:227`); 1100-node BFS hit truncated at
  budget — the reason for the 25-node context cap.
- CLI/MCP parity: `explain ExperienceMemory` ≡ MCP `get_node{label}` output;
  `path` ≡ `shortest_path` (both need undirected for that pair).
- MCP: 10 tools live
  (`query_graph get_node get_neighbors get_community god_nodes graph_stats
  shortest_path list_prs get_pr_impact triage_prs`).

## Regression checklist (must stay green)

```text
[ ] route_tests.py 14/14 PASS (unchanged routing contract)
[ ] validate_skills.py exit 0 (contract still valid)
[ ] validate_workspace.py exit 0
[ ] root AGENTS.md hash unchanged by provider install
[ ] provider absent → full Fable flow works (fallback path exercised)
[ ] MCP dead → CLI answers the same query
[ ] STALE graph → preflight void, fallback recorded
[ ] EXTRACTED/INFERRED distinction preserved end-to-end
[ ] multi-file Python change → preflight query ran before mutation
[ ] overlapping write sets still blocked (single-writer, merge-owner = 1)
[ ] graph result cannot satisfy/bypass risk gate alone
[ ] MQL5 in scope → PARTIAL declared, LSP/textual fallback recorded
[ ] provider AGENTS.md/hook surface untouched (pilot posture)
[ ] per-project rollback (delete skill dir + graphify-out) is clean
```

## Token/latency metrics (same task set, Baseline vs Fable+Graphify)

Record per task:

```text
input_tokens, tool_result_tokens, files_opened, full_files_opened,
symbol/range_reads, duplicate_reads, graph_queries,
graph_nodes_returned, graph_nodes_injected_to_context,
targeted_tests_selected, total_task_tokens
```

KPI: `CONTEXT_EFFICIENCY = useful_source_tokens / total_source_tokens_injected`

```text
Quality:    NEW_REGRESSION = 0, verification coverage >= baseline
Efficiency: duplicate_reads ↓, full_file_reads ↓,
            irrelevant source context ↓, total task input tokens ↓
Latency:    ATOMIC overhead ≈ 0, GRAPH preflight overhead bounded
Correctness: stale → unused; INFERRED-only → no high-risk authority;
            MQL5 → PARTIAL explicit
```

## Faz 6 measured pilot (2026-09-23, "/manage → experience_memory write path")

Mode: STALE-suggest (parallel live sessions moved the tree mid-pilot —
`tracked_source_fingerprint_changed` + `relevant_untracked_source_exists`;
gate correctly refused CURRENT). Preflight ran as retrieval suggest, every
claim re-checked against source before use.

```text
graph_queries:               4 CLI (query/explain/path/affected) + 4 MCP
graph_nodes_returned:        ~1100 (query BFS, budget-truncated to 50 shown)
graph_nodes_injected:        0 (bounded-context discipline held)
files_opened:                1 (experience_memory.py:260-339, 80 lines)
full_files_opened:           0 (baseline naive flow: 3 files / 3131 lines)
duplicate_reads:             0
targeted_tests_selected:     test_ai_native_experience_memory.py (from affected set)
```

Preflight finding (source-verified, not graph-authorized): `_save()`
(L271) is write-behind — marks dirty + ensures single writer thread,
`flush()` (L318) persists snapshot outside the lock; callers
`get_memory()` (L588), `mature_pending_episodes()`,
`run_background()`, health stats wired through the affected set.
MQL5 side: 0 graph nodes → PARTIAL stands, no claim made about `.mqh` path.

## Faz 4 hook (freshness checker — implemented)

`.agents/tools/graph_freshness.py` (stdlib-only) +

- `graph_build_head` (git HEAD at build), `working_tree_fingerprint`
  (tracked-source hash + `git status --porcelain` dirty scan),
  `indexed_file_count`, `indexed_languages`, `generated_at`,
  `coverage: full|partial`.
- STALE if: head changed, fingerprint changed, relevant untracked source
  exists, or metadata missing. Checker exit 0 = current, 1 = stale.
