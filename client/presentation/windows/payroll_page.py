"""
My Payroll — what was paid, and the payslip that says why.

ONLY FINALISED MONTHS APPEAR HERE. A draft is working material that may still
move, and showing somebody a number that then changes is worse than showing
them nothing: they will remember the first one.

The payslip opens in a browser rather than downloading a PDF. It prints from
there, or saves as a PDF with the browser's own dialog — which every machine
can already do, and which needs nothing installed on the server.
"""
from __future__ import annotations

import os
import tempfile
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from client.core import http as _http
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.presentation.theme import C, table_style
from client.presentation.widgets.panel_widgets import Card, PageHeader, fit_columns


def _headers() -> dict:
    return {"Authorization": f"Bearer {SessionManager.auth_token}"}


def _money(value) -> str:
    """Indian grouping, two decimals — what a payslip is read in."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    whole, fraction = f"{abs(number):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'−' if number < 0 else ''}₹{whole}.{fraction}"


class _Worker(QThread):
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


class PayrollPage(QWidget):
    """Every finalised month, and a button that opens the payslip."""

    COLUMNS = ["Month", "Gross", "Days", "Present", "Leave", "Absent",
               "Deductions", "Overtime", "Net pay", ""]

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._workers: list[_Worker] = []
        self._build()

    def _run(self, fn, on_done, on_fail=None, *args, **kwargs):
        worker = _Worker(fn, *args, **kwargs)
        worker.done.connect(on_done)
        worker.failed.connect(on_fail or (lambda m: self._say(m, ok=False)))
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _say(self, message: str, ok: bool = True):
        self._status.setText(("" if ok else "") + message)
        self._status.setStyleSheet(
            f"color:{C.TEXT_MUTED if ok else C.AMBER};font-size:12px;"
            f"background:transparent;border:none;")

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(14)

        outer.addWidget(PageHeader(
            "My Payroll",
            "What you were paid, month by month, and why."))

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:12px;background:transparent;border:none;")
        outer.addWidget(self._status)

        card = Card()
        column = QVBoxLayout(card)
        column.setContentsMargins(18, 16, 18, 16)

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
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
        outer.addWidget(card, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        def fetch():
            response = _http.get(f"{API_BASE_URL}/payroll/mine",
                                 headers=_headers(), timeout=20)
            if response.status_code != 200:
                raise RuntimeError("Could not read your payroll.")
            return response.json().get("data") or []

        self._run(fetch, self._fill)

    def _fill(self, rows: list):
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            def cell(text, bold=False, colour=None):
                item = QTableWidgetItem(str(text))
                item.setForeground(QColor(colour or C.TEXT))
                if bold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                return item

            deductions = (float(row.get("absent_deduction") or 0)
                          + float(row.get("unpaid_deduction") or 0))
            self._table.setItem(i, 0, cell(row.get("month", ""), bold=True))
            self._table.setItem(i, 1, cell(_money(row.get("gross_monthly"))))
            self._table.setItem(i, 2, cell(f"{float(row.get('working_days') or 0):g}"))
            self._table.setItem(i, 3, cell(f"{float(row.get('present_days') or 0):g}"))
            self._table.setItem(i, 4, cell(
                f"{float(row.get('paid_leave_days') or 0):g}"
                + (f" +{float(row.get('unpaid_leave_days') or 0):g} unpaid"
                   if float(row.get("unpaid_leave_days") or 0) else "")))
            self._table.setItem(i, 5, cell(
                f"{float(row.get('absent_days') or 0):g}",
                colour=C.RED if float(row.get("absent_days") or 0) else None))
            self._table.setItem(i, 6, cell(
                _money(deductions) if deductions else "—",
                colour=C.RED if deductions else None))
            self._table.setItem(i, 7, cell(
                _money(row.get("overtime_amount"))
                if float(row.get("overtime_amount") or 0) else "—"))
            self._table.setItem(i, 8, cell(_money(row.get("net_pay")),
                                           bold=True, colour=C.GREEN))

            open_slip = QPushButton("Payslip")
            open_slip.setCursor(Qt.CursorShape.PointingHandCursor)
            open_slip.setStyleSheet(
                f"QPushButton{{background:{C.ELEVATED};border:1px solid {C.BORDER};"
                f"border-radius:12px;color:{C.TEXT};font-size:12px;padding:4px 10px;}}"
                f"QPushButton:hover{{border-color:{C.PRIMARY};}}")
            open_slip.clicked.connect(
                lambda _checked=False, month=row.get("month"): self._payslip(month))
            self._table.setCellWidget(i, 9, open_slip)

        if not rows:
            self._say("No payslips yet. They appear here once a month is finalised.")
        else:
            self._say(f"{len(rows)} month{'' if len(rows) == 1 else 's'}.")
        # Sized to the content, after it exists — see fit_columns.
        fit_columns(self._table, stretch=0)

    def _payslip(self, month: str):
        """Fetch the payslip and open it in the browser.

        SAVED TO A FILE FIRST rather than pointing the browser at the API: the
        browser has no token, so the URL alone would come back as 401. This
        keeps the page exactly as the server rendered it — including the print
        button, which is how it becomes a PDF.
        """
        def fetch():
            response = _http.get(f"{API_BASE_URL}/payroll/payslip/{month}",
                                 headers=_headers(), timeout=30)
            if response.status_code != 200:
                raise RuntimeError("That payslip could not be opened.")
            path = os.path.join(tempfile.gettempdir(),
                                f"payslip-{month}-{SessionManager.employee_id}.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(response.text)
            return path

        def opened(path):
            webbrowser.open(f"file://{path}")
            self._say(f"Payslip for {month} opened in your browser. "
                      f"Use Print to save it as a PDF.")

        self._run(fetch, opened)
