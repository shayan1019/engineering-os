# Parallelism

*Native Fable 5.1 restatement of `graph-engineering/references/task-graphs.md`'s "The stop rule" — checklist items B2.5, B2.6. Includes one explicit synthesis section (marked below) that formalizes the source's prose decision procedure into a checklist — not present verbatim in the source.*

> **Authority boundary**: see `graph/task-graph.md`. This file decides scheduling only; it does not redefine scope, completion, or evidence requirements.

## Independent work is not automatically parallel

**Independent work ≠ automatically safe to parallelize.** The absence of a fake-edge dependency (`graph/task-graph.md`) is necessary but not sufficient. Different agents working "at the same time" does not by itself imply safe parallel writers.

## The stop-rule decision procedure (from source, B2.5)

1. Ask: where does the work split into pieces that never read each other's results?
2. Split only that. Everything sequential stays with one agent.
3. Never let findings merge without one owner of the merge — see `graph/writer-and-merge-owner.md`.

More agents is not a strategy by itself. The shape of the work decides.

## Why this matters (empirical grounding, from source, B2.6)

From the Google DeepMind × MIT study "Towards a Science of Scaling Agent Systems" (180 controlled configurations): coordinated teams beat a single agent by ~80% on work that splits into independent pieces — and every multi-agent configuration *lost* on sequential work where each step needs the full picture (degrading 39-70%). Uncoordinated agents amplified each other's errors 17.2×; a single coordinator owning the merge cut that to 4.4×.

## Safe-parallelism checklist — *new synthesis, not verbatim in source*

The source states the stop-rule decision procedure in prose; this checklist makes the "safe to run in parallel" condition explicit and conjunctive (all must hold, not any):

- no real dependency in either direction (not just "no fake edge found" — genuinely no result-need either way)
- non-overlapping authoritative write sets (`graph/writer-and-merge-owner.md`)
- compatible resource ownership (no two jobs contending for the same external resource/lock)
- no unresolved merge-ownership conflict for the artifact(s) involved
- policy allows the operation (Stage 5 `policy/*`, once it exists — until then, defer to `core/do-not-regress.md`'s human-gate-adjacent caution for anything irreversible)

If any condition fails, sequentialize — don't parallelize "to be fast" and hope the merge sorts it out.
