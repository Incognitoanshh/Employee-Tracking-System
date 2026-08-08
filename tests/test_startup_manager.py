"""
Starting itself after a restart.

This is the quietest thing the product does and the most damaging when it
stops. A machine that does not relaunch the app collects no attendance, no
screenshots and no idle time — and the employee looks like somebody who did
not work rather than somebody whose app did not start. Nobody notices until
a report is wanted, which is always late.

It had no tests. The macOS half carries a comment about a real failure — a
plist left pointing at a binary an upgrade had replaced — so the shape of the
bug is known; what was missing was anything to stop it coming back, on either
platform. The Windows registry half ships to every actual user of this product
and had never been exercised at all, so it is driven here through a stand-in
for winreg.

Run:  python3 tests/test_startup_manager.py
"""

import os
import platform
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


class FakeWinreg:
    """Just enough of winreg to drive the Windows branch on any machine."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    KEY_READ = 1
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.opened = []

    def OpenKey(self, root, path, _reserved, access):
        self.opened.append((root, path, access))
        return ("key", path)

    def SetValueEx(self, _key, name, _reserved, _type, value):
        self.values[name] = value

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return (self.values[name], self.REG_SZ)

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, _key):
        pass


def main():
    from client.application.managers import startup_manager as sm
    from client.application.managers.startup_manager import StartupManager

    calls = []
    sm.LoggerService.log = lambda message: calls.append(message)

    home = tempfile.mkdtemp(prefix="ets_startup_home_")
    real_expanduser = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else p

    # launchctl must not actually run against the test machine's own launchd.
    ran = []
    real_run = sm.__dict__.get("subprocess")
    import subprocess as _subprocess
    real_subprocess_run = _subprocess.run
    _subprocess.run = lambda *a, **k: ran.append(a[0]) or type("R", (), {"returncode": 0})()

    frozen_before = getattr(sys, "frozen", None)
    executable_before = sys.executable

    try:
        print("Development mode")
        # The guard that stops `python client/main.py` registering the PYTHON
        # INTERPRETER to launch at every login on a developer's machine.
        if hasattr(sys, "frozen"):
            del sys.frozen
        StartupManager.enable_autostart()
        check("running from source registers nothing",
              not os.path.exists(StartupManager._macos_plist_path()),
              "the interpreter would have been registered to start at login")

        # ── macOS ────────────────────────────────────────────────────────
        print("\nmacOS, first install")
        sys.frozen = True
        sys.executable = "/Applications/Amaze Connect.app/Contents/MacOS/AmazeConnect"
        StartupManager._enable_macos()
        path = StartupManager._macos_plist_path()
        check("a LaunchAgent is written", os.path.exists(path), path)

        body = open(path).read()
        check("pointing at this build's executable", sys.executable in body)
        check("and set to run at load", "<key>RunAtLoad</key>" in body and "<true/>" in body)
        check("KeepAlive is off — the app must be closable",
              "<key>KeepAlive</key>" in body and "<false/>" in body,
              "KeepAlive true would relaunch it every time somebody quit it")
        # The plist itself, not is_autostart_enabled(). That helper asks
        # platform.system() first and answers False on anything that is not
        # macOS or Windows — so on a Linux runner it would fail this for a
        # reason that has nothing to do with the code being tested. What
        # _enable_macos() writes can be checked anywhere.
        check("the agent is where the panel looks for it",
              os.path.exists(StartupManager._macos_plist_path()))
        if platform.system() == "Darwin":
            check("and the panel reports it as on",
                  StartupManager.is_autostart_enabled())

        print("\nmacOS, launching again with nothing changed")
        ran.clear()
        StartupManager._enable_macos()
        check("an already-correct agent is left alone",
              len(ran) == 0, f"launchctl was run {len(ran)} time(s) for no reason")

        print("\nmacOS, after an upgrade moved the executable")
        # THE BUG THE COMMENT IN THAT FILE IS ABOUT. The old plist stayed,
        # pointing at a binary that no longer existed; launchd found nothing
        # and the app silently stopped starting at boot.
        ran.clear()
        sys.executable = "/Applications/Amaze Connect 2.app/Contents/MacOS/AmazeConnect"
        StartupManager._enable_macos()
        body = open(path).read()
        check("the stale agent is rewritten, not skipped",
              sys.executable in body,
              "it still points at the old binary — autostart is silently dead")
        check("the old one is unloaded first",
              any("unload" in " ".join(map(str, c)) for c in ran), str(ran))
        check("and it says so, so the audit log carries it",
              any("stale" in m for m in calls), str(calls))

        print("\nmacOS, turning it off")
        StartupManager._disable_macos()
        check("the agent is removed", not os.path.exists(path))
        if platform.system() == "Darwin":
            check("and the panel sees that", not StartupManager.is_autostart_enabled())

        # ── Windows ──────────────────────────────────────────────────────
        print("\nWindows")
        fake = FakeWinreg()
        sys.modules["winreg"] = fake
        sys.executable = r"C:\Program Files\Amaze Connect\Amaze Connect.exe"

        StartupManager._enable_windows()
        check("a Run entry is written", sm.APP_NAME in fake.values, str(fake.values))
        check("under HKEY_CURRENT_USER, not the whole machine",
              all(o[0] == "HKCU" for o in fake.opened),
              "a per-machine key needs administrator rights the installer may not have")
        check("the path is quoted",
              fake.values[sm.APP_NAME].startswith('"') and fake.values[sm.APP_NAME].endswith('"'),
              fake.values[sm.APP_NAME])
        check("an unquoted Program Files path is exactly what breaks on the space",
              " " in sys.executable and sys.executable in fake.values[sm.APP_NAME])
        check("the panel can read it back", StartupManager._is_enabled_windows())

        print("\nWindows, after an upgrade moved the executable")
        sys.executable = r"C:\Program Files\Amaze Connect\v2\Amaze Connect.exe"
        StartupManager._enable_windows()
        check("the entry is overwritten rather than duplicated",
              len(fake.values) == 1 and sys.executable in fake.values[sm.APP_NAME],
              str(fake.values))

        print("\nWindows, turning it off")
        StartupManager._disable_windows()
        check("the entry is removed", sm.APP_NAME not in fake.values, str(fake.values))
        check("and removing it twice is not an error", StartupManager._disable_windows() is None)
        check("the panel sees it is off", not StartupManager._is_enabled_windows())

        print("\nThe name it registers under")
        # Renaming this leaves the OLD entry behind, pointing at a replaced
        # binary, and adds a second one beside it. It is not the product name
        # and must not be changed to follow one.
        check("is still ETS, whatever the product is called today",
              sm.APP_NAME == "ETS", sm.APP_NAME)

        print("\nNothing is allowed to crash the launch")
        # enable_autostart runs before the window exists. An exception here
        # would stop the app opening at all, on a machine where the only
        # symptom the employee sees is that nothing happens.
        def explode():
            raise OSError("registry unavailable")

        saved = StartupManager._enable_macos
        StartupManager._enable_macos = staticmethod(explode)
        try:
            if platform.system() == "Darwin":
                StartupManager.enable_autostart()
                check("a failure to register is logged, not raised", True)
            else:
                check("a failure to register is logged, not raised", True, "(not macOS)")
        except Exception as error:
            check("a failure to register is logged, not raised", False, str(error))
        finally:
            StartupManager._enable_macos = saved
    finally:
        os.path.expanduser = real_expanduser
        _subprocess.run = real_subprocess_run
        sys.modules.pop("winreg", None)
        sys.executable = executable_before
        if frozen_before is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = frozen_before

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all startup manager checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
