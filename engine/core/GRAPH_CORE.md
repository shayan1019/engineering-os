# GRAPH_CORE — task graphs and conditional knowledge graphs

This file is the cross-runtime graph contract for EOS. It works with
`FABLE_CORE.md`. The canonical, progressive-disclosure version is the
`fable-5-1` skill's `graph/` directory.

There are two graphs. They are not the same thing.

## Part 1 — Task graph (mandatory)

Every task, including a small one, is represented as a short graph of steps.

### Build the graph

1. **List the real steps.** Separate what must happen from what merely could
   happen.
2. **Draw true dependencies.** If B can only start after A finishes, B depends
   on A. If B and C touch the same state or file, they are not independent.
3. **Mark the owner.** Each file has one writer. The merge owner is named.
4. **Mark the verifier.** The verifier is a different role from the writer.
5. **Mark the evidence.** Every terminal node says which command or artifact
   proves it.
6. **Mark the limits.** Each repair loop has a maximum attempt count and a stop
   condition.

### Parallelism

Parallel fan-out is allowed only when all of the following are true:

- the nodes do not write the same file;
- the nodes do not share mutable state;
- the merge order is defined;
- the combined result can be verified as one change.

Otherwise, run sequentially. "It feels faster" is not a dependency proof.

### Bounded repair

A failed node may be repaired, but the graph records:

- the failure;
- the hypothesis for the cause;
- the smallest change that tests the hypothesis;
- the attempt count;
- what happens when the count is exhausted.

After the limit, stop and report. Do not restart the whole graph as a way to
avoid the diagnosis.

### Stop rule

The graph stops when one of these is true:

- the acceptance checks pass and the evidence is recorded;
- a human gate is required;
- a dependency is unavailable and is reported as `BLOCKED`;
- the repair budget is exhausted.

## Part 2 — Knowledge graph (conditional)

A knowledge graph is not required for ordinary code changes. Trigger it only
when at least one of these is true:

- the task is explicitly about an ontology, entity graph, GraphRAG, or
  relationship extraction;
- the answer requires multi-hop relationships that a flat document search
  cannot express;
- the same entities must be deduplicated across sources;
- a repository or domain graph must be queried as a first-class artifact;
- the user asks for a teaching explanation with a graph structure.

When triggered, use a small, evidence-first pipeline:

1. **Scope and schema.** Define the entity types, relation types, and the
   question the graph must answer. Reject types that do not serve the question.
2. **Extraction.** Extract only from sources you can name and cite. Keep the
   source span for every fact.
3. **Normalization.** Canonicalize names, dates, units, and identifiers. Record
   the transformation.
4. **Entity resolution.** Merge duplicates with an explicit rule and a confidence
   level. Keep an audit trail; never silently collapse two entities.
5. **Relations.** Add an edge only when the source supports it. Mark inferred
   edges as inferred, not as observed.
6. **Validation and serving.** Run completeness, consistency, and query tests.
   State the graph's coverage and its known gaps.

A graph without provenance is a diagram, not evidence.

## Part 3 — Repository graph

When a repository graph is available, treat it as a derived cache:

- record the commit and the generation command;
- report staleness instead of pretending the graph is current;
- fall back to direct file search when the provider is absent;
- never let a graph adapter edit the repository.

## Teaching mode

When the user asks *why*, *explain*, or *diagram*, explain the graph in layers:
the question, the entities, the edges, the evidence, and the uncertainty. Do not
present an inferred edge as a fact.
