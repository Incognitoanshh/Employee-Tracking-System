"""
The application shell: the grouped menu, and the header that names the page.

THE BUG THIS FILE EXISTS FOR. The menu draws its entries in sections now, so
the order on screen is no longer the order of PAGES. Everything else in the
panel — select(), the page stack, the jump to My Profile — indexes into
PAGES. The moment the two stopped matching, `self._buttons[index]` opened the
right page and highlighted the wrong menu entry: a fault that looks like a
rendering glitch and is actually a wrong lookup.

Run:  python3 tests/test_shell_layout.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


from client.presentation.windows import admin_config_panel as panel  # noqa: E402

PAGES = panel.PAGES
SECTIONS = panel.NAV_SECTIONS
SOURCE = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "admin_config_panel.py"), encoding="utf-8").read()

print("\nEvery page is reachable, and none is listed twice")
listed = [key for _heading, keys in SECTIONS for key in keys]
check("no page appears in two sections",
      len(listed) == len(set(listed)),
      ", ".join(sorted({k for k in listed if listed.count(k) > 1})))
known = {p["key"] for p in PAGES}
check("no section names a page that does not exist",
      set(listed) <= known, ", ".join(sorted(set(listed) - known)))
missing = known - set(listed)
check("every page is filed in a section",
      not missing,
      f"{', '.join(sorted(missing))} — these fall into OTHER, which works but "
      f"means somebody forgot")

print("\nThe two pages that are one row apart and almost the same name")
# "Leave" is everybody's; "My Leave" is yours. They sat next to each other in
# a flat list, and an admin looking for their own request found the approval
# queue instead.
def section_of(key):
    return next((h for h, keys in SECTIONS if key in keys), None)

check("Leave and My Leave are in different sections",
      section_of("leave") != section_of("myleave"),
      f"both in {section_of('leave')}")
check("Payroll and My Payroll are too",
      section_of("payroll") != section_of("mypayroll"))
check("the personal ones are grouped together",
      section_of("myleave") == section_of("mypayroll") == section_of("profile"))

print("\nThe menu index and the PAGES index cannot drift apart")
check("select() goes through the button group, not the drawn order",
      "self._group.button(index)" in SOURCE,
      "self._buttons[index] is the position on screen, which is no longer "
      "the position in PAGES")
check("buttons are registered with their PAGES index as their id",
      re.search(r"self\._group\.addButton\(btn, index\)", SOURCE) is not None)
check("and the first selection is made by key, not by position",
      'self._nav_by_key["dashboard"].setChecked(True)' in SOURCE,
      "_buttons[0] is only the dashboard until somebody reorders a section")

print("\nThe header names the page")
check("the breadcrumb is built from NAV_SECTIONS, not written twice",
      "for heading, keys in NAV_SECTIONS" in SOURCE)
check("set_page takes the section", "def set_page(self, icon: str, title: str, "
      "subtitle: str, section: str = \"\")" in SOURCE)
check("the product name is no longer the page heading",
      'self._title = QLabel("Amaze Connect")' not in SOURCE,
      "it said Amaze Connect on all fifteen pages, in the largest text on "
      "the screen")
check("the long subtitles still elide",
      "elidedText" in SOURCE,
      "Configuration's runs under the header buttons and is drawn cut off "
      "mid-word without it")

print("\nThe menu rows are tall enough to hold themselves")
# The navitem rule pads 11px above and below 13px text. A row shorter than
# that cannot contain its own contents, and Qt drew the highlight of one
# entry over the text of the one above it — reported from a screenshot.
padding = int(re.search(r'padding: (\d+)px \d+px \d+px \d+px;\s*\n\s*color: \{C\[.text_secondary',
                        SOURCE).group(1))
nav_font = 13
height = int(re.search(r"btn\.setFixedHeight\((\d+)\)", SOURCE).group(1))
check(f"a {height}px row fits {padding}px + {nav_font}px + {padding}px",
      height >= padding * 2 + nav_font,
      f"needs at least {padding * 2 + nav_font}px — rows overlap below that")

print("\nThe status bar reports the server it is actually talking to")
check("the hardcoded 'Production Server' claim is gone",
      'QLabel("●  Connected to Production Server")' not in SOURCE,
      "a test build pointed at a laptop said it too")
# It lives in client.core.config now: the employee panel says the same thing
# and had its own wrong version of it ("● Connected to Server").
from client.core.config import server_label  # noqa: E402
from client.core import config as core_config  # noqa: E402

check("the label is built from the URL the app actually uses",
      "urlparse(API_BASE_URL)" in open(core_config.__file__.replace(".pyc", ".py"),
                                       encoding="utf-8").read())
# NO LONGER "STARTS WITH ●". The dot was a character out of the text font in
# a string the status bar draws; the bar colours itself, so the dot said
# nothing the colour did not. What matters is that the label names the host.
check("it returns something for the current configuration",
      bool(server_label().strip()), server_label())
check("localhost is named as local rather than dressed up",
      "local" in server_label() or "127.0.0.1" not in core_config.API_BASE_URL,
      server_label())
# Comments stripped: the note explaining what the old line said contains the
# old line, and a test that cannot tell code from a comment forbids writing
# the explanation down.
_emp = open(os.path.join(ROOT, "client", "presentation", "windows",
                         "employee_panel.py"), encoding="utf-8").read()
_emp_code = "\n".join(line for line in _emp.splitlines()
                      if not line.strip().startswith("#"))
check("and the employee panel no longer claims a server it cannot know",
      "Connected to Server" not in _emp_code)
check("its nav is grouped under headings too",
      '"MY WORK"' in _emp_code and '"ACCOUNT"' in _emp_code,
      "ten flat entries put Attendance next to Activity Logs with nothing "
      "to say they are different kinds of thing")

print("\nThe bell only lights when something is waiting")
check("it starts with nothing waiting",
      'setToolTip("Nothing is waiting.")' in SOURCE)
check("alerts are deliberately not counted",
      "Alerts are deliberately not counted" in SOURCE,
      "conditions that come and go would keep it permanently lit")
check("pressing it opens a page rather than doing nothing",
      "def _open_attention" in SOURCE)
# IT USED TO GUESS, and guessed wrong: a bell showing "1" opened Attendance
# when the reader expected Leave. A total cannot say which page it belongs to.
check("and it offers the choice instead of guessing",
      "menu.addAction(f\"{count}  {what}\")" in SOURCE,
      "picking the larger count is a guess dressed as a decision")
check("with nothing waiting it goes straight to Alerts",
      "if not waiting:" in SOURCE)
check("it polls on its own timer, not on every page change",
      "self._attention_timer.setInterval(60_000)" in SOURCE)
check("and a failed count is silent",
      "worker.error.connect(lambda _e: None)" in SOURCE,
      "an error in front of somebody doing something else is worse than a "
      "count that is briefly stale")

print("\nEvery shortcut goes where its label says")
# ALL FIVE QUICK ACTIONS OPENED THE WRONG PAGE. They held hard-coded indices
# into PAGES; a page was inserted at some point and the numbers stayed put,
# so each one opened its neighbour — "Employees" opened Configuration,
# "Audit Logs" opened Screenshots. Nothing raised and every button still
# landed somewhere plausible, which is exactly why nobody caught it.
quick = re.search(r"self\._quick_buttons = \{\}\s*\n\s*for icon, label, key in \((.*?)\):",
                  SOURCE, re.S)
check("the shortcuts are declared", quick is not None)
pairs = re.findall(r'\("[^"]+", "([^"]+)", "([^"]+)"\)', quick.group(1) if quick else "")
check("they are declared by page key, not by index",
      len(pairs) == 5, f"{len(pairs)} found — an integer here is the old bug")
titles = {p["key"]: p["title"] for p in PAGES}
for label, key in pairs:
    check(f"“{label}” → {titles.get(key, '???')}",
          key in titles and label.lower().replace(" ", "") ==
          titles[key].lower().replace(" ", "").replace("&", ""),
          f"key {key!r} is {titles.get(key)!r}")
check("and the key is resolved against PAGES at wiring time",
      'if page["key"] == page_key' in SOURCE)

print("\nThe dashboard's alert strip reads what the server sends")
# THE SERVER SENDS "HIGH", NOT "high". Measured against the running API. With
# a case-sensitive lookup every alert fell through to the default — sorted
# last and drawn as a blue "info" chip, critical ones included.
check("severity is lowercased before it is mapped",
      'str(a.get("severity") or "").lower()' in SOURCE
      and 'str(alert.get("severity") or "info").lower()' in SOURCE,
      "the server shouts its severities")
check("it shows the three most severe, not the three most recent",
      "order.get(" in SOURCE and "[:3]" in SOURCE)
check("an empty list says so rather than leaving a blank box",
      "Nothing needs attention right now." in SOURCE,
      "nothing and failed-to-load look identical when a panel is just empty")
check("and it says when alerts are switched off entirely",
      "Alerts are switched off in Configuration." in SOURCE)

print("\nThe two green cards say what makes them different")
check("Online Now explains itself",
      'self._card_online.set_subtitle("signed in on a device")' in SOURCE,
      "beside 'Active Now' with no subtitle, one of them looks wrong")

print("\n_section_of answers for every page")
for page in PAGES:
    name = panel._section_of(page["key"])
    check(f"{page['key']} → {name or 'OTHER'}", isinstance(name, str))


print("\nNo page is wider than the window it lives in")
# THE FAULT: one section description was a single unbreakable line asking for
# 1195px. The Configuration tab therefore wanted 1331px inside a 1160px
# viewport, grew a horizontal scrollbar, and pushed every value box off the
# right-hand edge — the settings were readable but not reachable.
#
# 1160 is the content width of a 1440px window beside a 270px sidebar, which
# is a normal laptop. Anything that overflows THAT overflows for everybody.
from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _stop_workers(widget):
    """Wait for the tab's threads before dropping it.

    A QThread still running when its object is destroyed aborts the process —
    Qt says so and then calls abort(). The tabs each start fetches on
    construction, so a test that builds one and exits takes the interpreter
    down with it: the checks all print PASS, the runner records a failure,
    and macOS raises a "Python quit unexpectedly" dialog with no connection
    to anything the reader did.
    """
    # BOTH PLACES. The panel's workers are QThreads with NO PARENT — they are
    # kept alive by a plain list on the tab (_track_worker), so findChildren
    # cannot see them and waiting only on children leaves them running.
    pending = list(widget.findChildren(QThread))
    pending += list(getattr(widget, "_workers", []))
    for worker in pending:
        try:
            worker.quit()
            worker.wait(6000)
        except RuntimeError:
            # Already collected — nothing to wait for.
            pass
    _app.processEvents()
for tab_name in ("_ConfigTab", "_AlertsTab", "_ReportsTab"):
    klass = getattr(panel, tab_name, None)
    if klass is None:
        continue
    try:
        tab = klass()
    except Exception as error:            # noqa: BLE001
        check(f"{tab_name} builds", False, repr(error))
        continue
    tab.setStyleSheet(panel._global_stylesheet())
    tab.resize(1160, 800)
    tab.show()
    _app.processEvents()
    scroll = tab.findChild(QScrollArea)
    if scroll is None:
        check(f"{tab_name} fits its width", tab.sizeHint().width() <= 1160,
              f"wants {tab.sizeHint().width()}px")
    else:
        wanted = scroll.widget().sizeHint().width()
        check(f"{tab_name} fits its width",
              wanted <= scroll.viewport().width(),
              f"wants {wanted}px in {scroll.viewport().width()}px — a "
              f"horizontal scrollbar hides the controls on the right")
    tab.hide()
    _stop_workers(tab)

print("\nall shell layout checks passed" if failures == 0 else f"\n{failures} FAILED")

# LEAVING THE WAY THE APPLICATION DOES, AND FOR THE SAME REASON.
#
# These tabs start blocking fetches on construction. A blocking request
# cannot be interrupted — quit() speaks to an event loop and there is none
# inside a socket read — so if one is still in flight when the interpreter
# tears Qt down, Qt destroys a running QThread and aborts the process.
#
# What that looked like: every check printed PASS, the runner recorded a
# failure (exit 134), and macOS raised "Python quit unexpectedly" with
# nothing in it connected to anything the reader had done. Measured: five
# runs, five aborts, after the API port was closed and the fetches began
# hanging. main.py ends the same way, deliberately — see the note there.
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if failures == 0 else 1)
