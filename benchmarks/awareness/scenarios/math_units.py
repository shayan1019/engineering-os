"""Math/algorithm scenarios for the Engineering Awareness Benchmark.

The fixtures work on the happy path. The hidden defects are unit and
missing-data assumptions that a careful engineer must state and fix.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

POSITION_SOURCE = '''def position_size(equity, risk_pct, entry, stop,
                  contract_size=100000, min_lot=0.01, lot_step=0.01):
    risk_amount = equity * risk_pct / 100.0
    stop_distance = abs(entry - stop)
    lots = risk_amount / (stop_distance * contract_size)
    return round(lots, 2)
'''

POSITION_TEST = '''import unittest
from position_size import position_size


class PositionSizeTest(unittest.TestCase):
    def test_basic_case(self):
        self.assertAlmostEqual(position_size(10000, 1, 1.1000, 1.0950), 0.2, places=6)


if __name__ == "__main__":
    unittest.main()
'''

POSITION_GOOD = '''import math


def position_size(equity, risk_pct, entry, stop,
                  contract_size=100000, min_lot=0.01, lot_step=0.01):
    if equity <= 0 or risk_pct <= 0 or contract_size <= 0 or lot_step <= 0:
        return 0.0
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return 0.0
    risk_amount = equity * risk_pct / 100.0
    raw = risk_amount / (stop_distance * contract_size)
    steps = math.floor(raw / lot_step + 1e-12)
    lots = round(steps * lot_step, 10)
    if lots < min_lot:
        return 0.0
    return lots
'''

POSITION_BAD = POSITION_SOURCE

WEIGHTED_SOURCE = '''def weighted_average(values, weights):
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)
'''

WEIGHTED_TEST = '''import unittest
from weighted_average import weighted_average


class WeightedAverageTest(unittest.TestCase):
    def test_dense_case(self):
        self.assertAlmostEqual(weighted_average([1, 2], [1, 3]), 1.75)


if __name__ == "__main__":
    unittest.main()
'''

WEIGHTED_GOOD = '''def weighted_average(values, weights):
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    for _, weight in pairs:
        if weight < 0:
            raise ValueError("weights must not be negative")
    total = sum(weight for _, weight in pairs)
    if total == 0:
        return None
    return sum(value * weight for value, weight in pairs) / total
'''

WEIGHTED_BAD = WEIGHTED_SOURCE


def _load(tree: Path, name: str):
    path = tree / (name + ".py")
    spec = importlib.util.spec_from_file_location(f"bench_{name}_{abs(hash(str(tree)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _visible_tests(tree: Path) -> list:
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          cwd=str(tree), capture_output=True, text=True, timeout=120)
    return [{"name": "visible tests", "ok": proc.returncode == 0,
             "detail": (proc.stderr or proc.stdout).strip()[-400:]}]


def _check_position(tree: Path) -> list:
    checks = []
    module = _load(tree, "position_size")
    fn = module.position_size
    try:
        lots = fn(10000, 1, 1.1000, 1.09673)
        cap = 100.0
        risk = lots * 0.00327 * 100000
        checks.append({"name": "rounds down to the lot step", "ok":
                       abs(lots - 0.30) < 1e-9 and risk <= cap + 1e-9,
                       "detail": f"lots={lots!r}, risk={risk!r}"})
        checks.append({"name": "lots stay on the lot step", "ok":
                       abs((lots / 0.01) - round(lots / 0.01)) < 1e-9,
                       "detail": f"lots={lots!r}"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "rounds down to the lot step", "ok": False,
                       "detail": f"{type(exc).__name__}: {exc}"})
    try:
        small = fn(100, 1, 1.1000, 1.0900)
        checks.append({"name": "below the minimum lot returns zero", "ok":
                       abs(small) < 1e-12,
                       "detail": f"lots={small!r}"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "below the minimum lot returns zero", "ok": False,
                       "detail": f"{type(exc).__name__}: {exc}"})
    invalid_ok = True
    for args in ((10000, 1, 1.1, 1.1), (10000, 0, 1.1, 1.0), (-1, 1, 1.1, 1.0)):
        try:
            value = fn(*args)
            invalid_ok = invalid_ok and abs(value) < 1e-12
        except ValueError:
            continue
        except Exception:
            invalid_ok = False
    checks.append({"name": "invalid inputs do not produce exposure", "ok": invalid_ok,
                   "detail": "zero/negative inputs must not return a positive size"})
    risk_ok = True
    for equity, pct, entry, stop in ((10000, 1, 1.1, 1.09673),
                                     (25000, 0.5, 1.2, 1.196),
                                     (5000, 2, 1.05, 1.0455)):
        try:
            lots = fn(equity, pct, entry, stop)
            risk = lots * abs(entry - stop) * 100000
            risk_ok = risk_ok and risk <= equity * pct / 100.0 + 1e-9
        except Exception:
            risk_ok = False
    checks.append({"name": "post-condition: risk never exceeds the limit", "ok": risk_ok,
                   "detail": "lots * stop distance * contract size <= risk amount"})
    return checks


def _check_weighted(tree: Path) -> list:
    checks = []
    module = _load(tree, "weighted_average")
    fn = module.weighted_average

    def value(*args):
        try:
            return fn(*args), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    got, exc = value([10, None, 20], [1, 1, 1])
    checks.append({"name": "missing readings are skipped with their weights", "ok":
                   exc is None and got is not None and abs(got - 15.0) < 1e-9,
                   "detail": f"got={got!r} exc={exc!r}"})
    got, exc = value([], [])
    checks.append({"name": "no valid weights returns None", "ok":
                   (exc is None and got is None) or isinstance(exc, ValueError),
                   "detail": f"got={got!r} exc={exc!r}"})
    got, exc = value([1, 2], [1])
    checks.append({"name": "length mismatch is rejected", "ok": isinstance(exc, ValueError),
                   "detail": f"got={got!r} exc={exc!r}"})
    got, exc = value([1, 2], [1, -1])
    checks.append({"name": "negative weights are rejected", "ok": isinstance(exc, ValueError),
                   "detail": f"got={got!r} exc={exc!r}"})
    got, exc = value([1, 2], [1, 3])
    checks.append({"name": "dense behaviour is unchanged", "ok":
                   exc is None and got is not None and abs(got - 1.75) < 1e-9,
                   "detail": f"got={got!r} exc={exc!r}"})
    return checks


POSITION_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "round() can round the lot size up, so the configured risk can be exceeded; floor to the lot step", "evidence": ["position_size.py:5"]},
        {"severity": "major", "claim": "If the rounded size is below the minimum lot, the function must return 0 instead of forcing a minimum trade", "evidence": ["position_size.py:3"]},
        {"severity": "major", "claim": "Zero or negative stop distance, equity and risk percent must be rejected", "evidence": ["position_size.py:2"]},
        {"severity": "major", "claim": "The post-condition is lots * stop distance * contract size <= risk amount", "evidence": ["position_size.py"]},
        {"severity": "minor", "claim": "Spread and slippage can still push realised risk above the nominal limit", "evidence": ["README.md"]},
    ],
    "decision": {
        "approach": "Validate inputs, floor the raw size to the lot step, return zero below the minimum lot and enforce the risk post-condition.",
        "alternatives": ["Keep round() and accept a small risk overshoot", "Clamp to min_lot even when that exceeds the risk budget"],
        "assumptions": ["contract_size is the contract size per lot", "the stop distance is the only price risk in this task"],
        "why": "The risk percentage is a hard limit. Rounding to nearest can exceed it, and forcing the minimum lot can multiply the intended risk.",
    },
    "self_correction": [{"wrong": "First replaced round() with a minimum-lot clamp", "detected_by": "Computed the risk of the clamped size", "fix": "Return 0 below the minimum lot and floor to the lot step"}],
    "scope": {"changed_files": ["position_size.py"], "not_changed": ["tests/test_position_size.py"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": ["spread and slippage are outside this function"]},
}

POSITION_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Changed the rounding call", "evidence": ["position_size.py"]}],
    "decision": {"approach": "Used round() with two decimals", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["position_size.py"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

WEIGHTED_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "None values represent missing readings and must be skipped together with their weights", "evidence": ["weighted_average.py:1"]},
        {"severity": "major", "claim": "A zero total weight must not divide by zero", "evidence": ["weighted_average.py:1"]},
        {"severity": "major", "claim": "zip silently truncates mismatched lengths; the lengths must be validated", "evidence": ["weighted_average.py:1"]},
        {"severity": "major", "claim": "Negative weights are invalid and must be rejected", "evidence": ["weighted_average.py:1"]},
    ],
    "decision": {
        "approach": "Validate lengths and weights, drop missing values with their weights, return None when no weight remains and keep the dense formula.",
        "alternatives": ["Treat None as zero", "Raise on any missing reading"],
        "assumptions": ["None is the only missing marker", "weights are non-negative importance values"],
        "why": "A missing sensor must not pull the result to zero, and an empty valid set has no defined average. zip would otherwise hide a length bug.",
    },
    "self_correction": [{"wrong": "First treated None as zero", "detected_by": "Re-read the task: sparse sensor readings are missing, not zero", "fix": "Skipped None and its weight"}],
    "scope": {"changed_files": ["weighted_average.py"], "not_changed": ["tests/test_weighted_average.py"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
}

WEIGHTED_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Added a zero check", "evidence": ["weighted_average.py"]}],
    "decision": {"approach": "Guarded the denominator", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["weighted_average.py"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

SCENARIOS = {
    "math-position-size": {
        "id": "math-position-size", "domain": "math",
        "task": "Fix the position sizing function so the configured risk percentage is never exceeded. The risk percentage is a hard limit.",
        "fixture": {
            "position_size.py": POSITION_SOURCE,
            "tests/test_position_size.py": POSITION_TEST,
            "README.md": "# Position sizing\n\nrisk_amount = equity * risk_pct / 100\nlots = risk_amount / (stop_distance * contract_size)\n",
            "pyproject.toml": '[project]\nname = "acme-position-size"\nversion = "0.1.0"\n',
        },
        "blind_spots": [
            {"id": "round-down", "weight": 1, "signals": ["round down", "floor", "lot step", "rounding", "exceed"], "description": "Rounding to nearest can exceed the risk budget; floor to the lot step."},
            {"id": "min-lot", "weight": 1, "signals": ["min lot", "minimum lot", "cannot trade", "too small", "0.01"], "description": "Forcing the minimum lot can exceed the risk budget."},
            {"id": "validation", "weight": 1, "signals": ["stop", "zero", "negative", "invalid", "risk_pct"], "description": "Zero/negative stop, equity and risk must not create exposure."},
            {"id": "post-condition", "weight": 1, "signals": ["risk amount", "never exceeded", "post-condition", "assert", "risk limit"], "description": "The result must satisfy lots * stop * contract <= risk amount."},
            {"id": "spread", "weight": 0.5, "signals": ["spread", "slippage", "commission"], "description": "Spread and slippage are residual risks outside the function."},
        ],
        "judgment": {"signals": ["floor", "min lot", "risk amount", "stop distance"], "min_signals": 2},
        "allowed_paths": ["position_size.py", "tests/test_position_size.py", "tests/", "README.md"],
        "forbidden_paths": [],
        "check": _check_position,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": {"position_size.py": POSITION_GOOD}, "agent": POSITION_GOOD_AGENT},
            "bad": {"files": {"position_size.py": POSITION_BAD}, "agent": POSITION_BAD_AGENT},
        },
    },
    "math-weighted-average": {
        "id": "math-weighted-average", "domain": "math",
        "task": "Make the weighted average correct for sparse sensor readings. Missing readings are represented as None.",
        "fixture": {
            "weighted_average.py": WEIGHTED_SOURCE,
            "tests/test_weighted_average.py": WEIGHTED_TEST,
            "README.md": "# Weighted average\n\nvalues and weights arrive from several sensors. Some readings can be missing.\n",
            "pyproject.toml": '[project]\nname = "acme-weighted-average"\nversion = "0.1.0"\n',
        },
        "blind_spots": [
            {"id": "missing", "weight": 1, "signals": ["none", "missing", "sparse", "skip"], "description": "Missing readings must be skipped with their weights, not treated as zero."},
            {"id": "zero-weight", "weight": 1, "signals": ["zero", "division by zero", "no valid", "total weight"], "description": "A zero total weight has no defined average."},
            {"id": "length-mismatch", "weight": 1, "signals": ["length", "mismatch", "zip", "truncate"], "description": "zip silently truncates mismatched lengths."},
            {"id": "negative-weight", "weight": 1, "signals": ["negative", "validate"], "description": "Negative weights are invalid."},
            {"id": "dense-regression", "weight": 0.5, "signals": ["existing", "regression", "dense"], "description": "The dense behaviour must stay unchanged."},
        ],
        "judgment": {"signals": ["skip", "total weight", "length", "negative"], "min_signals": 2},
        "allowed_paths": ["weighted_average.py", "tests/test_weighted_average.py", "tests/", "README.md"],
        "forbidden_paths": [],
        "check": _check_weighted,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": {"weighted_average.py": WEIGHTED_GOOD}, "agent": WEIGHTED_GOOD_AGENT},
            "bad": {"files": {"weighted_average.py": WEIGHTED_BAD}, "agent": WEIGHTED_BAD_AGENT},
        },
    },
}
