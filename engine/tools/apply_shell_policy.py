#!/usr/bin/env python3
"""One-off: apply the "no-nag" shell policy to the nine EOS agent contracts.

Why this exists
---------------
Every EOS agent carried `shell: * -> ask`. OpenCode evaluates the permission
list with the most specific (last) rule winning, so a command that matched
none of the narrow allow patterns fell through to `ask` and the user was
prompted for permission on every ordinary command. In Build mode that is
noise: the prompts arrive for `cat`, `ls`, `npm test`, `cargo test` and
anything else the model decided to run.

The new policy:

* the two roles that legitimately execute project commands (implementer,
  test-engineer) get `shell: * -> allow`, with the destructive operations
  still denied explicitly (git add/commit/push, rm) and the workspace
  boundary still enforced by `external_directory: deny`;
* the read-only roles get `shell: * -> deny` instead of `ask`, so an
  unmatched command is refused outright instead of asking a human, and their
  allow lists are widened to the ordinary read commands so that legitimate
  investigation keeps working.

`ask -> deny` is a tightening, not a loosening: it removes the human from the
loop and makes the refusal deterministic. The prompt lists in the agent files
are a guardrail, not a security boundary - the boundary is the host sandbox
plus the EOS release and promotion authority.
"""
import os
import re
import sys

AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents")

# Agents that execute project commands on behalf of the change.
EXEC_ROLES = {"implementer", "test-engineer"}

# Ordinary read-only commands, added to the roles that only investigate.
READ_COMMANDS = [
    "cat *",
    "ls *",
    "dir *",
    "type *",
    "head *",
    "tail *",
    "wc *",
    "find *",
    "tree *",
    "pwd",
    "git show*",
]

SHELL_WILDCARD = re.compile(
    r"(  - action: shell\n    resource: \"\*\"\n    effect: )(ask|allow|deny)\n"
)


def apply(name: str, text: str) -> str:
    effect = "allow" if name in EXEC_ROLES else "deny"
    new_text, count = SHELL_WILDCARD.subn(r"\g<1>" + effect + "\n", text)
    if count != 1:
        raise SystemExit(f"{name}: expected exactly one shell wildcard rule, found {count}")

    if name not in EXEC_ROLES:
        # Widen the read list, but only for commands that are not already there.
        lines = new_text.split("\n")
        out, added = [], []
        for line in lines:
            if line.strip() == "  - action: subagent":
                for cmd in READ_COMMANDS:
                    if f'resource: "{cmd}"' not in new_text:
                        added.append(f'  - action: shell\n    resource: "{cmd}"\n    effect: allow')
            out.append(line)
        if added:
            new_text = "\n".join(out[: out.index("  - action: subagent")] + added + out[out.index("  - action: subagent") :])
    return new_text


def main() -> int:
    changed = []
    for name in sorted(os.listdir(AGENTS_DIR)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(AGENTS_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "action: shell" not in text:
            continue
        new_text = apply(name[:-3], text)
        if new_text != text:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new_text)
            changed.append(name)
    print("updated:", ", ".join(changed) if changed else "(nothing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
