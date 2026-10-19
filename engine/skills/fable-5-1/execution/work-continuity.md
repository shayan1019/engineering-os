# Work-Continuity Discipline (native Fable 5.1 methodology)

*New synthesis (Stage-7 soak correction). Provider-independent behavior, Astra-derived
provenance — not a model or provider dependency. Any compatible AI follows this; runtime
capabilities only decide how efficiently, never whether.*

> **Authority boundary**: Fable owns scope/completion policy (`../core/scope-contract.md`,
> `../core/do-not-regress.md`); Graph Engineering supplies task structure
> (`../graph/task-graph.md`); this file governs steady progress on long-horizon work.

## Rules

1. **No idle long-horizon execution.** A long-running task keeps productive work moving;
   blocked or waiting states are scheduling events, not rest states.
2. **Advance the frontier steadily.** Always work the ready frontier
   (`../graph/parallelism.md`); when nothing is ready, block explicitly with the reason
   recorded (`../runtime/state-behavior.md`), never silently.
3. **Continuity across turns.** Scope, acceptance criteria, decisions, completed/pending
   nodes, and blockers travel with the work so progress survives interruptions — the
   persistence mechanism may vary, the continuity requirement does not.

> Invariant: **runtime capability changes execution efficiency, not Fable 5.1 methodology.**

## What this file must never do

Claim persistence or async ability, redefine graph scheduling, or condition any rule on
a provider or model name.
