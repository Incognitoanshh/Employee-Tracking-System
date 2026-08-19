"""
Line icons, drawn from Lucide's own paths. No emoji anywhere.

WHY THIS EXISTS. The product used emoji for every icon in the menu, the
buttons and the status bar. Emoji are drawn by the operating system's colour
font: they carry their own palette, their own weight and their own idea of a
baseline, so a menu made of them is fifteen different illustration styles in a
column — and they look, correctly, like a hobby project. They also render
differently on macOS and Windows, so the two builds did not match.

These are stroke icons on a 24×24 grid, 1.75px, round caps and joins — one
weight, one grid, and they take the colour of whatever they sit on, so an
active menu row tints its icon with its text.

RENDERED THROUGH QtSvg, AT THE DEVICE PIXEL RATIO. Rasterising at 24px and
letting Qt scale it up is what makes an icon look soft on a retina screen;
QSvgRenderer draws at the size actually needed. Results are cached per
(name, size, colour) because a table can ask for the same icon a hundred
times while it fills.

The paths are Lucide's, which is ISC-licensed and permits this. Where a
concept has no Lucide equivalent that reads at 18px, the nearest one is used
rather than a new drawing invented — an icon nobody recognises is worse than
a slightly approximate one.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# The inner markup of each Lucide icon, minus the <svg> wrapper.
_PATHS: dict[str, str] = {
    # ── navigation ────────────────────────────────────────────────────
    "layout-dashboard":
        '<rect width="7" height="9" x="3" y="3" rx="1"/>'
        '<rect width="7" height="5" x="14" y="3" rx="1"/>'
        '<rect width="7" height="9" x="14" y="12" rx="1"/>'
        '<rect width="7" height="5" x="3" y="16" rx="1"/>',
    "bell":
        '<path d="M10.268 21a2 2 0 0 0 3.464 0"/>'
        '<path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673'
        'C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
    "users":
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "messages-square":
        '<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/>'
        '<path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>',
    "message-circle":
        '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    "calendar-days":
        '<path d="M8 2v4"/><path d="M16 2v4"/>'
        '<rect width="18" height="18" x="3" y="4" rx="2"/>'
        '<path d="M3 10h18"/><path d="M8 14h.01"/><path d="M12 14h.01"/>'
        '<path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/>',
    "palmtree":
        '<path d="M13 8H7a4 4 0 0 0-4 4 7 7 0 0 1 7-2"/>'
        '<path d="M13 7.14A5.82 5.82 0 0 1 16.5 6a5 5 0 0 1 5 5 7 7 0 0 0-7-2"/>'
        '<path d="M5.89 9.71c-2.15 2.15-2.3 5.06-.5 6.85l6.85-6.85c-1.79-1.8-4.7-1.65-6.85 0"/>'
        '<path d="M11 15.5c.5 2.5-.17 4.5-1 6.5h4c2-5.5-.5-12-1-14"/>',
    "wallet":
        '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/>'
        '<path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "bar-chart-3":
        '<path d="M3 3v18h18"/><path d="M18 17V9"/>'
        '<path d="M13 17V5"/><path d="M8 17v-3"/>',
    "camera":
        '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
        '<circle cx="12" cy="13" r="3"/>',
    "clipboard-list":
        '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/>'
        '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'
        '<path d="M12 11h4"/><path d="M12 16h4"/>'
        '<path d="M8 11h.01"/><path d="M8 16h.01"/>',
    "settings":
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>',
    "user":
        '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>'
        '<circle cx="12" cy="7" r="4"/>',
    "umbrella":
        '<path d="M22 12a10.06 10.06 1 0 0-20 0Z"/>'
        '<path d="M12 12v8a2 2 0 0 0 4 0"/><path d="M12 2v1"/>',
    "receipt":
        '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/>'
        '<path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',

    "circle-check":
        '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "circle-slash":
        '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "clock":
        '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "calendar-off":
        '<path d="M4.18 4.18A2 2 0 0 0 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 1.82-1.18"/>'
        '<path d="M21 15.5V6a2 2 0 0 0-2-2H9.5"/><path d="M16 2v4"/>'
        '<path d="M3 10h7"/><path d="M21 10h-5.5"/><path d="m2 2 20 20"/>',
    "server":
        '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/>'
        '<rect width="20" height="8" x="2" y="14" rx="2" ry="2"/>'
        '<line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
    "database":
        '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
        '<path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
    "crosshair":
        '<circle cx="12" cy="12" r="10"/><line x1="22" x2="18" y1="12" y2="12"/>'
        '<line x1="6" x2="2" y1="12" y2="12"/><line x1="12" x2="12" y1="6" y2="2"/>'
        '<line x1="12" x2="12" y1="22" y2="18"/>',
    "cloud":
        '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
    "image":
        '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>'
        '<circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "activity":
        '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
    "monitor":
        '<rect width="20" height="14" x="2" y="3" rx="2"/>'
        '<line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
    "timer":
        '<line x1="10" x2="14" y1="2" y2="2"/><line x1="12" x2="15" y1="14" y2="11"/>'
        '<circle cx="12" cy="14" r="8"/>',
    "trash-2":
        '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
        '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
        '<line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
    "save":
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
        '<path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/>'
        '<path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    "shield":
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "crown":
        '<path d="M11.562 3.266a.5.5 0 0 1 .876 0L15.39 8.87a1 1 0 0 0 1.516.294L21.183 5.5a.5.5 0 0 1 .798.519l-2.834 10.246a1 1 0 0 1-.956.734H5.81a1 1 0 0 1-.957-.734L2.02 6.02a.5.5 0 0 1 .798-.519l4.276 3.664a1 1 0 0 0 1.516-.294z"/>'
        '<path d="M5 21h14"/>',
    "paperclip":
        '<path d="M13.234 20.252 21 12.3a4.243 4.243 0 0 0-6-6L4.116 17.408a6 6 0 0 0 8.485 8.485"/>',
    "megaphone":
        '<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
    "globe":
        '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
        '<path d="M2 12h20"/>',
    # ── actions ───────────────────────────────────────────────────────
    "log-out":
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        '<polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
    "key-round":
        '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/>'
        '<circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>',
    "search":
        '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "x":
        '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "download":
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    "refresh-cw":
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>',
    "cloud-upload":
        '<path d="M12 13v8"/><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/>'
        '<path d="m8 17 4-4 4 4"/>',
    "moon":
        '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "sun":
        '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "chevron-left":
        '<path d="m15 18-6-6 6-6"/>',
    "chevron-right":
        '<path d="m9 18 6-6-6-6"/>',
    # Up and down exist for the same reason as left and right: a stepper and
    # a dropdown need an arrow, and the alternative Qt reaches for is the
    # platform's own — which on a dark surface is the black block reported as
    # a "black spot" on every row of the configuration page.
    "chevron-down":
        '<path d="m6 9 6 6 6-6"/>',
    "chevron-up":
        '<path d="m18 15-6-6-6 6"/>',
    # A FILLED DOT — the one icon here that is not a stroke. It fills with
    # `currentColor` so the same colour argument drives it, and it exists
    # because a chosen radio button needs a mark: Qt will not reliably round
    # a border thick enough to be the dot itself, which drew as a blue SQUARE.
    # The composer's own three: attach (paperclip, already here), emoji and
    # mention. Drawn from the same Lucide set so the toolbar reads as one row
    # of controls rather than three borrowed marks.
    "smile":
        '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/>'
        '<line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/>',
    "at-sign":
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.92 7.94"/>',
    "send":
        '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5'
        'a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>',
    "eye":
        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3"/>',
    "more-horizontal":
        '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'
        '<circle cx="5" cy="12" r="1"/>',
    "corner-up-left":
        '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>',
    "dot":
        '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>',
    "lock":
        '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/>'
        '<path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "circle-help":
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "triangle-alert":
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "check":
        '<path d="M20 6 9 17l-5-5"/>',
    "info":
        '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
}

# Names the product uses, mapped to the icon that carries them. Kept separate
# from the paths so a page asks for "attendance", not for "calendar-days" —
# changing which glyph represents a concept is then one line here.
BY_KEY = {
    "dashboard":   "layout-dashboard",
    "alerts":      "bell",
    "employees":   "users",
    "teams":       "messages-square",
    "mychat":      "message-circle",
    "attendance":  "calendar-days",
    "leave":       "palmtree",
    "payroll":     "wallet",
    "reports":     "bar-chart-3",
    "screenshots": "camera",
    "logs":        "clipboard-list",
    "config":      "settings",
    "myleave":     "umbrella",
    "mypayroll":   "receipt",
    "profile":     "user",
    "help":        "circle-help",
    "settings":    "settings",
    "team":        "messages-square",
}

_cache: dict[tuple, QIcon] = {}


def pixmap(name: str, size: int, colour: str, ratio: float = 2.0) -> QPixmap:
    """One icon, stroked in `colour`, sharp at `ratio` device pixels."""
    body = _PATHS.get(BY_KEY.get(name, name))
    if body is None:
        return QPixmap()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" fill="none" stroke="{colour}" '
        # `color` so an icon may fill with currentColor — the dot does.
        f'color="{colour}" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
        f'{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    target = QPixmap(int(size * ratio), int(size * ratio))
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


def icon(name: str, size: int = 18, colour: str = "#94A3B8") -> QIcon:
    """A QIcon, cached. Safe to call in a loop that fills a table."""
    key = (name, size, colour)
    hit = _cache.get(key)
    if hit is None:
        hit = QIcon(pixmap(name, size, colour))
        _cache[key] = hit
    return hit


def known(name: str) -> bool:
    return BY_KEY.get(name, name) in _PATHS


_files: dict[tuple, str] = {}


def icon_file(name: str, size: int = 14, colour: str = "#ffffff") -> str:
    """An icon written to a PNG, and the path to it — for Qt stylesheets.

    QT STYLESHEETS CANNOT READ A data: URI. `image: url("data:image/svg+xml…")`
    parses without complaint and draws nothing, which is how a checked
    checkbox ended up as a plain blue square with no tick in it — on and off
    told apart by colour alone, which a colourblind reader cannot do at all.
    QSS wants a file path, so this makes one.

    Written once per (name, size, colour) into the system temp directory and
    reused. Forward slashes always: a Windows path in a stylesheet is read
    with its backslashes as escapes.
    """
    key = (name, size, colour)
    hit = _files.get(key)
    if hit:
        return hit

    import os
    import tempfile

    folder = os.path.join(tempfile.gettempdir(), "amaze-connect-icons")
    os.makedirs(folder, exist_ok=True)
    safe = colour.lstrip("#")
    path = os.path.join(folder, f"{BY_KEY.get(name, name)}-{size}-{safe}.png")
    if not os.path.exists(path):
        pixmap(name, size, colour, ratio=2.0).save(path, "PNG")
    path = path.replace("\\", "/")
    _files[key] = path
    return path


def clear_cache():
    """Called when the theme changes — the colours are baked into the pixmaps."""
    _cache.clear()
    _files.clear()
