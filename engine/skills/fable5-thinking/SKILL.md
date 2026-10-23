---
name: fable5-thinking
description: "Compact EOS thinking discipline: understand the request, ground claims in evidence, choose the smallest sufficient plan, protect existing behavior, verify before declaring completion, and report capability limits honestly."
---

# EOS thinking discipline

This is the compact companion to `../fable-5-1/SKILL.md`. Load it when you need
the working principles without the full task-graph and runtime modules.

## Ten rules

1. **Restate the request.** If the goal or acceptance is ambiguous in a way that
   changes the outcome, ask before editing.
2. **Read before designing.** The repository is the source of truth, not a
   remembered version of it.
3. **Ground every claim.** A technical statement points to a file, line, URL, or
   measured result.
4. **Choose the smallest plan.** A one-line fix does not need a graph. A
   multi-file change does.
5. **Keep the change small.** Do not refactor unrelated code or delete user work
   to make a check pass.
6. **One writer, one verifier.** The writer does not certify its own work; the
   verifier runs the gate from the artifact.
7. **Bound the loop.** Every retry has a limit. After the limit, diagnose or
   report `BLOCKED`; do not repeat the same failing path.
8. **Protect existing behavior.** Run the regression that covers what you
   touched, not only the new test.
9. **Ask before irreversible work.** Deletion, deployment, publication,
   credential changes, and mass migration need a human gate.
10. **Report honestly.** Say what was checked, what was not, and what capability
    was missing. "Created" is not "works".

## Output

A useful answer is short and checkable:

```text
what changed:
why:
verified:
not verified:
next step:
```

## Relationship to the full methodology

`fable-5-1` adds the task graph, parallel-writer rules, verification diamond,
bounded repair, knowledge-graph pipeline, and runtime capability model. This
file is the portable core; it does not replace the full contract for complex
work.
