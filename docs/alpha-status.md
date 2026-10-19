# EOS ALPHA — status and evidence

Date: 2026-10-19 · Platform: Windows 11 Pro 26200 · Runtime: OpenCode 2.0.32
Scope: Windows + portable core. macOS builds, code signing, notarization, installer
and desktop UI are BETA/STABLE work and are explicitly not claimed here.

Every row below was produced by executing a command in this workspace. Rows that
could not be verified say so.

## 1. Verified

| # | Capability | Evidence |
| --- | --- | --- |
| 1 | Baseline snapshot with restore proof | `snapshot.py create --label 2026-10-19 --with-payload` → 1655 files, 36.5 MB; `verify` → 1655 ok, 0 changed, 0 missing; `restore-test` → all files re-hashed identically in a temp tree |
| 2 | Skill discovery | `skill_index.py discover` → 423 entries across 6 roots, 210 unique names, 213 collisions classified (`INTENTIONAL_OVERRIDE` 212, `STALE_DUPLICATE` 1) |
| 3 | Skill selection with a reason | `skill_index.py explain` returns different top-3 sets for BigHero (trading), zipforge (Tauri/Rust) and vpniran (network/security), each with per-skill reasons and a rejected near-miss list |
| 4 | Ranking is not keyword-only | task tokens are bridged Turkish→English (islem→trading, kanitla→evidence); project domains weigh 3.0, task-only domains 1.5, curated domains come from SKILL_CATALOG.json |
| 5 | Agent registration | `agent_dispatch_test.py` reads `opencode debug agents`: 16 agents, all 9 EOS agents present with the intended mode |
| 6 | Edit boundary per role | runtime view: chief/architect/reviewer/verifier/security/performance/evidence = `edit deny`; implementer = allow; test-engineer = glob (`tests/**`, `test/**`, `**/tests/**`) |
| 7 | Delegation boundary | chief-architect has `subagent "*": deny` plus exactly 9 named allows; every other agent denies delegation entirely |
| 8 | Real dispatch | `opencode run --standalone --agent evidence-analyst --model opencode/deepseek-v4.1-flash-free` from `engineering-os` (no project `.opencode`) returned `DISPATCH_OK` |
| 9 | Global integration is reversible | `agents install --target global` writes 9 files to `~/.config/opencode/agents` with a backup dir; `agents uninstall --target global` removes exactly those files (9 removed) |
| 10 | Gate returns PASS | `gate run --project eos --suite pass` → PASS, rc=0, artifact + log hash + baseline recorded |
| 11 | Gate refuses to rubber-stamp | `suite fail` → FAIL, `suite mixed` (1 pass + 1 fail) → FAIL |
| 12 | Optional steps do not block | `suite optional_failure` → PASS with `optional_failures: [fail_optional]` recorded |
| 13 | Timeout enforced | `suite timeout` (2 s) → rc=124 after 2.03 s, not an indefinite wait |
| 14 | Missing tool reported | `suite missing_binary` → rc=127 recorded as failure, not an exception |
| 15 | Gate is not agent-programmable | commands come only from `GATES.json`; the CLI has no flag accepting a command |
| 16 | Promotion refuses FAIL | `promote --gate <fail artifact>` → BLOCKED (`verdict is FAIL`, `a required command failed in the record`) |
| 17 | Promotion refuses project mismatch | `--project bighero --gate <eos artifact>` → BLOCKED |
| 18 | Promotion refuses missing/absent payload | missing file → BLOCKED; no `--artifact` → BLOCKED |
| 19 | Promotion refuses stale evidence | `gate verify --max-age-min 0` → BLOCKED |
| 20 | Tampering detected | editing the recorded log hash → BLOCKED (`gate log hash mismatch`); editing a promoted file after the fact → BLOCKED |
| 21 | Baseline integrity tied to the gate | `verify` re-hashes the baseline snapshot; replacing it invalidates the artifact |
| 22 | Graph portability defect detected | BigHero → `STALE_RELOCATED`: graph built at `/Users/mac/Desktop/MY PROCECT/6.0_final_merge`, 286 sources newer, head `335f8646` vs `2e473334`, coverage `partial` |
| 23 | MQL coverage never claimed | 74 MQL files, `.mq5/.mqh` absent from indexed languages → findings mark MQL impact PARTIAL; `graph fallback` emits 371 lexical definitions with an explicit PARTIAL confidence and limits |
| 24 | Destructive graph step gated | `graph invalidate` without `--confirm` → BLOCKED with a printed plan |
| 25 | Secret scan works | control plane with backups/evidence excluded → 1670 files, 0 findings; a planted `ghp_…` token in a temp file → detected |
| 26 | Published tree is clean | `secret_scan.py engineering-os` → 44 files, 0 findings; staged-file check → no `.db/.log/.env/.ex5/.hcc/.pem` |
| 27 | Router integrated | `route_task.py` now emits `agents[]` with rationale and `skills.ranked[]`; `route_tests.py` → 14/14 PASS (no regression) |
| 28 | Workspace validators | `validate_workspace.py` → 0 errors; `validate_skills.py` → warnings only (broken skill links, shadowed duplicates) |
| 29 | Portability at the registry level | `SKILL_INDEX.json` and `GATES.json` contain no absolute paths; global roots are recorded as `~/…`, others as `<external>/<basename>` |
| 30 | Repo published | `shayan1019/engineering-os` private, 44 files, remote head `744e9a2` |
| 31 | CLI works | `eos version|doctor|skills discover|gate run|agents verify|route|graph report` all executed; `eos doctor` → READY, 0 failures |
| 32 | CLI resolves the engine by context | control plane wins when present, `engine/` tree otherwise; `EOS_ROOT`/`EOS_PYTHON` overrides |

## 2. Deliberately blocked (no false green)

| Area | State | Why |
| --- | --- | --- |
| BigHero / zipforge gate suites | `NOT_CONFIGURED` → BLOCKED | the real test entry points were not verified in this session; the gate refuses to invent a command |
| MQL5 semantic analysis | PARTIAL | graphify 0.9.66 in this workspace does not index `.mq5/.mqh` |
| Graph reindex | not executed | `invalidate` is destructive inside a user project and needs a human confirmation plus the project's own reindex command |
| Code signing, notarization | absent | no certificates; ALPHA/BETA are unsigned development builds |
| macOS builds | not attempted | no macOS host; needs CI runners and a budget decision |
| GitHub Actions | absent | the token has no `workflow` scope; `gh auth refresh -s workflow` is interactive and must be run by the user |
| Model execution inside *this* agent set | partial | the default model for a fresh run required an OpenCode Go subscription; an explicitly selected free model worked |
| chief → specialist delegation end-to-end | not executed | permission boundary is verified mechanically; a full delegated task was not run to keep the ALPHA cost bounded |
| `opencode debug agents` from an unrelated cwd | unusable | the command reports the background server's project context (returns `[]` elsewhere), so cross-directory probing is invalid; global visibility was instead proven by an actual `opencode run --agent` from a foreign directory |

## 3. Known limitations

1. Skill ranking is lexical plus curated domains. Precision is bounded by the
   quality of domain tags in `SKILL_CATALOG.json`. Example: `bighero.preferred_skills`
   contains `alpaca-broker-market-data`, which is a different broker; the ranking
   surfaces it above `mt5-trading` for a BigHero task. The decision trace makes
   this visible instead of silent. Fixing it means editing profile data, which is
   a user-owned contract.
2. `subagent` permission lists are visible in the runtime only as merged rules;
   the wildcard deny plus named allows is verified, not the exact evaluation order
   for every edge case.
3. Background-service caching means agent file changes need `opencode reload`
   before they are observable, in both directions.
4. The published repo is a verified copy of the live control plane
   (`publish.py sync` + `verify`), not the other way round. A drift check exists;
   a single-source-of-truth move belongs to BETA.

## 4. Reproduce

```bash
python .agents/tools/snapshot.py verify --snapshot .agents/backups/baseline_2026-10-19
python .agents/tools/skill_index.py discover
python .agents/tools/skill_index.py explain --task "BTCUSD kaybini arastir" --project bighero
python .agents/tools/agent_dispatch_test.py
python .agents/tools/quality_gate.py run --project eos --suite pass
python .agents/tools/quality_gate.py run --project eos --suite fail
python .agents/tools/graph_freshness.py report --project bighero
python .agents/tools/secret_scan.py .agents --exclude ".agents/backups/*" --exclude ".agents/evidence/*"
python .agents/router/route_tests.py
python .agents/tools/publish.py verify
```

## 5. Next (BETA entry criteria)

1. Configure real gate suites for BigHero, AURA, vpniran and zipforge, and prove a
   FAIL that turns into a PASS with a real fix.
2. Run one fully delegated task (chief → architect → implementer → reviewer →
   verifier) and record the transcript as evidence.
3. Move the canonical source into the repository and make the workspace consume it.
4. Decide the macOS CI budget, then add signed builds.
5. Optional: evaluate Ruflo against this baseline with a measured comparison.
