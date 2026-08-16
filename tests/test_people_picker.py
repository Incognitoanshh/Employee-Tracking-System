"""
Choosing twenty people out of a hundred.

This picker is how a team gets its members and how a channel decides who can
see it. It was a plain checkable list: to put twenty people in a channel out
of a hundred somebody had to scroll and read every row, twice, and hope. At
six employees that looks perfectly fine, which is why it shipped — the first
real company makes it unusable.

THE ONE THING THAT MUST BE TRUE. Filtering hides rows; it never unticks one.
You search "priya", tick her, search "amit", tick him, and both are still
ticked when you press Create. A picker that quietly forgot the first person
when you typed the second name would be worse than having no search at all,
because you would not notice until somebody was missing from a channel they
were meant to be in.

Run:  python3 tests/test_people_picker.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_picker_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def main():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import Qt

    QApplication.instance() or QApplication([])

    from client.presentation.windows.admin_teams_tab import _pick_people, _checked_ids

    people = [
        {"employee_id": "EMP001", "name": "Admin", "username": "amazeinternet"},
        {"employee_id": "EMP002", "name": "Ansh", "username": "ansh"},
        {"employee_id": "TEST001", "name": "raju", "username": "raju"},
        {"employee_id": "TEST002", "name": "exo", "username": "exo"},
        {"employee_id": "AMZ004", "name": "Shailabh", "username": "shailabh"},
        {"employee_id": "AMZ005", "name": "Priya Menon", "username": "priya"},
        {"employee_id": "AMZ006", "name": "Amit Kumar", "username": "amit"},
    ]

    host = QWidget()
    picker = _pick_people(host, people)

    print("Everybody is listed, and nobody is ticked to begin with")
    check("every person is in the list", picker.count() == len(people), str(picker.count()))
    check("none is preselected", _checked_ids(picker) == [], str(_checked_ids(picker)))

    print("\nSearching narrows what is shown")
    picker.search.setText("priya")
    shown = [picker.item(i).text() for i in range(picker.count())
             if not picker.item(i).isHidden()]
    check("only the match is shown", len(shown) == 1 and "Priya" in shown[0], str(shown))
    check("the rest are hidden, not removed", picker.count() == len(people), str(picker.count()))

    print("\nSearching by employee ID works too — it is how people are actually found")
    picker.search.setText("AMZ006")
    shown = [picker.item(i).text() for i in range(picker.count())
             if not picker.item(i).isHidden()]
    check("the id finds the person", len(shown) == 1 and "Amit" in shown[0], str(shown))

    print("\nTHE ONE THAT MATTERS: a tick survives the next search")
    picker.search.setText("priya")
    for i in range(picker.count()):
        if not picker.item(i).isHidden():
            picker.item(i).setCheckState(Qt.CheckState.Checked)
    check("Priya is ticked", _checked_ids(picker) == ["AMZ005"], str(_checked_ids(picker)))

    picker.search.setText("amit")
    for i in range(picker.count()):
        if not picker.item(i).isHidden():
            picker.item(i).setCheckState(Qt.CheckState.Checked)
    picked = sorted(_checked_ids(picker))
    check("and Priya is STILL ticked after searching for somebody else",
          picked == ["AMZ005", "AMZ006"],
          f"{picked} — a picker that forgets is worse than one with no search")

    picker.search.setText("")
    check("clearing the search shows everyone again",
          all(not picker.item(i).isHidden() for i in range(picker.count())))
    check("and the selection is untouched", sorted(_checked_ids(picker)) == ["AMZ005", "AMZ006"],
          str(sorted(_checked_ids(picker))))

    print("\nThe count tells you what you have, even while filtered")
    picker.search.setText("zzz-nobody")
    check("a search that matches nobody hides everything",
          all(picker.item(i).isHidden() for i in range(picker.count())))
    check("but still says two are selected",
          "2 selected" in picker.counter.text(), picker.counter.text())
    check("and says how many the search is hiding",
          "hidden" in picker.counter.text(), picker.counter.text())

    print("\nCase does not matter — people type how they type")
    picker.search.setText("PRIYA")
    shown = [picker.item(i).text() for i in range(picker.count())
             if not picker.item(i).isHidden()]
    check("upper case finds her too", len(shown) == 1, str(shown))

    print("\nPreselected members come back ticked")
    picker2 = _pick_people(host, people, {"EMP002", "TEST001"})
    check("the people already in the team are ticked",
          sorted(_checked_ids(picker2)) == ["EMP002", "TEST001"],
          str(sorted(_checked_ids(picker2))))

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all people picker checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
