"""
One card, built the way the card has to be built.

THIS EXISTS BECAUSE TWO OF THE THREE COPIES CARRIED A BUG THE THIRD ONE
DOCUMENTS.

A plain `QFrame{...}` selector in Qt does not style only that frame. It styles
every QFrame INSIDE it as well — so a card's border and 14px radius were
inherited by every divider, every separator and every nested frame in it. A
one-pixel divider became a glowing rounded box. It was reported as "white
white lines" on a screenshot, fixed once, in one of the three builders, and
the other two went on doing it: the admin dashboard's own cards and every
card on the employee panel.

The fix is to scope the rule to a unique objectName. It is easy to get wrong
and impossible to notice in a diff, which is exactly the kind of thing that
belongs in one place.
"""
from __future__ import annotations

from itertools import count

from PySide6.QtWidgets import QFrame

from client.presentation.theme import Radius

_uid = count(1)


def card(*, bg: str, border: str, radius: int = Radius.CARD,
         hover_border: str | None = None) -> QFrame:
    """A surface card whose style reaches nothing inside it.

    Colours are passed in rather than read here: the two panels have their own
    palettes for historical reasons, and hard-coding one of them would quietly
    restyle half the product.
    """
    frame = QFrame()
    name = f"etsCard{next(_uid)}"
    frame.setObjectName(name)
    style = (f"QFrame#{name} {{ background:{bg}; border:1px solid {border};"
             f" border-radius:{radius}px; }}")
    if hover_border:
        style += f"QFrame#{name}:hover {{ border-color:{hover_border}; }}"
    frame.setStyleSheet(style)
    return frame
