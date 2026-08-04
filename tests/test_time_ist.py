"""Unit tests for the canonical time utility.

Every shape the client can hold must resolve to the same IST wall-clock,
and anything unrecognisable must raise rather than quietly produce a
wrong window.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.core.time_ist import (          # noqa: E402
    IST, ShiftTimeParseError, end_of_ist_day, ist_day_str,
    now_ist, parse_shift_time, today_ist,
)

DAY = date(2026, 8, 4)

ACCEPTED = [
    ("ISO with +05:30",        "2026-08-04T09:00:00+05:30", "09:00:00"),
    ("ISO in UTC (same time)", "2026-08-04T03:30:00+00:00", "09:00:00"),
    ("ISO with Z",             "2026-08-04T09:00:00Z",      "14:30:00"),
    ("ISO without offset",     "2026-08-04T09:00:00",       "09:00:00"),
    ("ISO with stale date",    "2026-06-29T09:00:00+05:30", "09:00:00"),
    ("ISO with microseconds",  "2026-08-04T09:00:00.123456+05:30", "09:00:00"),
    ("HH:MM",                  "09:00",                     "09:00:00"),
    ("HH:MM:SS",               "09:00:00",                  "09:00:00"),
    ("H:MM",                   "9:05",                      "09:05:00"),
    ("padded with spaces",     "  22:00  ",                 "22:00:00"),
    ("midnight",               "00:00",                     "00:00:00"),
    ("last minute",            "23:59",                     "23:59:00"),
    ("datetime object",        datetime(2026, 1, 1, 9, 0),  "09:00:00"),
    ("aware datetime object",  datetime(2026, 1, 1, 3, 30, tzinfo=timezone.utc), "09:00:00"),
    ("time object",            time(9, 0),                  "09:00:00"),
]

REJECTED = [
    ("None", None), ("empty", ""), ("whitespace", "   "),
    ("prose", "not a time"), ("hour out of range", "99:99"),
    ("single-digit minute", "9:5"),
]


def main() -> int:
    failures = 0

    print("accepted formats — all must land on the same IST wall-clock:")
    for label, value, expected in ACCEPTED:
        try:
            got = parse_shift_time(value, DAY)
            ok = got.strftime("%H:%M:%S") == expected and got.date() == DAY \
                 and got.tzinfo is None
        except Exception as error:
            got, ok = f"{type(error).__name__}: {error}", False
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:24} -> {got}")

    print("\nrejected values — must raise, not guess:")
    for label, value in REJECTED:
        try:
            parse_shift_time(value, DAY)
            print(f"  FAIL  {label:24} was accepted")
            failures += 1
        except ShiftTimeParseError:
            print(f"  PASS  {label:24} raised ShiftTimeParseError")
        except Exception as error:
            print(f"  FAIL  {label:24} raised {type(error).__name__}, expected ShiftTimeParseError")
            failures += 1

    print("\nclock helpers:")
    n = now_ist()
    checks = [
        ("now_ist() is naive", n.tzinfo is None),
        ("now_ist() matches UTC+5:30",
         abs((n - datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None)).total_seconds()) < 5),
        ("today_ist() equals now_ist().date()", today_ist() == n.date()),
        ("end_of_ist_day() is 23:59:59",
         end_of_ist_day(n).strftime("%H:%M:%S") == "23:59:59"),
        ("end_of_ist_day() keeps the date", end_of_ist_day(n).date() == n.date()),
        ("ist_day_str() formats YYYY-MM-DD",
         ist_day_str(datetime(2026, 8, 4, 1, 2, 3)) == "2026-08-04"),
        ("IST offset is +05:30", IST.utcoffset(None) == timedelta(hours=5, minutes=30)),
    ]
    for label, ok in checks:
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

    print()
    if failures:
        print(f"{failures} failure(s)")
        return 1
    print(f"all {len(ACCEPTED) + len(REJECTED) + len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
