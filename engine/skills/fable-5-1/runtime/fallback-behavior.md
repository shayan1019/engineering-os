# Fallback Behavior

*Every flag in `skill.contract.yaml`'s `capability_model.flags` must appear here;
`validate_skills.py` checks that coverage. A missing capability is a change in
mechanism, not a change in the result.*

> **The rule, restated**: no capability may be required for correctness unless the task itself explicitly requires that capability and cannot be degraded safely. `CAPABILITY = true` unlocks an optimization; `CAPABILITY = false` (or unknown, per `runtime/capability-model.md`'s evidence tiers) always has a defined, safe path that reaches the same result.

- **`structured_output`** — fallback: an equivalent constrained textual/serialized contract (fixed, predictable plain-text shape — consistent labeled fields) instead of a schema-validated structure. Full guidance: `runtime/state-behavior.md`.
- **`async_tools`** — fallback: blocking/sequential execution — wait for each tool result before proceeding — without changing graph semantics or acceptance criteria. Full guidance: `runtime/scheduler-behavior.md`.
- **`mid_turn_steering`** — fallback: a checkpoint/replan cycle — finish the current node or reach the next natural checkpoint, then run the same scope-impact-check → graph-impact-analysis sequence as a fresh planning pass, rather than interrupting mid-node. Full guidance: `runtime/steering-behavior.md`.
- **`subagents`** — fallback: execute specialist nodes through the current model/runtime sequentially, inline, in the main context. The same graph and worker/merge-owner contracts remain valid. Full guidance: `runtime/scheduler-behavior.md`.
- **`persistent_state`** - fallback: reconstruct the required state (`runtime/state-behavior.md`'s "what EOS owns" list, whichever items apply) from visible context/files, **verify the reconstruction is actually complete**, and continue only if confirmed - otherwise BLOCK and request recovery rather than continuing on a guess. Not a simple re-derive-and-proceed; a checked procedure. Full guidance: `runtime/state-behavior.md`.
- **`history_constraints`** — this one isn't a capability to fall back *from*; when a runtime imposes a constraint (e.g. append-only), it's respected as given regardless of any other flag's value — there is no "fallback" for a constraint, only compliance. Full guidance: `runtime/state-behavior.md`.
- **`parallel_tool_support`** — fallback: issue independent tool calls one at a time, in dependency order, instead of batched. The same graph, the same eventual result, realized without concurrency. Full guidance: `runtime/scheduler-behavior.md`.

## Unknown capability = conservative fallback

Per `runtime/capability-model.md`'s evidence tiers: when a flag's value is genuinely unknown (no `known`/`runtime-detected`/`configured` evidence), treat it as `false` and use the fallback above — never assume `true` to reach for the "better" path speculatively.
