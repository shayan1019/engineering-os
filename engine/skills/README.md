# Skills shipped with EOS

These are the methodology skills the engine depends on. They are installed with
the product so routing and verification work on a machine that has never seen the
development workspace.

| Skill | Why it ships |
| --- | --- |
| `fable-5-1` | Canonical EOS working discipline. The router's evidence and graph cases route to it; removing it would fail the engine's own regression. |
| `fable5-thinking` | Compact companion covering the ten core thinking rules without the full graph/runtime modules. |
| `graph-engineering` | Standalone task-graph and knowledge-graph primer, also used by teaching-shaped requests. |
| `ai-inventor` | Structured invention workflow used by the routing map for concept-generation tasks. |

All four are original Engineering OS material under Apache-2.0. They do not
contain a vendor system prompt or copied course text. Attribution and the
clean-room statement are in
`fable-5-1/references/ATTRIBUTION.md`.

Everything else is discovered per workspace and per host:

```text
<workspace>/.agents/skills      <workspace>/.claude/skills      <workspace>/.opencode/skills
~/.agents/skills                ~/.claude/skills                ~/.config/opencode/skills
```

`eos skills discover` builds the index from those roots. The generated index and
catalog (`engine/registry/SKILL_INDEX.json`, `SKILL_CATALOG.json`) are
per-workspace artifacts and are never committed or copied between installs.
