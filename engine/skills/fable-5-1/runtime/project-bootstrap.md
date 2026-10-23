# Project bootstrap

A repository graph is useful only when it is current and scoped to the project.
Bootstrap is lazy: detect the project, check the provider, and build only when a
task actually needs repository intelligence.

## Separation

```text
GLOBAL (control plane)
  methodology, discovery logic, capability detection

PROJECT-LOCAL (project root)
  optional provider skill
  graph index
  metadata and freshness state
  provider ignore file
```

Never install a graph provider globally, edit a project's host instruction files
from the provider, share a graph across projects, or rewrite a working setup
that the project already has.

## Lazy trigger

```text
open a project        -> detection only (cheap; no build)
first PLANNED task    -> check state:
                         MISSING -> build -> query
                         STALE   -> update -> query
                         CURRENT -> query
next session          -> CURRENT -> reuse
```

- `ATOMIC` tasks never install or build a graph.
- `DIRECT` tasks may use an existing current graph but do not build one.
- An absent or dead provider never blocks the task; fall back to direct search.

## Bootstrap sequence

1. Find the project root (nearest ancestor with a project marker, else cwd).
2. Detect supported and unsupported languages. Report unsupported coverage as
   `PARTIAL`.
3. Check whether a project-local provider and graph exist.
4. Check freshness against the commit and working-tree fingerprint.
5. Build or update only when the task needs it and the state requires it.
6. Report installed/scope/state/coverage as one JSON object.

## Rollback

The provider's footprint is project-local and reversible: remove its skill
directory and its graph output. The project's source and host instruction files
must be byte-identical before and after a provider install. If they are not,
stop and report it.
