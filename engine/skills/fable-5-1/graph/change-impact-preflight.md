# Change-impact preflight

Before a multi-file or high-risk change, spend a few minutes mapping the blast
radius. The goal is not a complete graph; it is to avoid discovering a dependency
after the edit.

## Steps

1. **Name the change.** One sentence: what behavior or contract changes.
2. **Find the direct callers.** Search for the symbol, route, config key, or
   file name. Start with exact matches, then broaden.
3. **Find the indirect consumers.** Tests, scripts, installers, docs, generated
   artifacts, and external integrations that depend on the same contract.
4. **Map the data.** What input shape, output shape, persisted state, or
   migration is affected?
5. **Select verification.** The smallest targeted test first, then the
   regression suite that covers the touched boundary.
6. **Write the preflight.** List files, consumers, tests, and unknowns.

## When a repository graph is available

Use it to find candidates faster, but read the source before acting on an edge:

- an `EXTRACTED` edge is a citation;
- an `INFERRED` edge is a lead;
- an `AMBIGUOUS` edge is not evidence.

A graph query never authorizes a mutation by itself. It narrows the search.

## Output

```text
change:
direct callers:
indirect consumers:
data/state impact:
targeted tests:
regression suite:
unknowns:
risk: auto | verify_required | approval_required
```

If the preflight finds a consumer that cannot be verified, say so before the
change. A known unknown is safer than a silent assumption.
