"""
The change-password dialog, used from three places.

  * an employee or admin changing their own password from Settings
  * the login flow, when an admin has issued a temporary password and the
    account is flagged must_change_password
  * an admin changing their own password from the admin panel

The forced variant hides Cancel and refuses to close, because the whole
point of a temporary password is that it does not survive first use. Every
other rule about what a password may be lives on the server; this dialog
only catches the two mistakes worth catching before a round trip (an empty
field, and a confirmation that does not match) so the rules cannot drift
apart between client and server.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from client.application.services.auth_service import AuthService
from client.presentation.theme import C, R, R_SM


class _ChangeWorker(QThread):
    """Off the UI thread — bcrypt on the server takes a noticeable moment."""
    done = Signal(dict)

    def __init__(self, current: str, new: str):
        super().__init__()
        self._current = current
        self._new = new

    def run(self):
        self.done.emit(AuthService.change_password(self._current, self._new))


class ChangePasswordDialog(QDialog):

    def __init__(self, parent=None, *, forced: bool = False):
        super().__init__(parent)
        self._forced = forced
        self._worker: _ChangeWorker | None = None

        self.setWindowTitle("Change Password")
        self.setModal(True)
        self.setFixedWidth(430)
        if forced:
            # No close button — the only way out is to set a password.
            self.setWindowFlags(
                Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint
                | Qt.WindowType.WindowTitleHint
            )

        self._build()

    # ── layout ──────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(14)

        title = QLabel("Set a new password" if self._forced else "Change your password")
        title.setStyleSheet(
            f"color:{C.TEXT}; font-size:17px; font-weight:600; background:transparent;"
        )
        root.addWidget(title)

        if self._forced:
            note = QLabel(
                "Your password was reset by an administrator. Choose your own "
                "before continuing."
            )
            note.setWordWrap(True)
            note.setObjectName("cpdNote")
            note.setStyleSheet(
                f"#cpdNote {{ color:{C.AMBER}; background:{C.AMBER_BG};"
                f" border:1px solid {C.AMBER}; border-radius:{R_SM}px;"
                f" padding:10px 12px; font-size:12px; }}"
            )
            root.addWidget(note)

        self._current = self._field("Current password")
        self._new     = self._field("New password")
        self._confirm = self._field("Confirm new password")

        root.addWidget(self._labelled("Current password", self._current))
        root.addWidget(self._labelled("New password", self._new))
        root.addWidget(self._labelled("Confirm new password", self._confirm))

        hint = QLabel("At least 8 characters. Cannot be your username or employee ID.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{C.TEXT_DIM}; font-size:11px; background:transparent;"
        )
        root.addWidget(hint)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size:12px; background:transparent;")
        self._status.hide()
        root.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()

        if not self._forced:
            cancel = QPushButton("Cancel")
            cancel.setObjectName("cpdCancel")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.setStyleSheet(
                f"#cpdCancel {{ background:{C.ELEVATED}; color:{C.TEXT_MUTED};"
                f" border:1px solid {C.BORDER}; border-radius:{R_SM}px;"
                f" padding:9px 20px; font-size:13px; }}"
                f"#cpdCancel:hover {{ color:{C.TEXT}; }}"
            )
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)

        self._submit = QPushButton("Change Password")
        self._submit.setObjectName("cpdSubmit")
        self._submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit.setDefault(True)
        self._submit.setStyleSheet(
            f"#cpdSubmit {{ background:{C.PRIMARY}; color:#ffffff; border:none;"
            f" border-radius:{R_SM}px; padding:9px 22px; font-size:13px;"
            f" font-weight:600; }}"
            f"#cpdSubmit:hover {{ background:{C.PRIMARY_DIM}; }}"
            f"#cpdSubmit:disabled {{ background:{C.ELEVATED}; color:{C.TEXT_DIM}; }}"
        )
        self._submit.clicked.connect(self._submit_clicked)
        buttons.addWidget(self._submit)
        root.addLayout(buttons)

        self.setStyleSheet(f"QDialog {{ background:{C.BG}; border-radius:{R}px; }}")

    def _field(self, placeholder: str) -> QLineEdit:
        box = QLineEdit()
        box.setEchoMode(QLineEdit.EchoMode.Password)
        box.setPlaceholderText(placeholder)
        box.setObjectName("cpdInput")
        # Scoped to the objectName: a bare QLineEdit rule here would also
        # repaint the labels, which are QFrame subclasses.
        box.setStyleSheet(
            f"#cpdInput {{ background:{C.CARD}; color:{C.TEXT};"
            f" border:1px solid {C.BORDER}; border-radius:{R_SM}px;"
            f" padding:9px 12px; font-size:13px; }}"
            f"#cpdInput:focus {{ border:1px solid {C.PRIMARY}; }}"
        )
        box.returnPressed.connect(self._submit_clicked)
        return box

    def _labelled(self, text: str, widget: QLineEdit) -> QFrame:
        wrap = QFrame()
        wrap.setObjectName("cpdRow")
        wrap.setStyleSheet("#cpdRow { background:transparent; border:none; }")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{C.TEXT_MUTED}; font-size:12px; background:transparent;"
        )
        layout.addWidget(label)
        layout.addWidget(widget)
        return wrap

    # ── behaviour ───────────────────────────────────────────────────────
    def _say(self, message: str, colour: str):
        self._status.setStyleSheet(
            f"color:{colour}; font-size:12px; background:transparent;"
        )
        self._status.setText(message)
        self._status.show()

    def _submit_clicked(self):
        current = self._current.text()
        new     = self._new.text()
        confirm = self._confirm.text()

        if not current or not new or not confirm:
            self._say("Fill in all three fields.", C.AMBER)
            return
        if new != confirm:
            self._say("The two new passwords do not match.", C.AMBER)
            return

        self._submit.setEnabled(False)
        self._submit.setText("Changing…")
        self._say("Contacting the server…", C.TEXT_MUTED)

        self._worker = _ChangeWorker(current, new)
        self._worker.done.connect(self._on_result)
        self._worker.start()

    def _on_result(self, result: dict):
        self._submit.setEnabled(True)
        self._submit.setText("Change Password")

        if result.get("success"):
            self._say("Password changed.", C.GREEN)
            self.accept()
            return

        self._say(result.get("message", "Could not change the password."), C.RED)
        self._current.clear()
        self._current.setFocus()

    def closeEvent(self, event):
        # A forced change cannot be dismissed with the window controls or
        # Escape; otherwise the temporary password stays in use.
        if self._forced and self.result() != QDialog.DialogCode.Accepted:
            event.ignore()
            return
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if self._forced and event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)
