# Task Graph

*Native Fable 5.1 restatement of `graph-engineering/references/task-graphs.md` — checklist items B2.1, B2.2, B2.7, B2.8, B2.11. Wording reorganized; behavior unchanged.*

> **Authority boundary**: this file (and the rest of `graph/*.md`) defines *how* work is structured — dependency shape, node types, scheduling. It does not redefine user intent, scope, acceptance criteria, do-not-regress, completion semantics, evidence requirements, or user-steering policy — those stay `core/*.md`'s responsibility. Precedence, per `skill.contract.yaml`: `fable_methodology > graph_scheduling_preference > runtime_optimization`.

## What a task graph is

Nodes are jobs — each one something you'd hand to a single assistant (research one competitor, write one draft, check one claim). Draw an edge only when a job needs another job's *result* before it can start. A small state object (what was found, what was decided, what remains) travels with the work. (B2.1)

For small tasks (`core/task-complexity-classifier.md`'s DIRECT/PLANNED levels), this is a mental pass, not a written artifact. At GRAPH level or above, the routing lives in written steps — the model fills the jobs, not the plan. Don't improvise the graph shape mid-execution once it's written down. (B2.11)

## Fake edges

The first, free optimization: for every "and then" in a candidate pipeline, ask whether the next job actually reads the previous job's output. "Summarize this file and then check my calendar" — the calendar step never uses the summary; the edge is fake. Delete fake edges and those jobs become independently schedulable — see `graph/parallelism.md` for what "schedulable in parallel" actually requires beyond just lacking a real edge. (B2.2)

## The human gate is a node

Route every irreversible edge — send, publish, refund, delete, deploy — through explicit approval, modeled as a node in the graph like any other job. Placement rule: put the gate where a mistake is expensive to undo, not on every step. A gate on everything makes the human the bottleneck; a gate on nothing means nobody is watching. (B2.7)

Judge the graph on numbers that can't argue back — tests that ran, money that landed — never on its own self-reports of success. This is the same evidence discipline as `core/completion-model.md`, applied at the graph level: a node claiming "done" is not evidence the node is done. (B2.8)

**Related but distinct** — Stage 5 (`policy/risk-contract.md`, not yet built) will add a separate risk-classification-label mechanism (`auto`/`verify_required`/`approval_required`) derived from `FABLE_CORE.md` P8 and `route_task.py`'s risk keywords. That's a different, complementary mechanism (a label on a task) from this file's human-gate-as-a-node principle (a placement rule within a graph's structure) — both preserved, neither replaces the other.
