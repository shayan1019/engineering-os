# EngineeringOS 0.2.5-alpha - release notes

**Status: ALPHA. Unsigned. Published as a private prerelease.**

`eos 0.2.5-alpha` is a Windows engineering control plane for OpenCode V2. It
does not replace the agent runtime; it adds project resolution, skill routing,
bounded specialists, a mechanical quality gate, and evidence that survives a
reboot and a directory move.

Release commit: `dddc03bd8b28192404fce1671b140f66d14042fc`
Workflow run: `39852134381` (Windows jobs successful; macOS Intel still queued)

## Readiness

Four separate claims. One does not imply another.

| Status | This build |
| --- | --- |
| `CORE_READY` | **YES** - engine, security and regression gates PASS on the exact commit; forged gate and tampered package are refused |
| `HOST_READY` | **YES on Windows 11 + OpenCode 2.0.32** - live Plan/Build/reviewer permission harness 11/11 |
| `DISTRIBUTION_READY` | **WINDOWS: YES. macOS: PARTIAL** - unsigned Windows installer provided; macOS arm64 DMG built, Intel x64 had no runner |
| `PRODUCTION_READY` | **NO** - alpha and unsigned; no Authenticode, Apple Developer ID or notarization |

## What changed

0.2.5 fixes defects that were invisible to a file-existence check. Each fix is
covered by a command that was actually run.

| # | Defect | Fix |
| --- | --- | --- |
| 1 | The activation plugin did not load on OpenCode 2 (V1 export shape) | V2 `id` + `setup` with `prompt`/`context` hooks; `server()` kept for V1. One router run per turn, 32-session cache. |
| 2 | Routing read the engine's registry instead of the workspace's | A workspace with `.agents/registry` wins; the engine copy is the fallback. |
| 3 | `nasil` (how) contains `sil` (delete) and became risk=high | Risk keywords must begin a word. Turkish inflections still match. |
| 4 | The same library was indexed three times | Deduplicate by real path: 212 entries, 210 unique, 2 real collisions. |
| 5 | `LOCAL_VERIFIED` was granted by an empty `.eos-verified` file | JSON record with `sha256` of `SKILL.md` and a non-empty `checks` list; only `eos skills verify` writes it. |
| 6 | Installing a skill dropped its own `evidence/` | Root-relative skip rule: EOS's `evidence/` never ships; a skill's own does. Clean install 99 -> 102 files. |
| 7 | The release gate trusted a hand-written PASS file | `release_gate.py` executes engine/security/regression against the exact commit, embeds suite artifacts and command hashes, records the gate registry hash and a 109-file baseline, and requires a clean tree before and after. |
| 8 | Packaging and publishing were not bound to the gate | `seal` hashes the exact installer/ZIP/CLI; `publish` re-verifies gate, commit, workflow run, manifest and every file. A tampered package is rejected. |
| 9 | Orchestrators could answer a probe but not execute | `orchestrators test --self-test` runs a real bounded fixture: 3 worker PIDs at depth 2, with max_agents/max_depth/max_steps/timeout/cancel/workspace-escape enforcement. A version probe is never execution. |
| 10 | Agent permissions were prose, not host outcomes | Live harness 11/11: Plan writes denied, Build/implementer writes allowed, reviewer write/shell/delegation denied, external directory denied, independent gate verified. The writer lost `shell: *` and keeps a bounded command allowlist. |

## New CLI surfaces

- `eos agents list | explain <name> [--prompt] | test | permissions [--json]`
- `eos orchestrators discover | doctor | test [--self-test] | run --provider <id> --plan <file> [--workspace DIR]`
- `eos skills verify <name> [--check <label>]`
- `eos install | update | rollback | uninstall | repair`

`orchestrators` discovers a provider only from `registry/ORCHESTRATORS.json` or
a skill-shipped `orchestrator.contract.json`, never from its name. Execution
requires `execution.protocol = eos-orchestrator/1`. Ruflo has no such adapter,
so it reports `ADAPTER_ONLY` and nothing is spawned.

## Verification

```
release_gate.py run (CI, commit dddc03b, run 39852134381)
  engine       PASS  (4 commands)  FE167869C6A5387AE74D9136D649C1A1CAC1A1E6CA3451F7DBE00EBE160B2B36
  security     PASS  (1 command)   92B99AA68E0C3BDF7DB78BB731264F63C252FE7C82FAD6F0D485628B68F1EFC8
  regression   PASS  (4 commands)  24D14D7B42B31E5C967C4283B1E41C3B94600D48D7E91B9B6FF0CA06CDEBD805
  baseline 109 files, clean before/after, GATES.json E4537F90432F5E79
  forged PASS artifact for a wrong commit -> BLOCKED
  tampered setup after sealing             -> BLOCKED (artifact hash changed)

release_gate.py selftest            16 gate + 6 seal/binding cases
orchestrator_runtime.py selftest    9 real process-tree cases (pids=3, depth=2)
orchestrators test --self-test      6/6 including delegation_fixture
runtime_permissions check-contracts 9 agents, 0 problems
live permission harness             11/11, 0 blocked
route_tests.py                      20/20 (workspace, installed engine, clean temp)
plugin test                         19/19 assertions
skill_index doctor                  ERRORS 0 | WARNINGS 0
validate_workspace.py               pass, 0 errors, 0 warnings
validate_skills.py                  211 records, 210 unique, 0 errors
secret scan                         103 files, 0 insecure, 0 forbidden
NSIS silent install/run/uninstall   PASS on windows-latest
local NSIS install                  exit 0; doctor --product 3294 files, 0 problems
local rollback                     0.2.4-alpha -> 0.2.5-alpha, both directions PASS
local uninstall                    exit 0; launcher/version/install dir/PATH removed
```

The live permission evidence is at
`docs/evidence/permission-evidence-20261022T202737Z.json`
(SHA-256 `175D84061EE63FECAD465D13892EB6A2355157BE32AD1AA42C218AB278C2550F`).
The CI gate artifact is at `docs/evidence/release-gate-39852134381.json`
(SHA-256 `B800914272EFC0C4F85F08E79D87A9ED83208678FE17492C2A7A28F4F5469FC0`).

## Artifacts

The Windows artifacts are attached to the private prerelease `v0.2.5-alpha`.
Hashes are the ones sealed in `RELEASE-MANIFEST.json`, which binds them to the
gate artifact and commit above:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `EngineeringOS-Setup-0.2.5-alpha.exe` | 28,860,086 | `3F30E1AA986E2FE04D5A2D6D0F553E986B8AF8917FD48FCA2E4135E790639290` |
| `EngineeringOS-0.2.5-alpha-portable.zip` | 50,027,587 | `59042F16258D5EBDEFE920D41FF1CBD65F254D193945A55F491F6AD65740E811` |
| `eos-0.2.5-alpha.exe` | 487,424 | `376D00D58411988612C728F59BEB906A146307CCAECC28372897DA80A04EDBF1` |
| `SHA256SUMS.txt` | 374 | `68ADBC7BE4E25101D8ADBBF6BF6A3AAB2174052AD8641CC7936409AE4E2C8EA3` |
| `RELEASE-MANIFEST.json` | 1,078 | `D46B22B4DC61784DB94E608D658819DFB286AA84222950C176803249BB4AFB03` |

Verify before running:

```powershell
Get-FileHash .\EngineeringOS-Setup-0.2.5-alpha.exe -Algorithm SHA256
Get-Content .\RELEASE-MANIFEST.json
```

The 0.2.4 packages were not renamed or presented as 0.2.5; they remain separate.

macOS is reported separately. The `macos-arm64` artifact (59,966,697 bytes) was
built in the same run, but it is not attached to this release because
`publish-macos` waits for both matrix legs and the Intel (`macos-13`) job stayed
queued with no runner. No Intel 0.2.5 package is claimed, and no macOS package
is signed or notarized. Windows publication did not wait for macOS.

## Install and upgrade

Run the NSIS installer, or use the portable ZIP. Both install the same product
layout through `eos install`, so the CLI recognises it.

For a source install:

```powershell
cargo build --release --manifest-path cli/Cargo.toml
target\release\eos.exe install --source <repo> --version 0.2.5-alpha
```

To sync a workspace control plane after installing:

```powershell
eos install --local <workspace>\.agents
```

Restart the host (or `opencode reload`) before a new plugin or agent contract is
in force.

## Rollback and uninstall

```powershell
eos rollback       # 0.2.5-alpha -> 0.2.4-alpha
eos rollback       # back to 0.2.5-alpha
```

The previous version remains installed; rollback swaps the active version and
does not download anything.

```powershell
eos uninstall                  # removes the launcher, versions and PATH entry
eos uninstall --keep-versions  # keeps downloaded versions for a later repair
```

The NSIS uninstaller calls the same uninstall path. It leaves `~/.eos` user data
(config, evidence, baselines) in place; use `--purge-config` only when that is
intended.

## Known limitations

- Unsigned and not notarized. Authenticode, Apple Developer ID and notarization
  belong to the STABLE gate.
- macOS: arm64 DMG built; Intel x64 not built (no runner); macOS packages are
  unsigned and were not attached to this release.
- No live Ruflo multi-agent run. It stays `ADAPTER_ONLY` until an
  `eos-orchestrator/1` adapter exists; EOS does not install or upgrade it.
- The live permission proof is a non-interactive `opencode run` session with
  explicit agents and permissions, not a recording of the interactive TUI.
  It exercises the same host permission layer.
- The router's risk classifier is a keyword heuristic. Over-classification asks
  for approval and is safe; under-classification is possible for a term outside
  the vocabulary and is covered by the human gate, not by the classifier.
- `PRODUCTION_READY` is **NO**. This is an alpha, unsigned development release.
