# Execution discipline

The default loop is **understand → plan → act → verify → report**. Skipping a
step is allowed only when the task is genuinely trivial, and the reason is
stated.

## Understand

- Restate the request and the acceptance checks.
- Inspect the repository before proposing a design.
- For an unfamiliar API, framework, or platform, read the official source. Do
  not answer from memory when the answer is checkable.
- Separate facts (observed) from assumptions (not yet checked).

## Plan

- Choose the execution depth in `task-complexity-classifier.md`.
- For DIRECT work, keep a short mental plan.
- For PLANNED work or higher, write the steps, dependencies, owners, and
  verification.
- Identify the smallest test that could disprove the plan.

## Act

- Make one coherent change at a time.
- Keep the working tree understandable. Do not mix a refactor with a behavior
  change unless the task requires both.
- When a command fails, read the error before changing code. Do not retry the
  same command blindly.
- After three failed attempts at the same fix, stop and diagnose the assumption
  that is wrong.

## Verify

- Run the acceptance check, not a proxy for it.
- Re-run the regression that covers the behavior you touched.
- Record the command and the result.
- If the check cannot run, report `BLOCKED`; do not call it a pass.

## Report

State what changed, what was verified, what was not verified, and what the next
step is. Keep the report proportional to the change.
