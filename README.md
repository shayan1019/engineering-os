# Engineering OS (EOS)

A private engineering control plane for AI-assisted work on real, multi-project
machines. EOS does not replace the agent runtime and does not load skills itself.
It adds the parts that are missing when an agent works on a real codebase:
project resolution, skill selection with a reason, specialists with enforced
boundaries, a mechanical quality gate that an agent cannot weaken, evidence that
survives a reboot, and portability that survives a directory move.

Built on OpenCode V2 (runtime), FABLE 5.1 and GRAPHIFY (methodology and graph).

## Status: ALPHA, Windows, unsigned

Readiness is reported as four separate claims. One passing does not imply the
others.

| Status | This build |
| --- | --- |
| `CORE_READY` | **YES** - engine, security and regression gates PASS on the exact commit; forged gate and tampered package are refused |
| `HOST_READY` | **YES on Windows 11 + OpenCode 2.0.32** - live Plan/Build/reviewer permission harness 11/11 |
| `DISTRIBUTION_READY` | **WINDOWS: YES. macOS: PARTIAL** - NSIS installer built and smoke-tested in CI; macOS arm64 DMG built; Intel x64 had no runner; unsigned |
| `PRODUCTION_READY` | **NO** - alpha, unsigned; no Authenticode/Developer ID/notarization |

Verified on this machine (2026-10-22), by executing the commands, not by
generating configuration files:

| Area | Evidence |
| --- | --- |
| Release gate | CI run `39852134381`: engine/security/regression PASS on commit `dddc03b`, 109-file baseline, clean tree before/after; forged gate and tampered installer rejected |
| Skill index | 212 entries, 210 unique names, 2 real collisions, doctor 0/0 |
| Skill routing | same task resolves differently per project with a decision trace; 20/20 routing cases |
| Agents | 9 specialist definitions; runtime view 9/9; implementer bounded command allowlist, no `shell: *` |
| Delegation | `chief-architect` may invoke exactly 9 named subagents; `subagent "*": deny` blocks everything else |
| Real dispatch | live permission harness 11/11: Plan write denied, Build/implementer write allowed, reviewer write/shell/delegation denied, external directory denied |
| Nested orchestration | `orchestrators test --self-test` 6/6 including 3 real worker PIDs at depth 2; Ruflo stays `ADAPTER_ONLY` without an `eos-orchestrator/1` adapter |
| Quality gate | PASS, FAIL, mixed, optional-failure, timeout and missing-binary suites behave as specified |
| Promotion | refuses a FAIL gate, a project mismatch, a missing artifact, an absent artifact list, a stale gate and a post-run edit |
| Packaging | NSIS silent install/run/uninstall PASS in CI; local SHA-256, install, `doctor --product` and rollback PASS |
| Secret scan | clean on the control plane; a planted GitHub token is detected |

Not in ALPHA: code signing, notarization, a macOS Intel 0.2.5 build, a live
third-party orchestrator execution, desktop UI.

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

## Release evidence

- `docs/ACCEPTANCE_2026-10-22.md` - A-G and P0-P4 rows with the command output behind each done claim.
- `docs/PRODUCT_STATUS_0.2.5.md` - the four readiness statuses and the defects fixed in this build.
- `docs/evidence/release-gate-39852134381.json` - CI gate artifact bound to commit `dddc03b`.
- `docs/evidence/RELEASE-MANIFEST-39852134381.json` - sealed Windows asset hashes.
- `docs/evidence/permission-evidence-20261022T202737Z.json` - 11/11 live host permission transcript.
- `release/RELEASE_NOTES-0.2.5-alpha.md` - published release notes.

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
eos agents test
eos agents permissions
eos orchestrators discover
eos orchestrators test --self-test
```

Or call the engine directly:

```bash
python engine/tools/quality_gate.py run --project eos --suite pass
python engine/tools/release_gate.py selftest
python engine/tools/orchestrator_runtime.py selftest
python engine/tools/runtime_permissions.py check-contracts
python engine/tools/skill_index.py explain --task "..." --project bighero
python engine/tools/agent_dispatch_test.py
python engine/tools/graph_freshness.py report --project bighero
python engine/tools/secret_scan.py engine docs
```

Build the CLI with `cargo build --release --offline` inside `cli/`.

Paths in the committed tree are workspace-relative or `~/`-relative. An absolute
path in the registry is a bug.
