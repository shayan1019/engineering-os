# Completion model

Completion is a claim about evidence, not a feeling about the edit.

## States

| State | Meaning |
| --- | --- |
| `DONE` | Every acceptance check ran and passed; the evidence is recorded |
| `PARTIAL` | Some checks ran; a named part is incomplete or unverified |
| `BLOCKED` | A required capability, dependency, or decision is unavailable |
| `INSUFFICIENT_EVIDENCE` | The claim may be true, but the evidence does not establish it |
| `FAIL` | A check ran and did not pass |

Do not use `DONE` when a check was skipped, a package was not built, or a test
was not run because the environment was missing. Those are `PARTIAL` or
`BLOCKED`.

## Evidence for a completion claim

- the exact command or observation;
- the observed output or artifact;
- the commit or file state it applies to;
- the environment, if the result is environment-dependent;
- the known gaps.

## What each state does not mean

- A passing unit test does not prove the integration works.
- A successful build does not prove the binary installs.
- A model's summary does not prove the files are correct.
- A clean secret scan does not prove there is no secret.
- A graph query does not prove the source code behaves as the graph suggests.

## Independent verification

For medium or high risk, a different role runs the gate. The writer may run
tests while developing; the writer's own green run is not the final verdict.
The verifier reports from the artifact, not from the writer's narrative.

## Stop condition

Stop when the acceptance checks pass and the evidence is recorded, or when a
human gate or a blocked dependency is reached. Do not keep working to make a
report look better.
