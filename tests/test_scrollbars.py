"""
Scrollbars that can be seen.

THE REPORT. "Scrollable screen me scrollbar ya visual indicator hona chahiye."
A page that scrolled gave no sign that it did, so whatever was below the fold —
the rest of a form, the last rows of a table — read as not being there.

WHAT WAS THERE. Every one of those screens HAD a scrollbar. Measured by drawing
all 107 of them in both panels: the handle stood between 1.00 and 1.24 to 1
against the track behind it. It was painted in the border colour — a wash of
six to ten percent white on near-black — and a border is designed not to be
noticed. At 1.00 the handle and the track were the same colour to the pixel.
And four to six pixels wide.

WHAT IT HAS TO BE. 3:1 against whatever it is drawn over — the WCAG minimum
for the part of a control a person has to see in order to use it (1.4.11,
non-text contrast) — in both themes and over every surface the panels paint.
And at least six pixels across.

HOW THIS LOOKS. By drawing, not by reading stylesheets. A stylesheet says what
somebody asked for; the pixels say what arrived after every ancestor's sheet
had its turn. For each scroll area in a real panel a probe scrollbar is made
INSIDE it, so exactly the same cascade reaches it, given something to scroll,
and painted over each background the theme has.

A PROBE, NOT THE AREA'S OWN BAR. The area owns its bars' range and puts it
back whenever it lays itself out. Measured on the real bar, a handle that is
plainly visible read as 1.00:1 — the range had been reset under the
measurement and the handle filled the whole track.

AND ON A MAC, BARS THAT DO NOT FADE AWAY. Under the macOS style an unstyled bar
is an overlay that disappears a moment after scrolling stops: the system's way,
and exactly "no indicator". Checked wherever the macOS style exists; anywhere
else the check says it was skipped rather than claiming a pass.

Run:  python3 tests/test_scrollbars.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolated from anything real before any client module is imported — the
# reason is written at the top of test_theme.py.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_scrollbar_test_")
import client.core.config as config                                          # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module       # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

from PySide6.QtCore import QPoint, Qt                                        # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QRegion                           # noqa: E402
from PySide6.QtWidgets import (                                              # noqa: E402
    QAbstractScrollArea, QApplication, QScrollBar, QStyle, QStyleFactory,
    QStyleOptionSlider, QWidget,
)

app = QApplication.instance() or QApplication([])

# THE STYLE A MAC ACTUALLY GETS, where there is one. It has to be in place
# before anything is built — the transient hint is decided by it.
MAC_STYLE = "macOS" in QStyleFactory.keys()
if MAC_STYLE:
    app.setStyle("macOS")

from client.infrastructure.database.database import Database                 # noqa: E402
from client.presentation import theme                                        # noqa: E402
from client.presentation.theme import ADMIN, C                               # noqa: E402
from client.application.managers.session_manager import SessionManager       # noqa: E402

failures = 0

MIN_CONTRAST = 3.0
MIN_ACROSS = 6
LENGTH = 300          # how long the probe is drawn
INSET = 12            # where along it the handle, and the far track, are read


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))


def _luminance(colour: QColor) -> float:
    def channel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(colour.red()) + 0.7152 * channel(colour.green())
            + 0.0722 * channel(colour.blue()))


def contrast(a: QColor, b: QColor) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def surfaces() -> list:
    """Every opaque background either panel paints, read from the palettes
    in force — not typed out here, so a palette change cannot leave it stale."""
    found = {C.BG, C.CARD, C.ELEVATED}
    found |= {v for k, v in ADMIN.items()
              if k.startswith("bg") and isinstance(v, str) and v.startswith("#")}
    return [QColor(v) for v in sorted(found)]


def where(widget: QWidget, root: QWidget) -> str:
    """The nearest classes of our own above a widget — enough to find it."""
    names, node = [], widget
    while node is not None and node is not root:
        name = type(node).__name__
        if not name.startswith("Q") and name not in names:
            names.append(name)
        node = node.parentWidget()
    return "/".join(reversed(names[:2])) or type(widget).__name__


def draw(area: QAbstractScrollArea, orientation, fill: QColor):
    """A probe bar inside `area`, with a handle, painted over `fill`."""
    probe = QScrollBar(orientation, area)
    try:
        probe.setRange(0, 1000)
        probe.setPageStep(100)
        probe.setValue(0)
        probe.ensurePolished()
        vertical = orientation == Qt.Orientation.Vertical
        thick = probe.sizeHint().width() if vertical else probe.sizeHint().height()
        probe.resize(thick, LENGTH) if vertical else probe.resize(LENGTH, thick)
        pixmap = QPixmap(probe.size())
        pixmap.fill(fill)
        probe.render(pixmap, QPoint(), QRegion(),
                     QWidget.RenderFlag.DrawChildren)
        return pixmap.toImage(), thick, vertical
    finally:
        probe.setParent(None)
        probe.deleteLater()


def measure(area: QAbstractScrollArea, orientation):
    """(worst contrast over every surface, handle width in pixels)."""
    worst, across = None, None
    for fill in surfaces():
        image, thick, vertical = draw(area, orientation, fill)
        mid = thick // 2
        at = (lambda along, cross: image.pixelColor(cross, along)) if vertical \
            else (lambda along, cross: image.pixelColor(along, cross))
        handle, track = at(INSET, mid), at(LENGTH - INSET, mid)
        ratio = contrast(handle, track)
        worst = ratio if worst is None else min(worst, ratio)
        # Across the handle, at the same point along it: every pixel that
        # stands out from the track is handle.
        width = sum(1 for cross in range(thick)
                    if contrast(at(INSET, cross), track) >= 1.5)
        across = width if across is None else min(across, width)
    return worst, across


def transient(bar: QScrollBar) -> bool:
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    return bool(bar.style().styleHint(
        QStyle.StyleHint.SH_ScrollBar_Transient, option, bar))


def audit(root: QWidget, label: str, at_least: int = 1):
    faint, thin, fading, count = [], [], [], 0
    lowest, narrowest = None, None
    areas = root.findChildren(QAbstractScrollArea)
    if isinstance(root, QAbstractScrollArea):
        areas.insert(0, root)          # findChildren never returns the root
    for area in areas:
        for orientation, policy, own in (
                (Qt.Orientation.Vertical, area.verticalScrollBarPolicy(),
                 area.verticalScrollBar()),
                (Qt.Orientation.Horizontal, area.horizontalScrollBarPolicy(),
                 area.horizontalScrollBar())):
            if policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
                continue
            count += 1
            name = f"{where(area, root)} {type(area).__name__} " \
                   f"{'V' if orientation == Qt.Orientation.Vertical else 'H'}"
            ratio, across = measure(area, orientation)
            lowest = ratio if lowest is None else min(lowest, ratio)
            narrowest = across if narrowest is None else min(narrowest, across)
            if ratio < MIN_CONTRAST:
                faint.append((ratio, name))
            if across < MIN_ACROSS:
                thin.append((across, name))
            if MAC_STYLE and transient(own):
                fading.append(name)

    check(f"{label}: its scroll areas were found ({count})", count >= at_least,
          f"expected at least {at_least} — a check over nothing passes")
    faint.sort()
    check(f"{label}: every handle stands {MIN_CONTRAST:.0f}:1 against what is behind it"
          + (f" (lowest {lowest:.2f}:1)" if lowest is not None else ""),
          not faint,
          f"{len(faint)} of {count}; worst "
          + ", ".join(f"{r:.2f}:1 {n}" for r, n in faint[:3]))
    thin.sort()
    check(f"{label}: and is at least {MIN_ACROSS}px across"
          + (f" (narrowest {narrowest}px)" if narrowest is not None else ""), not thin,
          f"{len(thin)} of {count}; thinnest "
          + ", ".join(f"{w}px {n}" for w, n in thin[:3]))
    if MAC_STYLE:
        check(f"{label}: and none fades away when scrolling stops", not fading,
              f"{len(fading)}: " + ", ".join(fading[:3]))
    else:
        print(f"  SKIP  {label}: fading bars — no macOS style on this machine")


def main():
    Database.initialize()

    # ── the one rule the rest use ────────────────────────────────────────
    print("\nThe shared rule, by itself")
    for mode in ("dark", "light"):
        theme.set_theme(mode)
        host = QAbstractScrollArea()
        host.setStyleSheet(theme.scrollbar())
        audit(host, f"scrollbar() in {mode}")
        host.deleteLater()

    SessionManager.employee_id = "E001"
    SessionManager.full_name = "Rajesh Kumar"
    SessionManager.role = "employee"
    SessionManager.auth_token = "not-a-real-token"

    # ── the employee panel, both themes ──────────────────────────────────
    print("\nThe employee panel")
    theme.set_theme("dark")
    from client.presentation.windows.employee_panel import EmployeePanel
    panel = EmployeePanel()
    audit(panel, "employee panel, dark", at_least=20)
    panel._toggle_theme()
    audit(panel, "employee panel, light", at_least=20)
    panel._toggle_theme()
    panel._teardown_pages()
    panel.deleteLater()

    # ── the admin console, both themes ───────────────────────────────────
    print("\nThe admin console")
    SessionManager.role = "super_admin"
    theme.set_theme("dark")
    from client.presentation.windows.admin_config_panel import AdminConfigPanel
    console = AdminConfigPanel()
    console._stop_background_services()
    audit(console, "admin console, dark", at_least=40)
    console._toggle_theme()
    audit(console, "admin console, light", at_least=40)
    console._toggle_theme()
    console.deleteLater()

    # ── the windows the tray opens ───────────────────────────────────────
    print("\nThe tray's windows")
    from client.presentation.windows.logs_window import LogsWindow
    from client.presentation.windows.settings_window import SettingsWindow
    for mode in ("dark", "light"):
        theme.set_theme(mode)
        for build in (LogsWindow, SettingsWindow):
            window = build()
            audit(window, f"{build.__name__}, {mode}")
            window.deleteLater()
    theme.set_theme("dark")
    app.processEvents()


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    failures += 1

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
