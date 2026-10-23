# Permissions


> **Authority boundary**: same as `policy/risk-contract.md` — this file documents permission *distinctions*, it does not itself grant or block anything mechanically. This workspace's actual permission enforcement (if any, for a given runtime) lives in that runtime's own tool-permission system, outside this skill.

## The core invariant: capability ≠ permission

**Runtime capability and permission are separate axes.** `runtime/capability-model.md`'s flags describe what a runtime *can technically do*; permission describes what it's *authorized* to do. Neither implies the other:

```text
can_execute_shell = true         # capability
authorized_to_modify_production = true   # permission — NOT implied by the above
```

A runtime capable of running shell commands is not thereby authorized to run any particular one. `graph/*.md` and `runtime/*.md` never conflate these — a `subagents=true` or `parallel_tool_support=true` capability never itself unlocks an operation `policy/risk-contract.md` would otherwise gate.

## Distinctions to keep separate, at minimum

- **read vs. write** — reading/analyzing is lower-risk than mutating; `graph/writer-and-merge-owner.md`'s one-writer rule only applies to the write side.
- **local vs. shared state** — a scratch/temp file a task owns outright vs. an artifact other work depends on.
- **tool execution** — running a tool is not itself a permission statement; what the tool does with what arguments is what matters.
- **external side effects** — anything visible outside the immediate working context (a message sent, a push, a deploy) is categorically different from a local edit, regardless of how the local edit was made.
- **network access** — where relevant, distinct from local file/tool access.
- **secret/credential handling** — distinct from ordinary file read/write; never assumed available just because file access is.
- **approval-required operations** — the set of things `policy/risk-contract.md`'s `approval_required` gate actually covers (irreversible, destructive, credential/deploy/migration-shaped).
- **writer-ownership interaction** — a permission to write does not by itself resolve *which* writer holds the artifact right now (`graph/writer-and-merge-owner.md` answers that separately).

## Lower layer never expands what a higher layer granted

Per `skill.contract.yaml`'s authority precedence, a lower layer (runtime optimization, graph scheduling) never widens what a higher layer (EOS methodology, external control-plane policy) has authorized. A `runtime/*.md` optimization deciding "I could do this faster by also touching X" is not permission to touch X — permission comes from `core/scope-contract.md`'s scope and `policy/risk-contract.md`'s gate, not from execution convenience.

## Roles (reused, not reinvented)

`engine/registry/AGENT_REGISTRY.json` defines the nine shipped roles ? chief-architect, architect, implementer, reviewer, verifier, test-engineer, performance-engineer, security-auditor and evidence-analyst ? with governing rules (one-writer, verifier != writer, bounded delegation, human gate, fallback-to-inline). `fable-5-1` does not restate that taxonomy; when a task needs role-based delegation, use the existing registry and do not invent a parallel one here.
