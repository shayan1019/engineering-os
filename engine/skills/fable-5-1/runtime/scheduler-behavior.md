# Scheduler Behavior


> **Authority boundary**: Graph Engineering decides *which* nodes may execute concurrently (`graph/parallelism.md`'s safe-parallelism checklist). This file decides *whether the current runtime can actually exploit that* — a strictly weaker, execution-layer question. Correctness must never depend on which answer this file gives.

## The distinction

```text
Graph Engineering: "These nodes MAY execute concurrently."
Runtime layer:      "I can/cannot actually execute them concurrently right now."
```

The graph's answer doesn't change based on runtime capability. The runtime's answer only ever narrows what the graph already permitted — it never widens it. A node the graph marked as sequential-only is never run concurrently no matter what `parallel_tool_support` says.

## A — Provider-independent execution principles (always apply, regardless of provider)

*Canonical statement: `../execution/pending-work.md`, `../execution/work-continuity.md`,
`../execution/delegation-discipline.md`, `../execution/steering-replan.md`,
`../execution/state-continuity.md` — this section applies that methodology to scheduling;
on any conflict the execution/ files win. Section B below decides mechanism only.*

These are EOS working discipline distilled in part from general execution
patterns, stated here as native methodology — not as claims that any provider provider/API
is present. They apply even when every capability flag below is `false`/`unknown`:

- maintain explicit pending/ready work awareness — consult the graph's ready frontier
  (`graph/parallelism.md`, `graph/task-graph.md`) instead of tracking only the current node.
- when one branch is blocked (tool pending, external wait), look for independent ready
  work instead of idling — see section B for how the mechanism differs by capability.
- distinguish worker/agent-as-tool from ownership handoff — most delegation is
  agent-as-tool; handoff requires explicit transfer (see subagents section below and
  `graph/writer-and-merge-owner.md`'s `worker_is_not_merge_owner_unless_assigned`).
- preserve completed valid work during steering/replan — verified nodes
  (`graph/verification-diamond.md`), constraints, ownership, approvals, and evidence
  survive unless explicitly overridden (`runtime/steering-behavior.md`).
- use structured intermediate/node-result discipline even when native structured-output
  is unavailable — fixed labeled-field textual contract (`runtime/state-behavior.md`).
- steering-aware impact/replan discipline — scope check → graph impact analysis →
  cancel/preserve/add (`runtime/steering-behavior.md`, `core/scope-contract.md`).
- maintain task state explicitly enough to resume/reconstruct safely
  (`runtime/state-behavior.md`'s "what EOS owns" list, checked reconstruction).

> Invariant: **runtime capability changes execution efficiency, not EOS methodology.**

## B — Capability acceleration (gated; mechanism only)

### `parallel_tool_support = true`

The scheduler may execute the graph's ready frontier — the set of nodes `graph/parallelism.md` has already cleared as safe to run together — concurrently, batched in one turn where the runtime supports it.

### `parallel_tool_support = false` (or unknown)

Execute the exact same valid graph sequentially, one ready node at a time, in any order consistent with the graph's dependency edges (`graph/task-graph.md`). This is not a degraded result — it is the same graph, the same acceptance criteria, the same eventual outcome, just realized without concurrency.

### Async / blocked-work behavior — `async_tools`

Section A's blocked-work principle applies in both cases below; only the mechanism differs.

If `async_tools = true`: a blocked external/tool branch must not force the entire task to idle when other independent ready nodes exist. Consult the graph's ready frontier and continue productive independent work while the blocked branch is pending, rather than waiting on it before touching anything else.

If `async_tools = false` (or unknown): apply the same principle sequentially — execute another ready independent node sequentially when one exists, instead of idling on the blocked branch; block/wait normally only when no other ready work exists. Do not change the graph's semantics or the task's acceptance criteria. A slower execution order is an acceptable cost; a different result is not.

## Subagents — folds in the plan's point 6, not a separate file

`subagents = true`: `fable-5-1` may exploit the native fan-out `graph/parallelism.md` and `graph/writer-and-merge-owner.md` already describe, by delegating a node to a separate agent context instead of handling it inline. Two distinct delegation shapes, kept distinct:

- **agent-as-tool / worker** — the manager/`fable-5-1` retains ownership of the final task; the delegate's output comes back as an input to further work the manager still owns.
- **handoff** — ownership is explicitly transferred; the delegate becomes responsible for the outcome, not just a step feeding back to the manager.

Not every delegated task is a handoff — most are agent-as-tool. Per `graph/writer-and-merge-owner.md`'s `worker_is_not_merge_owner_unless_assigned` invariant: merge ownership never transfers implicitly just because a specialist was delegated work. A handoff has to be stated, not inferred from "someone else did this part."

`subagents = false` (or unknown): execute specialist nodes through the current model/runtime sequentially, inline. The same graph, the same contracts, the same worker/merge-owner distinctions remain valid — only the mechanism (separate context vs. inline) changes.

## What this file must never do

Change which nodes exist, their dependencies, their write-set conflicts, or their acceptance criteria. Those are entirely `graph/*.md`'s domain (via `graph/task-graph.md`, `graph/parallelism.md`, `graph/writer-and-merge-owner.md`). This file only decides execution order, batching, and delegation mechanism within whatever the graph already permits.
