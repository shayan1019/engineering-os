# Verification diamond

The verification diamond separates the context that produces work from the
context that judges it.

```text
        -> produce A -> verify A -
start --                          -> compare -> verdict
        -> produce B -> verify B -
```

The two produce branches are optional; the separation is not. A single writer
grading its own output in the same context misses its own mistakes.

## Rules

1. **Separate context.** The verifier is a different role, session, or agent
   from the writer.
2. **Artifact first.** The verifier reads the artifact (diff, file, gate result)
   before reading the writer's explanation. The explanation can anchor the
   judgment.
3. **Independent check.** The verifier runs the acceptance check, not a copy of
   the writer's command transcript. If the environment cannot run it, the result
   is `BLOCKED`.
4. **No silent repair.** The verifier reports; it does not edit the artifact and
   then certify its own edit.
5. **Recorded verdict.** PASS, FAIL, BLOCKED, or INSUFFICIENT_EVIDENCE, with the
   command and the observed result.

## When two branches exist

Two independent attempts can be compared when the task is uncertain or the cost
of a wrong answer is high. The comparison is against the acceptance checks, not
against which answer sounds better.

## What the diamond does not do

- It does not replace the quality gate.
- It does not make a risky change safe.
- It does not prove the absence of a defect; it proves the checks that ran.
- It does not let the verifier widen the scope. A new concern is reported to the
  owner.
