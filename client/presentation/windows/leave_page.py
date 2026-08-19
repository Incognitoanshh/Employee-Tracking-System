"""
My Leave — asking for time off, and seeing what happened to it.

WHAT THIS PAGE IS FOR. Before it, a day off was arranged by message and the
system recorded it as an absence. The number that went into a report — and
into anybody's idea of who turns up — did not know the difference between a
day off with permission and a day somebody did not appear.

THE TWO RULES THE UI HAS TO CARRY, both the owner's decisions:

  * a request can be withdrawn while it is PENDING and not after. Once it is
    approved the roster has been planned around it, so the Cancel button is
    simply not there — rather than there and refused, which teaches people
    that buttons lie.

  * a rejection always carries a reason, and it is shown here. The point of
    writing it was that the employee reads it.

Everything is read-only apart from the form: an employee cannot change a
decision, a date on an approved request, or anybody else's anything. There is
no employee id in any request this page makes.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QDateEdit, QTextEdit, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QAbstractItemView,
)

from client.core import http as _http
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.presentation.theme import C, R_SM, button, scrollbar, table_style
from client.presentation.widgets.badge import badge_cell
from client.presentation.widgets.panel_widgets import Card, PageHeader, fit_columns

LEAVE_TYPES = [("CASUAL", "Casual"), ("SICK", "Sick"), ("UNPAID", "Unpaid")]

# The status colours used to live here, as a dict built at import time.
#
# TWO THINGS WERE WRONG WITH THAT. It was a third copy of a mapping the
# attendance page and payroll each had their own version of, so one green
# meant three things. And being built at import, it captured the DARK
# palette once and kept it — switching to the light theme left these five
# colours behind, which is the exact trap theme.scrollbar documents.
#
# theme.status_colors is looked up per call and is the only copy.


def _headers() -> dict:
    return {"Authorization": f"Bearer {SessionManager.auth_token}"}


class _Worker(QThread):
    """One request, off the UI thread. Same shape the profile page uses."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as error:            # noqa: BLE001 - reported, not raised
            self.failed.emit(str(error))


class LeavePage(QWidget):
    """Apply for leave, and the history of what was asked and decided."""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._workers: list[_Worker] = []
        self._rows: list[dict] = []
        self._build()

    # ── plumbing ────────────────────────────────────────────────────────
    def _run(self, fn, on_done, on_fail=None, *args, **kwargs):
        worker = _Worker(fn, *args, **kwargs)
        worker.done.connect(on_done)
        worker.failed.connect(on_fail or (lambda message: self._say(message, ok=False)))
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _say(self, message: str, ok: bool = True):
        self._status.setText(("" if ok else "") + message)
        self._status.setStyleSheet(
            f"color:{C.GREEN if ok else C.AMBER};font-size:12px;"
            f"background:transparent;border:none;")

    @staticmethod
    def _message_from(response, fallback: str) -> str:
        try:
            return response.json().get("message") or fallback
        except Exception:                      # noqa: BLE001
            return fallback

    # ── the page ────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(14)

        outer.addWidget(PageHeader(
            "My Leave",
            "Ask for time off, and see what was decided."))

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:12px;background:transparent;border:none;")
        outer.addWidget(self._status)

        outer.addWidget(self._form_card())
        outer.addWidget(self._history_card(), 1)

    def _form_card(self) -> Card:
        card = Card()
        column = QVBoxLayout(card)
        column.setContentsMargins(18, 16, 18, 16)
        column.setSpacing(10)

        heading = QLabel("APPLY")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:800;"
            f"letter-spacing:1px;background:transparent;border:none;")
        column.addWidget(heading)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._type = QComboBox()
        for value, label in LEAVE_TYPES:
            self._type.addItem(label, value)
        self._type.setFixedWidth(140)

        self._from = QDateEdit(QDate.currentDate())
        self._to = QDateEdit(QDate.currentDate())
        for field in (self._from, self._to):
            field.setCalendarPopup(True)
            field.setDisplayFormat("yyyy-MM-dd")
            field.setFixedWidth(140)
        # A DATE IN THE PAST IS ALLOWED, deliberately. Illness is reported
        # after the fact, and refusing it here would only mean it is arranged
        # by message and never recorded — which is the state this replaces.
        self._from.dateChanged.connect(self._keep_range_sane)

        self._half = QCheckBox("Half day")
        self._half.toggled.connect(self._on_half_toggled)

        for widget, label in ((self._type, "Type"), (self._from, "From"),
                              (self._to, "To")):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            caption = QLabel(label)
            caption.setStyleSheet(
                f"color:{C.TEXT_MUTED};font-size:12px;background:transparent;border:none;")
            cell.addWidget(caption)
            cell.addWidget(widget)
            row.addLayout(cell)
        row.addWidget(self._half)
        row.addStretch()
        column.addLayout(row)

        self._reason = QTextEdit()
        self._reason.setPlaceholderText(
            "Why — this is what the person approving it reads.")
        self._reason.setFixedHeight(64)
        self._reason.setStyleSheet(
            f"QTextEdit{{background:{C.ELEVATED};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;color:{C.TEXT};font-size:13px;padding:8px;}}")
        column.addWidget(self._reason)

        send = QPushButton("Apply for leave")
        send.setStyleSheet(button("primary"))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.clicked.connect(self._apply)
        holder = QHBoxLayout()
        holder.addWidget(send)
        holder.addStretch()
        column.addLayout(holder)
        return card

    def _keep_range_sane(self, value: QDate):
        """The last day can never be before the first."""
        if self._to.date() < value:
            self._to.setDate(value)

    def _on_half_toggled(self, on: bool):
        # Half of one day. Keeping the two dates together while it is ticked
        # is what stops "half a day" spanning a week, which the server would
        # refuse anyway — better not to offer it.
        self._to.setEnabled(not on)
        if on:
            self._to.setDate(self._from.date())

    def _history_card(self) -> Card:
        card = Card()
        column = QVBoxLayout(card)
        column.setContentsMargins(18, 16, 18, 16)
        column.setSpacing(10)

        heading = QLabel("HISTORY")
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:800;"
            f"letter-spacing:1px;background:transparent;border:none;")
        column.addWidget(heading)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Type", "From", "To", "Days", "Status", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)  # zebra striping competes with the data
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        # A TABLE STYLED WITH ONLY ITS SCROLLBAR KEEPS QT'S OWN PALETTE,
        # which is near-black: a hole punched through the card it sits
        # in, and the black rectangle reported on this page. table_style()
        # is the same one the rest of the product uses.
        self._table.setStyleSheet(table_style())
        column.addWidget(self._table, 1)
        return card

    # ── talking to the server ───────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        def fetch():
            response = _http.get(f"{API_BASE_URL}/leave/mine",
                                 headers=_headers(), timeout=20)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(
                    response, "Could not read your leave."))
            return response.json().get("leave") or []

        self._run(fetch, self._fill)

    def _fill(self, rows: list):
        self._rows = rows
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            def cell(text, muted=False):
                item = QTableWidgetItem(str(text))
                # QColor, not the hex string the theme holds — setForeground
                # takes a brush, and a str raises rather than being ignored.
                item.setForeground(QColor(C.TEXT_MUTED if muted else C.TEXT))
                return item

            self._table.setItem(i, 0, cell(row.get("leave_type", "").title()))
            self._table.setItem(i, 1, cell(row.get("start_date", "")))
            self._table.setItem(i, 2, cell(row.get("end_date", "")))
            days = row.get("total_days")
            self._table.setItem(i, 3, cell(
                f"{float(days):g}" if days is not None else ""))

            # The remark is why a rejection is not just "no". On hover, so the
            # table stays readable and the reason is never lost.
            status = str(row.get("status", ""))
            self._table.setCellWidget(i, 4, badge_cell(
                status, status.title(),
                str(row["remarks"]) if row.get("remarks") else None))

            # CANCEL EXISTS ONLY WHILE IT CAN WORK. An approved request is the
            # employer's plan now, and a button that is always there but
            # refuses half the time teaches people that buttons lie.
            if status == "PENDING":
                cancel = QPushButton("Cancel")
                cancel.setStyleSheet(button("secondary"))
                cancel.setCursor(Qt.CursorShape.PointingHandCursor)
                cancel.clicked.connect(
                    lambda _checked=False, rid=row["id"]: self._cancel(rid))
                self._table.setCellWidget(i, 5, cancel)
            else:
                self._table.setCellWidget(i, 5, QWidget())

        if not rows:
            self._say("No leave asked for yet.")
        # Sized to the content, after it exists — see fit_columns.
        fit_columns(self._table, stretch=0)

    def _apply(self):
        reason = self._reason.toPlainText().strip()
        if not reason:
            self._say("Say why — it is what the approver reads.", ok=False)
            return

        body = {
            "leave_type": self._type.currentData(),
            "reason": reason,
            "start_date": self._from.date().toString("yyyy-MM-dd"),
            "end_date": (self._from if self._half.isChecked()
                         else self._to).date().toString("yyyy-MM-dd"),
            "half_day": self._half.isChecked(),
        }

        def send():
            response = _http.post(f"{API_BASE_URL}/leave", json=body,
                                  headers={**_headers(),
                                           "Content-Type": "application/json"},
                                  timeout=20)
            if response.status_code not in (200, 201):
                raise RuntimeError(self._message_from(
                    response, "That request was not accepted."))
            return response.json().get("leave")

        def applied(leave):
            self._reason.clear()
            self._half.setChecked(False)
            days = float(leave.get("total_days", 0))
            self._say(f"Asked for {days:g} day{'' if days == 1 else 's'}. "
                      f"An administrator has been told.")
            self.refresh()

        self._run(send, applied)

    def _cancel(self, request_id: int):
        answer = QMessageBox.question(
            self, "Withdraw this request",
            "Withdraw this leave request?\n\n"
            "It has not been decided yet, so nothing has been planned around "
            "it. You can apply again afterwards.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def send():
            response = _http.post(f"{API_BASE_URL}/leave/{request_id}/cancel",
                                  headers=_headers(), timeout=20)
            if response.status_code != 200:
                raise RuntimeError(self._message_from(
                    response, "It could not be withdrawn."))
            return True

        self._run(send, lambda _ok: (self._say("Withdrawn."), self.refresh()))
