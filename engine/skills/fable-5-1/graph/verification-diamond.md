# Verification Diamond

*Native Fable 5.1 restatement of `graph-engineering/references/task-graphs.md`'s "The diamond pattern" — checklist items B2.3, B2.4. Fulfills the forward reference left open in `core/completion-model.md` (Stage 2).*

> **Authority boundary**: see `graph/task-graph.md`. This file defines the verification *topology* for GRAPH-level work; it does not redefine what counts as evidence in the first place (`core/execution-discipline.md`, `core/completion-model.md`) — it adds an independent-context requirement on top of that evidence discipline.

## The shape

```
        +-- worker 1 --+
plan ---+-- worker 2 --+--> verify --> merge --> result
        +-- worker 3 --+
```

Split the task into independent angles, run workers in parallel (subject to `graph/parallelism.md`), **verify in a separate context**, merge survivors. (B2.3)

## Why the separate context is non-negotiable

A model grading its own work in its own context misses most of its own mistakes — `skill.contract.yaml`'s `writer_is_not_verifier` invariant, made concrete: the verify step must be a genuinely separate context/agent from whichever worker produced the output, not the same context continuing to grade itself.

## Diverse verifiers

Give each verifier a different question — is it correct? is it current? is the source real? — diverse skeptics catch what identical ones cannot. (B2.4) A single verifier re-asking "is this good?" is weaker than several verifiers each checking one specific thing.

## Relationship to `core/completion-model.md`

`core/completion-model.md` (Stage 2) governs what counts as adequate evidence for *this agent's own* claim of done, and explicitly deferred independent-context verification to this file. This file is that deferred piece: for GRAPH-level-or-higher work, a completion claim additionally requires passing through this diamond — evidence-per-claim (Stage 2) is necessary but not sufficient once the task has real independent workers to verify against each other.
