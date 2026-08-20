"""
Deciding what is worth interrupting somebody for.

The showing is three lines of Qt. The deciding is the part that makes this
either useful or the first thing everybody turns off, so it lives here on its
own, as plain functions over plain dictionaries, and is tested that way.

THE RULES, and why each one exists

  * Never for your own message. Obvious, and easy to get wrong once the same
    poll feeds both the panel and this.

  * Never for the conversation that is open in front of you while the window
    has focus. You are already reading it. A notification for a message you
    are watching arrive is the noise that teaches people to switch the whole
    feature off — and then they miss the one that mattered.

  * A DIRECT message always wins. Somebody wrote to you personally; that is
    not the same as a line going past in a busy channel.

  * BEING NAMED counts as personal. "@rajesh can you check this" in a channel
    of twenty is addressed to one person, and should reach them like it.

  * Everything else is announced quietly but is still announced, because the
    owner asked for group messages to be seen too.

Wording is deliberately in the shape people already know from every messaging
application they use: who it was from, then where, then what.
"""

from __future__ import annotations


#: How a notification should feel. The panel maps these to a sound and an
#: icon; keeping them named rather than boolean means a fourth kind can be
#: added without every caller learning about it.
URGENT = "urgent"       # a direct message, or being named
NORMAL = "normal"       # an ordinary message in a channel you are in
QUIET = "quiet"         # announcements and administrative alerts

MAX_PREVIEW = 120


def _preview(message: dict) -> str:
    """One line of what was said, or what came instead of words."""
    body = str(message.get("body") or "").strip()
    if message.get("deleted"):
        return "Deleted a message"
    if body:
        return body[:MAX_PREVIEW] + ("…" if len(body) > MAX_PREVIEW else "")
    if message.get("attachments"):
        count = len(message["attachments"])
        return "Sent a photo" if count == 1 else f"Sent {count} files"
    return "Sent a message"


def for_messages(messages, *, me, open_channel_id=None, window_active=False,
                 channel_names=None, direct_channel_ids=None):
    """What to show for a batch of newly arrived messages.

    Returns a list of {title, body, kind, channel_id} in the order they should
    appear. Nothing is shown for messages that fail the rules above.

    `channel_names` maps a channel id to what to call it — "General" for a
    channel, the person's name for a direct message. `direct_channel_ids` is
    the set that are one-to-one, which changes both the wording and the
    urgency.
    """
    names = channel_names or {}
    directs = set(direct_channel_ids or ())
    out = []

    for message in messages or []:
        if message.get("sender_id") == me:
            continue                                  # your own
        if message.get("deleted"):
            continue                                  # a withdrawal is not news
        channel_id = message.get("channel_id")

        # EVERY message is announced, including the conversation open in
        # front of you. That is the owner's decision, asked for in those
        # words — "khuli chat pe bhi notification aana chahiye" — after
        # messages arrived in an open chat with nothing to show for it.
        #
        # The rule used to be the opposite, on the reasoning that notifying
        # about a message you are watching arrive is noise. It is not worth
        # the doubt it creates: somebody who is told nothing cannot tell a
        # working notification from a broken one.
        #
        # `open_channel_id` and `window_active` are still taken, and still
        # passed by both panels, because the difference is real and the next
        # person to want it back should not have to re-thread it.

        who = str(message.get("sender_name") or "Somebody").strip()
        where = str(names.get(channel_id) or "").strip()
        is_direct = channel_id in directs
        named_me = bool(message.get("mentions_me"))

        if is_direct:
            title = f"{who} messaged you"
            kind = URGENT
        elif named_me:
            title = f"{who} mentioned you" + (f" in {where}" if where else "")
            kind = URGENT
        else:
            title = f"{who}" + (f" in {where}" if where else "")
            kind = NORMAL

        out.append({
            "title": title,
            "body": _preview(message),
            "kind": kind,
            "channel_id": channel_id,
        })
    return out


def for_alerts(alerts, *, role):
    """Announcements, and — for administrators — the things needing attention.

    An ordinary employee has no business being interrupted by "EM103's app has
    stopped reporting"; that is an administrator's problem and appears on their
    Alerts page. So the kind of alert somebody sees depends on what they are.
    """
    elevated = role in ("admin", "super_admin")
    out = []
    for alert in alerts or []:
        kind_of = str(alert.get("type") or "").upper()

        if kind_of == "ANNOUNCEMENT":
            where = " — ".join(
                p for p in (alert.get("team_name"), alert.get("channel_name")) if p)
            out.append({
                "title": f"Announcement{f' in {where}' if where else ''}",
                "body": "A new announcement was posted.",
                "kind": QUIET,
                "channel_id": alert.get("channel_id"),
            })
            continue

        if kind_of == "MENTION":
            # Already covered by for_messages when the message itself arrives.
            # Showing both would notify twice for one event.
            continue

        if not elevated:
            continue

        # Anything else is administrative: an app that has stopped reporting,
        # a shift nobody logged in for. Quiet, because none of it needs
        # answering in the next thirty seconds — but said, because the whole
        # point of the Alerts feature is that nobody has to think to look.
        out.append({
            "title": f"{alert.get('title') or 'Something needs attention'}",
            "body": str(alert.get("detail") or "Open Alerts to see more."),
            "kind": QUIET,
            "channel_id": None,
        })
    return out


def collapse(items, limit=3):
    """Three at most, then a count.

    A client that has been offline can come back to forty messages at once.
    Forty notifications is not forty times as useful as one; it is a wall
    somebody dismisses without reading, which is worse than silence.
    """
    items = list(items or [])
    if len(items) <= limit:
        return items
    head = items[:limit]
    rest = len(items) - limit
    head.append({
        "title": f"{rest} more message{'s' if rest != 1 else ''}",
        "body": "Open Amaze Connect to read them.",
        "kind": QUIET,
        "channel_id": None,
    })
    return head


# ─────────────────────────────────────────────── what this machine wants

# Preferences live in the client's own settings table, beside the theme:
# these are choices about THIS machine, and somebody signed in on two may
# reasonably want different answers on each. They are read here, where the
# deciding happens, so a page cannot set one and have nothing change.
PREF_DESKTOP = "notify_desktop"
PREF_SOUND = "notify_sound"
PREF_CHAT = "notify_chat"
PREF_ALERTS = "notify_alerts"


def pref_enabled(key: str, default: bool = True) -> bool:
    """One preference. Anything unset means ON.

    A fresh install that silently stopped notifying looks like a broken
    feature, and the first thing anybody does with a broken feature is stop
    trusting the ones beside it.
    """
    try:
        from client.services.settings_service import SettingsService
        raw = SettingsService.get_setting(key)
    except Exception:
        return default
    if raw is None or raw == "":
        return default
    return str(raw) == "1"


def deliver(tray, title, body):
    """Actually put it on the screen, on whichever desktop this is.

    Reported from a real machine: notifications appeared on Windows and never
    on macOS. Nothing was wrong with the deciding above — the delivery was.

    `QSystemTrayIcon.showMessage` is a request to the platform's notification
    service, and macOS grants it only to a signed, bundled application the
    user has allowed in Notification Center. Ours is neither, so the call
    returns cleanly and NOTHING APPEARS — no exception, no warning. The most
    expensive kind of failure, because everything looks like it worked.

    macOS has a second door that does not care about any of that:
    `display notification` through osascript. A subprocess per notification
    costs a few tens of milliseconds, which is irrelevant at the rate people
    receive messages, and `collapse` above already caps a burst at four.

    The tray is still asked on every platform — it is what works on Windows
    and Linux, and on macOS it is a no-op rather than a duplicate. The sound
    follows the same split: AppleScript plays its own, so beeping as well
    would be two sounds for one message.

    Returns True when something was shown.
    """
    import sys

    # Switched off on this machine — and switched off means nothing appears
    # and nothing sounds, not "appears without a sound".
    if not pref_enabled(PREF_DESKTOP):
        return False

    mac = sys.platform == "darwin"
    quiet = not pref_enabled(PREF_SOUND)
    shown = False

    # INSIDE A .app BUNDLE, MACOS CAN POST IT AS US — WITH OUR ICON.
    #
    # The osascript path below works everywhere, but macOS attributes that
    # notification to Script Editor, which is the application actually running
    # the AppleScript. So every notification this product sent carried Script
    # Editor's icon, and the picture beside "Raju Kumar — haa" was a generic
    # plug. Reported, correctly, as "logo nahi aa raha".
    #
    # There is no setting for that picture: macOS takes it from the POSTING
    # application's bundle. The only way to make it ours is to be the posting
    # application — which Qt can do once the app is a bundle with a bundle
    # identifier, both of which the packaged build has.
    #
    # RUNNING FROM SOURCE IT STILL CANNOT BE OURS. python3 -m client.main is
    # not a bundle; there is nothing to take an icon from. The fallback below
    # is kept for exactly that case rather than pretending otherwise.
    bundled = mac and getattr(sys, "frozen", False) and ".app/Contents/" in sys.executable

    if tray is not None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon, QApplication
            # The tray's own icon is the brand mark, so this is what carries
            # the logo on Windows — a balloon draws whatever the tray holds.
            #
            # getattr, NOT tray.icon(). Anything that quacks like a tray can
            # be passed in here, and one that has no icon() would raise —
            # into the except below, where it becomes a notification that
            # silently never appears. That is the exact failure this whole
            # module exists because of, and it was reintroduced by this line
            # until a test caught it.
            # A NULL ICON IS NOT AN ICON, AND WINDOWS TREATS IT THAT WAY.
            #
            # This asked whether tray.icon was callable and never whether what
            # it returned was usable. QIcon() with nothing in it is a perfectly
            # valid object that draws nothing — and Windows, handed one, drops
            # the notification without a word. Same silent failure as the
            # macOS one described above, on the other platform.
            badge = getattr(tray, "icon", None)
            badge = badge() if callable(badge) else None
            if badge is None or (hasattr(badge, "isNull") and badge.isNull()):
                badge = QSystemTrayIcon.MessageIcon.Information
            # macOS PAR TRAY KO KABHI "HO GAYA" NAHI MAANA JAATA.
            #
            # Pehle yahan `shown = (not mac) or bundled` tha — yaani ek
            # bundled .app ke liye maan liya jaata tha ki tray ne dikha diya,
            # aur neeche wala osascript raasta chalta hi nahi tha.
            #
            # Wo maan-lena galat hai. macOS showMessage sirf us app ko deta
            # hai jise Notification Center me IJAAZAT mili ho. Ek naya
            # install kiya hua, ad-hoc signed app wahan hai hi nahi — call
            # saaf lautti hai aur SCREEN PAR KUCH NAHI AATA.
            #
            # Isi wajah se notification source se chalane par aata tha aur
            # installed app me gayab ho gaya: dev me `bundled` False tha, to
            # osascript chalta tha; .app me True hua aur wahi band ho gaya.
            #
            # showMessage ka koi jawab nahi hota — "dikha ya nahi" jaanne ka
            # tareeka hi nahi. Isliye macOS par hamesha osascript, jise na
            # ijaazat se matlab hai na signature se.
            if mac:
                shown = False
            else:
                tray.showMessage(title, body, badge, 6000)
                shown = True
            if not mac and not quiet:
                # A SOUND ON EVERY NOTIFICATION, by the owner's decision —
                # group messages and administrative alerts included, not only
                # what is addressed to this person by name.
                QApplication.beep()
        except Exception:
            # A notification that cannot be shown must never take the panel
            # down with it — this runs off a poll, on every arrival.
            pass

    # ONLY WHEN QT COULD NOT. In a bundle the line above has already posted
    # it as Amaze Connect; doing both would show the same message twice, once
    # with our icon and once with Script Editor's.
    if mac and not shown:
        try:
            import subprocess
            script = (
                f"display notification {_applescript_string(body)} "
                f"with title {_applescript_string(title)} "
                + ("" if quiet else 'sound name "Ping"')
            )
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=10)
            shown = True
        except Exception:
            pass

    # A LINE IN THE LOG SAYING WHAT HAPPENED.
    #
    # "Notification nahi aaya" cannot be answered from the outside: the panel
    # decides, the delivery decides, and macOS decides, and none of them said
    # anything. Now the first two do. Verbose, so it costs nothing on a
    # normal machine and is there when somebody asks.
    try:
        from client.services.logger_service import LoggerService
        route = "qt" if (not mac or bundled) else "osascript"
        if shown:
            # The ordinary case, and noisy — one line per message.
            LoggerService.log_verbose(f"notifier: shown via {route} — {title!r}")
        else:
            # THE CASE SOMEBODY ASKS ABOUT. "Notification nahi aaya" cannot
            # be answered from outside the app: the panel decides, delivery
            # decides, and the operating system decides, and none of them
            # used to say anything. A failure is rare and worth a line at the
            # ordinary level, where it will actually be found.
            LoggerService.log(
                f"notifier: NOTHING SHOWN via {route} — {title!r}")
    except Exception:
        pass

    return shown


def _applescript_string(text):
    """A literal AppleScript string, safely."""
    text = str(text or "").replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text.replace("\n", " ") + '"'
