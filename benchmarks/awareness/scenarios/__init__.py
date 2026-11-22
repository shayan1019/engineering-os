"""Scenario registry for the Engineering Awareness Benchmark."""
from .backend import SCENARIOS as BACKEND
from .extra import SCENARIOS as EXTRA
from .math_units import SCENARIOS as MATH
from .web import SCENARIOS as WEB

SCENARIOS = {}
for _group in (WEB, MATH, BACKEND, EXTRA):
    SCENARIOS.update(_group)

__all__ = ["SCENARIOS"]
