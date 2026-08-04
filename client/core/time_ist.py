"""The project's single source of truth for time.

Shift schedules are defined in IST. The admin panel labels them "(IST)",
the server stores them that way, and every client must resolve them to the
same wall-clock moment — whether that client sits in Mumbai, London or
Los Angeles, and regardless of how its operating system is configured.

WHY THIS MODULE EXISTS

Three production incidents, all from time handling spread across the
codebase rather than living in one place:

  1. The scheduler built its window from datetime.now() and a naive
     strptime, both in machine-local time. On a Pacific client the shift
     "09:00-18:00 IST" became 09:00-18:00 PDT, the code decided the shift
     had ended, and fell onto a path that ignored the configured count and
     captured every few minutes — 130 scheduled where 10 were configured.

  2. Shift values arrive in more than one shape. `/config/sync` sends full
     ISO with a +05:30 offset; the admin config path sends "HH:MM"; a
     Postgres TIME column yields "HH:MM:SS". Two separate parsing branches
     handled these, and they did not behave the same. The tz-aware branch
     was the one production actually used, and the one the tests never
     exercised.

  3. IST was defined independently in five modules. Consolidating them
     left a dangling reference and every scheduling attempt raised
     NameError — zero screenshots for a whole session, with no error
     visible anywhere.

So: one timezone constant, one clock function, one parser. Anything that
needs to reason about scheduling time imports from here and nowhere else.

RULES

  - Never call datetime.now() for scheduling. Use now_ist().
  - Never parse a shift string yourself. Use parse_shift_time().
  - Naive datetimes returned by this module are IST wall-clock. Compare
    them only against each other; the differences are real durations and
    are safe to hand to QTimer.
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, time, timedelta, timezone

__all__ = [
    "IST",
    "now_ist",
    "today_ist",
    "end_of_ist_day",
    "ist_day_str",
    "parse_shift_time",
    "ShiftTimeParseError",
]

#: India Standard Time. A fixed +05:30 offset with no DST, so a plain
#: timezone() is exact and avoids a dependency on the host's tz database —
#: which a frozen PyInstaller build may not ship.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class ShiftTimeParseError(ValueError):
    """Raised by parse_shift_time() when a value cannot be understood."""


def now_ist() -> datetime:
    """Current moment as IST wall-clock, naive.

    Derived from UTC rather than from local time, so a machine with the
    wrong timezone configured still gets the correct IST instant. Only a
    wrong *clock* (not timezone) could shift this, and that is outside the
    application's control.
    """
    return datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None)


def today_ist() -> _date:
    """Current IST calendar date. This is the day the screenshot budget
    is anchored to, so it must not follow the machine's local date."""
    return now_ist().date()


def end_of_ist_day(moment: datetime) -> datetime:
    """Last instant of the IST day that `moment` falls in."""
    return moment.replace(hour=23, minute=59, second=59, microsecond=0)


def ist_day_str(moment: datetime | None = None) -> str:
    """`YYYY-MM-DD` for the IST day — used as the daily budget key."""
    return (moment or now_ist()).strftime("%Y-%m-%d")


# "9:05", "09:00", "09:00:00", "09:00:00.123". Minutes must be two digits:
# a malformed value should fail loudly rather than be guessed at. A leading
# date is skipped by the search, so this also lifts the time out of an ISO
# string that datetime.fromisoformat could not take.
_CLOCK = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")


def parse_shift_time(value, on_date: _date) -> datetime:
    """Resolve any accepted shift representation to IST wall-clock.

    ONE implementation for every input shape, so there is no second branch
    that can drift from the first:

        "2026-08-04T09:00:00+05:30"  ISO with an offset  -> converted to IST
        "2026-08-04T09:00:00"        ISO, no offset      -> read as IST
        "09:00"                      HH:MM               -> read as IST
        "09:00:00"                   HH:MM:SS            -> read as IST
        datetime / time objects                          -> accepted directly

    Only the TIME OF DAY is used. A shift is a daily recurrence, so the
    date that happens to ride along in an ISO payload is discarded and
    replaced with `on_date` — the server has been observed sending stale
    dates, and honouring them made shifts look permanently finished.

    Returns a naive datetime on `on_date` in IST.
    Raises ShiftTimeParseError if the value cannot be understood; callers
    decide what to do about it rather than silently getting a wrong window.
    """
    if value is None:
        raise ShiftTimeParseError("shift time is not set")

    # Native objects — accept rather than force callers to stringify.
    if isinstance(value, datetime):
        moment = value.astimezone(IST) if value.tzinfo else value
        return datetime.combine(on_date, moment.time())
    if isinstance(value, time):
        return datetime.combine(on_date, value.replace(tzinfo=None))

    text = str(value).strip()
    if not text:
        raise ShiftTimeParseError("shift time is empty")

    # 1. Full timestamp. Handles both offset-aware and naive ISO, and is
    #    the only place an offset is ever applied.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(IST)
        return datetime.combine(on_date, parsed.time())

    # 2. Bare clock time, or a timestamp fromisoformat could not take.
    #    Same destination as branch 1 — only the extraction differs.
    match = _CLOCK.search(text)
    if match:
        hour, minute, second = (
            int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
        )
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return datetime.combine(on_date, time(hour, minute, second))
        raise ShiftTimeParseError(f"time out of range: {text!r}")

    raise ShiftTimeParseError(f"unrecognised shift time: {text!r}")
