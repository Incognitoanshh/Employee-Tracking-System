"""
The Amaze Connect mark, and the lock-up it sits in.

WHAT WAS THERE BEFORE. A flat blue rounded square with the letter "A" set in
the interface font. That is a placeholder — it says "somebody has not made a
logo yet", because the one thing a mark must not look like is text in a box.

WHAT THIS IS. A drawn glyph on a gradient tile: a chevron A cut from two
wedges with a separate crossbar, so it reads as a mark rather than as a
character, at 28px and at 96px alike. Vector throughout, rendered at the
device pixel ratio, because a logo is the first thing anybody looks at and a
soft one is noticed even by people who could not say why.

The lock-up — tile, hairline rule, name, and the console it belongs to — is
one widget so that every place showing the brand shows the same brand. It
appeared in three places with three different spacings before.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from client.presentation.theme import C, Space, Type


# The dark the mark sits on. Used by the tray, where a status dot needs a
# ring that separates it from the tile on both a light and a dark menu bar —
# and the palette cannot supply it, because the mark does not change colour
# with the theme.
RING = "#0b1220"


def _svg(size: int) -> str:
    """The tile and the glyph, on a 48-unit grid.

    THE GLYPH IS STROKED, NOT FILLED. A chevron drawn as two filled wedges
    changes weight as it tapers, and at 20px the apex turns into a blob. A
    stroke of one width holds its shape at every size the product uses — the
    menu tile is 40px, the login card wants 72px, and the same file has to
    serve both.

    The crossbar stops short of the left leg. That gap is the mark: without
    it this is the letter A in a box, which is what it was.
    """
    return f'''
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"
     width="{size}" height="{size}">
  <defs>
    <!-- DARK TILE, BLUE MARK — the company's own logo, not a generic app
         icon. Amaze Internet's mark is an outlined angular A in blue on
         black; a white A on a bright blue square was a different brand that
         happened to share a letter. -->
    <linearGradient id="tile" x1="0.15" y1="0" x2="0.9" y2="1">
      <stop offset="0%"   stop-color="#16233F"/>
      <stop offset="55%"  stop-color="#0C1424"/>
      <stop offset="100%" stop-color="#05080F"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.3" cy="0.15" r="0.9">
      <stop offset="0%"   stop-color="#3B82F6" stop-opacity="0.30"/>
      <stop offset="55%"  stop-color="#3B82F6" stop-opacity="0.06"/>
      <stop offset="100%" stop-color="#3B82F6" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="ink" x1="0.1" y1="0" x2="0.9" y2="1">
      <stop offset="0%"   stop-color="#7FB2FF"/>
      <stop offset="55%"  stop-color="#4A90E2"/>
      <stop offset="100%" stop-color="#2563EB"/>
    </linearGradient>
  </defs>

  <rect width="48" height="48" rx="13.5" fill="url(#tile)"/>
  <rect width="48" height="48" rx="13.5" fill="url(#glow)"/>
  <rect x="0.9" y="0.9" width="46.2" height="46.2" rx="12.8"
        fill="none" stroke="#3B82F6" stroke-opacity="0.40" stroke-width="1.6"/>

  <!-- The A: an outlined chevron with a cut right leg and a solid counter,
       the shape from the company wordmark rather than a letter in a font. -->
  <g stroke="url(#ink)" fill="none" stroke-width="3.1"
     stroke-linejoin="miter" stroke-linecap="butt">
    <path d="M24 11 L37 37"/>
    <path d="M24 11 L11 37"/>
    <path d="M15.4 30.2 H32.6"/>
  </g>
  <path d="M24 22.4 L28.2 31.2 H19.8 Z" fill="url(#ink)"/>
</svg>'''


def _screen_ratio() -> float:
    """The device pixel ratio of the screen this is actually drawn on.

    IT WAS HARD-CODED TO 2.0. That is right on a retina Mac and wrong
    everywhere else: on a 1× display it rendered four times the pixels needed
    and Qt scaled them back down, which is a blur; on a 3× phone-class panel
    it rendered too few and the edges went soft. The logo is the first thing
    anybody looks at, and a soft one is noticed by people who could not say
    what is wrong with it.
    """
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 2.0


def mark_pixmap(size: int = 40, ratio: float | None = None) -> QPixmap:
    """The tile on its own, sharp at the screen's own pixel ratio."""
    ratio = ratio or _screen_ratio()
    # The SVG is asked for the FINAL pixel size, not the logical one, so the
    # renderer lays out its strokes on the real grid instead of on a 40px one
    # that is then magnified.
    renderer = QSvgRenderer(QByteArray(_svg(int(size * ratio)).encode("utf-8")))
    target = QPixmap(int(size * ratio), int(size * ratio))
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


class BrandMark(QLabel):
    """Just the tile."""

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setPixmap(mark_pixmap(size))
        self.setStyleSheet("background:transparent;border:none;")


class BrandLockup(QWidget):
    """Mark · rules · wordmark — the company's lock-up, in one place.

    THE RULES ARE PART OF THE LOGO. Amaze Internet's wordmark sits between two
    blue lines that run past the text to the right; without them this is just
    bold type next to an icon. They are drawn as thin frames rather than
    characters so they scale with the widget rather than with the font.
    """

    def __init__(self, subtitle: str = "", size: int = 40, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.SM)

        row.addWidget(BrandMark(size))

        stack = QVBoxLayout()
        stack.setSpacing(3)
        stack.setContentsMargins(0, 0, 0, 0)

        def rule():
            line = QFrame()
            line.setFixedHeight(2)
            line.setStyleSheet(
                f"background:{C.PRIMARY};border:none;border-radius:1px;")
            return line

        stack.addWidget(rule())

        word = QLabel("AMAZE CONNECT")
        word.setStyleSheet(
            f"color:{C.TEXT};font-size:{Type.SECTION}px;font-weight:800;"
            f"letter-spacing:1.2px;background:transparent;")
        stack.addWidget(word)

        stack.addWidget(rule())

        if subtitle:
            caption = QLabel(subtitle)
            caption.setStyleSheet(
                f"color:{C.TEXT_MUTED};font-size:{Type.MICRO}px;"
                f"letter-spacing:0.6px;background:transparent;")
            stack.addWidget(caption)

        row.addLayout(stack)
        row.addStretch()
