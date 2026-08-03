# ETS — Manual Test Plan

Har step: **kya karna hai** → **kya dikhna chahiye**. Jo match na kare wo note
kar lena (step number + kya dikha), main fix kar dunga.

Ye wo cheezein cover karta hai jo automated tests verify **nahi** kar sakte —
asli screenshot capture, macOS permission, visual UI, live sync, aur real-time
behaviour.

**Kul time: ~45 minute** (+ 1 shift screenshot spread ke liye)

---

## Session 0 — Prep (5 min)

### 0.1 macOS Screen Recording permission

**System Settings → Privacy & Security → Screen & System Audio Recording**
→ **Terminal** (ya iTerm) ON hona chahiye.

> Agar abhi ON kiya hai to **Terminal poora quit karke dobara kholo** —
> permission tabhi lagti hai.

Iske bina screenshots ya to fail hongi ya sirf wallpaper capture karengi.

### 0.2 Test ke liye shift set karo

Abhi ke time se aage ka shift chahiye, warna screenshots schedule hi nahi hongi.

```bash
ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c "UPDATE employee_configs SET shift_start = make_time(0,1,0), shift_end = make_time(23,59,0), screenshot_count = 8, screenshot_min_minutes = 2, screenshot_max_minutes = 6, idle_threshold_seconds = 60, verbose_logging = false;" -c "SELECT COALESCE(employee_id,(chr(60)||chr(71)||chr(76)||chr(66)||chr(62))) emp, shift_start, shift_end, screenshot_count FROM employee_configs ORDER BY employee_id NULLS FIRST;"'
```

**Expected:** har row `00:01:00 | 23:59:00 | 8`

### 0.3 Log window kholo (alag terminal)

```bash
tail -f ~/Library/Application\ Support/ETS/storage/app.log
```

### 0.4 App chalao (teesra terminal)

```bash
cd "/Users/ansh/Downloads/Employee-Tracking-System-main copy" && python3 -m client.main
```

**Expected:** Login window. Neeche `v2.1.0 · macOS · IST`
*(agar `Windows` likha ho to purana code chal raha hai)*

---

## Session 1 — Login & Employee Panel (10 min)

### 1.1 Galat password

`ansh` / `wrongpass` → **Sign In**

- [ ] Laal error: "Invalid credentials"
- [ ] App crash nahi hui

### 1.2 Brute force protection

Wahi galat password **10 baar** dabao.

- [ ] ~10th attempt pe: "Too many failed login attempts for this account, try after 15 minutes"

> **Ab `admin` se login karke dekho — wo kaam karna chahiye.** Yehi wo fix hai
> jisse poora office lock nahi hota. Phir wapas `ansh` pe aao.
> Lock hata na ho to: `ssh etsadmin@65.21.212.85 'pm2 restart ets-server'`

### 1.3 Sahi login

`ansh` se login.

- [ ] Employee Panel khula
- [ ] Header: avatar + **naam** (ID nahi) + `EMP002 · <designation>`
- [ ] Daayen: `● ONLINE / Tracking Active` + chalti ghadi
- [ ] Sidebar: **6 items** — Dashboard, Attendance, Activity Logs, Screenshots, Settings, Help & Support
- [ ] Sidebar footer: `ETS Client v2.1.0` / `● Connected to Server` / `🔒 AES-256 GCM Encrypted`
- [ ] **Reports item NAHI hona chahiye** (wo admin ka hai)
- [ ] **Take Screenshot / Sync Now buttons NAHI hone chahiye**

Log me:
```
LOGIN SUCCESS : ansh
ScreenshotManager: N screenshots scheduled across shift ... (first HH:MM, last HH:MM)
```

- [ ] `first` aur `last` me **ghanton ka fasla** (sab ek saath nahi)

### 1.4 Today's Overview — 6 cards

- [ ] Tracking Status → `ACTIVE`
- [ ] Activity Status → `WORKING`
- [ ] Internet Status → `CONNECTED` + `Latency: NN ms`
- [ ] Upload Status → `SYNCED`
- [ ] Session Duration → chal raha hai (har second badhta)
- [ ] Screenshots Today → number
- [ ] Har card ke neeche sparkline

### 1.5 Recent Activity

- [ ] Har row me **time** (`HH:MM:SS`) — khali nahi
- [ ] `ScreenshotManager:` / `SchedulerService:` jaisi lines **nahi**
- [ ] `Login Successful` sabse upar
- [ ] Neeche count visible rows se match karta hai

### 1.6 Idle detection

**70 second tak mouse-keyboard bilkul mat chhuo.**

- [ ] Log: `USER IDLE (6x.xs)`
- [ ] Activity Status card → `IDLE` (amber)
- [ ] Mouse hilao → `USER ACTIVE`, card wapas `WORKING`
- [ ] Recent Activity me dono events

---

## Session 2 — Employee ke baaki pages (5 min)

### 2.1 Attendance
- [ ] Rows dikhte hain, time **IST** me (`03 Aug 2026, 09:12:45 AM`)
- [ ] Chalti session pe `● ACTIVE`, Duration `—`
- [ ] Prev/Next + `Page 1 · N total`
- [ ] Date chuno → Search → sirf us din ki rows
- [ ] Clear → sab wapas
- [ ] **Export CSV** → file kholo: time **IST** me, aur **saare pages** (sirf 50 nahi)

### 2.2 Activity Logs
- [ ] Rows newest-first
- [ ] Event dropdown (All / Screenshots / Idle / Active / Sessions) filter karta hai
- [ ] `ScreenshotManager:` jaisi internal lines nahi

### 2.3 Screenshots
- [ ] Apne screenshots ki list (dusron ke nahi)
- [ ] **View** dabao → asli screen dikhni chahiye
  - Blank/wallpaper aaye → step 0.1 permission

### 2.4 Settings
- [ ] **Account**: ID, naam, designation, shift, timezone
- [ ] **Monitoring**: count, interval, idle threshold, `Settings synced: just now`
- [ ] **Sync & Storage**: server URL poora, pending 0, data folder **asli path** (`C:\ETS` nahi)
- [ ] **Open Data Folder** → Finder khulta hai
- [ ] **Security**: `AES-256-GCM · active`, `ETS Client v2.1.0`, `Darwin ...`

### 2.5 Help & Support
- [ ] 6 FAQ + contact card

---

## Session 3 — Live config sync (5 min) ⭐

**Employee panel Settings page pe khula rakho.** Dusre terminal se:

```bash
ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c "UPDATE employee_configs SET screenshot_count = 15, idle_threshold_seconds = 120 WHERE employee_id IS NULL;"'
```

**~10 second ke andar, bina kuch kiye:**
- [ ] Screenshots per shift → `15`
- [ ] Idle threshold → `120 sec`
- [ ] Dono **green highlight** hote hain (~2.5s)
- [ ] `Settings synced: just now`

Wapas:
```bash
ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c "UPDATE employee_configs SET screenshot_count = 8, idle_threshold_seconds = 60 WHERE employee_id IS NULL;"'
```

---

## Session 4 — Admin Panel (15 min)

Employee panel se **Logout** → `admin` se login.

- [ ] Log me `LOGOUT` phir `LOGIN SUCCESS : admin`
- [ ] Admin Console khula (employee panel nahi)

### 4.1 Header & Dashboard
- [ ] `ETS Control Center` + `Real-time Monitoring & Management`
- [ ] `↻ Refresh` · `📥 Export` · `☁ Sync Now`
- [ ] Daayen chip: `EMP001 / Super Admin`
- [ ] Sidebar footer: **asli naam** + `Super Admin · Full Access` *(hardcoded "Administrator" nahi)*
- [ ] Bottom bar: `ETS Admin Console v2.1.0 | ● Connected to Production Server | 🔒 AES-256 GCM`
- [ ] Today's Summary — 5 cards, sparklines ke saath
- [ ] Status tiles — Server / Database / Tracking / Sync
- [ ] 3 bar charts (Last 7 Days)
- [ ] Recent Activity — time ke saath, internal noise nahi
- [ ] Quick Actions ke 5 buttons → sahi page pe le jaate hain

### 4.2 Employees ⭐
- [ ] Role caps: `👑 N/3 super admins   🛡 N/20 admins   👤 N employees`
- [ ] Roles: `👑 Super Admin` / `🛡 Admin` / `Employee`
- [ ] Last Seen: `5 min ago` type *(raw timestamp nahi)*
- [ ] Actions me sirf **2 buttons**: `View` + `Manage ▾` — **overlap nahi**
- [ ] `Manage ▾` → verbose toggle, force logout, role actions, delete

**Apne row (EMP001) pe `Manage ▾`:**
- [ ] Delete **greyed out** + tooltip "promote another admin first"

**Employee row pe:**
- [ ] `⬆ Make admin` maujood
- [ ] `View` → details dialog: Active Time, Idle Time, Screenshots, Logs, latest 10 logs
- [ ] **Active Time realistic ho** (800+ ghante nahi)

**Search:** `ansh` type karo
- [ ] ~0.4s baad filter (server-side)

### 4.3 Configuration ⭐
- [ ] Dropdown: `🌐 Global Default` + har employee `username (ID)` ke saath
- [ ] Global Default ke values DB se match
- [ ] Har row ka description **poora dikhe** (neeche se kata nahi)
- [ ] Header subtitle buttons ke **peeche na jaaye** (`…` se elide ho)

**Per-employee test:**
1. Dropdown se `ansh (EMP002)` chuno
2. Shift `22:00` – `06:00`, count `20`, idle `150` → **Save Config**
3. Dropdown se wapas `Global Default`
   - [ ] Global ki purani values — EMP002 wali **leak nahi**
4. Wapas `ansh` chuno
   - [ ] `22:00`–`06:00`, 20, 150

**Validation:**
- [ ] Shift start `25:99` → Save → `Shift start time must be HH:MM (00:00–23:59)`
- [ ] Idle 150 se upar nahi jaata, count 20 se upar nahi

*(Test ke baad EMP002 ka override hata do — session 0.2 dobara chalao)*

### 4.4 Attendance / Screenshots / Audit Logs
- [ ] **Attendance**: rows IST me, date filter, pagination, Export CSV
- [ ] **Screenshots**: list + **View** → decrypt hoke dikhe
- [ ] **Audit Logs**: rows, filter, Export CSV

### 4.5 Header actions
- [ ] **Refresh** → current page reload
- [ ] **Export** → current page ka CSV (Config page pe saaf message)
- [ ] **Sync Now** → status bar update

---

## Session 5 — Role restrictions (5 min) ⭐

Super admin se ek test admin banao: **Employees → + Add Employee**
`TESTADM` / `testadm` / password / role **admin**

- [ ] Super admin ko dropdown me teeno role dikhte hain

Ab **logout** → `testadm` se login.

- [ ] **+ Add Employee** → role dropdown me sirf `employee`, **disabled**, tooltip
- [ ] Super admin (EMP001) ke row pe `Manage ▾` → verbose/delete **greyed**
- [ ] Dusre admin ke row pe delete **greyed**
- [ ] **Force logout kisi pe bhi chalta hai** (super admin chhod ke)
- [ ] Role change options **bilkul nahi dikhte**

Wapas super admin se login karke `TESTADM` delete kar do.

---

## Session 6 — Force logout live ⭐ (3 min)

**Do machine chahiye** (ya do user accounts). Ek pe `ansh` logged in rakho,
dusre pe `admin`.

Admin se: Employees → `ansh` → `Manage ▾` → `⏻ Force logout`

- [ ] **2–5 second me** employee ki app khud login screen pe chali jaati hai
- [ ] Employee kuch kar nahi sakta, rok nahi sakta
- [ ] Employee turant dobara login kar sakta hai

---

## Session 7 — Screenshots end-to-end ⭐ (shift bhar)

`ansh` se login karke **app chalte rehne do**.

Log dekho:
```bash
tail -f ~/Library/Application\ Support/ETS/storage/app.log | grep -E "SCREENSHOT|capture failed"
```

- [ ] `SCREENSHOT CAPTURED : ...enc (NNN KB)` — **size log me dikhta hai**
- [ ] Size **~150–500 KB** *(3 MB aaye to compression nahi laga)*
- [ ] `capture failed` nahi

**Disk cleanup (naya fix):**
```bash
ls -1 ~/Library/Application\ Support/ETS/storage/screenshots/*.enc 2>/dev/null | wc -l
```
- [ ] Upload safal hone ke baad ye count **badhta nahi rehta** — files delete hoti hain

**Admin panel se:**
- [ ] Screenshots tab me naya capture dikhe
- [ ] **View** → asli screen, padhne laayak

**Poori shift ke baad:**
```bash
ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c "SELECT employee_id, COUNT(*) shots, MIN(created_at::time) first, MAX(created_at::time) last FROM screenshots WHERE created_at > NOW() - INTERVAL '"'"'1 day'"'"' GROUP BY 1;"'
```
- [ ] `first` → `last` me **ghanton ka fasla** *(20 min me sab nahi)*

---

## Session 8 — Offline behaviour (5 min)

App chalte hue **Wi-Fi band karo**.

- [ ] ~15s me Internet Status → `OFFLINE` (laal)
- [ ] Upload Status → `QUEUED`
- [ ] Sidebar → `● Server unreachable`
- [ ] **App crash nahi hoti**, UI freeze nahi hoti

Wi-Fi wapas on:
- [ ] `CONNECTED` + `SYNCED` wapas
- [ ] Settings me pending counts 0 pe wapas

**Logout test:** offline rehte hue **Logout** dabao
- [ ] Crash nahi (yahi wo QThread race fix hai)
- [ ] Login screen aata hai

---

## Session 9 — Restart & persistence (3 min)

- [ ] App band (Cmd+Q) → dobara `python3 -m client.main` → session yaad ya login screen
- [ ] Login → Attendance me nayi row
- [ ] Tray icon: right-click → Logs / Settings / Quit
- [ ] Window band karo (X) → app tray me chalti rahe

---

## Reporting

Jo fail ho:
```
Step: 4.3
Kya kiya: ansh chuna, shift 22:00-06:00 save kiya
Expected: global unchanged
Actual: global bhi 22:00 ho gaya
```

Log ke saath aur accha:
```bash
tail -50 ~/Library/Application\ Support/ETS/storage/app.log
```
