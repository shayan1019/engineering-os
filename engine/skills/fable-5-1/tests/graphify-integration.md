# Repository graph integration tests

These are the checks for the optional repository-graph adapter. They are
provider-agnostic: replace "provider" with the graph tool you are testing.

## Setup

- A small fixture repository with known symbols and calls.
- A provider installed in the project (not globally).
- A recorded commit and working-tree fingerprint.

## Cases

| Case | Expected |
| --- | --- |
| Provider absent | Normal search works; report `ADAPTER_ONLY` or `UNAVAILABLE`, never fail the task |
| Provider install | Only the project-local provider directory changes; host instruction files are byte-identical |
| Graph build | Metadata records commit, fingerprint, file count, languages, build time, coverage |
| `EXTRACTED` edge | Query returns a source location that matches the file |
| `INFERRED` edge | Labeled inferred; cannot alone authorize a high-risk mutation |
| `AMBIGUOUS` edge | Treated as absent until source confirms it |
| Stale graph | Freshness check returns `STALE`; preflight falls back or rebuilds |
| Unsupported language | Coverage is `PARTIAL`; no full-coverage claim |
| Provider crash | Task falls back to direct search; error is recorded |
| Rollback | Removing the provider directory and graph output restores the original tree |

## Evidence to record

```text
provider:
version:
commit:
working_tree_fingerprint:
files_indexed:
languages:
coverage: full | partial
build_seconds:
query:
result:
source_verified: yes | no
```

## Boundaries

- A graph result is retrieval evidence, not a gate.
- The provider must not edit `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or other host
  instruction files.
- The provider must not write outside its project-local graph directory.
- A stale or partial graph is reported as such; it is never silently treated as
  current and complete.
