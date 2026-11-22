"""Scenario registry for the Engineering Awareness Benchmark."""
from .web import SCENARIOS as WEB
from .math_units import SCENARIOS as MATH

SCENARIOS = {}
for _group in (WEB, MATH):
    SCENARIOS.update(_group)

__all__ = ["SCENARIOS"]
