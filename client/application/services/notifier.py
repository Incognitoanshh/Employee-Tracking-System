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

        # The one you are looking at, with the window in front of you.
        # `window_active` is required: the same conversation left open behind
        # a browser is not being read.
        if window_active and channel_id == open_channel_id:
            continue

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
                "title": f"📢 Announcement{f' in {where}' if where else ''}",
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
            "title": f"⚠️ {alert.get('title') or 'Something needs attention'}",
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
