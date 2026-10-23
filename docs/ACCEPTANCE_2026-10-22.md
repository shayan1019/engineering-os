# EOS acceptance matrix - 2026-10-22

Product: `eos 0.2.5-alpha` (unsigned development build)
Host: OpenCode 2.0.32 on Windows 11 Pro 26200
Workspace: `MY PROCECT` (installation) + `engineering-os` (canonical source)
Release commit: `056ec89ea935e780268295bf40cb2dfb101d2af8`
Release workflow: run `39854082689` (workflow_dispatch, version 0.2.5-alpha)
Windows jobs (gate, build, seal, publish): **success**. Only the macOS Intel
job was still queued at the time of this record; it does not gate the Windows
publication.

The `v0.2.5-alpha` tag points to `dddc03b`. Commits `904f981` and `056ec89`
add documentation, evidence and the packaging checksum fix; no engine, CLI or
skill code differs from the tag. The manifest on the release names `056ec89`,
the commit the sealed assets and gate artifact were built and verified against.

A row is **DONE** only when a command or artifact produced in this session shows
it. A row that could not be executed says **PARTIAL** or **BLOCKED** and names
the missing proof. A version probe is not execution; a file on disk is not a
running feature.

## Readiness is four different claims

These are deliberately not interchangeable:

| Status | Meaning | This build |
| --- | --- | --- |
| `CORE_READY` | Engine, security and regression gates pass on the exact commit; no dirty tree; forged or tampered evidence is refused. | **YES** |
| `HOST_READY` | The host adapter really enforces the role boundaries in live sessions. | **YES on Windows 11 + OpenCode 2.0.32**; not measured on macOS/Linux hosts |
| `DISTRIBUTION_READY` | A downloadable package exists, its hashes are sealed, and install/run/uninstall was performed. | **WINDOWS: YES. macOS: PARTIAL** (arm64 DMG built; Intel x64 had no runner; unsigned) |
| `PRODUCTION_READY` | All of the above plus signing, notarization and a stable support policy. | **NO** (alpha; unsigned; no Authenticode/Developer ID/notarization) |

`PRODUCTION_READY: NO` does not contradict the other three. `CORE_READY: YES`
does not imply `DISTRIBUTION_READY`; a Windows package does not imply a macOS
package; and neither implies a signed production release.

## Summary

| # | Requirement | Verdict | Evidence |
| --- | --- | --- | --- |
| A | Host / workspace independence | **DONE** | clean install in `%TEMP%`: 102 files, skill doctor 0/0, routing 20/20 |
| B | Plan read-only, Build writes, controlled implementer, independent gate | **DONE** | live host harness 11/11; host denial text and filesystem outcomes recorded |
| C | Bounded nested orchestration | **DONE for the bounded fixture; ADAPTER_ONLY for Ruflo** | 9/9 real-process cases; `orchestrators test --self-test` 6/6; no live Ruflo runtime |
| D | Negative permissions / security | **DONE** | contracts 9 agents / 0 problems; 8 violations + stale-dispatch check; secret scan 0/0 |
| E | Regression of the EOS suite | **DONE** | CI release gate: engine/security/regression PASS on commit `056ec89`; 109-file baseline |
| F | Clean installation | **DONE** | NSIS silent install/run/uninstall PASS in CI; local install + doctor 3294 files 0 problems |
| F2 | Packaged Windows artifacts | **DONE** | sealed NSIS + portable ZIP + CLI; SHA-256 verified locally; published private prerelease |
| G | Real-project safety | **DONE** | writes only to `%TEMP%`, `~/.eos`, `MY PROCECT`, `engineering-os`; BigHero untouched |
| P0 | Release gate is the authority | **DONE** | forged gate commit, missing suite, changed registry and tampered installer all rejected |
| P1 | Orchestrator executes, not just probes | **DONE** | real PIDs, depth/timeout/cancel/step/agent/workspace bounds; Ruflo ADAPTER_ONLY |
| P2 | Real Plan/Build permission proof | **DONE** | Plan write denied, Build/implementer write allowed, reviewer denied; external/shell/delegation cases |
| P3 | Packages | **WINDOWS DONE; macOS arm64 BUILT; macOS Intel BLOCKED** | Windows run `39854082689` published; arm64 DMG artifact exists; Intel runner never became available |
| P4 | Status split | **DONE** | this table, README and release notes use the four statuses |

## A. Host / workspace independence - DONE

The product was installed into an empty temporary directory and driven from
there, not from the repository it was built in.

```
eos install --local %TEMP%\eos_accept_f942c33b\.agents --source <repo>
  PASS: 102 file(s)
    skill_index.py doctor   -> ERRORS 0 | WARNINGS 0
    route_tests.py          -> 20/20 passed
    route_task.py --task "FastAPI backend icin tenant isolation testi yaz"
      project=None risk=medium agents=[architect, implementer, reviewer,
      verifier, test-engineer]
```

`skills.primary` is empty there on purpose: the clean install ships six skills
and none is a test-authoring skill. The router reports no skill instead of
borrowing a name that is not installed. The same task in the full workspace
resolves `tdd` (case 12).

## B. Mode independence and live permissions - DONE

Measured with `opencode run --standalone --agent <name> --format json` against
throw-away agents in `%TEMP%\eos-perm-harness`. The model was
`opencode/deepseek-v4.1-flash-free`. The harness judges the filesystem and the
host transcript, not the model's claim: a denial that still created the file is
a FAIL.

```
PASS plan_write_denied
PASS plan_delegation_denied
PASS implementer_write_allowed
PASS build_write_allowed
PASS reviewer_write_denied
PASS implementer_outside_denied
PASS host_external_directory_denied
PASS reviewer_shell_write_denied
PASS reviewer_delegation_denied
PASS readonly_edit_denied
PASS independent_gate    artifact verified: ...gate-pass-20261022T202736Z.json

PASS: 11/11 live case(s) pass, 0 blocked
```

Representative host outcomes:

| Attempt | Host outcome |
| --- | --- |
| Plan agent writes a file | `Cannot use write to modify files outside the Plan directory: C:\Users\SHAYAN\.opencode\plan`; file absent |
| Plan agent delegates to the implementer | no delegation surface; file absent |
| Implementer writes inside the workspace | file created with the expected content |
| Build agent writes inside the workspace | file created with the expected content |
| Reviewer tries to write | no write tool in the host catalog; file absent |
| Implementer writes outside the workspace | `external_directory` denial; file absent |
| Probe writer writes outside the workspace | `Permission denied: external_directory`; file absent |
| Reviewer tries a shell write | `Permission denied: shell`; file absent |
| Reviewer tries to delegate to the implementer | no delegation surface; file absent |
| Read-only probe tries to edit | no write/edit tool in the catalog; file absent |
| Independent verification | `eos gate` artifact verified separately from the writer |

Evidence: `%USERPROFILE%\.eos\evidence\permissions\opencode-20261022T202737Z.json`
and a durable copy at `docs/evidence/permission-evidence-20261022T202737Z.json`
(SHA-256 `175D84061EE63FECAD465D13892EB6A2355157BE32AD1AA42C218AB278C2550F`).

Limit, stated plainly: this is a non-interactive `opencode run` session with
explicit agents and permissions, not a recording of a human clicking in the TUI.
It exercises the same host permission layer; it does not claim the TUI itself
was driven.

## C. Nested orchestration - bounded fixture DONE, Ruflo ADAPTER_ONLY

```
eos orchestrators discover
  ruflo  MULTI_AGENT_ORCHESTRATOR  ADAPTER_ONLY  ADAPTER_ONLY  depth 2  timeout 120s

eos orchestrators test --self-test
  PASS probe_ok: PROBE_OK
  PASS adapter_only: ADAPTER_ONLY
  PASS timeout: TIMEOUT
  PASS invalid: INVALID
  PASS probe_is_not_execution: ADAPTER_ONLY
  PASS delegation_fixture: PASS
  PASS orchestrator contract self-test
```

The runtime self-test is the execution proof, not a version probe:

```
PASS real_nested_delegation: verdict=PASS pids=3 steps=3 max_worker_depth=2
PASS max_agents_refused:     verdict=PARTIAL refusals=2 slots=1
PASS max_depth_refused:      verdict=PARTIAL depth_refusals=1 executed_depth=1
PASS max_steps_refused:      verdict=PARTIAL budget_refusals=1 steps=1
PASS timeout_killed:         verdict=TIMEOUT statuses=[TIMEOUT] elapsed=2.4s
PASS cancellation:           verdict=CANCELLED statuses=[CANCELLED] elapsed=0.7s
PASS workspace_escape_refused: verdict=PARTIAL escape_refusals=1 file=False
PASS missing_runtime_adapter_only: verdict=ADAPTER_ONLY spawned=False
PASS missing_execution_contract:   verdict=ADAPTER_ONLY
PASS orchestrator runtime selftest: 9 real process-tree cases
```

`orchestrators run --provider ruflo` returns `ADAPTER_ONLY` because Ruflo does
not implement `execution.protocol = eos-orchestrator/1`; a version probe is not
counted as a delegated task. No Ruflo worker was started or installed.

## D. Negative permissions and security - DONE

| Attempt | Result |
| --- | --- |
| read-only role asks for `shell: *` | denied by contract; live runtime view agrees (`eos agents test` 9/9) |
| read-only role asks to edit | `edit: * -> deny` for eight roles |
| implementer asks for `shell: *` | removed; bounded allowlist only (`python`, `pytest`, `cargo test/build`, `npm test/build`, `git status/diff`; `git add/commit/push` and `rm` denied) |
| chief architect asks for web access | `webfetch` and `websearch` denied |
| installer asked to overwrite a locally modified engine file | `BLOCKED: ... differs from the canonical copy. Re-run with --force` |
| gate asked to pass a failing command | FAIL, covered by the gate fixtures |
| stale dispatch expectation | caught by `runtime_permissions check-contracts` (new) |
| secret scan over the engine | `scanned 103 file(s) | INSECURE 0 | FORBIDDEN 0` |
| tampered installer after sealing | rejected by `release_gate.py verify` in CI (new step) |

## E. Regression - DONE

Local release gate (clean tree, commit `0d7866b` at the time) and CI release
gate (commit `056ec89`) both returned PASS. CI artifact:
`docs/evidence/release-gate-39854082689.json`:

```
version 0.2.5-alpha
commit  056ec89ea935e780268295bf40cb2dfb101d2af8
ref     refs/heads/main
repository shayan1019/engineering-os
workspace_clean_before true
workspace_clean_after  true
baseline entries 109
gates_sha256 E4537F90432F5E79A0872B844991EA6E12A0015791046A580D82F531F72C0EAA
engine      PASS  BB0DC2157E26DE1DB0E795C2B84946D579C55F5AB0CC1DDBDAF37FE34256EFF7
security    PASS  D804F363293170F46A1459FA8B1657DE40E5B1C90C0973866E72E4FF3A0B732B
regression  PASS  39AF1AB9FB6CDEDB0D3755A8A3305035C2B5DFDF437A5BBA360E67A63BB8DCAE
```

Engine suite (4 commands): skill index discover, router 20/20, OpenCode plugin
19 assertions, orchestrator self-test including the real nested fixture.
Security: secret scan. Regression: release-gate selftest, orchestrator runtime
selftest, permission contract selftest, permission contracts.

```
validate_workspace.py   -> pass=True errors=0 warnings=0
validate_skills.py      -> 211 records, 210 unique, 0 errors
skill_index.py doctor   -> ERRORS 0 | WARNINGS 0
route_tests.py          -> 20/20 (workspace and installed engine)
```

## F. Clean installation - DONE

```
NSIS /S /D=%TEMP%\eos-nsis-clean        -> exit 0
  launcher True, uninstaller True, eos.exe True
eos version                            -> eos 0.2.5-alpha
eos doctor --product (MY PROCECT)       -> PRODUCT READY: 0 blocking item(s)
  installed version: 3294 file(s) verified, 0 problem(s)
  agent contracts: PASS
  activation plugin: PASS

eos rollback                            -> rolled back to 0.2.4-alpha
eos version                             -> eos 0.2.4-alpha
eos rollback                            -> rolled back to 0.2.5-alpha
eos version                             -> eos 0.2.5-alpha

uninstall.exe /S (from the same install dir) -> exit 0
  removed: ~/.eos/bin/eos.exe, every version directory, the install dir
  retained: ~/.eos/config.json, state, evidence, baselines (user data)
  PATH: the captured user PATH was restored; the uninstaller leaves an entry
        it does not own untouched rather than deleting a user setting
```

The uninstall test was destructive by design, so the whole `~/.eos` tree and
the user PATH were captured before it and restored afterwards; `doctor
--product` returned PRODUCT READY again after the restore. This is a test
procedure, not something a user has to do.

`install --local` on the workspace was idempotent: 0 written, 102 already
identical. The NSIS silent install/run/uninstall path is also exercised on the
`windows-latest` runner in CI (smoke step) before the artifacts are sealed.

## F2. Packaged Windows artifacts - DONE

Produced by the GitHub Actions release workflow, not by a local `makensis`:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `EngineeringOS-Setup-0.2.5-alpha.exe` (NSIS) | 28,863,879 | `E92699165FD7E8B1598E76C1FF412AA86747E580FE763499631B2F855F522357` |
| `EngineeringOS-0.2.5-alpha-portable.zip` | 50,027,590 | `81CCE072121ECD8B2BA3C2CEA36A32915BD69D8576FEA2F4251B20872EF87622` |
| `eos-0.2.5-alpha.exe` (standalone CLI) | 487,424 | `D607A29EF8CCE97D7FE23F4B4658CF129B0C8CE9188CB17894CCBE3CDD3223F8` |
| `SHA256SUMS.txt` | 293 | `3549B9E2BDE7E251C1970174CC9832139157A1C45D91290CF13C131F3975185D` |
| `RELEASE-MANIFEST.json` | 1,078 | `983662097474B5A92D9D44F58AFD0465188C0ACD8694B2637D3AD5ACCD929027` |

All five are attached to the private prerelease `v0.2.5-alpha`. The manifest
binds them to gate artifact SHA-256
`8EF195BFA75F637799E0EF82F370F4B87EE49DC3434127C0B75E4DAD41924FB1` and commit
`056ec89ea935e780268295bf40cb2dfb101d2af8`. `SHA256SUMS.txt` no longer lists a
hash of itself, and `sha256sum -c SHA256SUMS.txt` passes on the downloaded
directory. The 0.2.4 packages were not renamed or presented as 0.2.5; they
remain separate.

Local verification: every downloaded file's SHA-256 matched the manifest and
the release asset digest; the NSIS install exited 0; `doctor --product` passed
with 3294 files verified and 0 blocking items; `eos agents test` was 9/9; the
orchestrator self-test was 6/6; rollback moved the active version to 0.2.4 and
back to 0.2.5; and the uninstaller exited 0 and removed the launcher, version
directories and install directory.

macOS is reported separately: the `macos-arm64` artifact (59,968,316 bytes) was
built in the same run. It is not attached to the release because `publish-macos`
waits for both matrix legs, and the Intel (`macos-13`) job remained queued with
no runner at the time of this record. No Intel 0.2.5 artifact is claimed, and
the macOS bundles are unsigned. Windows publishing did not wait for either.

## G. Real-project safety - DONE

Writes performed by this session exist only under:

- `%TEMP%\eos_*` (throw-away installs, harness, release downloads),
- `%USERPROFILE%\.eos` (installed product, evidence, versions),
- `MY PROCECT\.agents` and `MY PROCECT\.opencode` (this workspace),
- `engineering-os` (canonical source and docs/evidence).

`6.0_final_merge` (BigHero) has a dirty working tree from pre-existing work. No
command in this session addressed that project, and no file EOS wrote resolves
inside it. EOS did not touch trading, risk, live-execution or BTCUSD repair
paths. No live database was written, and no credential or pasted secret was
requested.

## P0-P2 evidence, in one place

- **P0 Release gate.** `release_gate.py run` executed the three suites in CI
  and recorded commit, ref, repository, run id/attempt, `GATES.json` hash,
  109-entry baseline and a clean tree before/after. CI then mutated a valid
  gate artifact's verdict to PASS on a wrong commit: `BLOCKED: the release is
  not authorized` (step 8). The package job sealed four artifacts, verified
  them, then flipped one byte in the setup executable: `BLOCKED: the release is
  not authorized` / `artifact hash changed: EngineeringOS-Setup-0.2.5-alpha.exe`.
  Publishing runs only after that verify.
- **P1 Orchestration.** `orchestrator_runtime.py selftest` spawned real worker
  processes (`pids=3`, `steps=3`, `max_worker_depth=2`), refused max_agents,
  max_depth and max_steps, killed a timeout tree in about 2.4s, honoured a
  cancellation file in about 0.7s, and refused a workspace escape. `eos
  orchestrators test --self-test` runs those cases as a `delegation_fixture`
  after the six contract cases; a version probe alone cannot pass. Ruflo has no
  `eos-orchestrator/1` adapter, so it is `ADAPTER_ONLY` and spawns nothing.
- **P2 Live permissions.** The 11/11 harness in section B ran the real host
  permission layer; the evidence is the JSON transcript plus filesystem
  outcomes, not an agent's sentence. Plan write, reviewer write/shell/
  delegation and both external-directory attempts were denied by the host.
  Implementer/Build writes inside the workspace succeeded under the same
  harness, and the independent gate artifact was verified separately.

## What this record does not claim

- A live Ruflo (or other third-party) multi-agent execution. Ruflo stays
  `ADAPTER_ONLY` until an `eos-orchestrator/1` adapter exists.
- A macOS Intel 0.2.5 build, or any signed/notarized package.
- A recording of a human driving the interactive TUI. The live proof is a
  non-interactive host session with explicit agents and permission cases.
- `PRODUCTION_READY`. This is an alpha, unsigned development release.
