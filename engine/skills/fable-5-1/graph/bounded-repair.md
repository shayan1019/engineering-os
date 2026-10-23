# Bounded repair

A repair loop is allowed. An unbounded loop is not.

## Before the loop

Record:

- the failure (command, output, artifact);
- the current hypothesis for the cause;
- the smallest change that will test the hypothesis;
- the maximum number of attempts;
- the fallback when the budget is exhausted.

## Attempt accounting

| Attempt | What changed | Result | Next |
| --- | --- | --- | --- |
| 1 |  |  |  |
| 2 |  |  |  |
| 3 |  |  |  |

Each attempt must change the hypothesis or the evidence. Re-running the same
command with the same input is not a new attempt.

## Non-progress signals

Stop and diagnose when:

- the same error appears with no new information;
- the fix moves the failure without reducing it;
- the change grows beyond the original scope;
- the test itself is being weakened to pass;
- the environment is missing a required capability.

## Escalation

When the budget is exhausted:

1. State what was tried and what was observed.
2. State the most likely remaining cause and the evidence for it.
3. Mark the task `BLOCKED` or `PARTIAL`.
4. Ask for the missing decision, capability, or access.

Do not restart the entire graph as a way to avoid the diagnosis. Do not hide a
failed attempt by deleting the test or reverting the acceptance check.

## Retry vs repair

- **Retry** is for a transient failure (network, lock, flaky scheduler).
- **Repair** is for a reproducible defect.
- **Replan** is for a wrong approach.
- **Block** is for a missing capability or decision.

Name which one you are doing; they have different budgets.
