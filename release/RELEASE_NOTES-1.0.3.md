# EOS 1.0.3 - Windows x64

Patch release. Unsigned.

## Fixed
- Scope preflight rejects comma-joined and wrong-root scopes before an EDR is created or reused.
- Skill use, denied writes and file hashes are recorded as evidence; `recovery.py` gives bounded next steps.
- OpenCode guard and activation run exactly `active_version`, verified against its manifest. Invalid state: guard DENY, activation UNAVAILABLE.
- install, update and rollback switch engine, launcher, EOS-owned OpenCode plugins and config as one transaction. `eos recover` resolves an interrupted one.

## Notes
- Restart OpenCode after a version change so the new plugin code is loaded.
- A project with its own `.agents` engine keeps that engine until it is upgraded separately (`eos install --local`).
- `eos rollback` run by a 1.0.2 launcher switches forward only partially; finish it with `eos recover` from 1.0.3.
- A comma-joined scope of files that do not exist is still treated as one literal name.
