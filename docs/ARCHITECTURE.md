# Architecture

EOS separates four concerns that are usually mixed together in an AI coding
setup: **where the work happens**, **who is allowed to do it**, **what command
proves it**, and **what record survives**.

## Components

```text
cli/                    Rust product shell
  install.rs            versioned install, repair, update, rollback
  workspace.rs          project identity and relocation-safe registry
  hosts.rs              host detection and integration
  main.rs               command dispatch; forwards engineering commands

engine/
  router/               project + skill + specialist resolution
  tools/                gate, promotion, skill index, agents, graph, scan
  registry/             project profiles, routing map, gate authority
  agents/               nine specialist definitions
  skills/               four shipped methodology skills
  host-plugins/         OpenCode activation plugin
  selftest/             deliberate PASS and FAIL gate fixtures
```

### The CLI is a locator, not a judge

The Rust binary owns the things that are true about *this machine*: where the
engine is installed, which Python interpreter to use, which host is present,
which directories exist, and what the install manifest contains. It does not
decide whether engineering work passed. Engineering commands are forwarded to
the Python engine and the engine's exit code is preserved.

This is why `eos install` works on a machine with no engine: installation is the
bootstrap operation. The installer hands the payload to `eos install`; it never
copies a version into place, so the installer and the CLI cannot disagree about
what an installation is.

### The engine is the authority

The Python engine is standard-library-only. It owns:

- **Routing** — resolve a task to a project, risk level, skill set, specialist
  set, gate, and evidence requirement. The output includes the reasons.
- **Skills** — discover, index, explain, and verify skill packages; enforce
  frontmatter, depth, duplicate names, broken links, and contract shape.
- **Gate** — execute a suite named in `engine/registry/GATES.json`; classify
  `PASS`, `FAIL`, `BLOCKED`, `PARTIAL`, or `INSUFFICIENT_EVIDENCE`.
- **Promotion** — refuse a gate from another project, another baseline, a stale
  run, a missing artifact, or a file edited after the gate.
- **Agents** — install role definitions into a host, test dispatch, and check
  permission contracts.
- **Graph** — report repository graph freshness and fall back explicitly when a
  graph provider is absent.
- **Scan** — heuristic secret detection with a clear non-guarantee.

### User data is outside the repository

Everything mutable lives under `~/.eos` (overridable with `EOS_HOME`):

```text
~/.eos/
  bin/eos.exe
  config.json
  versions/<version>/
    eos.exe
    engine/**
    runtime/python/**
    install-manifest.json
  evidence/gates/<project>/<artifact>.json
  baselines/
  state/
  logs/
```

Uninstall removes files recorded in the install manifest and nothing else. A
user project is never inside that manifest.

## Trust boundaries

| Boundary | Rule |
| --- | --- |
| Prompt → gate | A prompt cannot supply a command. Only `GATES.json` can. |
| Writer → verifier | The implementer writes; the verifier is read-only and runs the gate. |
| Installer → version | The installer invokes `eos install`; it does not copy files itself. |
| Host → engine | The host asks for context; EOS answers with a bounded activation block. |
| Provider → execution | A provider without an `eos-orchestrator/1` contract stays `ADAPTER_ONLY`. |
| Repository → evidence | Evidence is written under `~/.eos`, not into the user's project by default. |

## Routing model

`engine/router/route_task.py` is deterministic and dependency-free:

1. Read the workspace registry if the scope carries `.agents/registry`.
2. Otherwise use the installed engine registry.
3. Match task text to project aliases, then cwd/discovery, then leave the
   project unresolved and report it.
4. Classify risk from explicit high-risk and knowledge-graph vocabularies.
5. Match project domains to the skill catalog.
6. Apply the routing map to resolve overlapping triggers to one primary skill.
7. Select specialists by risk and task shape.
8. Return the gate, evidence requirement, and a `why` for each decision.

The router never loads a skill body. It selects *which* skill should be loaded;
the host performs progressive disclosure.

## Gate model

A gate artifact contains the project, suite, baseline, commit, run id, command
results, and file hashes. `promote.py` verifies all of them before copying an
artifact. A forged artifact, a stale artifact, a wrong project, or a changed file
is refused. The engine's own self-test includes a deliberately failing suite and
a deliberately passing suite so a gate that always returns green cannot pass the
test suite.

## Packaging model

- **Windows installer** — NSIS stub plus staged `eos.exe`, `engine/`, and a
  relocatable CPython runtime. Per-user; no administrator rights.
- **Portable Windows ZIP** — the same payload, installed with
  `eos.exe install --source .`.
- **macOS** — arm64 and Intel archives built by the release workflow; unsigned
  and not notarized in this alpha.
- **Source** — `cargo build --release` plus the Python engine.

## Evolution rules

- Public CLI flags and registry shapes are contracts; changing them requires a
  changelog entry and a test.
- A new gate suite requires a verified entry point; otherwise it is
  `NOT_CONFIGURED` and the gate returns `BLOCKED`.
- A new specialist requires a definition in `engine/agents/`, a registry entry,
  and a dispatch test.
- A new shipped skill requires a license/provenance review and a contract.
- No generated index, runtime binary, local evidence, or absolute path enters
  the source tree.
