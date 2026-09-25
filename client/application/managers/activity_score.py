"""
How much of the last minute was work, and how much was a mouse jiggler.

THE QUESTION THIS ANSWERS. "Aisa na ho ki koi word open krke auto button
click ya mouse movement laga ke chala de." The idle tracker cannot answer it:
all it knows is how long ago the last input was, and a jiggler moving the
cursor once a second keeps that number at zero for ever. On the timesheet a
machine nobody is sitting at reads exactly like a machine somebody is working
at.

WHAT IS MEASURED, and why these and not others. Every signal here can be read
on Windows and macOS WITHOUT asking for another permission — the product
already has a Screen Recording prompt people struggle with, and a second one
for input monitoring would be the end of it.

    keystrokes            a jiggler produces none, ever
    clicks and scrolls    interacting with an application, not just moving
    mouse movements       the cheapest thing to fake, so the weakest signal
    window changes        nothing automated changes the foreground app
    the GAPS between inputs   the strongest signal of all: a human's are
                          ragged, an automation's are identical to the
                          millisecond

HOW THE NUMBERS WERE CHOSEN. They were not; they are the owner's, from the
brief, and they are kept where they can be read and changed rather than
buried in the arithmetic:

    Input activity             +10
    Keyboard interaction       +15
    Application interaction    +20
    Foreground-window change   +10
    Meaningful work event      +30
    Highly repetitive input    -25
    No meaningful events       -20
    Same input interval        -30

    70-100  Active      40-69  Low activity      0-39  Potentially idle

WHAT THIS IS NOT. It is not proof, and it must never be treated as proof. An
hour of reading a document scores low and so does an hour of a jiggler; the
difference between them is in the EVIDENCE, not the score — which is why
`evidence` is returned beside it and why the alert rule on the server asks
for the evidence rather than for a low number. A person's pay is never
touched by anything in this file.

And a determined automation — random intervals, random distances, the odd
keystroke — beats all of this. What it catches is the ordinary case: the
jiggler bought for ₹300 that moves the cursor two pixels every second.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# The owner's table, exactly as it was written.
POINTS = {
    "input": 10,
    "keyboard": 15,
    "application": 20,
    "window_change": 10,
    "meaningful": 30,
    "repetitive": -25,
    "no_meaningful": -20,
    "same_interval": -30,
}

#: The bands, also the owner's.
ACTIVE_FROM = 70
LOW_FROM = 40


def band(score: int) -> str:
    if score >= ACTIVE_FROM:
        return "ACTIVE"
    if score >= LOW_FROM:
        return "LOW"
    return "IDLE"


#: A minute with at least this many keystrokes is somebody writing something,
#: not somebody pressing a key to keep the screen awake.
MEANINGFUL_KEYSTROKES = 10
#: Or moving between applications while typing or clicking in them — the
#: shape of real work that involves no long prose at all.
MEANINGFUL_WINDOW_CHANGES = 3

#: Gaps this close to identical are not a person. Measured as the spread
#: between the shortest and longest gap, in milliseconds: a human pausing,
#: thinking and typing varies by hundreds of milliseconds at least, while a
#: timer fires on the same tick every time.
SAME_INTERVAL_SPREAD_MS = 120
#: Below this many samples there is nothing to judge — two gaps can look
#: identical by chance.
SAME_INTERVAL_MIN_GAPS = 6

#: Input that is all of one kind, over and over, with nothing else in the
#: minute: movement and nothing to show for it.
REPETITIVE_EVENTS = 20


@dataclass
class Minute:
    """What was observed in one minute. Counts, not opinions."""

    keystrokes: int = 0
    clicks: int = 0
    scrolls: int = 0
    mouse_moves: int = 0
    window_changes: int = 0
    #: Milliseconds between one input and the next, in the order they came.
    gaps_ms: list = field(default_factory=list)

    @property
    def inputs(self) -> int:
        return self.keystrokes + self.clicks + self.scrolls + self.mouse_moves


def _same_interval(gaps: list) -> bool:
    """Were the gaps between inputs identical enough to be a machine?"""
    usable = [g for g in gaps if g > 0]
    if len(usable) < SAME_INTERVAL_MIN_GAPS:
        return False
    return (max(usable) - min(usable)) <= SAME_INTERVAL_SPREAD_MS


def score_minute(minute: Minute) -> dict:
    """The minute's score, its band, and what the score is made of.

    The parts are returned as well as the total because an alert that says
    "score 12" tells an administrator nothing they can act on, while "no
    keystrokes at all, and 58 movements spaced identically" is a sentence
    they can take to the person.
    """
    reasons = []
    score = 0

    if minute.inputs > 0:
        score += POINTS["input"]
        reasons.append("input")
    if minute.keystrokes > 0:
        score += POINTS["keyboard"]
        reasons.append("keyboard")
    if minute.clicks > 0 or minute.scrolls > 0:
        score += POINTS["application"]
        reasons.append("application")
    if minute.window_changes > 0:
        score += POINTS["window_change"]
        reasons.append("window change")

    meaningful = (minute.keystrokes >= MEANINGFUL_KEYSTROKES
                  or (minute.window_changes >= MEANINGFUL_WINDOW_CHANGES
                      and (minute.keystrokes > 0 or minute.clicks > 0)))
    if meaningful:
        score += POINTS["meaningful"]
        reasons.append("meaningful work")

    # ── and what is missing ───────────────────────────────────────────────
    same_interval = _same_interval(minute.gaps_ms)
    if same_interval:
        score += POINTS["same_interval"]
        reasons.append("identical intervals")

    # ALL OF ONE KIND AND NOTHING TO SHOW FOR IT. Movement with no
    # keystroke, no click and no change of window is what a jiggler leaves
    # behind — and also what a person watching a video leaves behind, which
    # is why this lowers a score rather than accusing anybody.
    repetitive = (minute.mouse_moves >= REPETITIVE_EVENTS
                  and minute.keystrokes == 0
                  and minute.clicks == 0
                  and minute.window_changes == 0)
    if repetitive:
        score += POINTS["repetitive"]
        reasons.append("repetitive movement")

    if not meaningful and minute.keystrokes == 0 and minute.window_changes == 0:
        score += POINTS["no_meaningful"]
        reasons.append("nothing meaningful")

    score = max(0, min(100, score))
    return {
        "score": score,
        "band": band(score),
        "reasons": reasons,
        # The two that say "a machine did this", kept apart from the score so
        # the server can alert on evidence rather than on a low number.
        "automation_suspected": bool(same_interval or repetitive),
        "keystrokes": minute.keystrokes,
        "clicks": minute.clicks,
        "scrolls": minute.scrolls,
        "mouse_moves": minute.mouse_moves,
        "window_changes": minute.window_changes,
    }
