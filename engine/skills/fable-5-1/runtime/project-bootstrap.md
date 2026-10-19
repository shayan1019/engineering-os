# Project Bootstrap (universal, lazy, project-local)

*New synthesis (Faz 8). Makes Fable 5.1 universal across projects while the
Graphify provider stays auto-discovered and always project-local. Executed
via `.agents/tools/project_bootstrap.py` (stdlib-only).*

> **Authority boundary**: this file decides *when* bootstrap runs and what it
> may touch. It never builds a task graph (`graph/*.md`), never classifies
> risk (`policy/risk-contract.md`), and never redefines completion
> (`core/completion-model.md`).

## Separation (frozen)

```text
GLOBAL (control plane)
  Fable methodology, bootstrap/discovery logic, capability detection

PROJECT-LOCAL (per project root)
  Graphify skill (.agents/skills/graphify/)
  graph index (graphify-out/)
  graph.meta.json + freshness state
  .graphifyignore
```

Forbidden, always: global Graphify install, provider-driven changes to any
`AGENTS.md`/`CLAUDE.md`/`GEMINI.md`, cross-project graph/state sharing,
re-writing an existing working project setup.

## Lazy trigger (no build on shell init)

Terminal/directory entry performs **capability detection only** (cheap
`--check-only`: root + language scan, no install, no build). A graph build
starts only on the first Fable coding task that needs project intelligence:

```text
terminal opens / cd new-project
  → detection only (fast, no CPU spike)
first Fable coding task (PLANNED or higher, needs repo structure)
  → state check: MISSING → bootstrap → build → query
  →           STALE   → update → query
  →           CURRENT → reuse, query immediately
next session, nothing changed
  → CURRENT → reuse (no reinstall, no rebuild)
```

- ATOMIC tasks never trigger install or build.
- DIRECT tasks may use an existing CURRENT graph, never build one.
- Absent/dead provider never blocks Fable: the task continues with
  grep/read scoping (`runtime/graphify-adapter.md` fallback table).

## What the tool does (in order)

1. Project-root detection (nearest ancestor with a project marker, else cwd).
2. Supported vs unparsed language detection (bounded walk; MQL5-class
   extensions reported as `PARTIAL`, never silent FULL).
3. Install check (`<root>/.agents/skills/graphify/SKILL.md`); missing +
   `--init` → project-scope install only, then assert instruction files
   are byte-identical (report-only guard, never auto-restore).
4. State check via `graph_freshness.py` → `CURRENT` / `STALE` / `MISSING`.
5. `--init`: build when MISSING/STALE (`extract --code-only`), then write
   the freshness snapshot. `--update`: rebuild only when STALE.
6. Report CLI/MCP availability and the full result as one JSON object for
   Fable to consume (installed, scope, graph_state, cli, mcp, coverage).

## Rollout rule (replaces manual per-project rollout)

No manual BigHero → AURA → … sequence. A project gets the provider the
first time Fable needs repo intelligence inside it; BigHero's existing
working setup is never re-written (bootstrap detects `installed: true` +
existing graph and stops at the state check).
