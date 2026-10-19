# Skills shipped with EOS

These are the methodology skills the engine itself depends on. They travel with
the product so an installed EOS can route and verify work on a machine that has
never seen the development workspace.

| Skill | Why it ships |
| --- | --- |
| `fable-5-1` | The canonical unified methodology. The router's Stage-7 acceptance cases assert that KG/evidence-heavy tasks route to it, so its absence would fail the engine's own test suite. |
| `fable5-thinking` | Source material for `fable-5-1`; preserved byte-for-byte as declared in `AGENTS.md` section 2. |
| `graph-engineering` | Source material for the knowledge-graph half of `fable-5-1`; preserved byte-for-byte. |
| `ai-inventor` | Referenced by the routing map for invention-shaped tasks. |

Everything else is discovered per workspace and per host:

```
<workspace>/.agents/skills      <workspace>/.claude/skills      <workspace>/.opencode/skills
~/.agents/skills                ~/.claude/skills                ~/.config/opencode/skills
```

`eos skills discover` builds the index from those roots. The generated index and
catalog (`engine/registry/SKILL_INDEX.json`, `SKILL_CATALOG.json`) are per-workspace
artifacts and therefore never committed or copied between installs.
