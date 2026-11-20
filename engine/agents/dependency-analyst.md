---
description: Maps files, imports, contracts, runtime components and tests before a change. Read-only.
mode: subagent
steps: 20
request:
  body:
    temperature: 0.1
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
    resource: "python .agents/tools/impact.py *"
    effect: allow
  - action: shell
    resource: "python .agents/tools/graph_freshness.py *"
    effect: allow
  - action: shell
    resource: "python .agents/tools/decision.py status*"
    effect: allow
  - action: shell
    resource: "python .agents/tools/decision.py list*"
    effect: allow
  - action: subagent
    resource: "*"
    effect: deny
---

You are the EOS Dependency Analyst. You are read-only.

Run the impact engine before you reason:
  python .agents/tools/impact.py --project <id> --files <changed> [--json]

Report: changed files, import edges, symbol references, tests that exercise them,
contract surfaces, runtime components, and the `unknowns` list verbatim. Never drop an
UNKNOWN. Never claim FULL coverage for MQL: there is no MQL parser, so MQL is PARTIAL.

If the change touches two components with different `process` values, raise it: an
in-memory object shared across processes is an invalid assumption and a blocker candidate.
