# Bounded Repair

*Native Fable 5.1 restatement of `graph-engineering/references/task-graphs.md`'s "Guardrails" #1 and #4 — checklist items B2.9, B2.12. Contains one explicit synthesis section (marked below): the retry/repair/replan/terminal-block distinction and the non-progress heuristic are not present verbatim in the source.*

> **Authority boundary**: see `graph/task-graph.md`. This file governs what happens after `graph/verification-diamond.md` reports a failure; it does not redefine what counts as a pass (`core/completion-model.md`) or when human approval is required (`graph/task-graph.md`'s human-gate section).

## Preserved source guardrails

- Every loop gets a maximum number of rounds. (B2.9) The number itself is not fixed globally here — per `graph/task-graph.md`'s "routing lives in written steps," the round limit for a given loop is set when that part of the graph is planned, task/contract-specific, not a universal constant this file prescribes.
- A hard cap on how many agents can spawn. (B2.12) Same principle: the specific cap is set per task/graph, not hard-coded here.

## Retry vs. repair vs. replan vs. terminal block — *new synthesis, not verbatim in source*

The source establishes that loops need a cap; it doesn't distinguish what kind of loop iteration is happening. This distinction is new:

- **Retry** — re-attempt the identical action once (e.g. a transient tool failure). No analysis, no change to the approach.
- **Repair** — `graph/verification-diamond.md` names a specific defect; fix that specific defect, then re-verify. Requires the defect to be nameable, not just "it failed."
- **Replan** — repair attempts on the same node aren't converging (the defect keeps recurring or a new one appears each time); step back to the graph/plan level and reconsider the approach for that piece — not the whole task, just the stuck node.
- **Terminal block** — the round cap (B2.9) is reached, or non-progress (below) is detected. Stop. Report to the human gate (`graph/task-graph.md`), don't keep spending rounds. This is the same "revert, find root cause, retry deliberately" discipline as `core/do-not-regress.md`, applied at the graph-node level.

## Non-progress detection — *new synthesis, not verbatim in source*

A repeated verifier failure with no meaningful artifact/state change between attempts is not "still working on it" — it's non-progress, and should escalate to terminal block rather than consume another retry. Concretely: if consecutive rounds on the same node produce the same failure signature (same defect, same verifier objection) with no change in the underlying artifact or state, that is the signal to stop retrying and replan or terminal-block instead — retrying an unchanged approach against an unchanged failure is not covered by the round cap's intent, it's a faster way to exhaust it for no gain.

No arbitrary universal retry count is prescribed here — see "preserved source guardrails" above.
