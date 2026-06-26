from __future__ import annotations

import threading
from typing import Callable, Optional

import requests

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService


DEFAULT_SYNC_INTERVAL_SECONDS = 300  # 5 minutes


class ConfigSyncManager:

    def __init__(
        self,
        employee_id:     str,
        device_id:       str,
        auth_token:      str,                               # kept for compat
        on_new_config:   Optional[Callable[[dict], None]] = None,
        on_force_logout: Optional[Callable[[], None]]     = None,
        sync_interval:   int = DEFAULT_SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._employee_id     = employee_id
        self._device_id       = device_id
        self._on_new_config   = on_new_config
        self._on_force_logout = on_force_logout
        self._sync_interval   = sync_interval          # seconds
        self._stop_event      = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="ConfigSyncThread",
            daemon=True,
        )
        self._thread.start()
        LoggerService.log_verbose("ConfigSyncManager: started")

    def stop(self) -> None:
        self._stop_event.set()
        if (
            self._thread
            and self._thread.is_alive()
            and self._thread != threading.current_thread()
        ):
            self._thread.join(timeout=5)
        LoggerService.log_verbose("ConfigSyncManager: stopped")

    def sync_now(self) -> dict | None:
        return self._do_sync()

    # ── Internal ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        failures         = 0
        base_interval    = self._sync_interval
        current_interval = base_interval

        # Pehle turant sync karo
        result = self._do_sync()
        if result is None:
            failures += 1
            current_interval = min(base_interval * (2 ** failures), 600)

        while not self._stop_event.wait(timeout=current_interval):
            result = self._do_sync()
            if result is None:
                failures        += 1
                current_interval = min(base_interval * (2 ** failures), 600)
                LoggerService.log_verbose(
                    f"ConfigSyncManager: backoff #{failures}, "
                    f"next in {current_interval}s"
                )
            else:
                failures         = 0
                current_interval = base_interval

    def _do_sync(self) -> dict | None:
        try:
            # ✅ Token hamesha SessionManager se live read karo
            token = SessionManager.auth_token
            if not token:
                LoggerService.log_verbose("ConfigSyncManager: no token — skip")
                return None

            response = requests.post(
                f"{API_BASE_URL}/config/sync",
                json={
                    "employee_id": self._employee_id,
                    "device_id":   self._device_id,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                timeout=10,
            )

            if response.status_code != 200:
                LoggerService.log_verbose(
                    f"ConfigSyncManager: HTTP {response.status_code}"
                )
                return None

            data   = response.json()
            config = data.get("config", {})

            # ✅ Config hamesha pehle persist karo
            self._persist_config(config)

            if config.get("force_logout"):
                LoggerService.log("ConfigSyncManager: force_logout received")
                if self._on_force_logout:
                    self._on_force_logout()
                return config

                # Normal flow
            if self._on_new_config:
                self._on_new_config(config)
            LoggerService.log_verbose(
                f"ConfigSyncManager: sync OK — "
                f"screenshot={config.get('screenshot_min_minutes')}-"
                f"{config.get('screenshot_max_minutes')}min"
            )
            return config   # ✅ Hamesha return — loop ko success pata chale

        except requests.exceptions.ConnectionError:
            LoggerService.log_verbose("ConfigSyncManager: server unreachable")
        except requests.exceptions.Timeout:
            LoggerService.log_verbose("ConfigSyncManager: request timed out")
        except Exception as exc:
            LoggerService.log_verbose(f"ConfigSyncManager: error — {exc}")

        return None

        # ── Persistence ───────────────────────────────────────────────────────

    @staticmethod
    def _persist_config(config: dict) -> None:
        field_map = {
            "screenshot_min_minutes":  "screenshot_min_minutes",
            "screenshot_max_minutes":  "screenshot_max_minutes",
            "screenshot_count":        "screenshot_count",
            "upload_interval_minutes": "upload_interval_minutes",
            "idle_threshold_seconds":  "idle_threshold_seconds",
            "verbose_logging":         "verbose_logging",
        }

        for server_key, db_key in field_map.items():
            value = config.get(server_key)
            if value is not None:
                SettingsService.save_setting(db_key, str(value))

                # ✅ Shift block loop ke BAAD — sirf ek baar chalega
        shift = config.get("shift")
        if shift:
            if shift.get("start_ist"):
                SettingsService.save_setting("shift_start_ist", shift["start_ist"])
            if shift.get("end_ist"):
                SettingsService.save_setting("shift_end_ist", shift["end_ist"])