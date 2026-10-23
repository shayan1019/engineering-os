# Parallelism

Parallel work is a scheduling decision, not a mood. Run branches concurrently
only when they are actually independent.

## Independence test

All must be true:

1. **Disjoint writes.** No two branches write the same file or the same
   generated artifact.
2. **No shared mutable state.** No shared database row, cache key, branch, or
   process that one branch can change under another.
3. **Defined join.** The merge order and the owner of the combined result are
   named before the branches start.
4. **Verifiable together.** The combined result has one acceptance check.

If any condition is false, run the work sequentially.

## Fan-out and join

```text
        -> branch A -> join -> verify
start -- -> branch B -> join
        -> branch C -> join
```

- Each branch has one writer.
- The join is a real node with an owner. It is not "whatever finishes last."
- The join resolves conflicts; it does not silently pick a side.
- A failed branch does not get retried by a different branch.

## Shared files

A shared file is a serialization point. Either:

- one owner makes all edits, or
- the branches produce patches and the merge owner applies them in a defined
  order.

Never let two agents write the same file and hope the edits do not overlap.

## Communication overhead

Parallelism has a cost: merge work, context duplication, and coordination. Do
not split a job that one writer can finish faster than the split can be
explained. The graph should be simpler than the work it coordinates.

## Failure

If a branch fails, the join is blocked until the failure is resolved, the branch
is removed from scope, or the graph is replanned. Do not mark the join complete
because the other branches finished.
