"""
Signing out has to reach the server.

It did not. `/auth/logout` was called from exactly one place — the tray's
Exit — so the Logout button in either panel cleared the session locally and
left it wide open on the server.

That was survivable while the same device could reclaim its own session. It
stopped being survivable the moment one login at a time became strict: sign
out, sign straight back in, and you are refused for two minutes while the
abandoned session goes stale. Reported from a real installed build, twice.

The failure is silent by nature — the app returns to the login screen looking
exactly as it should — so it needs a test rather than care.

Run:  python3 tests/test_logout.py
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


def main():
    from client.application.services import auth_service
    from client.application.services.auth_service import AuthService
    from client.application.managers.session_manager import SessionManager

    calls = []

    class _Response:
        def __init__(self, status):
            self.status_code = status

    def fake_post(url, headers=None, timeout=None, **kw):
        calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return _Response(200)

    real_post = auth_service._http.post
    auth_service._http.post = fake_post
    real_log = auth_service.LoggerService.log
    logged = []
    auth_service.LoggerService.log = lambda m, *a, **k: logged.append(str(m))

    try:
        print("With a live session")
        SessionManager.auth_token = "the-live-token"
        ok = AuthService.sign_out_on_server()
        check("the server is told", len(calls) == 1, str(len(calls)))
        check("at the logout endpoint",
              calls[0]["url"].endswith("/auth/logout"), calls[0]["url"])
        check("carrying the token, or the server cannot tell whose session it is",
              calls[0]["headers"].get("Authorization") == "Bearer the-live-token",
              str(calls[0]["headers"]))
        check("with a timeout — this runs while somebody waits to be signed out",
              calls[0]["timeout"] is not None and calls[0]["timeout"] <= 15,
              str(calls[0]["timeout"]))
        check("and it reports success", ok is True, str(ok))

        print("\nWith no session")
        calls.clear()
        SessionManager.auth_token = None
        ok = AuthService.sign_out_on_server()
        check("nothing is sent — there is nothing to end",
              len(calls) == 0, str(len(calls)))
        check("and it says so rather than pretending", ok is False, str(ok))

        print("\nWhen the network is down")
        # Signing out must never be blocked by a server that cannot be
        # reached. The session goes stale on its own within a couple of
        # minutes, which is the same safety valve a crash relies on.
        calls.clear()
        logged.clear()

        def exploding_post(*a, **k):
            raise ConnectionError("connection refused")

        auth_service._http.post = exploding_post
        SessionManager.auth_token = "the-live-token"
        raised = False
        try:
            ok = AuthService.sign_out_on_server()
        except Exception:
            raised = True
        check("it does not raise — the person must still get signed out",
              not raised)
        check("it reports the failure rather than claiming success",
              ok is False, str(ok))
        check("and writes down why, so a stuck account can be explained",
              any("LOGOUT" in m for m in logged), str(logged))
    finally:
        auth_service._http.post = real_post
        auth_service.LoggerService.log = real_log
        SessionManager.auth_token = None

    print("\nWhen the server ends the session")
    # THE ONE REPORTED FROM A REAL MACHINE. An administrator forced a logout;
    # the server cleared the token exactly as it should, so every request came
    # back 401 — and the client did nothing with it. The panel stayed open
    # saying "ONLINE · Tracking Active" for an account that had been signed
    # out, and the retry loops kept firing at a dead session, writing a log
    # line per failure. Force logout looked broken. It was not; the client had
    # simply never been taught to notice.
    from client.application.managers import config_sync_manager as csm

    class _Reply:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload or {}
            self.text = str(self._payload)

        def json(self):
            return self._payload

    for status, payload, should_sign_out, label in [
        (401, {"message": "Session expired"}, True, "a 401"),
        (403, {"suspended": True, "message": "You are suspended."}, True,
         "a suspension"),
        (500, {"message": "Internal server error"}, False,
         "a server fault — that is not a sign-out"),
        (503, {}, False, "a server restarting"),
    ]:
        signed_out = []
        sync = csm.ConfigSyncManager(
            employee_id="E001", device_id="d", auth_token="t",
            on_new_config=lambda *a, **k: None,
            on_force_logout=lambda *a, **k: signed_out.append(True),
            sync_interval=999)
        real = csm._http.post
        csm._http.post = lambda *a, **k: _Reply(status, payload)
        try:
            sync._do_sync()
        except Exception:
            pass
        finally:
            csm._http.post = real
        check(f"{label} → {'signs out' if should_sign_out else 'keeps working'}",
              bool(signed_out) == should_sign_out,
              f"status {status} did the opposite")

    print("\nAnd the chat poll notices first")
    # The config sync runs on a long interval; the chat poll runs every few
    # seconds, so it is what actually sees a forced logout. Reported live:
    # "employee panel khula h login window ni aaya aur ye chat manager wala
    # continuesly aarha h" — one "poll HTTP 401" line per attempt, each one
    # itself uploaded, for as long as the app was left open.
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _os.environ.setdefault("SCREENSHOT_ENCRYPTION_KEY",
                           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    # The poll reads its cursor out of the local database, which on a machine
    # that has never run the app does not exist yet.
    from client.infrastructure.database.database import Database
    Database.initialize()
    from client.application.managers import chat_manager as cm

    for status in (401, 403):
        chat = cm.ChatManager()
        ended = []
        chat.session_ended.connect(lambda why: ended.append(why))
        written = []
        real_write = cm.LoggerService.log
        cm.LoggerService.log = lambda m, *a, **k: written.append(str(m))
        real_get = cm._http.get
        cm._http.get = lambda *a, **k: _Reply(status, {"message": "Session"})
        try:
            for _ in range(5):        # five polls, as a left-open app would
                chat._poll()
        finally:
            cm._http.get = real_get
            cm.LoggerService.log = real_write

        check(f"HTTP {status} on the poll → the panel is told to sign out",
              len(ended) == 1, f"{len(ended)} — expected exactly one")
        # "ChatManager: stopped" is in there too and belongs there — it is
        # written once, on the way down. What must not repeat is the failure.
        ended_lines = [m for m in written if "SESSION ENDED" in m]
        check(f"HTTP {status} is written down ONCE, not once per poll",
              len(ended_lines) == 1 and not any("poll HTTP" in m
                                                for m in written),
              f"{written} — every one of these is itself uploaded, which is "
              f"what flooded the audit log")
        check(f"HTTP {status} stops the loop — this token is dead",
              chat._stop.is_set(), "it would keep asking a dead session")

    chat = cm.ChatManager()
    ended = []
    chat.session_ended.connect(lambda why: ended.append(why))
    real_get = cm._http.get
    cm._http.get = lambda *a, **k: _Reply(503, {})
    try:
        chat._poll()
    finally:
        cm._http.get = real_get
    check("a server fault does NOT sign anybody out", ended == [], str(ended))
    check("and leaves the poll running", not chat._stop.is_set())

    print("\nWhen signing out goes wrong")
    # A ONE-WAY LATCH IS A DEAD BUTTON. Both panels guard against being
    # signed out twice — two watchers can notice the same forced logout a
    # second apart. But the flag was never let go of, so anything raising
    # part-way through left Logout doing nothing at all, with no way back but
    # restarting the app. Reported from a real machine: "logout ni ho raha,
    # click kar raha hoon".
    from client.presentation.windows import employee_panel as ep2
    from client.presentation.windows import admin_config_panel as acp2

    for label, module, cls_name, flag in (
            ("employee panel", ep2, "EmployeePanel", "_signing_out"),
            ("admin panel", acp2, "AdminConfigPanel", "_logging_out")):
        cls = getattr(module, cls_name)
        panel = cls.__new__(cls)
        setattr(panel, flag, False)
        boom = []

        def explode(reason=""):
            boom.append(1)
            raise RuntimeError("the network went away mid-logout")

        panel._do_logout = explode
        real_log = module.LoggerService.log
        said = []
        module.LoggerService.log = lambda m, *a, **k: said.append(str(m))
        try:
            try:
                panel.logout()
            except RuntimeError:
                pass
            check(f"{label}: a failed sign-out is written down",
                  any("LOGOUT FAILED" in m for m in said), str(said)[:120])
            check(f"{label}: and the button still works afterwards",
                  getattr(panel, flag) is False,
                  "the flag stayed set — Logout is dead until the app restarts")
            try:
                panel.logout()
            except RuntimeError:
                pass
            check(f"{label}: the second click actually tries again",
                  len(boom) == 2, f"{len(boom)} attempt(s)")
        finally:
            module.LoggerService.log = real_log

    print("\nBoth panels use it")
    # One helper, called from both, because a third sign-out path added later
    # would otherwise repeat the same omission.
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("employee_panel.py", "admin_config_panel.py"):
        source = (root / "client" / "presentation" / "windows" / name).read_text()
        check(f"{name} tells the server on the way out",
              "sign_out_on_server()" in source,
              "this panel clears the session locally and leaves it open on "
              "the server")
        check(f"{name} does not when the SERVER ended it",
              "if not reason:" in source,
              "a force logout or a suspension has already cleared the row, "
              "and the token is dead — calling with it is a wasted round trip "
              "while somebody waits")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all logout checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
