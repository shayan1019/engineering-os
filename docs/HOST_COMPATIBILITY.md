# Host compatibility matrix

EOS supports six AI coding hosts. Each host has a versioned adapter contract.

| Host | Binary | Status | Capabilities |
| --- | --- | --- | --- |
| OpenCode | `opencode` | LIVE_VERIFIED | subagents, skills, permissions, MCP, skill_discovery, native_activation |
| Claude Code | `claude` | ADAPTER_READY | subagents, skills, permissions, MCP, skill_discovery, native_activation |
| Cursor | `cursor` | BLOCKED (not installed) | subagents, skills, MCP, skill_discovery, native_activation |
| BLACKBOX AI | `blackbox` | BLOCKED (not installed) | subagents, skills, MCP, skill_discovery, native_activation |
| Codex | `codex` | BLOCKED (not installed) | skills, MCP, skill_discovery |
| Gemini CLI | `gemini` | BLOCKED (not installed) | subagents, skills, MCP, skill_discovery, native_activation |

## Capability notes

- **Cursor** now supports Agent Skills (`.agents/skills/`, `.cursor/skills/`) and custom subagents.
- **BLACKBOX CLI** supports skills, MCP and multi-agent execution (`/multi-agent`).
- **Codex** supports skills and MCP; subagent support varies by version.
- **Gemini CLI** supports `activate_skill` and subagent delegation.

## OpenCode
- Activation: `engine/host-plugins/opencode/eos-activation.js`
- Agents: `.opencode/agents/` (project) or `~/.config/opencode/agents/` (global)
- Skills: `.opencode/skills/`, `.agents/skills/`
- Permissions: enforced by OpenCode's permission model
- Verified: 11/11 live permission tests, nested delegation fixture

## Claude Code
- Activation: `engine/tools/claude_route_hook.py` (UserPromptSubmit hook)
- Agents: `~/.claude/agents/<id>.md`
- Skills: `~/.claude/skills/<name>/SKILL.md`
- Permissions: declared in agent frontmatter, enforcement by Claude runtime
- Adapter: `engine/tools/claude_host.py`
- Status: adapter installed, live verification pending

## Cursor
- Activation: `.cursor/rules/` (rules + gate commands)
- Agents: custom subagents supported
- Skills: `.agents/skills/`, `.cursor/skills/` (auto-discovered)
- Adapter: rules-based (no subagent fleet)
- Status: BLOCKED (Cursor not installed)

## BLACKBOX AI
- Activation: TBD (skill/MCP support confirmed)
- Agents: `/multi-agent` support
- Skills: supported
- Status: BLOCKED (BLACKBOX not installed)

## Codex
- Activation: `.codex/agents/*.toml`
- Agents: TOML definitions
- Skills: repo-scoped
- Status: BLOCKED (Codex not installed)

## Gemini CLI
- Activation: `activate_skill` mechanism
- Agents: subagent support
- Skills: depth-1 discovery
- Status: BLOCKED (Gemini not installed)

## Detection
Run `python engine/tools/host_detect.py` to check which hosts are available.
