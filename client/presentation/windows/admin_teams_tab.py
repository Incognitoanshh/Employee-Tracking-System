"""
The Teams tab — teams, channels, membership, and the audited chat viewer.

Kept in its own module because admin_config_panel.py is already five thousand
lines; it borrows that file's helpers rather than growing a second set, so the
tab looks like every other one.

WHAT AN ADMINISTRATOR CAN AND CANNOT DO HERE

They can create teams and channels, add and remove members, archive a team,
and post announcements. They cannot read anybody's conversation — that button
does not exist for them, and the server refuses it regardless.

A super admin can read one, and doing so is not quiet. A purpose has to be
chosen and a reference given, both are written to a table that is never
purged, and a line goes to the audit log the weekly report already reads.
That is a protection for whoever runs this: "why were you reading my chat"
gets asked long after anyone remembers the answer.

THERE IS NO DELETE, anywhere on this tab. A team is archived — read-only,
still searchable — because deleting one would take every conversation in it,
and chat is the one thing in this system kept indefinitely.
"""

from __future__ import annotations

import requests
from client.core import http as _http
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
    QLineEdit, QWidget,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QVBoxLayout,
    QWidget,
)

from client.core.config import API_BASE_URL
from client.presentation import theme as _theme
from client.application.managers.session_manager import SessionManager
from client.presentation.windows.admin_config_panel import (
    C, _btn, _card, _cell, _divider, _fmt_ts, _muted_label, _track_worker,
    _tune_table, _FetchWorker, _auth_headers,
)


PURPOSES = [
    ("HR_INVESTIGATION", "HR Investigation"),
    ("COMPLAINT",        "Complaint"),
    ("LEGAL",            "Legal"),
    ("COMPLIANCE",       "Compliance"),
    ("EMPLOYEE_REQUEST", "Employee Request"),
    ("OTHER",            "Other"),
]

# A reference is not demanded for these two: an employee asking to see their
# own channel has no ticket number, and OTHER asks for a written note instead.
NO_REFERENCE_NEEDED = {"EMPLOYEE_REQUEST", "OTHER"}


class _ApiWorker(QThread):
    """
    Any method, and the status code is actually looked at.

    _PostWorker in admin_config_panel emits whatever JSON comes back without
    checking whether the request succeeded, which leaves each caller to notice
    on its own. Every write on this tab goes through here instead, so a
    refusal arrives as an error with the server's own wording rather than as
    data that happens to be missing fields.
    """
    result = Signal(dict)
    error = Signal(str)

    def __init__(self, method: str, path: str, body: dict | None = None):
        super().__init__()
        self._method = method.upper()
        self._path = path
        self._body = body

    def run(self):
        try:
            response = _http.request(
                self._method,
                f"{API_BASE_URL}{self._path}",
                json=self._body,
                headers=_auth_headers(),
                timeout=15,
            )
            payload = {}
            try:
                payload = response.json()
            except Exception:
                pass
            if not response.ok or payload.get("success") is False:
                self.error.emit(payload.get("message") or f"HTTP {response.status_code}")
                return
            self.result.emit(payload)
        except Exception as error:
            self.error.emit(str(error))


def _humanise(message: str) -> str:
    """Turn a Python exception into something worth showing somebody.

    requests raises with its internals attached — the employee was being
    shown "HTTPConnectionPool(host='65.21.212.85', port=8000): Read timed
    out. (read timeout=10)", which names the server, the port, and nothing
    they can act on.
    """
    text = str(message or "")
    low = text.lower()
    if "timed out" in low or "timeout" in low:
        return "The server is taking too long to answer. It will retry."
    if "connection" in low or "max retries" in low or "unreachable" in low:
        return "Cannot reach the server. Check the connection."
    if text.startswith("HTTP 5"):
        return "The server had a problem with that request."
    if text.startswith("HTTP 401") or "unauthenticated" in low:
        return "Your session has expired. Sign in again."
    return text


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setStyleSheet(
        f"color:{C['text_primary']};font-size:13px;font-weight:700;"
        f"background:transparent;border:none;")
    return label


class _PeoplePicker(QWidget):
    """A searchable, checkable list of employees.

    WHY THE SEARCH IS NOT OPTIONAL. This is how a team or a channel gets its
    members, and it was a plain list: to put twenty people in a channel out of
    a hundred, somebody had to scroll and read every row, twice, and hope. At
    six employees it looks fine; the first real company makes it unusable.

    FILTERING HIDES ROWS, IT NEVER UNTICKS ONE. That is the whole trick — you
    search "priya", tick her, search "amit", tick him, and both are still
    ticked when you press Create. A picker that forgot the first name when you
    typed the second would be worse than no search at all, and it is the
    mistake this shape is chosen to avoid: the items are never rebuilt, only
    shown and hidden.

    `.count()` and `.item()` are forwarded so the callers that already read
    the list keep working unchanged.
    """

    def __init__(self, parent, people, preselected=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search by name or employee ID…")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f"QLineEdit{{background:{C['bg_surface_alt']};border:1px solid {C['border']};"
            f"border-radius:{_theme.Radius.CONTROL}px;color:{C['text_primary']};"
            f"font-size:{_theme.Type.SMALL}px;padding:6px 10px;}}")
        column.addWidget(self.search)

        self.listing = _checkable_list(self, people, preselected)
        column.addWidget(self.listing)

        self.counter = QLabel("")
        self.counter.setStyleSheet(
            f"color:{C['text_muted']};font-size:12px;background:transparent;")
        column.addWidget(self.counter)

        self.search.textChanged.connect(self._filter)
        self.listing.itemChanged.connect(lambda _i: self._recount())
        self._recount()

    def _filter(self, text: str):
        needle = text.strip().lower()
        for i in range(self.listing.count()):
            item = self.listing.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())
        self._recount()

    def _recount(self):
        picked = sum(1 for i in range(self.listing.count())
                     if self.listing.item(i).checkState() == Qt.CheckState.Checked)
        hidden = sum(1 for i in range(self.listing.count())
                     if self.listing.item(i).isHidden())
        # The count is what makes the search safe to trust: somebody who has
        # filtered the list can still see that the four people they ticked
        # earlier are still ticked.
        note = f"{picked} selected"
        if hidden:
            note += f"  ·  {hidden} hidden by the search"
        self.counter.setText(note)

    # The callers already speak QListWidget; keep them working.
    def count(self):
        return self.listing.count()

    def item(self, index):
        return self.listing.item(index)

    def setEnabled(self, on):
        super().setEnabled(on)
        self.listing.setEnabled(on)


def _pick_people(parent, people: list[dict], preselected: set | None = None):
    """A searchable, checkable list of employees, for 'new team' and 'new channel'."""
    return _PeoplePicker(parent, people, preselected)


def _checkable_list(parent, people: list[dict], preselected: set | None = None) -> QListWidget:
    listing = QListWidget(parent)
    listing.setStyleSheet(
        f"QListWidget{{background:{C['bg_surface_alt']};border:1px solid {C['border']};"
        f"border-radius:12px;color:{C['text_primary']};font-size:12px;padding:4px;}}"
        f"QListWidget::item{{padding:5px 6px;}}")
    for person in people:
        item = QListWidgetItem(
            f"{person.get('name') or person.get('username')}  ·  {person['employee_id']}")
        item.setData(Qt.ItemDataRole.UserRole, person["employee_id"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked
            if preselected and person["employee_id"] in preselected
            else Qt.CheckState.Unchecked)
        listing.addItem(item)
    return listing



def _people_rows(parent, height: int = 170):
    """
    A scrolling column of rows, each free to hold a button.

    NOT a QListWidget with setItemWidget. That sizes each row to the ITEM,
    whose width comes from the size hint rather than the visible area — so a
    row wide enough for a button ends up wider than the viewport and the
    button is clipped at the right edge. Turning the horizontal scrollbar off
    only hides the evidence: the row is still too wide, it just gets cut
    instead of scrolled. A scroll area with a plain layout inherits the
    viewport width, so nothing can overflow.

    Returns (area, column) — add rows to the column.
    """
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFixedHeight(height)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet(
        f"QScrollArea{{background:{C['bg_surface_alt']};"
        f"border:1px solid {C['border']};border-radius:12px;}}")
    host = _cell_holder()
    column = QVBoxLayout(host)
    column.setContentsMargins(6, 6, 6, 6)
    column.setSpacing(2)
    area.setWidget(host)
    return area, column



def _cell_holder() -> QWidget:
    """A transparent wrapper for a widget placed inside a table cell.

    The stylesheet is SCOPED to the wrapper by objectName. A bare
    `background:transparent` — which is what this used to be — is applied by
    Qt to the widget and everything inside it, so the button in the cell lost
    its own background and rendered as an empty outline. The Post button on
    announcement channels was invisible for exactly this reason: correct
    variant, correct rule, painted over by its own parent.
    """
    holder = QWidget()
    holder.setObjectName("cellHolder")
    holder.setStyleSheet("QWidget#cellHolder { background: transparent; }")
    return holder


def _clear_cell(table, row: int, column: int) -> None:
    """Remove whatever is in a cell — item AND widget.

    A cell can hold either, and setting one does not remove the other. These
    tables are refilled in place, so a cell that held plain text and now holds
    a button ended up showing both, drawn on top of each other: "General" and
    "General, Backend" overlapping into "Genemerall".
    """
    existing = table.cellWidget(row, column)
    if existing is not None:
        table.removeCellWidget(row, column)
        existing.deleteLater()
    table.setItem(row, column, None)


def _person_row(text: str, button: QPushButton | None = None) -> QWidget:
    row = _cell_holder()
    line = QHBoxLayout(row)
    line.setContentsMargins(8, 3, 8, 3)
    line.setSpacing(8)
    label = QLabel(text)
    label.setStyleSheet(
        f"color:{C['text_primary']};font-size:12px;background:transparent;border:none;")
    line.addWidget(label, 1)
    if button is not None:
        line.addWidget(button, 0)
    return row


def _checked_ids(listing: QListWidget) -> list[str]:
    return [listing.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(listing.count())
            if listing.item(i).checkState() == Qt.CheckState.Checked]


# ──────────────────────────────────────────────────────────────────────────────
#  Dialogs
# ──────────────────────────────────────────────────────────────────────────────

class _BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 460):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        # THE BACKGROUND IS NOT SET HERE. Every other dialog in the console
        # takes it from the panel's own QDialog rule; these two set
        # bg_surface, a shade lighter than the rest, which nobody chose — it
        # was simply written twice, differently.
        #
        # The fields follow the console's own controls for the same reason.
        self.setStyleSheet(
            f"QLabel{{color:{C['text_secondary']};"
            f"font-size:{_theme.Type.SMALL}px;background:transparent;}}"
            f"QLineEdit,QPlainTextEdit,QComboBox{{background:{C['bg_surface_alt']};"
            f"color:{C['text_primary']};border:1px solid {C['border']};"
            f"border-radius:{_theme.Radius.CONTROL}px;padding:8px 10px;"
            f"font-size:{_theme.Type.BODY}px;}}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 18)
        self.body.setSpacing(10)

    def buttons(self, ok_text: str, danger: bool = False):
        row = QHBoxLayout()
        row.addStretch()
        cancel = _btn("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        confirm = _btn(ok_text, "danger" if danger else "primary")
        confirm.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(confirm)
        self.body.addLayout(row)


class _NewTeamDialog(_BaseDialog):
    def __init__(self, parent, people: list[dict]):
        super().__init__(parent, "New team", 520)
        self.body.addWidget(_section("Create a team"))
        self.body.addWidget(_muted_label(
            "A General channel is created with it. Everyone added to the team "
            "can see General; every other channel is granted separately."))

        self.name = QLineEdit()
        self.name.setPlaceholderText("Development")
        self.body.addWidget(QLabel("Name"))
        self.body.addWidget(self.name)

        self.description = QLineEdit()
        self.description.setPlaceholderText("Optional")
        self.body.addWidget(QLabel("Description"))
        self.body.addWidget(self.description)

        self.body.addWidget(QLabel("Members"))
        self.people = _pick_people(self, people)
        self.people.setFixedHeight(200)
        self.body.addWidget(self.people)
        self.buttons("Create team")

    def payload(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "description": self.description.text().strip(),
            "members": _checked_ids(self.people),
        }


class _NewChannelDialog(_BaseDialog):
    def __init__(self, parent, team: dict, members: list[dict]):
        super().__init__(parent, "New channel", 520)
        self.body.addWidget(_section(f"New channel in {team['name']}"))

        self.name = QLineEdit()
        self.name.setPlaceholderText("Backend")
        self.body.addWidget(QLabel("Name"))
        self.body.addWidget(self.name)

        self.body.addWidget(QLabel("Type"))
        self.type = QComboBox()
        self.type.addItem("Standard — everyone added can post", "STANDARD")
        self.type.addItem("Announcement — only administrators post", "ANNOUNCEMENT")
        self.body.addWidget(self.type)

        self.private = QCheckBox("Private")
        self.private.setStyleSheet(f"color:{C['text_secondary']};font-size:12px;")
        self.body.addWidget(self.private)

        self.body.addWidget(QLabel("Who can see it"))
        self._note = _muted_label(
            "Only the people ticked here. An employee cannot see a channel they "
            "are not in — they cannot even see its name.")
        self.body.addWidget(self._note)
        self.people = _pick_people(self, members)
        self.people.setFixedHeight(180)
        self.body.addWidget(self.people)

        self.type.currentIndexChanged.connect(self._on_type)
        self._on_type()
        self.buttons("Create channel")

    def _on_type(self):
        # A public announcement channel is team-wide by design — its purpose is
        # to reach everyone — so a membership list would be meaningless there.
        announcement = self.type.currentData() == "ANNOUNCEMENT"
        team_wide = announcement and not self.private.isChecked()
        self.people.setDisabled(team_wide)
        self._note.setText(
            "Everyone in the team sees announcements — that is the point of them."
            if team_wide else
            "Only the people ticked here. An employee cannot see a channel they "
            "are not in — they cannot even see its name.")

    def payload(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "type": self.type.currentData(),
            "is_private": self.private.isChecked(),
            "members": [] if self.people.isEnabled() is False else _checked_ids(self.people),
        }


class _ArchiveDialog(_BaseDialog):
    def __init__(self, parent, team: dict):
        super().__init__(parent, "Archive team")
        self.body.addWidget(_section(f"Archive {team['name']}"))
        self.body.addWidget(_muted_label(
            "The conversation stays — readable and searchable — but no new "
            "messages can be added. There is no delete: removing a team would "
            "take its entire history with it."))
        self.body.addWidget(QLabel("Reason"))
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Department merged")
        self.body.addWidget(self.reason)
        self.buttons("Archive", danger=True)


class _AnnounceDialog(_BaseDialog):
    def __init__(self, parent, channel: dict):
        super().__init__(parent, "Post announcement", 540)
        self.body.addWidget(_section(f"Announcement — {channel['name']}"))
        self.body.addWidget(_muted_label(
            "Everyone in the team is notified. They can read it but not reply."))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Maintenance tonight from 11pm to 1am.")
        self.text.setFixedHeight(140)
        self.body.addWidget(self.text)
        self.buttons("Post")


class _ViewChatDialog(_BaseDialog):
    """
    Reading a conversation. Super admin only, and recorded.

    The purpose is asked before anything is shown, not after, so the record is
    written whether or not the reader finds what they were looking for.
    """

    def __init__(self, parent, channel: dict):
        super().__init__(parent, "Read conversation", 560)
        self._channel = channel
        self.body.addWidget(_section(f"Read {channel['name']}"))
        self.body.addWidget(_muted_label(
            "This is recorded — who read it, when, and why — and the record is "
            "never purged. Employees are told their team chat is kept in the "
            "company record."))

        self.body.addWidget(QLabel("Purpose"))
        self.purpose = QComboBox()
        for value, label in PURPOSES:
            self.purpose.addItem(label, value)
        self.body.addWidget(self.purpose)

        self.reference_label = QLabel("Reference")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Complaint #214")
        self.body.addWidget(self.reference_label)
        self.body.addWidget(self.reference)

        self.note_label = QLabel("Describe the reason")
        self.note = QLineEdit()
        self.body.addWidget(self.note_label)
        self.body.addWidget(self.note)

        self.purpose.currentIndexChanged.connect(self._on_purpose)
        self._on_purpose()
        self.buttons("Open conversation")

    def _on_purpose(self):
        value = self.purpose.currentData()
        needs_reference = value not in NO_REFERENCE_NEEDED
        self.reference_label.setVisible(needs_reference)
        self.reference.setVisible(needs_reference)
        is_other = value == "OTHER"
        self.note_label.setVisible(is_other)
        self.note.setVisible(is_other)

    def payload(self) -> dict:
        return {
            "channel_id": self._channel["id"],
            "purpose": self.purpose.currentData(),
            "reference_id": self.reference.text().strip(),
            "note": self.note.text().strip(),
            "limit": 300,
        }


class _TranscriptDialog(QDialog):
    """The conversation itself, with every version of anything edited."""

    def __init__(self, parent, payload: dict):
        super().__init__(parent)
        channel = payload.get("channel", {})
        self.setWindowTitle(f"{channel.get('team_name', '')} / {channel.get('name', '')}")
        self.setMinimumSize(720, 620)
        # Inherits the console's QDialog rule, like every other dialog.

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        head = QLabel(f"{channel.get('team_name', '')} / {channel.get('name', '')}")
        head.setStyleSheet(
            f"color:{C['text_primary']};font-size:16px;font-weight:700;background:transparent;")
        root.addWidget(head)
        root.addWidget(_muted_label("This read has been recorded in the access log."))
        root.addWidget(_divider())

        edits: dict[int, list] = {}
        for version in payload.get("edit_history") or []:
            edits.setdefault(int(version["message_seq"]), []).append(version)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        host = _cell_holder()
        feed = QVBoxLayout(host)
        feed.setContentsMargins(0, 0, 8, 0)
        feed.setSpacing(10)

        messages = payload.get("messages") or []
        if not messages:
            feed.addWidget(_muted_label("Nothing has been said in this channel."))

        for message in messages:
            block = QVBoxLayout()
            block.setSpacing(2)
            name = message.get("sender_name") or "Unknown"
            if message.get("former"):
                name += "  (Former Employee)"
            who = QLabel(f"{name}   ·   {_fmt_ts(message.get('created_at'))}")
            who.setStyleSheet(
                f"color:{C['text_secondary']};font-size:12px;font-weight:700;"
                f"background:transparent;")
            body = QLabel(str(message.get("body") or ""))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet(
                f"color:{C['text_primary']};font-size:13px;background:transparent;")
            block.addWidget(who)
            block.addWidget(body)

            # The whole reason edit history is kept: without showing it here,
            # somebody can say something, change it a minute later, and the only
            # record of what was actually said is one nobody can reach.
            for version in sorted(edits.get(int(message.get("seq", 0)), []),
                                  key=lambda v: v["version"]):
                old = QLabel(f"    version {version['version']}:  {version['old_body']}")
                old.setWordWrap(True)
                old.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                old.setStyleSheet(
                    f"color:{C['warning']};font-size:12px;font-style:italic;"
                    f"background:transparent;")
                block.addWidget(old)

            wrapper = _cell_holder()
            wrapper.setLayout(block)
            feed.addWidget(wrapper)

        feed.addStretch()
        area.setWidget(host)
        root.addWidget(area, 1)

        close = _btn("Close", "secondary")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        root.addLayout(row)


# ──────────────────────────────────────────────────────────────────────────────
#  The tab
# ──────────────────────────────────────────────────────────────────────────────

class _TeamsTab(QWidget):
    """Teams down the left, the selected team's detail on the right."""

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._teams: list[dict] = []
        self._people: list[dict] = []
        self._selected: int | None = None
        self._detail: dict | None = None
        self._build()

    # ── layout ──────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(18)

        # Left — the teams.
        left = _card(0)
        left.setFixedWidth(280)
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(16, 16, 16, 16)
        left_col.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(_section("Teams"))
        head.addStretch()
        new_team = _btn("+ New", "primary", height=36, width=80)
        new_team.clicked.connect(self._new_team)
        head.addWidget(new_team)
        left_col.addLayout(head)

        self._team_list = QListWidget()
        self._team_list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;color:{C['text_secondary']};"
            f"font-size:13px;}}"
            f"QListWidget::item{{padding:9px 8px;border-radius:12px;}}"
            f"QListWidget::item:selected{{background:{C['accent_soft']};"
            f"color:{C['text_primary']};}}")
        self._team_list.currentRowChanged.connect(self._on_team_selected)
        left_col.addWidget(self._team_list, 1)
        root.addWidget(left)

        # Right — the detail.
        right = _card(0)
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(22, 20, 22, 20)
        right_col.setSpacing(14)

        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = QLabel("Select a team")
        self._title.setStyleSheet(
            f"color:{C['text_primary']};font-size:18px;font-weight:700;"
            f"background:transparent;")
        self._subtitle = _muted_label("")
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        title_row.addLayout(titles)
        title_row.addStretch()

        self._archive_btn = _btn("Archive", "secondary")
        self._archive_btn.clicked.connect(self._toggle_archive)
        self._archive_btn.hide()
        title_row.addWidget(self._archive_btn)
        right_col.addLayout(title_row)
        right_col.addWidget(_divider())

        # Channels.
        channels_head = QHBoxLayout()
        channels_head.addWidget(_section("Channels"))
        channels_head.addStretch()
        self._new_channel_btn = _btn("+ Channel", "secondary", height=36, width=104)
        self._new_channel_btn.clicked.connect(self._new_channel)
        self._new_channel_btn.hide()
        channels_head.addWidget(self._new_channel_btn)
        right_col.addLayout(channels_head)

        self._channels = QTableWidget(0, 4)
        self._channels.setHorizontalHeaderLabels(["Channel", "Type", "Messages", "Actions"])
        _tune_table(self._channels)
        self._channels.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._channels.setFixedHeight(200)
        # Only the name stretches. The rest are fixed and wide enough for what
        # actually goes in them: "Announcement" was being clipped to
        # "Announc…", and three buttons were overlapping in a column sized for
        # one.
        header = self._channels.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 140), (2, 90), (3, 250)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._channels.setColumnWidth(column, width)
        right_col.addWidget(self._channels)

        # Members.
        members_head = QHBoxLayout()
        members_head.addWidget(_section("Members"))
        members_head.addStretch()
        self._add_members_btn = _btn("+ Members", "secondary", height=36, width=110)
        self._add_members_btn.clicked.connect(self._add_members)
        self._add_members_btn.hide()
        members_head.addWidget(self._add_members_btn)
        right_col.addLayout(members_head)

        self._members = QTableWidget(0, 5)
        self._members.setHorizontalHeaderLabels(
            ["Employee", "ID", "Role", "Channels", "Actions"])
        _tune_table(self._members)
        self._members.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        mheader = self._members.horizontalHeader()
        mheader.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        mheader.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 90), (2, 100), (4, 120)):
            mheader.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._members.setColumnWidth(column, width)
        right_col.addWidget(self._members, 1)

        root.addWidget(right, 1)

    # ── plumbing ────────────────────────────────────────────────────────

    def _fetch(self, path: str, on_done):
        # Reads fail QUIETLY.
        #
        # They used to share _on_error with the writes, which opens a modal.
        # refresh() makes two of them, the header's Refresh makes two more,
        # and on a link that drops a fifth of its packets a timeout is normal
        # — so the admin got a dialog box every few seconds saying
        # "HTTPConnectionPool(host=... Read timed out". Nothing was wrong that
        # the next poll would not fix, and the panel was unusable.
        worker = _FetchWorker(f"{API_BASE_URL}{path}")
        worker.result.connect(on_done)
        worker.error.connect(self._on_read_failed)
        _track_worker(self._workers, worker)
        worker.start()

    def _write(self, method: str, path: str, body: dict | None, on_done):
        worker = _ApiWorker(method, path, body)
        worker.result.connect(on_done)
        worker.error.connect(self._on_error)
        _track_worker(self._workers, worker)
        worker.start()

    def _on_error(self, message: str):
        # A WRITE failed — somebody pressed a button and it did not happen, so
        # they have to be told. The server's own wording where there is one:
        # "Development is archived — it is read-only" says what to do next,
        # "Save failed" does not.
        QMessageBox.warning(self, "Could not do that", _humanise(message))

    def _on_read_failed(self, message: str):
        """A refresh did not come back. Say so on the page, not in a box."""
        self._subtitle.setText(_humanise(message))

    def _is_super_admin(self) -> bool:
        return SessionManager.role == "super_admin"

    # ── loading ─────────────────────────────────────────────────────────

    def refresh(self):
        self._fetch("/admin/teams", self._on_teams)
        self._fetch("/admin/employees", self._on_people)

    def _on_people(self, payload):
        self._people = payload.get("employees") or payload.get("data") or []

    def _on_teams(self, payload):
        self._teams = payload.get("teams") or []
        remembered = self._selected

        self._team_list.blockSignals(True)
        self._team_list.clear()
        for team in self._teams:
            label = team["name"]
            if team.get("is_archived"):
                label += "   · archived"
            item = QListWidgetItem(f"{label}\n{team['member_count']} member(s)"
                                   f" · {team['channel_count']} channel(s)")
            item.setData(Qt.ItemDataRole.UserRole, team["id"])
            self._team_list.addItem(item)
        self._team_list.blockSignals(False)

        if not self._teams:
            self._title.setText("No teams yet")
            self._subtitle.setText("Create one to give people somewhere to talk.")
            return

        target = remembered if any(t["id"] == remembered for t in self._teams) \
            else self._teams[0]["id"]
        row = next(i for i, t in enumerate(self._teams) if t["id"] == target)
        self._team_list.setCurrentRow(row)

    def _on_team_selected(self, row: int):
        if row < 0 or row >= len(self._teams):
            return
        self._selected = self._teams[row]["id"]
        self._fetch(f"/admin/teams/{self._selected}", self._on_detail)

    def _on_detail(self, payload):
        self._detail = payload
        team = payload.get("team") or {}

        self._title.setText(team.get("name", "—"))
        bits = []
        if team.get("description"):
            bits.append(team["description"])
        if team.get("is_archived"):
            bits.append(f"archived — {team.get('archived_reason') or 'no reason given'}")
        self._subtitle.setText("   ·   ".join(bits) or "Active")

        archived = bool(team.get("is_archived"))
        self._archive_btn.setText("Restore" if archived else "Archive")
        self._archive_btn.show()
        # Everything that writes into a team is refused while it is archived,
        # so the buttons go rather than failing when pressed.
        self._new_channel_btn.setVisible(not archived)
        self._add_members_btn.setVisible(not archived)

        self._fill_channels(payload.get("channels") or [], archived)
        self._fill_members(payload.get("members") or [],
                           payload.get("channels") or [], archived)

    def _fill_channels(self, channels: list, archived: bool):
        self._channels.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            for column in range(self._channels.columnCount()):
                _clear_cell(self._channels, row, column)
            name = channel["name"]
            if channel.get("is_default"):
                name += "  (default)"
            if channel.get("is_private"):
                name = " " + name
            self._channels.setItem(row, 0, _cell(name))
            self._channels.setItem(row, 1, _cell(
                "Announcement" if channel["type"] == "ANNOUNCEMENT" else "Standard",
                muted=True))
            self._channels.setItem(row, 2, _cell(str(channel["message_count"]),
                                                 mono=True, align_right=True))

            actions = _cell_holder()
            line = QHBoxLayout(actions)
            line.setContentsMargins(4, 2, 4, 2)
            line.setSpacing(6)

            if channel["type"] == "ANNOUNCEMENT" and not archived:
                post = _btn("Post", "primary", height=36, width=76)
                post.clicked.connect(lambda _=False, c=channel: self._announce(c))
                line.addWidget(post)

            # Edit is on every row, including General and the announcements.
            #
            # It was only on the channels with their own membership list at
            # first, which left two rows with no way to change anything about
            # them at all — not even their description. What Edit offers
            # differs by channel: everything can be renamed and described,
            # and the people list appears only where it means something.
            if not archived:
                who = _btn("Edit", "secondary", height=36, width=74)
                who.setToolTip("Rename, describe, and choose who can see it")
                who.clicked.connect(lambda _=False, c=channel: self._edit_channel(c))
                line.addWidget(who)

            # Only a super admin sees this at all. An ordinary admin pressing a
            # button that always fails teaches them the panel is unreliable.
            if self._is_super_admin():
                read = _btn("Read", "secondary", height=36, width=76)
                read.clicked.connect(lambda _=False, c=channel: self._read_channel(c))
                line.addWidget(read)

            line.addStretch()
            self._channels.setCellWidget(row, 3, actions)

    def _fill_members(self, members: list, channels: list, archived: bool):
        by_id = {c["id"]: c for c in channels}
        # Channels somebody can be added to or taken out of individually.
        # General and public announcement channels are not among them: every
        # member of the team is in those by definition.
        editable = [c for c in channels
                    if not c.get("is_default")
                    and (c["type"] != "ANNOUNCEMENT" or c.get("is_private"))]

        self._members.setRowCount(len(members))
        for row, member in enumerate(members):
            for column in range(self._members.columnCount()):
                _clear_cell(self._members, row, column)
            self._members.setItem(row, 0, _cell(member.get("name") or member["username"]))
            self._members.setItem(row, 1, _cell(member["employee_id"], mono=True))
            self._members.setItem(row, 2, _cell(member.get("role", ""), muted=True))

            extra = [by_id[cid]["name"] for cid in (member.get("channel_ids") or [])
                     if cid in by_id]
            listing = ", ".join(["General"] + extra)

            # The channels cell is where channel membership is CHANGED.
            #
            # It used to be plain text, and the only control on the row was a
            # red Remove that took the person out of the entire team. Asked to
            # drop somebody from one channel, the obvious thing to press was
            # that — and it removed them from everything, so they vanished
            # from the list altogether. Correct behaviour, wrong affordance:
            # the action people actually wanted had no button at all.
            if editable and not archived:
                # THE MARK THAT SAYS THIS CELL IS A CONTROL.
                #
                # It was a ✎ appended to the text, and it went out with the
                # emoji — which quietly removed the only sign that the column
                # could be clicked at all. The affordance matters more than
                # the glyph did: without it people press the red Remove
                # button instead, and that takes somebody out of the whole
                # team rather than one channel.
                edit = QPushButton(f"{listing}   Edit")
                edit.setCursor(Qt.CursorShape.PointingHandCursor)
                edit.setToolTip("Choose which channels this person can see")
                edit.setStyleSheet(
                    f"QPushButton {{ color:{C['text_secondary']};background:transparent;"
                    f"border:none;text-align:left;"
                    f"font-size:{_theme.Type.MICRO}px;padding:0 4px; }}"
                    f"QPushButton:hover {{ color:{C['accent_hover']}; }}")
                edit.clicked.connect(
                    lambda _c=False, m=member: self._member_channels(m, editable))
                self._members.setCellWidget(row, 3, edit)
            else:
                self._members.setItem(row, 3, _cell(listing, muted=True))

            if archived:
                self._members.setCellWidget(row, 4, QWidget())
                continue

            # There is deliberately NO red Remove sitting in this row.
            #
            # There was, and it took the person out of the WHOLE team. Asked
            # to drop somebody from one channel, that is the button people
            # reached for — three separate times in testing — and every time
            # the person disappeared from the team entirely, General included.
            # Renaming it and adding a warning did not help, because a red
            # button in the row is simply the most obvious thing on it.
            #
            # So the row opens an editor, and the team-wide removal lives at
            # the bottom of that editor, behind its own heading. The dangerous
            # action is still one click away — it is just no longer the click
            # you make by accident.
            edit = _btn("Edit", "secondary", height=36, width=84)
            edit.setToolTip("Channels, and removing them from the team")
            edit.clicked.connect(
                lambda _=False, m=member: self._member_channels(m, editable))
            holder = _cell_holder()
            line = QHBoxLayout(holder)
            line.setContentsMargins(4, 2, 4, 2)
            line.addWidget(edit)
            line.addStretch()
            self._members.setCellWidget(row, 4, holder)

    def _member_channels(self, member: dict, channels: list):
        """Which channels ONE person can see, without touching the team."""
        current = set(member.get("channel_ids") or [])
        name = member.get("name") or member["employee_id"]

        dialog = _BaseDialog(self, f"{name} — channels", 560)
        dialog.body.addWidget(_section(f"Channels for {name}"))
        dialog.body.addWidget(_muted_label(
            "General is always included — it comes with being in the team. "
            "Unticking a channel here removes them from that channel only; "
            "they stay in the team."))

        listing = QListWidget(dialog)
        listing.setStyleSheet(
            f"QListWidget{{background:{C['bg_surface_alt']};border:1px solid {C['border']};"
            f"border-radius:12px;color:{C['text_primary']};font-size:12px;padding:4px;}}"
            f"QListWidget::item{{padding:6px;}}")
        for channel in channels:
            item = QListWidgetItem(
                f"{channel['name']}"
                + ("   (announcement)" if channel["type"] == "ANNOUNCEMENT" else ""))
            item.setData(Qt.ItemDataRole.UserRole, channel["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if channel["id"] in current
                               else Qt.CheckState.Unchecked)
            listing.addItem(item)
        listing.setFixedHeight(180)
        dialog.body.addWidget(listing)

        if not channels:
            dialog.body.addWidget(_muted_label(
                "This team has no channels beyond General, which everyone in "
                "it can see."))

        dialog.body.addWidget(_divider())
        danger = _section("Remove from the team")
        dialog.body.addWidget(danger)
        dialog.body.addWidget(_muted_label(
            "This is the whole team, not one channel — they lose General too. "
            "Their messages stay either way."))
        kick = _btn(f"Remove {name} from the team", "danger", height=40)
        dialog.body.addWidget(kick)

        leaving = {"team": False}

        def leave_team():
            leaving["team"] = True
            dialog.accept()

        kick.clicked.connect(leave_team)
        dialog.buttons("Save")

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if leaving["team"]:
            self._remove_member(member)
            return

        wanted = {listing.item(i).data(Qt.ItemDataRole.UserRole)
                  for i in range(listing.count())
                  if listing.item(i).checkState() == Qt.CheckState.Checked}
        to_add = sorted(wanted - current)
        to_remove = sorted(current - wanted)
        self._apply_channel_changes(member["employee_id"], to_add, to_remove)

    def _apply_channel_changes(self, employee_id: str, to_add: list, to_remove: list):
        """Send the differences, and reload only once they have all landed."""
        if not to_add and not to_remove:
            return
        pending = len(to_add) + len(to_remove)

        def done(_payload=None):
            nonlocal pending
            pending -= 1
            # Reloading after each one would redraw the table halfway through
            # and briefly show a state that never existed.
            if pending <= 0:
                self._reload_detail()

        for channel_id in to_remove:
            self._write("DELETE",
                        f"/admin/channels/{channel_id}/members/{employee_id}",
                        None, done)
        for channel_id in to_add:
            self._write("POST", f"/admin/channels/{channel_id}/members",
                        {"employee_ids": [employee_id]}, done)

    # ── actions ─────────────────────────────────────────────────────────

    def _new_team(self):
        dialog = _NewTeamDialog(self, self._people)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload["name"]:
            QMessageBox.warning(self, "Name required", "Give the team a name.")
            return
        self._write("POST", "/admin/teams", payload, lambda _p: self.refresh())

    def _new_channel(self):
        if not self._detail:
            return
        dialog = _NewChannelDialog(self, self._detail["team"],
                                   self._detail.get("members") or [])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload["name"]:
            QMessageBox.warning(self, "Name required", "Give the channel a name.")
            return
        self._write("POST", f"/admin/teams/{self._selected}/channels", payload,
                    lambda _p: self._reload_detail())

    def _toggle_archive(self):
        if not self._detail:
            return
        team = self._detail["team"]
        if team.get("is_archived"):
            self._write("POST", f"/admin/teams/{team['id']}/archive",
                        {"archived": False}, lambda _p: self.refresh())
            return

        dialog = _ArchiveDialog(self, team)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reason = dialog.reason.text().strip()
        if not reason:
            # The server refuses this too; catching it here saves a round trip
            # on a link where that is most of a second.
            QMessageBox.warning(self, "Reason required",
                                "Say why. Months later an unexplained archived "
                                "team is a question nobody can answer.")
            return
        self._write("POST", f"/admin/teams/{team['id']}/archive",
                    {"archived": True, "reason": reason}, lambda _p: self.refresh())

    def _add_members(self):
        if not self._detail:
            return
        already = {m["employee_id"] for m in self._detail.get("members") or []}
        candidates = [p for p in self._people if p["employee_id"] not in already]
        if not candidates:
            QMessageBox.information(self, "Members",
                                    "Everyone is already in this team.")
            return
        dialog = _BaseDialog(self, "Add members", 480)
        dialog.body.addWidget(_section(f"Add to {self._detail['team']['name']}"))
        dialog.body.addWidget(_muted_label(
            "They get the General channel. Other channels are granted separately."))
        listing = _pick_people(dialog, candidates)
        listing.setFixedHeight(240)
        dialog.body.addWidget(listing)
        dialog.buttons("Add")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = _checked_ids(listing)
        if not chosen:
            return
        self._write("POST", f"/admin/teams/{self._selected}/members",
                    {"employee_ids": chosen}, lambda _p: self._reload_detail())

    def _remove_member(self, member: dict):
        name = member.get("name") or member["employee_id"]
        answer = QMessageBox.question(
            self, "Remove from team",
            f"Remove {name} from the whole {self._detail['team']['name']} team?\n\n"
            f"They lose every channel in it, including General.\n\n"
            f"To take them out of ONE channel, use that channel's Members "
            f"button instead.\n\n"
            f"Their messages stay either way — being removed from a team is "
            f"not a reason to take their side of every conversation with them.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._write("DELETE",
                    f"/admin/teams/{self._selected}/members/{member['employee_id']}",
                    None, lambda _p: self._reload_detail())

    def _edit_channel(self, channel: dict):
        """
        One page for a channel: what it is called, and who can see it.

        A team-wide channel — General, or a public announcement — has no
        membership list, because everyone in the team is in it. Rather than
        offering no Edit at all (which left those rows with nothing), the
        dialog simply says so and offers the parts that do apply.
        """
        if not self._detail:
            return
        team_wide = channel.get("is_default") or (
            channel["type"] == "ANNOUNCEMENT" and not channel.get("is_private"))

        dialog = _BaseDialog(self, f"Edit {channel['name']}", 560)
        dialog.body.addWidget(_section(f"Edit {channel['name']}"))

        dialog.body.addWidget(QLabel("Name"))
        name = QLineEdit(channel["name"])
        if channel.get("is_default"):
            # The server refuses this too. Employees are told their team has a
            # General; renaming it would leave them looking for one.
            name.setDisabled(True)
            name.setToolTip("The General channel cannot be renamed.")
        dialog.body.addWidget(name)

        dialog.body.addWidget(QLabel("Description"))
        description = QLineEdit(channel.get("description") or "")
        description.setPlaceholderText("Optional")
        dialog.body.addWidget(description)

        dialog.body.addWidget(_divider())

        team_members = self._detail.get("members") or []
        inside = [m for m in team_members
                  if channel["id"] in (m.get("channel_ids") or [])]
        outside = [m for m in team_members
                   if channel["id"] not in (m.get("channel_ids") or [])]
        removed: set = set()
        listing = None
        adding = None

        if team_wide:
            note = _muted_label(
                "Everyone in the team can see this channel — that is what "
                "makes it "
                + ("the team's General." if channel.get("is_default")
                   else "an announcement channel.")
                + " To limit who sees it, make a normal channel instead.")
            dialog.body.addWidget(note)
        else:
            dialog.body.addWidget(_section("Who can see it"))
            dialog.body.addWidget(_muted_label(
                "Removing somebody here takes them out of THIS channel only. "
                "They stay in the team and keep General."))

            area, column = _people_rows(dialog, 170)

            def draw():
                while column.count():
                    item = column.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                staying = [m for m in inside if m["employee_id"] not in removed]
                if not staying:
                    column.addWidget(_muted_label("Nobody is in this channel."))
                    column.addStretch()
                    return
                for member in staying:
                    drop = _btn("Remove", "danger", height=36, width=96)
                    drop.setToolTip(f"Remove from {channel['name']} only")
                    drop.clicked.connect(lambda _c=False, m=member: take_out(m))
                    column.addWidget(_person_row(
                        f"{member.get('name') or member['username']}"
                        f"   ·   {member['employee_id']}", drop))
                column.addStretch()

            def take_out(member):
                removed.add(member["employee_id"])
                draw()

            draw()
            dialog.body.addWidget(area)

            dialog.body.addWidget(QLabel("Add somebody"))
            adding = _pick_people(dialog, outside)
            adding.setFixedHeight(130)
            if not outside:
                adding.addItem(QListWidgetItem("Everybody in the team is already in it."))
                adding.setDisabled(True)
            dialog.body.addWidget(adding)

        dialog.buttons("Save")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        pending = 0
        renamed = (not channel.get("is_default")
                   and name.text().strip()
                   and name.text().strip() != channel["name"])
        redescribed = (description.text().strip() or None) != (channel.get("description") or None)
        to_remove = sorted(removed)
        to_add = _checked_ids(adding) if (adding is not None and outside) else []
        pending = (1 if (renamed or redescribed) else 0) + len(to_remove) + (1 if to_add else 0)
        if pending == 0:
            return

        def done(_payload=None):
            nonlocal pending
            pending -= 1
            # Reloaded only once every change has landed, so the table does
            # not redraw halfway through and show a state that never existed.
            if pending <= 0:
                self._reload_detail()

        if renamed or redescribed:
            body = {"description": description.text().strip()}
            if renamed:
                body["name"] = name.text().strip()
            self._write("PATCH", f"/admin/channels/{channel['id']}", body, done)
        for employee_id in to_remove:
            self._write("DELETE",
                        f"/admin/channels/{channel['id']}/members/{employee_id}",
                        None, done)
        if to_add:
            self._write("POST", f"/admin/channels/{channel['id']}/members",
                        {"employee_ids": to_add}, done)

    def _announce(self, channel: dict):
        dialog = _AnnounceDialog(self, channel)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = dialog.text.toPlainText().strip()
        if not text:
            return
        self._write("POST", f"/admin/channels/{channel['id']}/announce",
                    {"body": text},
                    lambda _p: QMessageBox.information(
                        self, "Posted", "Everyone in the team has been notified."))

    def _read_channel(self, channel: dict):
        if not self._is_super_admin():
            return
        dialog = _ViewChatDialog(self, channel)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if payload["purpose"] not in NO_REFERENCE_NEEDED and not payload["reference_id"]:
            QMessageBox.warning(self, "Reference required",
                                "Give the complaint or case number this relates to.")
            return
        if payload["purpose"] == "OTHER" and not payload["note"]:
            QMessageBox.warning(self, "Reason required",
                                "Describe why you are reading this conversation.")
            return
        self._write("POST", "/admin/chat/view", payload, self._on_transcript)

    def _on_transcript(self, payload):
        _TranscriptDialog(self, payload).exec()

    def _reload_detail(self):
        if self._selected:
            self._fetch(f"/admin/teams/{self._selected}", self._on_detail)
        self._fetch("/admin/teams", self._on_teams_counts_only)

    def _on_teams_counts_only(self, payload):
        # Refresh the left-hand counts without disturbing which team is open —
        # rebuilding the list would reset the selection and bounce the admin
        # back to the first team mid-task.
        self._teams = payload.get("teams") or []
        for row in range(self._team_list.count()):
            item = self._team_list.item(row)
            team = next((t for t in self._teams
                         if t["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
            if not team:
                continue
            label = team["name"] + ("   · archived" if team.get("is_archived") else "")
            item.setText(f"{label}\n{team['member_count']} member(s)"
                         f" · {team['channel_count']} channel(s)")
