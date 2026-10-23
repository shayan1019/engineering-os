# Verification


## The invariant, restated

**Writer ≠ verifier.** Where independent verification is required (`policy/risk-contract.md`'s `verify_required`/`approval_required` gates, or any GRAPH-level-or-above task per `graph/verification-diamond.md`), the one who wrote a change does not also declare it correct. A verifier must not merely restate the writer's conclusion — "the writer says it works" is not a verification result, it's a quote.

## What verification answers

A verification pass, for a completion claim worth checking formally, answers all of:

1. **Did the requested behavior pass?** — the actual acceptance criteria from `core/scope-contract.md`, checked, not assumed.
2. **Did protected behavior remain valid?** — `core/do-not-regress.md`'s baseline, re-checked after the change, not merely "nothing looked different."
3. **Were only allowed deltas introduced?** — scope discipline (`core/scope-contract.md`'s scope freeze) didn't silently expand or narrow.
4. **Are required policy conditions satisfied?** — `policy/risk-contract.md`'s gate for this task's risk level has actually been met (approval obtained if required, not skipped).
5. **Is required evidence present?** — `evidence/evidence-contract.md`'s fields exist for the claims that needed them, in `verified` state, not `unverified`.

A `DONE` claim (`core/completion-model.md`) needs all five where applicable to the task at hand — not all five apply to every task (a DIRECT-level task with no protected behavior and no policy gate skips 2 and 4 trivially), but none may be silently skipped when they *do* apply.

## How the verifier thinks: falsify-first, unknowns-first

`graph/verification-diamond.md` defines *where* verification happens; this section defines

1. **Falsify-first.** Before accepting a result, actively seek counterexamples, violated
   invariants, missing evidence, and failure conditions — ask "how could this be wrong?"
   before "how do I PASS this?". Insufficient evidence produces `NOT_VERIFIED`, never PASS.
   Use `REFUTED`/`FAIL` only when actual counterevidence or a failed required check exists —
   uncertainty alone is not refutation.
2. **Pre-verification unknowns.** Before verifying, list the load-bearing unknowns (the
   assumptions the result stands on) and assign each the smallest reliable check,
   preferring deterministic checks where available — so verification tests what matters
   instead of probing at random.

## Prefer deterministic verification when it exists

Where a mechanical check is available, use it instead of a judgment call. This workspace already has a working example: `validate_skills.py`'s `check_contract()` (Phase 1) — a deliberately malformed contract was shown to fail deterministically (exit 1, 8 concrete errors), not judged as "probably fine." `route_tests.py`'s 9 regression cases are the same pattern applied to routing. Whenever a claim can be checked this way rather than by reading and judging, do that first — judgment-based verification is the fallback for what can't be made deterministic, not the default.

## Connecting graph, evidence, and completion — three separate questions

- **`graph/verification-diamond.md`** defines *where* verification occurs (a separate context/agent, never the writer's own).
- **This file (`evidence/verification.md`)** defines *what* counts as sufficient verification evidence (the 5-question checklist above, backed by `evidence/evidence-contract.md`'s fields).
- **`core/completion-model.md`** defines *when* enough verification exists to call the task complete (the `DONE` state, distinguished from a model merely saying so).

Keep these responsibilities separate — don't let `graph/verification-diamond.md` grow evidence-shape content, and don't let this file grow graph-topology content.
