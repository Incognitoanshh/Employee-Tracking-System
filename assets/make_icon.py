#!/usr/bin/env python3
"""Amaze Connect ka app icon — wahi mark jo app ke andar hai.

    assets/icon.png     1024x1024   (Linux / preview / docs)
    assets/icon.ico     multi-size  (Windows .exe, aur balloon notifications)
    assets/icon.icns    multi-size  (macOS .app / DMG / Notification Center)

Chalane ka tarika:
    python3 assets/make_icon.py

WHY THIS FILE NO LONGER DRAWS ANYTHING ITSELF.

It used to build its own icon with PIL — a monogram in a rounded square with a
ring around it — which meant the application icon and the mark inside the
application were two different drawings that merely resembled each other. They
drifted, and nobody noticed until both were on screen at once: the Dock showed
one logo and the sidebar another.

It now renders client/presentation/widgets/brand.py, the same SVG the sidebar
draws. One source, so they cannot disagree.

THE MACOS NOTIFICATION ICON COMES FROM HERE. macOS takes the picture beside a
notification from the posting application's bundle, so icon.icns IS the
notification logo — there is no separate setting for it.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MASTER = 1024


def render_master(path: str) -> None:
    """The brand mark at 1024px, straight from its own SVG."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication([])
    from client.presentation.widgets import brand

    # THE TILE IS DRAWN EDGE TO EDGE, and macOS/Windows apply their own
    # rounding and padding to what they are given. The SVG already carries the
    # rounded corner, so it is handed over as-is rather than being inset —
    # insetting it here and letting the platform round it again produced a
    # visibly smaller, double-rounded tile in the Dock.
    svg = brand._svg(MASTER).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    target = QPixmap(MASTER, MASTER)
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    target.save(path, "PNG")
    del app


def write_ico(master_png: str, out: str) -> None:
    from PIL import Image

    image = Image.open(master_png).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256)]
    image.save(out, format="ICO", sizes=sizes)


def write_icns(master_png: str, out: str) -> None:
    """iconutil on macOS; a Pillow fallback everywhere else."""
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as work:
            iconset = os.path.join(work, "icon.iconset")
            os.makedirs(iconset)
            from PIL import Image
            image = Image.open(master_png).convert("RGBA")
            for size in (16, 32, 128, 256, 512):
                image.resize((size, size), Image.LANCZOS).save(
                    os.path.join(iconset, f"icon_{size}x{size}.png"))
                image.resize((size * 2, size * 2), Image.LANCZOS).save(
                    os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out],
                           check=True)
        return

    from PIL import Image
    Image.open(master_png).convert("RGBA").save(out, format="ICNS")


def main() -> int:
    png = os.path.join(HERE, "icon.png")
    render_master(png)
    print(f"  wrote {png}")

    ico = os.path.join(HERE, "icon.ico")
    write_ico(png, ico)
    print(f"  wrote {ico}")

    icns = os.path.join(HERE, "icon.icns")
    write_icns(png, icns)
    print(f"  wrote {icns}")

    print("\nThese three are what the Dock, the installer, the Windows "
          "balloon and the macOS notification all draw from.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
