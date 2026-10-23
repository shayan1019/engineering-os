#!/usr/bin/env python3
"""rollback_verify.py ? verify a version switch preserves host activation.

Runs against an isolated EOS_HOME so the working installation is never touched.
Compares `eos version` and `eos activate` output before and after a rollback.

Usage:
  python engine/tools/rollback_verify.py --home <isolated-eos-home> \
      --from-version 0.2.6-alpha --to-version 0.2.5-alpha \
      --eos-bin <path-to-eos.exe>

Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def run_eos(eos_bin: str, eos_home: str, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["EOS_HOME"] = eos_home
    proc = subprocess.run([eos_bin, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=60)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="isolated EOS_HOME directory")
    ap.add_argument("--from-version", required=True)
    ap.add_argument("--to-version", required=True)
    ap.add_argument("--eos-bin", required=True, help="path to eos.exe launcher")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    eos_home = os.path.abspath(args.home)
    os.makedirs(eos_home, exist_ok=True)
    versions_dir = os.path.join(eos_home, "versions")
    os.makedirs(versions_dir, exist_ok=True)

    for ver in (args.from_version, args.to_version):
        vdir = os.path.join(versions_dir, ver)
        if not os.path.isdir(vdir):
            print(f"MISSING: version directory {vdir}")
            return 1

    results = {}

    config_path = os.path.join(eos_home, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"current": args.from_version}, fh)

    rc, out, err = run_eos(args.eos_bin, eos_home, "version")
    results["from_version"] = {"rc": rc, "stdout": out.strip(), "stderr": err.strip()}
    if rc != 0 or args.from_version not in out:
        print(f"FAIL: version check for {args.from_version}: rc={rc} out={out.strip()}")
        return 1

    rc, out, err = run_eos(args.eos_bin, eos_home, "activate", "--task", "rollback test", "--json")
    results["from_activate"] = {"rc": rc, "stdout": out.strip()[:500], "stderr": err.strip()[:500]}
    from_activate_ok = rc == 0 and "eos-context" in out

    rc, out, err = run_eos(args.eos_bin, eos_home, "rollback")
    results["rollback"] = {"rc": rc, "stdout": out.strip()[:500], "stderr": err.strip()[:500]}

    rc, out, err = run_eos(args.eos_bin, eos_home, "version")
    results["to_version"] = {"rc": rc, "stdout": out.strip(), "stderr": err.strip()}
    if rc != 0 or args.to_version not in out:
        print(f"FAIL: version check for {args.to_version} after rollback: rc={rc} out={out.strip()}")
        return 1

    rc, out, err = run_eos(args.eos_bin, eos_home, "activate", "--task", "rollback test", "--json")
    results["to_activate"] = {"rc": rc, "stdout": out.strip()[:500], "stderr": err.strip()[:500]}
    to_activate_ok = rc == 0 and "eos-context" in out

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"current": args.from_version}, fh)

    rc, out, err = run_eos(args.eos_bin, eos_home, "version")
    results["restore_version"] = {"rc": rc, "stdout": out.strip(), "stderr": err.strip()}

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"from_version: {results['from_version']['stdout']}")
        print(f"from_activate: {'PASS' if from_activate_ok else 'FAIL'}")
        print(f"rollback: rc={results['rollback']['rc']}")
        print(f"to_version: {results['to_version']['stdout']}")
        print(f"to_activate: {'PASS' if to_activate_ok else 'FAIL'}")
        print(f"restore: {results['restore_version']['stdout']}")

    if not from_activate_ok or not to_activate_ok:
        print("FAIL: activation did not survive version switch")
        return 1
    print("PASS: rollback verification complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
