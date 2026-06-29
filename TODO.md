# TODO

## Backend: Express error interception
- [ ] Inspect existing Express middleware chain in `server/server.js`.
- [ ] Replace generic error handler with advanced formatter:
  - [ ] Log explicit stack trace + request context to terminal.
  - [ ] Detect JSON parse errors from `express.json()` and return `{ success:false, message: err.message }` (HTTP 400).
  - [ ] For all other unhandled errors: return HTTP 500 with `{ success:false, message: err.message }`.
- [ ] Ensure formatter is positioned at very bottom before `app.listen()`.
- [ ] Quick manual test by sending malformed payload to `/api/logs/create` and verify response shape.

