# Provider adapters

EOS runs on more than one host. The methodology is provider-neutral; this file
records only the mechanical differences a host may require.

## Adapter rule

An adapter may translate:

- a skill reference into the host's native skill mechanism;
- a generic subagent request into the host's delegation API;
- a structured-output request into the host's schema or a plain-text contract;
- a file edit into the host's edit tool;
- a shell command into the host's execution tool.

An adapter may **not**:

- change scope, acceptance, risk, or completion rules;
- grant a read-only role write permission;
- skip a gate or weaken a check because the host lacks a feature;
- inject a vendor system prompt into EOS skills.

## Capability declaration

Before using a host, record:

```text
host:
version:
capabilities:
  structured_output: true | false | unknown
  async_tools: ...
  mid_turn_steering: ...
  subagents: ...
  persistent_state: ...
  history_constraints: ...
  parallel_tool_support: ...
evidence: known | runtime-detected | configured | unknown
```

Unknown means `false` for scheduling. The fallback is in
`runtime/fallback-behavior.md`.

## Host notes

- **OpenCode** — tested host. The activation plugin injects a bounded context
  block at session start; the host's permission model enforces the role
  sandboxes.
- **Claude Code** — skill discovery uses the host's skills directory; subagents
  map to the host's delegation mechanism. Not live-verified in this release.
- **Codex** — repo-scoped skills and TOML agent definitions; instruction size
  limits apply. Not live-verified in this release.
- **Gemini CLI** — depth-1 skill discovery; inline delegation when subagents are
  unavailable. Not live-verified in this release.
- **Cursor / other hosts** — use the CLI contract (`eos route`, `eos activate`)
  and the host's own tools. No adapter is assumed.

A host that is absent is `BLOCKED` for tasks that require it, not a reason to
invent output.
