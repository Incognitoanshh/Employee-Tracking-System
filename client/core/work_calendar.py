"""
Which days are working days.

The scheduler needs one question answered — "should anything be captured on
this date?" — and it must answer the same way on every machine, from data
the server sent. Keeping it here rather than in time_ist.py is deliberate:
time_ist.py is the timezone authority and depends on nothing, while this
reads configuration and is policy, not arithmetic.

WHAT THE SERVER SENDS
    weekly_offs   ISO weekday numbers as a comma-separated string.
                  1 = Monday ... 7 = Sunday. '7' means Sundays are off.
    holidays      IST dates as YYYY-MM-DD, comma-separated, covering the
                  next couple of months.

Both are stored verbatim by ConfigSyncManager and parsed here, so a value
the server has never sent (an empty string, a NULL that became "None",
whitespace, a stray weekday number like 9) degrades to "this is a working
day" rather than switching monitoring off. Failing towards capturing is the
safe direction: an extra day of screenshots is a nuisance, a silently
disabled tracker is the incident this project has already had twice.
"""
from __future__ import annotations

from datetime import date

from client.services.settings_service import SettingsService

_DAY_NAMES = {
    1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
    5: "Friday", 6: "Saturday", 7: "Sunday",
}


def weekly_offs() -> set[int]:
    """The ISO weekday numbers configured as non-working, ignoring junk."""
    raw = SettingsService.get_setting("weekly_offs", "") or ""
    out: set[int] = set()
    for piece in str(raw).split(","):
        piece = piece.strip()
        if not piece.isdigit():
            continue
        value = int(piece)
        if 1 <= value <= 7:
            out.add(value)
    return out


def holidays() -> set[str]:
    """Configured holiday dates as YYYY-MM-DD strings."""
    raw = SettingsService.get_setting("holidays", "") or ""
    return {
        piece.strip() for piece in str(raw).split(",")
        if len(piece.strip()) == 10 and piece.strip()[4] == "-"
    }


def day_off_reason(day: date) -> str | None:
    """
    Why `day` is not a working day, or None if it is one.

    The string is for logs and for the Attendance column, so it names the
    reason rather than just saying no.
    """
    if day.isoformat() in holidays():
        return "holiday"
    if day.isoweekday() in weekly_offs():
        return f"weekly off ({_DAY_NAMES[day.isoweekday()]})"
    return None


def is_working_day(day: date) -> bool:
    return day_off_reason(day) is None
