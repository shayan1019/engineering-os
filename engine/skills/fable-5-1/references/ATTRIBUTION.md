# Attribution and provenance

The `fable-5-1` skill is original Engineering OS material, licensed under the
Apache License 2.0.

## What this package does not contain

- No vendor system prompt, transcript, or verbatim model instruction.
- No lecture notes, slides, or copied text from a university course.
- No source code copied from another agent framework.

The earlier private development line of this skill carried reference material
whose redistribution rights were not established. That material was removed
before the public release. The public package keeps only the behavior that EOS
authors can license: scope control, task-graph orchestration, single-writer
rules, bounded repair, evidence, and runtime capability honesty.

## Ideas and citations

The methodology uses general, long-standing engineering patterns:

- directed acyclic graphs for task scheduling;
- single-writer and merge-owner rules from concurrent version control;
- separation of implementation and verification;
- provenance and confidence labels for extracted facts;
- progressive disclosure for context management.

Where a specific external work is cited (for example in
`references/task-graphs.md`), the citation is a pointer to the idea. The wording,
examples, and structure in this package are EOS's own.

## Companion skills

- `fable5-thinking` — a compact statement of the EOS working principles.
- `graph-engineering` — a standalone task-graph and knowledge-graph primer.

All three are maintained together. None is a source of vendor prompt text.

## Trademarks

Product names such as OpenCode, Claude, Codex, Gemini, Cursor, Windows, and macOS
belong to their owners. References are descriptive and do not imply endorsement.
