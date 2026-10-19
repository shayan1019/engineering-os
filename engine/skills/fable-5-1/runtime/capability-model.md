# Capability Model

*New synthesis (Stage 4) — not a distillation of `fable5-thinking`/`graph-engineering`. Formalizes the research document's Astra-derived capability concepts into a provider-neutral flag set, per `skill.contract.yaml`'s `capability_model.flags`.*

> **Authority boundary**: this file (and the rest of `runtime/*.md`) is execution optimization only. It does not redefine what Graph Engineering decides is schedulable (`graph/*.md`) or what Fable methodology requires (`core/*.md`). Precedence, per `skill.contract.yaml`: `fable_methodology > graph_scheduling_preference > runtime_optimization`.

## The rule

**Core behavior never branches on `provider == X`.** It branches on capability flags. A provider-mapping file (`runtime/claude-adapter.md`, `runtime/astra-adapter.md`) may describe which flags a given provider tends to support — but `core/*.md` and `graph/*.md` never reference a provider name, and this file's flags are the only vocabulary the scheduler/steering/state files consume.

## The seven flags

| Flag | What it means |
|---|---|
| `structured_output` | The runtime can reliably produce output constrained to a schema/contract, not just free text. |
| `async_tools` | A tool call can be in flight without blocking all other work — other ready, independent nodes can proceed while one is pending. |
| `mid_turn_steering` | New user instructions can be incorporated while execution is actively in progress, not only at a turn boundary. |
| `subagents` | Work can be delegated to a separate agent context (native subagent/Task mechanism), not just handled inline. |
| `persistent_state` | Graph/task state can survive across turns or runtime operations without being re-derived from scratch each time. |
| `history_constraints` | The runtime imposes specific requirements on how conversation/history is treated (e.g. append-only) that execution must respect. |
| `parallel_tool_support` | Independent tool calls can be issued together in one batch rather than strictly one-at-a-time. |

## Evidence tiers — how a flag's value gets determined

Per the approved plan: "Do not hardcode that any provider always supports a feature unless the repository already has authoritative runtime evidence for it. Do not infer capability solely from provider/model name." Every capability assessment uses one of four tiers, weakest-to-strongest is not the point — *honesty about which tier applies* is:

- **known** — documented and guaranteed by the runtime's own official specification, checked against a source in `.agents/registry/COMPATIBILITY.json` or equivalent authoritative documentation.
- **runtime-detected** — directly observed in the current session (e.g. a subagent tool was actually invoked and returned; parallel tool calls actually appeared in one turn). Session-level evidence, not a repository claim — it doesn't outlive the session that observed it.
- **configured** — explicitly turned on/off by workspace or user configuration, independent of what the underlying model could theoretically do.
- **unknown** — no evidence in any of the above tiers. **Default to the conservative fallback** (`runtime/fallback-behavior.md`) whenever a flag is unknown; never assume `true` to get the "better" path.

`.agents/registry/COMPATIBILITY.json`'s `runtime_matrix` documents skill-discovery-level compatibility per runtime (not these seven flags) and is explicitly `"docs only"` / `"NOT RUNTIME-VERIFIED"` for every runtime except `opencode`. No file in this repository currently has authoritative, capability-flag-level evidence for any provider — see `runtime/claude-adapter.md` and `runtime/astra-adapter.md` for what little session-level/documentary evidence does exist, tier-labeled.

## Consumers

`runtime/scheduler-behavior.md` (`parallel_tool_support`, `async_tools`), `runtime/steering-behavior.md` (`mid_turn_steering`), `runtime/state-behavior.md` (`persistent_state`, `history_constraints`), and `graph/*.md`'s subagent/structured-output touchpoints (`subagents`, `structured_output`) all read flags from this file's vocabulary — none of them define their own.
