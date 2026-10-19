# Attribution

*Provenance/credits pointer, not behavioral content — kept separate from `core/*.md`/`graph/*.md` deliberately, per Stage 6 review. `fable-5-1` synthesizes and natively restates material from two source skills; this file identifies where that material originally came from.*

## `fable5-thinking`

Distilled from a Claude Fable 5 system prompt. Full original source carried over verbatim: [fable5-full-system-prompt.txt](fable5-full-system-prompt.txt).

## `graph-engineering`

Two halves:
- **Knowledge graphs** (the 9-stage pipeline restated in `graph/knowledge-graph-trigger.md`, and Teaching Mode restated in `graph/teaching-mode.md`): distilled and translated from Southeast University's graduate Knowledge Graph course (东南大学《知识图谱》研究生课程), Prof. Peng Wang — https://github.com/npubird/KnowledgeGraphCourse. All original lecture PDFs are in Chinese; the source skill (and this package's native restatement of it) is an independent English distillation adapted for AI-agent workflows. Full attribution is also retained inside the carried-over reference files themselves: [curriculum.md](curriculum.md) and [fusion-and-llm.md](fusion-and-llm.md) both credit the course and author directly.
- **Task graphs** (restated across `graph/task-graph.md`, `graph/parallelism.md`, `graph/writer-and-merge-owner.md`, `graph/verification-diamond.md`, `graph/bounded-repair.md`): original source carried over verbatim: [task-graphs.md](task-graphs.md), including its own citation of the Google DeepMind × MIT study "Towards a Science of Scaling Agent Systems."

Additional carried-over grounding material: [modeling.md](modeling.md), [extraction.md](extraction.md).

## Everything else in `fable-5-1`

`core/task-complexity-classifier.md`, `execution/pending-work.md`,
`execution/work-continuity.md`, `execution/delegation-discipline.md`,
`execution/steering-replan.md`, `execution/state-continuity.md` (provider-independent work
disciplines with Astra-derived provenance — behavior sources, not model dependencies), all of `runtime/*.md`, all of `policy/*.md`, all of `evidence/*.md`, and the safe-parallelism/non-progress/steering synthesis sections called out inline in `graph/parallelism.md` and `graph/bounded-repair.md` are new synthesis authored for this package — each flagged as such in its own file header, not attributed to either source skill.
