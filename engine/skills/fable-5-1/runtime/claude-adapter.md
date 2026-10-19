# Claude Adapter (optional, provenance/reference only)

*New synthesis (Stage 4). Provider-mapping reference only — nothing in `core/*.md`, `graph/*.md`, or the other `runtime/*.md` files reads this file or branches on it. Per `runtime/capability-model.md`'s evidence tiers; every line below is tier-labeled, not asserted as blanket fact.*

`.agents/registry/COMPATIBILITY.json`'s `runtime_matrix` marks Claude's row `"verified": "docs only — native .agents/skills probe bootstrap.ps1 ile"` — i.e. even skill-discovery-level Claude support is not independently runtime-verified in this repository today (see BLOCKER-1, `.claude/skills` adapter). The capability-flag assessments below are more specific than that row, but carry the same caution: most are session-level observations, not repository-documented guarantees.

| Flag | Tier | Basis |
|---|---|---|
| `subagents` | runtime-detected | Directly observed in the session that authored this package: a subagent-spawning tool was invoked multiple times and returned results asynchronously. |
| `parallel_tool_support` | runtime-detected | Directly observed: independent tool calls were issued together in single turns during this same session. |
| `async_tools` | runtime-detected (partial) | This session observed background-task execution with asynchronous completion notifications (spawned agents continued while other work proceeded, results arrived later). Not confirmed for all tool types. |
| `structured_output` | known (general, not repo-specific) | Claude's tool-use mechanism is schema-constrained by design — general product documentation, not something independently verified against this specific repository. |
| `persistent_state` | runtime-detected (partial) | This session's own operating context describes automatic conversation summarization/compaction that preserves work across a long session — a form of persistence, not confirmed to extend across separate sessions. |
| `mid_turn_steering` | unknown | Partial evidence only: this session's tooling documentation references a user-interrupt mechanism (stopping active work), which is adjacent to but not the same as incorporating new instructions into in-flight execution without stopping it. Treat as unconfirmed. |
| `history_constraints` | unknown | Not confirmed for this specific session; treat as unconfirmed rather than assuming an append-only or any other specific guarantee. |

Where a row says "unknown" or "partial," `runtime/fallback-behavior.md`'s conservative path applies — this adapter file does not upgrade those to `true`.
