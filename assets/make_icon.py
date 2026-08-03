#!/usr/bin/env python3
"""Amaze ETS ka app icon generate karta hai.

Ek hi source se teeno format nikalte hain, taaki Windows/Mac/Linux pe icon
kabhi mismatch na ho:

    assets/icon.png     1024x1024   (Linux / preview / docs)
    assets/icon.ico     multi-size  (Windows .exe)
    assets/icon.icns    multi-size  (macOS .app / DMG)

Chalane ka tarika:
    python3 assets/make_icon.py

Design: dark rounded-square, blue gradient, beech me "A" monogram, aur uske
charon taraf ek adhura ring jo tracking/monitoring ko darshata hai. Ring
jaan-boojh kar halka rakha hai — 16px pe sirf "A" padhna chahiye, baaki
detail us size pe shor banti hai.
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # master size

BG_TOP    = (23, 32, 56)     # deep navy
BG_BOT    = (12, 17, 32)
ACCENT    = (59, 130, 246)   # blue-500  (panel ke accent se match)
ACCENT_2  = (34, 211, 238)   # cyan-400
WHITE     = (255, 255, 255)


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        g.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t)
                                 for i in range(3)))
    return g.resize((size, size), Image.BILINEAR)


def diagonal_gradient(size, c1, c2):
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return g


def load_font(px):
    """Bold sans dhoondo. Har OS pe alag jagah hoti hai, is liye list."""
    for path in (
        "/System/Library/Fonts/Supplemental/Futura.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                continue
    return ImageFont.load_default()


def build_master(with_ring: bool = True):
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 1. Background: rounded square with vertical gradient
    bg = vertical_gradient(S, BG_TOP, BG_BOT).convert("RGBA")
    icon.paste(bg, (0, 0), rounded_mask(S, int(S * 0.225)))

    if not with_ring:
        # 48px se neeche ring aur monogram aapas me ghul jaate hain aur icon
        # ek dhabba lagta hai. Un sizes pe sirf "A" rakhte hain — dock/taskbar
        # me yahi legible rehta hai. (Yehi approach macOS/Windows ke apne
        # icons me hai: chhote size ka artwork alag hota hai, resize nahi.)
        txt = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        font = load_font(int(S * 0.70))
        box = td.textbbox((0, 0), "A", font=font)
        td.text(((S - (box[2] - box[0])) / 2 - box[0],
                 (S - (box[3] - box[1])) / 2 - box[1]),
                "A", font=font, fill=WHITE + (255,))
        icon.alpha_composite(txt)
        return icon

    # 2. Tracking ring — adhura, taaki "monitoring / in progress" lage
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    pad, w = int(S * 0.105), int(S * 0.045)
    rd.arc([pad, pad, S - pad, S - pad], start=-72, end=252,
           fill=ACCENT + (255,), width=w)
    # gradient ko ring ke andar mask karke bhar do
    grad = diagonal_gradient(S, ACCENT, ACCENT_2).convert("RGBA")
    icon.alpha_composite(Image.composite(
        grad, Image.new("RGBA", (S, S), (0, 0, 0, 0)), ring.split()[3]))

    # 3. Ring ke gap pe ek chhota dot — "live" indicator
    d = ImageDraw.Draw(icon)
    import math
    ang = math.radians(-72)
    cx = S / 2 + (S / 2 - pad) * math.cos(ang)
    cy = S / 2 + (S / 2 - pad) * math.sin(ang)
    r = w * 0.85
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT_2 + (255,))

    # 4. "A" monogram — yahi 16px pe padha jaata hai
    txt = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt)
    font = load_font(int(S * 0.56))
    box = td.textbbox((0, 0), "A", font=font)
    td.text(((S - (box[2] - box[0])) / 2 - box[0],
             (S - (box[3] - box[1])) / 2 - box[1]),
            "A", font=font, fill=WHITE + (255,))
    icon.alpha_composite(txt)

    return icon


def main():
    icon  = build_master(with_ring=True)
    small = build_master(with_ring=False)

    def at(size):
        """<=48px pe simplified artwork, uske upar full design."""
        src = small if size <= 48 else icon
        return src.resize((size, size), Image.LANCZOS)

    png = os.path.join(HERE, "icon.png")
    icon.save(png)
    print(f"✅ {png}")

    # Windows .ico — chhote sizes alag se resize karo, warna 16px dhundhla
    ico = os.path.join(HERE, "icon.ico")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    at(256).save(ico, format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=[at(s) for s in sizes])
    print(f"✅ {ico}")

    # macOS .icns — iconutil sirf Mac pe hota hai
    if sys.platform != "darwin":
        print("ℹ️  .icns skip (macOS pe hi ban sakta hai)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "icon.iconset")
        os.makedirs(iconset)
        for size in (16, 32, 128, 256, 512):
            at(size).save(os.path.join(iconset, f"icon_{size}x{size}.png"))
            at(size * 2).save(os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
        icns = os.path.join(HERE, "icon.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       check=True)
        print(f"✅ {icns}")


if __name__ == "__main__":
    main()
