from __future__ import annotations

from logging import config
import threading
from typing import Callable, Optional

import requests

from client.core.config import API_BASE_URL
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService


DEFAULT_SYNC_INTERVAL_SECONDS = 5 * 60  # 5 minutes


class ConfigSyncManager:
    """Background thread jo server se config periodically fetch karta hai."""

    def __init__(
        self,
        employee_id:      str,
        device_id:        str,
        auth_token:       str,
        on_new_config:    Optional[Callable[[dict], None]] = None,
        on_force_logout:  Optional[Callable[[], None]]     = None,
        sync_interval:    int = DEFAULT_SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._employee_id     = employee_id
        self._device_id       = device_id
        self._auth_token      = auth_token
        self._on_new_config   = on_new_config
        self._on_force_logout = on_force_logout
        self._sync_interval   = sync_interval
        self._stop_event      = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Background sync thread shuru karo."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="ConfigSyncThread",
            daemon=True,
        )
        self._thread.start()
        LoggerService.log("ConfigSyncManager: started")

    def stop(self) -> None:
        """Thread band karo (graceful)."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            LoggerService.log("ConfigSyncManager: stopped")

    def sync_now(self) -> dict | None:
        """Abhi ek baar sync karo (blocking)."""
        return self._do_sync()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        """Thread loop — pehle turant, phir har _sync_interval seconds mein."""
        self._do_sync()
        while not self._stop_event.wait(timeout=self._sync_interval):
            self._do_sync()

    def _do_sync(self) -> dict | None:
        """Server se config fetch karo aur DB mein save karo."""
        try:
            response = requests.post(
                f"{API_BASE_URL}/config/sync",
                json={
                    "employee_id": self._employee_id,
                    "device_id":   self._device_id,
                },
                headers={
                    "Authorization": f"Bearer {self._auth_token}",
                    "Content-Type":  "application/json",
                },
                timeout=10,
            )

            if response.status_code != 200:
                LoggerService.log(
                    f"ConfigSyncManager: HTTP {response.status_code} — {response.text[:200]}"
                )
                return None

            data   = response.json()
            config = data.get("config", {})
            print("SERVER CONFIG =", config)

        
            if config.get("force_logout"):
                LoggerService.log("ConfigSyncManager: force_logout received")

                if self._on_force_logout:
                    self._on_force_logout()

                return config

            # NORMAL FLOW
            self._persist_config(config)

            if self._on_new_config:
                self._on_new_config(config)

                LoggerService.log(
                    f"ConfigSyncManager: sync OK — "
                    f"screenshot={config.get('screenshot_min_minutes')}-"
                    f"{config.get('screenshot_max_minutes')}min, "
                    f"upload={config.get('upload_interval_minutes')}min"
                )

            return config

        except requests.exceptions.ConnectionError:
            LoggerService.log("ConfigSyncManager: server unreachable — will retry")
        except requests.exceptions.Timeout:
            LoggerService.log("ConfigSyncManager: request timed out — will retry")
        except Exception as exc:
            LoggerService.log(f"ConfigSyncManager: unexpected error — {exc}")

            return None

        # ------------------------------------------------------------------ #
        #  Persistence                                                         #
        # ------------------------------------------------------------------ #

    @staticmethod
    def _persist_config(config: dict) -> None:
        """Config values ko local SQLite settings table mein save karo."""
        field_map = {
            "screenshot_min_minutes":  "screenshot_min_minutes",
            "screenshot_max_minutes":  "screenshot_max_minutes",
            "screenshot_count":        "screenshot_count",
            "upload_interval_minutes": "upload_interval_minutes",
            "idle_threshold_seconds":  "idle_threshold_seconds",
        }
        for server_key, db_key in field_map.items():
            value = config.get(server_key)

            print("SYNC SAVE:", server_key, value)
            if value is not None:
                SettingsService.save_setting(
                    db_key,
                    str(value)
                )

        shift = config.get("shift")

        if shift:
            SettingsService.save_setting(
                "shift_start_ist",
                shift.get("start_ist", "")
            )
            SettingsService.save_setting(
                "shift_end_ist",
                shift.get("end_ist", "")
            )
