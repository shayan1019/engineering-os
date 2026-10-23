# Capability model

The engine branches on **capabilities**, not on a provider name. A host either
has a capability or it does not; the task must still reach a correct result.

## Flags

| Flag | Meaning |
| --- | --- |
| `structured_output` | The runtime can reliably produce output constrained to a schema or a fixed contract. |
| `async_tools` | A tool call can be in flight while independent work proceeds. |
| `mid_turn_steering` | New user instructions can be incorporated while execution is in progress. |
| `subagents` | Work can be delegated to a separate agent context. |
| `persistent_state` | Task state can survive across turns or runtime operations. |
| `history_constraints` | The runtime imposes rules on how history is treated (for example, append-only). |
| `parallel_tool_support` | Independent tool calls can be issued in one batch. |

## Evidence tiers

- **known** — documented and guaranteed by the runtime's official specification.
- **runtime-detected** — observed in the current session.
- **configured** — explicitly enabled or disabled by workspace/user configuration.
- **unknown** — no evidence. Treat as `false` and use the fallback.

Do not infer a capability from a model name, a provider reputation, or a
screenshot. Record the tier next to the claim.

## Core rule

A capability can change *how efficiently* work is done. It never changes the
scope, the acceptance check, the writer/verifier separation, or the definition
of completion.

## Consumers

- `runtime/scheduler-behavior.md` — `parallel_tool_support`, `async_tools`,
  `subagents`
- `runtime/steering-behavior.md` — `mid_turn_steering`
- `runtime/state-behavior.md` — `persistent_state`, `history_constraints`
- `graph/*` — subagent and structured-output touchpoints

No file defines a second capability vocabulary. If a new capability is needed,
add it here and to `skill.contract.yaml` together.
