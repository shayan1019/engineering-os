# EngineeringOS 0.2.5-alpha — release notes

**Status: ALPHA. Unsigned build, source install only. Not a stable release.**

0.2.5 fixes six defects that shared one shape: a check that was true on paper
but false in the tree the user was actually working in. It also adds the two
surfaces that were missing from the CLI: `agents` and `orchestrators`.

## What changed

| # | Defect | Fix |
| --- | --- | --- |
| 1 | The activation plugin did not load on OpenCode 2 (`Plugin must export a default definition with an id and an effect or setup function`) | Ported to the V2 definition API (`id` + `setup`) with the `prompt` and `context` hooks; `server()` kept for V1 hosts. One router run per user turn. |
| 2 | Routing read the engine's registry, so an installed engine routed a 210-skill workspace over its own seven names | A workspace that carries `.agents/registry` is the scope being routed for and wins; the engine copy is the fallback. |
| 3 | `nasil` (how) contains `sil` (delete), so a harmless question became risk=high + approval_required | A risk keyword must begin a word. Inflected Turkish forms still match. |
| 4 | The same 210 skills were indexed three times (633 entries, 423 collisions), which penalised the whole library | Sources and files are deduplicated by real path: 212 entries, 210 unique, 2 real collisions. |
| 5 | `LOCAL_VERIFIED` was granted on the existence of `.eos-verified`, even empty | The badge is a JSON record: sha256 of `SKILL.md` + a non-empty check list. `eos skills verify <name>` runs the contract check first. |
| 6 | Installing a skill dropped its `evidence/` directory, so the installed skill had broken links | The skip rule is root-relative: EOS's own `evidence/` never ships, a skill's own `evidence/` does. |

## New CLI surfaces

- `eos agents list | explain <name> [--prompt] | test`
- `eos orchestrators discover | doctor | test [--self-test] [--provider <id>]`
- `eos skills verify <name> [--check <label>]`

`orchestrators` keeps a bounded adapter contract. A provider is discovered from
`registry/ORCHESTRATORS.json` or from a skill that ships
`orchestrator.contract.json` — never from its name. Every provider must declare
runtime, delegation, permissions, side effects, cost, bounds and evidence. A
missing runtime is reported `ADAPTER_ONLY` and nothing is executed.

## Verification

```
route_tests.py                     20/20   (workspace, installed engine, clean temp install)
eos-activation.test.mjs            19/19   assertions
eos gate run --suite engine         PASS    4 commands
  artifact: ~/.eos/evidence/gates/eos/gate-engine-20261022T191847Z.json
skill_index.py discover            212 entries, 210 unique, 2 collisions
skill_index.py doctor              ERRORS 0 | WARNINGS 0
validate_workspace.py              pass, 0 errors, 0 warnings
validate_skills.py                 211 records, 210 unique, 0 errors
eos agents test                    16 runtime agents, 9/9 EOS agents verified
eos orchestrators test --self-test 4/4
secret_scan.py (engine)            103 files, 0 insecure, 0 forbidden
```

Install proof (this machine):

```
eos install --source <repo> --version 0.2.5-alpha
  engine 105 files, manifest 447A88E7A1E685BA
eos doctor --product
  PASS installed version 3290 file(s) verified, 0 problem(s)
  PRODUCT READY: 0 blocking item(s)
eos install --local <workspace>\.agents   (second run)
  0 written, 102 already identical
```

## Artifacts

**Not built for 0.2.5.** `makensis` is not installed on the build machine, and
the release workflow that produces the Windows installer and portable ZIP has
not run for this version. There is no `EngineeringOS-Setup-0.2.5.exe`, no
portable ZIP and no `SHA256SUMS.txt` in this release.

The last packaged artifacts remain the 0.2.4-alpha ones recorded in
`RELEASE_NOTES-0.2.4-alpha.md`. Do not substitute them for 0.2.5: they contain
the V1 plugin that fails to load on OpenCode 2.

A source install is available and verified:

```
cargo build --release --manifest-path cli/Cargo.toml
target/release/eos.exe install --source <repo> --version 0.2.5-alpha
```

## Upgrade

```
eos install --source <repo> --version 0.2.5-alpha
eos install --local <workspace>\.agents      # sync the workspace control plane
```

The host must be restarted (or `opencode reload`) before a new plugin or a new
agent contract is in force.

## Rollback

```
eos rollback        # returns the active version to 0.2.4-alpha
```

The pre-0.2.5 plugin is preserved at
`.agents/backups/eos-activation.v1-20261022T214634Z.js` in the workspace where
the upgrade ran.

## Known limitations

- Unsigned, not notarized. Code signing belongs to the STABLE gate.
- No live interactive Plan-mode denial transcript and no live nested
  orchestrator run; both are recorded as PARTIAL in the acceptance matrix.
- macOS artifacts are still at 0.2.4; the arm64 DMG for 0.2.5 does not exist.
- The router's risk classifier is a keyword heuristic. Over-classification asks
  for approval and is safe; under-classification is possible for a term not in
  the vocabulary and is covered by the human gate, not by the classifier.
