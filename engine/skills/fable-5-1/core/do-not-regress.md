# Do not regress

A change is not complete when the new path works. It is complete when the new
path works and the protected behavior still works.

## Before the change

1. Identify the behavior that must not change.
2. Find the test, command, or artifact that proves it.
3. Record the baseline: commit, command, result, and date.
4. If no test exists, add the smallest one that would catch a regression.

## During the change

- Keep the regression check runnable. Do not disable, skip, or weaken it.
- If the check must change because the contract changed, say so explicitly and
  update the acceptance record.
- If a fix breaks another check, revert the fix first. Then diagnose the
  interaction; do not stack a second workaround.

## After the change

- Run the protected check again.
- Compare the result with the baseline.
- Record the result in the completion report.
- If the check cannot run, the state is `PARTIAL` or `BLOCKED`, not `DONE`.

## Error ownership

When a command fails, the failure belongs to the change until proven otherwise.
Read the error, locate the failing assumption, and make the smallest correction.
Do not blame the environment before checking the command, the path, and the
input.

## Rollback

Every non-trivial change needs a way back: a commit, a backup, a versioned
install, or a reversible migration. If rollback is impossible, the action is
irreversible and needs a human gate.
