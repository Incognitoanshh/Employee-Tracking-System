# Employee Tracking System (ETS)

A professional employee monitoring and productivity tracking system built using Python (PySide6), SQLite, PostgreSQL, and Node.js.

## Features

### Employee Monitoring

* Employee Login Tracking
* Shift Tracking
* Idle Detection
* Activity Monitoring
* Screenshot Capture
* Screenshot Preview

### Sync Engine

* Automatic Screenshot Upload
* Activity Log Sync
* Offline Data Storage
* Retry Mechanism for Failed Uploads
* Background Synchronization

### Backend APIs

* Employee Authentication
* Screenshot Upload API
* Activity Log API
* Dashboard Statistics API
* Screenshot Listing API
* Activity Log Listing API

### Storage

* SQLite (Local Client Storage)
* PostgreSQL (Centralized Server Database)

---

## Project Structure

```text
Employee Tracking System
│
├── client/
│   ├── application/
│   ├── infrastructure/
│   ├── presentation/
│   ├── services/
│   └── main.py
│
├── server/
│   ├── controllers/
│   ├── routes/
│   ├── config/
│   ├── uploads/
│   └── server.js
│
├── storage/
├── logs/
├── tests/
└── venv/
```

---

## Technologies Used

### Client

* Python 3
* PySide6
* SQLite
* Requests
* PyAutoGUI

### Server

* Node.js
* Express.js
* PostgreSQL
* Multer

---

## Available APIs

### Authentication

POST /api/auth/login

### Activity Logs

POST /api/logs/create

GET /api/logs/all

### Screenshots

POST /api/screenshots/upload

GET /api/screenshots/all

### Dashboard

GET /api/dashboard/stats

Example Response:

```json
{
  "success": true,
  "data": {
    "employees": 1,
    "screenshots": 94,
    "activity_logs": 176
  }
}
```

---

## Reliability Features

* Offline Screenshot Queue
* Offline Log Queue
* Automatic Retry Uploads
* Local Data Persistence
* Background Sync Scheduler

---

## Current Status

Core Monitoring System: Complete

Implemented:

* Login Tracking
* Shift Tracking
* Idle Detection
* Screenshot Capture
* Activity Tracking
* Screenshot Sync
* Log Sync
* Dashboard APIs

Upcoming:

* Dashboard UI Improvements
* Auto Start
* AES Encryption
* Installer Packaging

---

## Author

Amrit Anshu
