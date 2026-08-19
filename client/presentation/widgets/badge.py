"""
A status chip, and the cell wrapper that puts one in a table.

WHY THIS IS SHARED RATHER THAN COPIED. Three pages had their own dictionary
of status colours — attendance in the admin panel, leave in its own page,
payroll in a third — so the same green meant "on time" here, "approved"
there and "finalised" somewhere else, and a status added to one was missing
from the others. The colours now come from theme.status_colors, which is the
only place they exist.

WHY A WIDGET RATHER THAN A COLOURED STRING. A QTableWidgetItem cannot carry a
stylesheet, so the best those pages could do was tint the text. A column of
twelve differently coloured words has no shape to it; a chip gives the eye an
edge to find first and a colour to read second.

The one cost is that a cell widget is not a cell item: it does not take part
in the table's own sorting or its selection colours. Both are acceptable here
— these tables are sorted by the server, and the chip carries its own
background by design.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from client.presentation import theme as _theme


def badge_label(status: str, text: str | None = None) -> QLabel:
    """The chip on its own, for use outside a table."""
    chip = QLabel(text if text is not None else str(status).title())
    chip.setStyleSheet(_theme.badge(status))
    chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return chip


def badge_cell(status: str, text: str | None = None,
               tooltip: str | None = None) -> QWidget:
    """A chip wrapped for setCellWidget, left-aligned like the text beside it."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    # The left inset matches a table item's own padding, so a chip column
    # lines up with the plain columns instead of starting a few pixels off.
    # 8px, not 16. The wider inset pushed the chip right until the column
    # clipped its own text — "Completed" was drawn as "omplete".
    layout.setContentsMargins(_theme.Space.XS, 0, _theme.Space.HAIR, 0)
    layout.setSpacing(0)

    chip = badge_label(status, text)
    if tooltip:
        # Wrapped: a status explanation is a sentence or two, and a tooltip
        # does not wrap on its own — see theme.tip.
        wrapped = _theme.tip(tooltip)
        chip.setToolTip(wrapped)
        holder.setToolTip(wrapped)

    layout.addWidget(chip)
    layout.addStretch(1)
    return holder
