# Repository graph contract

A repository graph is an optional, derived view of code structure. It can make
navigation faster. It cannot decide scope, schedule work, or certify a result.

## Authority boundary

| Layer | Owns |
| --- | --- |
| EOS methodology (`core/*`) | scope, acceptance, risk, completion |
| Task graph (`graph/task-graph.md`) | jobs, dependencies, owners, merge order |
| Repository graph | symbols, calls, imports, communities, source locations |
| Verifier | the independent PASS/FAIL verdict |

The repository graph is evidence. It is not policy, a scheduler, or a verifier.
An edge in the graph does not imply a task dependency. A community does not
imply a parallel work split. Converting structure into a task graph is the task
graph's job.

## Edge confidence

Label every edge:

- `EXTRACTED` — explicit in source; cite the source location.
- `INFERRED` — resolved by the provider; a lead, not proof.
- `AMBIGUOUS` — unresolved; treat as absent until source confirms it.

An inferred-only chain cannot authorize a high-risk mutation. Read the source
and cite it.

## Staleness

A graph is current only for the commit and working-tree fingerprint it was built
from. If the code moved, mark it `STALE` and rebuild or fall back to direct
search. Never present a stale graph as current.

## Coverage

- A language with a verified parser has structured coverage.
- A language without one (for example, a domain-specific EA language) is
  `PARTIAL`. Use textual search, language tooling, or a human answer; do not
  claim full coverage.
- Report the covered languages, file count, and build time.

## Integration posture

- The provider is optional and per-project.
- It must not modify `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or other host
  instruction files.
- It must not write outside its project-local graph directory.
- If the provider is absent, dead, or stale, the normal file-search flow works
  unchanged.
- Provider guidance never overrides EOS policy. If they conflict, EOS wins.
