"""Guard: nothing on the scheduling path may read the machine's local clock.

The scheduler, the screenshot manager and the shift manager decide WHEN
captures happen and which IST day they belong to. If any of them calls
datetime.now(), date.today() or defines its own timezone, a client in a
different timezone silently gets a different schedule — which is exactly
what happened in production.

Display code is deliberately out of scope: converting a stored UTC
timestamp to IST for the UI is correct and unrelated.

Run: python3 tests/test_no_local_time_in_scheduling.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEDULING_MODULES = [
    "client/application/schedulers/scheduler_service.py",
    "client/application/managers/screenshot_manager.py",
]

FORBIDDEN = [
    (re.compile(r"\bdatetime\.now\s*\("),   "datetime.now() — use now_ist()"),
    (re.compile(r"\bdate\.today\s*\("),     "date.today() — use today_ist()"),
    (re.compile(r"\bdatetime\.today\s*\("), "datetime.today() — use now_ist()"),
    (re.compile(r"\btime\.localtime\b"),    "time.localtime()"),
    (re.compile(r"\bstrptime\s*\("),        "strptime — use parse_shift_time()"),
    (re.compile(r"\bfromisoformat\s*\("),   "fromisoformat — use parse_shift_time()"),
    (re.compile(r"timezone\s*\(\s*timedelta"), "a local timezone definition — import IST"),
]

# Only one module may define the canonical helpers.
CANONICAL = "client/core/time_ist.py"


def strip_comments_and_docstrings(source: str) -> str:
    """Crude but adequate: drop # comments and triple-quoted blocks, so a
    file that merely *describes* the old behaviour does not fail."""
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    source = re.sub(r"'''[\s\S]*?'''", "", source)
    return "\n".join(
        line.split("#", 1)[0] for line in source.splitlines()
    )


def main() -> int:
    failures = []

    for rel in SCHEDULING_MODULES:
        path = os.path.join(ROOT, rel)
        code = strip_comments_and_docstrings(open(path, encoding="utf-8").read())
        hits = []
        for pattern, why in FORBIDDEN:
            for match in pattern.finditer(code):
                line = code[:match.start()].count("\n") + 1
                hits.append(f"line {line}: {why}")
        if hits:
            failures.extend(f"{rel}: {h}" for h in hits)
            print(f"  FAIL  {rel}")
            for h in hits:
                print(f"          {h}")
        else:
            print(f"  PASS  {rel}")

    # IST must be defined exactly once, in the canonical module.
    definers = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "client")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT)
            code = strip_comments_and_docstrings(open(full, encoding="utf-8").read())
            if re.search(r"^IST\s*=\s*timezone\s*\(", code, re.M):
                definers.append(rel)

    if definers == [CANONICAL]:
        print(f"  PASS  IST defined once, in {CANONICAL}")
    else:
        failures.append(f"IST defined in: {definers or 'nowhere'}")
        print(f"  FAIL  IST defined in {definers or 'nowhere'} (expected only {CANONICAL})")

    print()
    if failures:
        print(f"{len(failures)} violation(s) — scheduling still depends on local time")
        return 1
    print("no scheduling code reads the machine's local clock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
