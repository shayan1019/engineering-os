# Task graph

A task graph is a directed acyclic graph of jobs. A node is a unit of work one
agent can own. An edge means "this job needs the other job's result before it
can start."

## Minimal shape

```text
understand -> design -> [implement A || implement B] -> integrate -> verify
```

That is a sketch, not a template. Draw edges only for real dependencies.

## Node fields

```text
id:
job:
owner:            one writer
inputs:           files, decisions, artifacts
outputs:          files, artifacts, evidence
depends_on:       node ids
acceptance:       the check that closes the node
status:           pending | active | done | failed | blocked
```

## Rules

- **A node is a job, not a file.** Several small edits to one file belong to one
  writer; one logical job may touch several files.
- **No cycles.** If two nodes need each other's result, they are one node or the
  design is wrong.
- **No orphan edges.** An edge must name the result that is required.
- **No implicit fan-out.** Parallel branches need disjoint write sets and a
  merge owner (see `parallelism.md`).
- **No infinite graph.** Every repair loop has a budget and a stop condition.

## Small tasks

For `ATOMIC` and `DIRECT` work, the graph is a mental checklist. Do not create a
diagram for a one-line fix. At `PLANNED` or higher, write the nodes and
dependencies down; the plan is the contract the execution follows.

## State

Carry a small state object through the graph: what was learned, what was
decided, what remains, what is blocked. The state is not a transcript; it is the
minimum needed to resume.

## Stop

Stop when the acceptance checks pass and the evidence is recorded, when a human
gate is reached, or when a dependency is blocked. Do not keep adding nodes to
avoid a diagnosis.
