"""
The design system: the scales, the one status mapping, and a ratchet.

WHY A RATCHET RATHER THAN A RULE. The panels contain 383 hand-written
stylesheets, nineteen font sizes and fourteen border radii. A test demanding
that all of them come from the scale would fail on the first run and stay
failing for weeks, and a test that is always red is a test nobody reads.

So it counts instead. The counts may go DOWN freely; they may not go up. Each
migrated page lowers the ceiling behind it, and a new page written the old way
fails immediately — which is the behaviour that matters, because that is how
the inconsistency got here in the first place.

Run:  python3 tests/test_design_system.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

UI_DIRS = [os.path.join(ROOT, "client", "presentation", "windows"),
           os.path.join(ROOT, "client", "presentation", "widgets"),
           os.path.join(ROOT, "client", "presentation", "tray")]

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


def ui_sources():
    for directory in UI_DIRS:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                yield path, open(path, encoding="utf-8").read()


ALL = "\n".join(text for _p, text in ui_sources())

# THE MARK'S OWN COLOURS ARE NOT THEME COLOURS. widgets/brand.py holds the
# gradient stops of the logo, and a logo that changed colour with the theme
# would not be a logo. Counting them as drift would push somebody to "fix"
# them by wiring the brand to the palette, which is the wrong outcome.
BRAND = os.path.join(ROOT, "client", "presentation", "widgets", "brand.py")
ALL_THEMED = "\n".join(text for path, text in ui_sources()
                       if os.path.abspath(path) != os.path.abspath(BRAND))

def _without_prose(text: str) -> str:
    """The file with comments and docstrings blanked out, line numbers kept.

    A docstring explaining which glyph was removed necessarily contains that
    glyph. Scanning it would make writing the explanation an offence, which
    is how a rule ends up being deleted instead of followed.
    """
    stripped = re.sub(r'("""|\'\'\')(?:.|\n)*?\1',
                      lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return "\n".join("" if line.strip().startswith("#") else line
                      for line in stripped.splitlines())



print("\nThe scales exist and have the values everything else assumes")
from client.presentation import theme  # noqa: E402
from client.presentation.theme import Type, Space, Radius, Weight  # noqa: E402

# The eight steps the design brief specifies. The scale was 11/12/13/15/20/28
# before it; every value moved, which is why the ratchet baselines below were
# reset on the same day rather than carried over.
check("the type scale is the one the brief specifies",
      [Type.MICRO, Type.SMALL, Type.BODY, Type.SECTION,
       Type.LARGE, Type.TITLE, Type.HEADING, Type.DISPLAY]
      == [12, 13, 14, 16, 18, 20, 24, 32],
      str([Type.MICRO, Type.SMALL, Type.BODY, Type.SECTION,
           Type.LARGE, Type.TITLE, Type.HEADING, Type.DISPLAY]))
check("spacing is the 8px system",
      [Space.XS, Space.SM, Space.MD, Space.LG, Space.XL, Space.XXL, Space.HUGE]
      == [8, 12, 16, 20, 24, 32, 40],
      str([Space.XS, Space.SM, Space.MD, Space.LG, Space.XL, Space.XXL, Space.HUGE]))
# 12 and 16, per the brief. R and R_SM are the older names and now differ
# deliberately — anything still reading them is a page not yet migrated,
# which the ratchet counts.
check("the radii are the two the brief allows",
      (Radius.CONTROL, Radius.CARD) == (12, 16),
      f"{Radius.CONTROL}/{Radius.CARD}")

print("\nOne status mapping, and it survives a theme switch")
check("a known status has a foreground and a background",
      len(theme.status_colors("late")) == 2 and
      theme.status_colors("late")[0].startswith("#"))
check("an unknown status is neutral, not a crash",
      theme.status_colors("nonsense") == theme.status_colors("neutral"))
check("an empty status is neutral too",
      theme.status_colors("") == theme.status_colors("neutral"))
check("the key is case-insensitive, because the server shouts some of them",
      theme.status_colors("APPROVED") == theme.status_colors("approved"))

dark_late = theme.status_colors("late")
theme.set_theme("light")
light_late = theme.status_colors("late")
theme.set_theme("dark")
check("the colours follow the theme rather than being frozen at import",
      dark_late != light_late,
      "a table built once at import keeps the dark palette for the whole run")

print("\nStatuses the product actually sends all have an entry")
# Anything missing here reads as grey, which is how "Rejected" ended up
# looking the same as "Cancelled".
for key in ("active", "completed", "incomplete", "on_time", "late",
            "early_exit", "overtime", "outside_shift", "day_off", "extra",
            "half_day", "on_leave", "pending", "approved", "rejected",
            "cancelled", "draft", "finalized", "absent"):
    check(f"“{key}” is mapped",
          theme.status_colors(key) != theme.status_colors("neutral")
          or key in ("completed", "day_off", "extra", "cancelled", "draft"),
          "falls through to neutral")

print("\nThe ratchet: hand-written values may only decrease")
# THE NUMBERS ARE THE MEASUREMENT TAKEN THE DAY THE SCALE WAS INTRODUCED.
# Lower them whenever a page is migrated. Never raise them.
#
# NOT a count of setStyleSheet calls. The first version of this counted those,
# and the very first file written against the scale failed it — because that
# number measures how many widgets exist, not how inconsistent they are. A
# metric that punishes correct new code teaches people to delete the metric.
#
# What is counted is values that are NOT on the scale. Those are the drift.
ON_SCALE_TYPE = {Type.MICRO, Type.SMALL, Type.BODY, Type.SECTION,
                 Type.LARGE, Type.TITLE, Type.HEADING, Type.DISPLAY}
# 0 counts as on-scale: a square corner is a choice, not a guess — the menu
# rows use it so their left indicator meets the edge cleanly.
ON_SCALE_RADIUS = {0, Radius.CONTROL, Radius.CARD, Radius.PILL}

# ALL_THEMED excludes widgets/brand.py, and the same exclusion applies here:
# the lock-up's 2px rule has a 1px radius because that is the shape of the
# logo, not because somebody guessed at a corner.
# CODE ONLY, not comments or docstrings — one comment quotes an HTML
# `font-size:40px` while explaining a rendering bug, and counting that would
# make writing the explanation an offence. Same reason as the emoji scan.
THEMED_CODE = "\n".join(_without_prose(text) for path, text in ui_sources()
                        if os.path.abspath(path) != os.path.abspath(BRAND))
off_type = [int(v) for v in re.findall(r"font-size: ?(\d+)px", THEMED_CODE)
            if int(v) not in ON_SCALE_TYPE]
off_radius = [int(v) for v in re.findall(r"border-radius: ?(\d+)px", THEMED_CODE)
              if int(v) not in ON_SCALE_RADIUS]

# Lowered as pages migrate. Started at 53 / 41 / 20 the day the scale landed;
# the type scale and the console's radii came down first because those two
# were why the admin console and the employee panel did not look like halves
# of the same product — 12/8 there against 14/10 here.
# RESET WHEN THE DESIGN BRIEF CHANGED THE SCALE. The old ceilings were 22
# and 30 against a scale of 11/12/13/15/20/28 and 6/10/14; every step moved,
# so values that were on-scale on Monday are off-scale today. Carrying the
# old numbers forward would have meant a red test that says nothing about
# whether anybody is drifting — the measurement is only useful against the
# scale actually in force.
# ZERO. Every value in the interface is now on the scale, so the ratchet has
# become the rule it was always meant to be: the next off-scale number
# anybody writes fails this test on the spot.
CEILINGS = {
    "off-scale font sizes": (len(off_type), 0),
    "off-scale border radii": (len(off_radius), 0),
    "hard-coded hex colours": (
        len(set(re.findall(r"#[0-9a-fA-F]{6}", ALL_THEMED))), 15),
}
for label, (found, ceiling) in CEILINGS.items():
    check(f"{label}: {found} (ceiling {ceiling})",
          found <= ceiling,
          f"{found - ceiling} more than when the scale was introduced — "
          f"use the scale, or lower the ceiling if you migrated something")

print("\nNo page keeps its own copy of the status colours")
# FOUR COPIES EXISTED: attendance and leave in the admin panel, the employee
# leave page, and payroll. One green meant "on time", "approved" and
# "finalised" depending on which screen you were looking at, and a status
# missing from one dict read as plain grey — which is how CANCELLED looked
# identical to a row that had failed to load.
STATUS_WORDS = ("PENDING", "APPROVED", "REJECTED", "REVOKED", "FINALIZED")
for path, text in ui_sources():
    name = os.path.basename(path)
    body = "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("#"))
    # A dict literal mapping a status word straight to a colour.
    guilty = [w for w in STATUS_WORDS
              if re.search(rf'"{w}"\s*:\s*C[\[.]', body)]
    check(f"{name} does not map statuses to colours itself",
          not guilty, ", ".join(guilty))

check("and the leave page's frozen import-time table is gone",
      "STATUS_COLOUR" not in ALL,
      "built at import, it kept the dark palette through a theme switch")

print("\nNo emoji anywhere in the interface")
# WHY THEY HAD TO GO. Emoji are drawn by the operating system's colour font:
# their own palette, their own weight, their own baseline. Fifteen of them in
# a menu is fifteen illustration styles in a column, and macOS and Windows
# draw them differently, so the two builds did not match. They are replaced
# by Lucide stroke icons that take the colour of whatever they sit on.
#
# Comments are excluded — the notes explaining what was removed name the
# glyphs, and a test that cannot tell code from a comment forbids the
# explanation.
import unicodedata  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF"
                   "\U0001F000-\U0001F0FF\U00002B00-\U00002BFF]")
# ONE EXCEPTION, AND IT IS NAMED. Chat reactions ARE emoji — the glyph is the
# content, not an icon standing in for a concept. A line may carry them if it
# says so with this marker, which keeps the exception greppable and stops it
# from quietly becoming "emoji are fine again".
ALLOWED = "# reaction content"

offenders = []
for path, text in ui_sources():
    for number, line in enumerate(_without_prose(text).splitlines(), 1):
        if ALLOWED in line:
            continue
        for glyph in EMOJI.findall(line):
            offenders.append(f"{os.path.basename(path)}:{number} {glyph}")
check("the interface contains no emoji", not offenders,
      "; ".join(offenders[:6]) + (" …" if len(offenders) > 6 else ""))

from client.presentation.widgets import icons as _icons  # noqa: E402
check("every navigation key has an icon",
      all(_icons.known(k) for k in _icons.BY_KEY),
      ", ".join(k for k in _icons.BY_KEY if not _icons.known(k)))
check("the icons are stroked, one weight",
      'stroke-width="1.75"' in open(
          os.path.join(ROOT, "client", "presentation", "widgets", "icons.py"),
          encoding="utf-8").read())

print("\nNothing draws an empty icon slot")
# THE SHAPE OF THIS BUG. Removing the emoji left the CONTAINERS behind: a
# tinted 30px square with nothing in it, a button with no label and no icon.
# Every Configuration section, two dashboard cards and the chat composer's
# attach button became small black holes, and each was reported separately
# because there is nothing about an empty box that says what it used to be.
#
# A widget that is given a fixed size and a background is claiming to show
# something. This looks for the ones that then show nothing.
slots = []
for path, text in ui_sources():
    body = _without_prose(text)
    # QLabel("") ... setFixedSize(...) ... a background — within a few lines
    for match in re.finditer(r'QLabel\(""\)(.{0,240})', body, re.S):
        tail = match.group(1)
        if "setFixedSize" in tail and "background:" in tail \
                and "setPixmap" not in tail:
            slots.append(os.path.basename(path))
    # A button given a FIXED SIZE with neither text nor icon. One whose text
    # arrives later (the pinned-messages shelf) is not a hole — it is empty
    # only until there is something to say, and it hides itself meanwhile.
    for match in re.finditer(r'QPushButton\(""\)(.{0,200})', body, re.S):
        tail = match.group(1)
        if ("setFixedWidth" in tail or "setFixedSize" in tail) \
                and "setIcon" not in tail:
            slots.append(f"{os.path.basename(path)} (button)")
check("no widget reserves space for an icon it never sets",
      not slots, ", ".join(sorted(set(slots))))

# And the section builder cannot be handed a blank icon again.
panel = open(os.path.join(ROOT, "client", "presentation", "windows",
                          "admin_config_panel.py"), encoding="utf-8").read()
check("Configuration sections all name a real icon",
      '_build_section(\n            ""' not in panel
      and '_build_section("", ' not in panel,
      "a section with an empty icon draws a tinted square with a hole in it")

print("\nCards do not style what is inside them")
# A plain `QFrame{...}` rule in Qt reaches every QFrame INSIDE the widget as
# well, so a card's border and radius were being inherited by its own
# dividers — a one-pixel line drawn as a small bordered box. It was found on
# a screenshot, fixed in one of the three card builders, and left in the
# other two for months. It cannot be seen in a diff, so it is checked here.
for path, text in ui_sources():
    name = os.path.basename(path)
    body = "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("#"))
    unscoped = re.findall(r'QFrame\{\{background:[^}]*border-radius', body)
    check(f"{name} scopes its card style to one frame",
          not unscoped,
          f"{len(unscoped)} unscoped QFrame rule(s) — use widgets/card.py")

card_source = open(os.path.join(ROOT, "client", "presentation", "widgets",
                                "card.py"), encoding="utf-8").read()
check("the shared card uses a unique objectName",
      "setObjectName(name)" in card_source and "QFrame#" in card_source)
check("and takes its colours rather than assuming a palette",
      "bg: str" in card_source and "border: str" in card_source,
      "the two panels have different palettes; hard-coding one restyles half "
      "the product")

print("\nThe toast says what it is for")
toast_source = open(os.path.join(ROOT, "client", "presentation", "widgets",
                                 "toast.py"), encoding="utf-8").read()
check("it holds its animation on the instance",
      "self._animation = animation" in toast_source,
      "an animation that goes out of scope is collected mid-flight and "
      "the toast never appears")
check("it parents to the window, not the caller",
      "parent.window()" in toast_source,
      "otherwise it vanishes when the page under it is swapped")
check("it caps how many stack up", "len(stack) >= 3" in toast_source)
check("and it is documented as being for successes only",
      "WHEN NOT TO USE IT" in toast_source,
      "an error that disappears after four seconds is worse than no error")

print("\nEvery theme name a widget uses is a name the theme HAS")
# THE BUG THIS EXISTS FOR, AND IT WAS MINE, TWICE. A button was styled with
# C.DANGER, which does not exist — the palette calls it C.RED. Nothing raises
# on import: the attribute is read when the widget is BUILT, so it waits until
# somebody opens that menu, and then the whole page dies. The same shape once
# ate the header chip via SessionManager.employee_name.
#
# Python cannot catch this and a diff does not show it, so it is checked here
# against the real classes.
import re as _re
from client.presentation.theme import C as _C, Type as _Type, Space as _Space, Radius as _Radius
_have = {"C": dir(_C), "Type": dir(_Type), "Space": dir(_Space), "Radius": dir(_Radius)}
_pattern = _re.compile(r"\b(C|Type|Space|Radius)\.([A-Z][A-Z0-9_]*)\b")
_wrong = []
for _root, _dirs, _files in os.walk(os.path.join(ROOT, "client")):
    if "__pycache__" in _root:
        continue
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _path = os.path.join(_root, _f)
        _src = open(_path, encoding="utf-8").read()
        # THE ADMIN CONSOLE'S `C` IS A DICT, NOT THE PALETTE CLASS. It binds
        # C = ADMIN at module level and subscripts it: C["text_primary"].
        # The first version of this check missed that entirely — it looked up
        # C.TEXT against the CLASS, found TEXT there, and passed while the
        # console crashed on that very line. A test that reads the wrong
        # object is worse than no test, because it is believed.
        _dict_C = "\nC = _THEME_ADMIN" in _src
        for _i, _line in enumerate(_src.split("\n"), 1):
            for _cls, _attr in _pattern.findall(_line):
                if _cls == "C" and _dict_C:
                    _wrong.append(f"{os.path.relpath(_path, ROOT)}:{_i} "
                                  f"C.{_attr} (here C is a dict — use C[\"...\"])")
                elif _attr not in _have[_cls]:
                    _wrong.append(f"{os.path.relpath(_path, ROOT)}:{_i} {_cls}.{_attr}")
check("no widget names a colour, size or spacing the theme does not define",
      not _wrong, "; ".join(_wrong[:4]))

print("\nArrows in buttons are drawn icons, not text characters")
# "↻ Refresh" and "← Prev" are glyphs out of the interface font: they sit on
# the baseline instead of centred, take whatever weight the font gives them,
# and are simply absent on a machine whose font lacks them. The brief asked
# for Lucide throughout, and a control is exactly where that matters.
# Widened past the arrows once the sweep found status dots and ticks doing
# the same job: "●" and "✓" are characters out of the text font, sized by
# whatever font the widget happened to use and absent where the font lacks
# them. Interface marks are drawn — an icon, or a rounded label for a dot.
_glyphs = _re.compile(r"[←→↑↓↻↺↩↪✖✔✓✕✗☰⋮⋯●○■□▪▫★☆✦⚠⚡✱✚✘]")
_found = []
for _root, _dirs, _files in os.walk(os.path.join(ROOT, "client")):
    if "__pycache__" in _root:
        continue
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _path = os.path.join(_root, _f)
        _src = open(_path, encoding="utf-8").read()
        for _node in ast.walk(ast.parse(_src)):
            # Only what is DRAWN: the text handed to a widget. Arrows in
            # comments, docstrings and help sentences are prose and stay.
            _called = None
            if isinstance(_node, ast.Call):
                if isinstance(_node.func, ast.Name):
                    _called = _node.func.id
                elif isinstance(_node.func, ast.Attribute):
                    _called = _node.func.attr
            if _called in ("QPushButton", "QLabel", "QAction", "QToolButton",
                           "QCheckBox", "QRadioButton", "QTableWidgetItem",
                           "QListWidgetItem", "setText", "setPlaceholderText",
                           "setToolTip", "setWindowTitle"):
                for _arg in _node.args:
                    if isinstance(_arg, ast.Constant) and isinstance(_arg.value, str) \
                            and _glyphs.search(_arg.value):
                        _found.append(f"{os.path.relpath(_path, ROOT)}: {_arg.value[:30]}")
check("no arrow, tick or dot characters inside anything drawn",
      not _found, "; ".join(_found[:4]))

print("\nNothing calls a method the widget does not have")
# THE THIRD TIME THIS SHAPE OF MISTAKE LANDED. C.DANGER on a class that calls
# it RED; SessionManager.employee_name when the attribute is full_name; and
# then setIcon() on a QLabel, which has no such method — a status chip that
# would have died the moment it repainted. Python raises none of these until
# the line runs, and the line only runs when somebody opens that page.
#
# So: for every `x = QSomething(...)` in a function, check the methods called
# on `x` against the real Qt class. Deliberately narrow — it follows plain
# local assignments only, which is exactly where this keeps happening.
from PySide6 import QtWidgets as _QtW

_missing = []
for _path, _text in ui_sources():
    try:
        _tree = ast.parse(_text)
    except SyntaxError:
        continue
    def _name_of(_node):
        """"x" for a local, "self._x" for an attribute — or None."""
        if isinstance(_node, ast.Name):
            return _node.id
        if isinstance(_node, ast.Attribute) and isinstance(_node.value, ast.Name):
            return f"{_node.value.id}.{_node.attr}"
        return None

    # PER FILE, NOT PER FUNCTION. `self._status_chip = QLabel()` is built in
    # one method and used in another — which is exactly where the setIcon()
    # slipped through, so a per-function scope would have missed the very bug
    # this was written for. Locals are still scoped, by carrying the function
    # name in the key.
    _types, _unknown = {}, set()
    for _fn in [n for n in ast.walk(_tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for _node in ast.walk(_fn):
            if isinstance(_node, ast.Assign) and len(_node.targets) == 1 \
                    and isinstance(_node.value, ast.Call) \
                    and isinstance(_node.value.func, ast.Name) \
                    and _node.value.func.id.startswith("Q"):
                _klass = getattr(_QtW, _node.value.func.id, None)
                _target = _name_of(_node.targets[0])
                if _klass is not None and _target:
                    _key = _target if "." in _target else f"{_fn.name}:{_target}"
                    # A name rebound to two different widgets tells us
                    # nothing reliable, so it is dropped rather than guessed.
                    _types[_key] = None if _key in _types and _types[_key] not in (None, _klass) else _klass
            # REBOUND TO SOMETHING THIS CANNOT SEE — `header = QHBoxLayout()`
            # early in a method and `header = table.horizontalHeader()` later
            # is a real pattern, and believing the first binding turned the
            # second one's perfectly good calls into four false failures. A
            # check that cries wolf gets deleted, so any name that is ever
            # assigned something unrecognised is forgotten outright. Walk
            # order is not source order, so this cannot be decided in passing
            # — it is collected and applied afterwards.
            elif isinstance(_node, ast.Assign) and len(_node.targets) == 1:
                _target = _name_of(_node.targets[0])
                if _target:
                    _unknown.add(_target if "." in _target
                                 else f"{_fn.name}:{_target}")
            # A LOOP VARIABLE IS A BINDING TOO. `row = QHBoxLayout()` in one
            # method and `for i, row in enumerate(rows)` in another put a
            # dict where a layout was, and the check reported QHBoxLayout.get.
            elif isinstance(_node, (ast.For, ast.AsyncFor, ast.comprehension,
                                    ast.withitem, ast.ExceptHandler)):
                _bound = getattr(_node, "target", None) or getattr(
                    _node, "optional_vars", None)
                if isinstance(_node, ast.ExceptHandler) and _node.name:
                    _unknown.add(f"{_fn.name}:{_node.name}")
                for _leaf in ast.walk(_bound) if _bound is not None else []:
                    _leaf_name = _name_of(_leaf)
                    if _leaf_name:
                        _unknown.add(_leaf_name if "." in _leaf_name
                                     else f"{_fn.name}:{_leaf_name}")
    for _key in _unknown:
        _types.pop(_key, None)

    for _fn in [n for n in ast.walk(_tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for _node in ast.walk(_fn):
            if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute):
                _owner = _name_of(_node.func.value)
                if not _owner:
                    continue
                _key = _owner if "." in _owner else f"{_fn.name}:{_owner}"
                _klass = _types.get(_key)
                if _klass is not None and not hasattr(_klass, _node.func.attr):
                    _missing.append(
                        f"{os.path.relpath(_path, ROOT)}:{_node.lineno} "
                        f"{_klass.__name__}.{_node.func.attr}()")
check("every method called on a freshly built widget exists on it",
      not _missing, "; ".join(_missing[:4]))

print("\nTooltips are styled, and they wrap")
# TWO BUGS, BOTH SEEN ON A REAL MAC AND NEITHER OFFSCREEN.
#
# 1. A tooltip is its own top-level window, so a panel's stylesheet never
#    reaches it — the rule has to go on the QApplication. set_theme() does
#    that, but main.py loads the saved theme BEFORE the application exists,
#    so on a session where nobody touches the toggle it was applied to
#    nothing at all. Under a light-themed app on a Mac in dark mode the tip
#    came up as a black box with dark text.
# 2. A plain-text tooltip never wraps. The Payroll nav sentence measured 953
#    pixels wide — a band of text across the whole window.
_main = open(os.path.join(ROOT, "client", "main.py"), encoding="utf-8").read()
check("main.py styles tooltips once the application exists",
      "apply_tooltip_style()" in _main,
      "set_theme runs before QApplication, so it cannot be the only caller")
_app_line = _main.find("app = QApplication(")
check("and does it AFTER the application is made, not before",
      _app_line != -1 and _main.find("apply_tooltip_style()", _app_line) > _app_line)

from client.presentation.theme import tip as _tip
_long = ("Salaries, and a month's pay built from attendance and approved "
         "leave. A finalised month stops moving.")
check("a long tooltip is turned into something that wraps",
      "<table" in _tip(_long), _tip(_long)[:40])
check("a short one is left as plain text", _tip("Attach a file") == "Attach a file")
check("and mark-up characters in the text are escaped",
      "&amp;" in _tip("R&D " + _long), "an & would otherwise open a bad entity")

print("\nall design system checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
