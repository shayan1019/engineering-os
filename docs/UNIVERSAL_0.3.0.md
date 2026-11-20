# EOS vNext 0.3.0 — Universal runtime (P4)

Date: 2026-11-21
Engineering decision: `edr-20261120T214003-b55089` (STANDARD)
Scope: universal workspace discovery, project isolation, generic routing,
tool/skill evidence, completion gate, safe ship, host scope.

## 1. Universal workspace

`workspace.py` answers three questions deterministically:

| Question | Mechanism |
| --- | --- |
| Which projects exist under a root? | marker files (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `*.csproj`, `*.mq5`, ...) |
| Is this path inside the scope? | `realpath` + `commonpath`, so a junction/symlink cannot escape |
| Which project owns this path? | the nearest ancestor that carries a marker or `.eos/project.json` |

Commands:

```
workspace.py scope set --root <dir> --mode workspace|global --host opencode
workspace.py scope show
workspace.py discover --root <dir> [--write]
workspace.py resolve --path <dir>
workspace.py status
```

Discovery on this machine: 15 real projects (BigHero, AURA, alpha, liquid-demo,
monster_k3_rgb, zipforge, forexnewsbot, engineering-os/cli, vpniran, UPS templates,
zipforge/src-tauri, ...). Backup trees (`_d6_isolated`, `_restore_test`,
`_runtime_snapshots`, `_ARCHIVE_*`) are excluded by prefix, not guessed.

New, unregistered project routing was proved with `monster_k3_rgb`:
`source=discovered`, `tech=["python"]`, `confidence=MEDIUM`, and the invariant
"architecture and tests are UNKNOWN until inspected". A known project
(`zipforge`) still resolves through its registered profile.

## 2. Generic specialist selection

`route_task.py` now selects roles from task wording, discovered technology, risk
and domains, not from a project name:

- `researcher` — external documentation or an explicit unknown
- `dependency-analyst` — blast radius / runtime boundaries (web, mobile, rust, go, dotnet, medium+)
- `logic-reviewer` — formula, units, leverage, PnL, trading/finance domains
- `bug-hunter` — failures, regressions, medium/high risk
- existing architect, implementer, reviewer, verifier, security, test, performance, evidence

The team is capped at six. Architect, implementer and verifier are never dropped;
an explicitly triggered role (tests, security, logic, bugs) is protected.
`route_tests.py`: 20/20 after the change.

## 3. Tool and skill evidence

The guard writes one JSONL event per permission/tool call. `tool_evidence.py`
turns it into evidence types:

- `decision` — an EDR exists
- `gate` — a gate run/artifact exists
- `test` — a real pytest/unittest shell call
- `research` — a webfetch/websearch call
- `skill` — a read of `.agents/skills/<name>/SKILL.md`

A selected skill is not a used skill. A planned test is not a run test. Missing
evidence is **BLOCKED**.

`completion.py` is the completion gate:

```
completion.py check --project <id> --task "<text>" --decision <edr> [--session S] [--skills a,b]
```

PASS requires: CLOSED decision + PASS gate whose hash still matches + zero open
challenge blockers + the tool evidence the task needs + the selected skill was
actually read. Selftests: `tool_evidence` 5/5, `completion` 4/4.

## 4. Safe Git delivery

`ship.py` is the only commit/push path:

- requires a CLOSED decision and its verified PASS gate;
- every file must be inside the decision scope and inside one Git repository;
- branch must match `eos/<task-id>`; `main`, `master`, `trunk`, `develop`,
  `release` are refused;
- stages exactly the decision files, never unrelated user work;
- never force-pushes, never rewrites history, never deletes a branch;
- a push failure is reported with the commit SHA; the commit is not reset;
- writes a receipt under `.agents/evidence/git/<project>/`.

Selftest: 5/5, including a real bare remote where the pushed SHA equals the
local HEAD.

## 5. Global / Workspace installation

`scope_install.py`:

| Mode | Behaviour |
| --- | --- |
| `workspace` | scope file at `<root>/.eos/scope.json`; real-path isolation; host adapters under that root |
| `global` | scope file at `~/.eos/scope.json`; no filesystem-wide permission is granted by the tool |

Host capability is explicit (`registry/HOSTS.json`):

| Host | Context | Guard | Status |
| --- | --- | --- | --- |
| OpenCode | plugin | verified | **FULL_GUARD** |
| Codex | AGENTS.md | unverified | CONTEXT_ONLY |
| Claude Code | CLAUDE.md | unverified | CONTEXT_ONLY |
| BLACKBOX | unknown | none | ADAPTER_ONLY |

A context file is not a guard. Only OpenCode has a live test that a denied write
does not reach the filesystem. The installer writes a managed
`<!-- EOS:BEGIN -->` block and removes only that block on uninstall; user rules
are preserved. Selftest: 5/5 (install, idempotent re-install, uninstall, unknown
host, global plan).

## 6. Real model calls: BLOCKED by provider balance

Both model IDs are present in `opencode models`, but a real invocation failed:

| Model | Result |
| --- | --- |
| `opencode/claude-fable-5-2` | HTTP 402, "Insufficient balance. OpenCode Go covers only the OpenCode Go provider." |
| `opencode/gpt-6.2-astra` | HTTP 402, same |
| `openrouter/anthropic/claude-fable-5.2` | HTTP 402, key can afford 1000 tokens but the request declares 128000 output |

Evidence: `~/.eos/evidence/models/model-probe-20261120T2155*.json` and
`...2157*.json`. Therefore:

- `MODEL_POLICY.json` remains **PROVISIONAL**;
- no live Fable+Astra team run is claimed;
- the typed team runtime is proven with a fixture worker (5/5), not with a live
  premium model.

This is a billing blocker, not a code blocker. Top up the provider or set a
smaller output limit, then re-run:

```
model_probe.py invoke --model opencode/claude-fable-5-2 --prompt "Reply with exactly OK"
model_probe.py invoke --model opencode/gpt-6.2-astra --prompt "Reply with exactly OK"
```

## 7. What is still missing

| Item | Status |
| --- | --- |
| Fable + Astra live team coordination | BLOCKED by 402 |
| Cross-project scenario benchmark (web, backend, desktop, mobile, AI, security) | P5 |
| Always-On live tests on Codex / Claude Code / BLACKBOX | adapter contracts only; no host test on this machine |
| BigHero full-suite failure baseline | PENDING measurement; `unit` gate BLOCKED |
| Skill usage on hosts other than OpenCode | needs adapter JSONL parity |
| Metrics loop | P6 |

## 8. Evidence

- `.agents/evidence/gates/eos/gate-vnext_dev-*.json` — universal runtime suite
- `.agents/evidence/decisions/eos/edr-20261120T214003-b55089.json` — this change
- `engine/registry/HOSTS.json` — host capability matrix
- `engine/registry/topology/bighero.json` — read-only reference topology
- `~/.eos/evidence/models/model-probe-*.json` — model list and the 402 invocations
