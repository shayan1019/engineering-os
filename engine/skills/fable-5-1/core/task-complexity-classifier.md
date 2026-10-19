# Task Complexity Classifier

*New synthesis — this file is genuinely new, not a distillation of existing `fable5-thinking`/`graph-engineering` text (flagged explicitly per the approved plan's gap analysis, item F). It formalizes a judgment `fable5-thinking` already makes informally (A11: "simple questions get short answers; open-ended questions get proportionate depth"; A12: "structure before solution") into named levels, so the rest of this package can reference a level by name instead of re-arguing the judgment call each time.*

Neither source skill currently decides, up front, "does this task need a graph at all" — that judgment was previously left implicit. This file makes it explicit, without prescribing graph mechanics itself (those are Stage 3's).

## Levels

1. **ATOMIC** — a single, self-contained action with no real decision points (answer a factual question, make a one-line edit, run one command). No plan, no graph. Corresponds to A11's "simple questions get short answers."
2. **DIRECT** — a task with a few sequential steps, none of them independently parallelizable, no meaningful risk of overlapping writes. Mentally track the steps; no written plan needed. This is the default level absent a reason to go higher (see `skill.contract.yaml`'s `execution_depth_model.default`).
3. **PLANNED** — a multi-step task worth writing the scope contract down (`core/scope-contract.md`) before starting, because the steps, risks, or verification method aren't obvious from a single read of the request. Still single-agent, still sequential.
4. **GRAPH** — the task has real independent sub-pieces (parallelizable) and/or a genuine dependency structure worth drawing out explicitly, and/or touches more than one file where write conflicts are possible. *Mechanics (dependency types, parallel fan-out, one-writer, merge-owner, bounded repair) are native `fable-5-1` behavior starting Stage 3 — this file only names the level and the trigger question, it does not yet define how a GRAPH-level task is executed.*
5. **MULTI_AGENT_GRAPH** — GRAPH-level work where the independent pieces are large/distinct enough to warrant separate agents or subagents rather than one agent working through a list. *Mechanics: Stage 3/4.*
6. **EVIDENCE_GRAPH** — the task involves claims that need explicit claim→evidence lineage across multiple sources (provenance, multi-hop reasoning, an audit trail), not just a single evidence line per claim (`core/execution-discipline.md`, A13). This is the least common level and is never the default. *Mechanics: Stage 3/5 (`graph/knowledge-graph-trigger.md`, `evidence/provenance.md`).*

## The trigger question

Before starting: "Does this task need more than a mentally-tracked sequence of direct steps?" If no, stay at DIRECT (or ATOMIC). If yes, ask which of PLANNED/GRAPH/MULTI_AGENT_GRAPH/EVIDENCE_GRAPH actually applies — don't default upward "to be safe." Per `skill.contract.yaml`, the default is `direct`, and `evidence_graph` may never be a default for any task, only an explicit escalation.

## Relationship to Graph Engineering's stop rule

Once Stage 3 lands, `graph/task-graph.md`'s "fake edges" and stop-rule content will sit directly beneath GRAPH/MULTI_AGENT_GRAPH — this classifier decides *whether* to reach for that machinery at all; Stage 3 decides *how* to use it once reached for.
