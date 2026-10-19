#!/usr/bin/env python3
"""EOS gate fixture: a command that hangs, to prove the timeout is enforced."""
import time

if __name__ == "__main__":
    print("slow_case: sleeping 30s (gate timeout is 2s)")
    time.sleep(30)
    print("slow_case: should not be reachable under a 2s timeout")
