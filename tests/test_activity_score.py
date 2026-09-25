"""
Telling a person from a mouse jiggler.

THE REPORT. "Aisa na ho ki koi word open krke auto button click ya mouse
movement laga ke chala de." The idle tracker cannot answer it: all it knows
is how long since the last input, and a jiggler holds that at zero all day.
On the timesheet an empty chair reads exactly like a working one.

WHAT IS TESTED HERE, and why each one matters more than the total:

  * A JIGGLER SCORES LOW. Movement, no keystroke, no click, no change of
    window, and the gaps between events identical to the millisecond.
  * A PERSON WORKING SCORES HIGH — typing, clicking, moving between
    applications, at the ragged intervals a hand produces.
  * A PERSON READING IS NOT ACCUSED. Scrolling a long document with no
    typing scores low, and that is expected — which is exactly why the
    alert on the server asks for the EVIDENCE (no keystrokes at all, and
    machine-like intervals) rather than for a low score. A test that only
    checked the number would have missed the difference.
  * THE ARITHMETIC IS THE OWNER'S. +10/+15/+20/+10/+30, -25/-20/-30, and
    the bands 70-100 / 40-69 / 0-39. If somebody changes one of those
    numbers, these say so.

Run:  python3 tests/test_activity_score.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_score_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_score_db_")
import client.core.config as config                                          # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module       # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


from PySide6.QtWidgets import QApplication                                   # noqa: E402

app = QApplication.instance() or QApplication([])

from client.application.managers.activity_score import (                      # noqa: E402
    Minute, POINTS, score_minute, band)

print("\nThe numbers are the ones that were asked for")

check("input +10, keyboard +15, application +20, window +10, meaningful +30",
      [POINTS["input"], POINTS["keyboard"], POINTS["application"],
       POINTS["window_change"], POINTS["meaningful"]] == [10, 15, 20, 10, 30],
      str(POINTS))
check("repetitive -25, nothing meaningful -20, same interval -30",
      [POINTS["repetitive"], POINTS["no_meaningful"], POINTS["same_interval"]]
      == [-25, -20, -30], str(POINTS))
check("70 and above is active", band(70) == "ACTIVE" and band(100) == "ACTIVE")
check("40 to 69 is low activity", band(40) == "LOW" and band(69) == "LOW")
check("below 40 is potentially idle", band(39) == "IDLE" and band(0) == "IDLE")

print("\nA jiggler")

# Two pixels every second, for a minute. Nothing else at all.
jiggler = score_minute(Minute(
    mouse_moves=58, keystrokes=0, clicks=0, scrolls=0, window_changes=0,
    gaps_ms=[1000, 1001, 999, 1000, 1000, 1002, 1000, 999]))
check("scores as potentially idle", jiggler["band"] == "IDLE",
      f"{jiggler['score']} — {jiggler['reasons']}")
# THE PART THE ALERT USES. A low score is not evidence; this is.
check("and is marked as automation, which is what an alert may act on",
      jiggler["automation_suspected"], str(jiggler))
check("saying the intervals were identical",
      "identical intervals" in jiggler["reasons"], str(jiggler["reasons"]))
check("and that the movement had nothing to show for it",
      "repetitive movement" in jiggler["reasons"], str(jiggler["reasons"]))

# An auto-clicker: clicks, still no keyboard, still a metronome.
clicker = score_minute(Minute(
    mouse_moves=0, clicks=60, keystrokes=0, window_changes=0,
    gaps_ms=[1000] * 10))
check("an auto-clicker is caught by its rhythm alone",
      clicker["automation_suspected"] and clicker["band"] != "ACTIVE",
      f"{clicker['score']} — {clicker['reasons']}")

print("\nA person working")

working = score_minute(Minute(
    keystrokes=180, clicks=12, scrolls=4, mouse_moves=200, window_changes=5,
    gaps_ms=[120, 340, 90, 1500, 60, 800, 210, 45]))
check("scores as active", working["band"] == "ACTIVE",
      f"{working['score']} — {working['reasons']}")
check("and is not marked as automation",
      not working["automation_suspected"], str(working["reasons"]))

# A minute of steady typing and nothing else — no mouse, no switching.
#
# THIS SCORES 55, WHICH IS "LOW ACTIVITY", and that is the table as it was
# given: input 10 + keyboard 15 + meaningful 30, with application
# interaction (20) reserved for clicks and scrolls and the window bonus (10)
# for changing application. Somebody writing for an hour in one document
# never earns either. It is recorded here rather than quietly rounded up,
# because the number is the owner's to change and this is where anybody
# changing it will look.
typing = score_minute(Minute(keystrokes=140, gaps_ms=[90, 150, 70, 300, 110, 80]))
check("somebody who only types earns the keyboard and meaningful points",
      typing["score"] == 55 and typing["band"] == "LOW",
      f"{typing['score']} {typing['band']} — {typing['reasons']}")
# AND THE PART THAT MATTERS: low is not an accusation. Nothing acts on the
# band; the alert asks for evidence of a machine, and a writer produces none.
check("and is never suspected of automation, which is what alerts act on",
      not typing["automation_suspected"], str(typing["reasons"]))

print("\nA person reading, who must NOT be treated as a faker")

reading = score_minute(Minute(
    scrolls=9, mouse_moves=12, keystrokes=0, clicks=0, window_changes=0,
    gaps_ms=[2400, 900, 5100, 1800, 700, 3300]))
check("scores low, because it does look quiet",
      reading["band"] != "ACTIVE", f"{reading['score']} — {reading['reasons']}")
# AND THIS IS THE ONE THAT MATTERS. The alert is built on this flag, not on
# the score, so a quiet hour of reading never becomes an accusation.
check("but is NOT marked as automation — the intervals are human",
      not reading["automation_suspected"], str(reading["reasons"]))

print("\nA minute with nothing in it")

nothing = score_minute(Minute())
check("scores zero", nothing["score"] == 0, str(nothing))
check("and claims no automation — an empty chair is not a trick",
      not nothing["automation_suspected"], str(nothing["reasons"]))

print("\nSix gaps, not two")

# TWO GAPS CAN MATCH BY CHANCE. Somebody who happens to click twice a second
# apart is not a machine, and this used to be the shape of a false accusation.
coincidence = score_minute(Minute(
    mouse_moves=30, gaps_ms=[1000, 1000]))
check("a couple of matching gaps prove nothing",
      "identical intervals" not in coincidence["reasons"],
      str(coincidence["reasons"]))

print("\nThe tracker that collects the minute")

import client.application.managers.activity_tracker as tracker_mod           # noqa: E402
from client.infrastructure.database.database import Database                 # noqa: E402
from client.application.managers.session_manager import SessionManager       # noqa: E402

Database.initialize()
SessionManager.employee_id = "E001"
SessionManager.role = "employee"

# The three doors onto the operating system, answered by hand — that is the
# whole reason they are three small functions.
counters = {"keystrokes": 0, "clicks": 0, "scrolls": 0, "mouse_moves": 0}
clock = {"ms": 1_000_000}
window = {"name": "Word"}
real = (tracker_mod.read_counters, tracker_mod.last_input_ms,
        tracker_mod.foreground_window)
try:
    tracker_mod.read_counters = lambda previous=None: dict(counters)
    tracker_mod.last_input_ms = lambda: clock["ms"]
    tracker_mod.foreground_window = lambda: window["name"]

    tracker = tracker_mod.ActivityTracker()
    tracker.sample()                      # the first sample is the baseline

    for _ in range(10):                   # a jiggler: one move a second
        counters["mouse_moves"] += 1
        clock["ms"] += 1000
        tracker.sample()

    scored = tracker.close_minute()
    check("it counts what the system reported",
          scored["mouse_moves"] == 10 and scored["keystrokes"] == 0, str(scored))
    check("measures the gaps between inputs, in milliseconds",
          scored["automation_suspected"], str(scored["reasons"]))
    check("and starts the next minute empty",
          tracker._minute.mouse_moves == 0 and tracker._minute.gaps_ms == [],
          str(tracker._minute))

    with Database.get_connection() as conn:
        row = conn.execute(
            "SELECT score, band, mouse_moves, automation_suspected, uploaded "
            "FROM activity_minutes ORDER BY id DESC LIMIT 1").fetchone()
    check("the minute is stored to be sent later",
          row is not None and row["mouse_moves"] == 10 and row["uploaded"] == 0,
          str(dict(row)) if row else "nothing stored")
    check("with the judgement, not just the numbers",
          row["band"] == "IDLE" and row["automation_suspected"] == 1,
          str(dict(row)) if row else "")

    # A COUNTER THAT GOES BACKWARDS IS NOT NEGATIVE ACTIVITY. Locking the
    # screen or switching users resets the session counters, and counting
    # that as a step would corrupt the minute.
    counters["mouse_moves"] = 0
    clock["ms"] += 1000
    tracker.sample()
    check("a counter reset is ignored rather than counted backwards",
          tracker._minute.mouse_moves == 0, str(tracker._minute.mouse_moves))

    # A change of application is what no automation produces.
    window["name"] = "Chrome"
    counters["keystrokes"] += 5
    clock["ms"] += 700
    tracker.sample()
    check("a change of application is noticed",
          tracker._minute.window_changes == 1, str(tracker._minute.window_changes))
finally:
    (tracker_mod.read_counters, tracker_mod.last_input_ms,
     tracker_mod.foreground_window) = real

print("\nThe owner's own machine is not scored")

SessionManager.role = "super_admin"
quiet = tracker_mod.ActivityTracker()
quiet.start()
check("a super admin's minutes are never collected", not quiet._timer.isActive())
SessionManager.role = "employee"

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
