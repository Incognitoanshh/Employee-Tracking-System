"""
Every widget a tab touches, that tab actually has.

THE BUG THIS EXISTS FOR, AND IT WAS MINE. A find-and-replace that looked
harmless copied three lines resetting a dropdown into the Screenshots tab,
which has no such dropdown. Nothing raised on import, nothing raised on
build, and nothing raised on load — it raised only when somebody pressed
Clear on that one page. Python cannot see it, a diff does not show it, and
the tests at the time all passed.

So this walks the panel's tab classes and checks that every `self._x` READ in
a method is `self._x` ASSIGNED somewhere in the same class. It is a coarse
rule and it finds exactly this shape of mistake.

Run:  python3 tests/test_panel_attributes.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TARGETS = [
    os.path.join(ROOT, "client", "presentation", "windows", "admin_config_panel.py"),
    os.path.join(ROOT, "client", "presentation", "windows", "employee_panel.py"),
    os.path.join(ROOT, "client", "presentation", "windows", "leave_page.py"),
    os.path.join(ROOT, "client", "presentation", "windows", "payroll_page.py"),
]

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


def attribute_names(node, store: bool):
    """Every self.<name> in `node`, either assigned to or read from."""
    found = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        if not (isinstance(child.value, ast.Name) and child.value.id == "self"):
            continue
        is_store = isinstance(child.ctx, (ast.Store, ast.Del))
        if is_store == store:
            found.add(child.attr)
    return found


for path in TARGETS:
    tree = ast.parse(open(path, encoding="utf-8").read())
    name = os.path.basename(path)
    for klass in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        assigned = attribute_names(klass, store=True)
        read = attribute_names(klass, store=False)

        # Inherited members are legitimately read without being assigned here,
        # and so is anything Qt provides. Only underscore-prefixed names that
        # look like this file's own widgets are considered.
        bases = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                 for b in klass.bases}
        inherits_local = any(base.startswith("_") or base in
                             ("BasePage", "_TablePage", "Card", "PageHeader")
                             for base in bases)
        if inherits_local:
            continue

        suspicious = sorted(
            attr for attr in read - assigned
            if attr.startswith("_")
            and not attr.startswith("__")
            # Qt's own protected members and methods defined on the class.
            and attr not in {n.name for n in klass.body
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        )
        check(f"{name}::{klass.name} reads only what it sets",
              not suspicious,
              ", ".join(suspicious))

print("\nall panel attribute checks passed" if failures == 0
      else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
