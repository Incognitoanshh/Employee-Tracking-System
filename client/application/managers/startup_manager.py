import sys
import os
import platform

from client.services.logger_service import LoggerService

# NOT the product name. This is the Windows registry value and the macOS
# LaunchAgent filename that register the app to start at login. Renaming it
# would leave the OLD entry in place — pointing at an executable the update
# has replaced — and add a second one beside it.
APP_NAME = "ETS"


class StartupManager:
    """
    Registers the app to launch automatically at OS boot/login
    (Windows Registry Run key / macOS LaunchAgent).

    Only does anything for a frozen PyInstaller executable — in dev mode
    (`python client/main.py`) it silently skips, otherwise the Python
    interpreter itself would get registered at startup by mistake.
    """

    @staticmethod
    def _is_frozen():
        return bool(getattr(sys, "frozen", False))

    @staticmethod
    def enable_autostart():
        if not StartupManager._is_frozen():
            return
        system = platform.system()
        try:
            if system == "Windows":
                StartupManager._enable_windows()
            elif system == "Darwin":
                StartupManager._enable_macos()
        except Exception as error:
            LoggerService.log(f"StartupManager: enable_autostart failed — {error}")

    @staticmethod
    def disable_autostart():
        if not StartupManager._is_frozen():
            return
        system = platform.system()
        try:
            if system == "Windows":
                StartupManager._disable_windows()
            elif system == "Darwin":
                StartupManager._disable_macos()
        except Exception as error:
            LoggerService.log(f"StartupManager: disable_autostart failed — {error}")

    @staticmethod
    def is_autostart_enabled():
        system = platform.system()
        try:
            if system == "Windows":
                return StartupManager._is_enabled_windows()
            elif system == "Darwin":
                return os.path.exists(StartupManager._macos_plist_path())
        except Exception:
            return False
        return False

    # ---------------- Windows ----------------

    @staticmethod
    def _run_key():
        return r"Software\Microsoft\Windows\CurrentVersion\Run"

    @staticmethod
    def _enable_windows():
        import winreg
        exe_path = sys.executable
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, StartupManager._run_key(),
            0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
        finally:
            winreg.CloseKey(key)
        LoggerService.log("StartupManager: Windows autostart registered")

    @staticmethod
    def _disable_windows():
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, StartupManager._run_key(),
                0, winreg.KEY_SET_VALUE
            )
            winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass

    @staticmethod
    def _is_enabled_windows():
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, StartupManager._run_key(),
                0, winreg.KEY_READ
            )
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    # ---------------- macOS ----------------

    @staticmethod
    def _macos_plist_path():
        return os.path.expanduser(
            f"~/Library/LaunchAgents/com.ets.{APP_NAME.lower()}.plist"
        )

    @staticmethod
    def _enable_macos():
        plist_path = StartupManager._macos_plist_path()
        exe_path = sys.executable

        # BUG FIX (autostart silently stopped working after an upgrade):
        # this used to be just `if os.path.exists(plist_path): return`, i.e.
        # it returned as soon as the plist existed, without checking where
        # that plist pointed.
        #
        # Installing a new version (or renaming/moving the app) changes the
        # executable path, but the old plist stayed exactly as it was —
        # pointing at a binary that no longer exists. launchd tried to load
        # it, found nothing, and the app stopped starting at boot. No error
        # appeared anywhere, so nobody noticed.
        #
        # The Windows branch does not have this bug because SetValueEx
        # overwrites the value every time. macOS needs the same behaviour.
        if os.path.exists(plist_path):
            try:
                with open(plist_path) as f:
                    existing = f.read()
                if f"<string>{exe_path}</string>" in existing:
                    return  # already correct, nothing to do
            except Exception:
                pass  # unreadable — fall through and rewrite it below

            # Stale or corrupt — unload the old one and write a fresh plist
            try:
                import subprocess
                subprocess.run(["launchctl", "unload", plist_path],
                               timeout=5, check=False)
            except Exception:
                pass
            LoggerService.log(
                "StartupManager: stale macOS LaunchAgent found, refreshing"
            )

        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ets.{APP_NAME.lower()}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
        with open(plist_path, "w") as f:
            f.write(plist_content)

        try:
            import subprocess
            subprocess.run(["launchctl", "load", plist_path], timeout=5, check=False)
        except Exception:
            pass  # the plist loads automatically at the next login/boot anyway

        LoggerService.log("StartupManager: macOS LaunchAgent registered")

    @staticmethod
    def _disable_macos():
        plist_path = StartupManager._macos_plist_path()
        if not os.path.exists(plist_path):
            return
        try:
            import subprocess
            subprocess.run(["launchctl", "unload", plist_path], timeout=5, check=False)
        except Exception:
            pass
        os.remove(plist_path)
