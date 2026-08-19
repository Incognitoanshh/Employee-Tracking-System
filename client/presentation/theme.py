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

# TEXT_DIM is measured, not chosen by eye. It carries timestamps, hints and
# "idle for 23 minutes" — the small print somebody actually has to read. The
# old values sat at 2.87:1 to 3.55:1 against the surfaces they are drawn on,
# under the 4.5:1 that ordinary body text needs to be legible. These clear it
# on every surface in their own palette. If you change them, measure again.
_DARK = {
    # The palette the design brief specifies, to the value.
    #
    # THE BACKGROUND IS NEARLY BLACK AND THE CARDS ARE NOT. #09090B behind
    # #18181B is what gives a card an edge without drawing one — the old
    # palette put #0a0e1a behind #111827, two blues four steps apart, and
    # every surface needed a visible border to be found at all. Borders are
    # a 6% white wash now, which is a hairline you feel rather than a line
    # you see.
    "BG": "#09090b", "SIDEBAR": "#111827", "CARD": "#18181b",
    "CARD_HOVER": "#1f1f23", "ELEVATED": "#212127",
    "BORDER": "rgba(255,255,255,0.06)", "BORDER_SOFT": "rgba(255,255,255,0.04)",
    "TEXT": "#f8fafc", "TEXT_MUTED": "#94a3b8", "TEXT_DIM": "#64748b",
    "PRIMARY": "#3b82f6", "PRIMARY_DIM": "#2563eb",
    "GREEN": "#22c55e", "GREEN_BG": "rgba(34,197,94,0.12)",
    "BLUE": "#3b82f6", "BLUE_BG": "rgba(59,130,246,0.12)",
    "PURPLE": "#a78bfa", "PURPLE_BG": "rgba(167,139,250,0.12)",
    "AMBER": "#f59e0b", "AMBER_BG": "rgba(245,158,11,0.12)",
    "RED": "#ef4444", "RED_BG": "rgba(239,68,68,0.12)",
    "CYAN": "#22d3ee", "CYAN_BG": "rgba(34,211,238,0.12)",
    # Danger buttons are a SOFT red, per the brief: a bright red button is
    # read as an error message rather than as a control.
    "DANGER_BG": "rgba(239,68,68,0.12)", "DANGER_BORDER": "rgba(239,68,68,0.32)",
    "ON_ACCENT": "#ffffff",
    "HOVER": "rgba(255,255,255,0.05)",
    "ACTIVE": "rgba(59,130,246,0.15)",
    # What a selected row looks like. Kept as a PAIR so the background and
    # the text on it can never be chosen separately and end up the same
    # colour — which is exactly what happened: a light theme kept the dark
    # theme's white-on-accent text and put it on a pale background.
    "SELECTED_BG": "rgba(59,130,246,0.15)", "SELECTED_TEXT": "#f8fafc",
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
    "BG": "#f4f5f7", "SIDEBAR": "#ffffff", "CARD": "#ffffff",
    "CARD_HOVER": "#f7f8fa", "ELEVATED": "#eef0f4",
    "BORDER": "rgba(9,9,11,0.08)", "BORDER_SOFT": "rgba(9,9,11,0.05)",
    "TEXT": "#09090b", "TEXT_MUTED": "#52525b", "TEXT_DIM": "#71717a",
    "PRIMARY": "#2563eb", "PRIMARY_DIM": "#1d4ed8",
    "GREEN": "#15803d", "GREEN_BG": "rgba(34,197,94,0.14)",
    "BLUE": "#1d4ed8", "BLUE_BG": "rgba(59,130,246,0.14)",
    "PURPLE": "#6d28d9", "PURPLE_BG": "rgba(167,139,250,0.18)",
    "AMBER": "#b45309", "AMBER_BG": "rgba(245,158,11,0.18)",
    "RED": "#dc2626", "RED_BG": "rgba(239,68,68,0.14)",
    "CYAN": "#0e7490", "CYAN_BG": "rgba(34,211,238,0.18)",
    "DANGER_BG": "rgba(220,38,38,0.10)", "DANGER_BORDER": "rgba(220,38,38,0.30)",
    "ON_ACCENT": "#ffffff",
    "HOVER": "rgba(9,9,11,0.04)",
    "ACTIVE": "rgba(37,99,235,0.12)",
    "SELECTED_BG": "rgba(37,99,235,0.12)", "SELECTED_TEXT": "#09090b",
}

# The admin console's names for the same idea.
_ADMIN_DARK = {
    # The same values as _DARK, under the console's own names. The two
    # vocabularies are historical; keeping the VALUES identical is what stops
    # the two halves of the product looking like two products.
    "bg_app": "#09090b", "bg_sidebar": "#111827", "bg_surface": "#18181b",
    "bg_surface_alt": "#141417", "bg_elevated": "#212127",
    "border": "rgba(255,255,255,0.06)", "border_light": "rgba(255,255,255,0.10)",
    "text_primary": "#f8fafc", "text_secondary": "#94a3b8", "text_muted": "#64748b",
    "accent": "#3b82f6", "accent_hover": "#60a5fa", "accent_pressed": "#2563eb",
    "accent_soft": "rgba(59,130,246,0.15)",
    "success": "#22c55e", "warning": "#f59e0b",
    "danger": "#ef4444", "danger_strong": "#dc2626",
    "danger_soft": "rgba(239,68,68,0.12)",
    "warning_soft": "rgba(245,158,11,0.12)",
    "hover": "rgba(255,255,255,0.05)",
    "selected_bg": "rgba(59,130,246,0.15)", "selected_text": "#f8fafc",
}

_ADMIN_LIGHT = {
    "bg_app": "#f4f5f7", "bg_sidebar": "#ffffff", "bg_surface": "#ffffff",
    "bg_surface_alt": "#fafafa", "bg_elevated": "#eef0f4",
    "border": "rgba(9,9,11,0.08)", "border_light": "rgba(9,9,11,0.14)",
    "text_primary": "#09090b", "text_secondary": "#52525b", "text_muted": "#71717a",
    "accent": "#2563eb", "accent_hover": "#1d4ed8", "accent_pressed": "#1e40af",
    "accent_soft": "rgba(37,99,235,0.12)",
    "success": "#15803d", "warning": "#b45309",
    "danger": "#dc2626", "danger_strong": "#b91c1c",
    "danger_soft": "rgba(220,38,38,0.10)",
    "warning_soft": "rgba(180,83,9,0.12)",
    "hover": "rgba(9,9,11,0.04)",
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
    apply_tooltip_style()
    return name


def tip(text: str, width: int = 320) -> str:
    """Tooltip text that WRAPS instead of running off the screen.

    A plain-text tooltip is one line however long it is: the Payroll nav
    item's sentence came out 953 pixels wide — measured — a band of text
    lying across the whole window. Qt only word-wraps a tooltip when the
    text is rich text, and of the ways to cap the width only a table is
    honoured: `max-width` and a `width` attribute on a div are both ignored,
    which is why this is a table and not the obvious thing.

    Short text is left alone — it needs no wrapping and rich text would only
    make the mark-up characters in it (`<`, `&`) a hazard.
    """
    words = str(text or "")
    if len(words) < 60:
        return words
    escaped = (words.replace("&", "&amp;")
                    .replace("<", "&lt;").replace(">", "&gt;"))
    return f"<table width='{width}'><tr><td>{escaped}</td></tr></table>"


def apply_tooltip_style() -> None:
    """Tooltips, on the APPLICATION rather than on a window.

    A tooltip is its own top-level window. A stylesheet set on the panel
    therefore does not reach it, so both panels styled a QToolTip that was
    never the one being drawn — and on the light theme the tip came up as a
    dark grey box with pale text over a white page, which is what was
    reported. Setting it on the application is the only place it lands.

    Only the tooltip rule goes here. An application stylesheet is the weakest
    in Qt's order, so this cannot overrule anything a widget sets for itself,
    and keeping it to one rule means it stays that way.
    """
    # CALLED FROM set_theme, WHICH RUNS ONCE WHILE THIS MODULE IS STILL
    # BEING READ — at that point Radius and Type below do not exist yet, and
    # neither does any application. Both are the same "too early" case, so
    # both are simply nothing to do.
    #
    # AND THAT IS WHY main.py CALLS THIS AGAIN. The saved theme is loaded
    # BEFORE QApplication exists, so on a machine whose theme never changes
    # during the session this ran exactly once, with no application to style
    # — and the tooltip fell through to the platform's own. On a Mac in dark
    # mode under a light-themed app that is a black box with dark text, which
    # is how it was reported.
    if "Radius" not in globals() or "Type" not in globals():
        return
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(
        f"QToolTip {{ background:{C.ELEVATED}; color:{C.TEXT};"
        f"border:1px solid {C.BORDER}; padding:6px 10px;"
        f"border-radius:{Radius.CHIP}px; font-size:{Type.SMALL}px; }}")


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


# ── the scales ─────────────────────────────────────────────────────────────
#
# MEASURED BEFORE THEY WERE CHOSEN. The panels between them used nineteen
# different font sizes, fourteen border radii and fourteen paddings — every
# one picked by hand at the moment it was written. Nothing was wrong with any
# single value; the effect of all of them together is a product that looks
# like a collection of pages rather than one application.
#
# These are the values everything is allowed to use. They are deliberately
# few: a scale with a step for every occasion is the same problem with extra
# steps. When something does not fit, the answer is usually the nearest step
# rather than a new one.

class Type:
    """Font sizes — the eight the design brief specifies.

    The floor moved from 11 to 12. Eleven-pixel text was carrying column
    headings and timestamps, which is the small print somebody actually has
    to read, and it was the single thing that most made the product look
    like an old admin panel rather than something made this decade.
    """
    MICRO   = 12    # column headings, chips, timestamps
    SMALL   = 13    # secondary text, hints
    BODY    = 14    # the default — buttons, inputs, table cells
    SECTION = 16    # a heading over a group of things
    LARGE   = 18    # a card's own title
    TITLE   = 20    # the name of the page
    HEADING = 24    # a page that is mostly one thing
    DISPLAY = 32    # the single number on a stat card


class Weight:
    NORMAL = 400
    MEDIUM = 500
    SEMIBOLD = 600
    BOLD = 700


class Space:
    """An 8px system: 8, 12, 16, 20, 24, 32, 40.

    Four survives only as a hairline gap inside a control. Everything that
    separates one thing from another starts at eight — the cramped feel of
    the old panels came from 4px and 6px gaps doing structural work.
    """
    HAIR = 4
    XS   = 8
    SM   = 12
    MD   = 16
    LG   = 20
    XL   = 24
    XXL  = 32
    HUGE = 40
    PAGE = 32      # the margin around a page's content


class Radius:
    """Two radii, and a pill. The brief allows 12 and 16; anything else
    reads as a different product on the same screen."""
    CONTROL = 12   # buttons, inputs, menu rows
    CARD     = 16  # cards, tables, dialogs
    CHIP     = 12  # kept as a name; a chip is a pill unless it must not be
    PILL     = 999 # fully rounded; Qt clamps it to half the height


# ── status colours ─────────────────────────────────────────────────────────
#
# ONE MAPPING FOR THE WHOLE PRODUCT. Attendance, Leave and Payroll each chose
# their own colours for the same ideas, so a green on one page and a green on
# another meant different things, and "pending" was amber in one place and
# grey in another. Adding a status means adding it here, which is the point:
# it cannot be added anywhere else.
#
# Keys are the words the server sends, so a page maps its value straight
# through instead of translating first.

def _status_table() -> dict:
    return {
        # Attendance — the record
        "active":       (C.GREEN,  C.GREEN_BG),
        "completed":    (C.TEXT_MUTED, C.ELEVATED),
        "incomplete":   (C.AMBER,  C.AMBER_BG),
        # Attendance — the shift
        "on_time":      (C.GREEN,  C.GREEN_BG),
        "late":         (C.RED,    C.RED_BG),
        "early_exit":   (C.AMBER,  C.AMBER_BG),
        "overtime":     (C.BLUE,   C.BLUE_BG),
        "outside_shift": (C.AMBER, C.AMBER_BG),
        "day_off":      (C.TEXT_MUTED, C.ELEVATED),
        "extra":        (C.TEXT_MUTED, C.ELEVATED),
        "unknown":      (C.TEXT_MUTED, C.ELEVATED),
        # Leave
        "half_day":     (C.PURPLE, C.PURPLE_BG),
        "on_leave":     (C.PURPLE, C.PURPLE_BG),
        "pending":      (C.AMBER,  C.AMBER_BG),
        "approved":     (C.GREEN,  C.GREEN_BG),
        "rejected":     (C.RED,    C.RED_BG),
        "cancelled":    (C.TEXT_MUTED, C.ELEVATED),
        "revoked":      (C.RED,    C.RED_BG),
        # Payroll
        "draft":        (C.TEXT_MUTED, C.ELEVATED),
        "review":       (C.AMBER,  C.AMBER_BG),
        "finalized":    (C.GREEN,  C.GREEN_BG),
        # Generic
        "absent":       (C.RED,    C.RED_BG),
        "present":      (C.GREEN,  C.GREEN_BG),
        "info":         (C.BLUE,   C.BLUE_BG),
        "neutral":      (C.TEXT_MUTED, C.ELEVATED),
    }


def status_colors(key: str) -> tuple[str, str]:
    """(foreground, background) for a status. Unknown keys read as neutral.

    Built on each call rather than once at import: the palette is rebound by
    set_theme, and a table captured at import time would keep the dark
    theme's colours for the life of the process — the same trap the scrollbar
    default argument documents above.
    """
    table = _status_table()
    return table.get(str(key or "").lower(), table["neutral"])


def status_fg(key: str) -> str:
    return status_colors(key)[0]


def badge(key: str = "neutral", *, pill: bool = True) -> str:
    """A status chip: coloured text on its own tinted background.

    Coloured TEXT ALONE was what these pages did, and on a dense table it
    reads as noise — twelve differently coloured words in a column with no
    shape to them. A chip has an edge, so the eye finds the column first and
    the colour second.
    """
    fg, bg = status_colors(key)
    radius = Radius.PILL if pill else Radius.CHIP
    return (f"background:{bg};color:{fg};border:none;"
            f"border-radius:{radius}px;padding:3px 10px;"
            f"font-size:{Type.MICRO}px;font-weight:{Weight.SEMIBOLD};")


def card_style(*, hover: bool = False) -> str:
    """The one card. Every panel had its own, differing by a pixel or two."""
    style = (f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};"
             f"border-radius:{Radius.CARD}px;}}")
    if hover:
        style += f"QFrame:hover{{border-color:{C.PRIMARY};}}"
    return style


def modal_style() -> str:
    """Dialogs. The background must not be the page background, or a modal
    over a page looks like part of it."""
    return (f"QDialog{{background:{C.BG};}}"
            f"QDialog QLabel{{background:transparent;}}") + input_style()


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


def dot_style(size: int, colour: str) -> str:
    """A status dot: a round patch of colour, at whatever size is asked for.

    HERE RATHER THAN INLINE IN FOUR FILES. A dot's corner radius is not a
    design decision to be taken from the scale — it is half the width, every
    time, or the circle is an egg. Writing that as a literal in each place
    both invited drift and tripped the off-scale radius ratchet, which cannot
    tell geometry from a guess. One function answers both.
    """
    return f"background:{colour};border-radius:{size / 2}px;border:none;"


def _tick_file() -> str:
    """The white tick, as a file — QSS cannot read a data: URI."""
    from client.presentation.widgets import icons as _icons
    return _icons.icon_file("check", 11, "#ffffff")


def table_style() -> str:
    return f"""
        QTableWidget {{
            background:{C.CARD}; border:1px solid {C.BORDER}; border-radius:{R}px;
            color:{C.TEXT}; gridline-color:transparent; outline:none;
            selection-background-color:{C.ELEVATED}; selection-color:{C.TEXT};
        }}
        /* 4px TOP AND BOTTOM, NOT 10 — the same lesson the admin console
           already learned and this half of the product did not.
           Item padding is taken out of the cell BEFORE a cell widget is
           given its geometry, so on a 30px row 10px each side left NINE
           pixels for a chip that needs twenty-one. The leave history drew
           every status badge sliced in half, top edge only. Measured, not
           guessed: the holder came back 9px tall against a 21px hint.
           The breathing room moves to the row height below, where a widget
           can use it. */
        QTableWidget::item {{ padding:4px 12px; border-bottom:1px solid {C.BORDER_SOFT}; }}
        QTableWidget::item:selected {{ background:{C.ELEVATED}; color:{C.TEXT}; }}
        /* The item check indicator, which is NOT the QCheckBox one — see the
           console's copy of this rule. Unstyled it falls to the platform,
           and on the light theme macOS draws a large red circle. */
        QTableWidget::indicator {{
            width:16px; height:16px; border:1.5px solid {C.BORDER};
            border-radius:{Radius.CHIP}px; background:{C.CARD};
        }}
        QTableWidget::indicator:hover {{ border-color:{C.PRIMARY}; }}
        QTableWidget::indicator:checked {{
            background:{C.PRIMARY}; border:1.5px solid {C.PRIMARY};
            image: url("{_tick_file()}");
        }}
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
    # Imported here, not at the top: icons draws with QtGui and this module is
    # pulled in by almost everything, some of it before an application object
    # exists. A local import keeps the palette importable on its own.
    from client.presentation.widgets import icons as _icons
    return f"""
        QLineEdit, QComboBox, QDateEdit, QSpinBox {{
            background:{C.CARD}; border:1px solid {C.BORDER}; border-radius:{R_SM}px;
            color:{C.TEXT}; padding:9px 12px; font-size:13px;
        }}
        QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
            border-color:{C.PRIMARY}; }}
        /* AN ARROW THAT IS DRAWN, NOT INHERITED FROM THE PLATFORM.
           `drop-down` with only a width takes the control off native drawing
           for the frame but leaves the arrow to Qt's fallback — which on this
           dark card is a black block, and on some builds nothing at all, so
           the field does not look like a dropdown. The same rule covers the
           spin and date steppers, which showed the same smudge on every row
           of the configuration page. */
        QComboBox::drop-down {{ border:none; width:22px; background:transparent; }}
        QComboBox::down-arrow {{
            image: url("{_icons.icon_file('chevron-down', 12, C.TEXT_MUTED)}");
            width:12px; height:12px;
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDateEdit::up-button, QDateEdit::down-button {{
            border:none; background:transparent; width:18px; height:14px; margin-right:4px;
        }}
        QSpinBox::up-button, QDateEdit::up-button {{ subcontrol-position: top right; }}
        QSpinBox::down-button, QDateEdit::down-button {{ subcontrol-position: bottom right; }}
        QSpinBox::up-arrow, QDateEdit::up-arrow {{
            image: url("{_icons.icon_file('chevron-up', 10, C.TEXT_MUTED)}");
            width:10px; height:10px;
        }}
        QSpinBox::down-arrow, QDateEdit::down-arrow {{
            image: url("{_icons.icon_file('chevron-down', 10, C.TEXT_MUTED)}");
            width:10px; height:10px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover,
        QDateEdit::up-button:hover, QDateEdit::down-button:hover {{
            background:{C.ELEVATED}; border-radius:{Radius.CHIP}px;
        }}
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
    """ + input_style() + scrollbar() + tick_style()


def tick_style() -> str:
    """Check boxes and radio buttons, drawn rather than left to the platform.

    THE EMPLOYEE PANEL NEVER HAD THIS. The admin console styles its
    indicators and the employee panel did not, so "Half day" on the leave
    form and the notification boxes on the profile page fell through to the
    native macOS control: a dark rounded square on a dark card — the black
    spot — with a tick nobody can see against it.

    Same shape, same 18px, same accent as the console, because they are the
    same product and a person moving between the two panels should not be
    able to tell which one drew the box.
    """
    from client.presentation.widgets import icons as _icons
    return f"""
        QCheckBox, QRadioButton {{ background:transparent; spacing:10px;
                                   color:{C.TEXT}; font-size:13px; }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width:18px; height:18px;
            border:1.5px solid {C.BORDER};
            background:{C.CARD};
        }}
        QCheckBox::indicator {{ border-radius:6px; }}
        QRadioButton::indicator {{ border-radius:9px; }}
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
            border-color:{C.PRIMARY}; }}
        QCheckBox::indicator:checked {{
            background:{C.PRIMARY}; border:1.5px solid {C.PRIMARY};
            image: url("{_icons.icon_file('check', 12, '#ffffff')}");
        }}
        /* A DOT DRAWN INSIDE THE RING, not a thick border pretending to be
           one. A 5px border on an 18px box is the usual trick and Qt would
           not round it — the chosen radio drew as a blue SQUARE, seen in a
           render rather than guessed. Keeping the border width identical to
           the unchecked state keeps the corner radius honest. */
        QRadioButton::indicator:checked {{
            border:1.5px solid {C.PRIMARY}; border-radius:9px;
            image: url("{_icons.icon_file('dot', 16, C.PRIMARY)}");
        }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
            border-color:{C.BORDER_SOFT}; background:{C.ELEVATED}; }}
    """
