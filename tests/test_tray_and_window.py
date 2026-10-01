"""
The tray, and what closing the window does.

WHY THIS FILE EXISTS. Everything the application does when somebody uses the
tray or the window's own buttons had no test, because those look like things
only a person at a desk can do. The click is — macOS draws the menu bar extra
and the three dots at the corner, and delivering a click to them needs
assistive access that a build machine does not have. What they are WIRED TO is
ordinary code: a QSystemTrayIcon this application builds with actions it
defines, and window-state changes and a closeEvent it handles. That is the half
a bug lives in, and it is all driven here.

THE ONE THAT MATTERS MOST. Closing the window must not stop the monitoring.
This is a tracking client: if the close button signed somebody out, closing a
window would be how you stop being tracked, and the product would be
decorative. So the window hides to the tray, the session stays, and the tray
brings it back.

Run:  python3 tests/test_tray_and_window.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_tray_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_tray_db_")
import client.core.config as config                                          # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module       # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")
database_module.Database.initialize()

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


from PySide6.QtCore import QElapsedTimer                                     # noqa: E402
from PySide6.QtTest import QTest                                             # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget         # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms=300):
    clock = QElapsedTimer()
    clock.start()
    while clock.elapsed() < ms:
        app.processEvents()
        QTest.qWait(20)


print("\nThe tray this application builds\n")

from client.presentation.tray.system_tray import SystemTray                  # noqa: E402

holder = QWidget()
holder.resize(900, 600)
holder.show()
tray = SystemTray(holder)
tray.show()
pump(200)

check("it is a system tray icon, not something drawn inside the window",
      isinstance(tray, QSystemTrayIcon), type(tray).__name__)

menu = tray.contextMenu()
items = [action.text() for action in (menu.actions() if menu else []) if action.text()]
check("with a menu on it", bool(menu) and len(items) >= 4, str(items))
# THE WAY OUT HAS TO BE THERE. A tray icon with no Exit is an application
# somebody has to kill from Activity Monitor.
check("and a way to leave the application in it",
      any("exit" in text.lower() or "quit" in text.lower() for text in items),
      str(items))
check("each item is named in words, not in an icon alone",
      all(len(text.strip()) > 2 for text in items), str(items))

# ── EVERY ITEM PUTS SOMETHING ON SCREEN ───────────────────────────────────
#
# trigger() is what macOS delivers when the item is clicked: same signal, same
# slot, same consequence. An item that silently does nothing is an item nobody
# presses twice — and the tray is used precisely when the window is hidden, so
# each one has to be able to bring something up from nothing.
for wanted in ("Open Dashboard", "View Logs", "Settings"):
    action = next((a for a in menu.actions() if wanted in a.text()), None)
    if action is None:
        check(f"the tray offers {wanted!r}", False, str(items))
        continue
    holder.hide()
    pump(150)
    before = {w for w in app.topLevelWidgets() if w.isVisible()}
    action.trigger()
    pump(600)
    after = {w for w in app.topLevelWidgets() if w.isVisible()}
    appeared = after - before
    check(f"{wanted!r} puts something on screen from a hidden state",
          holder.isVisible() or bool(appeared),
          f"holder={holder.isVisible()}, new={sorted(type(w).__name__ for w in appeared)}")
    for extra in appeared:
        if extra is not holder:
            extra.close()
    pump(150)

print("\nWhat the window's own buttons do")

holder.show()
pump(200)

holder.showMinimized()
pump(300)
check("minimising is accepted", holder.isMinimized(), str(holder.windowState()))
holder.showNormal()
pump(300)
check("and it comes back", not holder.isMinimized() and holder.isVisible())

holder.showMaximized()
pump(300)
check("maximising is accepted", holder.isMaximized(), str(holder.windowState()))
holder.showNormal()
pump(200)

tray.hide()
holder.close()
pump(100)

# ── AND THE CLOSE BUTTON, ON THE REAL PANEL ───────────────────────────────
#
# The panel's own closeEvent, which is where the decision lives.
print("\nClosing the panel window — the one that must not stop the monitoring")

from client.application.managers.session_manager import SessionManager       # noqa: E402

SessionManager.employee_id = "E001"
SessionManager.auth_token = "not-a-real-token"
SessionManager.role = "employee"
SessionManager.is_authenticated = True

from client.presentation.windows.employee_panel import EmployeePanel         # noqa: E402

panel = EmployeePanel()
panel.resize(1100, 740)
panel.show()
pump(1200)

check("the panel opens with its pages", bool(getattr(panel, "pages", None)),
      str(list((getattr(panel, "pages", {}) or {}).keys()))[:80])
check("and it carries its own tray", isinstance(getattr(panel, "tray", None), QSystemTrayIcon),
      type(getattr(panel, "tray", None)).__name__)

# THE EVENT ITSELF, which is what macOS delivers when the red dot is pressed.
#
# Checking only that the window ended up hidden proves nothing: a close that
# is ACCEPTED also hides it, and then the application is on its way out —
# tracking stopped, session abandoned — while the test goes on passing. What
# distinguishes the two is whether the panel refuses the event.
from PySide6.QtGui import QCloseEvent                                        # noqa: E402

ordinary = QCloseEvent()
panel.closeEvent(ordinary)
pump(200)
check("an ordinary close is REFUSED, not accepted",
      not ordinary.isAccepted(), f"accepted={ordinary.isAccepted()}")
check("and the window is hidden instead",
      not panel.isVisible(), f"visible={panel.isVisible()}")
check("the person is still signed in afterwards",
      SessionManager.is_authenticated and bool(SessionManager.auth_token),
      f"authenticated={SessionManager.is_authenticated}")
check("and the pages are all still there, so it can be brought back",
      bool(getattr(panel, "pages", None)),
      str(len(getattr(panel, "pages", {}) or {})))

# AND THE TRAY BRINGS IT BACK, which is the other half of hiding it.
tray_menu = panel.tray.contextMenu() if getattr(panel, "tray", None) else None
open_item = next((a for a in (tray_menu.actions() if tray_menu else [])
                  if "Open" in a.text()), None)
if open_item is not None:
    open_item.trigger()
    pump(600)
    check("and the tray's Open brings the window back", panel.isVisible())
else:
    check("the tray has an Open item to bring it back", False,
          str([a.text() for a in (tray_menu.actions() if tray_menu else [])]))

# A REAL CLOSE IS STILL POSSIBLE, for the sign-out path that has to end it —
# and the difference is again in the event, not in what is on screen.
panel._force_close = True
deliberate = QCloseEvent()
panel.closeEvent(deliberate)
pump(200)
check("while a deliberate close — the one sign-out uses — IS accepted",
      deliberate.isAccepted(), f"accepted={deliberate.isAccepted()}")
panel.close()
pump(200)

print("\nWhat a build machine cannot do, and is not claimed")
print("  BLOCKED  the click on the menu bar extra, and the press on the window's")
print("           own three dots: macOS draws and delivers those, and reaching")
print("           them needs assistive access this process does not have.")

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
