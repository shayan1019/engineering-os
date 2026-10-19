# Astra Adapter (optional, provenance/reference only)

*New synthesis (Stage 4). Provider-mapping reference only — nothing in `core/*.md`, `graph/*.md`, or the other `runtime/*.md` files reads this file or branches on it.*

## Evidence status: external reference only, not repository-verified

GPT Astra is **not installed, not configured, and not present anywhere in this repository** — it does not appear in `.agents/registry/COMPATIBILITY.json`'s `runtime_matrix` at all (that file covers opencode, claude, codex, gemini only). Every statement below is sourced from an external research document's description of Astra's public API guidance, pasted into this workspace's conversation history — it is **not** authoritative runtime evidence per `runtime/capability-model.md`'s tier system, and does not qualify as `known`, `runtime-detected`, or `configured`. It is included only because that research document is what originally motivated Astra as a capability-model inspiration for this package.

| Flag | Tier | Basis |
|---|---|---|
| `async_tools` | unknown (external doc only) | The source research document describes asynchronous tool calling as a documented Astra feature. No independent verification exists in this repository. |
| `mid_turn_steering` | unknown (external doc only) | The source research document describes mid-turn steering as a documented Astra feature. No independent verification exists in this repository. |
| `subagents` | unknown (external doc only) | The source research document describes multi-agent orchestration support. No independent verification exists in this repository. |
| `structured_output` | unknown (external doc only) | The source research document describes structured-output support. No independent verification exists in this repository. |
| `persistent_state` | unknown (external doc only) | The source research document describes persisted reasoning/compaction-aware operation. No independent verification exists in this repository. |
| `parallel_tool_support` | unknown (external doc only) | Not independently confirmed. |
| `history_constraints` | unknown (external doc only) | Not independently confirmed. |

**Every row is `unknown` by this repository's own evidence standard.** `runtime/fallback-behavior.md`'s conservative path applies across the board for Astra until (if ever) it is actually installed/configured in this workspace and produces `runtime-detected` or `configured` evidence, per `runtime/capability-model.md`. This file exists to document the inspiration honestly, not to grant Astra a privileged capability profile it hasn't earned in this repository.
