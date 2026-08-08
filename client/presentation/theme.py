"""
ETS design system — every colour, in both themes, from one place.

Previously each window wrote its own hex codes inline, so the app looked
different on every screen. That was consolidated into the palette below, and
this file now also carries a LIGHT palette and the switch between them.

HOW THE SWITCH WORKS, AND WHY IT WORKS THAT WAY

There are 294 `setStyleSheet(f"...{C.TEXT}...")` calls across the panels. Each
one bakes its colours in at the moment the widget is built, so rebinding the
palette does nothing to widgets that already exist. Two ways out:

  1. Turn all 294 into re-runnable builders and re-run them on a switch. That
     is a lot of mechanical edits, and the ones inside loops capture their
     loop variable — they would quietly re-apply the WRONG row's colour later.

  2. Change the palette, then build the pages again. Every widget is then
     constructed with the new colours, so there is no such thing as one that
     was missed.

The second is what happens. It costs a rebuild — a blink, and any half-typed
message in a composer — and in exchange the result cannot be partially themed,
which is exactly the failure that would be hardest to notice and most annoying
to chase.

Both panels share this module. The admin console's palette is a dict rather
than a class purely for historical reasons; it is defined here too so that one
switch moves both.
"""

from __future__ import annotations


# ── the palettes ───────────────────────────────────────────────────────────
#  Light is not the dark palette inverted. Text on a light background needs
#  more contrast from the accent colours, not less, so the accents are darker
#  here than their dark-theme counterparts — a #22c55e green that reads well
#  on near-black is nearly invisible on white.

_DARK = {
    "BG": "#0a0e1a", "SIDEBAR": "#0d1220", "CARD": "#111827",
    "CARD_HOVER": "#161f33", "ELEVATED": "#1a2337",
    "BORDER": "#1e2a42", "BORDER_SOFT": "#172033",
    "TEXT": "#e8edf7", "TEXT_MUTED": "#8b9bb4", "TEXT_DIM": "#5a6b85",
    "PRIMARY": "#3b82f6", "PRIMARY_DIM": "#1d4ed8",
    "GREEN": "#22c55e", "GREEN_BG": "#0d2a1a",
    "BLUE": "#38bdf8", "BLUE_BG": "#0c2537",
    "PURPLE": "#a78bfa", "PURPLE_BG": "#1e1b3a",
    "AMBER": "#f59e0b", "AMBER_BG": "#2a1f0a",
    "RED": "#ef4444", "RED_BG": "#2a1015",
    "CYAN": "#22d3ee", "CYAN_BG": "#0b2b33",
    "DANGER_BG": "#7f1d1d", "DANGER_BORDER": "#991b1b",
    "ON_ACCENT": "#ffffff",
    # What a selected row looks like. Kept as a PAIR so the background and
    # the text on it can never be chosen separately and end up the same
    # colour — which is exactly what happened: a light theme kept the dark
    # theme's white-on-accent text and put it on a pale background.
    "SELECTED_BG": "#1d4ed8", "SELECTED_TEXT": "#ffffff",
}

# Light is not "dark with the values flipped". Three things had to change
# after seeing it on a real screen:
#
#   * SIDEBAR and CARD were both pure white, so the sidebar and the content
#     it sits beside dissolved into one another — the only thing between them
#     was a hairline nobody notices. The sidebar is now a shade cooler than
#     the cards, which is what makes the layout read as a layout.
#   * The page behind the cards was too close to the cards themselves, so
#     white panels on near-white had no edge. It is a step darker now.
#   * The borders were too faint to do their job at all.
_LIGHT = {
    "BG": "#e7edf5", "SIDEBAR": "#f4f7fb", "CARD": "#ffffff",
    "CARD_HOVER": "#eaf0f8", "ELEVATED": "#e2e9f3",
    "BORDER": "#c8d4e4", "BORDER_SOFT": "#dde5ef",
    "TEXT": "#0f172a", "TEXT_MUTED": "#475569", "TEXT_DIM": "#7b8aa1",
    "PRIMARY": "#2563eb", "PRIMARY_DIM": "#1d4ed8",
    "GREEN": "#15803d", "GREEN_BG": "#dcfce7",
    "BLUE": "#0369a1", "BLUE_BG": "#e0f2fe",
    "PURPLE": "#6d28d9", "PURPLE_BG": "#ede9fe",
    "AMBER": "#b45309", "AMBER_BG": "#fef3c7",
    "RED": "#dc2626", "RED_BG": "#fee2e2",
    "CYAN": "#0e7490", "CYAN_BG": "#cffafe",
    "DANGER_BG": "#dc2626", "DANGER_BORDER": "#b91c1c",
    "ON_ACCENT": "#ffffff",
    "SELECTED_BG": "#2563eb", "SELECTED_TEXT": "#ffffff",
}

# The admin console's names for the same idea.
_ADMIN_DARK = {
    "bg_app": "#0a0e16", "bg_sidebar": "#0b0f1a", "bg_surface": "#111827",
    "bg_surface_alt": "#0e1626", "bg_elevated": "#18222f",
    "border": "#1e293b", "border_light": "#27344a",
    "text_primary": "#f1f5f9", "text_secondary": "#94a3b8", "text_muted": "#64748b",
    "accent": "#2563eb", "accent_hover": "#3b82f6", "accent_pressed": "#1d4ed8",
    "accent_soft": "rgba(37, 99, 235, 0.16)",
    "success": "#22c55e", "warning": "#f59e0b",
    "danger": "#ef4444", "danger_strong": "#dc2626",
    "danger_soft": "rgba(239, 68, 68, 0.14)",
    "warning_soft": "rgba(245, 158, 11, 0.14)",
    "selected_bg": "rgba(37, 99, 235, 0.16)", "selected_text": "#ffffff",
}

_ADMIN_LIGHT = {
    "bg_app": "#e7edf5", "bg_sidebar": "#f4f7fb", "bg_surface": "#ffffff",
    "bg_surface_alt": "#f3f7fb", "bg_elevated": "#e2e9f3",
    "border": "#c8d4e4", "border_light": "#aebdd2",
    "text_primary": "#0f172a", "text_secondary": "#475569", "text_muted": "#64748b",
    "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_pressed": "#1e40af",
    "accent_soft": "rgba(37, 99, 235, 0.12)",
    "success": "#15803d", "warning": "#b45309",
    "danger": "#dc2626", "danger_strong": "#b91c1c",
    "danger_soft": "rgba(220, 38, 38, 0.10)",
    "warning_soft": "rgba(180, 83, 9, 0.12)",
    # Solid, with white on it. A translucent wash was unreadable here: the
    # sidebar is already pale, so tinting it 12% blue and writing white on
    # top produced white-on-white.
    "selected_bg": "#2563eb", "selected_text": "#ffffff",
}

THEMES = ("dark", "light")
_THEME_KEY = "ui_theme"


class C:
    """Colour palette. Attributes are rebound by set_theme()."""


# The admin console reads this dict. It is MUTATED in place rather than
# replaced, because admin_config_panel binds `C = ADMIN` at import and
# admin_teams_tab imports that name from there — rebinding here would leave
# both of them pointing at the old dict.
ADMIN: dict = {}

_current = "dark"


def current_theme() -> str:
    return _current


def set_theme(name: str) -> str:
    """Point the palettes at `name`. Widgets already built are unaffected."""
    global _current
    if name not in THEMES:
        name = "dark"
    _current = name
    for key, value in (_DARK if name == "dark" else _LIGHT).items():
        setattr(C, key, value)
    ADMIN.clear()
    ADMIN.update(_ADMIN_DARK if name == "dark" else _ADMIN_LIGHT)
    return name


def load_saved_theme() -> str:
    """Whatever was chosen last time, defaulting to dark."""
    try:
        from client.services.settings_service import SettingsService
        saved = SettingsService.get_setting(_THEME_KEY, "dark")
    except Exception:
        saved = "dark"
    return set_theme(str(saved or "dark"))


def save_theme(name: str) -> str:
    applied = set_theme(name)
    try:
        from client.services.settings_service import SettingsService
        SettingsService.save_setting(_THEME_KEY, applied)
    except Exception:
        pass
    return applied


def toggle_theme() -> str:
    return save_theme("light" if _current == "dark" else "dark")


def is_light() -> bool:
    return _current == "light"


# Bind the defaults before anything imports C.
set_theme("dark")


R = 14          # card radius
R_SM = 10       # control radius


def scrollbar(bg: str | None = None) -> str:
    # NOT `bg: str = C.BG`. A default argument is evaluated once, when the
    # module is imported — so it would hold the dark background for the life
    # of the process and every scrollbar would stay dark in the light theme.
    bg = bg or C.BG
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
        "primary":   (C.PRIMARY_DIM, C.PRIMARY, C.ON_ACCENT, C.PRIMARY),
        "secondary": (C.ELEVATED, C.BORDER, C.TEXT, C.CARD_HOVER),
        "danger":    (C.DANGER_BG, C.DANGER_BORDER, C.ON_ACCENT, C.DANGER_BORDER),
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
