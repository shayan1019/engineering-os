# Method — Stage Protocol

## Stage 0 — FRAME

Produce exactly three lines before any idea work:

```text
PROBLEM:  <tek cümle — ne çözülecek>
CONSTRAINTS: <sabit olanlar: bütçe, teknoloji, zaman, fizik>
SUCCESS:  <ölçülebilir başarı tanımı>
```

- If the user gave only a wish ("bir şey icat et"), ask one clarifying
  question targeting the PROBLEM line, not the solution space.
- Constraints the user did not state are recorded as `ASSUMED:` and must
  be visible in every later table.

## Stage 1 — DIVERGE

1. Open `references/techniques.md`. Select 2–3 techniques whose
   "reach for when" matches the frame. Do not use more than 3 per round.
2. Generate 8–12 raw concepts total. Each concept is ONE line:
   `<konsept adı> — <mekanizma cümlesi> [teknik: <ad>]`.
3. Tag or delete. Untagged concepts do not survive Gate A.

**Gate A:** count ≥ 8 tagged concepts → proceed. Otherwise run one more
round with different techniques. Max 3 rounds; then stop and ask which
constraint to relax (bounded loop, stop rule).

## Gate B — Human Selection

Present top 3 as a comparison table:

| # | Konsept | Novelty | Feasibility | Impact | Tek cümle gerekçe |
|---|---------|---------|-------------|--------|-------------------|

Wait for the user's pick. No pick → no brief. If the user says "hepsini",
write one brief per concept but mark the recommended one.

## Stage 2 — CONVERGE (before Gate B)

Score every tagged concept with `rules/quality-bar.md`. Scores without a
one-line justification are invalid and must be re-derived.

## Stage 3 — BRIEF

Fill `templates/invention-brief.md` for the selected concept. The brief is
self-contained: a reader who never saw this conversation must understand
the mechanism, why it is novel, and how to falsify it.

## Stage 4 — HANDOFF (optional)

Only after the brief exists and only if the user wants execution:

| User goal | Handoff |
|---|---|
| Denemek/test etmek istiyor | prototip spec'i çıkar; trading konseptiyse `backtest` skill'ine devret |
| Ürün/mekanizma inşası | build planı: adım, bağımlılık, risk sütunlarıyla |
| Fikri sıkılaştırma | `idea-refine` varsa ona devret, yoksa brief'i kendin daralt |

Handoff does not mean re-running the other skill's workflow here.
