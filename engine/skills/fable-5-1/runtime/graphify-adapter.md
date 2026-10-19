# Graphify Adapter

*New synthesis (Graphify integration, Faz 3). The only file that touches the
Graphify provider's mechanics: how it is accessed (CLI + MCP), how its
presence is detected, and what happens when it is absent, dead, or stale.*

> **Authority boundary**: execution access only. This file never interprets
> what the graph *means* (`graph/repo-graph-contract.md`) or whether a
> query runs (`graph/change-impact-preflight.md`). On conflict, those files
> win over this one.

## Scope (pilot-frozen)

```text
Graphify project skill       YES  (<project>/.agents/skills/graphify/)
Graphify CLI                 YES  (graphify query/path/explain/affected)
Graphify MCP                 YES  (query_graph/get_node/get_neighbors/shortest_path/…)
Graphify AGENTS injection    NO
Graphify strict hook         NO
```

## Access paths (both live in the pilot)

- **CLI** (`graphify query "<q>" [--dfs] [--budget N]`, `graphify path A B
  [--undirected]`, `graphify explain "<symbol>"`, `graphify affected "X"`):
  primary for terminal-driven preflight. Default graph location
  `<project>/graphify-out/graph.json`; override with `--graph`.
- **MCP** (`graphify-mcp --graph <graph.json>`, stdio): `query_graph`,
  `get_node` (key: `label`), `get_neighbors`, `get_community`, `god_nodes`,
  `graph_stats`, `shortest_path` (mirrors CLI semantics, including the
  directed/undirected distinction), plus `list_prs`/`get_pr_impact`/
  `triage_prs`. CLI/MCP parity verified in the pilot (identical `explain`
  output for `ExperienceMemory`, identical directed-path behavior).

Known environment notes (BigHero pilot, macOS x86_64):

- The provider's `[mcp]` extra can fail to build (`cryptography` maturin/
  cargo toolchain). Workaround that held: install base package, then add the
  MCP runtime with binary-only wheels
  (`uv pip install --python <tool-python> --only-binary=:all: mcp`).
- MCP stdio servers send no response to `notifications/*` — test harnesses
  must not `readline` after a notification.

## Presence detection (capability-model evidence tiers)

The provider is **not** a `capability_model` flag; it is an optional,
per-project tool. Assess per `runtime/capability-model.md` tiers:

- **configured** — `<project>/.agents/skills/graphify/SKILL.md` exists and
  `<project>/graphify-out/graph.json` loads (parseable, node/link keys).
- **runtime-detected** — a `query`/`explain`/MCP call actually returned in
  this session.
- **unknown** — anything else. Treat as absent; use the fallback below.

## Fallback behavior (unavailable → sequential, never blocked)

| Condition | Behavior |
|---|---|
| Skill/graph absent | Whole Fable system runs unchanged; preflight degrades to grep/read scoping. |
| Graph file missing/corrupt | Same as absent; note it, do not rebuild speculatively mid-task (rebuild is a PLANNED-level job of its own). |
| MCP dead, CLI alive | Use CLI for the identical query surface. |
| CLI dead, MCP alive | Use MCP. |
| Both dead | Direct search/read scoping; record the outage in the session. |
| STALE verdict (see below) | Treat as absent for authorization purposes; stale edges may still *suggest* search targets, never evidence. |

No fallback ever widens authority: an unavailable provider removes a
retrieval optimization, never a risk gate.

## Freshness gate (summary; full spec Faz 4)

A preflight query is usable only with a current freshness verdict —
`<project>/graphify-out/graph.meta.json` carrying `graph_build_head`,
`working_tree_fingerprint`, `indexed_file_count`, `indexed_languages`,
`generated_at`, and `coverage: full|partial`. STALE if any of: git HEAD
changed, tracked-source fingerprint changed, relevant untracked source
exists, or metadata missing. Details live in `tests/graphify-integration.md`
(Faz 4 implements the checker).
