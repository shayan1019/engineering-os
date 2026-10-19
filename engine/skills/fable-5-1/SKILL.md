---
name: fable-5-1
description: "Fable 5.1 -- unified working discipline for complex agent work: scope/do-not-regress, native task-graph orchestration (dependencies, parallelism, single-writer, merge-owner, bounded repair, conditional knowledge/evidence graph), capability-aware runtime execution (async tools, mid-turn steering, subagents), and conditional teaching mode for explain/why/diagram requests. One skill identity; internally modular, loaded on demand."
---

# Fable 5.1

Fable 5.1 is the canonical id for the unified working methodology: Fable's execution
discipline as the working authority, Graph Engineering as its internal structuring engine,
the provider-independent work disciplines (`execution/` — pending-work, work-continuity,
delegation, steering-replan, state-continuity; Astra-derived provenance, no model or
provider dependency) as native methodology for complex/long-horizon/multi-agent work,
and runtime capability awareness (async tools, steering, subagents) as an execution layer —
never the other way around. See `skill.contract.yaml` for the typed contract (execution-depth
model, capability model, authority precedence, do-not-regress sources).

> **Status: Stage 5 of `~/.claude/plans/fable-5-1-parsed-hinton.md`.** `core/` (Stage 2),
> `graph/` (Stage 3), `runtime/` (Stage 4), and `policy/`+`evidence/` (Stage 5) are all native
> `fable-5-1` behavior now — risk classification, permission/capability separation, evidence
> shape, verification, and completion-state semantics are integrated, without claiming
> mechanical enforcement this workspace doesn't have (see `policy/risk-contract.md`).
> `fable5-thinking` and `graph-engineering` remain the live, byte-preserved sources of truth
> until Stage 6 parity is shown and Stage 7 cutover is explicitly approved. BLOCKER-1
> (`.claude/skills` adapter) remains open — see
> `.agents/backups/baseline_2026-09-07/KNOWN_BLOCKERS.md` — and must be resolved before
> Stage 6 parity claims / Stage 7 cutover.

## Load rules (progressive disclosure — do not load everything up front)

Always load:
- [core/scope-contract.md](core/scope-contract.md)
- [core/execution-discipline.md](core/execution-discipline.md)
- [core/task-complexity-classifier.md](core/task-complexity-classifier.md)

Conditional — Fable core:
- Load [core/memory-discipline.md](core/memory-discipline.md) when the task involves
  learning something about the user, or applying previously saved preferences/context.
- Load [core/completion-model.md](core/completion-model.md) before making any claim that a
  task, or a step of it, is done.
- Load [core/do-not-regress.md](core/do-not-regress.md) whenever the task changes existing
  behavior (not a purely additive/net-new task).

Conditional — graph (once `core/task-complexity-classifier.md` classifies the task GRAPH or
higher):
- Load [graph/task-graph.md](graph/task-graph.md) first — node/edge fundamentals, fake-edge
  check, human gate placement.
- Load [graph/parallelism.md](graph/parallelism.md) before running anything concurrently.
- Load [graph/writer-and-merge-owner.md](graph/writer-and-merge-owner.md) whenever more than
  one job could touch the same artifact, or a MULTI_AGENT_GRAPH merge is needed.
- Load [graph/verification-diamond.md](graph/verification-diamond.md) before any completion
  claim at GRAPH level or above.
- Load [graph/bounded-repair.md](graph/bounded-repair.md) when a verifier reports failure and
  a retry/repair/replan/terminal-block decision is needed.
- Load [graph/knowledge-graph-trigger.md](graph/knowledge-graph-trigger.md) only at
  EVIDENCE_GRAPH level, per that file's own trigger section — never by default.
- Load [graph/repo-graph-contract.md](graph/repo-graph-contract.md) whenever a
  repo-structure question arises (what calls X, blast radius, dependency shape)
  at PLANNED level or above — it defines what the repo graph is and is not.
- Load [graph/change-impact-preflight.md](graph/change-impact-preflight.md)
  before any mutation at GRAPH level or above (REQUIRED), for PLANNED
  multi-module/uncertain-blast-radius mutations (CONDITIONAL), optionally at
  DIRECT — never at ATOMIC.
- Load [graph/teaching-mode.md](graph/teaching-mode.md) only on genuine teaching/explanation
  intent (explain, teach, walk me through, why, diagram, compare concepts, a learning/tutorial
  request) — never on an ordinary execution task, and never as a substitute for actually doing
  the work on a mixed request.

Conditional — execution (provider-independent work disciplines, Astra-derived provenance):
- Load [execution/pending-work.md](execution/pending-work.md),
  [execution/work-continuity.md](execution/work-continuity.md),
  [execution/delegation-discipline.md](execution/delegation-discipline.md),
  [execution/steering-replan.md](execution/steering-replan.md), and
  [execution/state-continuity.md](execution/state-continuity.md) for every GRAPH-level or
  higher task (`core/task-complexity-classifier.md`), and for any long-horizon or
  multi-agent task even before the graph is drawn — on any runtime (Claude, OpenCode,
  Codex, Gemini), regardless of capability flags and without any model or provider
  dependency. Never gated on a provider name; never skipped because a capability flag
  is `false`/`unknown`.

Conditional — runtime (do not load every module on every simple task):
- Load [runtime/capability-model.md](runtime/capability-model.md) before any
  capability-dependent planning or execution decision — this is the only file that defines
  the flag vocabulary the others below consume.
- Load [runtime/scheduler-behavior.md](runtime/scheduler-behavior.md) for every GRAPH-level
  or higher task — section A (provider-independent execution principles) always applies,
  even when all capability flags are `false`/`unknown`; section B only decides the
  concurrent-vs-sequential mechanism.
- Load [runtime/steering-behavior.md](runtime/steering-behavior.md) for every GRAPH-level or
  higher task with in-flight work — the preserve-valid-work / impact-replan principle
  applies even when `mid_turn_steering` is `false`/`unknown`; only the timing
  (instant vs. checkpoint/replan) differs by capability.
- Load [runtime/state-behavior.md](runtime/state-behavior.md) for long-running or
  persistence-relevant tasks, or whenever structured-output contracts are being decided —
  the "what Fable owns" state list and textual-contract discipline apply even when
  `persistent_state`/`structured_output` are `false`/`unknown`; only the persistence and
  serialization mechanism differ.
- Load [runtime/fallback-behavior.md](runtime/fallback-behavior.md) whenever a
  capability-dependent optimization above turns out to be unavailable.
- Load [runtime/graphify-adapter.md](runtime/graphify-adapter.md) whenever the
  optional repo-graph provider is consulted, absent, dead, or suspected stale
  — access, detection, and fallback behavior only.
- Load [runtime/project-bootstrap.md](runtime/project-bootstrap.md) on the
  first task in a project that needs repo intelligence (PLANNED or higher) —
  universal lazy bootstrap, project-local install/state, never on shell init,
  never for ATOMIC.
- [runtime/claude-adapter.md](runtime/claude-adapter.md) and
  [runtime/astra-adapter.md](runtime/astra-adapter.md) are optional, provenance/reference
  only — not part of the execution load path above, consulted only out of curiosity about
  what evidence exists for a given provider.

Conditional — policy and evidence:
- Load [policy/risk-contract.md](policy/risk-contract.md) whenever risk classification
  matters — effectively any task with a mutation or side effect.
- Load [policy/permissions.md](policy/permissions.md) whenever a capability-vs-permission
  distinction is at stake (e.g. before treating "the runtime can do X" as "X is allowed").
- Load [evidence/evidence-contract.md](evidence/evidence-contract.md) and
  [evidence/verification.md](evidence/verification.md) before any completion claim gated by
  `policy/risk-contract.md`'s `verify_required`/`approval_required`, or any GRAPH-level task.
- Load [evidence/provenance.md](evidence/provenance.md) only under its own trigger
  conditions (mirrors `graph/knowledge-graph-trigger.md`'s EVIDENCE_GRAPH trigger) — never by
  default.

## Grounding material

- [references/fable5-full-system-prompt.txt](references/fable5-full-system-prompt.txt) —
  carried over verbatim from `fable5-thinking/references/`, byte-identical.
- [references/task-graphs.md](references/task-graphs.md),
  [references/curriculum.md](references/curriculum.md),
  [references/modeling.md](references/modeling.md),
  [references/extraction.md](references/extraction.md),
  [references/fusion-and-llm.md](references/fusion-and-llm.md) — carried over verbatim from
  `graph-engineering/references/`, byte-identical.
- [../graph-engineering/SKILL.md](../graph-engineering/SKILL.md) — the original source this
  package's `graph/*.md` natively distills from. Kept for provenance/audit; **not required**
  to execute Fable 5.1 graph behavior.
- [references/ATTRIBUTION.md](references/ATTRIBUTION.md) — full credits/provenance for every
  source this package synthesizes from.

The `core/*.md` and `graph/*.md` files above are a native restatement of the relevant source
sections, not a replacement for the source as a grounding/audit reference.
`execution/*.md` are new synthesis (Astra-derived provenance, provider-independent
methodology — behavior sources, not model dependencies), not a restatement of either preserved source skill.

## Trigger

*Native restatement of `fable5-thinking`'s "Tetikleme" section — found missing from this
file during Stage 6 baseline-parity re-verification and patched explicitly, not silently.*

**Current status (pre-Stage-7)**: `fable-5-1` is not yet the mandatory-every-session load —
`fable5-thinking` still holds that role per `AGENTS.md` §1-2 until Stage 7 cutover is
explicitly approved. `fable-5-1` is loaded on request/explicit invocation, or when routing
selects it, same as any other candidate skill.

**Target status (post-Stage-7, not yet in effect)**: `fable-5-1` becomes the mandatory
every-session load for MY PROCECT on Claude runtime, superseding `fable5-thinking`'s
standalone trigger — loaded in other projects too if useful, unchanged from the source
behavior. This section will be updated to drop the "current/target" distinction once Stage 7
is actually approved and executed.
