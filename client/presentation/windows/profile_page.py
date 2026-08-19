"""
My Profile — a person's own account, in the employee panel.

WHAT THIS PAGE IS ALLOWED TO CHANGE

Two things: the phone number and the photo. Everything else it shows —
employee id, role, designation, department, manager, joining date, employment
status, hours, attendance — is the company's record, and it is drawn read-only
here. The server enforces the same line (see profile.controller); this page
does not rely on that, and the server does not rely on this. Either alone
would be a mistake.

WHERE THE NUMBERS COME FROM

Nothing is computed here. Every figure arrives from /api/profile/me/work-summary,
which reads the same attendance, activity, idle and screenshot tables the
dashboard and the reports read, through the same IST day boundary. A second
calculation in the client is how two screens start disagreeing about the same
day.

The seven-day charts reuse `Sparkline` from the shared widgets — the trend
line already used on the dashboard cards — rather than introducing a charting
library for four small graphs.
"""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QFileDialog, QMessageBox, QComboBox,
    QCheckBox, QSizePolicy, QInputDialog,
)

from client.core import http as _http
from client.core.config import API_BASE_URL, APP_VERSION
from client.application.managers.session_manager import SessionManager
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService
from client.presentation import theme as _theme
from client.presentation.theme import C, button, scrollbar
from client.presentation.widgets.avatar import Avatar, forget as forget_avatar
from client.presentation.widgets.panel_widgets import Card, PageHeader, Sparkline

# The keys live with the notification decisions, in application/services/
# notifier — the same constants the panels read when something arrives. A
# second copy here is how a switch starts saving a value nothing looks at.
from client.application.services.notifier import (
    PREF_DESKTOP, PREF_SOUND, PREF_CHAT, PREF_ALERTS, pref_enabled,
)

PHOTO_MAX_BYTES = 5 * 1024 * 1024


class _Worker(QThread):
    """One network call, off the UI thread — the pattern used on every other
    page here. A profile page that blocks while a photo uploads is a page
    people think has crashed."""

    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as error:
            self.fail.emit(str(error))


def _headers() -> dict:
    return {"Authorization": f"Bearer {SessionManager.auth_token}"}


def _seconds_as_hours(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    hours, rest = divmod(max(0, seconds), 3600)
    return f"{hours}h {rest // 60:02d}m"


class ProfilePage(QWidget):
    """The page itself. `panel` is the EmployeePanel, used only to reach the
    change-password dialog and the sign-out it already owns."""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._rows: dict[str, QLabel] = {}
        self._workers: list[_Worker] = []
        self._profile: dict = {}
        self._build()

    # ── plumbing ────────────────────────────────────────────────────────

    def _run(self, fn, on_done, on_fail=None, *args, **kwargs):
        worker = _Worker(fn, *args, **kwargs)
        worker.done.connect(on_done)
        worker.fail.connect(on_fail or self._on_failure)
        worker.finished.connect(
            lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_failure(self, error: str):
        """What a person sees when something did not work.

        NOT the exception. The first render of this page put
        "HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded..."
        across the top in orange — true, useless, and alarming. The detail
        belongs in the log, where somebody looking into it will find it; the
        screen gets a sentence and a way forward.
        """
        text = str(error or "")
        # TO A FILE, AND ONCE.
        #
        # LoggerService.log and log_verbose both write a row into the queue
        # that is uploaded, so a page that logs every failed refresh puts a
        # line in the company's audit log each time somebody opens it. Sixty-
        # eight rows of "Could not load your devices" turned up that way,
        # which is the same flood the screenshot queue produced and was fixed
        # for. A failure that keeps repeating is one fact, not sixty-eight.
        if text != getattr(self, "_last_failure", None):
            self._last_failure = text
            try:
                LoggerService._fallback_critical_log(f"ProfilePage: {text}")
            except Exception:
                pass
        network = any(word in text.lower() for word in
                      ("connection", "timed out", "timeout", "unreachable",
                       "max retries", "temporarily"))
        self._toast(
            "Could not reach the server — this will load when the connection is back."
            if network else (text if len(text) < 120 else "Something went wrong."),
            ok=False)

    def _toast(self, message: str, ok: bool = True):
        """One line under the header. Not a modal — saving a phone number
        should not need dismissing."""
        self._status.setText(("" if ok else "") + str(message))
        self._status.setStyleSheet(
            f"color:{C.GREEN if ok else C.AMBER};font-size:12px;border:none;"
            f"background:transparent;")
        self._status.setVisible(True)

    # ── layout ──────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader("My Profile", "Your account, your devices and your week."))

        self._status = QLabel("")
        self._status.setVisible(False)
        root.addWidget(self._status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar())
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(14)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        body.addWidget(self._identity_card())
        body.addWidget(self._section("Personal Information", [
            ("Employee ID", "employee_id"), ("Username", "username"),
            ("Department", "department"), ("Team", "team"),
            ("Designation", "designation"), ("Reporting manager", "reporting_manager"),
            ("Joining date", "joining_date"), ("Employment status", "employment_status"),
        ], note="Only your phone number, email and photo are yours to change. "
                "Everything else is set by your administrator."))
        body.addWidget(self._work_card())
        body.addWidget(self._devices_card())
        body.addWidget(self._security_card())
        body.addWidget(self._preferences_card())
        body.addWidget(self._history_card())
        body.addStretch()

    def _identity_card(self) -> Card:
        card = Card()
        outer = QHBoxLayout(card)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(18)

        self._avatar = Avatar(96)
        outer.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        self._name = QLabel("—")
        self._name.setStyleSheet(
            f"color:{C.TEXT};font-size:20px;font-weight:700;border:none;")
        col.addWidget(self._name)
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
        col.addWidget(self._subtitle)

        photo_row = QHBoxLayout()
        photo_row.setSpacing(8)
        for text, slot in (("Change photo", self._pick_photo),
                           ("Remove", self._remove_photo)):
            btn = QPushButton(text)
            btn.setStyleSheet(button("secondary"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            photo_row.addWidget(btn)
        photo_row.addStretch()
        col.addSpacing(6)
        col.addLayout(photo_row)

        # Phone and email together, saved by one button.
        #
        # Two Save buttons for two adjacent boxes is two requests and two ways
        # to leave half a change behind — somebody who edits both and presses
        # the first one has saved half of what they meant to.
        def _contact_row(caption, placeholder, width=240):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(caption)
            label.setFixedWidth(52)
            label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            field.setMaximumWidth(width)
            row.addWidget(label)
            row.addWidget(field)
            row.addStretch()
            return row, field

        phone_row, self._phone = _contact_row("Phone", "+91 98765 43210", 220)
        email_row, self._email = _contact_row("Email", "you@company.com")

        # WHETHER THE ADDRESS IS PROVED, beside the box that holds it.
        #
        # An address somebody typed and an address somebody proved are not the
        # same thing, and the difference only matters at the moment something
        # is sent to it. Saying so here — rather than nowhere — is what stops
        # an unverified address being treated as a working one later.
        self._email_state = QLabel("")
        self._email_state.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:12px;border:none;background:transparent;")
        self._verify_btn = QPushButton("Verify")
        self._verify_btn.setStyleSheet(button("secondary"))
        self._verify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._verify_btn.clicked.connect(self._start_email_verification)
        self._verify_btn.hide()
        email_row.insertWidget(2, self._email_state)
        email_row.insertWidget(3, self._verify_btn)
        for field in (self._phone, self._email):
            field.returnPressed.connect(self._save_contact)

        save = QPushButton("Save")
        save.setStyleSheet(button("primary"))
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_contact)
        phone_row.insertWidget(2, save)

        col.addSpacing(8)
        col.addLayout(phone_row)
        col.addSpacing(4)
        col.addLayout(email_row)

        outer.addLayout(col, 1)
        return card

    def _section(self, title, fields, note="", button_text="", button_slot=None) -> Card:
        """The same shape the Settings page uses, so the two read as one
        product rather than two."""
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        heading = QLabel(title.upper())
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:700;"
            f"letter-spacing:1px;border:none;")
        layout.addWidget(heading)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(9)
        for label_text, key in fields:
            row = grid.rowCount()
            name = QLabel(label_text)
            name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
            value = QLabel("—")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setStyleSheet(
                f"color:{C.TEXT};font-size:13px;font-weight:600;border:none;")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._rows[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        if button_text:
            row = QHBoxLayout()
            row.addStretch()
            btn = QPushButton(button_text)
            btn.setStyleSheet(button("secondary"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if button_slot:
                btn.clicked.connect(button_slot)
            row.addWidget(btn)
            layout.addLayout(row)

        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;")
            layout.addWidget(hint)
        return card

    def _work_card(self) -> Card:
        card = self._section("Work Summary", [
            ("Signed in today", "today_login"),
            ("Worked today", "today_worked"),
            ("Active today", "today_active"),
            ("Idle today", "today_idle"),
            ("Screenshots today", "today_shots"),
            ("This week", "week_hours"),
            ("Days present this week", "week_days"),
            ("This month", "month_hours"),
            ("Average day this month", "month_avg"),
            ("Attendance this month", "month_attendance"),
        ])
        layout = card.layout()

        charts = QGridLayout()
        charts.setHorizontalSpacing(18)
        charts.setVerticalSpacing(6)
        self._charts: dict[str, Sparkline] = {}
        for column, (key, caption, colour) in enumerate((
                ("worked", "Hours worked  ·  last 7 days", C.GREEN),
                ("idle", "Idle  ·  last 7 days", C.AMBER),
                ("shots", "Screenshots  ·  last 7 days", C.BLUE))):
            caption_label = QLabel(caption)
            caption_label.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;border:none;")
            spark = Sparkline(colour, points=7)
            self._charts[key] = spark
            charts.addWidget(caption_label, 0, column)
            charts.addWidget(spark, 1, column)
        layout.addSpacing(6)
        layout.addLayout(charts)
        return card

    def _devices_card(self) -> Card:
        card = self._section("This Device", [
            ("Device", "device_name"), ("Operating system", "device_os"),
            ("App version", "device_version"), ("Last login", "device_login"),
            ("Last seen", "device_seen"), ("Current status", "device_status"),
        ])
        layout = card.layout()
        heading = QLabel("SIGNED IN ON")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:700;"
            f"letter-spacing:1px;border:none;")
        layout.addSpacing(8)
        layout.addWidget(heading)
        self._sessions_box = QVBoxLayout()
        self._sessions_box.setSpacing(6)
        layout.addLayout(self._sessions_box)
        return card

    def _security_card(self) -> Card:
        card = self._section("Security", [
            ("Password last changed", "password_changed"),
        ], note="Signing out everywhere ends this session too — you will be asked "
                "to sign in again on this machine.")
        layout = card.layout()
        row = QHBoxLayout()
        row.addStretch()
        for text, slot, kind in (("Change password", self._change_password, "secondary"),
                                 ("Sign out on all devices", self._logout_all, "danger")):
            btn = QPushButton(text)
            btn.setStyleSheet(button(kind))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        layout.insertLayout(layout.count() - 1, row)
        return card

    def _preferences_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)
        heading = QLabel("NOTIFICATIONS & APPEARANCE")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:700;"
            f"letter-spacing:1px;border:none;")
        layout.addWidget(heading)

        self._checks: dict[str, QCheckBox] = {}
        for key, text in ((PREF_DESKTOP, "Desktop notifications"),
                          (PREF_SOUND, "Play a sound"),
                          (PREF_CHAT, "Messages"),
                          (PREF_ALERTS, "Administrative alerts")):
            box = QCheckBox(text)
            box.setChecked(pref_enabled(key))
            box.setStyleSheet(f"color:{C.TEXT};font-size:13px;border:none;")
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            box.toggled.connect(lambda on, k=key: self._save_pref(k, on))
            self._checks[key] = box
            layout.addWidget(box)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme")
        theme_label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
        self._theme = QComboBox()
        self._theme.addItems(["Dark", "Light"])
        self._theme.setCurrentText(_theme.current_theme().title())
        self._theme.setMaximumWidth(160)
        self._theme.currentTextChanged.connect(self._change_theme)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self._theme)
        theme_row.addStretch()
        layout.addSpacing(6)
        layout.addLayout(theme_row)

        hint = QLabel("These are settings for this machine, and are kept on it.")
        hint.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;")
        layout.addWidget(hint)
        return card

    def _history_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)
        heading = QLabel("RECENT SIGN-INS")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:700;"
            f"letter-spacing:1px;border:none;")
        layout.addWidget(heading)
        self._history_box = QVBoxLayout()
        self._history_box.setSpacing(6)
        layout.addLayout(self._history_box)
        empty = QLabel("Nothing yet.")
        empty.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;")
        self._history_box.addWidget(empty)
        return card

    # ── loading ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        self._run(self._fetch_profile, self._on_profile)
        self._run(self._fetch_summary, self._on_summary)
        self._run(self._fetch_sessions, self._on_sessions)

    @staticmethod
    def _fetch_profile():
        response = _http.get(f"{API_BASE_URL}/profile/me", headers=_headers(), timeout=15)
        if response.status_code != 200:
            raise RuntimeError("Could not load your profile — check the connection.")
        return response.json().get("profile") or {}

    @staticmethod
    def _fetch_summary():
        response = _http.get(f"{API_BASE_URL}/profile/me/work-summary",
                             headers=_headers(), timeout=20)
        if response.status_code != 200:
            raise RuntimeError("Could not load your work summary.")
        return response.json()

    @staticmethod
    def _fetch_sessions():
        response = _http.get(f"{API_BASE_URL}/profile/me/sessions",
                             headers=_headers(), timeout=15)
        if response.status_code != 200:
            raise RuntimeError("Could not load your devices.")
        return response.json()

    @staticmethod
    def _fetch_photo():
        response = _http.get(f"{API_BASE_URL}/profile/photo",
                             headers=_headers(), timeout=20)
        return response.content if response.status_code == 200 else None

    def _on_profile(self, profile: dict):
        self._last_failure = None
        self._profile = profile or {}
        name = profile.get("full_name") or profile.get("username") or "—"
        self._name.setText(str(name))
        self._avatar.set_initials(str(name))
        bits = [profile.get("designation"), profile.get("role", "").replace("_", " ").title()]
        self._subtitle.setText("  ·  ".join(b for b in bits if b))

        for key in ("employee_id", "username", "department", "team", "designation",
                    "reporting_manager", "joining_date", "employment_status"):
            value = profile.get(key)
            if key == "joining_date" and value:
                value = str(value)[:10]
            if key == "employment_status" and value:
                value = str(value).replace("_", " ").title()
            self._rows[key].setText(str(value) if value else "—")

        # Not overwritten while somebody is typing in it. A refresh landing
        # mid-edit would replace what they were half way through writing.
        if not self._phone.hasFocus():
            self._phone.setText(str(profile.get("phone") or ""))
        if not self._email.hasFocus():
            self._email.setText(str(profile.get("email") or ""))
        self._show_email_state(profile.get("email"),
                               bool(profile.get("email_verified")))

        changed = str(profile.get("password_changed_at") or "")
        self._rows["password_changed"].setText(changed[:10] if changed else "Never")

        if profile.get("photo"):
            self._run(self._fetch_photo, self._avatar.set_image, lambda _e: None)
        else:
            self._avatar.set_image(None)

    def _on_summary(self, data: dict):
        today = data.get("today") or {}
        week = data.get("week") or {}
        month = data.get("month") or {}

        login = str(today.get("login_time") or "")
        self._rows["today_login"].setText(login[11:16] if len(login) > 15 else "—")
        self._rows["today_worked"].setText(_seconds_as_hours(today.get("worked_seconds")))
        self._rows["today_active"].setText(_seconds_as_hours(today.get("active_seconds")))
        self._rows["today_idle"].setText(_seconds_as_hours(today.get("idle_seconds")))
        self._rows["today_shots"].setText(str(today.get("screenshots", 0)))
        self._rows["week_hours"].setText(_seconds_as_hours(week.get("worked_seconds")))
        self._rows["week_days"].setText(f"{week.get('days_present', 0)} of 7")
        self._rows["month_hours"].setText(_seconds_as_hours(month.get("worked_seconds")))
        self._rows["month_avg"].setText(_seconds_as_hours(month.get("average_daily_seconds")))
        self._rows["month_attendance"].setText(f"{month.get('attendance_percent', 0)}%")

        series = data.get("last_7_days") or []
        self._charts["worked"].set_series([d.get("worked_seconds", 0) / 3600 for d in series])
        self._charts["idle"].set_series([d.get("idle_seconds", 0) / 3600 for d in series])
        self._charts["shots"].set_series([d.get("screenshots", 0) for d in series])

    def _on_sessions(self, data: dict):
        sessions = data.get("sessions") or []
        history = data.get("history") or []

        import platform
        self._rows["device_name"].setText(platform.node() or "—")
        self._rows["device_os"].setText(f"{platform.system()} {platform.release()}")
        self._rows["device_version"].setText(str(APP_VERSION))

        mine = next((s for s in sessions if s.get("is_this_device")), None) or \
            (sessions[0] if sessions else {})
        self._rows["device_login"].setText(str(mine.get("login_time") or "—")[:19])
        self._rows["device_seen"].setText(str(mine.get("last_seen") or "—")[:19])
        self._rows["device_status"].setText("Online" if mine.get("is_live") else "Offline")

        self._fill(self._sessions_box, [
            f"{'▸ this device  ·  ' if s.get('is_this_device') else ''}"
            f"{s.get('device_id') or 'unknown device'}"
            f"{'  ·  ' + s['ip'] if s.get('ip') else ''}"
            f"  ·  since {str(s.get('login_time') or '')[:16]}"
            f"  ·  {'active' if s.get('is_live') else 'idle'}"
            for s in sessions] or ["No other device."])

        self._fill(self._history_box, [
            f"{str(h.get('login_time') or '')[:16]}"
            f"  →  {str(h.get('logout_time') or 'still open')[:16]}"
            for h in history] or ["Nothing yet."])

    def _fill(self, box: QVBoxLayout, lines: list[str]):
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for line in lines:
            label = QLabel(line)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;border:none;")
            box.addWidget(label)

    # ── the two things this page may change ─────────────────────────────

    def _show_email_state(self, email, verified: bool):
        """The tick, or the button that earns it."""
        if not email:
            self._email_state.setText("")
            self._verify_btn.hide()
            return
        if verified:
            self._email_state.setText(" Verified")
            self._email_state.setStyleSheet(
                f"color:{C.GREEN};font-size:12px;font-weight:600;"
                "border:none;background:transparent;")
            self._verify_btn.hide()
        else:
            self._email_state.setText("Not verified")
            self._email_state.setStyleSheet(
                f"color:{C.TEXT_MUTED};font-size:12px;"
                "border:none;background:transparent;")
            self._verify_btn.show()

    def _start_email_verification(self):
        """Ask the server to send a code, then ask for it back.

        The code is never in any reply — it goes to the mailbox and nowhere
        else, which is the entire point. So this cannot check anything itself;
        it carries what was typed to the server and reports what the server
        says.
        """
        self._verify_btn.setEnabled(False)

        def request():
            response = _http.post(f"{API_BASE_URL}/profile/me/email/code",
                                  headers=_headers(), timeout=30)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(
                    response, "The code could not be sent."))
            return response.json()

        def sent(data):
            self._verify_btn.setEnabled(True)
            where = data.get("sent_to") or self._email.text().strip()
            minutes = data.get("valid_minutes") or 10
            code, ok = QInputDialog.getText(
                self, "Verify your email",
                f"A six-digit code has been sent to\n{where}\n\n"
                f"It is valid for {minutes} minutes.\n\nEnter the code:")
            if not ok or not str(code).strip():
                return
            self._submit_email_code(str(code).strip())

        def failed(error):
            self._verify_btn.setEnabled(True)
            self._toast(str(error), ok=False)

        self._run(request, sent, failed)

    def _submit_email_code(self, code: str):
        def send():
            response = _http.post(
                f"{API_BASE_URL}/profile/me/email/verify",
                json={"code": code},
                headers={**_headers(), "Content-Type": "application/json"},
                timeout=20)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(
                    response, "That code was not accepted."))
            return True

        self._run(send, lambda _ok: (self._toast("Email verified."),
                                     self.refresh()))

    def _save_contact(self):
        phone = self._phone.text().strip()
        email = self._email.text().strip()

        # CHECKED HERE TOO, and not only on the server. A typed address with
        # no @ in it is the commonest mistake there is, and finding out after
        # a round trip — on a connection that is 200 ms away, see the latency
        # this product runs at — is a worse way to be told.
        if email and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
            self._toast("That does not look like an email address.", ok=False)
            return

        def send():
            response = _http.patch(
                f"{API_BASE_URL}/profile/me",
                json={"phone": phone, "email": email},
                headers={**_headers(), "Content-Type": "application/json"},
                timeout=15)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(response, "Could not save that."))
            return True

        self._run(send, lambda _v: self._toast("Saved."))

    def _pick_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a photo", "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError as error:
            self._toast(f"Could not read that file — {error}", ok=False)
            return
        # Checked here as well as on the server: telling somebody a 20 MB
        # photo is too large after uploading it over their connection is not
        # the same as telling them before.
        if size > PHOTO_MAX_BYTES:
            self._toast("That image is larger than 5 MB — choose a smaller one.", ok=False)
            return

        def send():
            kind = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            with open(path, "rb") as handle:
                response = _http.post(
                    f"{API_BASE_URL}/profile/me/photo",
                    files={"photo": (os.path.basename(path), handle.read(), kind)},
                    headers=_headers(), timeout=60)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(response, "That photo was not accepted."))
            return True

        self._run(send, lambda _ok: (forget_avatar(SessionManager.employee_id),
                                     self._toast("Photo updated."),
                                     self._redraw_everywhere()))

    def _remove_photo(self):
        def send():
            response = _http.delete(f"{API_BASE_URL}/profile/me/photo",
                                    headers=_headers(), timeout=15)
            if response.status_code != 200:
                raise RuntimeError("Could not remove the photo.")
            return True

        self._run(send, lambda _ok: (forget_avatar(SessionManager.employee_id),
                                     self._toast("Photo removed."),
                                     self._redraw_everywhere()))

    def _redraw_everywhere(self):
        """Put the new picture on this page AND on the header behind it.

        The cache is cleared first, so this re-asks the server once and every
        Avatar drawn afterwards — the team page, a message, the next panel
        opened — gets the new picture from that one answer.
        """
        self.refresh()
        header = getattr(self._panel, "_header_avatar", None)
        if header is not None and hasattr(header, "show_person"):
            header.show_person(
                SessionManager.employee_id,
                getattr(SessionManager, "full_name", None) or SessionManager.employee_id)

    # ── security ────────────────────────────────────────────────────────

    def _change_password(self):
        from client.presentation.windows.change_password_dialog import ChangePasswordDialog
        ChangePasswordDialog(self).exec()

    def _logout_all(self):
        answer = QMessageBox.question(
            self, "Sign out everywhere",
            "This signs you out on every device, including this one.\n\n"
            "You will need to sign in again here. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        def send():
            response = _http.post(f"{API_BASE_URL}/profile/me/logout-all",
                                  headers=_headers(), timeout=15)
            if response.status_code != 200:
                raise RuntimeError("Could not sign out everywhere.")
            return True

        def done(_ok):
            LoggerService.log("LOGOUT ALL DEVICES : requested by the employee")
            # The token this app is holding is dead now — that is the point of
            # the button — so the panel has to return to the login screen
            # rather than carry on with a session that no longer exists.
            self._panel.logout("You have been signed out on every device.")

        self._run(send, done)

    # ── preferences ─────────────────────────────────────────────────────

    def _save_pref(self, key: str, enabled: bool):
        try:
            SettingsService.save_setting(key, "1" if enabled else "0")
            self._toast("Saved.")
        except Exception as error:
            self._toast(f"Could not save that — {error}", ok=False)

    def _change_theme(self, name: str):
        try:
            _theme.save_theme(name.lower())
            self._toast(f"{name} theme saved — it applies when the panel is rebuilt.")
        except Exception as error:
            self._toast(f"Could not change the theme — {error}", ok=False)

    @staticmethod
    def _message_from(response, fallback: str) -> str:
        try:
            return response.json().get("message") or fallback
        except Exception:
            return fallback
