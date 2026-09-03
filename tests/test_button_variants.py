"""
A button styled by a dynamic property must still DRAW ITS FILL on the page
it actually lives on — not only when it is built on its own.

WHY THIS EXISTS. Thirteen buttons in the console rendered as empty outlines:
"Generate draft", "Set salary" and the rest. Every obvious explanation was
checked and cleared — the rule was right, the property was set, the button was
enabled, and the same button built in isolation came out accent blue. The
cause was one line nowhere near any of them:

    host.setStyleSheet("background:transparent;")

A stylesheet with NO selector is not a rule about that widget. Qt applies it to
the widget AND everything inside it, and — this is the part that makes it so
hard to find — a rule from an ancestor's stylesheet beats the application
stylesheet REGARDLESS OF SPECIFICITY. So `background:transparent` from the
scroll host silently outranked `QPushButton[variant="primary"]`, while `color`
kept coming from the primary rule because the host said nothing about colour.

White text, no fill. On the dark theme that reads as a flat label; on the light
one the card behind it is white, so the button disappeared completely.

This is the third time this exact bug has been found in this codebase —
widgets/card.py and admin_teams_tab._cell_holder() both carry a docstring
about it. Counting comments did not stop it coming back, so this test measures
pixels instead.

HOW IT MEASURES. Grabbing a widget that paints nothing gives an uninitialised
buffer, and "black" cannot be told apart from "transparent". So each button is
rendered ONTO A LOUD MARKER COLOUR instead. If the marker survives where the
fill should be, the button drew no background at all — which is the bug,
stated as a pixel rather than as an opinion.

Run:  python3 tests/test_button_variants.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))


from PySide6.QtCore import QPoint                                  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter                 # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton            # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation import theme as _theme                    # noqa: E402
from client.presentation.windows import admin_config_panel as panel  # noqa: E402

MARKER = "#ff00ff"


def fill_of(button: QPushButton) -> str:
    """The colour the button actually paints, over a marker it does not use.

    Sampled four pixels below the top edge and on the horizontal centre: far
    enough in to clear the one-pixel border and the rounded corners, and well
    above the text baseline, so the reading is the fill and nothing else.
    """
    size = button.size()
    image = QImage(size, QImage.Format.Format_ARGB32)
    image.fill(QColor(MARKER))
    painter = QPainter(image)
    button.render(painter, QPoint(0, 0))
    painter.end()
    return image.pixelColor(size.width() // 2, 4).name()


def payroll_tab():
    """The real page, built without the fetch it fires on construction.

    Same approach as tests/test_payroll_tab.py: what is under test here is
    what the page RENDERS, not what it loads.
    """
    tab = panel._PayrollTab.__new__(panel._PayrollTab)
    panel.QWidget.__init__(tab)
    tab._workers = []
    tab._month = "2026-06"
    tab._lines = []
    tab._status = "NONE"
    tab._selected_employee = None
    tab._build_ui()
    return tab


print("\nButtons that draw their fill\n")

for theme_name in ("light", "dark"):
    _theme.set_theme(theme_name)
    app.setStyleSheet(panel._global_stylesheet())
    accent = panel.C["accent"]

    tab = payroll_tab()
    tab.resize(1400, 900)
    app.processEvents()

    buttons = [b for b in tab.findChildren(QPushButton)
               if b.property("variant") is not None]
    check(f"[{theme_name}] the payroll page has variant-styled buttons",
          bool(buttons), f"found {len(buttons)}")

    blank = []
    for button in buttons:
        if fill_of(button) == MARKER:
            blank.append(f"{button.text()!r}({button.property('variant')})")
    check(f"[{theme_name}] every variant button paints a background",
          not blank, f"{len(blank)} drew nothing: {', '.join(blank[:6])}")

    primary = [b for b in buttons if b.property("variant") == "primary"]
    wrong = [f"{b.text()!r}={fill_of(b)}" for b in primary
             if fill_of(b).lower() != accent.lower()]
    check(f"[{theme_name}] primary buttons carry the accent fill {accent}",
          not wrong, ", ".join(wrong[:6]))

    tab.deleteLater()
    app.processEvents()

# ── a disabled button has to LOOK disabled ──────────────────────────────
#
# REPORTED AS: "Payroll → Finalize does nothing when clicked."
#
# It did nothing because it was disabled, and it was disabled for the right
# reasons — Finalize is off until the month in the box has a draft, and off
# once that draft is finalised. What was wrong is that none of that was
# visible. `QPushButton:disabled` and `QPushButton[variant="danger"]` have the
# SAME specificity in Qt's CSS, so the one written later wins, and every
# variant is written after the disabled rule. Only `primary` had a disabled
# treatment of its own.
#
# So a disabled Finalize rendered pixel for pixel like a live one: same soft
# red fill, same red label, no message on click because nothing had gone
# wrong. This is the check that would have caught it, and it is a rendering
# question, so it is answered in pixels.
print("\nA disabled button looks disabled\n")

CARD_KEY = "bg_surface"


def rendered(button: QPushButton, base: str):
    """The fill and the text row, over the colour actually behind the button.

    NOT over a marker colour. The soft variants are translucent, so a marker
    shows THROUGH them and enabled and disabled both come back as a blend of
    it — a measurement that cannot tell the two apart, which is the very thing
    being measured. The card is what is really behind these buttons.
    """
    image = QImage(button.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(base))
    painter = QPainter(image)
    button.render(painter, QPoint(0, 0))
    painter.end()
    return (image.pixelColor(button.width() // 2, 4).name(),
            image.pixelColor(button.width() // 2, button.height() // 2).name())


for theme_name in ("light", "dark"):
    _theme.set_theme(theme_name)
    app.setStyleSheet(panel._global_stylesheet())
    card = panel.C[CARD_KEY]

    same = []
    for variant in ("primary", "secondary", "danger", "danger-solid",
                    "ghost", "warning"):
        host = panel.QWidget()
        column = panel.QVBoxLayout(host)
        live = panel._btn("Finalize", variant=variant, height=36, width=110)
        dead = panel._btn("Finalize", variant=variant, height=36, width=110)
        dead.setEnabled(False)
        column.addWidget(live)
        column.addWidget(dead)
        host.resize(180, 120)
        host.show()
        app.processEvents()
        if rendered(live, card) == rendered(dead, card):
            same.append(variant)
        host.deleteLater()
    app.processEvents()

    check(f"[{theme_name}] every variant renders differently when disabled",
          not same, f"identical when disabled: {', '.join(same)}")

# ── the shape of the mistake, not just this instance of it ──────────────
#
# A RATCHET, in the spirit of tests/test_design_system.py. The pixel checks
# above only cover the payroll page; this covers the file. A selectorless
# stylesheet is never what anyone means — the intent is always "this widget" —
# so the honest form is scoped, and an unscoped one is a bug waiting for a
# page to be built on top of it.
SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "client", "presentation", "windows", "admin_config_panel.py")
text = open(SOURCE, encoding="utf-8").read()

# setStyleSheet("...") whose argument opens with a bare property rather than a
# selector — i.e. there is no '{' introducing a block.
unscoped = re.findall(r'setStyleSheet\(\s*f?"([^"{}]*:[^"{}]*)"\s*\)', text)
check("admin_config_panel sets no selectorless stylesheet",
      not unscoped, f"{len(unscoped)} found: {unscoped[:4]}")

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record: a real
# console starts threads Qt tears down during interpreter shutdown, aborting a
# fully passing run with signal 6.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
