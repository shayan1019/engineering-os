# Host compatibility matrix

EOS supports four AI coding hosts. Each host has a versioned adapter contract.

| Host | Binary | Status | Capabilities |
| --- | --- | --- | --- |
| OpenCode | `opencode` | VERIFIED (live) | subagents, skills, permissions, MCP |
| Claude Code | `claude` | ADAPTER (installed) | subagents, skills, permissions, MCP |
| Cursor | `cursor` | BLOCKED (not installed) | MCP only |
| BLACKBOX AI | `blackbox` | BLOCKED (not installed) | none |

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
- Activation: `.cursor/rules/` (rules only)
- Agents: not supported (no subagent fleet)
- Skills: not supported
- Adapter: rules + gate commands only
- Status: BLOCKED (Cursor not installed)

## BLACKBOX AI
- Activation: TBD
- Agents: not supported
- Skills: not supported
- Status: BLOCKED (BLACKBOX not installed)

## Detection
Run `python engine/tools/host_detect.py` to check which hosts are available.
