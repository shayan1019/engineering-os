# Completion Model

*Native Fable 5.1 restatement of `fable5-thinking` §5 (checklist item A22), cross-referencing A13 (`core/execution-discipline.md`). Wording reorganized; behavior unchanged. Scope note below.*

## Plan → apply → verify must complete

For a hard/multi-step task: plan, apply, and verify are one unit of work. Don't stop after "here's the plan" and treat that as a checkpoint that ends the turn — a plan is not a deliverable unless the task was explicitly to produce a plan. (A22)

## What counts as evidence for a completion claim

A completion claim needs the same evidence-per-claim backing as any other claim (`core/execution-discipline.md`, A13): a command that was actually run and its actual output, a file that was actually read back, a hash that actually matched — not "should work" or "this looks right." "Created" is not "working." "I think" is not "verified."

## Completion states — added Stage 5, documented semantics not a coded state machine

For work worth tracking beyond a single claim (GRAPH-level or above, or anything gated by `policy/risk-contract.md`), completion moves through named states — stated here as documented distinctions this package's own reasoning should track, not a mechanical state-machine implementation this repository has no engine to run:

`PLANNED` → `RUNNING` → `VERIFYING` → `MERGE_PENDING` → `EVIDENCE_PENDING` → `DONE`

Terminal alternatives: `BLOCKED`, `FAILED`, `INCOMPLETE`.

**No `DENIED` state** — stated explicitly, not omitted by accident: per `policy/risk-contract.md`, this workspace's control plane has no policy source capable of producing a deny verdict, so there is nothing for a `DENIED` completion state to represent here. If a real deny-capable policy source is ever added to this workspace, this state list would gain one; it does not invent one speculatively now.

**`DONE` requires the applicable conditions from `evidence/verification.md`'s 5-question checklist to actually hold** — not a plan that intends to satisfy them, not a claim that they're satisfied. A model saying "done" is a claim; `DONE` is a state that requires the evidence behind that claim.

## Scope of this file vs. Stage 3 / Stage 5

This file governs what counts as adequate evidence for **this agent's own** claim that something is done — the Fable-side discipline, present in `fable5-thinking` today (`core/execution-discipline.md`'s A13, restated here for completion claims specifically). Independent verification in a separate context (`graph/verification-diamond.md`, Stage 3), the structured evidence shape and 5-question verification checklist (`evidence/evidence-contract.md`, `evidence/verification.md`, Stage 5), and the completion states above are the pieces that make a `DONE` claim actually checkable — each owned by its own file, cross-referenced rather than duplicated here.
