# State Behavior


> **Authority boundary**: EOS owns *what* state must survive; the adapter owns *how* the runtime preserves it. This file states the "what" natively and describes the "how" only as capability-gated behavior — it does not add new methodology.

## Two distinct capabilities — do not conflate them

- **`persistent_state`** — whether graph/task state can survive across turns or runtime operations at all, without being re-derived from scratch each time.
- **`history_constraints`** — runtime-specific requirements on how conversation/history itself is treated (e.g. an append-only guarantee). This is a constraint the runtime imposes, not a capability that helps — respecting it is mandatory when it applies, regardless of whether `persistent_state` is available.

Runtime history mechanics never become EOS methodology. A runtime's append-only requirement, for instance, shapes *how* `fable-5-1` serializes/re-reads its own state — it does not change what `core/scope-contract.md` or `core/do-not-regress.md` require.

## What EOS owns (the "what") — must survive regardless of mechanism

- scope (`core/scope-contract.md`)
- acceptance criteria (`core/scope-contract.md`)
- do-not-regress requirements (`core/do-not-regress.md`)
- decisions made so far
- completed nodes (`graph/task-graph.md`)
- pending nodes
- writer/merge ownership (`graph/writer-and-merge-owner.md`)
- evidence references (`core/execution-discipline.md`)
- unresolved blockers

## `persistent_state = true`

The above may be carried forward by the runtime's own persistence mechanism rather than being re-derived. Use it — don't manually re-summarize state the runtime already retains for you.

## `persistent_state = false` (or unknown)

**Re-deriving from visible context is not, by itself, a safe fallback.** On a long-running task, silently reconstructing state and continuing optimistically can lose scope, acceptance criteria, completed-node history, writer ownership, or evidence without anyone noticing — a wrong guess presented with the same confidence as recovered fact is worse than no state at all. The fallback is a checked procedure, not a guess:

```text
reconstruct required EOS state (the list above, whichever items apply to this task)
    v
verify reconstruction completeness — can each required item actually be confirmed from
    visible context/files, or is it merely assumed?
    v
continue only if the required state is fully recoverable and verified
    v
otherwise: BLOCK / recovery-required — surface exactly which item(s) couldn't be
    reconstructed, rather than proceeding on a partial or assumed picture
```

"Required" is task-dependent — a short DIRECT-level task may have nothing beyond scope to reconstruct; a long GRAPH-level task with completed nodes, writer ownership, and gathered evidence has more to verify before it's safe to continue. Whichever items from the list above actually apply to the task at hand must each be confirmable, not inferred, before proceeding — do not guess missing authoritative state and continue as if it were recovered.

## `history_constraints` present (e.g. append-only)

Respect the constraint as given — don't rewrite or reorder prior context to "clean it up." State updates get appended, not retrofitted into earlier turns.

## Structured output — folds in the plan's point 7, not a separate file

Where `structured_output = true`, use it for the contracts that benefit from a reliable shape: a task-node result, a capability report (this file's own tier-labeled assessments), verification input (`graph/verification-diamond.md`), handoff metadata (`runtime/scheduler-behavior.md`'s subagent section), and pending-work state (this file's "what EOS owns" list).

Where `structured_output = false` (or unknown), the fallback is an equivalent constrained textual/serialized contract — a fixed, predictable plain-text shape for the same information (e.g. consistent labeled fields) rather than free prose. `fable-5-1` does not fail or degrade its methodology merely because native structured-output support is absent; only the serialization mechanism changes.
