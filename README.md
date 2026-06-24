# 🚀 Employee Tracking System (ETS)

A secure enterprise-grade Employee Tracking System built with **Python (PySide6)**, **Node.js**, **PostgreSQL**, and **SQLite**.

ETS helps organizations monitor employee activity, attendance, screenshots, idle time, and productivity through a centralized admin dashboard.

---

## ✨ Features

### 👨‍💼 Employee Side

* Secure Login & Logout
* Attendance Tracking
* Active Time Monitoring
* Idle Time Detection
* Automated Screenshot Capture
* Screenshot Encryption
* Background Screenshot Upload
* Local Offline Storage
* Auto Sync When Network Restores
* Session Tracking
* Activity Logging

---

### 🛠 Admin Side

* Dashboard Analytics
* Employee Management
* Add Employees
* Delete Employees
* Employee Details View
* Online / Offline Monitoring
* Attendance Monitoring
* Screenshot Monitoring
* Activity Log Monitoring
* CSV Export
* PDF Export
* Force Logout
* Productivity Insights

---

## 🔐 Security Features

* JWT Authentication
* Password Hashing (bcrypt)
* Screenshot Encryption (AES-GCM)
* Role-Based Access Control
* Session Validation
* Single Active Session Enforcement
* Screenshot Ownership Validation
* Secure API Access

---

## 🏗 Architecture

```text
Employee Client (PySide6)
        │
        ▼
 REST API (Node.js)
        │
        ▼
 PostgreSQL Database
        │
        ▼
 Admin Dashboard
```

---

## 📦 Technology Stack

### Client

* Python 3.12+
* PySide6
* SQLite
* Requests
* Cryptography

### Server

* Node.js
* Express.js
* PostgreSQL
* JWT
* bcrypt

### Reporting

* CSV Export
* ReportLab PDF Export

---

## 📁 Project Structure

```text
Employee-Tracking-System/
│
├── client/
│   ├── application/
│   ├── presentation/
│   ├── infrastructure/
│   ├── storage/
│   └── main.py
│
├── server/
│   ├── controllers/
│   ├── middleware/
│   ├── routes/
│   ├── uploads/
│   └── server.js
│
└── README.md
```

---

# ⚙ Installation

## 1. Clone Repository

```bash
git clone https://github.com/Incognitoanshh/Employee-Tracking-System.git
cd Employee-Tracking-System
```

---

## 2. Configure Server

Create `.env`

```env
PORT=8000

DATABASE_URL=postgresql://username:password@localhost:5432/ets_db

JWT_SECRET=your_secret_key
```

Install dependencies

```bash
cd server

npm install

npm start
```

Server starts at:

```text
http://localhost:8000
```

---

## 3. Configure Client

Create virtual environment

```bash
python3 -m venv venv312

source venv312/bin/activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Run client

```bash
python3 -m client.main
```

---

# 🧪 QA Testing Guide

## Authentication

* [ ] Valid Login
* [ ] Invalid Login
* [ ] Logout
* [ ] Session Persistence

---

## Employee Management

* [ ] Add Employee
* [ ] Delete Employee
* [ ] View Employee Details

---

## Force Logout

* [ ] Login as Employee
* [ ] Trigger Force Logout
* [ ] Verify Employee Session Ends

---

## Attendance

* [ ] Login Attendance
* [ ] Logout Attendance
* [ ] Attendance Dashboard

---

## Screenshots

* [ ] Capture Screenshot
* [ ] Upload Screenshot
* [ ] Preview Screenshot
* [ ] Download Screenshot

---

## Activity Logs

* [ ] Activity Generation
* [ ] Activity Sync
* [ ] Log Export

---

## Dashboard

* [ ] Online Users
* [ ] Offline Users
* [ ] Charts
* [ ] Statistics

---

## Reports

* [ ] CSV Export
* [ ] PDF Export

---

# 🐞 Bug Reporting Format

Please report issues using:

```text
Severity:
Module:

Steps To Reproduce:

Expected Result:

Actual Result:

Screenshot / Error Log:
```

---

# 📊 Current Status

### Functional Readiness

✅ Authentication

✅ Attendance Tracking

✅ Idle Tracking

✅ Screenshot Monitoring

✅ Activity Logging

✅ Admin Dashboard

✅ Employee Management

✅ Force Logout

✅ CSV Export

✅ PDF Export

✅ Multi-User Support

---

# ⚠ Notes For Testers

* Use PostgreSQL as backend database.
* Screenshot data will appear only after employee activity.
* Admin access is required for dashboard functions.
* Force Logout requires an active employee session.
* Test on a clean database whenever possible.

---

# 👨‍💻 Author

**Amrit Anshu**

Full Stack Developer | Cloud & DevOps Enthusiast

GitHub:
https://github.com/Incognitoanshh

---

# 📜 License

This project is intended for educational, research, and enterprise demonstration purposes.
