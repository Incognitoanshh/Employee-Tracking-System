"""
A face is drawn wherever a person is, or it is drawn nowhere.

WHAT THIS EXISTS FOR. Photographs worked on My Profile and on no other
screen. The sidebar showed a letter, the header showed a generic glyph, and
in chat both people appeared as initials — while each of them could see their
own photograph on their own profile page. Reported in those words: "photo
maine dala to globally change ho rha hai ya abhi upload kiya to bas abhi ke
liye hai".

THE CAUSE WAS ONE WORD. The shared widget read `SessionManager.token`.
SessionManager has never had that attribute — it defines `auth_token`, and
every other caller in the product uses it. Written with getattr and a
default, the mistake made no noise at all: the token was None, the request
was never sent, "this person has no photo" was cached for the rest of the
run, and initials were drawn for ever. My Profile was unaffected because it
builds its own Authorization header from auth_token.

AND THE WALKTHROUGH THAT SHOULD HAVE CAUGHT IT DID NOT, because it set both
`auth_token` and `token` on SessionManager before driving the UI. A test that
is more generous than the application is a test that agrees with whatever the
code does. So this file sets ONLY what the real login sets, and asserts on
the request that goes out — not on the widget's own idea of itself.

Run:  python3 tests/test_avatar.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolated before any client module is imported — otherwise this writes into
# the installed app's data directory and talks to the PRODUCTION server.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_avatar_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication
    import time

    QApplication.instance() or QApplication([])

    from client.application.managers.session_manager import SessionManager
    from client.presentation.widgets import avatar as av

    print("What the real login actually sets")
    # EXACTLY what create_session does, and nothing more. If a test needs an
    # attribute the application never sets, the test is wrong.
    SessionManager.auth_token = "a-real-looking-token"
    SessionManager.employee_id = "EMP002"
    check("SessionManager carries auth_token", SessionManager.auth_token is not None)
    check("and has no attribute called `token`",
          not hasattr(SessionManager, "token"),
          "if this ever becomes true, the two names must be reconciled "
          "rather than both quietly supported")

    print("\nAsking for somebody's face sends a request, with the token on it")
    sent = []

    class _Response:
        status_code = 200
        content = b"\x89PNG\r\n\x1a\n" + b"0" * 64      # not a real image

    def fake_get(url, headers=None, timeout=None):
        sent.append((url, (headers or {}).get("Authorization")))
        return _Response()

    real_get = av._http.get
    av._http.get = fake_get
    av.forget()
    try:
        widget = av.Avatar(32)
        widget.show_person("EMP002", "Ansh Kumar")
        # The fetch is a QThread; give it a moment and keep the loop turning.
        end = time.time() + 3
        while time.time() < end and not sent:
            QCoreApplication.processEvents()
            time.sleep(0.02)
    finally:
        av._http.get = real_get

    check("a request was made at all", len(sent) == 1,
          f"{len(sent)} requests — none means the widget never asked, which is "
          f"how initials ended up everywhere")
    if sent:
        url, auth = sent[0]
        check("it asked for that employee's photo", url.endswith("/profile/photo/EMP002"), url)
        check("and it carried the Authorization header",
              auth == "Bearer a-real-looking-token",
              str(auth) + "  — None means the token was read from the wrong attribute")

    print("\nOne request per person, however many places draw them")
    sent.clear()
    av._http.get = fake_get
    try:
        for _ in range(5):
            w = av.Avatar(24)
            w.show_person("EMP002", "Ansh Kumar")
            end = time.time() + 0.5
            while time.time() < end:
                QCoreApplication.processEvents()
                time.sleep(0.02)
    finally:
        av._http.get = real_get
    check("five more avatars send no further requests", sent == [],
          f"{len(sent)} — a thirty-row list must not be thirty requests")

    print("\nSomebody with no photo is asked about once, and then left alone")
    sent.clear()

    class _NotFound:
        status_code = 404
        content = b""

    av.forget()
    av._http.get = lambda url, headers=None, timeout=None: (
        sent.append(url) or _NotFound())
    try:
        for _ in range(3):
            w = av.Avatar(24)
            w.show_person("EMP999", "No Photo")
            end = time.time() + 0.6
            while time.time() < end:
                QCoreApplication.processEvents()
                time.sleep(0.02)
    finally:
        av._http.get = real_get
    check("the 404 is remembered rather than re-asked", len(sent) == 1, f"{len(sent)} requests")

    print("\nInitials until a photo arrives, never a blank circle")
    w = av.Avatar(40)
    w.show_person("EMP002", "Ansh Kumar")
    check("two letters, from the name", w.text() == "AK", repr(w.text()))
    w.set_initials("Priya")
    check("one word gives one letter", w.text() == "P", repr(w.text()))
    w.set_initials("")
    check("and no name at all still draws something", w.text() == "?", repr(w.text()))

    print("\nChanging your photo clears what was remembered about you")
    av.forget()
    sent.clear()
    av._http.get = fake_get
    try:
        w = av.Avatar(24)
        w.show_person("EMP002", "Ansh Kumar")
        end = time.time() + 2
        while time.time() < end and not sent:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        before = len(sent)
        av.forget("EMP002")
        w2 = av.Avatar(24)
        w2.show_person("EMP002", "Ansh Kumar")
        end = time.time() + 2
        while time.time() < end and len(sent) == before:
            QCoreApplication.processEvents()
            time.sleep(0.02)
    finally:
        av._http.get = real_get
    check("the next draw asks again", len(sent) == before + 1,
          f"{len(sent)} vs {before} — without this the old picture stays on "
          f"every other page until the app is restarted")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all avatar checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
