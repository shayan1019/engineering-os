# Engineering Awareness Benchmark

This is a measurement harness, not an EOS feature. It measures whether EOS helps
an agent discover unstated engineering requirements, choose a correct approach,
catch its own wrong solution, avoid unnecessary changes and keep the system
working.

The first slice is deliberately synthetic and controlled. A later slice adds
out-of-distribution tasks derived from public open-source projects.

## What this benchmark does not claim

Twelve synthetic scenarios do not prove universal engineering judgment. The
suite is accepted only when:

1. the scorer separates known-good from known-bad solutions,
2. a keyword-only report that changes no file and cites fake evidence earns
   discovery 0 and correctness below 0.5,
3. the same scenario produces the same score on a repeated control run, and
4. no scenario reports success without the oracle checks passing.

The real comparison is the 12-scenario suite and the open-source transfer set.

## Isolation rules

- The fixture is materialised into a fresh temporary directory for every run.
- The oracle, checks and controls live in `scenarios/` and are never copied into
  the fixture.
- Each arm gets its own `EOS_HOME` with its own engine and runtime:
  `old` = the `v1.0.1` tag, `new` = the current working tree engine.
- Both arms use the same model, the same task text, the same timeout and the same
  neutral output contract.
- The agent is not told about the review contract, the oracle or the expected
  blind spots.

## Metrics

| Metric | Definition |
|---|---|
| diagnosis | unstated blind spots the agent names with a real source citation (file/line); independent of whether the file changed |
| discovery | alias of diagnosis, kept for older result files |
| judgment | alternatives, assumptions and the correctness rationale in its decision |
| application | oracle checks on the resulting tree; 0 when the guard blocked the write |
| authorization | ALLOWED / BLOCKED / UNKNOWN |
| self-correction | a recorded wrong attempt, how it was detected and what changed, with a real source citation |
| scope | changed files outside the allowed set, forbidden files touched |
| regression | the fixture's own tests still pass |
| closure | the oracle checks pass twice (no flaky or partial fix) |
| false alarms | major/blocker findings that match no oracle blind spot |
| cost | wall-clock, tokens, permission prompts/denials |

`review` discipline is measured separately. A BLOCKED decision caused by a
missing review is not counted as engineering success; the discovery and
correctness scores stay independent.

## Scorer anti-gaming rules

- Diagnosis counts a blind spot only when the report names it **and** cites a
  real source file (and, when given, a real line) from the blind spot's
  `evidence` set. The file does **not** need to change; a CRITICAL gate that
  blocks the write must not erase a correct diagnosis.
- Application is measured separately by the hidden oracle checks on the tree.
- Authorization is recorded separately: `BLOCKED` means the guard refused the
  write and the result is not a reasoning failure.
- Self-correction needs `wrong`, `detected_by`, `fix` and a citation that
  resolves to a real source file/line.
- A control that does nothing must fail every correctness check; checks that a
  no-op passed are removed rather than averaged in.
- `adversarial` submits every blind-spot keyword with fake evidence and no file
  change. It must earn diagnosis 0 and application below 0.5.

Known confound: EOS may classify a task as CRITICAL and refuse a headless write
until a human approves. That is a product-permission outcome, not an engineering
judgment result. The scorer records `authorization=BLOCKED`; diagnosis is still
measured from real source citations, while application stays 0.

## Commands

```text
python benchmarks/awareness/runner.py list
python benchmarks/awareness/runner.py run --scenario web-i18n-01 --arm new
python benchmarks/awareness/runner.py run --scenario web-i18n-01 --arm old
python benchmarks/awareness/score.py score --scenario web-i18n-01 --result <dir>
python benchmarks/awareness/score.py controls --scenario web-i18n-01
python benchmarks/awareness/score.py adversarial --scenario web-i18n-01
python benchmarks/awareness/score.py compare --scenario web-i18n-01 --old <dir> --new <dir>
```

`controls` must pass before any model run is scored.

## Scenario contract

`scenarios/<domain>.py` exports `SCENARIOS`. Each entry has:

```python
{
  "domain": "web",
  "task": "the exact user request",
  "fixture": {"path": "content", ...},
  "blind_spots": [{"id": ..., "weight": 1, "signals": [...], "evidence": ["path"], "description": ...}],
  "judgment": {"signals": [...], "min_signals": 2},
  "allowed_paths": [...],
  "forbidden_paths": [...],
  "check": callable(tree: Path) -> [{"name", "ok", "detail"}],
  "regression": callable(tree: Path) -> [{"name", "ok", "detail"}],
  "controls": {"good": {"files": {...}, "agent": {...}},
               "bad":  {"files": {...}, "agent": {...}}}
}
```

The agent writes `.eos-benchmark.json`:

```json
{
  "findings": [{"severity": "blocker|major|minor", "claim": "...", "evidence": ["..."]}],
  "decision": {"approach": "...", "alternatives": ["..."], "assumptions": ["..."], "why": "..."},
  "self_correction": [{"wrong": "...", "detected_by": "...", "fix": "...", "evidence": ["path"]}],
  "scope": {"changed_files": ["..."], "not_changed": ["..."]},
  "closure": {"tests": ["..."], "residual_risks": ["..."]}
}
```
