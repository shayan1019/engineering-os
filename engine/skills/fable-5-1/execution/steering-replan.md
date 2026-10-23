# Steering-Replan Discipline (native EOS methodology)

provenance — not a model or provider dependency. Any compatible AI follows this; runtime
capabilities only decide how efficiently, never whether.*

> **Authority boundary**: scope impact is `../core/scope-contract.md`'s call; graph impact
> is `../graph/task-graph.md`'s call; this file states the replanning conduct. Timing
> (instant vs. checkpoint) is `../runtime/steering-behavior.md`'s mechanism decision,
> driven by the `mid_turn_steering` flag (`../runtime/capability-model.md`).

## Rules

1. **Replan, never discard-and-restart.** Every new instruction routes through scope-impact
   check → graph-impact analysis → cancel/preserve/add — not by appending it to whatever
   is running.
2. **Preserve completed valid work.** Verified nodes (`../graph/verification-diamond.md`),
   constraints, writer ownership, pending approvals, and gathered evidence survive steering
   and `../graph/bounded-repair.md`-style replans unless explicitly overridden. Silence
   about a prior constraint is not permission to drop it.

> Invariant: **runtime capability changes execution efficiency, not EOS methodology.**

## What this file must never do

Claim mid-turn steering ability, treat a new instruction as ownership transfer, or
condition any rule on a provider or model name.
