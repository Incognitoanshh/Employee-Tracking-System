from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QTimer, Signal, QMetaObject, Qt, Slot

from client.application.managers.sync_manager import SyncManager
from client.application.managers.config_sync_manager import ConfigSyncManager
from client.application.managers.screenshot_manager import ScreenshotManager
from client.core.time_ist import (
    now_ist,
    end_of_ist_day,
    parse_shift_time,
    ShiftTimeParseError,
)
from client.application.managers.session_manager import SessionManager
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService


class SchedulerService(QObject):

    screenshot_triggered  = Signal()   # screenshot lene ka waqt aa gaya
    force_logout          = Signal()   # config sync se force logout

    def __init__(self):
        super().__init__()
        self._pending_timers:  list[QTimer] = []   # shift ke random timers
        self._sync_timer = QTimer()
        self._sync_timer.timeout.connect(self._sync_tick)
        self._sync_counter = 0
        self._sync_interval = 60
        self._sync_failures = 0
        self._config_sync: ConfigSyncManager | None = None
        # Jab tak ka schedule already bana hua hai. Iske baad `_sync_tick`
        # apne aap agle din ka schedule bana deta hai (neeche dekho).
        self._scheduled_until: datetime | None = None
        self._rollover_counter = 0
        self._started = False


    def start(self):
        """Login ke baad call karo — schedule generate karo aur timers lagao."""
        # BUG FIX: start() do baar call ho sakta hai (jaise admin panel se
        # wapas aane par). Pehle har call ek NAYA ConfigSyncManager thread
        # bana deta tha bina purane ko stop kiye — har baar ek leaked
        # background thread jo server ko duplicate config requests bhejta
        # rehta tha.
        if self._started:
            return
        self._started = True
        self._schedule_shift_screenshots()
        self._sync_timer.start(1000)   # har second — sync retry counter ke liye
        self._start_config_sync()
        # Startup pe purane orphan records cleanup karo
        try:
            SyncManager.cleanup_old_orphans(days=7)
        except Exception as e:
            LoggerService.log_verbose(f"SchedulerService: cleanup_old_orphans failed on startup — {e}")
        LoggerService.log_verbose("SchedulerService: started (shift-based mode)")

    def stop(self):
        self._started = False
        self._sync_timer.stop()

        for t in self._pending_timers:
            t.stop()

        self._pending_timers.clear()
        self._scheduled_until = None

        if self._config_sync:
            self._config_sync.stop()
            self._config_sync = None
        LoggerService.log_verbose("SchedulerService: stopped")

    def _schedule_shift_screenshots(self):

        now = now_ist()

        # ── SUPER ADMIN ko monitor nahi karna ──
        # Super admin company ka owner/manager hai, tracked employee nahi.
        # BUG tha: production pe ULTA ho raha tha — saare 69 screenshots
        # EMP001 (super admin) ke the, aur employees/admins ka ek bhi nahi.
        # Ye line har reschedule pe chalti thi, is liye Audit Logs me
        # super admin ki hazaaron identical entries bhar jaati thin aur
        # asli events (LOGIN/LOGOUT, role changes) dab jaate the. Ab
        # log_verbose — sirf tab dikhega jab verbose_logging ON ho.
        if getattr(SessionManager, "role", "") == "super_admin":
            LoggerService.log_verbose(
                "SchedulerService: super admin — screenshots disabled for this account"
            )
            self._scheduled_until = None
            return

        # ── SHIFT WINDOW, RESOLVED IN IST ─────────────────────────────────
        #
        # One parser, one timezone, one code path. There is deliberately no
        # second branch here: two branches that were supposed to agree is
        # precisely what shipped a NameError to production, because the one
        # the real client took was the one the tests never exercised.
        #
        # parse_shift_time() accepts every shape the client can hold — ISO
        # with an offset, ISO without one, HH:MM, HH:MM:SS — and always
        # returns IST wall-clock on today's IST date.
        today = now.date()
        try:
            shift_start = parse_shift_time(SessionManager.shift_start, today)
            shift_end   = parse_shift_time(SessionManager.shift_end, today)
        except ShiftTimeParseError as error:
            # Fall back to a window rather than to nothing. Monitoring must
            # not switch itself off because a value was malformed, but the
            # operator has to be able to see that it happened — so this is
            # log(), not log_verbose().
            shift_start = now
            shift_end   = now + timedelta(hours=8)
            LoggerService.log(
                f"SCHEDULER: could not read shift times ({error}) — "
                f"falling back to an 8 hour window from now"
            )


        # ── OVERNIGHT (NIGHT SHIFT) NORMALISATION ─────────────────────────
        #
        # BUG (production me night-shift employees ko poora blind kar deta
        # tha): ye adjustment pehle SIRF ISO-parse wali branch ke andar tha.
        # Lekin config_sync_manager hamesha plain "HH:MM" store karta hai
        # (wo `start_ist` wali ISO value ko overwrite kar deta hai), aur
        # `datetime.fromisoformat("22:00")` ValueError deti hai — yaani
        # asal me HAMESHA neeche wali strptime fallback branch chalti thi,
        # jisme ye adjustment tha hi nahi.
        #
        # Nateeja: 22:00–06:00 shift ka window "aaj 22:00 → aaj 06:00" ban
        # jaata tha, yaani MINUS 16 ghante. Negative window me koi slot fit
        # nahi hota, is liye night-shift employee ka ek bhi screenshot
        # schedule nahi hota tha. Day shift (09:00–18:00) theek chalti thi,
        # is liye ye kabhi pakda nahi gaya.
        #
        # Ab ye dono branches ke BAAD ek hi jagah lagta hai. Jahan end pehle
        # se start ke aage hai (normal day shift) wahan ye no-op hai.
        # ── OVERNIGHT NORMALISATION ───────────────────────────────────────
        # A shift whose end is not after its start crosses midnight, so the
        # end belongs to the following day. No-op for an ordinary day shift.
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)

        # Both times were placed on TODAY's IST date above, which is wrong
        # when an overnight shift started YESTERDAY and is still running.
        # At 02:00 during a 22:00-06:00 shift the window would read as
        # "tonight 22:00 -> tomorrow 06:00" — twenty hours away — and the
        # employee, who is mid-shift right now, would be treated as
        # pre-shift. Shift the window back a day when the previous day's
        # window is the one actually containing `now`.
        #
        # (This was lost when the daily-budget rewrite replaced a block
        # starting with the same `if now < shift_start:` line. Restored,
        # and covered by tests/test_scheduler_timezones.py.)
        if now < shift_start:
            previous_start = shift_start - timedelta(days=1)
            previous_end   = shift_end - timedelta(days=1)
            if previous_start <= now < previous_end:
                shift_start, shift_end = previous_start, previous_end

        # ── DAILY BUDGET WINDOW ───────────────────────────────────────────
        #
        # The count is per IST calendar day, not per shift. There is no
        # separate "off-shift" mode with its own cadence any more — that was
        # the path that ignored the count and produced 157 extra captures a
        # night. One budget, spread over whatever monitoring time is left
        # today.
        #
        # The shift decides WHEN the day's captures are placed; it no longer
        # decides HOW MANY.
        day_end = end_of_ist_day(now)

        if now < shift_start:
            # Logged in before the shift starts. Cover until it does; the
            # rollover then re-plans across the shift itself.
            window_end = min(shift_start, day_end)
            label = "pre-shift"
        elif now < shift_end:
            window_end = min(shift_end, day_end)
            label = "in-shift"
        else:
            # Shift is over but the employee is still logged in. Cover the
            # rest of today only — tomorrow gets its own budget at midnight.
            window_end = day_end
            label = "post-shift"

        self._scheduled_until = window_end
        timestamps = ScreenshotManager.generate_daily_schedule(now, window_end)

        LoggerService.log_verbose(
            f"SchedulerService: {label} — {len(timestamps)} capture(s) planned "
            f"until {window_end.strftime('%d %b %H:%M')} IST "
            f"({ScreenshotManager.captures_today()}/"
            f"{ScreenshotManager.screenshots_per_day()} used today)"
        )
        self._arm_timers(timestamps, now)

    def _arm_timers(self, timestamps, now: datetime):
        """Timestamps ki list ke liye single-shot QTimers lagao."""
        for ts in timestamps:
            delay_ms = int((ts - now).total_seconds() * 1000)
            if delay_ms < 0:
                continue  # already past
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._fire_screenshot)
            timer.start(delay_ms)
            self._pending_timers.append(timer)
            LoggerService.log_verbose(
                f"SchedulerService: screenshot scheduled at {ts.strftime('%H:%M:%S')} "
                f"(in {delay_ms//1000}s)"
            )

    def _fire_screenshot(self):
        """Timer fire hone par signal emit karo."""
        self.screenshot_triggered.emit()


    def _sync_tick(self):
        self._check_shift_rollover()
        self._sync_counter += 1
        if self._sync_counter >= self._sync_interval:
            try:
                SyncManager.retry_uploads()
                SyncManager.retry_logs()
                # Success - reset to base interval (60s)
                self._sync_failures = 0
                self._sync_interval = 60
            except Exception as e:
                # Exponential backoff: 60 -> 120 -> 240 -> 480 -> 600 max
                self._sync_failures += 1
                self._sync_interval = min(60 * (2 ** self._sync_failures), 600)
            self._sync_counter = 0

    def _check_shift_rollover(self):
        """
        BUG FIX: `_schedule_shift_screenshots()` sirf login ke waqt EK BAAR
        chalta tha. Real-world me employee apna laptop band nahi karta —
        app din-raat chalti rehti hai. Uss case me pehle din ke baad kabhi
        naya schedule generate hi nahi hota tha, yaani AGLE DIN SE ZERO
        SCREENSHOTS (jab tak employee manually logout/login na kare).
        Ab jaise hi current shift window khatam hota hai, agle din ka
        schedule khud ban jaata hai.

        Har second call hota hai (sync timer se) — isliye actual check
        sirf har minute karte hain, warna bekaar ka kaam hota rahega.
        """
        self._rollover_counter += 1
        if self._rollover_counter < 60:
            return
        self._rollover_counter = 0

        if not self._scheduled_until:
            return

        if now_ist() >= self._scheduled_until:
            LoggerService.log_verbose("SchedulerService: shift window over — rescheduling for next shift")
            self.reschedule()

    def _start_config_sync(self):
        if not SessionManager.is_authenticated:
            return

        # Purana sync thread pehle band karo — warna har start() pe ek
        # naya thread leak hota hai.
        if self._config_sync:
            self._config_sync.stop()
            self._config_sync = None

        interval = int(
            SettingsService.get_setting("upload_interval_minutes", "5")
        ) * 60

        self._config_sync = ConfigSyncManager(
            employee_id     = SessionManager.employee_id,
            device_id       = SessionManager.get_device_id(),
            auth_token      = SessionManager.auth_token,
            on_new_config   = self._apply_new_config,
            on_force_logout = self._handle_force_logout,
            sync_interval   = 5,
        )
        self._config_sync.start()
        LoggerService.log_verbose("SchedulerService: ConfigSync started")

    def _apply_new_config(self, config: dict):
        """
        Server se naya config aaya — settings update karo.

        NOTE: ConfigSyncManager isko background thread se call karta hai,
        is liye yahan koi bhi QObject/QTimer creation seedha nahi karna —
        reschedule() ko main thread pe QMetaObject.invokeMethod se hi
        trigger karo. Saath hi, sirf tab reschedule karo jab values
        actually badli hon — warna har poll (5s) pe naya schedule
        generate hoga, chahe server same config hi baar baar bheje.
        """
        min_m  = config.get("screenshot_min_minutes")
        max_m  = config.get("screenshot_max_minutes")
        count  = config.get("screenshots_per_day")

        old_min   = SettingsService.get_setting("screenshot_min_minutes")
        old_max   = SettingsService.get_setting("screenshot_max_minutes")
        old_count = SettingsService.get_setting("screenshots_per_day")

        # Sirf un fields ko diff karo jo is payload mein actually present hain.
        # (Partial config aane par missing fields ko false-positive "changed"
        # na maana jaaye.)
        changed = (
            (min_m is not None and str(old_min) != str(min_m))
            or (max_m is not None and str(old_max) != str(max_m))
            or (count is not None and str(old_count) != str(count))
        )

        if min_m is not None:
            SettingsService.save_setting("screenshot_min_minutes", str(min_m))
        if max_m is not None:
            SettingsService.save_setting("screenshot_max_minutes", str(max_m))
        if count is not None:
            SettingsService.save_setting("screenshots_per_day", str(count))

        # Shift timing update karo SessionManager mein bhi
        shift = config.get("shift")
        if shift:
            start_ist = shift.get("start_ist")
            end_ist   = shift.get("end_ist")
            if start_ist and end_ist:
                old_start = SessionManager.shift_start
                old_end   = SessionManager.shift_end
                SessionManager.shift_start = start_ist
                SessionManager.shift_end   = end_ist
                if old_start != start_ist or old_end != end_ist:
                    changed = True
                    LoggerService.log_verbose(f"SchedulerService: shift updated {start_ist}–{end_ist}")

        if changed:
            LoggerService.log_verbose(
                f"SchedulerService: config updated — count={count}, "
                f"interval={min_m}–{max_m} min"
            )
            # Background thread se main thread pe safely hop karo before
            # reschedule() chalaye, kyunki reschedule() QTimer banata hai
            # (QObject children sirf apne owning thread pe create ho sakte hain).
            QMetaObject.invokeMethod(
                self,
                "_do_reschedule",
                Qt.ConnectionType.QueuedConnection,
            )

    @Slot()
    def _do_reschedule(self):
        self.reschedule()

    def _handle_force_logout(self):
        QMetaObject.invokeMethod(self, "_emit_force_logout", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _emit_force_logout(self):
        self.force_logout.emit()


    def reschedule(self):
        for t in self._pending_timers:
            t.stop()
        self._pending_timers.clear()
        self._schedule_shift_screenshots()
        LoggerService.log_verbose("SchedulerService: rescheduled after config change")

