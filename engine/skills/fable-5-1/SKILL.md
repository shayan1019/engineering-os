---
name: fable-5-1
description: "EOS working discipline for complex agent work: scope and do-not-regress, task-graph orchestration (dependencies, parallel fan-out, single writer, merge owner, bounded repair), capability-aware runtime execution (async tools, steering, subagents), conditional knowledge-graph work, and evidence-first completion."
---

# EOS working discipline (`fable-5-1`)

`fable-5-1` is the canonical working methodology shipped with Engineering OS.
The identifier is kept for router and package compatibility; the content is
original EOS material, not a vendor system prompt.

It combines four layers:

1. **Scope and completion** — what is being changed, what must not regress, and
   what counts as done.
2. **Graph structure** — a task graph for execution and, only when the task
   requires it, a knowledge graph for relationships.
3. **Runtime adaptation** — use the capabilities the host actually has; never
   pretend a missing capability exists.
4. **Evidence** — every completion claim is backed by a command, artifact, or
   cited source.

The methodology is modular. Load the core, then load only the modules the task
needs. Do not load a whole tree because it exists.

## Load order

### Always

- `core/scope-contract.md` — intent, scope, acceptance, do-not-regress.
- `core/execution-discipline.md` — understand, plan, act, verify.
- `core/task-complexity-classifier.md` — choose direct, planned, graph, or
  multi-agent depth.
- `core/completion-model.md` — what "done" means and what it does not mean.

### When the task is long, risky, or multi-file

- `core/do-not-regress.md`
- `core/memory-discipline.md`
- `graph/task-graph.md`
- `graph/parallelism.md`
- `graph/writer-and-merge-owner.md`
- `graph/verification-diamond.md`
- `graph/bounded-repair.md`
- `graph/change-impact-preflight.md`

### When a repository graph is available

- `graph/repo-graph-contract.md`
- `runtime/graphify-adapter.md`

### When the task is a knowledge-graph or teaching request

- `graph/knowledge-graph.md`
- `graph/teaching-mode.md`

### When work spans sessions or delegates

- `execution/pending-work.md`
- `execution/work-continuity.md`
- `execution/state-continuity.md`
- `execution/delegation-discipline.md`
- `execution/steering-replan.md`
- `runtime/scheduler-behavior.md`
- `runtime/steering-behavior.md`
- `runtime/state-behavior.md`

### Runtime and policy

- `runtime/capability-model.md`
- `runtime/fallback-behavior.md`
- `runtime/provider-adapters.md`
- `runtime/project-bootstrap.md`
- `policy/risk-contract.md`
- `policy/permissions.md`
- `evidence/evidence-contract.md`
- `evidence/verification.md`
- `evidence/provenance.md`

## Non-negotiable rules

1. **One writer per file.** Parallel work requires disjoint write sets and an
   explicit merge owner.
2. **The writer is not the verifier.** A separate role runs the gate.
3. **A model saying "done" is not completion.** Completion is an artifact.
4. **No unbounded repair.** Every loop has an attempt limit and a stop rule.
5. **No capability invention.** Absent host/provider → `BLOCKED` or
   `ADAPTER_ONLY`.
6. **No policy bypass from a graph.** A repository graph or knowledge graph is
   evidence, not authority.
7. **Irreversible actions need a human gate.**
8. **Committed configuration uses relative paths.** An absolute local path is a
   defect.

## Boundaries

- `graph/*` defines structure and scheduling; `core/*` defines intent, scope,
  acceptance, and completion.
- `runtime/*` defines how to use a capability; it never changes behavioral
  policy.
- `policy/*` defines risk and permission rules; it is not a suggestion.
- `evidence/*` defines what must be recorded; it is not optional for a PASS.

The typed contract is in `skill.contract.yaml`. Integration notes for an
optional repository graph are in `tests/graphify-integration.md`. Attribution
and provenance are in `references/ATTRIBUTION.md`.
