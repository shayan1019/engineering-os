# Repository graph adapter (optional)

This document describes how EOS talks to an optional repository-graph provider.
The provider is not bundled and is not required. Without it, EOS uses direct
file search and reading.

## Authority boundary

- `graph/repo-graph-contract.md` defines what a repository graph is and what it
  may not decide.
- `graph/change-impact-preflight.md` decides when a query is useful.
- This file only describes access mechanics and fallback behavior.

The provider never changes scope, risk, acceptance, or completion rules.

## Detection

The adapter treats the provider as available only when both are true:

1. the provider is installed for the project (not globally);
2. its graph file exists and parses.

Record the evidence tier from `runtime/capability-model.md`:

- **configured** — a project-local provider directory and a parseable graph
  exist;
- **runtime-detected** — a query actually returned in this session;
- **unknown** — anything else. Treat as absent.

## Access paths

Use whichever interface the project provides:

- **CLI** — `query`, `path`, `explain`, and `affected` style commands. Prefer
  the CLI for a terminal-driven preflight.
- **MCP** — a stdio server exposing query, node, neighbor, community, and path
  operations. Use it when the host supports MCP and the server is healthy.

The default graph location is a project-local output directory; the exact path
is provider-specific. Never assume a global graph or a shared cache.

## Freshness

A query is usable only when the graph's metadata is current for:

- the repository commit;
- the working-tree fingerprint;
- the indexed file count and time.

If the commit changed, the tracked source changed, or relevant untracked source
exists, the graph is `STALE`. A stale graph may suggest search targets; it is
not evidence for an authorization decision.

## Fallback

| Condition | Behavior |
| --- | --- |
| Provider or graph absent | Direct search and read; the task proceeds unchanged. |
| Graph file corrupt | Same as absent; record the condition. Do not rebuild in the middle of a small task. |
| MCP unavailable, CLI healthy | Use the CLI. |
| CLI unavailable, MCP healthy | Use the MCP server. |
| Both unavailable | Direct search; record the outage. |
| `STALE` | Treat as absent for authorization; use only as a search hint. |

No fallback widens permission. An unavailable provider removes a retrieval
optimization, never a risk gate.

## Environment notes

- A provider's MCP extra may require a native build. If the build fails, install
  the base package first and add the MCP runtime from a binary wheel; record the
  workaround in the project's own notes.
- A stdio MCP server may not reply to notifications. Test harnesses should not
  block on a read after one.

## Rollback

The provider's footprint is project-local. Remove its provider directory and
its graph output to restore the original tree. The project's source and host
instruction files must be byte-identical before and after the install.
