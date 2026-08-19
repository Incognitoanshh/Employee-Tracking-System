"""
The admin panel's Teams tab.

What is worth checking without a server running is the part that decides what
an administrator is allowed to reach, because getting that wrong is invisible
until somebody uses it:

  * The button that reads somebody's conversation must not exist for an
    ordinary admin. The server refuses them anyway, but a button that always
    fails teaches people the panel is unreliable, and one that is merely
    hidden-but-present is a permission check waiting to be bypassed.
  * A purpose must be demanded before the conversation is shown, and 'Other'
    must not be a way around giving a reason.
  * Archiving must demand a reason.
  * An archived team must not offer the controls that write into it.
  * Every tab must be in the shutdown list, or its threads outlive logout and
    a QThread destroyed while running takes the application down.

Run:  python3 tests/test_admin_teams_tab.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ISOLATED FROM ANYTHING REAL, BEFORE ANY CLIENT MODULE IS IMPORTED.
#
# Without these two the client's own config falls back to its defaults: the
# installed app's data directory, and the PRODUCTION server. Running this file
# on a machine that has the app installed then wrote into the real local
# database and uploaded to the real server — rows under a made-up employee id
# turned up in the company's audit log, which is exactly what happened.
#
# setdefault, so a harness that points these somewhere on purpose still wins.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_admin_teams_test_")
import client.core.config as config
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt

app = QApplication.instance() or QApplication([])

from client.application.managers.session_manager import SessionManager
from client.presentation.windows import admin_config_panel as acp
from client.presentation.windows import admin_teams_tab as att

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


TEAM = {"id": 1, "name": "Development", "description": "Product team",
        "is_archived": False, "archived_reason": None}
CHANNELS = [
    {"id": 1, "name": "General", "type": "STANDARD", "is_default": True,
     "is_private": False, "message_count": 120, "member_count": 0},
    {"id": 2, "name": "Backend", "type": "STANDARD", "is_default": False,
     "is_private": False, "message_count": 40, "member_count": 2},
    {"id": 3, "name": "Company Updates", "type": "ANNOUNCEMENT", "is_default": False,
     "is_private": False, "message_count": 3, "member_count": 0},
]
MEMBERS = [
    {"employee_id": "E001", "name": "Rajesh Kumar", "username": "emp1",
     "role": "employee", "channel_ids": [2]},
    {"employee_id": "E002", "name": "Amit Sharma", "username": "emp2",
     "role": "employee", "channel_ids": []},
]
DETAIL = {"team": TEAM, "channels": CHANNELS, "members": MEMBERS}


def buttons_in(table, row, column):
    holder = table.cellWidget(row, column)
    if holder is None:
        return []
    from PySide6.QtWidgets import QPushButton
    if isinstance(holder, QPushButton):
        return [holder.text()]
    return [b.text() for b in holder.findChildren(QPushButton)]


def cell_text(table, row, column):
    """The Channels column is a button now, not a plain cell."""
    item = table.item(row, column)
    if item is not None:
        return item.text()
    widget = table.cellWidget(row, column)
    return widget.text() if widget is not None and hasattr(widget, "text") else ""


def main():
    print("Admin panel — Teams tab\n")

    # ── the shutdown list ───────────────────────────────────────────────
    print("Shutting down cleanly")
    built = set()
    source = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "client", "presentation", "windows",
        "admin_config_panel.py"), encoding="utf-8").read()
    for line in source.splitlines():
        stripped = line.strip()
        # endswith, not "in" — `self._logs_table` contains "_tab" too, and
        # matching it made this check fail for a reason that had nothing to do
        # with what it is testing.
        # `= _` alone misses a tab whose class is not underscore-prefixed —
        # _mychat_tab is a TeamPage, borrowed from the employee panel.
        if not (stripped.startswith("self._") and "= " in stripped
                and stripped.rstrip().endswith(")")):
            continue
        attr = stripped.split("=")[0].strip().replace("self.", "")
        if attr.endswith("_tab"):
            built.add(attr)
    listed = set(acp.AdminConfigPanel.TAB_ATTRS)
    check("every tab the panel builds is in the shutdown list",
          built <= listed, f"missing: {sorted(built - listed)}")
    check("and the list names nothing that is not built",
          listed <= built, f"stale: {sorted(listed - built)}")
    # The real invariant, rather than "teams is item five".
    #
    # The panel switches pages by INDEX, so the sidebar's order and the order
    # widgets are added to the stack have to match exactly. This used to be
    # written as a hardcoded position, which broke the day a page was added in
    # front of Teams — a failure about counting, not about correctness.
    #
    # Every page key maps to `self._<key>_tab`, so the mount order can be read
    # out of the source and compared against PAGES directly. Adding a page now
    # only fails this if it is genuinely in the wrong place.
    mounted = []
    inside = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("for tab in ("):
            inside = True
            continue
        if inside:
            if stripped.startswith(")"):
                break
            if stripped.startswith("self._"):
                mounted.append(stripped.rstrip(",").replace("self.", ""))
    expected = [f'_{p["key"]}_tab' for p in acp.PAGES]
    check("every menu entry has a page mounted for it",
          len(mounted) == len(expected), f"{len(expected)} menu entries, {len(mounted)} mounted")
    check("the sidebar and the stack are in the same order",
          mounted == expected, f"menu {expected} vs stack {mounted}")

    # ── an ordinary admin ───────────────────────────────────────────────
    print("\nWhat an ordinary admin sees")
    SessionManager.role = "admin"
    tab = att._TeamsTab()
    tab._on_people({"employees": [
        {"employee_id": "E003", "name": "Priya Singh", "username": "emp3"}]})
    tab._on_teams({"teams": [{**TEAM, "member_count": 2, "channel_count": 3}]})
    tab._on_detail(DETAIL)

    check("channels are listed", tab._channels.rowCount() == 3,
          str(tab._channels.rowCount()))
    reads = [b for row in range(3) for b in buttons_in(tab._channels, row, 3)
             if b == "Read"]
    check("there is NO button to read a conversation",
          len(reads) == 0, f"{len(reads)} found")
    posts = [b for row in range(3) for b in buttons_in(tab._channels, row, 3)
             if b == "Post"]
    check("but an announcement channel can be posted to",
          len(posts) == 1, f"{len(posts)} found")

    check("members are listed with the channels they can reach",
          tab._members.rowCount() == 2 and "Backend" in cell_text(tab._members, 0, 3),
          cell_text(tab._members, 0, 3))
    check("and General is always shown, because team membership grants it",
          "General" in cell_text(tab._members, 1, 3), cell_text(tab._members, 1, 3))
    check("and that column is itself the way to change them",
          "Edit" in cell_text(tab._members, 0, 3), cell_text(tab._members, 0, 3))

    # The trap this closes: the only control on the row used to be a red
    # "Remove" that took somebody out of the ENTIRE team. Asked to drop
    # somebody from one channel, that is what people pressed — twice — and the
    # person disappeared from the list completely.
    row_buttons = buttons_in(tab._members, 0, 4)
    check("the row opens an editor rather than offering Remove directly",
          row_buttons == ["Edit"], str(row_buttons))
    check("and there is NO destructive button loose in the row",
          not any("remove" in b.lower() for b in row_buttons), str(row_buttons))

    # ── a super admin ───────────────────────────────────────────────────
    print("\nWhat a super admin sees")
    SessionManager.role = "super_admin"
    tab2 = att._TeamsTab()
    tab2._on_teams({"teams": [{**TEAM, "member_count": 2, "channel_count": 3}]})
    tab2._on_detail(DETAIL)
    reads = [b for row in range(3) for b in buttons_in(tab2._channels, row, 3)
             if b == "Read"]
    check("every channel can be read", len(reads) == 3, f"{len(reads)} found")

    # ── an archived team ────────────────────────────────────────────────
    print("\nAn archived team")
    tab2._on_detail({**DETAIL,
                     "team": {**TEAM, "is_archived": True,
                              "archived_reason": "department merged"}})
    check("the controls that write into it are gone",
          not tab2._new_channel_btn.isVisibleTo(tab2)
          and not tab2._add_members_btn.isVisibleTo(tab2))
    check("the archive button offers to restore instead",
          tab2._archive_btn.text() == "Restore", tab2._archive_btn.text())
    check("and the reason is on screen, not buried in a log",
          "department merged" in tab2._subtitle.text(), tab2._subtitle.text())
    check("members cannot be removed from it either",
          buttons_in(tab2._members, 0, 4) == [], str(buttons_in(tab2._members, 0, 4)))
    check("and their channels cannot be edited either",
          "✎" not in cell_text(tab2._members, 0, 3), cell_text(tab2._members, 0, 3))
    # An announcement channel in an archived team must not offer Post — the
    # server refuses it, and a button that always fails is worse than none.
    posts = [b for row in range(3) for b in buttons_in(tab2._channels, row, 3)
             if b == "Post"]
    check("and nothing can be announced into it", len(posts) == 0, f"{len(posts)} found")

    # ── the read dialog's rules ─────────────────────────────────────────
    print("\nBefore a conversation is shown")
    dialog = att._ViewChatDialog(None, CHANNELS[0])
    check("a purpose must be chosen from a fixed list",
          dialog.purpose.count() == len(att.PURPOSES), str(dialog.purpose.count()))
    check("and the list is the same one the server accepts",
          [dialog.purpose.itemData(i) for i in range(dialog.purpose.count())]
          == [value for value, _ in att.PURPOSES])

    dialog.purpose.setCurrentIndex(
        [v for v, _ in att.PURPOSES].index("COMPLAINT"))
    check("a complaint asks for a reference",
          dialog.reference.isVisibleTo(dialog) and not dialog.note.isVisibleTo(dialog))

    dialog.purpose.setCurrentIndex([v for v, _ in att.PURPOSES].index("OTHER"))
    check("'Other' asks for a written reason instead — it is not a way out",
          dialog.note.isVisibleTo(dialog) and not dialog.reference.isVisibleTo(dialog))

    dialog.purpose.setCurrentIndex(
        [v for v, _ in att.PURPOSES].index("EMPLOYEE_REQUEST"))
    check("somebody asking to see their own channel needs no ticket number",
          not dialog.reference.isVisibleTo(dialog))

    dialog.purpose.setCurrentIndex([v for v, _ in att.PURPOSES].index("LEGAL"))
    dialog.reference.setText("Case #11")
    payload = dialog.payload()
    check("the payload carries the channel, purpose and reference",
          payload["channel_id"] == 1 and payload["purpose"] == "LEGAL"
          and payload["reference_id"] == "Case #11", str(payload))

    # ── archiving demands a reason ──────────────────────────────────────
    print("\nArchiving")
    archive = att._ArchiveDialog(None, TEAM)
    check("the reason box starts empty, so it has to be typed",
          archive.reason.text() == "")
    from PySide6.QtWidgets import QLabel as _QLabel
    wording = " ".join(w.text() for w in archive.findChildren(_QLabel))
    check("and the dialog says the conversation survives, so archiving is not "
          "mistaken for deleting",
          "searchable" in wording.lower() and "no delete" in wording.lower(),
          wording[:120])

    # ── the transcript shows what was edited away ───────────────────────
    print("\nThe transcript")
    transcript = att._TranscriptDialog(None, {
        "channel": {"id": 1, "name": "General", "team_name": "Development"},
        "messages": [{"seq": 5, "sender_name": "Rajesh Kumar", "former": False,
                      "body": "Hello sir", "created_at": "2026-08-07T09:00:00Z",
                      "edit_count": 2}],
        "edit_history": [
            {"message_seq": 5, "version": 1, "old_body": "Hello sir",
             "edited_name": "Rajesh Kumar", "edited_at": "2026-08-07T09:01:00Z"},
            {"message_seq": 5, "version": 2, "old_body": "Hello sir pls ignore",
             "edited_name": "Rajesh Kumar", "edited_at": "2026-08-07T09:02:00Z"},
        ]})
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in transcript.findChildren(QLabel)]
    check("the message is shown", any("Hello sir" in t for t in texts))
    check("and so is the wording that was edited away",
          any("pls ignore" in t for t in texts),
          "the edit history is kept precisely so this view can show it")
    check("each earlier version is numbered",
          any("version 1" in t for t in texts) and any("version 2" in t for t in texts))

    # ── a new channel ───────────────────────────────────────────────────
    print("\nCreating a channel")
    new_channel = att._NewChannelDialog(None, TEAM, MEMBERS)
    check("a standard channel asks who can see it",
          new_channel.people.isEnabled())
    new_channel.type.setCurrentIndex(1)          # ANNOUNCEMENT
    check("a public announcement channel does not — everyone gets it",
          not new_channel.people.isEnabled())
    check("and says so, rather than leaving an empty list looking broken",
          "everyone" in new_channel._note.text().lower(), new_channel._note.text())

    # ── channel membership, separately from the team ────────────────────
    print("\nTaking somebody out of one channel")
    SessionManager.role = "admin"
    tab3 = att._TeamsTab()
    tab3._on_teams({"teams": [{**TEAM, "member_count": 2, "channel_count": 3}]})
    tab3._on_detail(DETAIL)

    def buttons_for(name):
        for row in range(tab3._channels.rowCount()):
            if name in tab3._channels.item(row, 0).text():
                return buttons_in(tab3._channels, row, 3)
        return None

    check("every channel has an Edit button, General included",
          all("Edit" in (buttons_for(n) or [])
              for n in ("General", "Backend", "Company Updates")),
          str({n: buttons_for(n) for n in ("General", "Backend", "Company Updates")}))

    calls = []
    tab3._write = lambda method, path, body, on_done: calls.append((method, path, body))
    original_exec = att._BaseDialog.exec

    def with_dialog(action):
        """Open the dialog, do something to it, accept."""
        def runner(self):
            action(self)
            return QDialog.DialogCode.Accepted
        att._BaseDialog.exec = runner

    try:
        # The dialog lists who is in the channel, each with their own Remove —
        # not a grid of tick boxes. Asked to take somebody out, people look
        # for the word "remove".
        seen = {}

        def inspect(dialog):
            from PySide6.QtWidgets import QPushButton as _PB, QLabel as _QL
            seen["removes"] = [b for b in dialog.findChildren(_PB) if b.text() == "Remove"]
            seen["labels"] = [w.text() for w in dialog.findChildren(_QL)]

        with_dialog(inspect)
        calls.clear()
        tab3._edit_channel(CHANNELS[1])           # Backend: Rajesh only
        check("each person in the channel has their own Remove",
              len(seen["removes"]) == 1, str(len(seen["removes"])))
        check("and the channel can be renamed from the same page",
              any("Name" == t for t in seen["labels"]), str(seen["labels"])[:120])
        check("and the dialog says the removal is channel-only",
              any("stay in the team" in t for t in seen["labels"]),
              str(seen["labels"])[:120])
        check("opening it and closing it changes nothing",
              calls == [], str(calls))

        # Press that Remove, then Save.
        def remove_first(dialog):
            from PySide6.QtWidgets import QPushButton as _PB
            for widget in dialog.findChildren(_PB):
                if widget.text() == "Remove":
                    widget.click()
                    break

        with_dialog(remove_first)
        calls.clear()
        tab3._edit_channel(CHANNELS[1])
        check("removing hits the CHANNEL route, not the team one",
              calls and calls[0][0] == "DELETE"
              and calls[0][1] == "/admin/channels/2/members/E001",
              str(calls))
        check("and nothing touches team membership",
              not any("/teams/" in path for _m, path, _b in calls),
              str([p for _m, p, _b in calls]))

        # Adding is the separate list underneath.
        def add_amit(dialog):
            from PySide6.QtWidgets import QListWidget as _LW
            listings = dialog.findChildren(_LW)
            for item_index in range(listings[-1].count()):
                item = listings[-1].item(item_index)
                if item.data(Qt.ItemDataRole.UserRole) == "E002":
                    item.setCheckState(Qt.CheckState.Checked)

        with_dialog(add_amit)
        calls.clear()
        tab3._edit_channel(CHANNELS[1])
        check("adding goes to the channel too",
              any(m == "POST" and p == "/admin/channels/2/members"
                  and b.get("employee_ids") == ["E002"] for m, p, b in calls),
              str(calls))
    finally:
        att._BaseDialog.exec = original_exec


    # A team-wide channel has no membership to edit, but must still be
    # editable — leaving those rows with no Edit at all is what prompted this.
    def peek(dialog):
        from PySide6.QtWidgets import QLabel as _QL, QPushButton as _PB
        seen["labels"] = [w.text() for w in dialog.findChildren(_QL)]
        seen["removes"] = [b for b in dialog.findChildren(_PB) if b.text() == "Remove"]

    original_exec2 = att._BaseDialog.exec
    try:
        att._BaseDialog.exec = lambda self: (peek(self), QDialog.DialogCode.Rejected)[1]
        tab3._edit_channel(CHANNELS[0])          # General
        check("General can be edited, but offers no people list",
              seen["removes"] == [], str(len(seen["removes"])))
        check("and says why instead of showing an empty box",
              any("everyone in the team" in t.lower() for t in seen["labels"]),
              str(seen["labels"])[:140])

        tab3._edit_channel(CHANNELS[2])          # public announcement
        check("a public announcement channel is the same",
              seen["removes"] == [] and any("announcement channel" in t.lower()
                                            for t in seen["labels"]),
              str(seen["labels"])[:140])
    finally:
        att._BaseDialog.exec = original_exec2

    # Removing from the team is now inside the member editor, behind its own
    # heading — not a red button sitting in the row waiting to be mis-clicked.
    print("\nRemoving from the whole team")
    kicked = []
    tab3._remove_member = lambda m: kicked.append(m["employee_id"])
    original_exec3 = att._BaseDialog.exec
    try:
        found = {}

        def look(dialog):
            from PySide6.QtWidgets import QPushButton as _PB, QLabel as _QL
            found["kick"] = [b for b in dialog.findChildren(_PB)
                             if "from the team" in b.text()]
            found["labels"] = [w.text() for w in dialog.findChildren(_QL)]

        att._BaseDialog.exec = lambda self: (look(self), QDialog.DialogCode.Rejected)[1]
        tab3._member_channels(MEMBERS[0], [CHANNELS[1]])
        check("the team removal lives inside the member editor",
              len(found["kick"]) == 1, str(len(found["kick"])))
        check("under a heading that says it is the whole team",
              any("whole team" in t.lower() or "not one channel" in t.lower()
                  for t in found["labels"]), str(found["labels"])[:160])
    finally:
        att._BaseDialog.exec = original_exec3

    # ── does the button actually fit? ───────────────────────────────────
    print("\nNothing is clipped")
    # This is measured, not eyeballed. The Remove button inside the channel
    # editor was clipped by the right edge twice, and both times it looked
    # fixed: a QListWidget sizes a row to the ITEM, whose width comes from the
    # size hint rather than the visible area, so a row wide enough for a
    # button overflows the viewport. Turning off the horizontal scrollbar hid
    # the scrollbar, not the overflow — the button was simply cut instead.
    from PySide6.QtWidgets import QPushButton as _PB, QScrollArea as _SA

    measured = {}

    def measure(dialog):
        dialog.resize(dialog.sizeHint())
        dialog.show()
        app.processEvents()
        areas = dialog.findChildren(_SA)
        buttons = [b for b in dialog.findChildren(_PB) if b.text() == "Remove"]
        measured["ok"] = []
        for button in buttons:
            area = next((a for a in areas if a.isAncestorOf(button)), None)
            if area is None:
                continue
            # Where the button's right edge lands inside the scrolling
            # viewport. Anything past the viewport width is off-screen.
            right = button.mapTo(area.viewport(), button.rect().topRight()).x()
            measured["ok"].append((button.text(), right, area.viewport().width()))
        dialog.hide()

    original = att._BaseDialog.exec
    try:
        att._BaseDialog.exec = lambda self: (measure(self),
                                             QDialog.DialogCode.Rejected)[1]
        tab3._edit_channel(CHANNELS[1])          # Backend — Rajesh is in it
        check("the Remove button is drawn at all",
              len(measured["ok"]) == 1, str(measured["ok"]))
        fits = all(right <= width for _t, right, width in measured["ok"])
        check("and its right edge is INSIDE the visible area, not past it",
              fits, str(measured["ok"]))
    finally:
        att._BaseDialog.exec = original

    # ── an admin who is in a team can actually talk in it ────────────────
    print("\nThe admin's own chat")
    # The gap this closes: an admin could be ADDED to a team — the server
    # served them their channels like anybody else — but chat existed only in
    # the employee panel, which an admin never sees. So they were a member
    # with no way to read or send anything, and the only sign was somebody
    # asking why their messages went unanswered.
    from client.presentation.windows.admin_config_panel import AdminConfigPanel, PAGES

    keys = [p["key"] for p in PAGES]
    check("the console has a page for it", "mychat" in keys, str(keys))
    check("and it is listed after Teams & Chat, which is a different thing",
          keys.index("mychat") == keys.index("teams") + 1, str(keys))
    check("the shutdown list knows about it",
          "_mychat_tab" in AdminConfigPanel.TAB_ATTRS,
          str(AdminConfigPanel.TAB_ATTRS))
    check("the sidebar and the stack still line up",
          len(keys) == len(AdminConfigPanel.TAB_ATTRS),
          f"{len(keys)} pages vs {len(AdminConfigPanel.TAB_ATTRS)} tabs")

    # It must be the SAME widget the employees use — a second chat
    # implementation is a second set of the bugs already fixed in this one.
    # NOT rebound to `acp` — that name is already imported at module scope,
    # and assigning it here makes Python treat every earlier use in this
    # function as a local read before assignment.
    source = open(acp.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    check("it reuses the employee panel's chat rather than a second copy",
          "from client.presentation.windows.team_page import TeamPage" in source)
    check("and the chat connection is built outside _build_central, so a "
          "theme switch does not replace it",
          source.index("self.chat = ChatManager(self)")
          < source.index("def _build_central"),
          "a rebuild would leave the old one polling")

    # ── two things that only show up on screen ──────────────────────────
    print("\nWhat the table actually draws")
    from PySide6.QtGui import QPalette
    from client.presentation.windows.admin_config_panel import _global_stylesheet
    from client.presentation import theme as _th

    _th.set_theme("light")
    painted = att._TeamsTab()
    painted.setStyleSheet(_global_stylesheet())
    painted._on_teams({"teams": [{**TEAM, "member_count": 1, "channel_count": 3}]})
    painted._on_detail(DETAIL)
    painted.show()
    app.processEvents()

    # A wrapper styled with a bare `background:transparent` applies that to
    # everything inside it too, so the button in the cell lost its own fill
    # and rendered as an empty outline — right variant, right rule, painted
    # over by its own parent.
    post = None
    for row in range(painted._channels.rowCount()):
        holder = painted._channels.cellWidget(row, 3)
        if holder is None:
            continue
        for widget in holder.findChildren(_PB):
            if widget.text() == "Post":
                post = widget
    check("the Post button exists", post is not None)
    check("and its background survives the cell wrapper",
          post is not None
          and post.palette().color(QPalette.ColorRole.Button).name() == "#2563eb",
          post.palette().color(QPalette.ColorRole.Button).name() if post else "-")

    # A cell holds an item OR a widget, and setting one does not remove the
    # other. These tables are refilled in place, so a cell that held text and
    # now holds a button showed both at once, overlapping into nonsense.
    def both_in_channels_cell():
        offenders = []
        for row in range(painted._members.rowCount()):
            if painted._members.item(row, 3) and painted._members.cellWidget(row, 3):
                offenders.append(row)
        return offenders

    painted._on_detail({**DETAIL, "team": {**TEAM, "is_archived": True,
                                           "archived_reason": "merged"}})
    painted._on_detail(DETAIL)
    painted._on_detail({**DETAIL, "team": {**TEAM, "is_archived": True,
                                           "archived_reason": "merged"}})
    app.processEvents()
    check("no cell ends up holding text AND a widget after repeated refills",
          both_in_channels_cell() == [], f"rows {both_in_channels_cell()}")
    painted.hide()
    _th.set_theme("dark")

    # ── nothing is clipped by its own height ────────────────────────────
    print("\nButtons are tall enough for their labels")
    # Call sites asked for 24 or 26 to fit table rows and setFixedHeight
    # honoured it, so the bottom of every button was cut — on every tab, not
    # just this one. It read as a rendering glitch rather than a size someone
    # had chosen, which is why nobody traced it.
    from client.presentation.windows.admin_config_panel import _btn as _mkbtn
    from PySide6.QtWidgets import QWidget as _QW, QHBoxLayout as _QH

    holder = _QW()
    holder.setStyleSheet(_global_stylesheet())
    line = _QH(holder)
    asked = [_mkbtn(label, "secondary", height=h)
             for label, h in (("View", 26), ("Manage ▾", 24), ("Remove", 26))]
    for b in asked:
        line.addWidget(b)
    holder.show()
    app.processEvents()
    clipped = [f"{b.text()}({b.height()}<{b.minimumSizeHint().height()})"
               for b in asked if b.height() < b.minimumSizeHint().height()]
    check("a button asked for less than it needs is given what it needs",
          clipped == [], ", ".join(clipped))
    holder.hide()

    # And the row it sits in has to be able to hold it.
    #
    # This is where the last two attempts went wrong: the button was the right
    # height and still drawn clipped, because QTableWidget::item padding is
    # taken out of the cell BEFORE the cell widget is given its geometry. At
    # 10px top and bottom it ate 21px, so a 42px row offered 21px of usable
    # space to a 32px button. Measuring the button alone never showed it.
    measured_rows = att._TeamsTab()
    measured_rows.setStyleSheet(_global_stylesheet())
    measured_rows._on_teams({"teams": [{**TEAM, "member_count": 1, "channel_count": 3}]})
    measured_rows._on_detail(DETAIL)
    measured_rows.resize(1000, 700)
    measured_rows.show()
    app.processEvents()
    app.processEvents()

    too_small = []
    for table in (measured_rows._channels, measured_rows._members):
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                cell = table.cellWidget(row, column)
                if cell is None:
                    continue
                for button in cell.findChildren(_PB):
                    if button.height() > cell.height():
                        too_small.append(
                            f"{button.text()} {button.height()}px in {cell.height()}px")
    check("and the table row leaves enough space for it after cell padding",
          too_small == [], "; ".join(too_small))
    measured_rows.hide()

    print()
    if failures:
        print(f"{failures} failure(s)")
    else:
        print("all admin teams tab checks passed")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if failures else 0)


if __name__ == "__main__":
    main()
