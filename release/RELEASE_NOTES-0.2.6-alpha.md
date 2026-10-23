# Engineering OS 0.2.6-alpha

First public release. Apache-2.0.

## What it is

EOS is a portable control plane for AI-assisted engineering work. It resolves the
project and skill set, assigns bounded specialists, runs a mechanical quality
gate, and records portable evidence. It is a product layer, not a model and not
a chat client.

## Highlights

- **Clean public source.** New repository history; no private project data and no
  vendor prompt or course-derived text.
- **Apache-2.0.** Root `LICENSE`, `NOTICE`, and `THIRD_PARTY_LICENSES.md`.
- **Nine specialists.** `chief-architect`, `architect`, `implementer`,
  `reviewer`, `verifier`, `test-engineer`, `performance-engineer`,
  `security-auditor`, `evidence-analyst`.
- **Mechanical gate.** PASS/FAIL/BLOCKED from `GATES.json`; a prompt cannot
  supply a command.
- **Portable install.** Windows NSIS installer, Windows portable ZIP, macOS
  arm64/Intel archives (unsigned).
- **Original methodology skills.** `fable-5-1`, `fable5-thinking`,
  `graph-engineering`, and `ai-inventor`, rewritten as EOS material.

## Install

Download from the release page:

- `EngineeringOS-Setup-0.2.6-alpha.exe` (Windows, per-user, unsigned)
- `EngineeringOS-0.2.6-alpha-portable.zip`
- macOS archives when the Intel/arm64 builds complete

Verify `SHA256SUMS.txt` before running. The Windows installer does not require
administrator rights. macOS packages are not notarized in this alpha.

## Verification

The release workflow:

1. runs the engine routing regression, activation plugin, secret scan, and
   release-gate self-test;
2. builds the Rust CLI and runs the Windows installer in silent mode;
3. runs `eos doctor`, then uninstalls and checks that the install manifest was
   respected;
4. builds the macOS packages where a runner is available;
5. re-verifies the gate, manifest, and every published SHA-256 before upload.

See `docs/ACCEPTANCE.md` for the command matrix and the run record.

## Compatibility

- Windows 10/11 x64 (tested on Windows 11).
- macOS arm64 and Intel (preview; unsigned).
- Python 3.11+ for source use.
- Rust 1.75+ to build the CLI.
- Host integration: OpenCode is tested; Claude Code, Codex, Gemini, and Cursor
  use the same CLI contract and are not all live-verified in this release.

## Known limitations

- Alpha software; no production guarantee.
- Packages are unsigned; macOS is not notarized.
- The optional Ruflo provider is `ADAPTER_ONLY`.
- The secret scanner is heuristic.
- No desktop UI.

## License

Apache License 2.0. See `LICENSE`, `NOTICE`, and `THIRD_PARTY_LICENSES.md`.
