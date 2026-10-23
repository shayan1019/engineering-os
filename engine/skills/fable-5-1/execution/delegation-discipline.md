# Delegation Discipline (native EOS methodology)

provenance — not a model or provider dependency. Any compatible AI follows this; runtime
capabilities only decide how efficiently, never whether.*

> **Authority boundary**: ownership rules live in `../graph/writer-and-merge-owner.md`
> (`worker_is_not_merge_owner_unless_assigned`); fan-out safety lives in
> `../graph/parallelism.md`; verification lives in `../graph/verification-diamond.md`.
> This file states the delegation conduct; the subagent-vs-inline mechanism is
> `../runtime/scheduler-behavior.md`'s decision, driven by the `subagents` flag
> (`../runtime/capability-model.md`).

## Rules

1. **Worker vs. handoff, kept distinct.** Most delegation is agent-as-tool: the manager
   keeps final ownership and the delegate's output feeds back into work the manager still
   owns. A handoff transfers ownership and must be stated explicitly — never inferred from
   "someone else did this part."
2. **Manager retains final ownership.** Delegating work — to a subagent where `subagents`
   holds, inline where it does not — never dilutes the manager's responsibility for the
   outcome. Same graph, same contracts; only the mechanism changes.
3. **Multi-agent orchestration discipline.** Fan out only over the graph-cleared ready
   frontier, verify in a separate context, merge under one merge owner. Never merge
   findings without an owner of the merge.

> Invariant: **runtime capability changes execution efficiency, not EOS methodology.**

## What this file must never do

Claim subagent availability, transfer merge ownership implicitly, or condition any rule
on a provider or model name.
