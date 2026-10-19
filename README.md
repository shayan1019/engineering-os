# Engineering OS (EOS)

A private engineering control plane for AI-assisted work on real, multi-project
machines. EOS does not replace the agent runtime and does not load skills itself.
It adds the parts that are missing when an agent works on a real codebase:
project resolution, skill selection with a reason, specialists with enforced
boundaries, a mechanical quality gate that an agent cannot weaken, evidence that
survives a reboot, and portability that survives a directory move.

Built on OpenCode V2 (runtime), FABLE 5.1 and GRAPHIFY (methodology and graph).

## Status: ALPHA, Windows, unsigned

Verified on this machine (2026-10-19), by executing the commands, not by
generating configuration files:

| Area | Evidence |
| --- | --- |
| Skill index | 423 skill entries across 6 discovery roots, 210 unique names, 213 collisions classified |
| Skill routing | same task resolves differently per project (BigHero vs zipforge vs vpniran) with a decision trace |
| Agents | 9 specialist definitions; `opencode debug agents` shows all 9 with the intended mode and edit boundary |
| Delegation | `chief-architect` may invoke exactly 9 named subagents; `subagent "*": deny` blocks everything else |
| Real dispatch | `opencode run --agent evidence-analyst` from a project with no `.opencode` returned `DISPATCH_OK` |
| Global integration | agents installed at `~/.config/opencode/agents`; `agents uninstall` removes them and restores backups |
| Quality gate | PASS, FAIL, mixed, optional-failure, timeout and missing-binary suites behave as specified |
| Promotion | refuses a FAIL gate, a project mismatch, a missing artifact, an absent artifact list, a stale gate and a post-run edit |
| Graph portability | the BigHero graph is detected as `STALE_RELOCATED` (built on a macOS path), 286 sources newer, 74 MQL files uncovered |
| Secret scan | clean on the control plane; a planted GitHub token is detected |

Not in ALPHA: macOS builds, code signing, notarization, installer, desktop UI.

## Layout

```
cli/                Rust command line (no external crates)
engine/tools/       quality gate, promotion, skill index, snapshot, agents, scan
engine/router/      deterministic task router
engine/registry/    project profiles, routing map, gate command authority
engine/agents/      canonical specialist definitions
engine/selftest/    gate fixtures (a passing and a failing case, on purpose)
docs/control-plane.md
```

## Design rules that are enforced, not documented

1. **The gate is not programmable.** Commands live in `engine/registry/GATES.json`.
   The CLI selects a project and a suite; there is no flag that accepts a command.
2. **A promotion needs a gate artifact** for the same project, against the same
   baseline, and every promoted file must still hash to what the gate saw.
3. **The writer is not the verifier.** `implementer` has edit permission;
   `verifier` has none and can only run the gate.
4. **Missing capability is reported, not invented.** No commands means `BLOCKED`,
   no MQL coverage means `PARTIAL`, an unknown source means `INSUFFICIENT_EVIDENCE`.
5. **Nothing personal is published.** No databases, logs, broker data, `.env`,
   credentials, compiled MQL artifacts or skill bodies.

## Local use

The CLI forwards to the engine, so there is one implementation of every rule:

```bash
eos doctor
eos skills explain --task "BTCUSD kaybini arastir" --project bighero
eos gate run --project eos --suite pass
eos gate verify --file .agents/evidence/gates/eos/<artifact>.json
eos promote --project eos --gate <artifact> --artifact <file>
eos graph report --project bighero
eos scan
eos agents verify
```

Or call the engine directly:

```bash
python engine/tools/quality_gate.py run --project eos --suite pass
python engine/tools/skill_index.py explain --task "..." --project bighero
python engine/tools/agent_dispatch_test.py
python engine/tools/graph_freshness.py report --project bighero
python engine/tools/secret_scan.py engine docs
```

Build the CLI with `cargo build --release --offline` inside `cli/`.

Paths in the committed tree are workspace-relative or `~/`-relative. An absolute
path in the registry is a bug.
