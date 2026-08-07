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
from client.core.work_calendar import day_off_reason
from client.application.managers.session_manager import SessionManager
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService


class SchedulerService(QObject):

    screenshot_triggered  = Signal()   # screenshot lene ka waqt aa gaya
    # Carries the reason so the panel can say WHY it is signing out. An
    # unexplained logout is indistinguishable from a crash.
    force_logout          = Signal(str)

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
        self._report_autostart_state()

    def _report_autostart_state(self):
        """Tell the server if the app will not start itself next time.

        On Windows autostart lives in the employee's OWN registry hive, so
        they can remove it without any privilege at all — and the tracker
        then simply stops appearing after the next reboot. Nothing anywhere
        says so; it reads as somebody who stopped working.

        Preventing that needs a Windows Service, which cannot be installed on
        a personal machine the company does not administer. What CAN be done
        is notice, and say so where an admin will see it. log(), not
        log_verbose(): this has to reach the audit log without anyone having
        first switched verbose logging on.

        Reported only when it is OFF. A line every launch saying everything
        is fine is the kind of noise that buries the launches where it is not.
        """
        try:
            from client.application.managers.startup_manager import StartupManager
            if not StartupManager.is_autostart_enabled():
                LoggerService.log(
                    "AUTOSTART DISABLED : this machine will not launch the app "
                    "on its own after a restart"
                )
        except Exception as error:
            LoggerService.log_verbose(
                f"SchedulerService: could not read autostart state — {error}"
            )

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

        # ── NON-WORKING DAYS ──────────────────────────────────────────────
        #
        # Checked against the day the shift STARTS, not against `now`. A
        # 22:00-06:00 shift beginning on Saturday night runs into Sunday; it
        # is Saturday's shift and it should be captured normally even when
        # Sunday is the weekly off. Testing `now` instead would cut that
        # shift in half at midnight — and only for overnight workers, which
        # is exactly the kind of fault nobody notices for weeks.
        #
        # This sits after the overnight normalisation above so shift_start
        # is already the real start, including the roll back to yesterday.
        day_off = day_off_reason(shift_start.date())
        if day_off:
            self._scheduled_until = end_of_ist_day(now)
            LoggerService.log_verbose(
                f"SchedulerService: {shift_start.strftime('%d %b')} is a non-working "
                f"day ({day_off}) — no captures scheduled"
            )
            self._arm_timers([], now)
            return

        # ── CAPTURE WINDOW — THE SHIFT, AND ONLY THE SHIFT ────────────────
        #
        # The Configuration page promises "Screenshots are only scheduled
        # inside this window (IST)". This is what makes that true.
        #
        # There used to be off-shift coverage: an employee logged in outside
        # their shift was captured anyway, on a separate cadence. That was
        # added to fix a real gap — someone logged in from 00:40 to 09:12 got
        # no screenshots at all — but it contradicted the label, and it meant
        # capturing people outside the hours the admin had defined. On
        # personal machines that is somebody's own time.
        #
        # The shift is the admin's decision and it now means what it says:
        # inside it, capture; outside it, nothing.
        day_end = end_of_ist_day(now)

        if now >= shift_end:
            # Today's shift is over. Nothing more today; the midnight
            # rollover plans tomorrow against tomorrow's budget.
            self._scheduled_until = day_end
            LoggerService.log_verbose(
                f"SchedulerService: shift ended at {shift_end.strftime('%H:%M')} IST — "
                f"no further captures today "
                f"({ScreenshotManager.captures_today()}/"
                f"{ScreenshotManager.screenshots_per_day()} used)"
            )
            self._arm_timers([], now)
            return

        # Before the shift, plan from its start — the timers simply fire
        # later. Inside it, plan from now. Either way the window ends with
        # the shift, capped at the IST day boundary so an overnight shift
        # spends tonight's budget tonight and gets a fresh one after
        # midnight.
        window_start = max(now, shift_start)
        window_end   = min(shift_end, day_end)
        label = "in-shift" if now >= shift_start else "before shift"

        timestamps = ScreenshotManager.generate_daily_schedule(window_start, window_end)

        LoggerService.log_verbose(
            f"SchedulerService: {label} — {len(timestamps)} capture(s) planned "
            f"between {window_start.strftime('%d %b %H:%M')} and "
            f"{window_end.strftime('%d %b %H:%M')} IST "
            f"({ScreenshotManager.captures_today()}/"
            f"{ScreenshotManager.screenshots_per_day()} used today)"
        )
        self._scheduled_until = window_end
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
                SyncManager.push_idle_totals()
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

    def _apply_new_config(self, config: dict, changed_keys: set[str] | None = None):
        """
        Server se naya config aaya — settings update karo.

        NOTE: ConfigSyncManager isko background thread se call karta hai,
        is liye yahan koi bhi QObject/QTimer creation seedha nahi karna —
        reschedule() ko main thread pe QMetaObject.invokeMethod se hi
        trigger karo. Saath hi, sirf tab reschedule karo jab values
        actually badli hon — warna har poll (5s) pe naya schedule
        generate hoga, chahe server same config hi baar baar bheje.

        `changed_keys` comes from ConfigSyncManager._persist_config, which is
        the only code that still holds the previous values by the time this
        runs. This method used to work it out by reading the settings table —
        but it is called after those settings have already been written, so
        it compared each new value against itself, found no difference, and
        skipped every reschedule. A changed Screenshots per day was stored
        and then ignored until the next midnight rollover or app restart,
        which is why config edits appeared to need a logout to take effect.
        """
        keys = changed_keys or set()

        # These matter to the schedule: how many captures, how far apart, and
        # which days are working days at all.
        changed = bool(keys & {
            "screenshot_min_minutes",
            "screenshot_max_minutes",
            "screenshots_per_day",
            "weekly_offs",
            "holidays",
        })

        count = config.get("screenshots_per_day")
        min_m = config.get("screenshot_min_minutes")
        max_m = config.get("screenshot_max_minutes")

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

    def _handle_force_logout(self, reason: str = ""):
        self._logout_reason = reason
        QMetaObject.invokeMethod(self, "_emit_force_logout", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _emit_force_logout(self):
        self.force_logout.emit(getattr(self, "_logout_reason", "") or "")


    def reschedule(self):
        for t in self._pending_timers:
            t.stop()
        self._pending_timers.clear()
        self._schedule_shift_screenshots()
        LoggerService.log_verbose("SchedulerService: rescheduled after config change")

