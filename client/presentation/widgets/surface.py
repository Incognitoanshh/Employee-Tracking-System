"""Make one widget transparent — and only that widget.

THIS EXISTS BECAUSE THE SAME MISTAKE HAS NOW BEEN MADE THREE TIMES.

    widget.setStyleSheet("background:transparent;")

A stylesheet with no selector reads like a statement about this widget. It is
not. Qt applies it to the widget AND to everything inside it, and an
ancestor's stylesheet outranks the application stylesheet REGARDLESS OF
SPECIFICITY — so a wrapper written this way silently overrules rules the app
sheet spent real care on.

The failures it produced, in order of discovery:

  * the Post button on announcement channels, invisible in a table cell —
    fixed locally in admin_teams_tab._cell_holder(), whose docstring named it
    "correct variant, correct rule, painted over by its own parent";
  * every card divider drawing as a rounded box — a different selector, the
    same shape of error, fixed in widgets/card.py;
  * thirteen console buttons rendering as empty outlines, "Generate draft"
    among them, because the payroll page's scroll host cleared its own
    background and took the buttons' fill with it.

Only the background is overruled, never the colour, so the text kept arriving
in white. On the dark theme that reads as a flat label. On the light theme the
card behind it is white and the control disappears outright.

Scoping the rule to a unique objectName costs one line and is impossible to
get wrong twice, which is why it belongs here rather than at ten call sites.
Covered by tests/test_button_variants.py, which renders the real page and
reads the pixels back.
"""
from __future__ import annotations

from itertools import count

from PySide6.QtWidgets import QWidget

_uid = count(1)


def clear_background(widget: QWidget, extra: str = "") -> QWidget:
    """Give `widget` a transparent background that reaches nothing inside it.

    `extra` carries any further declarations for the SAME widget — `border:
    none;` is the usual one — so a caller never has to hand-write the
    selector that is the whole point of this function.

    An objectName already on the widget is kept: the global stylesheet
    addresses some of these by name, and renaming one here would quietly
    unstyle it.
    """
    name = widget.objectName() or f"etsClear{next(_uid)}"
    widget.setObjectName(name)
    widget.setStyleSheet(f"#{name} {{ background:transparent;{extra} }}")
    return widget


def transparent_host() -> QWidget:
    """A plain wrapper widget that paints nothing and repaints nothing."""
    return clear_background(QWidget())
