# Change Impact Preflight

*New synthesis (Graphify integration, Faz 3). Defines when a repo-graph
preflight query runs before a mutation — REQUIRED / CONDITIONAL / OPTIONAL /
OFF — so trivial tasks pay zero graph overhead and large tasks never mutate
blind.*

> **Authority boundary**: this file decides *whether* a preflight query runs
> and what it must establish. It does not perform the mutation, does not
> replace `policy/risk-contract.md`'s gates, and a clean preflight never
> counts as verification (`graph/verification-diamond.md`,
> `evidence/verification.md`).

## Preflight matrix (frozen)

```text
preflight REQUIRED:
  GRAPH
  MULTI_AGENT_GRAPH
  EVIDENCE_GRAPH

preflight CONDITIONAL:
  PLANNED + multi-module mutation
  PLANNED + uncertain blast radius

preflight OPTIONAL:
  DIRECT (single cheap query when it saves broader search)

preflight OFF:
  ATOMIC (no graph call; solve directly)
```

When in doubt between CONDITIONAL and skipping: ask "could this mutation
touch a second module I haven't read?" Yes → run the preflight. No → skip.

## What a preflight establishes (minimum)

1. **Candidate symbols/files** for the change (query/neighbors, bounded —
   see below), each with `source_location`.
2. **Impacted neighborhood**: direct callers + callees (depth ≤ 2 default),
   plus the tests that reference them (`affected` / reverse traversal).
3. **Freshness verdict** from `runtime/graphify-adapter.md`: graph current
   or STALE. STALE → preflight is void; fall back to direct
   search/read and note the staleness.
4. **Coverage verdict** from `graph/repo-graph-contract.md`: full or
   PARTIAL (e.g. MQL5 in scope). PARTIAL → the uncovered part gets the
   textual/LSP fallback, explicitly recorded.

## Bounded context (token discipline)

A preflight never dumps the raw subgraph into context. Budget:

```text
max_graph_nodes_in_context: 25
shape: top impacted symbols + direct callers + direct callees
       + relevant tests + critical paths (each: label + file:line)
prefer_symbol_ranges_over_full_files: true
deduplicate_already_read_sources: true
expand_context_only_on_evidence_gap: true
```

If the query returns hundreds of nodes (observed: 1100-node BFS hit on the
BigHero pilot), narrow with `--budget`, `--dfs`, relation filters, or
`get_node` on the specific symbol — do not raise the context budget.

## How it feeds the task graph

Preflight output becomes an *input* to `graph/task-graph.md`: real
dependencies shape the DAG, the impacted neighborhood sizes write sets
(`graph/writer-and-merge-owner.md` — overlapping write sets still block),
and the relevant-tests list seeds targeted testing before full regression.
After mutation, re-query the impacted neighborhood (graph-diff) and run
targeted tests → full regression → Fable completion gate.
