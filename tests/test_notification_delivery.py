"""The notification path: which door it goes through, and whose icon it wears."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")
failures = 0
def check(label, ok, detail=""):
    global failures
    if not ok: failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))

src = open("client/application/services/notifier.py", encoding="utf-8").read()
print("\nHow a notification reaches the screen")
check("the tray message carries the tray's own icon",
      "tray.showMessage(title, body, badge, 6000)" in src
      and 'getattr(tray, "icon", None)' in src,
      "a balloon on Windows draws whatever the tray holds — it was a plain "
      "coloured square — and asking for it must not raise on a tray that "
      "has no icon(), which turns the notification into silence")
check("a bundled macOS app counts as having shown it",
      "shown = (not mac) or bundled" in src)
check("and osascript only runs when Qt could not",
      "if mac and not shown:" in src,
      "otherwise the same message appears twice, once with each icon")
check("the bundle test looks for a real .app",
      '".app/Contents/" in sys.executable' in src)

print("\nThe tray icon is the brand, not a colour swatch")
tray_src = open("client/presentation/tray/system_tray.py", encoding="utf-8").read()
check("it draws the brand mark",
      "from client.presentation.widgets.brand import RING, mark_pixmap" in tray_src)
check("with the status as a dot on it", "drawEllipse" in tray_src)

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from client.presentation.tray.system_tray import _color_icon
for colour in ("#22c55e", "#f59e0b", "#ef4444"):
    pm = _color_icon(colour).pixmap(18, 18)
    check(f"{colour} renders", not pm.isNull() and pm.width() > 0)

print("\nThe icon files exist and came from the mark")
import os.path
for name in ("icon.png", "icon.ico", "icon.icns"):
    path = os.path.join("assets", name)
    check(f"{name} exists", os.path.exists(path) and os.path.getsize(path) > 1000,
          f"{os.path.getsize(path) if os.path.exists(path) else 0} bytes")
maker = open("assets/make_icon.py", encoding="utf-8").read()
check("the icon is rendered from the shared brand SVG",
      "from client.presentation.widgets import brand" in maker,
      "a second drawing of the logo drifts from the first")

print("\nAn announcement is said ONCE, not on every poll")
# THE BUG. /chat/updates returns every UNREAD notification, so a notification
# that is announced but never marked comes back three seconds later, and
# again, and again. Proven against the running server: two polls with no mark
# in between returned the same row twice; after marking it, zero. Reported as
# "announcement ka notification lagataar aaye ja raha hai, ruk nahi raha".
#
# The method to mark them already existed. Nothing called it — which is why
# this test asserts on the CALL and not on the method.
chat_src = open("client/application/managers/chat_manager.py", encoding="utf-8").read()
announce = chat_src.split("self.notifications.emit(alerts)")[1]
check("the poll marks what it just announced",
      "mark_notifications_read(fresh)" in announce,
      "unmarked notifications are re-announced every few seconds, forever")
check("only the ids it announced",
      "fresh = [n.get(\"id\") for n in alerts if n.get(\"id\")]" in chat_src,
      "an empty list tells the server to mark EVERYTHING read")
check("and a failure there does not kill the poll thread",
      "except Exception as error:" in announce.split("mark_notifications_read")[1])

print("\nA message arrives while the reader is on another page")
# The sender is watching the clock; the reader is on the dashboard. That case
# is INTERVAL_APP_OPEN, and at 15s it was reported as "10 sec baad ja raha
# hai, instant nahi". The send is immediate — the READ was slow.
import re
app_open = int(re.search(r"INTERVAL_APP_OPEN = (\d+)", chat_src).group(1))
check("the app-open poll is quick enough to feel instant",
      app_open <= 5, f"{app_open}s between polls")

print("\nA direct message raises the badge AS IT ARRIVES")
# THE BUG. An arriving message bumped the unread count by walking self._teams
# only. A direct message is not in that list, so nothing happened here and the
# count changed only when fetch_directs next ran — a 30-to-60 second timer.
# The badge did appear, ten to twenty seconds later, which reads as broken
# rather than slow: "raju ko message kiya to ye 1 se 2 nahi hua".
#
# Driven through the real page rather than read off the source, because the
# question is what the count DOES.
from client.presentation.windows.team_page import TeamPage
from PySide6.QtWidgets import QWidget

page = TeamPage.__new__(TeamPage)
QWidget.__init__(page)
page._teams = [{"name": "Development",
                "channels": [{"id": 10, "name": "General", "unread": 0}]}]
page._directs = [{"channel_id": 20, "name": "Ansh", "unread": 0}]
page._rows, page._messages = {}, []
page._channel = page._channel_id = None
page._searching = False
page._run = lambda *a, **k: None

counts = []
page.unread_changed.connect(counts.append)
page._on_messages([{"channel_id": 10, "seq": 1}])
check("a team message counts", counts[-1] == 1, f"got {counts[-1]}")
page._on_messages([{"channel_id": 20, "seq": 2}])
check("a DIRECT message counts too, on arrival", counts[-1] == 2,
      f"got {counts[-1]} — the DM waited for the slow refresh")
page._on_messages([{"channel_id": 20, "seq": 3}])
check("and keeps counting", counts[-1] == 3, f"got {counts[-1]}")

# The conversation ON SCREEN is not unread — the same rule, the other way
# round, and only while the page is actually showing. `_channel` keeps its
# value after the page is closed, which is how a DM once fell out of the
# badge for the rest of the session.
page._channel = {"id": 20}
page.isVisible = lambda: True
page._emit_unread()
check("the conversation on screen is excluded", counts[-1] == 1,
      f"got {counts[-1]} — only the team channel's 1 should be left")
page.isVisible = lambda: False
page._emit_unread()
check("but counted again once the page is closed", counts[-1] == 3,
      f"got {counts[-1]} — a closed page must not keep excluding it")

print("\nall notification checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
