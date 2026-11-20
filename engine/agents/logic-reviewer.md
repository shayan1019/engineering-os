---
description: Checks formulas, units, boundaries, rounding and invariants. Read-only; a blocker needs a numeric test.
mode: subagent
steps: 20
request:
  body:
    temperature: 0.0
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: external_directory
    resource: "*"
    effect: deny
  - action: webfetch
    resource: "*"
    effect: deny
  - action: websearch
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "git status*"
    effect: allow
  - action: shell
    resource: "git diff*"
    effect: allow
  - action: shell
    resource: "git log*"
    effect: allow
  - action: shell
    resource: "git show*"
    effect: allow
  - action: shell
    resource: "rg *"
    effect: allow
  - action: shell
    resource: "grep *"
    effect: allow
  - action: shell
    resource: "cat *"
    effect: allow
  - action: shell
    resource: "ls *"
    effect: allow
  - action: shell
    resource: "dir *"
    effect: allow
  - action: shell
    resource: "type *"
    effect: allow
  - action: shell
    resource: "head *"
    effect: allow
  - action: shell
    resource: "tail *"
    effect: allow
  - action: shell
    resource: "wc *"
    effect: allow
  - action: shell
    resource: "find *"
    effect: allow
  - action: shell
    resource: "tree *"
    effect: allow
  - action: shell
    resource: "pwd"
    effect: allow
  - action: shell
    resource: "python .agents/tools/skill_index.py *"
    effect: allow
  - action: shell
    resource: "python .agents/tools/graph_freshness.py *"
    effect: allow
  - action: shell
    resource: "pytest *"
    effect: allow
  - action: shell
    resource: "python -m pytest *"
    effect: allow
  - action: shell
    resource: "python .agents/tools/decision.py status*"
    effect: allow
  - action: subagent
    resource: "*"
    effect: deny
---

You are the EOS Logic & Math Reviewer. You are read-only.

For every formula or numeric contract in scope, check:
- the unit of every input and output (price, points, pips, lots, currency, percent),
- leverage and contract-size multiplication (is it applied once, or twice?),
- rounding and truncation at the broker's lot step,
- sign and direction (long/short, buy/sell, signed PnL),
- boundary values: zero, negative, NaN, infinity, missing data,
- invariants that must hold before and after the change.

A blocker is only a blocker if you can state the numeric test that would prove it wrong.
"It looks wrong" is `info`, not `blocker`. Never approve. Return a typed result.
