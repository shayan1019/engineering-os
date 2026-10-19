# EngineeringOS 0.2.1-alpha — release notes

**Status: ALPHA. Unsigned build. Not a stable release.**

EngineeringOS (EOS) turns a validated engineering control plane into an
installable product: one command installs the program files, the bundled
runtime and the project adapters on any Windows or macOS machine, and every
decision the tool makes is backed by an artifact on disk.

---

## What is in this release

| Piece | What it is |
| --- | --- |
| `eos` | The CLI. Thin front-end in Rust; every engineering decision stays in the stdlib-only Python engine and is forwarded verbatim. |
| `engine/` | The control plane: router, quality gate, skill index, agent installer, promotion authority, secret scanner, snapshot tool. 96 files. |
| `runtime/python/` | A bundled CPython 3.11.17 (python-build-standalone, `install_only`). No system Python is required on the target machine. |
| Skills | The methodology packages the engine depends on (`fable-5-1`, `fable5-thinking`, `graph-engineering`, `ai-inventor`) travel with the product, so an installed EOS can route and verify work on a machine that has never seen the development workspace. |

## Artifacts

| File | Size | SHA-256 |
| --- | --- | --- |
| `EngineeringOS-Setup-0.2.1-alpha.exe` | 28.8 MB | `1d5439744baf8e41435cb98d3cda6f8434dfcb67a8692756b27855445a04eb94` |
| `EngineeringOS-0.2.1-alpha-portable.zip` | 50.0 MB | `4dfab8f0717c3a03884efb152ba7da000f60cd39cdf1731454a3b1aecebd407b` |
| `eos-0.2.1-alpha.exe` | 480 KB | `926b1d313208a220fd7ff66d18cf3a8282af925eeceacfb6ebffe787b18fa3f1` |

`SHA256SUMS.txt` ships with the release; verify with
`sha256sum -c SHA256SUMS.txt`.

## Install

**Windows (per user, no administrator rights):**

```
EngineeringOS-Setup-0.2.1-alpha.exe
```

The installer hands the payload to `eos install`, which is the only code that
decides what a version directory contains. Program files go to
`%USERPROFILE%\.eos\versions\0.2.1-alpha`, the launcher to
`%USERPROFILE%\.eos\bin\eos.exe`, and the user's PATH entry is added
(`%USERPROFILE%\.eos\bin`). Evidence, baselines and project identity stay in
the user data directory and survive an uninstall.

**Windows (portable, no registry, no PATH):** unpack the ZIP and run
`eos.exe` from the unpacked folder.

**macOS (BETA):** the DMGs are produced by the release workflow. The
application bundle is unsigned and unnotarised in this release, so macOS will
refuse to open it until the user allows it explicitly. Signing and notarisation
are a STABLE gate, not an ALPHA one.

## Verify the install

```
eos doctor            # engine, runtime, baseline, gate commands
eos doctor --product  # install readiness, host adapter, project identity
eos open .            # integrate this project and start a host there
```

A machine without any Python on `PATH` is supported and tested: the bundled
interpreter is used, and `eos doctor` reports `standalone runtime PASS`.

## Host support — what is claimed and what is not

| Host | Agents | Subagents | Permission enforcement | Skills | Hooks | MCP | Rules |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenCode | yes | yes | yes | yes | yes | yes | yes |
| Claude Code | yes | yes | **unverified** | yes | yes | yes | yes |
| Cursor | no | no | no | no | no | yes | yes |
| BLACKBOX AI | no | no | no | no | no | no | no |

Every claim is per host and is either VERIFIED on the machine that produced
this release or explicitly marked as not verified. No host is credited with a
capability it does not have.

## Evidence from this release

| Check | Result |
| --- | --- |
| `eos doctor --product` | 0 blocking items |
| Engine suite (`eos/engine`: skill index rebuild + 14 routing scenarios) | PASS, 9.9 s |
| Secret scan over the installed engine | PASS, 0 findings |
| Router regression (workspace layout and engine layout) | 14/14 |
| Agent dispatch check against the live runtime | 9/9 EOS agents, permission matrix enforced |
| Windows installer on a clean machine | exit code 0, launcher + version + PATH published |
| `eos` with no system Python on `PATH` | PASS (bundled 3.11.17) |
| Unicode + space + second-drive project paths | PASS (identity and routing preserved) |
| Project relocation across drives | PASS (project UUID unchanged) |
| `vpniran/unit` gate suite | **BLOCKED** (exit 2) — 35 real test files exist, but no interpreter can run them. A missing test run is never reported as PASS. |
| `zipforge` gate suite | **BLOCKED** (exit 2) — `node_modules` is incomplete, no `tsc`/`vite` binary. |

## Known limitations (ALPHA)

- **Unsigned.** Windows Authenticode and Apple Developer ID signing are STABLE
  work. Expect SmartScreen warnings on Windows and Gatekeeper warnings on
  macOS.
- **macOS is BETA.** The DMG builds on the release workflow; the bundle is
  unsigned and unnotarised.
- **Windows installers end their own teardown in a session without an
  interactive desktop.** The install itself completes and reports success; the
  wrapper's message loop is terminated explicitly so the process still exits.
- **Free model providers are rate limited.** Delegated runs can fail with
  "Rate limit exceeded"; the chain then retries with another free model.
- **`BigHero`'s full suite is not green** (4138 passed / 17 failed, measured
  outside the gate). The gate runs a real, isolated slice of it so that the
  gate result is a fact about a known test set.
- **The BigHero graph reindex is destructive** and was not run without an
  explicit human decision.
- **The `vpniran` and `zipforge` gate suites are not configured**; they report
  BLOCKED with the reason, never PASS.

## Gates and promotion

`eos gate run --project <p> --suite <s>` exits 0 on PASS, 1 on FAIL, and 2 on
BLOCKED (usage error, missing interpreter, or a suite that is not configured).
A suite without a runnable command produces **no** artifact: a file in the
evidence directory is a claim that something ran.

`eos promote verify` refuses to promote without a matching, freshly hashed
gate artifact. Promotion authority is not something an agent can grant itself.

## Provenance

Every artifact is produced by `installer/build.ps1` from the sources in this
repository, with the bundled interpreter pinned by version and build tag
(python-build-standalone `20261013`, CPython `3.11.17`). The repository's
`.gitattributes` disables end-of-line conversion so the bytes in the release
are the bytes in the commit.

---

## Changes since 0.2.0-alpha

- **The host launcher is resolved correctly on Windows.** npm-style launchers
  ship an extensionless POSIX script next to `name.cmd`; the product picked
  the script and Windows refused to start it (error 193, "not a valid Win32
  application"). The product now looks for an executable extension and runs a
  `.cmd`/`.bat` launcher through the command interpreter. `eos open .`
  now starts OpenCode.
- The CLI never panics when it is run without a console (the installer runs it
  that way): a failed write to an invalid standard output is reported, not
  fatal.
- `eos uninstall` can remove the launcher it is running from (Windows refuses
  to delete a running executable, so the file is renamed first) and leaves
  nothing half-removed behind.
- `eos skills resolve`/`explain` accept the task as a positional argument.
- A gate suite without a runnable command is BLOCKED and writes no artifact.