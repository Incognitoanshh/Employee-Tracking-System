"""
The dark / light switch.

The failure this is written against is a PARTIAL theme: most of the window
turns light and a handful of widgets stay dark, usually the ones nobody looked
at while building it. That is easy to ship and miserable to chase, because
each straggler has to be found by eye.

So the checks here do not ask "did the palette change" — that is trivially
true. They ask whether any widget still carries a colour from the theme that
is no longer active, by walking the whole tree of a real panel and reading the
stylesheets back.

Run:  python3 tests/test_theme.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ISOLATED FROM ANYTHING REAL, BEFORE ANY CLIENT MODULE IS IMPORTED.
#
# Without these two the client's own config falls back to its defaults: the
# installed app's data directory, and the PRODUCTION server. Running this file
# on a machine that has the app installed then wrote into the real local
# database and uploaded to the real server — rows under a made-up employee id
# turned up in the company's audit log, which is exactly what happened.
#
# setdefault, so a harness that points these somewhere on purpose still wins.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_theme_test_")
import client.core.config as config
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

from PySide6.QtWidgets import QApplication, QWidget

app = QApplication.instance() or QApplication([])

from client.infrastructure.database.database import Database
from client.presentation import theme
from client.presentation.theme import C, ADMIN
from client.application.managers.session_manager import SessionManager

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


def stylesheets(root: QWidget) -> str:
    """Every stylesheet in a widget tree, concatenated."""
    parts = [root.styleSheet()]
    for child in root.findChildren(QWidget):
        parts.append(child.styleSheet())
    return "\n".join(parts).lower()


# Colours unique to one theme — the ones whose presence proves a widget was
# built under it. Shared values (pure white, the accents that appear in both)
# are excluded, or every check would trip on a legitimate match.
# DERIVED FROM THE PALETTES THEMSELVES, not typed out here.
#
# These were six hard-coded hex values from the palette as it stood the day
# the test was written. The design system was then rebuilt against a new
# brief and every one of them changed, so the test failed while the code was
# correct — the worst kind of failure, because the obvious fix is to weaken
# the test. Reading the palettes means the markers cannot go stale.
#
# Only opaque hex values are usable as markers: an rgba() wash appears in
# both palettes at different alphas and proves nothing.
def _hexes(palette):
    return {v.lower() for v in palette.values()
            if isinstance(v, str) and v.startswith("#") and len(v) == 7}

_dark_hexes = _hexes(theme._DARK) | _hexes(theme._ADMIN_DARK)
_light_hexes = _hexes(theme._LIGHT) | _hexes(theme._ADMIN_LIGHT)

DARK_ONLY = sorted(_dark_hexes - _light_hexes)
LIGHT_ONLY = sorted(_light_hexes - _dark_hexes)

if not DARK_ONLY or not LIGHT_ONLY:
    raise SystemExit("the two palettes share every colour; nothing to test")


def strays(text: str, colours: list) -> list:
    return [colour for colour in colours if colour in text]


def main():
    print("Dark and light\n")
    Database.initialize()

    # ── the palettes themselves ─────────────────────────────────────────
    print("The palettes")
    theme.set_theme("dark")
    dark_bg, dark_admin = C.BG, ADMIN["bg_app"]
    theme.set_theme("light")
    check("the employee palette moves", C.BG != dark_bg, f"{C.BG} vs {dark_bg}")
    check("and the admin one moves with it",
          ADMIN["bg_app"] != dark_admin, f"{ADMIN['bg_app']} vs {dark_admin}")

    # Compared against the palettes, not against two hex values typed here.
    # The literals went stale the moment the design system was rebuilt.
    check("light text is dark, and dark text is light",
          C.TEXT == theme._LIGHT["TEXT"], C.TEXT)
    theme.set_theme("dark")
    check("and back again", C.TEXT == theme._DARK["TEXT"], C.TEXT)

    # A dict rebound instead of mutated would leave admin_config_panel — which
    # binds `C = ADMIN` at import — pointing at the previous colours forever.
    from client.presentation.windows.admin_config_panel import C as ADMIN_C
    check("the admin console shares the very same dict, not a copy",
          ADMIN_C is ADMIN)
    theme.set_theme("light")
    check("so switching reaches it without re-importing anything",
          ADMIN_C["bg_app"] == theme._ADMIN_LIGHT["bg_app"], ADMIN_C["bg_app"])
    theme.set_theme("dark")

    # ── the trap that started this ──────────────────────────────────────
    print("\nDefaults evaluated at import")
    # `def scrollbar(bg=C.BG)` binds the default ONCE, when the module is
    # imported — so every scrollbar would keep the dark background for the
    # life of the process no matter how many times the theme changed.
    theme.set_theme("light")
    # Against the palettes rather than against literals, for the reason
    # written above DARK_ONLY.
    # NOT theme._DARK["BG"] as the marker. The light theme's TEXT is
    # #09090b and so is the dark theme's BG — near-black is near-black — so
    # that value proves nothing about which palette is in force. The derived
    # DARK_ONLY list holds only values that appear in one palette and not
    # the other, which is the whole point of computing it.
    _light_bg = theme._LIGHT["BG"]
    check("scrollbar() with no argument follows the current theme",
          strays([theme.scrollbar()], DARK_ONLY) == [], theme.scrollbar()[:70])
    check("and app_style() does too",
          strays([theme.app_style()], DARK_ONLY) == []
          and _light_bg in theme.app_style(),
          str(strays([theme.app_style()], DARK_ONLY)))
    theme.set_theme("dark")
    check("in the other direction as well",
          strays([theme.scrollbar()], LIGHT_ONLY) == [],
          str(strays([theme.scrollbar()], LIGHT_ONLY)))

    # ── a real panel, walked end to end ─────────────────────────────────
    print("\nA whole panel, widget by widget")
    SessionManager.employee_id = "E001"
    SessionManager.full_name = "Rajesh Kumar"
    SessionManager.role = "employee"
    SessionManager.auth_token = "not-a-real-token"

    theme.set_theme("dark")
    from client.presentation.windows.employee_panel import EmployeePanel

    panel = EmployeePanel()
    sheets = stylesheets(panel)
    check("the panel builds dark", strays(sheets, DARK_ONLY) != [],
          "no dark colours found at all")
    check("with nothing light in it",
          strays(sheets, LIGHT_ONLY) == [], str(strays(sheets, LIGHT_ONLY)))

    panel._toggle_theme()
    sheets = stylesheets(panel)

    # BEFORE the colours: is anything actually on screen?
    #
    # The first version of this test only read stylesheets, and passed while
    # the panel was completely blank. Qt refuses to install a second layout on
    # a widget that still has one, and deleteLater() does not remove the old
    # one in time — so every widget was rebuilt in the right colour, parented,
    # and never placed. The warning went to the console; the test saw perfect
    # colours on an empty window.
    layout = panel.layout()
    check("the panel still has a layout with something in it",
          layout is not None and layout.count() > 0,
          f"{layout.count() if layout else 'no layout'} items — a blank window")
    check("and every page is still in the stack",
          panel._stack.count() == len(panel.pages),
          f"{panel._stack.count()} of {len(panel.pages)}")

    check("after the switch every page is light",
          strays(sheets, LIGHT_ONLY) != [], "no light colours found")
    # The one that matters: a single widget left behind is the whole bug.
    left = strays(sheets, DARK_ONLY)
    check("and NOT ONE widget is still dark", left == [], str(left))

    panel._toggle_theme()
    sheets = stylesheets(panel)
    check("switching back leaves nothing light behind",
          strays(sheets, LIGHT_ONLY) == [], str(strays(sheets, LIGHT_ONLY)))
    check("and the window is still laid out after a second switch",
          panel.layout() is not None and panel.layout().count() > 0
          and panel._stack.count() == len(panel.pages),
          f"{panel.layout().count() if panel.layout() else 0} items, "
          f"{panel._stack.count()} pages")

    # BY ICON NAME, NOT BY GLYPH. It was ☾ / ☀ — emoji, drawn by the system
    # font, and removed with the rest of them. The name the button asked for
    # is a better thing to assert than the character it happened to contain.
    check("the button offers the theme you are not on",
          panel._theme_icon == "sun", panel._theme_icon)
    panel._toggle_theme()
    check("and flips when you take it", panel._theme_icon == "moon",
          panel._theme_icon)
    panel._toggle_theme()

    print("\nThe chat survives the switch")
    # REPORTED FROM USE: a conversation was open, the theme was switched, and
    # the chat closed — with a half-typed message in it. The page was already
    # preserved; what was inside it was not, and the two things a person
    # notices losing are the open channel and the draft.
    chat_page = panel.pages.get("team")
    if chat_page is not None and hasattr(chat_page, "snapshot"):
        chat_page._channel_id = 4242
        chat_page._composer.setPlainText("half typed, not sent")
        panel._toggle_theme()
        after = panel.pages.get("team")
        check("the channel that was open is open again",
              after._channel_id == 4242, str(after._channel_id))
        check("and the draft is still there",
              after._composer.toPlainText() == "half typed, not sent",
              repr(after._composer.toPlainText()))
        panel._toggle_theme()
    else:
        check("the chat page exposes snapshot/restore", False,
              "without them a theme switch discards the conversation")

    # ── what a rebuild must not break ───────────────────────────────────
    print("\nWhat survives the rebuild")
    chat_before = panel.chat
    pages_before = set(panel.pages)
    panel.go("team")
    panel._toggle_theme()
    check("the chat connection is the SAME object, not a second one",
          panel.chat is chat_before,
          "a new ChatManager would poll alongside the old one")
    check("every page is back", set(panel.pages) == pages_before,
          str(set(panel.pages) ^ pages_before))
    check("and the page you were on is still the one showing",
          panel._stack.currentWidget() is panel.pages["team"])
    check("the navigation follows it",
          panel._nav["team"].isChecked())

    # The cards owned by timers must be repainted at once, not left blank
    # until the next tick — fifteen seconds of an empty Internet Status card
    # reads as the switch having broken something.
    painted = []
    panel._check_network = lambda: painted.append(1)
    panel.go("dashboard")
    panel._toggle_theme()
    check("timer-driven cards are repainted immediately after a switch",
          painted != [], "the panel would sit blank until the next tick")
    panel._toggle_theme()

    panel._teardown_pages()
    panel.deleteLater()

    # ── the admin console, the same way ─────────────────────────────────
    print("\nThe admin console, widget by widget")
    SessionManager.role = "super_admin"
    theme.set_theme("dark")
    from client.presentation.windows.admin_config_panel import AdminConfigPanel

    console = AdminConfigPanel()
    # It starts a scheduler and an idle tracker of its own; neither has
    # anything to do with the theme and both would keep running under the
    # test.
    console._stop_background_services()

    sheets = stylesheets(console)
    check("the console builds dark", strays(sheets, DARK_ONLY) != [],
          "no dark colours found at all")
    check("with nothing light in it",
          strays(sheets, LIGHT_ONLY) == [], str(strays(sheets, LIGHT_ONLY)))

    console.sidebar.select(5)                      # Teams & Chat
    console._toggle_theme()
    sheets = stylesheets(console)
    check("after the switch the whole console is light",
          strays(sheets, LIGHT_ONLY) != [], "no light colours found")
    left = strays(sheets, DARK_ONLY)
    check("and NOT ONE widget is still dark", left == [], str(left))
    check("the page you were on is still the one showing",
          console.stack.currentIndex() == 5, str(console.stack.currentIndex()))
    # The worst possible outcome of a cosmetic change: tracking stops.
    #
    # _toggle_theme used to call _stop_background_services(), which also stops
    # the scheduler and the idle tracker — and nothing starts them again. In a
    # product whose entire job is tracking, switching the theme ended it for
    # the rest of the session, leaving one line in the audit log and a
    # dashboard still reporting itself healthy.
    stopped = []
    class _Watch:
        def __init__(self, name): self.name = name
        def stop(self): stopped.append(self.name)
        def deleteLater(self): pass
    console.scheduler = _Watch("scheduler")
    console.idle_tracker = _Watch("idle_tracker")
    console._toggle_theme()
    check("switching the theme does NOT stop tracking",
          stopped == [], f"stopped: {stopped}")
    check("and the activity card is redrawn rather than left blank",
          getattr(console, "_own_idle_status", None) is not None)
    # Toggled back, so the checks below still know which theme they are in.
    console._toggle_theme()
    check("nor on the way back", stopped == [], f"stopped: {stopped}")

    check("and the console is actually laid out, not just correctly coloured",
          console.centralWidget() is not None
          and console.centralWidget().layout() is not None
          and console.centralWidget().layout().count() > 0
          and console.stack.count() == len(AdminConfigPanel.TAB_ATTRS),
          f"{console.stack.count()} tabs in the stack")

    check("every tab was rebuilt, none lost",
          all(getattr(console, attr, None) is not None
              for attr in AdminConfigPanel.TAB_ATTRS),
          str([a for a in AdminConfigPanel.TAB_ATTRS
               if getattr(console, a, None) is None]))

    # A rebuilt header that re-attached its old signals as well as its new
    # ones would fire every action twice — two requests per Refresh.
    fired = []
    console._refresh_current_page = lambda: fired.append("refresh")
    console.header.refresh_clicked.disconnect()
    console.header.refresh_clicked.connect(console._refresh_current_page)
    console.header.refresh_clicked.emit()
    check("Refresh fires once, not once per theme switch",
          len(fired) == 1, f"{len(fired)} times")

    console._toggle_theme()
    sheets = stylesheets(console)
    check("switching back leaves nothing light behind",
          strays(sheets, LIGHT_ONLY) == [], str(strays(sheets, LIGHT_ONLY)))

    console._stop_background_services()
    console.deleteLater()

    # ── the small windows and the dialogs ───────────────────────────────
    print("\nEvery other window, and the dialogs")
    # "small page box sab dark to dark, light to light" — these are where a
    # missed colour hides, because nobody opens them while building a theme.
    from client.presentation.windows.login_window import LoginWindow
    from client.presentation.windows.settings_window import SettingsWindow
    from client.presentation.windows.change_password_dialog import ChangePasswordDialog
    from client.presentation.windows import admin_teams_tab as att

    TEAM = {"id": 1, "name": "Development", "description": "", "is_archived": False,
            "archived_reason": None}
    CHANNELS = [
        {"id": 1, "name": "General", "type": "STANDARD", "is_default": True,
         "is_private": False, "message_count": 0, "member_count": 0},
        {"id": 2, "name": "Backend", "type": "STANDARD", "is_default": False,
         "is_private": False, "message_count": 0, "member_count": 1},
    ]
    MEMBERS = [{"employee_id": "E001", "name": "Rajesh Kumar", "username": "emp1",
                "role": "employee", "channel_ids": [2]}]

    def small_windows():
        """One of each, freshly built under whatever theme is current."""
        made = [LoginWindow(), SettingsWindow(), ChangePasswordDialog(None)]
        made.append(att._NewTeamDialog(None, MEMBERS))
        made.append(att._NewChannelDialog(None, TEAM, MEMBERS))
        made.append(att._ArchiveDialog(None, TEAM))
        made.append(att._AnnounceDialog(None, CHANNELS[0]))
        made.append(att._ViewChatDialog(None, CHANNELS[0]))
        made.append(att._TranscriptDialog(None, {
            "channel": {"id": 1, "name": "General", "team_name": "Development"},
            "messages": [{"seq": 1, "sender_name": "Rajesh Kumar", "former": False,
                          "body": "hi", "created_at": "2026-08-07T09:00:00Z",
                          "edit_count": 0}],
            "edit_history": []}))
        return made

    for name, wrong_list in (("dark", LIGHT_ONLY), ("light", DARK_ONLY)):
        theme.set_theme(name)
        offenders = []
        for window in small_windows():
            found = strays(stylesheets(window), wrong_list)
            if found:
                offenders.append(f"{type(window).__name__}{found}")
            window.deleteLater()
        check(f"in {name}, no small window or dialog carries the other theme",
              offenders == [], "; ".join(offenders))

    theme.set_theme("dark")

    # ── it is remembered ────────────────────────────────────────────────
    print("\nRemembering the choice")
    theme.save_theme("light")
    from client.services.settings_service import SettingsService
    check("the choice is written down",
          SettingsService.get_setting("ui_theme", "") == "light",
          SettingsService.get_setting("ui_theme", ""))
    theme.set_theme("dark")
    check("and read back on the next start",
          theme.load_saved_theme() == "light", theme.current_theme())

    theme.save_theme("dark")
    check("a value nobody recognises falls back to dark rather than crashing",
          theme.set_theme("chartreuse") == "dark")

    print()
    if failures:
        print(f"{failures} failure(s)")
    else:
        print("all theme checks passed")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if failures else 0)


if __name__ == "__main__":
    main()
