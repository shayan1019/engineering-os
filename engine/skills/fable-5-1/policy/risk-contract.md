# Risk Contract

*New synthesis (Stage 5). Reuses `.agents/router/route_task.py`'s existing `RISK_HIGH`/`RISK_LOW` keyword lists (lines 25-31) as the classification vocabulary — deliberately not a second, independently-drifting risk-keyword set. Wires a structured `risk.policy` field into that router's JSON output.*

> **Authority boundary**: this file classifies and interprets risk. It does not itself enforce anything. Actual enforcement, where this workspace has any at all, is outside the skill — a human reading `AGENTS.md` §10, or a validator reporting FAIL after the fact. Per `skill.contract.yaml`'s precedence: `external_control_plane_policy_authority > fable_methodology > ...` — this file sits at the Fable-methodology layer, interpreting what an external authority *would* require, not acting as that authority itself.

## The three-tier gate

| `route_task.py`'s `risk.level` | `risk.policy.gate` | Meaning |
|---|---|---|
| `low` | `auto` | Standard verification flow is sufficient. |
| `medium` (default, no keyword match) | `verify_required` | Don't declare PASS without independent verification/evidence (`evidence/verification.md`). |
| `high` (RISK_HIGH keyword match) | `approval_required` | Don't proceed past an irreversible/risky step without human approval (`AGENTS.md` §10). |

## No `deny`/`blocked` tier — stated explicitly, not manufactured

The research this package originated from proposed a `DENY` capability as part of a `HARD_RISK_KERNEL`. **This workspace's actual control plane cannot express that today** — `route_task.py` has no keyword list or mechanism that concludes "refuse this outright," and nothing downstream would enforce a refusal even if one were classified. Rather than invent a `deny` value that nothing backs, this contract documents its absence: the strongest classification this workspace can produce is `approval_required` — a label asking a human to decide, not a mechanism that itself says no.

## Classification strength, enforcement strength, known bypass paths — documented explicitly

- **classification_strength**: keyword-match heuristic against `RISK_HIGH`/`RISK_LOW` word lists — not semantic risk analysis. A task can be genuinely risky without matching a keyword, and can match a keyword without being genuinely risky.
- **enforcement_strength**: checked-convention-only. Nothing in this workspace's tooling can refuse a tool call based on this label — no CI, not a git repo, no process boundary that intercepts execution. The label is informational, read by the model as instructed (`AGENTS.md` §6 mandates running the router first).
- **known_bypass_paths**: (1) the model can read the label and act anyway — nothing stops it; (2) risk discovered mid-task, after the initial classification, isn't retroactively reflected; (3) keyword absence doesn't mean low risk — it means no keyword matched.

**A checked convention is never called non-bypassable enforcement in this package.** Every reference to `risk.policy` elsewhere in `fable-5-1` (e.g. `graph/parallelism.md`'s deferred network reference, `runtime/*`) inherits this same honesty constraint.

## Where this is wired

`route_task.py`'s JSON output gains `risk.policy = {gate, action, classification_strength, enforcement_strength, known_bypass_paths}`, additive only — `resolved`, `domains`, `kg_required`, `skills`, and `delegation` are unchanged. See `.agents/router/route_tests.py`'s extended regression cases for concrete expected values.
