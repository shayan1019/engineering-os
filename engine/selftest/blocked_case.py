#!/usr/bin/env python3
"""Fixture that reports BLOCKED (exit code 2), not FAIL.

A gate must distinguish "the code failed" from "the check could not run". This
fixture proves the gate maps exit code 2 to BLOCKED and keeps exit code 1 as
FAIL.
"""
import sys

print("blocked_case: the check could not run (missing interpreter/baseline)")
sys.exit(2)
