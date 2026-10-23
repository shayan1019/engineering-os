# Provenance


> **Conditional, not default.** Per `core/task-complexity-classifier.md`, EVIDENCE_GRAPH is never the default execution depth, and `skill.contract.yaml`'s `execution_depth_model.default` is mechanically checked to never equal `evidence_graph` (`validate_skills.py`'s `check_contract()`). Normal low-risk coding work never reaches this file.

## When deeper provenance is actually warranted

Per `graph/knowledge-graph-trigger.md`'s trigger list, deeper provenance — explicit claim→evidence→source chains, not just a single evidence line — applies to:

- multi-source research, where claims need to be traceable to which source said what
- load-bearing factual claims — a decision genuinely rests on this being true, not incidental color
- EVIDENCE_GRAPH-classified work specifically
- decision lineage — why a choice was made, not just what was chosen
- high-risk changes (`policy/risk-contract.md`'s `approval_required` gate)
- impact analysis — what else does this claim's truth affect
- durable audit requirements — someone other than this session needs to reconstruct the reasoning later

## What ordinary work uses instead

Normal, low-risk coding work uses `core/execution-discipline.md`'s lightweight evidence-per-claim habit (a location per claim — A13) and `evidence/evidence-contract.md`'s command/test-result fields where a claim is checked formally. That's sufficient for the overwhelming majority of tasks. This file's deeper chains are the exception, triggered explicitly — never assumed necessary "to be safe."

## Shape, when it is warranted

A provenance record, when actually needed, links: the source (what was read/queried), the evidence extracted from it (`evidence/evidence-contract.md`'s fields), the claim(s) that evidence supports, and — for EVIDENCE_GRAPH-mode decisions — the decision the claim fed into. This deliberately does not prescribe a specific storage format (RDF, a JSON file, a table) — this repository has no existing provenance-storage convention to reuse, and inventing one speculatively here would be exactly the kind of unwarranted infrastructure this package's non-goals reject. When a task genuinely needs this, the concrete storage shape is a decision for that task, not a standing architecture choice.
