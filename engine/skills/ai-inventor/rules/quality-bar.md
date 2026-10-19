# Quality Bar — Scoring and Kill Criteria

## Scoring Rubric (1–5 each, integer only)

| Axis | 1 | 3 | 5 |
|---|---|---|---|
| **Novelty** | Bilinen yöntemin yeniden adlandırılmışı | Bilinen parçaların yeni dizilimi | Mekanizma düzeyinde yeni davranış |
| **Feasibility** | Fizik/ekonomi kısıtını ihlal ediyor | Yapılabilir ama 2+ kritik varsayım var | Mevcut teknolojiyle tek hamlede kurulabilir |
| **Impact** | Problemi kısmen hafifletir | Problemi belirgin azaltır | SUCCESS ölçütünü doğrudan karşılar |

## Kill Criteria (a concept dies immediately if any hit)

1. Mechanism sentence cannot name WHAT changes physically/logically.
2. It violates a stated CONSTRAINT from Stage 0.
3. Its only defense is "AI will figure it out".
4. Novelty = 1 (it already exists as named).

## Evidence Discipline

- Every score carries one justification line; "seems good" is not a line.
- Feasibility 4–5 claims must cite the existing capability that makes it
  possible (a library, an API, an observed behavior) or be downgraded to 3.
- If novelty is claimed against prior art the user might know, say what it
  is NOT (closest known thing + the delta). Confidence format:
  `novelty 4 — en yakın bilinen X, fark: Y`.

## Ranking Rule

Sort by `(Novelty + Impact, then Feasibility)`. Ties break toward the
concept with fewer ASSUMED constraints.
