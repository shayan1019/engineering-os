# Scope contract

Before changing a file, state the contract in plain language. A short task can
state it in one sentence; a multi-file task writes it down.

## The five fields

1. **Request** — what the user asked for, in the user's terms.
2. **In scope** — the files, behavior, and artifacts this change may touch.
3. **Out of scope** — adjacent work that must not be pulled in.
4. **Acceptance** — the observable checks that make the change correct.
5. **Do not regress** — existing behavior that must still work.

If a field cannot be filled without guessing, ask. A wrong assumption is more
expensive than a short question.

## Change discipline

- Read the relevant files before editing. Do not design against a remembered
  version of the code.
- Prefer the smallest change that satisfies the acceptance checks.
- Do not rename public contracts, reformat unrelated files, or delete user work
  to make a test pass.
- If the task grows beyond the stated scope, stop and renegotiate the scope.
- If the request is irreversible (delete, deploy, publish, credential or payment
  action), require human approval before acting.

## Output shape

A scope statement is not a plan. It is the boundary the plan must respect:

```text
request:
in scope:
out of scope:
acceptance:
do not regress:
risk: auto | verify_required | approval_required
```

The plan and the task graph may change; the scope changes only with an explicit
decision.
