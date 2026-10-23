# Memory discipline

Memory is for recoverable context, not for secrets or unverified claims.

## What to keep

- The task's scope, acceptance checks, and current state.
- Decisions with their reason and the alternative that was rejected.
- File paths, commands, and evidence that are expensive to rediscover.
- Known gaps and the next concrete step.

## What not to keep

- Passwords, tokens, private keys, session cookies, or personal data.
- A guess presented as a fact.
- A stale result without its timestamp and scope.
- Large copied source files; keep a path, a symbol, and a line range instead.

## Recording a memory

Use a stable, human-readable form:

```text
topic:
claim:
evidence: path:line | command output | URL
confidence: observed | inferred | unverified
date:
next step:
```

A memory that cannot be checked is a note, not evidence.

## Using a memory

- Re-read the source before acting on a claim that could have changed.
- Treat a memory older than the code as a lead, not as current truth.
- If a memory conflicts with the repository, the repository wins.
- When a memory is wrong, correct or delete it. Do not keep both versions.

## Context efficiency

Load the smallest context that can answer the question. Prefer a symbol range
over a full file, and a targeted search over a broad scan. Expand only when the
evidence is genuinely missing.
