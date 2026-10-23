#!/usr/bin/env python3
"""EOS gate fixture: a test-like step that must fail.

The gate is expected to return FAIL here. If this fixture ever passes, the gate
has been weakened.
"""
import sys


def check() -> None:
    expected = 5
    actual = 2 + 2
    assert actual == expected, f"expected {expected}, got {actual}"


if __name__ == "__main__":
    check()
    print("fail_case: should never reach this line")
    sys.exit(0)
