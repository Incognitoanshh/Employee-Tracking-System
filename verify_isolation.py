#!/usr/bin/env python3
"""Proves per-employee config isolation.

Ek employee ka idle threshold / shift badalte hain, aur dikhate hain ki
global aur baaki employees BILKUL waise hi rehte hain.

Usage:
    ./verify_isolation.py <admin-username> <password>                  # list employees
    ./verify_isolation.py <admin-username> <password> <target> <ctrl>  # full test

Note: login USERNAME leta hai, config employee_id pe chalti hai. Dono alag.
"""
import json
import sys
import time
import urllib.error
import urllib.request

API = "http://65.21.212.85:8000/api"


def call(method, path, token=None, body=None, retries=4):
    """India->Finland transit pe abhi ~40% packet loss hai, is liye har
    request ko retry karte hain. Warna test network ki wajah se toot jaata
    hai aur lagta hai ki app me bug hai."""
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(API + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except Exception:
                return e.code, {"raw": raw.decode(errors="replace")[:200]}
        except Exception as e:
            last = e
            if attempt < retries:
                print(f"   … network timeout, retry {attempt}/{retries - 1}")
                time.sleep(2 * attempt)
    print(f"❌ Server reach nahi ho raha ({API}) after {retries} tries: {last}")
    sys.exit(1)


def snapshot(token, emp):
    st, d = call("GET", f"/admin/config/{emp}", token)
    if st != 200:
        return None, f"HTTP {st} {d}"
    c = d.get("config", {})
    return {
        "idle":     c.get("idle_threshold_seconds"),
        "start":    str(c.get("shift_start"))[:5],
        "end":      str(c.get("shift_end"))[:5],
        "min":      c.get("screenshot_min_minutes"),
        "max":      c.get("screenshot_max_minutes"),
        "count":    c.get("screenshot_count"),
        "upload":   c.get("upload_interval_minutes"),
        "inherit":  c.get("inherited"),
    }, None


def line(emp, s):
    return (f"   {emp:<12} idle={str(s['idle']):>4}s  shift={s['start']}-{s['end']}  "
            f"ss={s['min']}/{s['max']}/{s['count']}  upload={s['upload']}  "
            f"inherited={s['inherit']}")


def main():
    if len(sys.argv) not in (3, 5):
        print(__doc__)
        sys.exit(2)
    user, pw = sys.argv[1], sys.argv[2]
    target = sys.argv[3] if len(sys.argv) == 5 else None
    control = sys.argv[4] if len(sys.argv) == 5 else None

    st, d = call("POST", "/auth/login", body={"username": user, "password": pw})
    token = d.get("token")
    if not token:
        print(f"❌ Login fail (HTTP {st}): {d}")
        sys.exit(1)
    print(f"✅ Login ok as '{user}'")

    st, d = call("GET", "/admin/employees?limit=200", token)
    rows = d.get("data") or d.get("employees") or []
    print("\n── EMPLOYEES ──")
    for r in rows:
        print(f"   {str(r.get('employee_id')):<12} {str(r.get('username')):<20} {r.get('role')}")
    if not rows:
        print(f"   (kuch nahi mila) HTTP {st} {d}")

    if not target:
        print("\nAb upar se do employee_id chunkar dobara chalao:")
        print(f"   ./verify_isolation.py {user} '<password>' <target> <control>")
        return

    others = [str(r.get("employee_id")) for r in rows
              if str(r.get("employee_id")) not in (target,)]
    watch = ["global"] + others

    before = {}
    for e in [target] + watch:
        s, err = snapshot(token, e)
        if err:
            print(f"❌ {e}: {err}")
            sys.exit(1)
        before[e] = s

    print("\n── BEFORE ──")
    print(line(target, before[target]) + "   ← target")
    for e in watch:
        print(line(e, before[e]))

    print(f"\n── {target} pe idle=10, shift 11:00-20:00 set kar rahe hain ──")
    st, d = call("POST", "/admin/config", token, {
        "employee_id": target,
        "screenshot_min_minutes":  before[target]["min"] or 3,
        "screenshot_max_minutes":  before[target]["max"] or 10,
        "screenshot_count":        before[target]["count"] or 3,
        "upload_interval_minutes": before[target]["upload"] or 60,
        "idle_threshold_seconds":  10,
        "verbose_logging":         False,
        "shift_start":             "11:00",
        "shift_end":               "20:00",
    })
    print(f"   save: {'✅' if d.get('success') else '❌'} HTTP {st} {d}")

    after = {}
    for e in [target] + watch:
        after[e], _ = snapshot(token, e)

    print("\n── AFTER ──")
    print(line(target, after[target]) + "   ← target")
    for e in watch:
        print(line(e, after[e]))

    print("\n── VERDICT ──")
    ok = True
    t = after[target]
    if t["idle"] == 10 and t["start"] == "11:00" and t["end"] == "20:00":
        print(f"   ✅ {target} pe naya config laga (idle=10, 11:00-20:00)")
    else:
        print(f"   ❌ {target} pe config NAHI laga: {t}")
        ok = False

    for e in watch:
        if before[e] == after[e]:
            print(f"   ✅ {e:<12} bilkul unchanged")
        else:
            print(f"   ❌ {e:<12} BADAL GAYA!")
            print(f"        before: {before[e]}")
            print(f"        after : {after[e]}")
            ok = False

    print("\n" + ("🎉 ISOLATION PASS — change sirf target pe laga."
                 if ok else "🚨 ISOLATION FAIL — upar ❌ dekho."))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
