---
name: graph-engineering
description: "Design task graphs for agent work and knowledge graphs for relationship-heavy data. Covers dependency edges, parallel fan-out, separate verifier contexts, the stop rule, human gates, ontology design, entity/relation/event extraction, entity resolution, provenance, GraphRAG serving, and a teaching mode with worked diagrams."
---

# Graph engineering

Graph engineering is the discipline of designing the structures work moves
through. It has two halves:

1. **Task graphs** — how agents work: jobs, dependencies, parallel branches,
   separate verification, stop rules, human gates.
2. **Knowledge graphs** — what the work remembers: entities, relations, events,
   provenance, confidence, and queries.

The two are related but not interchangeable. A code symbol graph is not a task
graph. A task dependency is not a knowledge-graph edge.

## Task graphs

Use a task graph when work has real independent branches, multiple writers, or
a verification step that must be separate from production. See
[references/task-graphs.md](references/task-graphs.md).

Core rules:

- A node is a job one agent can own, not a file.
- Draw an edge only when the next job needs the previous result.
- Parallel branches require disjoint write sets and a named merge owner.
- The writer is not the verifier.
- Every repair loop has a budget and a stop condition.
- Irreversible actions require a human gate.

## Knowledge graphs

Use a knowledge graph when the question is relationship-heavy, entities recur
across sources, or provenance is part of the answer. See
[references/knowledge-graphs.md](references/knowledge-graphs.md).

Core rules:

- Model the domain before extracting.
- Every node and edge has a source and a confidence label.
- Resolve duplicates with an explicit, reversible rule.
- Fix extraction precision before fusion; bad precision poisons the graph.
- A graph without coverage and provenance is a diagram, not evidence.

## Teaching mode

When the user wants to learn, do not just execute:

1. Ask for one real project or dataset and use it as the running example.
2. Teach in pipeline order, one stage per exchange.
3. Produce small diagrams: the pipeline, a 3-type ontology from the user's
   domain, a 5–10 node extracted subgraph, and the diamond pattern using the
   user's own jobs.
4. End with a starter `ontology.yaml` and one query the user can run.

## Relationship to EOS

The `fable-5-1` skill embeds these ideas in its `graph/` modules and adds the
scope, evidence, runtime, and policy layers. This skill is the standalone primer
for a request whose primary deliverable is a graph design or a lesson.
