# EOS 0.3.1 — Host-session coordination and automatic EDR

Date: 2026-11-21
Base: `eos/vnext-0.3.0` @ `2f643b090c8a`
Decision: `edr-20261120T221448-abfaa9` (STANDARD)

## 1. Chief is no longer a required mode

`chief-architect` remains only as an optional read-only planning specialist
(`mode: subagent`). It has no edit permission and no delegation. The active host
session coordinates:

- `route_task.py` returns `orchestrator: host-session`;
- `eos_activate.py` renders `orchestrator: host session (EOS Core coordinates; no Chief mode required)`;
- `team.py` plans carry `orchestrator: host-session` and no coordinator node;
- `AGENT_REGISTRY.json` lists every current specialist and states the host
  session owns coordination.

No user selects Chief, Plan, Build or EOS mode to activate EOS.

## 2. Automatic Engineering Decision Record

`auto_decision.py prepare --task ... --scope ...`:

1. resolves the project and scope;
2. runs the impact engine when available;
3. classifies risk from scope size, cross-process impact, sensitive surface and
   high-risk wording;
4. creates the EDR and fills objective, current behaviour, do-not-regress,
   evidence, assumptions, impact, alternatives, verification and rollback;
5. auto-approves **ATOMIC only**.

| Tier | Behaviour |
| --- | --- |
| ATOMIC | one file, low risk, no cross-process, bounded impact → auto-approved |
| STANDARD | 2–3 files or non-trivial change → prepared, specialist review required |
| CRITICAL | high-risk words, sensitive surface, cross-process or >3 files → never auto-approved; human required |

The guard calls `auto_decision.py` itself when a write is denied, using the
task text captured from the session prompt. The user does not run a decision
command.

### Live host evidence

| Test | Result |
| --- | --- |
| Prompt: create `.agents/evidence/auto-test.txt` | file created (`OK`); decision `edr-20261120T222618-042f55` **APPROVED_FOR_EDIT** by `auto ATOMIC decision` |
| Prompt: "Deploy to production: create `.agents/evidence/auto-critical.txt`" | file **absent**; decision `edr-20261120T222906-c73546` **DRAFT, CRITICAL**, not approved |
| Existing BigHero session on another project | its write was allowed only by its own decision; the workspace-root marker bug was found here and fixed |

## 3. Cross-project isolation

`decision.infer_project` used to return the workspace root `.eos/project.json`
id for every child path. That made a BigHero path look like the workspace and
could let the wrong decision authorise it. Now:

1. a `.eos/project.json` **below** the workspace root wins;
2. registered profile paths are checked next (`6.0_final_merge` → `bighero`,
   `engineering-os` → `eos`);
3. the workspace root id is only a fallback.

`decision.py selftest`: **17/17**, including "another project's decision cannot
authorise a path" and "workspace root marker does not swallow a child project".

## 4. Team and verification

`team.py selftest`: **5/5**; plan orchestrator is `host-session`; the typed
result contract, challenge ledger merge and blocker behaviour are unchanged.
`runtime_permissions check-contracts`: **13 agents, 0 problems**.
`vnext`: **PASS 5/5**. `vnext_dev`: **PASS 7/7**. Router: **20/20**. Guard:
**10/10**.

## 5. Ship

`ship.py` now verifies the pushed branch with `git ls-remote` and refuses a
receipt whose remote SHA differs from the local commit. Selftest: **5/5**,
including a real bare remote.

## 6. What is still blocked

| Item | Status | Why |
| --- | --- | --- |
| Fable 5.2 + Astra 6.2 live team run | BLOCKED | provider 402: OpenCode balance; OpenRouter key affordability |
| Codex / Claude Code / BLACKBOX live Always-On | NOT TESTED | no verified host hook on this machine; matrix stays CONTEXT_ONLY / ADAPTER_ONLY |
| BigHero full-suite baseline | PENDING | full run is 4h+ at current speed; unit gate stays BLOCKED |
| P5 cross-project scenario benchmark | NOT BUILT | next phase |
| P6 metrics loop | NOT BUILT | next phase |

## 7. Evidence

- `.agents/evidence/gates/eos/gate-vnext-20261120T223149Z.json` (PASS)
- `.agents/evidence/gates/eos/gate-vnext_dev-20261120T223233Z.json` (PASS)
- `.agents/evidence/decisions/5a332e34-.../edr-20261120T222618-042f55.json` (auto ATOMIC)
- `.agents/evidence/decisions/5a332e34-.../edr-20261120T222906-c73546.json` (CRITICAL DRAFT)
- `.agents/evidence/tools/ses_e04eefa0affeufXb7Yhn9nEoYS.jsonl` (auto ATOMIC permission)
- `.agents/evidence/tools/ses_*.jsonl` (auto critical denial is recorded by the host transcript; the file is absent)
