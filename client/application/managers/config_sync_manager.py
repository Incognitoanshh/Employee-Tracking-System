from __future__ import annotations

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
        LoggerService.log_verbose("ConfigSyncManager: started")

    def stop(self):
        self._stop_event.set()

        if (
            self._thread
            and self._thread.is_alive()
            and self._thread != threading.current_thread()
        ):
            self._thread.join(timeout=5)

            LoggerService.log_verbose("ConfigSyncManager: stopped")

    def sync_now(self) -> dict | None:
        """Abhi ek baar sync karo (blocking)."""
        return self._do_sync()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _loop(self) -> None:
        """Thread loop — pehle turant, phir har _sync_interval seconds mein."""
        failures = 0
        base_interval = self._sync_interval
        current_interval = base_interval
        self._do_sync()
        while not self._stop_event.wait(timeout=current_interval):
            result = self._do_sync()
            if result is None:
                # Failure - exponential backoff: base -> 2x -> 4x -> 8x -> max 10min
                failures += 1
                current_interval = min(base_interval * (2 ** failures), 600)
                LoggerService.log_verbose(f"ConfigSyncManager: backoff #{failures}, next sync in {current_interval}s")
            else:
                # Success - reset to base interval
                failures = 0
                current_interval = base_interval

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
                LoggerService.log_verbose(
                    f"ConfigSyncManager: HTTP {response.status_code} — {response.text[:200]}"
                )
                return None

            data   = response.json()
            config = data.get("config", {})
            # print("SERVER CONFIG =", config)

            if config.get("force_logout"):

                # Force logout HAMESHA important hai — verbose gate ke
                # bahar rakha gaya, security-critical event.
                LoggerService.log("ConfigSyncManager: force_logout received")
                print("[FORCE LOGOUT STEP 1] flag received")

                if self._on_force_logout:
                    print("[FORCE LOGOUT] invoking callback")
                    self._on_force_logout()

                return config


                # NORMAL FLOW
            self._persist_config(config)
            if self._on_new_config:
                self._on_new_config(config)
                LoggerService.log_verbose(
                    f"ConfigSyncManager: sync OK — "
                    f"screenshot={config.get('screenshot_min_minutes')}-"
                    f"{config.get('screenshot_max_minutes')}min, "
                    f"upload={config.get('upload_interval_minutes')}min"
                )
                return config

        except requests.exceptions.ConnectionError:
            LoggerService.log_verbose("ConfigSyncManager: server unreachable — will retry")
        except requests.exceptions.Timeout:
            LoggerService.log_verbose("ConfigSyncManager: request timed out — will retry")
        except Exception as exc:
            LoggerService.log_verbose(f"ConfigSyncManager: unexpected error — {exc}")

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
            "verbose_logging":         "verbose_logging",   # ← NAYA: yahi flag
            #   verbose gate
            #   control karta hai
        }
        for server_key, db_key in field_map.items():
            value = config.get(server_key)

            # print("SYNC SAVE:", server_key, value)  # noisy — removed
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