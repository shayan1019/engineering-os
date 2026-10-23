# Adopting EOS in a workspace

EOS can run in two modes:

1. **Installed product** — the engine lives under `~/.eos/versions/<version>` and
   operates on any project selected with `eos workspace add` or `EOS_WORKSPACE`.
2. **Workspace control plane** — the engine is checked into `.agents/` inside a
   repository, so a team can version its routing rules, gate authority, and
   specialist definitions with the code.

The product is the installed mode. The workspace layout below is for teams that
want the control plane in their own repository.

## Workspace layout

```text
<workspace>/
  .agents/
    core/
      FABLE_CORE.md        cross-runtime working principles
      GRAPH_CORE.md        task-graph and conditional knowledge-graph rules
    registry/
      PROJECT_PROFILES.json
      ROUTING_MAP.json
      GATES.json
      AGENT_REGISTRY.json
      COMPATIBILITY.json
    router/
    tools/
    agents/
    skills/
  AGENTS.md                optional host entry point
  CLAUDE.md                optional host entry point
  GEMINI.md                optional host entry point
```

`engine/` is the same tree in the source package. When EOS is installed, the
engine is copied into the version directory; when it is used as a control plane,
the tree is `.agents/`.

## Minimal adoption

1. Install EOS and run `eos doctor`.
2. Add the project:

   ```bash
   eos workspace add . --name my-project
   ```

3. Add project-specific profiles to
   `engine/registry/PROJECT_PROFILES.json` (or the workspace copy). Paths are
   relative to the workspace root; absolute paths are a bug.
4. Add gate suites to `engine/registry/GATES.json`. Only commands that a human
   has verified belong there.
5. Run `eos route --task "..."` and check that the selected project, skills, and
   specialists match your intent.
6. Run `eos gate run --project my-project --suite test` and keep the artifact.

## Registry ownership

| File | Owner | Rule |
| --- | --- | --- |
| `PROJECT_PROFILES.json` | workspace maintainer | project ids, aliases, relative paths, domains |
| `ROUTING_MAP.json` | workspace maintainer | resolve overlapping skill triggers to one primary |
| `GATES.json` | release owner | the only source of executable gate commands |
| `AGENT_REGISTRY.json` | workspace maintainer | role ids and sandboxes; must match `engine/agents/` |
| `COMPATIBILITY.json` | workspace maintainer | host capability claims and their evidence level |

The shipped public tree has an empty project registry and a single `eos`
self-test project. That is intentional: a new user should not inherit somebody
else's project names or machine paths.

## Host integration

The OpenCode activation plugin is in `engine/host-plugins/opencode/`. It asks the
engine for a bounded block on session start and injects it into the system
prompt. Other hosts use the CLI directly:

```bash
eos activate --task "..." --json
eos route --task "..." --json
```

A host that cannot run a hook can still use `eos route` before a task and pass
the result to the model. The engine does not require a specific host.

## Relocation

Everything in the committed tree is relative. When the workspace moves:

```bash
eos workspace relocate <id> /new/path
eos doctor
```

The relocation test in `engine/router/route_tests.py` copies the engine to a new
temporary directory and asserts that routing is identical.
