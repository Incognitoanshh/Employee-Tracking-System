# Chat API — Phase 1 and Phase 2

The contract the two panels are built against. Nothing here changes without a
matching change in the client, so it is written down before either panel is
started.

Every route sits behind `verifyToken`. Ids are integers, `seq` is a 64-bit
integer, and every timestamp returned is UTC unless a field name says `_ist`.

---

## Access rule

One rule, enforced in `server/utils/chat_access.js` and nowhere else.

A channel is visible when the person is **in its team**, and:

- it is the team's **General** channel, or
- it is an **announcement** channel that is not private, or
- they were **added to it** explicitly.

Consequences worth knowing before building the panel:

- Adding somebody to a team gives them General and the announcements. Every
  other channel is granted separately.
- Administrators get **no implicit access**. An admin who is not in a team
  cannot read it. The only way to read somebody else's conversation is the
  audited super-admin route.
- A channel you cannot see returns **404, not 403** — a 403 would confirm the
  channel exists and that you are outside it.

---

## Employee panel — `/api/chat`

### `GET /me/teams`
The whole left-hand pane in one request.

```jsonc
{ "success": true,
  "notifications_unread": 2,
  "teams": [{
    "id": 1, "name": "Development", "is_archived": false, "unread": 7,
    "channels": [{
      "id": 1, "name": "General", "type": "STANDARD",
      "is_default": true, "is_private": false,
      "unread": 7, "last_read_seq": 4810, "last_seq": 4817,
      "last_message_at": "2026-08-07T09:14:02.113Z"
    }]
  }]
}
```

`unread` never counts your own messages.

### `GET /updates?since=<seq>`
The poll. Everything after `since` across every visible channel, in one query.

- `since=0` returns **no messages** and only the current `cursor`. A fresh
  client establishes its cursor here, then loads history per channel.
- Capped at 500 messages. When capped, `cursor` is the last message actually
  returned — not the global head — so nothing is skipped.

```jsonc
{ "success": true, "cursor": 4817,
  "messages": [ /* Message */ ],
  "notifications": [{ "id": 9, "type": "ANNOUNCEMENT", "message_seq": 4816,
                      "channel_id": 3, "channel_name": "Company Updates",
                      "team_name": "Development" }] }
```

**Suggested poll intervals** — chat focused `3s`, app open `15s`, minimised
`60s`. Chosen over WebSockets deliberately: on a link with ~20% packet loss a
socket spends its life reconnecting, whereas a dropped poll costs nothing
because the next one asks the same question.

### `GET /channels/:id/messages?before=<seq>&limit=<n>`
History, walking backwards. Returned **oldest-first** so the client can
prepend without reversing. `limit` defaults to 50, maximum 200.

```jsonc
{ "success": true, "has_more": true,
  "channel": { "id": 1, "name": "General", "type": "STANDARD",
               "team_id": 1, "team_name": "Development",
               "is_archived": false, "can_post": true },
  "messages": [ /* Message */ ] }
```

Use `can_post` to show or hide the composer — it already accounts for archived
teams and announcement channels.

### `POST /channels/:id/messages`
```jsonc
{ "body": "@rajesh kal ka report bhej dena",
  "client_msg_id": "8f14e45f-ea1b-4c1e-9a2a-1f7f1e9d0b21",   // optional, UUID
  "reply_to": 4801,                                           // same channel only
  "mentions": ["E001"],                                       // from the autocomplete
  "attachment_ids": [12, 13] }                                // uploaded first
```

**`reply_to` must be in the same channel.** The reply carries a preview of
what it answers, so accepting a seq from anywhere would print a slice of
another team's conversation inside one you are allowed to read.

**Mentions come from two places** and are merged: the ids the autocomplete
supplies, and any `@username` found in the body. Both are then filtered to
people who can see the channel — mentioning somebody who cannot notifies
nobody, because the notification would tell them the channel exists.

**A message carrying a file needs no body.**

`201` on a new message, `200` with `"duplicate": true` when a retry of an
already-stored message arrives. **The offline queue must send the same
`client_msg_id` on every retry** — that is what stops a resend duplicating
when the first attempt landed and only the reply was lost.

| status | meaning |
|---|---|
| 400 | empty, over 2000 characters, or a malformed `client_msg_id` |
| 403 | announcement channel — only administrators post there |
| 404 | channel not visible to you |
| 409 | the team is archived |
| 429 | over 20 messages a minute |

### `PATCH /messages/:seq`   `{ "body": "..." }`
Your own message, within **5 minutes**. Every previous version is kept.
`403` for somebody else's message, `404` if you cannot see the channel at all,
`409` once the window has closed.

**There is no delete route.** That is deliberate, not missing.

### `POST /channels/:id/read`   `{ "seq": 4817 }`
Moves the read mark, and clears announcement notifications up to that point.
The mark only ever moves forward, so a late poll or a second panel cannot
un-read anything.

### `GET /channels/:id/members`
```jsonc
{ "members": [{ "employee_id": "E002", "name": "Amit Sharma",
                "designation": "Developer", "role": "employee",
                "status": "IDLE", "idle_minutes": 14,
                "last_seen": null, "is_me": false }] }
```

`status` is `ACTIVE` | `IDLE` | `OFFLINE`. Unlike a self-declared presence,
this is measured — `idle_minutes` is real.

### `GET /search?q=<text>&channel_id=<id>`
Only across visible channels. At least 2 characters, up to 100 results, each
with an `excerpt` where matches are wrapped in `<b>`.

Every term is matched by prefix against a `'simple'` tsvector, so `report`
finds `reports` and `reporting`. An English configuration is **not** used: its
stoplist discards `me`, `do` and `to`, which are ordinary Hinglish words here,
so searching for them would silently return nothing.

### `POST /notifications/read`   `{ "ids": [9, 10] }`
Omit `ids` to clear all.

### `POST /messages/:seq/pin`   `{ "pinned": true }`
Any member of the channel may pin or unpin; `pinned_by` records who. Capped at
20 per channel — a shelf of fifty is not a shelf. `409` past the cap, `404`
for a channel you cannot see.

### `GET /channels/:id/pinned`
The pinned messages, newest pin first, each with `pinned_by_name`.

### `POST /channels/:id/attachments`   (multipart, field `file`)
The bytes are **encrypted by the client** before upload, exactly as
screenshots are — the server stores a file it cannot read.

Uploaded **before** the message that carries it, and claimed by that message's
`attachment_ids`. Sending an empty message first and attaching afterwards
would leave a blank line in the conversation for the length of the upload, and
permanently if it then failed.

Limit 15 MB (`413` over it). Refused for announcement channels and archived
teams. A file never claimed by a message is swept up by `purge_old_data.sh`
after a day.

```jsonc
{ "success": true, "attachment": { "id": 12, "file_name": "report.pdf",
                                   "size_bytes": 2097152 } }
```

Only the uploader can claim a file, so a guessed id cannot attach somebody
else's upload to your message.

### `GET /attachments/:id`
The encrypted bytes, subject to the same visibility rule as the channel it was
posted in — without that, an id counted upwards in a URL walks every file in
the company. The client decrypts, writing to a temporary name and moving it
into place only once that succeeds.

---

## Admin panel — `/api/admin`

All of these need `admin` or `super_admin`. Any admin may manage any team: one
person being away should not stop the work.

| method | route | notes |
|---|---|---|
| `GET` | `/teams` | with member, channel and message counts |
| `POST` | `/teams` | `{ name, description?, members? }` — creates General too |
| `GET` | `/teams/:id` | channels + members, each member's channel ids |
| `PATCH` | `/teams/:id` | `{ name?, description? }` |
| `POST` | `/teams/:id/archive` | `{ archived, reason }` — **reason required** |
| `POST` | `/teams/:id/channels` | `{ name, type?, is_private?, members? }` |
| `PATCH` | `/channels/:id` | General cannot be renamed |
| `POST` | `/channels/:id/announce` | `{ body }` — announcement channels only |
| `POST` | `/teams/:id/members` | `{ employee_ids: [] }` |
| `DELETE` | `/teams/:id/members/:employee_id` | their messages stay |
| `POST` | `/channels/:id/members` | team members only |
| `DELETE` | `/channels/:id/members/:employee_id` | |

There is **no delete** for a team or a channel. Archiving closes a team to new
messages and leaves it readable and searchable; deleting would take every
conversation in it.

---

## Reading somebody's conversation — super admin only

### `POST /admin/chat/view`
```jsonc
{ "channel_id": 1,
  "purpose": "COMPLAINT",
  "reference_id": "Complaint #214",
  "note": "...",          // required when purpose is OTHER
  "before": 4817, "limit": 100 }
```

`purpose` is one of `HR_INVESTIGATION`, `COMPLAINT`, `LEGAL`, `COMPLIANCE`,
`EMPLOYEE_REQUEST`, `OTHER`. A `reference_id` is required for all except
`EMPLOYEE_REQUEST` and `OTHER`; `OTHER` requires a `note` instead, so it
cannot be used as a way around giving a reason.

A `POST` rather than a `GET` because it **writes**: the act of reading is
recorded. It also keeps the purpose out of the URL, where it would end up in
access logs and browser history.

Returns the messages, plus `edit_history` — every previous version of anything
edited. That is the point of keeping versions: without it, somebody can say
something, change it a minute later, and the only record of what was said is
one nobody can reach.

Each read writes a row to `chat_access_log` (never purged) **and** a
`CHAT VIEWED` line to `activity_logs`, so it appears in the existing weekly
audit report without that report needing to know this table exists.

### `GET /admin/chat/access-log?from=&to=`
Who read what, when and why — with a `by_purpose` breakdown.

---

## The Message object

```jsonc
{ "seq": 4817,
  "channel_id": 1,
  "sender_id": "E001",          // null once the account is deleted
  "sender_name": "Rajesh Kumar", // snapshot at send time — never changes
  "sender_code": "E001",
  "former": false,               // true = show "(Former Employee)"
  "body": "kal ka report bhej dena",
  "reply_to": null,
  "created_at": "2026-08-07T09:14:02.113Z",
  "edited": false,
  "edit_count": 0,

  // Phase 2 — always present, empty rather than absent, so the panel never
  // has to test for undefined before iterating.
  "pinned": false,
  "pinned_at": null,
  "attachments": [{ "id": 12, "file_name": "report.pdf", "size_bytes": 2097152 }],
  "mentions": [{ "employee_id": "E001", "name": "Rajesh Kumar" }],
  "mentions_me": false,
  "reply": { "seq": 4801, "sender_name": "Rajesh Kumar",
             "excerpt": "kal ka report bhej dena" } }
```

`sender_name` is copied in when the message is sent and never updated. If
somebody's name changes, old messages keep the name they were sent under; if
their account is deleted, `sender_id` becomes null while the name survives.
Without that snapshot every former employee would read as the same anonymous
person, and a conversation nobody can attribute is no use to the one review
that will ever read it.

---

## Two things the panel must get right

**Send optimistically, settle on the reply.** Show the message immediately in
a pending state, keep it in the queue with its `client_msg_id`, and only mark
it delivered when the server returns a `seq`. Retry the whole queue on
reconnect — the server deduplicates. This is the same pattern the screenshot
upload retry already uses.

**Never move a cursor backwards.** Both `updates.cursor` and the read mark
only ever advance. A late reply arriving out of order must be ignored, not
applied.

---

## A note on private channels

`channels.is_private` exists and the admin panel offers it, but under the
access rule above it changes **nothing about who can see the channel** — it
only changes the icon.

That is not an oversight. The rule chosen in Phase 1 is that a channel which
is not General is visible only to people explicitly added to it, so every
non-General channel is already private in the sense that matters. The flag
remains useful as a label — it tells the people in a channel that it is meant
to be a closed conversation — and as somewhere to hang a stricter rule later
without another migration.
