# Acceptance record — 0.2.6-alpha

This file records the checks behind the public readiness claims. A claim is only
as good as the command and the artifact next to it. The release workflow re-runs
the core checks on the exact tagged commit; the run URL is recorded below once
the release is published.

## Readiness

| Claim | Verdict | Basis |
| --- | --- | --- |
| `CORE_READY` | PENDING | Release gate on the tagged commit |
| `HOST_READY` | PENDING | Windows 11 + OpenCode live permission harness |
| `DISTRIBUTION_READY` | PENDING | Windows installer/portable and macOS package build |
| `PRODUCTION_READY` | **NO** | Alpha, unsigned, no notarization |

## Core checks

| Check | Command | Expected |
| --- | --- | --- |
| Routing regression | `python engine/router/route_tests.py` | 14 scenarios, registry scope, index dedupe, badge tamper PASS |
| Skill index | `python engine/tools/skill_index.py discover` | index and catalog written, no duplicate names |
| Activation plugin | `node engine/host-plugins/opencode/eos-activation.test.mjs` | all plugin cases PASS |
| Secret scan | `python engine/tools/secret_scan.py engine docs` | no findings; planted token detected in its own test |
| Gate self-test | `python engine/tools/release_gate.py selftest` | PASS and FAIL fixtures classified correctly |
| Orchestrator contracts | `python engine/tools/orchestrator_runtime.py selftest` | ADAPTER_ONLY and bounded execution cases PASS |
| Permission contracts | `python engine/tools/runtime_permissions.py check-contracts` | every role contract PASS |
| Agent dispatch | `python engine/tools/agent_dispatch_test.py` | nine roles resolve and dispatch |
| CLI build | `cargo build --release --manifest-path cli/Cargo.toml` | binary builds |
| CLI tests | `cargo test --manifest-path cli/Cargo.toml` | PASS |

## Distribution checks

| Check | Platform | Expected |
| --- | --- | --- |
| NSIS silent install | Windows | install, `eos doctor`, uninstall PASS |
| Portable archive | Windows | `eos.exe install --source .` PASS |
| Install manifest | Windows | every installed file hashed; uninstall removes only manifest files |
| Rollback | Windows | previous version restored after a forced update |
| Hash verification | All | `SHA256SUMS.txt` matches every asset |
| macOS arm64 archive | macOS | archive builds and contains the CLI, engine, and runtime |
| macOS Intel archive | macOS | archive builds when an Intel runner is available |

## Host checks

| Check | Environment | Expected |
| --- | --- | --- |
| OpenCode activation | Windows 11, OpenCode 2.0.32 | block injected once per user turn; no block on an empty session |
| Plan/Build permission harness | Windows 11, OpenCode 2.0.32 | 11/11 cases; implementer edits, verifier cannot |

The 0.2.6 release changes documentation, registry data, and skill content. It
does not change the host plugin's activation contract or the Rust permission
model. The live host transcript from the 0.2.5 build remains the evidence for
`HOST_READY`; a fresh 0.2.6 transcript is recorded when the release environment
is available.

## Release evidence

- Release run: `TBD`
- Windows assets: `TBD`
- macOS assets: `TBD`
- SHA-256 manifest: `TBD`
- Gate artifact: `TBD`

## Known limitations

- The packages are unsigned and macOS is not notarized.
- macOS Intel depends on GitHub-hosted Intel runner availability.
- The optional Ruflo provider remains `ADAPTER_ONLY`; no live third-party
  orchestrator execution is claimed.
- The secret scanner is a heuristic, not a guarantee.
- No desktop UI is included.
