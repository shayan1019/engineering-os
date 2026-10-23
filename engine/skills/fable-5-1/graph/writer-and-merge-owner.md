# Writer and merge owner

Two roles are easy to confuse and expensive to conflate.

## Writer

- Owns the content of a file for the duration of a task.
- Makes the edits, runs the local checks, and reports the result.
- Does not declare the final PASS; that belongs to the verifier.
- Does not write a file owned by another active writer.

## Merge owner

- Owns the combined result when work ran in parallel.
- Resolves conflicts and applies patches in a defined order.
- Confirms that the combined tree is coherent, not just that each branch passed
  in isolation.
- Is named before the parallel work starts.

A worker is not the merge owner unless explicitly assigned. "Someone else
touched it" is not an ownership transfer.

## Handoff

When work moves between roles, state:

```text
from:
to:
artifact:          path or commit
acceptance:        what the next role must check
open questions:
```

A handoff transfers ownership explicitly. A manager that delegates a node but
keeps the final task keeps ownership of the final result; an agent-as-tool
worker does not become the owner just because it produced output.

## Conflict rule

- One file, one writer at a time.
- If two writers need the same file, serialize them or split the file by a
  stable boundary first.
- The merge owner resolves the combined result; the original writers do not
  merge their own branches into each other.

## Verification

The writer may run tests while developing. The verifier runs the gate from the
artifact in a separate context. A green local run is useful evidence for the
writer, not the final verdict.
