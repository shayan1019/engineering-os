---
name: ai-inventor
description: Runs a structured invention process that turns a problem, goal, or vague wish into ranked, testable inventive concepts with novelty and feasibility scores. Use when the user says "icat et", "mucit gibi düşün", "yaratıcı fikir/çözüm bul", "invent", "invention", "come up with a new concept/product/mechanism", or wants original ideas beyond incremental improvement. Not for routine feature planning or bug fixing — those have their own skills.
metadata:
  tags: [invention, creativity, ideation, triz, divergent-thinking, concept-design, mucit, icat]
---

# AI Inventor

Turns a problem into invented solutions through a gated four-stage pipeline.
Core loop is advisory (thinking work); the final stage offers applied handoffs
(prototype spec, backtest, build plan) when the user wants execution.

## File Map

| File | Load when |
|---|---|
| `rules/method.md` | Always — stage protocol, gates, loop bounds |
| `rules/quality-bar.md` | Stage 2 (scoring concepts) |
| `references/techniques.md` | Stage 1 (divergence) — pick 2–3 techniques, not all |
| `templates/invention-brief.md` | Stage 3 — literal output skeleton |

## Pipeline

```text
Stage 0 FRAME    problemı tek cümleye indir, kısıtları ve başarı ölçütünü sabitle
Stage 1 DIVERGE  2–3 teknik seç (references/techniques.md), 8–12 ham konsept üret
Gate A           ham konsept sayısı < 8 ise yeni teknikle tur tekrarla (max 3 tur)
Stage 2 CONVERGE her konsepti puanla (rules/quality-bar.md), üst 3'ü kısa gerekçeyle seç
Gate B           kullanıcı seçimi onaylar (human gate) — onay yoksa brief yazılmaz
Stage 3 BRIEF    templates/invention-brief.md ile icat brifingi çıkar
Stage 4 HANDOFF  kullanıcı isterse: prototip spec'i, backtest planı veya build adımları
```

## Rules of Engagement

1. **Frame before inventing.** A fuzzy problem produces fuzzy inventions.
   Stage 0 output is one sentence: "X, Y kısıtıyla, Z'yi başarmalı."
2. **Diverge with technique, not vibes.** Each raw concept names the technique
   that produced it. Untagged concepts are deleted at Gate A.
3. **Score before pitching.** Never present a favorite without its
   novelty/feasibility/impact numbers (`rules/quality-bar.md`).
4. **Human gate at selection.** Present the top 3 as a comparison table;
   wait for the pick before writing the brief.
5. **Bounded loops.** Max 3 divergence rounds. Still under 8 concepts →
   tell the user the problem may be over-constrained and ask which
   constraint to relax.
6. **Composition over duplication.** If a dedicated skill exists for the
   chosen direction (e.g. `backtest`, `idea-refine`, `prototype`),
   hand off to it instead of re-implementing its job here.

## Output Contract

Every run ends with exactly one of:
- An invention brief (Stage 3 template, filled), or
- A blocked note stating which gate failed and what is needed to pass.

## Anti-Patterns

- Listing 20 shallow variations instead of 8–12 technique-tagged concepts.
- Skipping Gate B and writing a brief for your own favorite.
- Presenting scores without one-line justifications.
- Inventing where the user asked to fix, plan, or analyze — route back.
