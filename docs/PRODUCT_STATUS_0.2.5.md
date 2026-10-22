# Product state - 0.2.5-alpha (2026-10-22)

`eos 0.2.5-alpha` is an unsigned alpha engineering control plane for OpenCode
V2 on Windows. Release commit `dddc03bd8b28192404fce1671b140f66d14042fc`;
release workflow run `39852134381`.

## Readiness

Four claims, kept separate:

| Status | This build | Why |
| --- | --- | --- |
| `CORE_READY` | **YES** | engine, security and regression gates PASS on the exact commit, with a 109-file baseline and a clean tree before and after; forged gate and tampered package are refused |
| `HOST_READY` | **YES on Windows 11 + OpenCode 2.0.32** | live permission harness 11/11: Plan denied writes, Build/implementer allowed a controlled write, reviewer denied writes/shell/delegation, external directory denied, independent gate verified |
| `DISTRIBUTION_READY` | **WINDOWS: YES. macOS: PARTIAL** | NSIS installer built in CI, silent install/run/uninstall PASS, sealed and published; local SHA-256 + install + doctor + rollback PASS. macOS arm64 DMG built; Intel x64 had no runner; unsigned |
| `PRODUCTION_READY` | **NO** | alpha, unsigned; no Authenticode, Apple Developer ID or notarization; no live Ruflo execution |

One does not imply another. A passing core gate does not make a package; a
Windows package does not make a macOS package; and neither makes a signed
production release.

## Defects fixed in this build

The first six all had the same shape: a check that was true on paper but false
in the tree the user was actually working in.

### 1. Activation did not run at all on OpenCode 2

The deployed plugin exported the V1 `async (input) => ({ "chat.params": ... })`
shape. OpenCode 2 loads a default export with `id` and `setup` (or `effect`), so
the plugin was rejected. The V2 hooks capture the task once at prompt admission
and inject context just before the model request, cached by task text and
bounded at 32 sessions. `server()` remains for V1 hosts. Plugin test: 19/19.

### 2. Routing read the engine's registry instead of the workspace's

An installed engine routed a 210-skill workspace against its own seven names.
A workspace with `.agents/registry` now wins; the engine copy is the fallback.
Turkish stems (`testi`, `testler`) are matched.

### 3. "nasil" was read as "sil" (delete)

Risk matching now requires a word start (`(?<!\w)sil`), so `nasil` is a question
while `silme` / `silmek` still trigger approval. `silikon` is an accepted
conservative miss: over-classification asks for approval, never skips it.

### 4. The same library was indexed three times

`.claude/skills` is a junction onto `.agents/skills`, and in the workspace layout
the engine root is `.agents`. Sources and files are deduplicated by real path:
212 entries, 210 unique names, 2 real collisions. `fable-5-1` is
`LOCAL_VERIFIED` again.

### 5. The "verified" badge was a filename

`.eos-verified` must now be JSON with `sha256` equal to the `SKILL.md` hash and
a non-empty `checks` list. Only `eos skills verify` writes it. A tamper test
covers edited bytes, an empty check list, a wrong name and an unparseable file.

### 6. Installing a skill dropped the skill's own documents

The installer skipped any directory named `evidence` or `backups`, including a
skill's own `skills/<name>/evidence/`. The rule is now root-relative:
`<engine>/evidence` never ships, `skills/<name>/evidence/` does. Clean install
went from 99 to 102 files and doctor from 3 warnings to 0.

### 7. The release gate trusted a file, not the run

The old workflow could publish from a hand-written PASS record. The new
`release_gate.py` executes the fixed `engine`, `security` and `regression`
suites against the exact commit, embeds each suite artifact and its command
exit codes/output hashes, records the `GATES.json` hash and a 109-file tracked
baseline, and requires the tree to be clean before and after. `verify` rejects a
forged verdict, commit, suite, command, hash, baseline, run binding, or a
manifest that points at another gate. CI proves both a forged gate commit and a
tampered installer are refused. 16 gate + 6 seal/binding selftest cases pass.

### 8. Packaging and publication were not bound to the gate

`seal` hashes the exact installer, portable ZIP, CLI, checksums and manifest;
`publish` re-verifies the gate, commit, workflow run, manifest and every file
before upload. A tampered package fails before `gh release upload`. The first
CI run exposed two real verifier defects (repository looked up in the wrong
object; case-sensitive manifest skip), both fixed and covered by selftest.

### 9. Orchestrators could answer a probe but not execute

`eos orchestrators discover` measured a runtime, but `test` only exercised the
contract table. The self-test now runs the real bounded fixture: three worker
PIDs, depth 2, and mechanical refusal of max_agents, max_depth, max_steps,
timeout, cancellation and workspace escape. Execution requires
`execution.protocol = eos-orchestrator/1`; a version probe is never counted as a
delegated task. Ruflo, which has no such adapter, reports `ADAPTER_ONLY`.

### 10. Agent permissions were prose, not host outcomes

The live harness creates throw-away agents and runs one bounded `opencode run`
per case. It judges the filesystem and the host transcript, not the model's
claim. The final run is 11/11: Plan write denied (`Cannot use write to modify
files outside the Plan directory`), Build and implementer writes allowed,
reviewer write/shell/delegation denied, external directory denied, read-only
edit impossible, and an independent `eos gate` artifact verified. The
implementer no longer has `shell: *`; it has a narrow command allowlist, and
`git add/commit/push` and `rm` stay denied. Chief-architect also denies
`websearch`. `runtime_permissions check-contracts` cross-checks the host-facing
dispatch expectations against the canonical specs so a stale proof fails in CI.

## New surfaces

- `eos agents list | explain <name> [--prompt] | test | permissions`
- `eos orchestrators discover | doctor | test [--self-test] | run --provider <id> --plan <file>`
- `release_gate.py run | seal | verify | selftest` (CI release authority)
- `orchestrator_runtime.py` (real nested workers with bounds and evidence)
- `runtime_permissions.py` (static contracts + live OpenCode permission harness)

## Verification of this build

| Check | Result |
| --- | --- |
| CI release gate (commit `dddc03b`, run `39852134381`) | PASS: engine + security + regression, 109-file baseline, clean before/after |
| Forged gate commit in CI | rejected |
| Tampered installer in CI | rejected |
| `release_gate.py selftest` | 16 gate + 6 seal/binding cases |
| `orchestrator_runtime.py selftest` | 9 real process-tree cases (3 PIDs, depth 2) |
| `orchestrators test --self-test` | 6/6 contract + delegation fixture |
| `runtime_permissions.py check-contracts` | 9 agents, 0 problems |
| live permission harness | 11/11, 0 blocked |
| `route_tests.py` (workspace and installed engine) | 20/20 each |
| plugin test | 19 assertions |
| skill index | 212 entries, 210 unique, 2 collisions, doctor 0/0 |
| `validate_workspace.py` | pass, 0 errors, 0 warnings |
| `validate_skills.py` | 211 records, 210 unique, 0 errors |
| NSIS silent install/run/uninstall (CI) | PASS |
| local NSIS install + `doctor --product` | 3294 files verified, 0 problems, 0 blocking |
| local NSIS uninstaller | exit 0; launcher, version dir, install dir and PATH entry removed; `~/.eos` config/evidence retained; state restored and `doctor --product` PASS again |
| Windows assets | setup `3F30E1AA...639290`, portable `59042F16...40E811`, CLI `376D00D5...EDBF1`; manifest binds gate `B8009142...69FC0` and commit `dddc03b` |
| rollback | 0.2.4-alpha -> 0.2.5-alpha, both `eos version` PASS |
| secret scan | 103 files, 0 insecure, 0 forbidden |

## Rollback

The previous version remains installed and can be made active, then reversed:

```
eos rollback     # 0.2.5-alpha -> 0.2.4-alpha
eos rollback     # 0.2.4-alpha -> 0.2.5-alpha
```

Observed in this session on the installed NSIS product: both directions
returned exit 0 and the expected `eos version`.

## Not in this release

- Signed binaries, Authenticode, Apple Developer ID or notarization (STABLE gate).
- A macOS Intel 0.2.5 DMG: the `macos-13` job stayed queued with no runner.
- A live third-party orchestrator run. Ruflo is `ADAPTER_ONLY` until an
  `eos-orchestrator/1` adapter exists; EOS does not install or upgrade it.
- `PRODUCTION_READY`. This is an alpha, unsigned development release.
