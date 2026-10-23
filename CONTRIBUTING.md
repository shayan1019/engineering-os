# Contributing to Engineering OS

EOS is an alpha project. Small, focused changes with a test and a clear reason
are much easier to review than large rewrites.

## Before you start

- Read [README.md](README.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- For a behavior change, open an issue that describes the user-visible problem
  first. For a security issue, follow [SECURITY.md](SECURITY.md) instead.
- Keep the engine standard-library-only. A new Python dependency needs a strong
  reason and a license review.
- Do not commit generated indexes (`engine/registry/SKILL_INDEX.json`,
  `SKILL_CATALOG.json`), build output, runtime binaries, `.env` files, or
  evidence from your own machine.

## Development loop

```bash
git clone https://github.com/shayan1019/engineering-os.git
cd engineering-os
python engine/router/route_tests.py
node engine/host-plugins/opencode/eos-activation.test.mjs
python engine/tools/secret_scan.py engine docs
```

For the CLI:

```bash
cargo test --manifest-path cli/Cargo.toml
cargo build --release --manifest-path cli/Cargo.toml
```

For the release gate and the specialist invariants:

```bash
python engine/tools/release_gate.py selftest
python engine/tools/orchestrator_runtime.py selftest
python engine/tools/runtime_permissions.py check-contracts
python engine/tools/agent_dispatch_test.py
```

## Pull requests

1. One logical change per pull request.
2. Add or update a regression test for behavior changes.
3. Run the tests above locally and paste the commands you ran.
4. Update docs when the public contract changes (CLI flags, registry shape,
   package layout, or a security boundary).
5. Do not weaken a gate, skip a test, or add a credential to make CI green.

The release gate is intentionally strict: it checks the exact commit, the
engine suite, the manifest, and the hashes. A pull request that changes an
engine file must keep the self-tests green.

## Code style

- Python: standard library, 4-space indent, no new runtime dependencies.
- Rust: `cargo fmt` and `cargo clippy` where available.
- Markdown: short sections, concrete commands, no claims without a check.
- Paths in committed files must be relative or use `~`; absolute local paths are
  a bug.

## License

By contributing, you agree that your contribution is licensed under the Apache
License 2.0 and that you have the right to submit it. Do not paste code, text,
or assets from a project whose license does not allow redistribution. If you
want to cite an idea, cite it; do not copy the expression.
