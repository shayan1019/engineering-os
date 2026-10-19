# Repo Graph Contract (Graphify provider)

*New synthesis (Graphify integration, Faz 3) — not a distillation of
`fable5-thinking`/`graph-engineering`. Defines the authority boundary between
Fable methodology, the task graph, and the repo-structure graph provided by
the optional Graphify provider.*

> **Authority boundary**: this file classifies what the repo graph *is*. It
> does not schedule work (`graph/task-graph.md`), does not set scope or
> completion semantics (`core/*.md`), and does not verify results
> (`graph/verification-diamond.md`, `evidence/*`). Precedence, per
> `skill.contract.yaml`: `fable_methodology > graph_scheduling_preference >
> runtime_optimization > repo_graph_advice`. The repo graph gives evidence;
> it never gives orders.

## The three questions (contract sentences)

- `GRAPH_CORE`: "Ne yapacağız ve hangi sırayla?" — owns dependency shape,
  sequencing, parallelism, writer/merge ownership, bounded repair.
- `GRAPHIFY`: "Repo gerçekte nasıl bağlı?" — owns symbols, calls, imports,
  communities, path/query answers over `graph.json`.
- `FABLE`: "Değişiklik yapılmalı mı, kapsam ne, hangi davranışlar korunmalı
  ve ne zaman DONE?" — owns scope, do-not-regress, risk gates, completion.
- `Verifier`: "Yapılan gerçekten doğru mu?" — owns the independent PASS
  verdict; never the same context as the writer.

## What the repo graph is not

- **Not the task graph.** Nodes here are code symbols/files, not jobs. A
  `calls` edge never implies a task dependency; a community never implies a
  parallel work split. Converting repo structure into a task DAG is
  `graph/task-graph.md`'s job, using the repo graph as one input among others.
- **Not the knowledge graph.** `graph/knowledge-graph-trigger.md`'s 9-stage
  pipeline (ontology, entity/relation extraction, provenance) is a different
  machine with different triggers. A repo-graph query never counts as KG
  work and never satisfies an EVIDENCE_GRAPH trigger by itself.
- **Not policy, not scheduler, not verifier.** The graph cannot approve a
  risky mutation, cannot assign writers, cannot declare PASS.

## Edge-confidence discipline (Fable evidence mapping)

Graphify tags every edge `EXTRACTED` (explicit in source), `INFERRED`
(resolved by graphify), or `AMBIGUOUS`. Mapping onto Fable evidence:

- `EXTRACTED` edges are citable evidence (`source_location` quoted).
- `INFERRED` edges are leads, not proof: they may scope a search and size a
  blast radius, but **an INFERRED-only chain cannot alone authorize a
  `verify_required`/`approval_required` mutation** — the underlying source
  must be read and cited.
- `AMBIGUOUS` edges are treated as absent until resolved by reading source.

## Language-coverage honesty

- Python / SQL / config / docs indexed by the provider's parser: structured
  evidence.
- Any language without a verified parser (notably MQL5 `.mq5`/`.mqh`, absent
  from the provider's supported-extension list and empirically 0 nodes in the
  BigHero pilot graph): **explicit partial coverage**. Fallback is LSP +
  textual dependency analysis + targeted search. Never claim "repo graph
  complete" when an unsupported language is in scope.

## Integration posture (pilot-frozen)

- Provider is per-project (`<project>/.agents/skills/graphify/`), optional,
  and absent-safe: the whole Fable system works unchanged without it.
- The provider's own `AGENTS.md` always-on injection and strict hooks are
  **OFF** in the pilot. Fable's classifier decides when the graph is
  consulted; the provider never dictates agent behavior.
- The provider's skill files are reference material, not authority. On any
  conflict between provider guidance and Fable routing, Fable wins
  (see `.agents/registry/COMPATIBILITY.json` literal-translation rules).
