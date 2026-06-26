# Employee Tracking System (ETS)

## Architecture
- **Server**: Node.js + Express + PostgreSQL (VPS pe run karta hai)
- **Client**: Python + PySide6 (employee ke machine pe install hota hai)

---

## 🛠 Bug Fixes (this version)

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `client/services/logger_service.py` | DB code file `open()` context ke andar tha — indent bug | DB code file close ke baad |
| 2 | `client/services/api_service.py` | Auth headers missing the | `auth_token` parameter add kiya |
| 3 | `client/application/managers/session_manager.py` | `_generate_device_id = None` typo — device_id kabhi set nahi hota tha | `device_id = None` |
| 4 | `server/controllers/attendance.controller.js` | `total_hours` string ko PostgreSQL `INTERVAL` mein cast nahi kiya | `$2::interval` cast |
| 5 | `client/application/managers/screenshot_manager.py` | Encrypted `.enc` file upload ho rahi thi — server PNG expect karta hai | PNG bytes in-memory upload, `.enc` sirf local |
| 6 | `server/migrations/add_verbose_logging.sql` | `verbose_logging` column `employee_configs` mein missing tha | Migration + schema mein add |
| 7 | `client/application/managers/config_sync_manager.py` | `force_logout` path mein `return config` missing tha | Return statement add kiya |
| 8 | `server/routes/screenshot.routes.js` | `verifyToken` double apply (server.js + routes file dono mein) | Routes file se remove kiya |
| 9 | `ets.sql` | Schema incomplete — `verbose_logging` missing, no migration support | Complete schema with ALTER TABLE migrations |

---

## 🚀 Server Setup (VPS)

### 1. PostgreSQL setup
```bash
# DB create karo
psql -U postgres -c "CREATE DATABASE ets_db;"

# Schema import karo (fresh install)
psql -U postgres -d ets_db -f ets.sql

# Ya existing DB pe sirf migrations run karo
psql -U postgres -d ets_db -f server/migrations/add_verbose_logging.sql
psql -U postgres -d ets_db -f server/migrations/add_admin_config.sql
```

### 2. Server configure karo
```bash
cd server
cp .env.example .env
# .env mein apni values bharo:
#   DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
#   JWT_SECRET (strong random string)
#   PORT (default 5000)
#   ALLOWED_ORIGIN
```

### 3. Dependencies install karo
```bash
cd server
npm install
```

### 4. Server start karo
```bash
# Development
npm run dev

# Production (PM2 recommend karta hoon)
npm install -g pm2
pm2 start server.js --name ets-server
pm2 save
pm2 startup
```

### 5. Admin user banana
```bash
# bcrypt hash generate karo pehle
node -e "const b=require('bcryptjs'); b.hash('YourPassword123',10).then(h=>console.log(h))"

# Phir psql mein insert karo
psql -U postgres -d ets_db -c "
INSERT INTO employees (employee_id, username, password, role)
VALUES ('ADMIN001', 'admin', '<hash_from_above>', 'admin');
"
```

---

## 💻 Client Setup (Employee Machine)

### Requirements
```bash
pip install -r requirements.txt
```

### Configure karo
```bash
# client/ folder mein .env file banana
APP_NAME=ETS
API_BASE_URL=http://<your-vps-ip>:<port>/api
SCREENSHOT_MIN_INTERVAL=180
SCREENSHOT_MAX_INTERVAL=600
IDLE_THRESHOLD=60
```

### Run karo
```bash
python -m client.main
```

---

## 📁 Project Structure
```
├── server/
│   ├── server.js              # Entry point
│   ├── config/db.js           # PostgreSQL pool
│   ├── middleware/
│   │   ├── auth.middleware.js # JWT verify
│   │   └── admin.middleware.js
│   ├── routes/                # Express routes
│   ├── controllers/           # Business logic
│   ├── migrations/            # DB migrations
│   └── uploads/screenshots/   # Screenshot storage
├── client/
│   ├── main.py                # Entry point
│   ├── application/           # Business logic
│   ├── presentation/          # UI (PySide6)
│   ├── services/              # API + settings
│   ├── infrastructure/        # DB + encryption
│   └── core/config/           # Config + constants
├── ets.sql                    # Complete DB schema
└── requirements.txt
```
