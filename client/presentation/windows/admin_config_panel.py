
from __future__ import annotations

import requests
from datetime import date

from PySide6.QtCore    import Qt, QThread, Signal, QDate
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow
from PySide6.QtGui     import QFont
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL


# ──────────────────────────────────────────────────────────────────────────────
#  Background workers
# ──────────────────────────────────────────────────────────────────────────────

class _FetchWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, url: str, params: dict | None = None):
        super().__init__()
        self._url    = url
        self._params = params or {}

    def run(self):
        try:
            r = requests.get(
                self._url,
                params=self._params,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )
            self.finished.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))


class _PostWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, url: str, body: dict):
        super().__init__()
        self._url  = url
        self._body = body

    def run(self):
        try:
            r = requests.post(
                self._url,
                json=self._body,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )
            self.finished.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))

class _ConfigTab(QWidget):

    def __init__(self):
        super().__init__()
        self._employees: list[dict] = []
        self._workers:   list       = []
        self._build_ui()
        self._load_employees()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        # Header
        title = QLabel("Admin Config Panel")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        root.addWidget(title)

        sub = QLabel("Employee ka config set karo ya global default update karo.")
        sub.setStyleSheet("color: #94a3b8; font-size: 12px;")
        root.addWidget(sub)

        # Employee selector
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Employee:"))
        self._emp_combo = QComboBox()
        self._emp_combo.setMinimumWidth(220)
        self._emp_combo.currentIndexChanged.connect(self._on_employee_changed)
        sel_row.addWidget(self._emp_combo)
        sel_row.addStretch()
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setFixedWidth(90)
        refresh_btn.clicked.connect(self._load_employees)
        sel_row.addWidget(refresh_btn)
        root.addLayout(sel_row)

        # Form
        SPIN_STYLE = (
            "QSpinBox { background: #fff; color: #111; border: 1px solid #ccc; "
            "border-radius: 4px; padding: 4px 8px; font-size: 13px; min-width: 100px; }"
            "QSpinBox:focus { border: 1px solid #2563eb; }"
        )

        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 60)
        self._min_spin.setSuffix(" min")

        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 120)
        self._max_spin.setSuffix(" min")

        self._cnt_spin = QSpinBox()
        self._cnt_spin.setRange(1, 20)

        self._upl_spin = QSpinBox()
        self._upl_spin.setRange(1, 240)
        self._upl_spin.setSuffix(" min")

        self._idle_spin = QSpinBox()
        self._idle_spin.setRange(10, 600)
        self._idle_spin.setSuffix(" sec")

        for spin in [self._min_spin, self._max_spin, self._cnt_spin,
                     self._upl_spin, self._idle_spin]:
            spin.setStyleSheet(SPIN_STYLE)

        rows_data = [
            ("Screenshot min interval:", self._min_spin),
            ("Screenshot max interval:", self._max_spin),
            ("Screenshots per shift:",   self._cnt_spin),
            ("Upload interval:",         self._upl_spin),
            ("Idle threshold:",          self._idle_spin),
        ]

        form_frame = QFrame()
        form_frame.setStyleSheet(
            "QFrame { background: #1e293b; border: 1px solid #334155; border-radius: 8px; }"
        )
        form_vbox = QVBoxLayout(form_frame)
        form_vbox.setContentsMargins(16, 16, 16, 16)
        form_vbox.setSpacing(10)

        for label_text, spin_widget in rows_data:
            row = QHBoxLayout()
            row.setSpacing(12)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #e2e8f0; font-size: 13px; min-width: 200px;")
            row.addWidget(lbl)
            row.addWidget(spin_widget)
            row.addStretch()
            form_vbox.addLayout(row)

        root.addWidget(form_frame)

        # Buttons
        btn_row = QHBoxLayout()

        self._save_btn = QPushButton("💾  Save Config")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton { background:#2563eb; color:#fff; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#1d4ed8; }"
        )
        self._save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self._save_btn)

        self._force_btn = QPushButton("🚪  Force Logout")
        self._force_btn.setFixedHeight(36)
        self._force_btn.setStyleSheet(
            "QPushButton { background:#dc2626; color:#fff; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#b91c1c; }"
        )
        self._force_btn.clicked.connect(self._force_logout)
        btn_row.addWidget(self._force_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size:12px;")
        root.addWidget(self._status_label)
        root.addStretch()

    # Data loading

    def _load_employees(self):
        self._status_label.setText("Employees load ho rahe hain…")
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees")
        w.finished.connect(self._on_employees_loaded)
        w.error.connect(lambda e: self._status_label.setText(f"Error: {e}"))
        self._workers.append(w)
        w.start()

    def _on_employees_loaded(self, data: dict):
        print("EMPLOYEE API RESPONSE =", data)
        self._employees = data.get("data", [])
        self._emp_combo.blockSignals(True)
        self._emp_combo.clear()
        self._emp_combo.addItem("🌐  Global Default", "global")
        for emp in self._employees:
            label = f"{emp.get('full_name', '?')}  ({emp.get('employee_id', '')})"
            self._emp_combo.addItem(label, emp.get("employee_id"))
        self._emp_combo.blockSignals(False)
        self._on_employee_changed()
        self._status_label.setText("")

    def _on_employee_changed(self):
        emp_id = self._emp_combo.currentData() or "global"
        w = _FetchWorker(f"{API_BASE_URL}/admin/config/{emp_id}")
        w.finished.connect(self._populate_form)
        w.error.connect(lambda e: self._status_label.setText(f"Config load error: {e}"))
        self._workers.append(w)
        w.start()

    def _populate_form(self, data: dict):
        cfg = data.get("config", {})
        self._min_spin.setValue(cfg.get("screenshot_min_minutes",  3))
        self._max_spin.setValue(cfg.get("screenshot_max_minutes",  10))
        self._cnt_spin.setValue(cfg.get("screenshot_count",        3))
        self._upl_spin.setValue(cfg.get("upload_interval_minutes", 60))
        self._idle_spin.setValue(cfg.get("idle_threshold_seconds", 60))

    # Actions

    def _save_config(self):
        emp_id = self._emp_combo.currentData()
        body = {
            "screenshot_min_minutes":  self._min_spin.value(),
            "screenshot_max_minutes":  self._max_spin.value(),
            "screenshot_count":        self._cnt_spin.value(),
            "upload_interval_minutes": self._upl_spin.value(),
            "idle_threshold_seconds":  self._idle_spin.value(),
        }
        if emp_id and emp_id != "global":
            body["employee_id"] = emp_id

        self._save_btn.setEnabled(False)
        self._save_btn.setText("Saving…")
        w = _PostWorker(f"{API_BASE_URL}/admin/config", body)
        w.finished.connect(self._on_save_done)
        w.error.connect(lambda e: (
            self._status_label.setText(f"❌ Error: {e}"),
            self._save_btn.setEnabled(True),
            self._save_btn.setText("💾  Save Config"),
        ))
        self._workers.append(w)
        w.start()

    def _on_save_done(self, data: dict):
        self._save_btn.setEnabled(True)
        self._save_btn.setText("💾  Save Config")
        if data.get("success"):
            self._status_label.setStyleSheet("color: #16a34a; font-size:12px;")
            self._status_label.setText("✅ Config saved successfully!")
        else:
            self._status_label.setStyleSheet("color: #dc2626; font-size:12px;")
            self._status_label.setText(f"❌ {data.get('error', 'Save failed')}")

    def _force_logout(self):
        emp_id = self._emp_combo.currentData()
        if not emp_id or emp_id == "global":
            QMessageBox.warning(self, "Select Employee", "Select employee")
            return

        name = self._emp_combo.currentText()
        reply = QMessageBox.question(
            self, "Force Logout",
            f"{name} ko force logout karna chahte ho?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _PostWorker(f"{API_BASE_URL}/admin/force-logout", {"employee_id": emp_id})
        w.finished.connect(lambda d: self._status_label.setText(
            "✅ Force logout set!" if d.get("success") else f"❌ {d.get('error')}"
        ))
        w.error.connect(lambda e: self._status_label.setText(f"❌ {e}"))
        self._workers.append(w)
        w.start()


# ──────────────────────────────────────────────────────────────────────────────
#  Screenshots Tab
# ──────────────────────────────────────────────────────────────────────────────

class _ScreenshotsTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("Screenshots")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(title)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Employee ID:"))
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("e.g. EMP001")
        self._emp_filter.setFixedWidth(140)
        filter_row.addWidget(self._emp_filter)

        filter_row.addWidget(QLabel("Date:"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setFixedWidth(120)
        filter_row.addWidget(self._date_filter)

        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(lambda: self._load(page=1))
        filter_row.addWidget(search_btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "File", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_preview)
        root.addWidget(self._table)

        pag_row = QHBoxLayout()
        self._prev_btn  = QPushButton("◀ Prev")
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = QPushButton("Next ▶")
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = QLabel("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)
        self._load()


    def _load(self, page=1):
        
        self._page = page
        params = {"page": page}
        emp = self._emp_filter.text().strip()
        dt  = self._date_filter.date().toString("yyyy-MM-dd")
        if emp: params["employee_id"] = emp
        if dt:  params["date"]        = dt
        print("SCREENSHOTS REQUEST =", f"{API_BASE_URL}/admin/screenshots", params)
        w = _FetchWorker(f"{API_BASE_URL}/admin/screenshots", params)
        w.finished.connect(self._populate)
        w.error.connect(lambda e: print("Screenshots error:", e))
        self._workers.append(w)
        w.start()

    def _populate(self, data: dict):
        print("SCREENSHOTS RESPONSE =", data)
        rows  = data.get("data", [])
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(row.get("employee_id", "")))
            item = QTableWidgetItem(row.get("file_name", ""))
            item.setData( Qt.ItemDataRole.UserRole,row.get("file_name", ""))
            self._table.setItem(i, 2, item)
            self._table.setItem(i, 3, QTableWidgetItem(str(row.get("created_at", ""))))
        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 20 < total)

    def _prev_page(self): self._load(self._page - 1)
    def _next_page(self): self._load(self._page + 1)
    def _open_preview(self, row, column):

        item = self._table.item(row, 2)
        if not item:
            return
        file_name = item.data(
            Qt.ItemDataRole.UserRole
        )
        image_path = (
            f"server/uploads/screenshots/{file_name}"
        )
        self.preview_window = ScreenshotPreviewWindow(
            image_path
        )
        self.preview_window.show()


# ──────────────────────────────────────────────────────────────────────────────
#  Logs Tab
# ──────────────────────────────────────────────────────────────────────────────

class _LogsTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("Activity Logs")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(title)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Employee ID:"))
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("e.g. EMP001")
        self._emp_filter.setFixedWidth(140)
        filter_row.addWidget(self._emp_filter)

        filter_row.addWidget(QLabel("Date:"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setFixedWidth(120)
        filter_row.addWidget(self._date_filter)

        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(lambda: self._load(page=1))
        filter_row.addWidget(search_btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "Activity", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table)

        pag_row = QHBoxLayout()
        self._prev_btn  = QPushButton("◀ Prev")
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = QPushButton("Next ▶")
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = QLabel("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)
        self._load()


    def _load(self, page=1):
        self._page = page
        params = {"page": page}
        emp = self._emp_filter.text().strip()
        dt  = self._date_filter.date().toString("yyyy-MM-dd")
        if emp: params["employee_id"] = emp
        if dt:  params["date"]        = dt

        w = _FetchWorker(f"{API_BASE_URL}/admin/logs", params)
        w.finished.connect(self._populate)
        w.error.connect(lambda e: print("Logs error:", e))
        self._workers.append(w)
        w.start()

    def _populate(self, data: dict):
        print("LOGS RESPONSE =", data)
        rows  = data.get("data", [])
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(row.get("employee_id", "")))
            self._table.setItem(i, 2, QTableWidgetItem(row.get("activity", "")))
            self._table.setItem(i, 3, QTableWidgetItem(str(row.get("created_at", ""))))
        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 50 < total)

    def _prev_page(self): self._load(self._page - 1)
    def _next_page(self): self._load(self._page + 1)


class AdminConfigPanel(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS — Admin Panel")
        self.setMinimumSize(780, 560)
        self.resize(900, 620)

        tabs = QTabWidget()
        tabs.addTab(_ConfigTab(),      "⚙️  Config")
        tabs.addTab(_ScreenshotsTab(), "📸  Screenshots")
        tabs.addTab(_LogsTab(),        "📋  Logs")
        self.setCentralWidget(tabs)
