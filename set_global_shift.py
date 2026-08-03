#!/usr/bin/env python3
"""Global default shift set karta hai.

Sirf GLOBAL row badalta hai. Jin employees ka apna override hai
(inherited=False) unko haath nahi lagata — un pe koi asar nahi.

Usage:
    ./set_global_shift.py <admin-username> <password> 09:00 18:00
    ./set_global_shift.py <admin-username> <password>          # sirf dikhao
"""
import json, re, sys, time, urllib.error, urllib.request

API = "http://65.21.212.85:8000/api"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def call(method, path, token=None, body=None, retries=4):
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
                print(f"   … network timeout, retry {attempt}")
                time.sleep(2 * attempt)
    print(f"❌ Server reach nahi ho raha: {last}")
    sys.exit(1)


def show(token, emp):
    _, d = call("GET", f"/admin/config/{emp}", token)
    c = d.get("config", {})
    return (f"   {emp:<12} shift={str(c.get('shift_start'))[:5]}-"
            f"{str(c.get('shift_end'))[:5]}  idle={c.get('idle_threshold_seconds')}s"
            f"  inherited={c.get('inherited')}")


def main():
    if len(sys.argv) not in (3, 5):
        print(__doc__); sys.exit(2)
    user, pw = sys.argv[1], sys.argv[2]
    new = sys.argv[3:5] if len(sys.argv) == 5 else None
    if new and not all(TIME_RE.match(x) for x in new):
        print(f"❌ Time HH:MM (24-hour) hona chahiye, mila: {new}"); sys.exit(2)

    _, d = call("POST", "/auth/login", body={"username": user, "password": pw})
    token = d.get("token")
    if not token:
        print(f"❌ Login fail: {d}"); sys.exit(1)

    _, d = call("GET", "/admin/employees?limit=200", token)
    emps = [str(r.get("employee_id")) for r in (d.get("data") or [])]

    print("── ABHI ──")
    print(show(token, "global"))
    for e in emps:
        print(show(token, e))

    if not new:
        print("\nBadalne ke liye: ./set_global_shift.py <user> '<pw>' 09:00 18:00")
        return

    _, g = call("GET", "/admin/config/global", token)
    cfg = g.get("config", {})
    print(f"\n── GLOBAL ko {new[0]}-{new[1]} set kar rahe hain ──")
    _, r = call("POST", "/admin/config", token, {
        "employee_id": None,
        "screenshot_min_minutes":  cfg.get("screenshot_min_minutes", 3),
        "screenshot_max_minutes":  cfg.get("screenshot_max_minutes", 10),
        "screenshot_count":        cfg.get("screenshot_count", 3),
        "upload_interval_minutes": cfg.get("upload_interval_minutes", 60),
        "idle_threshold_seconds":  cfg.get("idle_threshold_seconds", 60),
        "verbose_logging":         bool(cfg.get("verbose_logging", False)),
        "shift_start": new[0], "shift_end": new[1],
    })
    print(f"   {'✅' if r.get('success') else '❌'} {r}")

    print("\n── AB ──")
    print(show(token, "global"))
    for e in emps:
        print(show(token, e))
    print("\ninherited=True wale global se le rahe hain (badle).")
    print("inherited=False wale apne override pe hain (NAHI badle).")


if __name__ == "__main__":
    main()
