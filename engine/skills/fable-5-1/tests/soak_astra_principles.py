#!/usr/bin/env python3
"""soak_astra_principles.py — Stage-7 soak regression test (deterministic, offline).

Proves the architecture-gap fix: Astra-derived working principles live as
first-class native Fable 5.1 behavior files (execution/pending-work.md,
work-continuity.md, delegation-discipline.md, steering-replan.md,
state-continuity.md — model-independent names, Astra only as provenance) while
true async / parallel / subagent / steering remain capability-gated acceleration
(runtime section B).

Structural assertions only — no brittle exact-sentence matching. Each check looks
for required concepts (token sets) and forbidden regressions (old pure-wait text,
provider-branching, adapter-as-authority).

Exit 0 = all pass, 1 = failure.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
# _ROOT = <workspace> when tests live at .agents/skills/fable-5-1/tests/


def _p(*parts):
    return os.path.join(_ROOT, *parts)


def _read(rel):
    with open(_p(*rel.split("/")), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _low(s):
    return s.lower()


def _has_all(text, tokens):
    t = _low(text)
    return all(tok.lower() in t for tok in tokens)


CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("skill_loads_scheduler_for_principles_on_graph")
def _c_skill_scheduler(ctx):
    skill = ctx["skill"]
    # SKILL.md must put scheduler-behavior.md in the GRAPH+ load path for its
    # principles (section A), not only when concurrency "could benefit".
    return _has_all(skill, ["scheduler-behavior.md", "graph", "section a",
                            "provider-independent", "false", "unknown"])


@check("skill_loads_steering_state_principles_when_unknown")
def _c_skill_steering_state(ctx):
    skill = ctx["skill"]
    return (_has_all(skill, ["steering-behavior.md", "preserve", "false", "unknown"])
            and _has_all(skill, ["state-behavior.md", "what fable owns",
                                 "false", "unknown"]))


@check("scheduler_section_a_principles_present")
def _c_sched_a(ctx):
    s = ctx["sched"]
    required_groups = [
        ["pending", "ready"],
        ["blocked", "independent", "ready"],
        ["agent-as-tool", "handoff"],
        ["preserve", "completed", "valid", "work"],
        ["structured", "intermediate"],
        ["replan"],
        ["resume", "reconstruct"],
    ]
    return all(_has_all(s, g) for g in required_groups)


@check("scheduler_invariant_efficiency_not_methodology")
def _c_sched_invariant(ctx):
    # Structural: capability affects efficiency, not methodology. No exact-sentence assert.
    return _has_all(ctx["sched"], ["capability", "efficiency", "methodology"])


@check("conservative_fallback_is_sequential_ready_work")
def _c_sched_conservative(ctx):
    s = ctx["sched"]
    low = _low(s)
    has_sequential_ready = _has_all(s, ["sequentially", "ready", "independent"])
    waits_only_when_nothing_ready = (
        "only when no other ready work exists" in low
        or ("no other ready" in low and "block/wait" in low))
    old_pure_wait_gone = "wait for each tool result before proceeding" not in low
    return has_sequential_ready and waits_only_when_nothing_ready and old_pure_wait_gone


@check("rich_runtime_acceleration_kept_separate")
def _c_sched_rich(ctx):
    s = ctx["sched"]
    return (_has_all(s, ["parallel_tool_support = true", "concurrently"])
            and _has_all(s, ["async_tools = true", "ready frontier"])
            and _has_all(s, ["subagents = true", "delegate"]))


EXEC_FILES = ("pending", "continuity", "delegation", "replan", "statecont")


@check("execution_subsystem_is_first_class_and_loaded")
def _c_exec_first_class(ctx):
    skill = ctx["skill"]
    names = ["execution/pending-work.md", "execution/work-continuity.md",
             "execution/delegation-discipline.md", "execution/steering-replan.md",
             "execution/state-continuity.md"]
    skill_loads_all = all(n in _low(skill) for n in names)
    load_context_ok = _has_all(skill, ["graph", "long-horizon", "multi-agent",
                                       "provider-independent"])
    no_model_gate = ("never gated on a provider name" in _low(skill)
                     or "without any model or provider" in _low(skill))
    return skill_loads_all and load_context_ok and no_model_gate


@check("execution_files_are_model_independent_with_provenance")
def _c_exec_naming(ctx):
    files = [ctx[k] for k in EXEC_FILES]
    provenanced = all("astra-derived" in _low(t) for t in files)
    no_provider_branch = all("provider ==" not in _low(t) for t in files)
    no_model_gate = all("model or provider dependency" in _low(t) for t in files)
    return provenanced and no_provider_branch and no_model_gate


@check("execution_covers_eleven_principles")
def _c_exec_principles(ctx):
    combined = "\n".join(ctx[k] for k in EXEC_FILES)
    groups = [
        ["pending", "ready"],
        ["idle", "independent"],
        ["worker", "handoff"],
        ["final ownership", "manager"],
        ["replan", "cancel/preserve/add"],
        ["preserve", "completed", "valid"],
        ["structured", "intermediate"],
        ["pending", "tool", "ready"],
        ["reconstruct", "verified"],
        ["multi-agent", "merge owner"],
        ["efficiency", "methodology"],
    ]
    per_file_spot = (
        _has_all(ctx["pending"], ["pending", "ready", "blocked"])
        and _has_all(ctx["delegation"], ["handoff", "ownership"])
        and _has_all(ctx["replan"], ["replan", "preserve"])
        and _has_all(ctx["statecont"], ["structured", "reconstruct"]))
    return all(_has_all(combined, g) for g in groups) and per_file_spot


@check("scheduler_defers_to_canonical_execution_file")
def _c_sched_defers(ctx):
    s = _low(ctx["sched"])
    return ("canonical statement" in s and "execution/pending-work.md" in s
            and "execution/state-continuity.md" in s)


@check("adapter_is_provenance_only_not_authority")
def _c_adapter(ctx):
    adapter = ctx["adapter"]
    skill = ctx["skill"]
    sched = ctx["sched"]
    ok_adapter_label = (
        "provenance" in _low(adapter) and "reference only" in _low(adapter))
    ok_skill_marks_optional = _has_all(
        skill, ["astra-adapter.md", "provenance", "not part of the execution load path"])
    others = [sched, ctx["steer"], ctx["state"], ctx["caps"], adapter]
    others += [ctx[k] for k in EXEC_FILES]
    no_provider_branch = all("provider == astra" not in _low(t) for t in others)
    return ok_adapter_label and ok_skill_marks_optional and no_provider_branch


@check("steering_state_preserve_discipline_intact")
def _c_steer_state(ctx):
    steer_ok = (_has_all(ctx["steer"], ["preserve", "completed valid work"])
                and _has_all(ctx["steer"], ["checkpoint/replan", "false", "unknown"]))
    state_ok = (_has_all(ctx["state"], ["what fable owns"])
                and _has_all(ctx["state"], ["textual", "contract", "false", "unknown"]))
    return steer_ok and state_ok


@check("verification_is_falsify_first_with_graded_outcomes")
def _c_verif_falsify(ctx):
    v = ctx["verif"]
    seeks_refutation = _has_all(v, ["falsify", "counterexample", "failure conditions"])
    not_verified_on_weak_evidence = _has_all(v, ["NOT_VERIFIED", "never PASS"])
    refuted_only_on_counterevidence = (
        "REFUTED" in v and "counterevidence" in _low(v)
        and "uncertainty alone is not refutation" in _low(v))
    return seeks_refutation and not_verified_on_weak_evidence and refuted_only_on_counterevidence


@check("verification_lists_unknowns_before_probing")
def _c_verif_unknowns(ctx):
    v = ctx["verif"]
    return _has_all(v, ["load-bearing unknowns", "smallest", "deterministic"])


def main():
    try:
        ctx = {
            "skill": _read(".agents/skills/fable-5-1/SKILL.md"),
            "sched": _read(".agents/skills/fable-5-1/runtime/scheduler-behavior.md"),
            "steer": _read(".agents/skills/fable-5-1/runtime/steering-behavior.md"),
            "state": _read(".agents/skills/fable-5-1/runtime/state-behavior.md"),
            "caps": _read(".agents/skills/fable-5-1/runtime/capability-model.md"),
            "adapter": _read(".agents/skills/fable-5-1/runtime/astra-adapter.md"),
            "pending": _read(".agents/skills/fable-5-1/execution/pending-work.md"),
            "continuity": _read(".agents/skills/fable-5-1/execution/work-continuity.md"),
            "delegation": _read(".agents/skills/fable-5-1/execution/delegation-discipline.md"),
            "replan": _read(".agents/skills/fable-5-1/execution/steering-replan.md"),
            "statecont": _read(".agents/skills/fable-5-1/execution/state-continuity.md"),
            "verif": _read(".agents/skills/fable-5-1/evidence/verification.md"),
        }
    except OSError as e:
        print(f"FAIL load-context: {e}")
        return 1

    fails = []
    for name, fn in CHECKS:
        try:
            ok = bool(fn(ctx))
        except Exception as e:  # never crash the harness opaquely
            ok = False
            print(f"FAIL {name} (exception: {e})")
        else:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        if not ok:
            fails.append(name)

    print(f"\nsummary: {len(CHECKS) - len(fails)}/{len(CHECKS)} passed; failed: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
