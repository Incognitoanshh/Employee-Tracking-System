"""Reactions in the chat panel: the chips, and what pressing one does.

WHY THESE ARE THE CHECKS. A reaction is a one-tap gesture, so everything that
matters is in the details of that tap: that the row says whether YOU reacted
(otherwise the only way to find out is to press it and watch the number move),
that pressing the chip toggles rather than always adds, and that a withdrawn
message carries no approving thumbs under its tombstone.

Run:  python3 tests/test_chat_reactions.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows.team_page import _Bubble  # noqa: E402

BASE = {"seq": 7, "sender_id": "E001", "sender_name": "Raju Kumar",
        "body": "deploy done", "created_at": "2026-08-18 05:00:00"}


def bubble(**extra):
    message = dict(BASE)
    message.update(extra)
    return _Bubble(message, mine=extra.pop("mine", False), can_post=True)


print("\nThe chips under a message")
b = bubble(reactions=[{"emoji": "👍", "count": 2, "mine": True},
                      {"emoji": "🎉", "count": 1, "mine": False}])
chips = [x for x in b.findChildren(QPushButton)
         if x.text().startswith("👍") or x.text().startswith("🎉")]
check("one chip per emoji, with its count", len(chips) == 2,
      str([c.text() for c in chips]))
mine_chip = next(c for c in chips if c.text().startswith("👍"))
theirs = next(c for c in chips if c.text().startswith("🎉"))
check("yours looks different from theirs",
      mine_chip.styleSheet() != theirs.styleSheet(),
      "with no pressed state the row says how many reacted but not whether "
      "you are one of them")
check("and says so on hover", "remove" in mine_chip.toolTip().lower(),
      mine_chip.toolTip())

print("\nPressing one")
fired = []
b.react_requested.connect(lambda seq, emoji: fired.append((seq, emoji)))
mine_chip.click()
check("the chip asks to toggle that emoji", fired == [(7, "👍")], str(fired))

fired.clear()
react_button = next((x for x in b.findChildren(QPushButton)
                     if x.text() == "React"), None)
check("there is a React action on the message", react_button is not None)

print("\nWhat is not shown")
b2 = bubble(deleted=True, reactions=[{"emoji": "👍", "count": 3, "mine": True}])
check("a withdrawn message carries no reactions",
      not [x for x in b2.findChildren(QPushButton) if x.text().startswith("👍")],
      "a row of approving thumbs under a tombstone")
b3 = bubble(reactions=[])
check("and a message with none has no empty strip",
      not [x for x in b3.findChildren(QPushButton) if "  " in x.text()
           and x.text()[0] not in "RPEU"],
      "an empty row on every message costs a line of the conversation")
b4 = bubble(reactions=[{"emoji": "👍", "count": 0, "mine": False}])
check("a count of zero draws nothing",
      not [x for x in b4.findChildren(QPushButton) if x.text().startswith("👍")])

print("\nThe choices come from the server")
source = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "team_page.py"), encoding="utf-8").read()
check("the panel asks for them", "ChatManager.reaction_choices" in source)
check("and caches them on the class rather than per message",
      "REACTION_CHOICES" in source)
manager = open(os.path.join(ROOT, "client", "application", "managers",
                            "chat_manager.py"), encoding="utf-8").read()
check("the manager has both calls",
      "def reaction_choices" in manager and "def react" in manager)

print("\nReacting does not move the conversation")
check("it redraws rather than reloading the channel",
      "self._render_feed()" in source.split("def _toggle_reaction")[1][:900]
      and "_reload_channel" not in source.split("def _toggle_reaction")[1][:900],
      "a reload scrolls back to the newest message and loses the reader's place")


print("\nThe direct list survives a duplicate")
# SEEN FOR REAL. A direct channel briefly had three members, so the server
# offered the same conversation twice — once under each of the other two
# people. The list is keyed by channel_id, so one row overwrote the other in
# _rows, and picking either drew BOTH as selected, because selection is
# decided by comparing that same id.
#
# The membership was the fault. A list that cannot survive a duplicate is a
# second one, and this is the cheaper of the two to defend.
source = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "team_page.py"), encoding="utf-8").read()
check("duplicates are dropped rather than drawn twice",
      "seen_channels" in source and
      'if direct.get("channel_id") in seen_channels:' in source,
      "two rows for one channel both light up when either is picked")


print("\nThe typing indicator")
# WHAT IT MUST NOT DO. Follow you into another conversation; stay up after
# Send; grow into a wall of names on a busy channel; or send a request per
# keystroke, which on a fast typist is a hundred writes a minute for a
# message that does not exist yet.
from client.presentation.windows.team_page import TeamPage  # noqa: E402


class _Stub(TeamPage):
    def __init__(self):                      # no network, no window
        pass


page = _Stub()
shown = []


class _Label:
    def __init__(self):
        self.text_ = ""
        self.visible = False

    def setText(self, value):
        self.text_ = value

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


page._typing_label = _Label()

page._show_typing([{"name": "Raju Kumar"}])
check("one person reads as a sentence",
      page._typing_label.text_ == "Raju Kumar is typing…", page._typing_label.text_)
page._show_typing([{"name": "Raju Kumar"}, {"name": "Priya Menon"}])
check("two are named",
      page._typing_label.text_ == "Raju Kumar and Priya Menon are typing…",
      page._typing_label.text_)
page._show_typing([{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}])
check("more than two become a count, not a list",
      page._typing_label.text_ == "4 people are typing…",
      f"{page._typing_label.text_} — a list of names grows wider than the panel")
page._show_typing([])
check("nobody typing hides the line entirely",
      page._typing_label.visible is False and page._typing_label.text_ == "",
      "an empty line still takes height from the conversation")
page._show_typing([{"name": ""}, {"name": None}])
check("a blank name is not drawn as an empty sentence",
      page._typing_label.visible is False, page._typing_label.text_)

source = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "team_page.py"), encoding="utf-8").read()
check("keystrokes are throttled, not sent one per key",
      "_typing_ping_gap" in source and "monotonic()" in source,
      "one request per keystroke is a hundred writes a minute per person")
check("emptying the box says stopped",
      "ChatManager.stop_typing" in source.split("def _on_typed")[1][:900])
check("sending says stopped too",
      "stop_typing" in source.split("self._composer.clear()")[1][:400],
      "otherwise the dots stay up as though the next message is coming")
check("leaving the page says stopped",
      "stop_typing" in source.split("def hideEvent")[1][:1400])
check("switching channels clears the old one's indicator",
      "self._show_typing([])" in source.split("def open_channel")[1][:900],
      "'Priya is typing…' must not follow you into a channel Priya is not in")


print("\nThreads")
source = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "team_page.py"), encoding="utf-8").read()
check("a message with replies offers to open them",
      'repl{"y" if replies == 1 else "ies"}'.replace('"', "'") in source
      or "repl{'y' if replies == 1 else 'ies'}" in source)
check("and a message with none offers nothing",
      "if replies and self.seq:" in source,
      "a '0 replies' line under every message is a column of noise")

check("the thread replaces the member panel rather than adding a column",
      "self._side = QStackedWidget()" in source,
      "three panels leave the conversation itself about 400px wide")
check("the root seq is taken from the server's answer",
      'self._thread_root = int(root["seq"])' in source,
      "clicking a reply opens the same thread, so what is on screen is not "
      "necessarily what was clicked — replying against the clicked message "
      "would start a second thread beside the first")
check("switching channels closes the thread",
      "self.close_thread()" in source.split("def open_channel")[1][:1200],
      "otherwise somebody replies into a conversation they are no longer "
      "looking at")
check("the thread redraws from the server after sending",
      "_load_thread" in source.split("def _send_thread_reply")[1][:800],
      "one source for what the thread contains")

manager = open(os.path.join(ROOT, "client", "application", "managers",
                            "chat_manager.py"), encoding="utf-8").read()
check("the manager can fetch a thread", "def thread(cls, seq: int)" in manager)


print("\nThe unread badge, while the page is closed")
# REPORTED: "jab tak My Chat nahi khol raha, pata hi nahi chalta message aaya
# hai". Two faults, both in this file.
source = open(os.path.join(ROOT, "client", "presentation", "windows",
                           "team_page.py"), encoding="utf-8").read()
check("direct messages are counted, not only team channels",
      "def _emit_unread" in source and "getattr(self, \"_directs\", [])" in source,
      "a DM is the one kind always addressed to you personally, and it added "
      "nothing to the badge")
check("the count refreshes while the page is hidden",
      "self._teams_timer.start()" in source.split("def hideEvent")[1][:900],
      "the badge froze at whatever it was when the page was last closed")
check("more slowly, because nobody is reading it",
      "setInterval(60_000)" in source.split("def hideEvent")[1][:900])
check("and returns to the open-page cadence",
      "setInterval(30_000)" in source.split("def showEvent")[1][:500])
check("the open channel is not counted as unread against you",
      'd.get("channel_id") != open_id' in source,
      "reading a conversation must clear it, not leave the number up — but "
      "only while it is actually on screen")



check("the badge is answered even if the page is never opened",
      "QTimer.singleShot(1500, self.refresh)" in source,
      "a page that has never been shown never polled, so a console where "
      "nobody opened the chat showed no count all day")
check("the open conversation is excluded only while the page is visible",
      "looking = self.isVisible() and self._channel is not None" in source,
      "self._channel keeps its value after the page closes, so the one "
      "conversation somebody had been reading was excluded for the rest of "
      "the session — the count stayed at zero while messages arrived")
check("and the same rule applies to team channels",
      "reading = self.isVisible() and self._channel is not None" in source)

print("\nall chat reaction checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
