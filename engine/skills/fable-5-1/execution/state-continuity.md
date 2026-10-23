# State-Continuity Discipline (native EOS methodology)

provenance — not a model or provider dependency. Any compatible AI follows this; runtime
capabilities only decide how efficiently, never whether.*

> **Authority boundary**: EOS owns *what* must survive (`../core/scope-contract.md`,
> `../core/do-not-regress.md`); this file states the continuity conduct. Persistence and
> serialization mechanisms are `../runtime/state-behavior.md`'s decision, driven by the
> `persistent_state` / `structured_output` / `history_constraints` flags
> (`../runtime/capability-model.md`).

## Rules

1. **State travels with the work.** Scope, acceptance criteria, do-not-regress
   requirements, decisions, completed/pending nodes, writer/merge ownership, evidence
   references, and unresolved blockers are never dropped between steps or turns.
2. **Structured intermediate results.** Every node result and pending-work snapshot uses a
   fixed, predictable shape — native schema where `structured_output` holds, an equivalent
   labeled-field textual contract otherwise. Never free prose as the coordination medium.
3. **Checked reconstruction, never guessing.** Where persistence is unavailable,
   reconstruct the required state from visible context, verify each item is confirmed
   (not inferred), continue only if fully recoverable — otherwise BLOCK and surface
   exactly what couldn't be reconstructed.

> Invariant: **runtime capability changes execution efficiency, not EOS methodology.**

## What this file must never do

Claim persisted state or structured-output enforcement, guess missing authoritative state
and continue, or condition any rule on a provider or model name.
