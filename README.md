# Engineering OS (EOS)

**A portable engineering control plane for AI-assisted software work.**

EOS is not another chat client and it is not a model. It is the layer that sits
between an AI coding host and a real repository: it resolves *which* project and
skill are in scope, assigns a specialist with a bounded role, makes the quality
gate mechanical rather than conversational, and keeps portable evidence for what
actually ran.

[![CI](https://github.com/shayan1019/engineering-os/actions/workflows/ci.yml/badge.svg)](https://github.com/shayan1019/engineering-os/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/shayan1019/engineering-os?include_prereleases)](https://github.com/shayan1019/engineering-os/releases)

## Why EOS

An AI agent working on a real codebase usually lacks five things:

| Missing piece | What EOS adds |
| --- | --- |
| Project context | Deterministic project resolution from aliases, paths, and a relocation-safe registry |
| Skill choice with a reason | A router that selects the minimum sufficient skill set and explains every selection |
| Role boundaries | Nine specialist definitions with explicit sandboxes; only the implementer writes |
| A gate an agent cannot weaken | Commands live in a registry, not in a prompt; PASS/FAIL/BLOCKED comes from an artifact |
| Evidence that survives | Gates, promotions, install manifests, and rollback records under `~/.eos` |

## Status: `0.2.6-alpha`, unsigned

Readiness is reported as four separate claims. One passing does not imply the
others.

| Claim | This build |
| --- | --- |
| `CORE_READY` | **YES** — engine self-tests, routing regression, and the release gate run on the exact commit |
| `HOST_READY` | **YES on Windows 11 + OpenCode 2.0.32** — live permission/AST harness; host code unchanged from the 0.2.5 verification |
| `DISTRIBUTION_READY` | **Windows: YES. macOS: PARTIAL** — unsigned Windows installer and portable ZIP; macOS arm64/Intel packages are built but not notarized |
| `PRODUCTION_READY` | **NO** — alpha software; no code signing, no notarization, no compatibility guarantee |

See [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) for the checks behind each claim
and [CHANGELOG.md](CHANGELOG.md) for the release history.

## Install

### Windows

1. Download `EngineeringOS-Setup-0.2.6-alpha.exe` from
   [Releases](https://github.com/shayan1019/engineering-os/releases).
2. Verify it against the published `SHA256SUMS.txt`.
3. Run the installer. It is per-user, does not require administrator rights, and
   installs the launcher at `%USERPROFILE%\.eos\bin\eos.exe`.
4. Open a new terminal and run `eos doctor`.

The installer is unsigned. Windows SmartScreen may warn; verify the SHA-256
before choosing **More info → Run anyway**.

### Portable Windows archive

```powershell
Expand-Archive EngineeringOS-0.2.6-alpha-windows-x64-portable.zip -DestinationPath .\eos
cd .\eos
.\eos.exe install --source . --add-to-path
eos doctor
```

The portable archive and installer contain the same engine. The installer only
hands the payload to `eos install`; it never copies a version into place itself.

### macOS

The release workflow builds arm64 and Intel archives when an Intel runner is
available. They are unsigned and not notarized, so Gatekeeper will require an
explicit open the first time. Treat the macOS package as a preview until signing
and notarization are added.

### From source

Requirements: Rust 1.75+, Python 3.11+, Node.js 18+ (for the host plugin test),
and NSIS 3 only if you want to build the Windows installer.

```bash
cargo build --release --manifest-path cli/Cargo.toml
python engine/router/route_tests.py
node engine/host-plugins/opencode/eos-activation.test.mjs

# Run the CLI without installing it
export EOS_ENGINE="$PWD/engine"
./cli/target/release/eos doctor
```

On Windows, `eos.exe` is at `cli\target\release\eos.exe`.

## First run

```bash
eos doctor                         # core readiness
eos workspace add . --name my-app  # give the project an identity
eos route --task "fix the flaky login test" --json
eos open . --host opencode         # integrate and start a host
```

`eos route` prints the resolved project, risk, selected skills, specialist set,
gate, and the reason for each selection. It is deterministic and uses only the
standard library.

### The quality gate

```bash
eos gate list
eos gate run --project eos --suite pass
eos gate verify --file .eos/evidence/gates/eos/<artifact>.json
```

An agent can select a project and a suite. It cannot supply a command. A suite
with no verified entry point returns `BLOCKED`, not a fake green. A promotion
requires a gate artifact for the same project and baseline, and every promoted
file must still match the hash the gate saw.

### Specialists and permissions

```bash
eos agents list
eos agents explain reviewer
eos agents test
eos agents permissions --json
```

The nine shipped roles are `chief-architect`, `architect`, `implementer`,
`reviewer`, `verifier`, `test-engineer`, `performance-engineer`,
`security-auditor`, and `evidence-analyst`. The implementer is the only writer;
the verifier can run the gate but cannot edit.

### Skills

```bash
eos skills discover
eos skills explain --task "add a knowledge graph for support tickets"
eos skills verify graph-engineering
```

`fable-5-1` is the canonical EOS working methodology. `fable5-thinking` and
`graph-engineering` are standalone companions; all four shipped skills are
original EOS material under Apache-2.0. See [engine/skills/README.md](engine/skills/README.md).

## Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ host: OpenCode / Claude Code / Codex / Gemini / Cursor       │
└───────────────┬──────────────────────────────────────────────┘
                │ activation block, project context, skill set
┌───────────────▼──────────────────────────────────────────────┐
│ eos CLI (Rust)                                               │
│   install · workspace · hosts · open · rollback · doctor     │
└───────────────┬──────────────────────────────────────────────┘
                │ forwards engineering decisions
┌───────────────▼──────────────────────────────────────────────┐
│ engine (Python, stdlib only)                                 │
│   router · skills · gate · promote · agents · graph · scan  │
│   registry: project profiles, routing map, gate authority    │
└───────────────┬──────────────────────────────────────────────┘
                │ writes
┌───────────────▼──────────────────────────────────────────────┐
│ ~/.eos  versions · evidence · baselines · state · logs        │
└──────────────────────────────────────────────────────────────┘
```

The CLI never decides whether work passed. It locates things and reports the
engine's exit code. The engine never installs itself. The installer never
copies a version into place. Those separations are deliberate.

## Development

```bash
python engine/router/route_tests.py
python engine/tools/release_gate.py selftest
python engine/tools/orchestrator_runtime.py selftest
python engine/tools/runtime_permissions.py check-contracts
python engine/tools/agent_dispatch_test.py
python engine/tools/secret_scan.py engine docs
```

CI runs the routing regression on every push and pull request. The release
workflow re-runs the gate on the exact commit, builds the Windows installer and
portable archive, runs a silent install/doctor/uninstall smoke test, builds the
macOS packages, and verifies the published hashes before upload.

## Security

EOS treats a repository as untrusted input and treats an agent as a potentially
over-eager actor. The gate is not programmable from a prompt, the verifier is
read-only, and the installer only removes files recorded in its own manifest.
See [SECURITY.md](SECURITY.md) for reporting and for the limits of the current
alpha.

## License

Apache License 2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

Product names and trademarks belong to their owners. EOS is an independent
project and is not endorsed by any model or host vendor.
