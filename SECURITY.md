# Security policy

## Supported versions

EOS is alpha software. Security fixes are applied to the latest release line;
older alpha builds are not maintained.

| Version | Supported |
| --- | --- |
| 0.2.6-alpha | yes (latest) |
| 0.2.5-alpha and earlier | no |

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could put users at
risk. Use GitHub's private vulnerability reporting on this repository, or
contact the maintainer through the address on the GitHub profile
(`shayan1019`). Include:

- the affected version and platform;
- the smallest reproduction you can provide;
- what an attacker gains (code execution, data disclosure, gate bypass, etc.);
- whether the issue affects the source tree, a package, or a host integration.

You will get an acknowledgement as soon as the report is read. Please allow
time for a fix and a coordinated disclosure before publishing details.

## Security model

EOS is built around a few explicit boundaries:

1. **The gate is not programmable from a prompt.** Suite commands live in
   `engine/registry/GATES.json`. The CLI accepts a project and a suite, never a
   command. A missing suite entry returns `BLOCKED`.
2. **The writer is not the verifier.** The implementer role can edit; the
   verifier role is read-only and can run the gate. The gate artifact is checked
   for project, baseline, file hashes, and freshness before a promotion.
3. **The installer removes only what it recorded.** Uninstall reads the install
   manifest; it cannot delete a user project.
4. **Secrets are scanned, not trusted.** `eos scan` looks for common credential
   patterns and refuses to treat a clean scan as proof that no secret exists.
5. **A capability is not invented.** A host, provider, or tool that is absent is
   reported as `BLOCKED` or `ADAPTER_ONLY`; it is never simulated as a success.

## What is *not* protected in this alpha

- The Windows and macOS packages are **unsigned**. Verify hashes from the
  release page before running them.
- macOS packages are not notarized.
- EOS does not sandbox the host or the model. It constrains what EOS itself will
  execute and record; it cannot prevent a host from running unrelated commands.
- The optional orchestrator adapters are contracts, not a security boundary.
  A provider that is `ADAPTER_ONLY` is not executed.
- The secret scanner is a heuristic. It does not replace a real secret manager
  or a repository history audit.

## Dependency and supply-chain posture

- The Rust CLI has a small dependency set; `cli/Cargo.lock` is committed.
- The Python engine uses the standard library only.
- The bundled Python runtime is staged at build time and is not tracked in Git.
- Third-party licenses are listed in `THIRD_PARTY_LICENSES.md`.
- Release assets are built by GitHub Actions from the tagged commit; the
  workflow re-verifies the gate, the manifest, and every published hash.
