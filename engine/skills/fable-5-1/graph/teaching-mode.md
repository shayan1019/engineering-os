# Teaching Mode

*PARITY_RESTORATION (Stage 6 correction) — restores `graph-engineering/SKILL.md`'s "Teaching Mode" section, found missing during the Stage 6 baseline-parity re-check. Not itemized as a B1/B2 item in the Stage 0 checklist (a checklist gap, not a Stage 3 implementation gap), so it fell outside tracking until this pass caught it directly against the source.*

> **Authority boundary**: this file changes *communication mode*, not methodology. Everything in `core/*.md`, the rest of `graph/*.md`, `runtime/*.md`, `policy/*.md`, and `evidence/*.md` still applies exactly as written — Teaching Mode explains those decisions, it does not loosen, skip, or reinterpret them.

## Trigger — conditional, never default

Activate only on genuine teaching/explanation intent: **explain**, **teach**, **walk me through**, **why**, **diagram**, **compare concepts**, requests to see the reasoning structure at a high level, or an explicit learning/tutorial request. An ordinary execution/engineering task does **not** activate this file — most tasks never load it, same as every other conditional module in this package.

```text
execution task    -> execution-first behavior (core/*.md, graph/*.md as normal)
teaching request  -> pedagogical behavior (this file)
mixed task        -> complete the work, then explain the parts that matter
```

## What Teaching Mode may do

Conceptual explanation; a step-by-step walkthrough; diagrams/flow structure when a concept genuinely has a shape worth drawing; worked examples; contrasting alternatives (why this approach over that one); explaining a specific graph/dependency/evidence/runtime-capability decision that was made or is being considered.

## What it must never do

Expose private chain-of-thought as a substitute for explanation — teaching means explaining *decisions, structure, evidence, and rationale* at a level appropriate to the question asked, not narrating raw internal reasoning. It also never weakens execution discipline: a mixed request ("explain why, then do it") still gets the full `core/scope-contract.md` → `core/execution-discipline.md` → (graph/runtime/policy/evidence as applicable) treatment for the *doing* part; the explanation is additive, not a replacement for rigor.

## Restored mechanics, from the source (generalized scope — see note below)

The source's actual teaching mechanics, native here:

- **Anchor in the user's own example.** Ask for one real task/project/decision and use it as the running example, rather than teaching in the abstract.
- **Generate visual artifacts when concepts are shapes.** A task graph, a dependency structure, an execution-depth decision tree — these are genuinely shapes; draw them (e.g. a mermaid diagram) rather than only describing them in prose.
- **Teach incrementally, not as one dump.** One concept per exchange where the topic has real depth, each closing with a small check ("does this match what you were trying to do?") before moving on.
- **Close with something concrete.** Assemble what was covered into a real artifact for the user's actual task — not just a recap.

**Scope note — generalization, disclosed**: the source (`graph-engineering`) scoped Teaching Mode specifically to graph engineering (the 9-stage KG pipeline and task graphs). Since `fable-5-1` unifies what were previously separate skills, this restoration extends the same mechanics to any of the package's decision domains on request — scope/acceptance choices (`core/scope-contract.md`), execution-depth classification (`core/task-complexity-classifier.md`), evidence/verification reasoning (`evidence/*.md`), or runtime-capability choices (`runtime/*.md`) — not only graph structure. This extension is stated explicitly here, not silently assumed; the graph-specific mechanics above are the actual restored source content.
