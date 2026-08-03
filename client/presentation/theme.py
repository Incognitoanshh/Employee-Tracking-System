"""
ETS design system — ek jagah se saare colours/spacing/styles.

Pehle har window apna alag hex code inline likhta tha (dashboard dark,
settings light, logs ka apna style) — is liye app har screen pe alag dikhti
thi. Ab sab yahan se aata hai.
"""

from __future__ import annotations


class C:
    """Colour palette."""

    # Surfaces
    BG          = "#0a0e1a"      # app background
    SIDEBAR     = "#0d1220"
    CARD        = "#111827"
    CARD_HOVER  = "#161f33"
    ELEVATED    = "#1a2337"
    BORDER      = "#1e2a42"
    BORDER_SOFT = "#172033"

    # Text
    TEXT        = "#e8edf7"
    TEXT_MUTED  = "#8b9bb4"
    TEXT_DIM    = "#5a6b85"

    # Accents
    PRIMARY     = "#3b82f6"
    PRIMARY_DIM = "#1d4ed8"
    GREEN       = "#22c55e"
    GREEN_BG    = "#0d2a1a"
    BLUE        = "#38bdf8"
    BLUE_BG     = "#0c2537"
    PURPLE      = "#a78bfa"
    PURPLE_BG   = "#1e1b3a"
    AMBER       = "#f59e0b"
    AMBER_BG    = "#2a1f0a"
    RED         = "#ef4444"
    RED_BG      = "#2a1015"
    CYAN        = "#22d3ee"
    CYAN_BG     = "#0b2b33"


R = 14          # card radius
R_SM = 10       # control radius


def scrollbar(bg: str = C.BG) -> str:
    return f"""
        QScrollBar:vertical {{ background:{bg}; width:8px; border-radius:4px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:{C.BORDER}; border-radius:4px; min-height:30px; }}
        QScrollBar::handle:vertical:hover {{ background:{C.TEXT_DIM}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar:horizontal {{ background:{bg}; height:8px; border-radius:4px; margin:2px; }}
        QScrollBar::handle:horizontal {{ background:{C.BORDER}; border-radius:4px; min-width:30px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
    """


def table_style() -> str:
    return f"""
        QTableWidget {{
            background:{C.CARD}; border:1px solid {C.BORDER}; border-radius:{R}px;
            color:{C.TEXT}; gridline-color:transparent; outline:none;
            selection-background-color:{C.ELEVATED}; selection-color:{C.TEXT};
        }}
        QTableWidget::item {{ padding:10px 12px; border-bottom:1px solid {C.BORDER_SOFT}; }}
        QTableWidget::item:selected {{ background:{C.ELEVATED}; color:{C.TEXT}; }}
        QHeaderView::section {{
            background:{C.SIDEBAR}; color:{C.TEXT_MUTED}; border:none;
            border-bottom:1px solid {C.BORDER}; padding:12px;
            font-size:11px; font-weight:700; letter-spacing:0.6px;
        }}
        QTableCornerButton::section {{ background:{C.SIDEBAR}; border:none; }}
    """ + scrollbar(C.CARD)


def button(variant: str = "secondary") -> str:
    palette = {
        "primary":   (C.PRIMARY_DIM, C.PRIMARY, "#ffffff", C.PRIMARY),
        "secondary": (C.ELEVATED, C.BORDER, C.TEXT, C.CARD_HOVER),
        "danger":    ("#7f1d1d", "#991b1b", "#ffffff", "#991b1b"),
        "ghost":     ("transparent", C.BORDER, C.TEXT_MUTED, C.ELEVATED),
    }
    bg, border, fg, hover = palette.get(variant, palette["secondary"])
    return f"""
        QPushButton {{
            background:{bg}; border:1px solid {border}; border-radius:{R_SM}px;
            color:{fg}; font-size:13px; font-weight:600; padding:9px 16px;
        }}
        QPushButton:hover {{ background:{hover}; }}
        QPushButton:disabled {{ background:{C.CARD}; color:{C.TEXT_DIM};
                                border-color:{C.BORDER_SOFT}; }}
    """


def input_style() -> str:
    return f"""
        QLineEdit, QComboBox, QDateEdit {{
            background:{C.CARD}; border:1px solid {C.BORDER}; border-radius:{R_SM}px;
            color:{C.TEXT}; padding:9px 12px; font-size:13px;
        }}
        QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color:{C.PRIMARY}; }}
        QComboBox::drop-down {{ border:none; width:22px; }}
        QComboBox QAbstractItemView {{
            background:{C.ELEVATED}; color:{C.TEXT};
            selection-background-color:{C.PRIMARY_DIM}; border:1px solid {C.BORDER};
        }}
    """


def app_style() -> str:
    """Poori window pe lagne wala base stylesheet."""
    return f"""
        QWidget {{ background:{C.BG}; color:{C.TEXT};
                   font-family:-apple-system,'Segoe UI',Inter,Arial,sans-serif; }}
        QToolTip {{ background:{C.ELEVATED}; color:{C.TEXT};
                    border:1px solid {C.BORDER}; padding:6px; border-radius:6px; }}
    """ + input_style() + scrollbar()
