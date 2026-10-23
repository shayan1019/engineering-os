# Knowledge graphs

A knowledge graph is a product with a schema, not a pile of triples. The pipeline
order matters more than the extraction model.

## When a graph is worth it

Use a graph when:

- queries are multi-hop;
- entities recur across sources and must be resolved;
- relationships are the data (not just metadata);
- provenance and time are part of the answer.

Do not use a graph for a single-hop lookup. A table is cheaper and clearer.

## Pipeline

1. **Scope and value test.** Name the question the graph must answer. If a
   table or a full-text search answers it, stop.
2. **Representation.** Choose a property graph, RDF, or typed edges in a plain
   store. Decide how time and provenance attach to every fact.
3. **Ontology.** Define entity types, relation types with domain/range, and
   attributes before extraction. Start with 5–15 entity types and 10–30
   relation types. Use precise verbs (`ACQUIRED`, not `RELATED_TO`).
4. **Entity extraction.** Start with rules/dictionaries for closed
   vocabularies; use a model with the ontology in the prompt for open text.
   Keep the source span.
5. **Relation extraction.** Constrain the model to the ontology. Reject edges
   whose endpoints have incompatible types.
6. **Event extraction.** For dynamic domains, model events as nodes with a
   trigger, typed arguments, and time.
7. **Quality gate.** Sample and score entity and relation precision. Fix the
   extraction rule, not the output. Do not fuse a low-precision batch.
8. **Entity resolution and fusion.** Merge duplicates with an explicit rule and
   a confidence. Keep a reversible audit trail. Never collapse two entities
   silently.
9. **Validation and serving.** Run completeness, consistency, and query tests.
   Report coverage, confidence, and known gaps.

## Provenance

Every node and edge carries:

```text
source:
span:
observed | inferred:
confidence:
date:
```

An inferred edge is labeled. An edge without a source is not evidence.

## Output

```text
question:
schema:
sources:
coverage: full | partial
confidence legend:
artifacts: graph, query log, validation report
known gaps:
```

## Teaching mode

Teach the pipeline in the user's domain, one stage per exchange, with a small
diagram at each stage. End with an `ontology.yaml`, a 5–10 node example graph,
and one query the user can run.
