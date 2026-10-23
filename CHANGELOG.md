# Changelog

All notable public changes are recorded here. EOS uses pre-1.0 alpha versions
while the CLI, registry format, and package layout are still stabilizing.

## 0.2.6-alpha — first public release

This is the first source release intended for public distribution. Earlier alpha
builds were private and are not part of this repository's history.

### Added

- Apache License 2.0, `NOTICE`, and a third-party license inventory.
- Public architecture, security, acceptance, and contribution documentation.
- A generic project-registry template and sanitized gate authority.
- Clean-room replacements for the four shipped methodology skills:
  `fable-5-1`, `fable5-thinking`, `graph-engineering`, and `ai-inventor`.
- A provider-agnostic runtime adapter document that replaces vendor-specific
  adapter notes.
- macOS arm64 and Intel packaging paths in the release workflow, in addition to
  the Windows installer and portable archive.

### Changed

- Version bumped from the private 0.2.5 line to `0.2.6-alpha`.
- The project registry ships empty. Add your own projects with
  `eos workspace add` or the `new_project` router helper.
- Example tasks and tests use generic fixture projects instead of private ones.
- The public agent registry now lists all nine shipped specialist roles.
- The CLI package metadata now declares `Apache-2.0` and the public repository.

### Removed

- Vendor system-prompt text and both packaged copies of it.
- Course-derived reference files that could not be redistributed.
- Vendor-specific and internal adapter notes.
- Private project profiles, private gate interpreters, internal acceptance
  evidence, and old release notes.

### Preserved

- The mechanical quality gate and its command authority model.
- Gate artifacts, promotion checks, rollback, and install manifests.
- Deterministic routing, skill selection, and the nine specialist definitions.
- Nested orchestrator contracts, secret scanning, and host activation.
- FABLE/GRAPHIFY-compatible methodology concepts, rewritten as EOS material.
