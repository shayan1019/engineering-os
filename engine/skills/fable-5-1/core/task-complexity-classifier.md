# Task complexity classifier

Choose the lowest level that can carry the work. Escalating "to be safe" adds
ceremony without adding evidence.

| Level | Use when | Artifact |
| --- | --- | --- |
| `ATOMIC` | One answer, one lookup, one-line edit, no dependencies | None |
| `DIRECT` | A short sequence of steps, no parallel writes, low risk | Mental plan |
| `PLANNED` | Multiple files or a real design decision, but mostly sequential | Written steps + acceptance |
| `GRAPH` | Genuine independent branches, dependencies, or concurrent writers | Task graph + merge owner |
| `MULTI_AGENT_GRAPH` | GRAPH work split across agents with distinct contexts | Graph + delegation contract |
| `EVIDENCE_GRAPH` | The deliverable is itself a provenance-heavy graph or audit | Graph + evidence schema |

## Rules

- Default is `DIRECT`. `EVIDENCE_GRAPH` is never a default; it is an explicit
  escalation.
- A task with one writer and no independent branches is not a GRAPH task, even
  if it touches several files.
- A task with parallel branches must have disjoint write sets and a named merge
  owner.
- If new information invalidates the level, reclassify before continuing.
- The classifier selects process, not importance. A high-risk task can still be
  DIRECT; it needs an approval gate, not a bigger diagram.

## Quick test

Ask: *Can I finish this with a short sequence of steps I can hold in mind?*
If yes, stay at DIRECT. If not, ask which of PLANNED, GRAPH, MULTI_AGENT_GRAPH,
or EVIDENCE_GRAPH actually applies. Write down the reason for the level you
choose.
