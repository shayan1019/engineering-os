# Knowledge Graph Trigger

*Native Fable 5.1 restatement of `graph-engineering/SKILL.md`'s 9-stage pipeline and working rules — checklist items B1.1-B1.13 — plus the conditional ON/OFF trigger list also present in `.agents/core/GRAPH_CORE.md` §2 (restated natively here, `GRAPH_CORE.md` itself not modified in this stage). Wording reorganized; behavior unchanged.*

> **Authority boundary**: see `graph/task-graph.md`. This file is never loaded by default — see the trigger section below and `core/task-complexity-classifier.md`'s EVIDENCE_GRAPH level, which is the gate that decides whether this file's pipeline runs at all.

## This is conditional, not default behavior

**A normal multi-file coding task must not automatically become a knowledge-graph or evidence-graph task.** The 9-stage pipeline below runs only when the task genuinely needs it — see triggers below. This mirrors `GRAPH_CORE.md` §2's existing ON/OFF split; nothing here loosens that boundary.

### Triggers (ON)
- Ontology/schema design, entity-relation extraction
- Building a knowledge graph, GraphRAG, graph memory, entity-graph setup
- Structured memory/memory-graph architecture
- Explicit user request ("extract a graph / build an ontology / do KG")
- Multi-source evidence lineage, provenance, claim→evidence relationships, multi-hop reasoning, impact analysis, or large artifact/document relationship analysis that a table genuinely can't represent (EVIDENCE_GRAPH level specifically)

### Stays OFF (task graph alone is sufficient)
- Bug fixes, typos, UI/test changes, single-file edits
- Running commands, formatting data, deploying, config changes
- Research/report writing, unless the report itself requires KG-level relationship analysis

### When in doubt
Ask: "does this work actually produce an entity-relation graph, or need claim-level evidence lineage across multiple sources?" If no, stay at GRAPH or below — don't reach for this file.

## The 9-stage pipeline (from source, B1.1-B1.9)

1. **Scope & value test** — confirm a graph beats a simpler structure (multi-hop queries, recurring entities, or relationships-as-data); otherwise use a table and stop.
2. **Knowledge representation choice** — property graph / RDF / typed JSON-SQLite edges; decide time+provenance attachment now.
3. **Ontology modeling** *before* extraction — 5-15 entity types, 10-30 relation types; precise verb-named relations; merge always-co-queried types. Never skip.
4. **Entity extraction (NER)** — rules/dictionaries for closed vocab, LLM+ontology for open text; always span+source pointer.
5. **Relation extraction** — constrain to the ontology's relation list with domain/range checks; reject type-incompatible edges.
6. **Event extraction** — for dynamic domains, events as first-class nodes (trigger + typed args + time).
7. **Quality gate** before fusion — sample+score entity/relation precision, target ≥90% on a 50-item sample; fix the prompt/rules, not the output.
8. **Knowledge fusion** — blocking+matching+merge policy for duplicate entities across sources. Never skip.
9. **Serve to LLMs** — GraphRAG retrieval, graph-as-memory, LLM-as-reasoner over paths.

## Working rules (from source, B1.10-B1.13)

- Schema first, always — extraction without an ontology produces a word cloud with arrows, not a graph.
- Provenance (source, extracted_at, confidence) on every node/edge, non-negotiable.
- Incremental over big-bang — pilot a small sample through all 9 stages before scaling.
- The LLM is stage machinery for stages 4-6 only, not the whole pipeline — schema, validation, and fusion are what make the output a knowledge graph.

## Full grounding

Deeper worked examples and per-stage detail: [../references/curriculum.md](../references/curriculum.md), [../references/modeling.md](../references/modeling.md), [../references/extraction.md](../references/extraction.md), [../references/fusion-and-llm.md](../references/fusion-and-llm.md).
