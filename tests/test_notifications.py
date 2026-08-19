"""
What is worth interrupting somebody for.

Showing a notification is three lines of Qt. Deciding whether to is the part
that makes this feature either useful or the first thing everybody turns off —
and once it is off, the message that actually mattered is missed too. So the
deciding lives in application/services/notifier as plain functions, and this
exercises it against the situations that matter rather than the code.

Run:  python3 tests/test_notifications.py
"""

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

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


ME = "E001"
NAMES = {10: "#General", 11: "#Backend", 50: "Sneha Iyer"}
DIRECTS = {50}


def message(**kw):
    base = {
        "channel_id": 10, "sender_id": "E002", "sender_name": "Amit Sharma",
        "body": "kal ka report bhej dena", "mentions_me": False,
        "attachments": [], "deleted": False,
    }
    base.update(kw)
    return base


def shown(messages, **kw):
    from client.application.services import notifier
    options = {"me": ME, "channel_names": NAMES, "direct_channel_ids": DIRECTS}
    options.update(kw)
    return notifier.for_messages(messages, **options)


def main():
    from client.application.services import notifier

    print("A message in a channel")
    out = shown([message()])
    check("is announced", len(out) == 1, str(out))
    check("saying who it was from and where",
          out[0]["title"] == "Amit Sharma in #General", out[0]["title"])
    check("with what they said",
          out[0]["body"] == "kal ka report bhej dena", out[0]["body"])
    check("quietly — a busy channel must not beep all day",
          out[0]["kind"] == notifier.NORMAL, out[0]["kind"])

    print("\nA message to you personally")
    out = shown([message(channel_id=50, sender_name="Sneha Iyer",
                         body="bhai ek kaam tha")])
    check("says so in those words",
          out[0]["title"] == "Sneha Iyer messaged you", out[0]["title"])
    check("and is urgent — somebody wrote to YOU",
          out[0]["kind"] == notifier.URGENT, out[0]["kind"])

    print("\nBeing named in a channel")
    out = shown([message(mentions_me=True, body="@rajesh ye dekh lena")])
    check("reads as being mentioned, not as ordinary traffic",
          out[0]["title"] == "Amit Sharma mentioned you in #General",
          out[0]["title"])
    check("and is urgent too — it is addressed to one person",
          out[0]["kind"] == notifier.URGENT, out[0]["kind"])

    print("\nWhat must NOT interrupt")
    out = shown([message(sender_id=ME, sender_name="Rajesh Kumar")])
    check("your own message", out == [], str(out))

    out = shown([message(deleted=True, body="")])
    check("somebody withdrawing one", out == [], str(out))

    print("\nWhat DOES interrupt, by the owner's decision")
    # This was the other way round until somebody sat with a chat open,
    # watched messages arrive and got nothing — and could not tell a working
    # notification from a broken one. Asked for directly: every message is
    # announced, the open conversation included.
    out = shown([message()], open_channel_id=10, window_active=True)
    check("even the conversation you are looking at", len(out) == 1, str(out))

    out = shown([message()], open_channel_id=10, window_active=False)
    check("and the same conversation behind another window",
          len(out) == 1, str(out))

    out = shown([message(channel_id=11)], open_channel_id=10, window_active=True)
    check("and a different channel always counts", len(out) == 1, str(out))

    print("\nMessages with no words")
    out = shown([message(body="", attachments=[{"id": 1}])])
    check("a photograph says so", out[0]["body"] == "Sent a photo", out[0]["body"])
    out = shown([message(body="", attachments=[{"id": 1}, {"id": 2}])])
    check("and several files say how many",
          out[0]["body"] == "Sent 2 files", out[0]["body"])

    long_line = "x" * 400
    out = shown([message(body=long_line)])
    check("a very long message is trimmed, not pasted whole into a popup",
          len(out[0]["body"]) < 130 and out[0]["body"].endswith("…"),
          str(len(out[0]["body"])))

    print("\nComing back after being offline")
    # The failure this prevents: a client that has been away returns to forty
    # messages and fires forty popups, which somebody dismisses without
    # reading — worse than silence, because the one that mattered went with
    # them.
    many = [message(body=f"line {i}") for i in range(40)]
    out = notifier.collapse(shown(many))
    check("at most a handful appear", len(out) == 4, str(len(out)))
    check("and the last one says how many more",
          "37 more messages" in out[-1]["title"], out[-1]["title"])
    check("without pretending to be from somebody",
          out[-1]["channel_id"] is None, str(out[-1]))

    out = notifier.collapse(shown([message()]))
    check("a single message is left exactly as it is", len(out) == 1, str(out))

    print("\nAnnouncements and administrative alerts")
    announcement = {"type": "ANNOUNCEMENT", "team_name": "Development",
                    "channel_name": "Company Updates", "channel_id": 3}
    out = notifier.for_alerts([announcement], role="employee")
    check("an announcement reaches everybody", len(out) == 1, str(out))
    check("naming where it was posted",
          "Company Updates" in out[0]["title"], out[0]["title"])

    stopped = {"type": "NOT_REPORTING", "title": "No data for 3 d",
               "detail": "The app has sent nothing."}
    out = notifier.for_alerts([stopped], role="employee")
    check("an employee is NOT interrupted by an administrator's problem",
          out == [],
          "'EM103 has stopped reporting' is not an employee's business")

    out = notifier.for_alerts([stopped], role="admin")
    check("an admin is", len(out) == 1, str(out))
    check("with what is wrong", "No data for 3 d" in out[0]["title"], out[0]["title"])
    out = notifier.for_alerts([stopped], role="super_admin")
    check("and so is a super admin", len(out) == 1, str(out))

    out = notifier.for_alerts([{"type": "MENTION", "channel_id": 10}], role="employee")
    check("a mention is not announced twice — the message itself already did",
          out == [], str(out))

    print("\nFrom an arriving message to the tray")
    # The decisions above are only half of it. This is the wiring: a message
    # arriving on the poll has to reach showMessage with the right words, and
    # the sound has to fire for what is addressed to this person and not for
    # everything else. Both panels are driven, because a rule that only one of
    # them applies is a rule half the company does not get.
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _os.environ.setdefault("SCREENSHOT_ENCRYPTION_KEY",
                           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from client.presentation.windows import employee_panel as ep
    from client.presentation.windows import admin_config_panel as acp

    class _FakeTray:
        """Shaped like the real QSystemTrayIcon, including icon().

        It had showMessage and nothing else. The delivery path now asks the
        tray for its icon — so a balloon carries the brand mark rather than a
        blank — and against this stub that raised, was swallowed by the
        catch, and every notification silently vanished. The stub has to
        answer the same calls the real object does, or it tests a different
        program.
        """
        def __init__(self):
            self.shown = []

        def icon(self):
            from PySide6.QtGui import QIcon
            return QIcon()

        def showMessage(self, title, body, icon=None, msecs=None):
            self.shown.append((title, body))

    beeps = []

    for label, panel_module, method, page_attr in (
            ("employee panel", ep, "_on_chat_messages", "pages"),
            ("admin panel", acp, "_on_chat_messages", None)):
        cls = (panel_module.EmployeePanel if page_attr
               else panel_module.AdminConfigPanel)
        panel = cls.__new__(cls)          # no window, no network, no login
        tray = _FakeTray()
        panel.tray = tray

        # The smallest surface each method actually reads.
        class _Chat:
            _channel_id = 999
            _teams = [{"channels": [{"id": 10, "name": "General"}]}]
            _directs = [{"channel_id": 50,
                         "with": {"employee_id": "E002", "name": "Sneha Iyer"}}]

            def refresh(self):
                pass

        chat_page = _Chat()
        if page_attr:
            panel.pages = {"team": chat_page}
            panel._stack = type("S", (), {"currentWidget": lambda self: None})()
        else:
            panel._mychat_tab = chat_page
            panel.stack = type("S", (), {"currentWidget": lambda self: None})()
        panel.isActiveWindow = lambda: False

        real_beep = panel_module.SessionManager
        panel_module.SessionManager.employee_id = ME

        import PySide6.QtWidgets as _qt
        real_qapp_beep = _qt.QApplication.beep
        _qt.QApplication.beep = staticmethod(lambda: beeps.append(label))
        try:
            getattr(panel, method)([
                message(channel_id=50, sender_name="Sneha Iyer",
                        body="bhai ek kaam tha"),
                message(channel_id=10, sender_name="Amit Sharma"),
            ])
        finally:
            _qt.QApplication.beep = real_qapp_beep

        titles = [t for t, _b in tray.shown]
        check(f"{label}: the tray is actually told", len(tray.shown) == 2,
              f"{len(tray.shown)} shown — the wiring is there but silent")
        check(f"{label}: a direct message reads as personal",
              any("Sneha Iyer messaged you" == t for t in titles), str(titles))
        check(f"{label}: a channel message names the channel",
              any("Amit Sharma in #General" == t for t in titles), str(titles))
        check(f"{label}: the words come through too",
              any("bhai ek kaam tha" in b for _t, b in tray.shown),
              str(tray.shown))

    # The owner's decision: everything makes a sound, group messages and
    # administrative alerts included — not only what names you. Two messages
    # were delivered to each panel, so two sounds each.
    #
    # On macOS the sound comes from AppleScript instead (below), so beeping
    # here as well would be two sounds for one message.
    if sys.platform != "darwin":
        check("a sound fires for EVERY notification, on both panels",
              beeps.count("employee panel") == 2
              and beeps.count("admin panel") == 2,
              f"{beeps} — expected two each, one per message shown")

    print("\nOn macOS, where the tray is silently ignored")
    # REPORTED FROM A REAL MACHINE: "windows pe notification aa rha hai but
    # mac pe ni". Qt's showMessage needs a signed, permitted bundle on macOS;
    # ours is neither, so it returns cleanly and nothing appears. This drives
    # the delivery as macOS to prove the second door is taken.
    import subprocess as _sp
    real_platform = sys.platform
    real_run = _sp.run
    ran, kwargs = [], []
    sys.platform = "darwin"

    def _fake_run(cmd, **kw):
        ran.append(cmd)
        kwargs.append(kw)
        return type("R", (), {"returncode": 0})()

    _sp.run = _fake_run
    try:
        tray = _FakeTray()
        ok = notifier.deliver(tray, 'Sneha "S" Iyer messaged you',
                              'line one\nline "two"')
    finally:
        sys.platform = real_platform
        _sp.run = real_run

    check("something is actually shown", ok is True, str(ok))
    check("through osascript, not the tray",
          bool(ran) and ran[0][0] == "osascript", str(ran))
    script = ran[0][-1] if ran else ""
    check("as a notification, with the title and the words",
          "display notification" in script
          and "messaged you" in script and "line one" in script, script)
    check("carrying a sound of its own", 'sound name "Ping"' in script, script)
    check("and quotes in somebody's message do not break the script",
          script.count('\\"') == 4 and script.count("\n") == 0, script)
    check("with a timeout — a stuck osascript must not stall the poll",
          bool(kwargs) and kwargs[0].get("timeout"), str(kwargs))

    print("\nAnd with no tray at all on macOS")
    sys.platform = "darwin"
    _sp.run = _fake_run
    try:
        ok = notifier.deliver(None, "Title", "Body")
    finally:
        sys.platform = real_platform
        _sp.run = real_run
    check("it still gets through — macOS never needed the tray", ok is True,
          str(ok))

    print("\nAdministrative alerts make a sound too")
    panel = ep.EmployeePanel.__new__(ep.EmployeePanel)
    tray = _FakeTray()
    panel.tray = tray
    ep.SessionManager.role = "admin"
    import PySide6.QtWidgets as _qt
    alert_sounds = []
    real_beep = _qt.QApplication.beep
    _qt.QApplication.beep = staticmethod(lambda: alert_sounds.append("beep"))
    real_run2 = _sp.run
    _sp.run = lambda cmd, **kw: alert_sounds.append("osascript") or type(
        "R", (), {"returncode": 0})()
    try:
        panel._on_chat_notifications([
            {"type": "NOT_REPORTING", "title": "No data for 3 d",
             "detail": "The app has sent nothing."}])
    finally:
        _qt.QApplication.beep = real_beep
        _sp.run = real_run2
        ep.SessionManager.role = "employee"
    check("an admin alert reaches the tray", len(tray.shown) == 1, str(tray.shown))
    # Whichever way this desktop makes a noise — AppleScript on macOS, Qt
    # elsewhere — exactly one sound for one alert.
    check("and makes a sound, on this platform's own path",
          len(alert_sounds) == 1, str(alert_sounds))

    print("\nThe switches on My Profile actually switch something")
    # A preference that saves a value nothing reads is worse than no
    # preference: somebody turns notifications off, they keep arriving, and
    # now nothing on the page can be trusted.
    from client.infrastructure.database.database import Database
    Database.initialize()
    from client.services.settings_service import SettingsService

    tray = _FakeTray()
    sounds = []
    _qt.QApplication.beep = staticmethod(lambda: sounds.append(1))
    ran = []
    import subprocess as _sp2
    real_run2 = _sp2.run
    _sp2.run = lambda cmd, **kw: ran.append(cmd) or type("R", (), {"returncode": 0})()
    try:
        SettingsService.save_setting(notifier.PREF_DESKTOP, "0")
        shown_now = notifier.deliver(tray, "Title", "Body")
        check("with desktop notifications off, nothing is shown at all",
              shown_now is False and tray.shown == [] and ran == [],
              f"shown={tray.shown} osascript={ran}")
        check("and nothing sounds either — off means off",
              sounds == [], str(sounds))

        SettingsService.save_setting(notifier.PREF_DESKTOP, "1")
        SettingsService.save_setting(notifier.PREF_SOUND, "0")
        tray.shown.clear(); sounds.clear(); ran.clear()
        notifier.deliver(tray, "Title", "Body")
        check("with only the sound off, it still appears",
              bool(tray.shown) or bool(ran), f"{tray.shown} {ran}")
        check("but makes no noise",
              sounds == [] and not any("Ping" in str(c) for c in ran),
              f"beeps={sounds} osascript={ran}")

        SettingsService.save_setting(notifier.PREF_SOUND, "1")
        tray.shown.clear(); sounds.clear(); ran.clear()
        notifier.deliver(tray, "Title", "Body")
        check("and with both on it is back to normal",
              bool(tray.shown) or bool(ran))
    finally:
        _sp2.run = real_run2
        _qt.QApplication.beep = real_beep
        for key in (notifier.PREF_DESKTOP, notifier.PREF_SOUND):
            SettingsService.save_setting(key, "1")

    check("a preference nobody has set means ON",
          notifier.pref_enabled("notify_something_new") is True)

    print("\nWhen there is no tray")
    # On a machine with no system tray — some Linux desktops — showMessage is
    # unreachable. That must not take the panel down; this runs off the poll,
    # on every arrival.
    panel = ep.EmployeePanel.__new__(ep.EmployeePanel)
    panel.tray = None
    crashed = False
    try:
        panel._notify("Title", "Body")
    except Exception:
        crashed = True
    check("nothing raises", not crashed,
          "an unshowable notification would crash the panel on every message")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all notification checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
