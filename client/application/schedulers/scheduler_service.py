from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal, QMetaObject, Qt, Slot

from client.application.managers.sync_manager import SyncManager
from client.application.managers.config_sync_manager import ConfigSyncManager
from client.application.managers.screenshot_manager import ScreenshotManager
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

        now = datetime.now()

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

        # Shift timings SessionManager se
        shift_start_str = SessionManager.shift_start
        shift_end_str   = SessionManager.shift_end

        if shift_start_str and shift_end_str:
            try:
                # ISO format parse karo
                shift_start = datetime.fromisoformat(shift_start_str)
                shift_end   = datetime.fromisoformat(shift_end_str)

                # BUG FIX: Server/ConfigSync kabhi kabhi shift ke saath
                # PURANI (ya kisi bhi) date bhej deta hai — e.g. aaj 30th hai
                # lekin payload me "2026-06-29T09:00:00+05:30" aata hai.
                # Ye ek REPEATING DAILY shift hai (sirf 09:00-18:00 time-of-day
                # matter karta hai, date nahi) — lekin pehle code us date ko
                # literally use kar leta tha, is wajah se shift turant hi
                # "already ended" ban jaati thi aur saare pending screenshot
                # timers reschedule() me cancel ho ke khaali reh jaate the
                # (ye poore app.log me sabse bada pattern hai — har
                # "shift updated" ke baad "shift already ended, no
                # screenshots scheduled" aa raha tha).
                # Fix: date hamesha AAJ ki date se replace karo, server jo
                # bhi date bheje uska koi farak na pade.
                today = now.date()
                shift_start = shift_start.replace(
                    year=today.year, month=today.month, day=today.day
                )
                shift_end = shift_end.replace(
                    year=today.year, month=today.month, day=today.day
                )
            except Exception:
                # Fallback: aaj ki date pe HH:MM format
                try:
                    shift_start = datetime.strptime(
                        f"{now.date()} {shift_start_str}", "%Y-%m-%d %H:%M"
                    )
                    shift_end = datetime.strptime(
                        f"{now.date()} {shift_end_str}", "%Y-%m-%d %H:%M"
                    )
                except Exception:
                    shift_start = now
                    shift_end   = now + timedelta(hours=8)
        else:
            # Shift info nahi mili — login time se 8 ghante
            shift_start = now
            shift_end   = now + timedelta(hours=8)
            LoggerService.log_verbose("SchedulerService: shift times not found, using 8hr window from now")

        # Timezone fix: dono ko naive bana do comparison ke liye
        if shift_start.tzinfo is not None:
            shift_start = shift_start.replace(tzinfo=None)
        if shift_end.tzinfo is not None:
            shift_end = shift_end.replace(tzinfo=None)

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
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)

        # BUG FIX (overnight shifts): upar hum shift ki date hamesha AAJ ki
        # date se replace karte hain. Overnight shift (e.g. 22:00–06:00) me
        # agar abhi 00:30 baje hain, to employee KAL raat shuru hui shift ke
        # BEECH me hai — lekin normalized window "aaj 22:00 → kal 06:00"
        # ban jaata hai, jo abhi se ~21 ghante door hai. Result: chal rahi
        # overnight shift ke liye ek bhi screenshot schedule nahi hota.
        # Fix: agar pichhle din ka window `now` ko contain karta hai, to
        # usi window ko use karo.
        if now < shift_start:
            previous_start = shift_start - timedelta(days=1)
            previous_end = shift_end - timedelta(days=1)
            if previous_start <= now < previous_end:
                shift_start, shift_end = previous_start, previous_end

        # ── OFF-SHIFT COVERAGE ────────────────────────────────────────────
        #
        #  BUG (production me pakda gaya): employee agar apni shift window ke
        #  BAHAR logged in ho, to pehle ek bhi screenshot schedule nahi hota
        #  tha — na abhi ke liye, na kuch der baad ke liye.
        #
        #  Asli case: EMP002 ki shift 09:00–23:59 thi. Wo raat 12:40 baje
        #  login hua. `effective_start` = max(09:00, 00:40) = 09:00 (agli
        #  subah) ban gaya, aur pehla screenshot 09:00–11:29 ke beech kahin
        #  schedule hua. Employee 09:12 pe logout ho gaya — matlab 8 GHANTE
        #  31 MINUTE ka poora kaam bina kisi screenshot ke nikal gaya.
        #
        #  Ek monitoring product ka poora maqsad hi khatam ho jaata hai agar
        #  wo tab andha ho jaye jab employee off-hours kaam kare. Ab: agar
        #  employee logged in hai lekin shift ke bahar hai, to shift shuru
        #  hone tak (ya agle rollover tak) configured min–max cadence pe
        #  captures lete rahenge. Shift shuru hote hi normal slot-based
        #  schedule automatically le leta hai (rollover ke through).
        # ──────────────────────────────────────────────────────────────────
        if now < shift_start:
            # Shift abhi shuru nahi hui — tab tak off-shift coverage.
            LoggerService.log_verbose(
                f"SchedulerService: off-shift ({now.strftime('%H:%M')}), shift "
                f"{shift_start.strftime('%H:%M')} baje shuru hogi — interim coverage on"
            )
            self._scheduled_until = shift_start
            self._arm_timers(
                ScreenshotManager.generate_interval_schedule(now, shift_start), now
            )
            return

        effective_start = max(shift_start, now)
        if effective_start >= shift_end:
            # Shift khatam ho chuki hai lekin employee abhi bhi kaam kar raha
            # hai — agle din ki shift tak off-shift coverage chalu rakho.
            next_start = shift_start + timedelta(days=1)
            LoggerService.log_verbose(
                f"SchedulerService: shift ended, employee still logged in — "
                f"off-shift coverage till {next_start.strftime('%d %b %H:%M')}"
            )
            self._scheduled_until = next_start
            self._arm_timers(
                ScreenshotManager.generate_interval_schedule(now, next_start), now
            )
            return

        self._scheduled_until = shift_end
        timestamps = ScreenshotManager.generate_random_schedule(effective_start, shift_end)

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

        if datetime.now() >= self._scheduled_until:
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
        count  = config.get("screenshot_count")

        old_min   = SettingsService.get_setting("screenshot_min_minutes")
        old_max   = SettingsService.get_setting("screenshot_max_minutes")
        old_count = SettingsService.get_setting("screenshot_count")

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
            SettingsService.save_setting("screenshot_count", str(count))

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

