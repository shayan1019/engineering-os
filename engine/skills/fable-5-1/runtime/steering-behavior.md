# Steering Behavior


> **Authority boundary**: this file governs *when/how* a new instruction reaches execution. It does not itself decide whether the new instruction changes scope — that determination is `core/scope-contract.md`'s, applied fresh each time steering occurs.

## `mid_turn_steering = true`

New user instructions may be incorporated while execution is active, through this sequence — not by simply appending the new instruction to whatever is currently running:

```text
new instruction
    v
scope impact check          (core/scope-contract.md: does this change what "done" means?)
    v
constraint / acceptance update
    v
graph impact analysis       (graph/task-graph.md: which nodes does this actually touch?)
    v
cancel / preserve / add nodes, as appropriate
    v
continue
```

## What must survive a steering event unless explicitly overridden

The new instruction does not silently discard in-flight state. Unless it explicitly says otherwise, preserve:

- existing accepted constraints (`core/scope-contract.md`)
- do-not-regress requirements (`core/do-not-regress.md`)
- completed valid work (nodes already verified — `graph/verification-diamond.md`)
- current writer ownership (`graph/writer-and-merge-owner.md`)
- pending approvals (`graph/task-graph.md`'s human-gate nodes)
- evidence already gathered (`core/execution-discipline.md`'s evidence-per-claim material)

A new instruction that touches one of these does so explicitly — "actually, don't do X anymore" — not by omission. Silence about a prior constraint is not permission to drop it.

## `mid_turn_steering = false` (or unknown)

Use a checkpoint/replan fallback instead: finish the current node (or reach the next natural checkpoint) rather than interrupting mid-node, then run the same scope-impact-check → graph-impact-analysis sequence as a fresh planning pass that incorporates the new instruction — effectively a `graph/bounded-repair.md`-style *replan*, not a silent redirect. The outcome is the same discipline as the `true` case; only the timing (at a checkpoint, not instantly) differs.

## What this file must never do

Treat a new instruction as ownership-transfer, delegation, or a reason to bypass `graph/writer-and-merge-owner.md`'s ownership rules. Steering changes *what* is being worked on; it does not change *who* owns what mid-flight unless the instruction explicitly reassigns it.
