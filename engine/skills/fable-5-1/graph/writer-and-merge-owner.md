# Writer and Merge Owner

*Native Fable 5.1 restatement of `graph-engineering/references/task-graphs.md` — checklist items B2.10 (guardrail #2), B2.5 step 3. Wording reorganized; behavior unchanged.*

> **Authority boundary**: see `graph/task-graph.md`. Ownership assignment is a graph-scheduling concern; it does not override `core/do-not-regress.md` or `core/completion-model.md`.

## One writer per artifact

No two jobs mutate the same artifact concurrently. One writer per file, always — this is a hard guardrail, not a preference. (B2.10)

## Merge owner is a distinct, assigned role

Never let findings merge without one owner of the merge. (B2.5 step 3) That owner is a specific, explicitly assigned role for the merge step — not whichever worker happens to finish last, and not implied by "someone has to do it."

This is `skill.contract.yaml`'s `authority_model.invariants` made concrete at the graph level:
- `writer_is_not_verifier` — the one who wrote a change does not also declare it correct (see `graph/verification-diamond.md`).
- `worker_is_not_merge_owner_unless_assigned` — a worker producing one branch of parallel output does not become the merge owner by default; that assignment is explicit, made when the graph is planned (`graph/task-graph.md`'s "routing lives in written steps"), not improvised at merge time.

## Practical shape

- Independent analysis/research can run in parallel freely (subject to `graph/parallelism.md`'s checklist) — read-only work doesn't need a single owner.
- The moment output becomes an authoritative write to a shared artifact, exactly one writer holds it at a time.
- Verified branch outputs converge through exactly one merge owner, who is responsible for resolving any conflict between them — not by re-running the original workers, but by making the integration decision.
