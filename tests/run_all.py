#!/usr/bin/env python3
"""Run every test in this directory. Exit non-zero if any fails."""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = sorted(f for f in os.listdir(HERE)
               if f.startswith("test_") and f.endswith(".py"))

failed = []
for name in TESTS:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    proc = subprocess.run([sys.executable, os.path.join(HERE, name)])
    if proc.returncode != 0:
        failed.append(name)

print(f"\n{'=' * 70}")
if failed:
    print(f"FAILED: {', '.join(failed)}")
    raise SystemExit(1)
print(f"ALL {len(TESTS)} TEST FILES PASSED")
