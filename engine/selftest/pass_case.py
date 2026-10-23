#!/usr/bin/env python3
"""EOS gate fixture: a test-like step that must pass."""
import sys

def check() -> None:
    assert 2 + 2 == 4, "arithmetic is broken"
    assert "eos".upper() == "EOS"

if __name__ == "__main__":
    check()
    print("pass_case: 2 assertions OK")
    sys.exit(0)
