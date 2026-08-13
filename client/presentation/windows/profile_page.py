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

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QFileDialog, QMessageBox, QComboBox,
    QCheckBox, QSizePolicy,
)

from client.core import http as _http
from client.core.config import API_BASE_URL, APP_VERSION
from client.application.managers.session_manager import SessionManager
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService
from client.presentation import theme as _theme
from client.presentation.theme import C, R_SM, button, scrollbar
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


class _Avatar(QLabel):
    """The photo, drawn round, with the person's initials until one exists."""

    SIZE = 96

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._pixmap: QPixmap | None = None
        self._initials = "?"
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._restyle()

    def _restyle(self):
        self.setStyleSheet(
            f"QLabel{{background:{C.PRIMARY_DIM};color:#ffffff;"
            f"border-radius:{self.SIZE // 2}px;font-size:30px;font-weight:700;"
            f"border:none;}}")

    def set_initials(self, name: str):
        parts = [p for p in str(name or "").split() if p]
        self._initials = ("".join(p[0] for p in parts[:2]) or "?").upper()
        if self._pixmap is None:
            self.setText(self._initials)

    def set_image(self, data: bytes | None):
        if not data:
            self._pixmap = None
            self.setPixmap(QPixmap())
            self.setText(self._initials)
            self._restyle()
            return
        source = QPixmap()
        if not source.loadFromData(data):
            return
        # Scaled and clipped here rather than by a stylesheet: a round mask in
        # CSS leaves the corners of the image showing through on some
        # platforms, which looks like a rendering fault.
        side = self.SIZE
        source = source.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        canvas = QPixmap(side, side)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, side, side)
        painter.setClipPath(path)
        painter.drawPixmap(
            (side - source.width()) // 2, (side - source.height()) // 2, source)
        painter.end()
        self._pixmap = canvas
        self.setText("")
        self.setStyleSheet("QLabel{background:transparent;border:none;}")
        self.setPixmap(canvas)


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
        worker.fail.connect(on_fail or (lambda error: self._toast(error, ok=False)))
        worker.finished.connect(
            lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _toast(self, message: str, ok: bool = True):
        """One line under the header. Not a modal — saving a phone number
        should not need dismissing."""
        self._status.setText(("✓  " if ok else "⚠  ") + str(message))
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
        ], note="Only your phone number and photo are yours to change. "
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

        self._avatar = _Avatar()
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

        phone_row = QHBoxLayout()
        phone_row.setSpacing(8)
        label = QLabel("Phone")
        label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
        self._phone = QLineEdit()
        self._phone.setPlaceholderText("+91 98765 43210")
        self._phone.setMaximumWidth(220)
        save = QPushButton("Save")
        save.setStyleSheet(button("primary"))
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save_phone)
        phone_row.addWidget(label)
        phone_row.addWidget(self._phone)
        phone_row.addWidget(save)
        phone_row.addStretch()
        col.addSpacing(8)
        col.addLayout(phone_row)

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
            f"color:{C.TEXT_DIM};font-size:11px;font-weight:700;"
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
            hint.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
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
                f"color:{C.TEXT_DIM};font-size:11px;border:none;")
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
            f"color:{C.TEXT_DIM};font-size:11px;font-weight:700;"
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
            f"color:{C.TEXT_DIM};font-size:11px;font-weight:700;"
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
        hint.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
        layout.addWidget(hint)
        return card

    def _history_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)
        heading = QLabel("RECENT SIGN-INS")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;font-weight:700;"
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

        # Not overwritten while somebody is typing in it.
        if not self._phone.hasFocus():
            self._phone.setText(str(profile.get("phone") or ""))

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

    def _save_phone(self):
        value = self._phone.text().strip()

        def send():
            response = _http.patch(f"{API_BASE_URL}/profile/me",
                                   json={"phone": value},
                                   headers={**_headers(), "Content-Type": "application/json"},
                                   timeout=15)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(response, "Could not save that."))
            return value

        self._run(send, lambda _v: self._toast("Phone number saved."))

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

        self._run(send, lambda _ok: (self._toast("Photo updated."), self.refresh()))

    def _remove_photo(self):
        def send():
            response = _http.delete(f"{API_BASE_URL}/profile/me/photo",
                                    headers=_headers(), timeout=15)
            if response.status_code != 200:
                raise RuntimeError("Could not remove the photo.")
            return True

        self._run(send, lambda _ok: (self._avatar.set_image(None),
                                     self._toast("Photo removed.")))

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
