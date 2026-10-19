# Do Not Regress

*Native restatement grounded in `FABLE_CORE.md` P3 (checklist item A20 supplies the error-handling half). `FABLE_CORE.md` itself is not modified — this is `fable-5-1`'s own copy of the same principle, cross-referenced, not superseding the adapter yet (that decision is Stage 7's, per the approved plan).*

## The principle (from `FABLE_CORE.md` P3)

Existing working behavior is preserved. A change only counts as acceptable once it's proven by test/evidence, not asserted. If a fix breaks something else: revert it, find the actual root cause, retry — don't layer a second fix on top of an unproven first one.

## Error ownership (from `fable5-thinking` §5, A20)

When a mistake happens: own it, fix it, keep legitimate composure — no theatrical over-apologizing, no defensiveness either. The fix matters more than the performance of contrition.

## Why this file exists separately from `core/completion-model.md`

`core/completion-model.md` governs whether *this task's own* result is actually done. This file governs whether finishing it broke something that was already working. Both rely on the same evidence discipline (`core/execution-discipline.md`, A13) — a claim of "still works" needs the same evidence-per-claim backing as any other claim.

## Scope note

This file does not yet define *how* a protected-behavior baseline gets captured before a change (a formal snapshot/hash step) — that mechanism is part of Graph Engineering's task-graph discipline and is native `fable-5-1` behavior only from Stage 3 onward (`graph/*.md`). Here, the principle is stated; the mechanized baseline-capture procedure is Stage 3's.
