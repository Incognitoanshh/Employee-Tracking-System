import sys
import os
import platform

from client.services.logger_service import LoggerService

APP_NAME = "ETS"


class StartupManager:
    """
    OS boot/login pe app ko automatically launch karne ke liye register
    karta hai (Windows Registry Run key / macOS LaunchAgent).

    Sirf PyInstaller se bane (frozen) executable ke liye kaam karta hai —
    dev mode (`python client/main.py`) me silently skip ho jata hai, warna
    galti se python interpreter hi startup me register ho jaata.
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
        if os.path.exists(plist_path):
            return  # already registered

        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        exe_path = sys.executable
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
            pass  # plist file agli login/boot pe automatically load ho jayega

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
