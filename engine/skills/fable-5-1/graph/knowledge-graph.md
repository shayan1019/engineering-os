# Knowledge graph

A knowledge graph is a conditional tool, not a default. Use it only when the
question depends on relationships that a flat document search cannot express.

## Triggers

Build or query a knowledge graph when at least one is true:

- the user asks for an ontology, entity graph, GraphRAG, or relationship
  extraction;
- the answer needs multi-hop facts (A relates to B, B relates to C);
- the same entity appears under several names across sources and must be
  resolved;
- a repository or domain graph is a deliverable, not a side effect;
- the user asks for a teaching explanation that is clearer as a graph.

Do not build one for a normal code change, a single-file bug, or a task whose
answer is a direct lookup.

## Pipeline

1. **Question and scope.** Write the question the graph must answer. Define the
   entity and relation types that the question needs. Drop everything else.
2. **Source inventory.** List the sources, their locations, and their access
   limits. A source you cannot cite does not enter the graph.
3. **Extraction.** Extract entities, relations, and events with the source span.
   Keep the original wording next to the normalized form.
4. **Normalization.** Canonicalize names, dates, units, and identifiers. Record
   every transformation.
5. **Entity resolution.** Merge duplicates with an explicit rule and a
   confidence. Keep a reversible audit trail. Never collapse two entities
   silently.
6. **Relation validation.** An observed edge needs a source. An inferred edge is
   labeled `inferred`, with the rule and confidence that produced it.
7. **Validation.** Run coverage, consistency, and query tests. Report the gaps.
8. **Serving.** Return the graph together with its schema, commit/source
   snapshot, coverage, and confidence legend.

## Evidence rules

- Every node and edge has a source or an explicit `inferred` label.
- A graph result is retrieval evidence. It is not permission to mutate a
  high-risk system, and it does not replace the quality gate.
- Stale input means a stale graph. Regenerate or mark it `STALE`; never present
  it as current.
- A graph with no coverage statement is a diagram, not a knowledge artifact.

## Output shape

A useful handoff contains:

```text
question:
sources:
schema: entity types, relation types
coverage: full | partial (with the missing area)
confidence: observed | inferred | ambiguous
artifacts: graph file, query log, validation report
known gaps:
```
