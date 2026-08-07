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
        if not (stripped.startswith("self._") and "= _" in stripped):
            continue
        attr = stripped.split("=")[0].strip().replace("self.", "")
        if attr.endswith("_tab"):
            built.add(attr)
    listed = set(acp.AdminConfigPanel.TAB_ATTRS)
    check("every tab the panel builds is in the shutdown list",
          built <= listed, f"missing: {sorted(built - listed)}")
    check("and the list names nothing that is not built",
          listed <= built, f"stale: {sorted(listed - built)}")
    check("the sidebar and the stack are in the same order",
          [p["key"] for p in acp.PAGES].index("teams") == 5,
          str([p["key"] for p in acp.PAGES]))

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
          "✎" in cell_text(tab._members, 0, 3), cell_text(tab._members, 0, 3))

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
