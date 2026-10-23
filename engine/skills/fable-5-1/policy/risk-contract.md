# Risk contract

Risk classification decides how much independent evidence and human approval a
task needs. It is not a substitute for judgment, and it is not a sandbox.

## Levels

| Router level | Gate | Meaning |
| --- | --- | --- |
| `low` | `auto` | Normal verification is sufficient. |
| `medium` | `verify_required` | An independent verifier and evidence are required before PASS. |
| `high` | `approval_required` | Do not cross an irreversible step without explicit human approval. |

There is no `deny` tier in the router. A high-risk label asks a human to decide;
it is not a mechanical refusal. If a task must be refused outright, a human or
the host's own permission layer must do it.

## Classification strength

The router uses keyword heuristics, not semantic risk analysis. A task can be
risky without matching a keyword, and can match a keyword while being harmless.
Treat the label as a prompt to look, not as a complete assessment.

## Enforcement strength

The label is a checked convention inside EOS. It is enforced by the verifier, the
gate workflow, and the release process, not by the model's goodwill. A host that
ignores EOS can ignore the label; that is a host boundary, not a claim of
non-bypassable enforcement.

## Known bypass paths

- The model can read the label and proceed anyway.
- Risk discovered mid-task is not retroactively classified.
- Keyword absence does not mean low risk.
- A host with broad permissions can act outside EOS.

## Wiring

`route_task.py` returns `risk.policy` with the gate, action, classification
strength, enforcement strength, and known bypass paths. The field is additive;
the existing `resolved`, `domains`, `kg_required`, `skills`, and `delegation`
outputs are unchanged.
