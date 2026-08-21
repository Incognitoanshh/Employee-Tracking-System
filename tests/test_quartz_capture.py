"""
On a Mac the screen must be read BY THIS PROCESS, not by a subprocess.

THE BUG THIS EXISTS FOR, reported from a real Mac on 21 August 2026: the app
asked for Screen Recording; System Settings showed it was already allowed; the
app was quit and reopened as macOS instructs — and the prompt came back. Quit
and reopened again, and at the next capture it asked a third time.

Both old macOS capture paths handed the screen to another program.
`pyautogui.screenshot()` goes through Pillow, which runs `screencapture -x`,
and the multi-display branch ran `screencapture` directly. macOS ties Screen
Recording to the code signature of whoever actually reads the screen and
re-decides per process, so a permission granted to the app did not settle the
question for the next `screencapture` it spawned.

_grab_via_quartz() reads the framebuffer in-process through CoreGraphics, so
the identity that was granted is the identity that is checked — the same one
CGPreflightScreenCaptureAccess() reports on.

WHAT THIS TEST REFUSES TO ACCEPT. That a capture "worked" because it returned
an image. A wrong stride shears the picture diagonally and a swapped channel
order turns every screenshot blue, and both produce a perfectly valid Image of
the right size. So this compares the in-process capture against the old
`screencapture` path on the live display, pixel statistics and all, and fails
if they disagree.

Skips anywhere that is not a Mac with a display and Screen Recording granted —
CI has no framebuffer, and a test that silently passes there proves nothing.

Run:  python3 tests/test_quartz_capture.py
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if not ok and detail else ""))
    if not ok:
        failures += 1


def skip(why):
    print(f"  SKIP  {why}")
    raise SystemExit(0)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from client.application.managers import screenshot_manager as sm   # noqa: E402

print("\nIN-PROCESS SCREEN CAPTURE (macOS)\n")

if sys.platform != "darwin":
    skip("not a Mac — this path exists only for macOS permissions")

try:
    import Quartz                                                  # noqa: F401
except Exception as error:
    skip(f"PyObjC's Quartz is not installed here — {error}")

from client.services.screen_permission import has_screen_access    # noqa: E402

if not has_screen_access():
    skip("Screen Recording is not granted on this machine")

# ── it returns something at all ──────────────────────────────────────────
shot = sm._grab_via_quartz()
check("CoreGraphics hands back an image", shot is not None,
      "None means it fell through to the prompting path")
if shot is None:
    raise SystemExit(1)

check("the image has real dimensions", shot.width > 0 and shot.height > 0,
      f"{shot.width}x{shot.height}")
check("it is RGB, ready for the JPEG encode downstream", shot.mode == "RGB",
      shot.mode)

# ── it agrees with the path it replaces ──────────────────────────────────
# One display only. With several, _grab_via_quartz pastes them side by side
# and `screencapture -D 1` is just the first, so the two are legitimately
# different pictures and comparing them would fail for the wrong reason.
error_code, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
if count != 1:
    print(f"  NOTE  {count} displays attached — skipping the pixel comparison, "
          f"which is single-display only")
else:
    path = tempfile.mktemp(suffix=".png")
    try:
        done = subprocess.run(["screencapture", "-x", "-D", "1", path],
                              capture_output=True, timeout=30)
        if done.returncode != 0 or not os.path.exists(path):
            skip("`screencapture` itself failed, so there is nothing to compare")

        from PIL import Image
        old = Image.open(path)
        old.load()
        old = old.convert("RGB")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    check("same dimensions as `screencapture`", shot.size == old.size,
          f"in-process {shot.size} vs screencapture {old.size}")

    if shot.size == old.size:
        # Downsampled before averaging: the two captures are moments apart, so
        # a blinking cursor or a moving clock differs by a few pixels and must
        # not be read as a broken decode.
        small_new = shot.resize((160, 100))
        small_old = old.resize((160, 100))

        def channel_means(image):
            raw = image.tobytes()          # RGBRGB…, three bytes per pixel
            n = len(raw) // 3
            return tuple(sum(raw[i::3]) / n for i in range(3))

        new_means = channel_means(small_new)
        old_means = channel_means(small_old)
        straight = sum(abs(a - b) for a, b in zip(new_means, old_means))

        # 30 is loose on purpose. The two captures are taken moments apart on
        # a live desktop — a clock ticks, a cursor blinks, a window animates —
        # and an earlier, tighter threshold failed on an ordinary grey desktop
        # for no reason but that. A test that cries wolf gets ignored, and
        # then it is worth less than no test. What this is here to catch is
        # the large, structural kind of wrong: a sheared decode or channels
        # read backwards, which move this into the hundreds. Channel order
        # itself is settled exactly, further down, against a known buffer.
        check("the colours are those of the old path",
              straight < 30,
              f"R,G,B off by {straight:.1f} in total "
              f"(in-process {tuple(round(v, 1) for v in new_means)}, "
              f"screencapture {tuple(round(v, 1) for v in old_means)})")

# ── channel order and stride, settled exactly ────────────────────────────
# Against a buffer built here, so the right answer is known rather than
# inferred from whatever is on screen. This is what catches a red/blue swap
# on a grey desktop, where comparing live captures cannot: both would be grey
# and the difference would be zero.
#
# Two rows of two pixels, in BGRA, with eight bytes of row padding — so a
# decoder that assumes stride == width * 4 reads the padding as picture and
# comes out visibly wrong.
red, green, blue, white = (0, 0, 255, 255), (0, 255, 0, 255), (255, 0, 0, 255), (255, 255, 255, 255)
padding = bytes(8)
buffer = (bytes(red) + bytes(green) + padding
          + bytes(blue) + bytes(white) + padding)

decoded = sm._image_from_bgra(buffer, 2, 2, 4 * 2 + len(padding))
corners = [decoded.getpixel((0, 0)), decoded.getpixel((1, 0)),
           decoded.getpixel((0, 1)), decoded.getpixel((1, 1))]
check("BGRA is decoded as BGRA, so red stays red",
      corners == [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)],
      f"{corners} — expected red, green, blue, white")

# And the same buffer read with the padding ignored, to prove the check above
# would actually have noticed.
sheared = sm._image_from_bgra(buffer, 2, 2, 4 * 2)
check("and the stride matters — ignoring it gives a different picture",
      [sheared.getpixel((0, 1)), sheared.getpixel((1, 1))] != corners[2:],
      "the padded and unpadded reads agree, so this test cannot see shearing")

# ── and the caller actually uses it ──────────────────────────────────────
# The point of the change is that _grab_every_screen stops shelling out. If it
# still calls pyautogui on a Mac, everything above can pass while the
# permission prompt keeps coming back.
called = {"pyautogui": False}
real_pyautogui = sm.pyautogui.screenshot
sm.pyautogui.screenshot = lambda *a, **k: (
    called.__setitem__("pyautogui", True) or real_pyautogui(*a, **k))
try:
    sm._grab_every_screen()
finally:
    sm.pyautogui.screenshot = real_pyautogui

check("_grab_every_screen reads the screen in-process, not via a subprocess",
      not called["pyautogui"],
      "it fell through to pyautogui, which runs `screencapture` — "
      "the permission prompt will keep returning")

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")
raise SystemExit(1 if failures else 0)
