# EngineeringOS 0.2.4-alpha — release notes

**Status: ALPHA. Unsigned build. Not a stable release.**

This release answers the two complaints that came out of real use:

1. **Build mode kept asking for permission.** Every EOS agent contract carried
   `shell: * -> ask`, so every ordinary command (`cat`, `ls`, `npm test`,
   `cargo test`) fell through to a human prompt. The read-only roles now refuse
   an unmatched command outright, and the single writer may run project
   commands without a prompt. The destructive operations stay denied.
2. **Chief Architect had to be selected by hand.** A host plugin now activates
   EOS automatically on the host's own session lifecycle event: the project,
   the rules, the relevant skills, the smallest competent specialist set and
   the gate are injected into the session. The user keeps using the host's own
   Plan or Build mode.

---

## What is in this release

| Piece | What it is |
| --- | --- |
| `eos` | The CLI. Thin front-end in Rust; every engineering decision stays in the stdlib-only Python engine and is forwarded verbatim. |
| `engine/` | The control plane: router, quality gate, skill index, agent installer, activation plugin installer, promotion authority, secret scanner, snapshot tool. 101 files. |
| `runtime/python/` | A bundled CPython 3.11.17 (python-build-standalone, `install_only`). No system Python is required on the target machine. |
| `engine/host-plugins/opencode/` | The activation plugin and its test. The plugin is additive: it adds a context block, never a tool and never a permission. |

## Artifacts

| File | Size | SHA-256 |
| --- | --- | --- |
| `EngineeringOS-Setup-0.2.4-alpha.exe` | 28.8 MB | `4d3465a75168b512be68cc3f64e59b23b9879f87e2833b0cfde2b90bcaffc91c` |
| `EngineeringOS-0.2.4-alpha-portable.zip` | 50.0 MB | `52a3dec2aa45855ba5ff76dfbd6ef3c52629b866bae2c03b569ab9811e4139ce` |
| `eos-0.2.4-alpha.exe` | 486 KB | `7e6eb66f6d2822ee1cc49682edb22919022153cd7a285d21c1d6f78375b8ce5f` |

`SHA256SUMS.txt` ships with the release; verify with `sha256sum -c SHA256SUMS.txt`.

## Install

```
EngineeringOS-Setup-0.2.4-alpha.exe
```

Per user, no administrator rights. Program files go to
`%USERPROFILE%\.eos\versions\0.2.4-alpha`, the launcher to
`%USERPROFILE%\.eos\bin\eos.exe`, and the user's PATH entry is added.
Evidence, baselines and project identity stay in the user data directory and
survive an uninstall.

## Verify the install

```
eos version
eos doctor --product     # now also checks the agent contracts and the activation plugin
eos activate --task "the task"   # the context block a host session receives
eos open .                # integrate this project and start a host there
```

A machine without any Python on `PATH` is supported and tested: the bundled
interpreter is used, and the gate passes with Python removed from `PATH`.

## The permission contract (the change that removes the prompts)

| Role | edit | shell fallback | behaviour |
| --- | --- | --- | --- |
| chief-architect | deny | deny | coordinates; matches no narrower rule -> refused, never a prompt |
| architect | deny | deny | read-only investigation |
| implementer | **allow** | **allow** | the single writer; `git add/commit/push` and `rm` stay denied |
| reviewer | deny | deny | read-only and adversarial |
| verifier | deny | deny | runs the gate, the promotion check and the snapshot check |
| security-auditor | deny | deny | secret, dependency and release security |
| test-engineer | deny | deny | runs the tests; it does not write them |
| performance-engineer | deny | deny | profiling |
| evidence-analyst | deny | deny | provenance |

The rule that is matched last wins, so the specific allows (`python *`,
`pytest *`, `git status*`, `rg *`, `cat *`, ...) still apply on top of the
fallback.

What this contract is **not**: a security boundary. The pattern list is a
guardrail. The boundary is the host's own sandbox, the workspace boundary
(`external_directory: deny`) and the EOS release and promotion authority, which
an agent cannot grant itself. The `agent_dispatch_test.py` check verifies the
*runtime's* view of these rules, not the file on disk.

**One operational note:** a running OpenCode service keeps the agent
definitions it loaded at start-up. After EOS updates the contracts, the host
has to be restarted (or its configuration reloaded) before the new rules are in
force. `eos doctor --product` reports what the host currently accepts.

## Automatic activation (no Chief Architect dependency)

`eos open <project>` installs `.opencode/plugin/eos-activation.js`. The plugin
hooks the host's `chat.params` event and asks the EOS engine for the context
of the current task. The block is bounded (2.4 kB) and names skills; it never
loads a skill body and never adds a tool or a permission.

```
project: bighero (matched by alias) | risk: high | gate policy: approval_required
rules in effect: AGENTS.md, CLAUDE.md, GEMINI.md
relevant skills: backtest, alpaca-broker-market-data, fable-5-1, mql-developer
orchestrator: chief-architect
specialists to use: architect, implementer, reviewer, verifier, security-auditor
not needed for this task: test-engineer, performance-engineer, evidence-analyst, explore
gate for this project: eos gate run --project bighero --suite thesis_exit
permissions: only the implementer may write; the others are read-only
```

The plugin is reversible: `plugin_install.py uninstall` removes it and restores
the previous file, and it refuses to overwrite a plugin it did not write.

## Host support — what is claimed and what is not

| Host | Agents | Subagents | Permission enforcement | Skills | Hooks | MCP | Rules |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenCode | yes | yes | yes | yes | yes | yes | yes |
| Claude Code | yes | yes | **unverified** | yes | yes | yes | yes |
| Cursor | no | no | no | no | no | yes | yes |
| BLACKBOX AI | no | no | no | no | no | no | no |

Every claim is per host and is either VERIFIED on the machine that produced
this release or explicitly marked as not verified.

## Evidence from this release

| Check | Result |
| --- | --- |
| `eos doctor --product` | PRODUCT READY, 0 blocking items, 9 checks |
| Engine suite (`eos/engine`: skill index + 14 routing scenarios + the activation plugin test) | PASS, 15.6 s |
| Secret scan over the installed engine | PASS, 0 findings |
| Agent contracts against the live runtime | 9/9, no `ask` on any shell fallback |
| Activation plugin test (Node) | 8/8: injection, preservation, no tool, no permission, missing engine, empty turn |
| Router regression (workspace layout and engine layout) | 14/14 |
| Windows installer on a clean machine | exit code 0, launcher + version + PATH published |
| `eos` with no system Python on `PATH` | PASS (bundled 3.11.17) |
| Unicode + space + second-drive project paths | PASS |
| Project relocation across drives | PASS (project UUID unchanged) |
| `vpniran/unit` gate suite | **BLOCKED** (exit 2) — 35 real test files exist, but no interpreter can run them. A missing test run is never reported as PASS. |
| `zipforge` gate suite | **BLOCKED** (exit 2) — `node_modules` is incomplete, no `tsc`/`vite` binary. |

## Known limitations (ALPHA)

- **Unsigned.** Windows Authenticode and Apple Developer ID signing are STABLE
  work. Expect SmartScreen warnings on Windows and Gatekeeper warnings on
  macOS.
- **macOS is BETA.** The DMG is produced by the release workflow on an Apple
  silicon runner; the Intel runner is often queued for a long time.
- **The host must be restarted** after EOS updates the agent contracts.
- **Free model providers are rate limited.** Delegated runs can fail with
  "Rate limit exceeded"; the chain then retries with another free model.
- **The BigHero full suite is not green** (4138 passed / 17 failed, measured
  outside the gate). The gate runs a real, isolated slice of it.
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
(python-build-standalone `20261013`, CPython `3.11.17`).
