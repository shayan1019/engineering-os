# EOS 1.0.0-rc.1 — Release Audit

Date: 2026-11-21
Base: `eos/rc-completion-0.6.3` @ `7ba82212e63d`
Scope of this release: **OpenCode, WORKSPACE installation**.

This is a release candidate, not a full 1.0. Two release blockers are open and
are not hidden.

## 1. What was verified

| Contract | Evidence |
| --- | --- |
| P6 evidence trust (invalid P5, suite-scoped activity, version-scoped delta, run_id conflict, gate project field) | selftest 22/22, red run 4 failures |
| P5 mechanical benchmark | 18/18, detection 1.0, false block 0 |
| P5-C mechanical runner (hidden oracle, broken/fixed, timeout) | 4/4, model BLOCKED |
| vnext_dev / vnext / regression / deploy | 15/15, 5/5, 4/4, 2/2 |
| EOS edits its own product sources | every source edit in this session was allowed through the guard; no Chief or legacy permission lock |
| Workspace installer | `eos install --local` + `agent_install` deploy gate PASS |
| Rollback / ship integrity | `ship_selftest`, `release_gate_selftest`, remote SHA verification |

## 2. Release blockers

### B1. Global installer writes plugins relative to the current directory

`scope_install.py`:

- `cmd_install` resolves `root` only from `--root`; global mode with no root
  passes `""`;
- `plugin_target("")` returns `.opencode/plugin`, so a global install writes
  into whatever directory the command runs from, not the user's global OpenCode
  configuration.

A global install is therefore **not trustworthy**. Status: **FAIL**. It must not
be published as working until the target resolves to the host's global config
and a fresh session proves it.

### B2. Fresh-session automatic activation is unproven

Because B1 is open, a new OpenCode session in an unregistered folder cannot be
given the global plugin reliably. The `auto-<folder>` discovery mechanism is
unit-tested, but the user journey (open a new folder, no EOS command, activation,
map, EDR, denied write, post-write refresh, resume) is **NOT_TESTED** live.

## 3. Known limits

| Item | Status |
| --- | --- |
| P5-C model benchmark | **BLOCKED** (provider 402) |
| Codex adapter | **NOT_TESTED** |
| Claude Code adapter | **NOT_TESTED** |
| BLACKBOX adapter | **ADAPTER_ONLY, NOT_TESTED** |
| Non-Python real project | **FAIL**: Pine Script project maps 0 sources |
| P5-D cross-host | **NOT_TESTED** |

## 4. Release decision

`1.0.0-rc.1` is published for **OpenCode + WORKSPACE install** only. It is not a
full 1.0 because B1 and B2 are open. The next release must, in this order:

1. resolve the global OpenCode config path and install the plugin there;
2. run one fresh session in an unregistered non-Python folder with no EOS
   command and record activation, map, EDR, write denial and post-write refresh;
3. re-run the four gates and publish 1.0.0.

No new feature work is included.
