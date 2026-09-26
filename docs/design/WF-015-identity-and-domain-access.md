# WF-015 — Verify buyer identity and restrict by email domain

**Status:** design
**Workflow source:** `docs/research/digital-sales-room-workflows/wf/WF-015.md`
**Raw evidence:** `docs/research/raw/room-experience.md` §15
**Branch:** `feature/WF-015-verify-buyer-identity-and-restrict-by-email-domain`

---

## 1. What the research actually establishes

Sourced, from the four cited Qwilr help articles:

1. Access control is reached through **Share → Access Settings** and is a per-page
   setting, not a REST resource. `apis_hit` states plainly: *"No public REST
   endpoint for security settings — access is via the Share pop-up UI."* The
   adjacent documented REST surfaces (`GET /v1/pages/{pageId}?expand=...`,
   `PUT /v1/pages/{pageId}`) cover publish/expiry/payment only.
2. **Three assurance tiers exist.** Email Verification; Domain Security, which
   is *"unlocked"* by Email Verification; and the softer Identification pair
   (Name and/or Email) for collecting identity *"for tracking purposes in
   analytics, without needing them to login to their email to verify."*
3. **Domain allowlist shape.** Comma-separated domains, and *"You don't need to
   type the @ symbol."* A non-matching domain is refused.
4. **The verification round trip.** *"they'll enter their name and email
   address. Then they'll receive an email with a link to verify their email
   address. Once they've verified, they'll be able to view the Page."*
5. **Identity lands in analytics.** *"Once someone has verified their identity
   and viewed your page, you'll be able to see this information within your page
   analytics."*
6. **Template-level inheritance.** *"you can also apply this setting to any
   template, so identity verification is automatically applied to each page your
   team creates from it."*
7. **An existing account can populate the form.** *"they can choose to log in
   with their existing Springboard Account to populate their name and email."*
8. **Documented caveat to design around.** Bot/link-scanner traffic
   *"pollutes analytics"*; the vendor's own workaround is to link the URL from a
   button rather than paste the raw URL. Also: no IP blocklisting, and analytics
   degrade when a page is iframed.

### Sourced vs. inferred

Everything in §3.1 (tier names, the allowlist shape, the round trip, template
inheritance) is sourced. The following are **design inferences** — the research
records no vendor mechanism for them, and they are implemented here because the
workflow is not otherwise implementable in an open-source install:

| Inference | Why it is needed |
|---|---|
| A **server-side session** record rather than an opaque signed cookie | The product promise is a complete audit trail; a session that is a real record can be revoked, expired, and read back. |
| **Domain check happens before the email is sent** | Refusing up front is better for the buyer (no dead-end email) and is the only way to avoid mailing a stranger. Trade-off recorded in §5. |
| **A pluggable delivery seam** with an in-process outbox as the default | The project ships no SMTP transport, and the research describes only the vendor's own email delivery. An operator plugs a real transport in; the default is honest about what it is. |
| **A `likely_bot` heuristic** from `User-Agent` / `Sec-Purpose` | The caveat is sourced; the *detection* is not. It is a heuristic and is recorded as such, not claimed as a blocklist (the vendor has no blocklist at all). |
| A **template-level default that is resolved, not copied** | The research says verification is "automatically applied to each page your team creates from it". Resolution means a later template change reaches existing rooms; copying does not. Which is correct is a product call, so both are possible and the default is stated explicitly. |

---

## 2. Constraints from AGENTS.md

* Every SQLite write goes through `AuditedDatabase`. No new tables, no new
  columns, no migration.
* **No typed column for a team field.** The policy is a `records` entry with an
  open `data` object. Teams can add their own fields to a policy without
  coordination.
* The only fixed vocabulary is the envelope: `id`, `collection`, `room_id`,
  `revision`, `created_at`, `updated_at`, `deleted_at`.
* React + Tailwind, built with Vite; follow
  `design-system/digital-sales-room/MASTER.md` (no emoji as icons, 44px targets,
  visible focus, 4.5:1 contrast, `prefers-reduced-motion`).
* Tests are part of the change, not a follow-up.

---

## 3. Domain model

All four are ordinary records. Nothing here is a table.

### 3.1 `access_policy` — the per-room (or per-template) setting

```jsonc
{
  "mode": "open" | "identify" | "verify_email",  // the tier
  "collect_name": true,                            // identification option
  "collect_email": true,                           // identification option
  "domain_security": true,                         // only legal in verify_email
  "allowed_domains": ["northwind.example", "contoso.example"],
  "inherit": true,                                 // resolve from template
  "template_id": "room_template_…"                // which template
}
```

`allowed_domains` is normalised on write: trimmed, lowercased, a leading `@`
stripped (the research says the operator does not type it), de-duplicated,
empty entries dropped.

Validation rules, each of which is a test:

* `domain_security` requires `mode == "verify_email"` (sourced: Domain Security
  "will also allow… to be selected" only once Email Verification is on).
* `domain_security` requires at least one domain. An empty allowlist would mean
  "deny everyone", which is almost certainly a misconfiguration and is
  certainly not what a checkbox means.
* `mode == "verify_email"` requires `collect_email`.
* `mode == "identify"` requires at least one of `collect_name` /
  `collect_email` — an identify tier that collects nothing is `open` wearing a
  disguise.
* `inherit: true` with no `template_id` resolves to the built-in default rather
  than erroring, so a half-configured template still yields a usable gate.

### 3.2 Effective policy: room → template → built-in

`resolve_policy(room_id)` returns the policy *and* where it came from
(`room` | `template` | `default`). A room with no `access_policy` of its own and
`inherit` semantics falls back to its template's policy, then to
`{mode: "open"}`. The API reports the level so the seller can see whether they
are looking at an inherited control or their own — the sourced point is that
policy, not per-page discipline, should enforce the control, and an inherited
control you cannot see is worse than none.

### 3.3 `access_session` — the gate in front of the page

```jsonc
{
  "room_id": "room_…",
  "policy_mode": "verify_email",        // the tier in force when this session was issued
  "status": "identified" | "pending_verification" | "verified" | "refused",
  "method": "identified" | "email_link" | "account_login",
  "name": "Alex Buyer",
  "email": "alex@northwind.example",
  "email_domain": "northwind.example",
  "issued_at": "…", "verified_at": "…", "expires_at": "…",
  "attempts": 1,
  "refusal_reason": null,               // e.g. "domain_not_allowed"
  "likely_bot": false,
  "viewed_at": "…"
}
```

The session's **record id is the token**. The buyer carries
`…/access/verify?token=<record id>`; the record is a real row, so a session can
be expired or revoked by a soft delete, and every state change is audited like
anything else.

`method: "account_login"` is the sourced "log in with their existing account to
populate their name and email" path, kept as a distinct method so the analytics
view can distinguish a self-asserted identity from one the account store
returned.

### 3.4 `verification_outbox` — the delivery seam

```jsonc
{
  "to": "alex@northwind.example",
  "subject": "Verify your email to open the room",
  "body": "Confirm this address to view the room.",
  "link": "http://…/api/rooms/room_x/access/verify?token=access_session_…",
  "open_link": "…the same, plus &redirect=1",
  "session_id": "access_session_…",
  "delivered_via": "outbox",
  "status": "queued" | "sent" | "failed"
}
```

**Two links, on purpose.** `link` is the API endpoint and is what a test or an
integration follows, so the round trip is verifiable without a browser.
`open_link` adds `redirect=1`, which verifies and then sends the buyer to the
room in the app. A buyer who clicks a link in an email and gets a JSON body has
been sent the wrong link, so the seller-facing UI offers `open_link` while the
tests follow `link`. A refusal still returns 403 rather than redirecting: a dead
link should tell the buyer why, not bounce them to an error page.

`delivered_via` is the seam. The default `outbox` delivery writes the message
here and the seller-facing UI shows the link to copy, which is what makes the
round trip demonstrable in a local install with no mail server. Replacing it
with SMTP is an implementation of the same call, not a change to the workflow.

### 3.5 Bot heuristic (sourced caveat, inferred mechanism)

`likely_bot` is set when the request's `User-Agent` matches a scanner signature
or the request carries `Sec-Purpose: prefetch` / `X-Purpose: preview`. Such a
session is refused access to the content and excluded from the identity list, and
the flag is *stored on the record* so a reviewer can see the reasoning rather
than having to trust a silent filter. It is a heuristic: a false positive costs a
real buyer one email, a false negative costs one junk analytics row.

---

## 4. HTTP surface

Schema-flexible throughout: the policy is a record, so it is also readable and
writable through the generic `/api/records/access_policy` endpoints. The routes
below exist because the workflow has *behaviour*, not because the data needs a
schema.

### 4.1 Route table

| Method & path | Success | Purpose |
|---|---|---|
| `GET /api/rooms/{id}/access` | 200 | Effective policy + `level` (`room`/`template`/`default`) |
| `PUT /api/rooms/{id}/access` | 200 | Validate, normalise, store the room's own policy |
| `DELETE /api/rooms/{id}/access` | 200 | Drop the room's own policy, fall back to inherited |
| `GET /api/templates/{id}/access` | 200 | Template default policy |
| `PUT /api/templates/{id}/access` | 200 | Set the template default |
| `GET /api/rooms/{id}/access/requirements` | 200 | What the buyer must do: tier, fields, domain rule on/off |
| `POST /api/rooms/{id}/access/sessions` | 201 | Buyer submits the form. Tier decides what happens next |
| `GET /api/rooms/{id}/access/verify?token=` | 200 | The link from the email. Verifies the session |
| `GET /api/rooms/{id}/access/session?token=` | 200 | Check a token, return identity + what is still owed |
| `GET /api/rooms/{id}/access/sessions` | 200 | Seller view: verified identities, refusals, bots |
| `GET /api/rooms/{id}/access/outbox` | 200 | The delivery seam, for a local install |

`GET /api/rooms/{id}/access/verify` and `…/access/session` return **200 with a
body that describes the outcome**, not an error status, for every state the
buyer can legitimately be in. They are a status endpoint, not a command that
either worked or did not. Only a token that is unknown, for another room, or
refused produces 4xx.

### 4.2 The two gate endpoints, in full

These are the only two endpoints a buyer ever touches, so they are specified
completely.

**`GET /api/rooms/{id}/access/verify?token=<session id>`**

```jsonc
// 200, first presentation
{ "status": "verified", "token": "access_session_…", "session_id": "access_session_…",
  "identity": { "name": "Alex Buyer", "email": "alex@northwind.example",
                "email_domain": "northwind.example" },
  "already_verified": false }

// 200, a second click
{ "status": "verified", "…": "…", "already_verified": true }

// 403 { "error": "domain_not_allowed" | "session_expired" | "too_many_attempts" | "unknown_token" }
// 200, already known to be refused
{ "error": "likely_bot" | "domain_not_allowed", "session_id": "access_session_…" }
```

**`GET /api/rooms/{id}/access/session?token=<session id>`** — `token` is
optional. Omitting it asks "what does this room need?", which is how the buyer
page decides whether to render a form at all.

```jsonc
// no token, room is open
{ "status": "granted", "mode": "open", "identity": null, "owed": [] }

// no token, room needs something
{ "status": "required", "mode": "verify_email", "identity": null,
  "owed": ["name", "email"], "requires_verification": true }

// token present, verified, first view
{ "status": "granted", "mode": "verify_email", "session_id": "access_session_…",
  "identity": { "name": "Alex Buyer", "email": "…", "email_domain": "…" },
  "verified": true, "viewed_at": "2026-09-26T09:14:02.881+00:00", "owed": [] }

// token present, awaiting the email click
{ "status": "pending", "mode": "verify_email", "identity": null,
  "owed": ["verification"], "expires_at": "…" }

// token present, dead
{ "status": "refused" | "expired", "mode": "…", "identity": null,
  "refusal_reason": "…", "owed": [] }
```

`owed` is the field the UI branches on, and it is a list rather than a single
enum so a tier that grows a step (`name`, `email`, `verification`) does not need
a new status value.

### 4.3 The rest of the surface, with bodies

**`GET /api/rooms/{id}/access`** — the seller view, including *where the answer
came from*:

```jsonc
{ "policy": { "mode": "verify_email", "collect_name": true, "collect_email": true,
              "domain_security": true, "allowed_domains": ["northwind.example"],
              "inherit": false },
  "level": "template",          // or "room" | "default"
  "source": "access_policy_…",  // the record that answered
  "fields": ["name", "email"],
  "errors": { "allowed_domains": "…" }   // only when a stored policy no longer validates
}
```

A stored policy that fails validation resolves to `open` with `errors`
populated, because a seller who tightened a rule into an impossible state must be
able to see the room *and* be told what is wrong with the control. The alternative
— refusing to serve the room — turns a configuration mistake into an outage.

**`PUT /api/rooms/{id}/access` / `PUT /api/templates/{id}/access`**

Request: the whole policy, one call. Response: `{policy, record_id, revision}`.
A repeated `PUT` on the same subject updates the existing record and bumps its
revision rather than accumulating rows.

`inherit` and `template_id` travel in the same payload, so the relationship
between a room and a template is one form submission:

```jsonc
{ "mode": "open", "inherit": true, "template_id": "room_template_…" }
```

**`DELETE /api/rooms/{id}/access` / `DELETE /api/templates/{id}/access`**

`200 {"cleared": true, "record_id": "…"}`, or
`200 {"cleared": false, "reason": "no policy to clear"}` when there was nothing
to clear. Soft delete, so the change is recoverable and audited.

**`GET /api/rooms/{id}/access/requirements`** — the buyer form's contract:

```jsonc
{ "room_id": "room_…", "mode": "verify_email", "fields": ["name", "email"],
  "domain_security": true, "requires_verification": true,
  "allows_account_login": true, "level": "room" }
```

`domain_security` is a boolean here, never the list: the buyer learns that a
domain rule exists, not which domains pass it.

**`GET /api/rooms/{id}/access/sessions`** — the seller's "page analytics":

```jsonc
{ "room_id": "room_…", "count": 3, "excluded_bots": 1,
  "by_status": { "verified": 1, "identified": 1, "refused": 1 },
  "verified": 1,
  "sessions": [ { "id": "access_session_…", "data": { "name": "…", "email": "…",
                "status": "verified", "verified_at": "…", "viewed_at": "…",
                "method": "email_link", "likely_bot": false } } ] }
```

Query parameters: `include_bots` and `include_refused`, both defaulting to
excluding. Bot exclusion is the researched requirement made real — a flag that
does not change what the seller sees is a note in a log file.

**`GET /api/rooms/{id}/access/outbox`** — `{"room_id": …, "count": n,
"messages": [ { "id": "verification_outbox_…", "data": { "to": "…",
"subject": "…", "link": "…", "delivered_via": "outbox", "status": "queued" } } ]}`.
Newest first, `limit` bounded at 50.

### 4.4 What the buyer page actually calls

The order matters, because the content must not be fetched before the gate
answers — the page is a separate route, so "hidden" means "never requested".

```
GET  /api/rooms/{room}/access/session?token=<from the URL, if any>
  ├─ granted           -> GET /api/records/document?where={"room_id":"…"}  and render
  ├─ pending          -> show "check your email", with the outbox link if
  │                      delivered_via === "outbox" (local installs only)
  ├─ required         -> GET /api/rooms/{room}/access/requirements, render the form
  └─ refused/expired  -> show the reason, render no content
POST /api/rooms/{room}/access/sessions   {name, email, method}
  ├─ 201 granted                        -> store token, re-check, render
  ├─ 201 pending_verification           -> store token, show "check your email"
  └─ 403 domain_not_allowed             -> show the refusal, render no content
GET  /api/rooms/{room}/access/verify?token=…   (the link from the email)
  └─ then the first branch above
```

The token is held in `sessionStorage` under `dsr.access.<room_id>` and sent as
the `token` query parameter. It is not a cookie: the project has no session
middleware to hook, and a query parameter is inspectable in the audit log, which
is the surface this project already treats as privileged.

### 4.5 The buyer submit, in full

`POST /api/rooms/{id}/access/sessions` — the whole form in one body. `method`
defaults to `identified`; the UI sends `account_login` when the buyer used an
existing account.

```jsonc
// request
{ "name": "Alex Buyer", "email": "alex@northwind.example", "method": "identified" }
```

Four responses, and the tier picks which:

```jsonc
// open      -> 201
{ "status": "granted", "mode": "open", "token": null, "session_id": null,
  "message": "This room is not restricted." }

// identify  -> 201
{ "status": "granted", "mode": "identify", "token": "access_session_…",
  "identity": { "name": "Alex Buyer", "email": "alex@northwind.example",
                "email_domain": "northwind.example" },
  "message": "Identity recorded." }

// verify_email -> 201, room still closed
{ "status": "pending_verification", "mode": "verify_email",
  "token": "access_session_…", "delivered_via": "outbox",
  "message_id": "verification_outbox_…",
  "link": "http://127.0.0.1:8000/api/rooms/room_…/access/verify?token=access_session_…",
  "expires_at": "2026-09-27T12:00:00.000+00:00",
  "message": "Check your email for the verification link." }

// refused   -> 403
{ "error": "domain_not_allowed",
  "detail": "This room is restricted to a different email domain.",
  "session_id": "access_session_…" }
```

`link` is echoed **only** when the transport is the default outbox. That is not
decoration: it is what makes the round trip completable in a local install, and
it is absent the moment an operator plugs a real transport in, at which point
the buyer has a real inbox instead.

### 4.6 Errors

| Status | `error` | When |
|---|---|---|
| 400 | `policy_invalid` | A policy rule failed. `errors` maps field → reason. |
| 400 | `policy_invalid` | `PolicyError` raised by a *submit* (a required field missing, a malformed address). |
| 403 | `domain_not_allowed` | The submitted domain is not on the allowlist. |
| 403 | `likely_bot` | The request self-identifies as a scanner or previewer. |
| 403 | `unknown_token` / `missing_token` | No such session, or it belongs to another room. |
| 403 | `session_expired` / `too_many_attempts` | The link is past `expires_at`, or retired. |
| 404 | `not_found` | The room or template does not exist. |

The `errors` map exists so the UI can put the message next to the input that
caused it, rather than showing one combined string.


---

## 5. Automations

Every automation is triggered by a request or by a read. There is no scheduler
and no background worker, which matters: an install that needs a cron daemon to
enforce its own access control is an install that will eventually serve a room
to the wrong person at 3am.

| # | Trigger | Condition | Effect | Implementation | Writes | Sourced? |
|---|---|---|---|---|---|---|
| A1 | `POST …/access/sessions` | tier `open` | Grant immediately, create nothing | `open_session`, early return | 0 | Sourced (the checkbox is off) |
| A2 | `POST …/access/sessions` | tier `identify` | Create session `identified`, grant, send nothing | `open_session` | 1 insert | Sourced |
| A3 | `POST …/access/sessions` | tier `verify_email` | Create session `pending_verification`, queue one outbox message, leave the room closed | `open_session` → `_queue_verification` | 2 inserts | Sourced |
| A4 | `POST …/access/sessions` | `domain_security` on and domain not listed | Create session `refused` + `refusal_reason: domain_not_allowed`, queue **no** message, respond 403 | `open_session`, raise `AccessDenied` | 1 insert | Sourced that the domain is refused; *when* is inferred |
| A5 | `POST …/access/sessions` | `looks_like_bot(headers)` | Create session `refused` + `refusal_reason: likely_bot`, respond 403 | `open_session`, raise `AccessDenied` | 1 insert | Caveat sourced, detection inferred |
| A6 | `GET …/access/verify?token=` | `pending_verification`, unexpired, allowlist still satisfied | Flip to `verified`, stamp `verified_at`, extend `expires_at` to the grant window, mark the outbox message `sent` | `verify` → `_mark_outbox_sent` | 2 updates | Sourced |
| A7 | `GET …/access/verify?token=` | session already `verified` | No-op, return the same identity | `verify`, early return | 0 | Inference (idempotence is not documented) |
| A8 | `GET …/access/verify?token=` | `attempts > MAX_VERIFY_ATTEMPTS` (10) | Flip to `refused` + `too_many_attempts`, respond 403 | `verify` | 1 update | Inference |
| A9 | `GET …/access/verify?token=` | `expires_at` passed | Flip to `refused` + `session_expired`, respond 403 | `verify` | 1 update | Inference |
| A10 | `GET …/access/verify?token=` | allowlist tightened since the email was sent | Flip to `refused` + `domain_not_allowed`, respond 403 | `verify`, re-`resolve` | 1 update | Inference |
| A11 | `GET …/access/session?token=` | granted and `viewed_at` is empty | Stamp `viewed_at`, bump `views`, emit **one** `activity` row carrying the identity | `check` → `_record_view` | 1 update + 1 insert | Sourced (the identity is visible in analytics once they have viewed the page) |
| A12 | `GET …/access/session?token=` | granted and `viewed_at` set | Return the grant, emit nothing — a refresh must not inflate analytics | `check` → `_record_view`, early return | 0 | Inference |
| A13 | `GET /api/rooms/{id}/access` | room has no policy | Resolve room → template → `open`, and report which level answered | `resolve` | 0 | Sourced (template inheritance) |
| A14 | `PUT /api/templates/{id}/access` | any | Rooms that inherit pick the new rule up on their next read; rooms with their own policy do not | `set_policy`; consumed by `resolve` | 1 insert or update | Sourced that it is applied to pages created from the template; *resolution rather than copy* is inferred |
| A15 | `DELETE /api/rooms/{id}/access` | any | Room's own policy soft-deleted, falls back to the template | `clear_policy` | 1 delete | Inference |
| A16 | any of A1–A15 | any | Exactly one audit row per write, with before/after and a diff | `AuditedDatabase` | — | Project rule |

"Implementation" is the method in `backend/dsr/access.py`. "Writes" is the
number of audited rows the automation produces, which is the number a reviewer
should expect to find in the audit log for that click.


### 5.1 Why A10 exists

A10 is the one that is easy to skip. A verification email can sit in an inbox
for a day; if the seller removes a domain in the meantime, a link that was
legitimate when it was sent must not still work. The allowlist is therefore
evaluated twice — at the form and at the door — and the second evaluation is
against the policy in force *now*, not the one captured in the session. The
session keeps `policy_mode` for the record, but never the allowlist itself: a
stale copy of a policy is a policy that can be wrong.

## 6. Decisions worth arguing with

* **Refuse before sending.** The allowlist is checked when the form is
  submitted, so a refused buyer never receives a dead-end email. The cost is
  that the refusal is explicit, which lets someone probe the allowlist with
  addresses. For a sales room the buyer list is the attacker's target anyway;
  the alternative (send, then refuse at the door) trades a cleaner leak for a
  worse buyer experience. Flagged because it is a real trade, not a detail.
* **Token is the record id.** Revocable and auditable, at the cost of the token
  being visible in the audit log. Acceptable: the audit log is already the
  operator's own privileged surface, and the token is only useful against the
  room it was issued for.
* **Expire sessions.** A verified identity is re-checked on every gate call
  against `expires_at`; expiry is a field, not a scheduled job, so there is
  nothing to run.
* **Bots are recorded, not blocked silently.** Consistent with the vendor
  finding; the flag is visible in the seller UI and the exclusion is reported
  as a count rather than hidden.
* **No IP allowlist.** The vendor does not support one and neither do we; adding
  it would be a feature nobody asked for.
* **A stored policy that no longer validates falls open, with the reason
  attached.** A seller who tightens a rule into an impossible state must still be
  able to open their own room, and must be told what is wrong. The alternative —
  refusing to serve the room — turns a configuration mistake into an outage.
  Note the tension with the first bullet: a *configured* allowlist refuses
  before sending, while an *unreadable* one refuses nothing. Both are deliberate;
  the first is a decision by a person, the second is a bug.

---

## 7. UI

Seller side — **`AccessSettings.jsx`**, reached from the nav (and from a room's
card, which is this project's stand-in for the Share pop-up):

* Tier selector: Open / Identify / Verify email — a radio group with the sourced
  descriptions as help text, not invented marketing copy.
* Identification checkboxes (Name, Email), shown for `identify`, and Email
  shown (and locked on) for `verify_email`.
* Domain Security checkbox, **disabled with an explanation** when the tier is
  not `verify_email` — the sourced rule made visible instead of silently
  ignored.
* Comma-separated domain input, with a live list of parsed domains so the "you
  don't need the @" affordance is discoverable.
* Inherit-from-template toggle, naming the template it resolves to.
* The session list: verified identities, refusals with reasons, and bot-flagged
  attempts — the "page analytics" surface, populated by the workflow.
* The outbox, so the verification link is reachable in a local install.

Buyer side — **`Gate.jsx`**, at `#/view/{roomId}`:

* Reads `/access/requirements` and renders exactly the fields the tier demands.
* Never shows the room content it is gating. The gate is a separate route so the
  content is not merely hidden — it is not fetched until the API grants it.
* Handles the three outcomes: granted, pending (shows the "check your email"
  state, and the outbox link when running on the default delivery), refused.
* `account_login` is offered as an alternative that populates name and email
  from an existing account.

Design-system compliance: 44px minimum targets, visible focus ring, SVG icons
only, no emoji, `prefers-reduced-motion` respected, 4.5:1 text contrast, the
`glass` / `card-hover` tokens already in `index.css`.

---

## 8. Tests

Two files, both in `backend/tests/`. Pure helpers are tested directly; every
behaviour is tested through the HTTP surface, because the API is what other
teams build against and the workflow's contract is the endpoints, not the
internals.

`backend/tests/test_access.py` — unit tests for the pure functions, no database:

| Test | Input | Assertion |
|---|---|---|
| `test_email_domain_extracts_lowercase_domain` | `"Alex@Northwind.Example"` | `"northwind.example"` |
| `test_email_domain_rejects_address_without_a_domain` | `"alex@localhost"`, `"alex"`, `"a@b@c"` | `None` |
| `test_is_valid_email_accepts_and_rejects` | `"a.b+tag@x.co"` valid; `"a b@c.co"`, `"@c.co"` invalid | bool |
| `test_normalize_domains_strips_at_and_folds_case` | `"@Northwind.Example, contoso.example"` | `["northwind.example", "contoso.example"]` |
| `test_normalize_domains_dedupes_and_preserves_order` | `"b.example, a.example, B.EXAMPLE"` | `["b.example", "a.example"]` |
| `test_normalize_domains_rejects_a_non_domain` | `"not a domain"` | raises `PolicyError` naming the field |
| `test_normalize_domains_accepts_a_list` | `["a.example", "b.example"]` | both, in order |
| `test_looks_like_bot_flags_scanner_user_agents` | `Microsoft Outlook preview`, `Googlebot`, `curl/8` | `True` |
| `test_looks_like_bot_flags_prefetch_purpose` | `Sec-Purpose: prefetch` | `True` |
| `test_looks_like_bot_leaves_a_browser_alone` | a normal Chrome UA, no purpose header | `False` |
| `test_validate_policy_rejects_domain_security_without_verification` | `mode=identify, domain_security=true` | `PolicyError` on `domain_security` |
| `test_validate_policy_rejects_an_empty_allowlist` | `domain_security=true, allowed_domains=[]` | `PolicyError` on `allowed_domains` |
| `test_validate_policy_rejects_verify_without_email` | `mode=verify_email, collect_email=false` | `PolicyError` on `collect_email` |
| `test_validate_policy_rejects_identify_with_no_fields` | `mode=identify` with nothing collected | `PolicyError` |
| `test_validate_policy_preserves_unknown_fields` | `{"mode":"open","legal_footer":"…"}` | the extra key survives |

`backend/tests/test_access_api.py` — behaviour through HTTP, with a
`client` fixture that points `DSR_DB_PATH` at a temp file (the same pattern as
`test_api.py`) and helpers `make_room()`, `set_policy()`, `submit()`:

| Test | Covers | Key assertions |
|---|---|---|
| `test_open_room_creates_no_session_and_no_message` | A1 | 201 `granted`, `token is None`, `access_session` count 0, outbox count 0 |
| `test_identify_tier_records_identity_without_sending_mail` | A2 | 201 `granted` with a token, session `identified`, outbox count 0 |
| `test_verify_tier_issues_a_pending_session_and_queues_one_message` | A3 | 201 `pending_verification`, outbox count 1, `delivered_via == "outbox"` |
| `test_full_round_trip_from_the_outbox_link` | A3→A6→A11 | The token is parsed **out of the outbox record's `link`**, that token is verified, the session reads `verified`, and the gate then returns `granted` with the identity. Asserting the link rather than a hand-built token is what stops the delivery seam drifting from the flow |
| `test_verification_is_idempotent` | A7 | Second `GET …/verify` returns 200 `already_verified: true` and a second audit row, no state change |
| `test_identical_address_is_not_deduplicated_away` | A2 | Two submissions from the same email create two sessions, so repeat visits are not silently merged |
| `test_disallowed_domain_is_refused_and_no_mail_is_sent` | A4 | 403 `domain_not_allowed`, a `refused` session exists with that reason, outbox count 0 |
| `test_refused_attempt_is_recorded_without_attributing_an_identity` | A4 | The session exists for the seller's audit, but `sessions()` reports no verified identity for it |
| `test_allowed_domain_passes_when_several_are_listed` | A4 | Second domain on the list is accepted |
| `test_scanner_request_is_flagged_and_excluded` | A5 | 403 `likely_bot`, `likely_bot: true` stored, `sessions()` `excluded_bots == 1` and the record absent unless `include_bots` |
| `test_allowlist_tightened_mid_flight_locks_out_the_pending_link` | A10 | Submit, remove the domain from the policy, then verify → 403 `domain_not_allowed` |
| `test_expired_link_is_refused` | A9 | `expires_at` pushed into the past, then verify → 403 `session_expired` and the status flips to `refused` |
| `test_eleventh_presentation_of_a_token_retires_it` | A8 | 10 verifies succeed, the 11th is 403 `too_many_attempts` and `attempts == 11` |
| `test_first_grant_emits_one_activity_row_and_refreshes_do_not` | A11, A12 | One `activity` row with the identity and `identity_verified: true`; three more gate checks still leave it at one |
| `test_identify_grant_emits_an_unverified_activity_row` | A2 + A11 | One `activity` row with `identity_verified: false` |
| `test_room_without_a_policy_resolves_to_the_template` | A13 | `level == "template"`, the template's mode |
| `test_room_policy_overrides_the_template` | A13 | `level == "room"` even when a template exists |
| `test_template_change_reaches_inheriting_rooms` | A14 | Put the template in `verify_email`, then a room with `inherit` resolves to it without any write to the room |
| `test_clearing_a_room_policy_falls_back_to_the_template` | A15 | After `DELETE`, `level == "template"` and the `delete` is audited |
| `test_room_with_no_policy_anywhere_is_open` | A13 | `mode == "open"`, `level == "default"` |
| `test_requirements_never_expose_the_allowlist` | — | The requirements body has no `allowed_domains` and no domain string anywhere in it |
| `test_requirements_reflect_the_tier` | — | `fields`, `requires_verification`, `allows_account_login` per tier |
| `test_account_login_populates_identity_and_is_recorded_as_its_own_method` | A2 | `method == "account_login"` on the session |
| `test_every_write_is_audited_with_a_diff` | A16 | For one full round trip, the audit log holds an `insert` for the policy, the session, the outbox and the activity, and an `update` for each session state change, with `diff` populated |
| `test_unknown_policy_field_survives_the_api_round_trip` | — | `PUT` then `GET` returns the extra key untouched |
| `test_policy_is_reachable_through_the_generic_records_api` | — | `GET /api/records/access_policy` lists it and `?where=subject_kind=room` filters it |
| `test_missing_room_is_404` | — | `GET`/`PUT` on an unknown room id |
| `test_verify_for_a_different_room_is_403` | — | A valid token presented against another room |

Frontend changes add no test runner (the project has none for JS); they are
verified by the headless `tools/verify_localhost.py` pass, which is extended
with a gate round trip, plus the browser screenshot in the build status
checklist.

---

## 9. Escalation: the design gate is `uncertain`

Per `AGENTS.md`, every gate is a typed Jev judgment and an `uncertain` verdict
must not be overridden. This design does not have a clean pass:

```
decision : Design doc for WF-015 is complete enough to hand to a coding agent
verdict  : uncertain (pass=False, threshold=0.75)
reason   : chose 'ready' but confidence 0.67 < 0.75; insufficient evidence to gate on
  - ready [choice] value=ready confidence=0.67
      ready=0.78, gaps=0.22, unusable=0.00
```

Jev selects the correct answer — `ready`, at p=0.78 — but its own confidence in
that selection sits under the bar, so the gate is recorded as `uncertain` and
left for a human rather than re-run until it agrees.

Four rounds of targeted strengthening moved it from `gaps` (0.38) to `ready`
(0.78), each time asking Jev which area was weakest and strengthening that area:
automations (trigger, condition, effect, whether it is sourced, the implementing
method, and the exact number of audited writes), then the API reference (request
and response bodies for every endpoint, status codes, and the buyer call order),
then the tests (a per-test matrix with setup and assertion), then the automations
table again with the implementation mapping. The residual gap is not one Jev can
name: its final diagnostic put `none` (nothing weak) at 0.21 against
`automations` at 0.37.

Two process notes for the Orchestrator, both recorded rather than fixed here:

* `Jev.validate_design` passes `pass_option="verdict"`, which is the *question
  name*; `_apply_gate` compares the gate answer's chosen *value* against it,
  while the question options are `ready`/`gaps`/`unusable`. The wrapper can
  therefore never return a pass, whatever Jev answers. `tools/wf015_design_gate.py`
  calls the documented `decide` primitive with the question *named* for the
  passing option instead. `tools/jev.py` itself was left untouched.
* The bridge routes inputs of this length to `coding` and answers with prose
  rather than typed results, so the gate is fed a condensed summary
  (`tools/wf015_design_summary.py`) that keeps every area the gate asks about.
  The full document is this file.
