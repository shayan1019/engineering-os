# Evidence Contract


> **Authority boundary**: this file defines what evidence *looks like*. `evidence/verification.md` defines what counts as *enough* of it to pass. `core/completion-model.md` defines *when* enough verification exists to call something done. Keep these three questions separate.

## Fields, when evidence is recorded formally

- **claim** — the specific statement being backed (not "it works," but the exact thing asserted).
- **source/artifact** — the file, command, or object the claim is about.
- **verification command/check** — what was actually run to check the claim.
- **result** — the actual output/observation.
- **exit/result status** — pass/fail/exit-code, whatever the check's own status signal is.
- **evidence reference** — where this is recorded (a file:line, a command transcript, a hash) so it can be re-checked later.
- **verifier** — who/what performed the check (per `evidence/verification.md`'s writer≠verifier invariant, not necessarily the same actor as whoever made the claim).
- **verification state** — `verified` / `unverified` / `blocked` (see below).

## Verification states

- **verified** — the check ran, and the result actually supports the claim.
- **unverified** — no check has been run yet, or the check that was run doesn't actually cover the claim (a common gap: the command ran, but it didn't test the specific thing being claimed).
- **blocked** — a check was attempted and could not be completed (missing dependency, denied access, environment limitation) — distinct from `unverified`, since a `blocked` claim is one where a check was tried and failed to resolve either way, not simply skipped.

## Depth is risk/task dependent — not a heavy default

**Do not require every trivial factual statement to produce a full evidence record above.** `core/execution-discipline.md`'s A13 — a location/evidence line per claim — remains the default, lightweight discipline for ordinary work. The structured fields above apply when the stakes justify it: `policy/risk-contract.md`'s `verify_required`/`approval_required` gates, `graph/verification-diamond.md`'s independent-verifier nodes, or `evidence/provenance.md`'s conditional triggers. A one-line edit doesn't need an evidence record; a claim gating an `approval_required` decision does.
