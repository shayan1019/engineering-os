# Pending-Work Discipline (native EOS methodology)

provenance — not a model or provider dependency. Any compatible AI follows this; runtime
capabilities only decide how efficiently, never whether.*

> **Authority boundary**: EOS owns scope/completion policy (`../core/scope-contract.md`,
> `../core/execution-discipline.md`); Graph Engineering decides which nodes are ready and
> safe (`../graph/task-graph.md`, `../graph/parallelism.md`); this file governs how the
> agent tracks pending vs. ready work and switches between them. The concurrent-vs-sequential
> mechanism is `../runtime/scheduler-behavior.md`'s decision, driven by
> `../runtime/capability-model.md` flags.

## Rules

1. **Explicit pending/ready tracking.** Maintain the pending set and the ready frontier
   explicitly — never track only the current node.
2. **Blocked branch → switch, don't idle.** When one branch is blocked (tool pending,
   external wait), advance another ready independent node instead of waiting on the
   blocked branch.
3. **Pending tool calls don't freeze the task.** While a tool result is pending, seek other
   doable work the graph offers; block/wait normally only when no other ready work exists.
4. **Waiting is a scheduling event.** Reconsult the ready frontier on every block; surface
   the blocker explicitly (`../runtime/state-behavior.md`'s unresolved-blockers item)
   rather than stalling silently.

> Invariant: **runtime capability changes execution efficiency, not EOS methodology.**

## What this file must never do

Claim async/parallel execution ability (those are capability flags with
`../runtime/fallback-behavior.md` safe paths), redefine which nodes are schedulable
(`../graph/*.md`'s domain), or condition any rule on a provider or model name.
