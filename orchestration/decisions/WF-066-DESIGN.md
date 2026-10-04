# WF-066 technical design: fan out meeting lifecycle events via signed webhooks

Research specification: `docs/research/digital-sales-room-workflows/wf/WF-066.md`,
drawn from section 16 of `docs/research/raw/scheduling-meetings.md`.

## What the workflow does

An admin registers a subscriber URL against one of the three researched event
types. When a meeting is created, updated or cancelled, the room serialises the
meeting payload once, signs the exact bytes with HMAC-SHA256 over
`{timestamp}.{raw_body}`, and POSTs it to every enabled subscription that names
that event type.

## The domain package

`backend/dsr/meeting_webhook_fanout/` holds the rules. It imports the store and
nothing else, so it can be unit tested without FastAPI and without a database
file.

The package was first written as `backend/dsr/scheduling_meetings/`, because the
research names `scheduling-meetings` as its domain. WF-067 shipped a package at
that path in PR 215 while this branch was open, so the rebase onto `origin/main`
found four files colliding by name. This package is the one that moved, because
WF-067's is already on main and its own feature module, tests and page depend on
the names there. WF-067's domain is a mutual action plan for e-signature
approval; this one is an outbound fan-out of signed meeting events. They share a
research document section and nothing else, and two packages under two names is
the outcome the feature contract asks for rather than a merge of two unrelated
rule sets into one directory.

| Module | Contents |
|--------|----------|
| `vocabulary.py` | The three event types, the payload field names, the header names, the deployment modes, the collection names. |
| `signing.py` | `signing_input`, `sign`, `verify`. HMAC-SHA256 over `{timestamp}.{raw_body}`. |
| `errors.py` | One hierarchy under `MeetingWebhookError`. Each carries its own `code` and `status`. |
| `inferences.py` | Every judgement call, named and served. |
| `payloads.py` | The meeting payload builder and the exact bytes that get signed. |
| `fanout.py` | `MeetingWebhookFanout`, the store-facing half. |

## Collections

All four are ordinary JSON in `records.data`. No migration and no typed column.

| Collection | One row is |
|------------|-----------|
| `meeting_webhook_subscription` | One subscriber URL bound to one event type, with a status and a secret reference. |
| `meeting_webhook_event` | One serialised, signed meeting event, before it goes anywhere. |
| `meeting_webhook_delivery` | One attempt at one subscriber, with its outcome. |
| `meeting_webhook_key` | One signing secret for a room. |

The subscription to event link is many to many, because the research says so in
its own words:

> "You are not limited by the number of webhooks you have"

and

> "multiple webhook types [may] have the same webhook URL, and multiple webhook
> URLs for the same type"

So one URL can carry several event types and one event type can have several
URLs. A single column on the meeting cannot express that, so it is not modelled
that way.

## Event types

The research names three, and the payload carries `type: Created|Updated|Deleted`.

| Subscription type | Payload `type` |
|-------------------|----------------|
| `new_meeting` | `Created` |
| `meeting_update` | `Updated` |
| `canceled_meeting` | `Deleted` |

Cancel maps to `Deleted`. That mapping is the research's, not this build's: the
three subscription names and the three payload values are given separately in the
data flow, and `For Canceled Meeting` is the only one of the three whose payload
value is `Deleted`.

## Signing

The signing input is the exact rule, not a choice:

> "Construct the signed payload by concatenating the timestamp and the raw
> request body, separated by a period: `{timestamp}.{raw_request_body}`"

```python
signing_input(timestamp, raw_body) -> f"{timestamp}.{raw_body}"
sign(secret, timestamp, raw_body) -> hmac.new(secret, signing_input(...), sha256).hexdigest()
```

The digest is hex encoded, because the header documentation says so:

> "**X-Chili-Signature** | HMAC-SHA256 signature of the payload (hex-encoded)"

The raw body is what gets signed and what gets sent. The room serialises the
payload once, signs those bytes, and sends those same bytes. A signature computed
over a re-serialised object does not verify, and the research names that as the
cause of a `Signature mismatch`.

## Headers

| Header | Value |
|--------|-------|
| `X-Chili-Signature` | The hex HMAC-SHA256 of the signing input. |
| `X-Chili-Timestamp` | Unix seconds, as the header documentation says. |

## The secret

The research says the secret is not created in the UI:

> "Optionally emails support to obtain the tenant's HMAC signing secret (not shown
> in the UI)"

So the create route accepts a secret, and the room serves one when the tenant has
not supplied any. A vendor screen that never sets it is a reason the room has to,
not a reason the room refuses.

## Replay protection

The research gives the freshness window as the consumer's, and says so:

> "Replay protection is left to the consumer (`MAX_AGE_SECONDS = 300`)"

So the room ships the timestamp header, publishes the window in the vocabulary,
and does not refuse a delivery on age. `verify` accepts an optional window and the
sender never sets one. A sender that enforced the window would refuse a
correctly signed request whose clock is fast, which is a clock bug wearing the
costume of a security control.

## Subscriber URL validation

The research gives two deployment modes and they disagree:

> "**Cal.com SaaS**: Only HTTPS URLs are accepted. HTTP, private/internal IP
> addresses (e.g., `10.x.x.x`, `192.168.x.x`, `127.0.0.1`), and `localhost` are
> blocked. **Self-hosted**: Both HTTP and HTTPS URLs are accepted, and private IP
> addresses are allowed for internal webhooks."

This build is self hosted, and `saas` is a per-room setting. Recorded as the
`saas-url-policy-is-a-room-setting` inference.

| Mode | Schemes | Private addresses |
|------|---------|-------------------|
| `self_hosted` | `http`, `https` | allowed |
| `saas` | `https` only | refused |

## Envelope

The research gives two shapes and warns against mixing them silently:

> "`MEETING_STARTED` and `MEETING_ENDED` are exceptions — they use a flat payload
> where booking fields are at the top level alongside `triggerEvent`, with no
> `payload` wrapper."

This build sends the Chili Piper shape, which is flat, because the three event
types this workflow fires are the Chili Piper ones and their documented fields are
top level. It does not reproduce Cal's `Meeting Started` and `Meeting Ended`, so
it has no occasion to mix the two shapes. Recorded as the
`one-flat-envelope-for-the-three-chili-types` inference.

## HTTP surface

Every write passes `source=f"{router.prefix}..."`, so the audit row names a route
the app actually serves.

| Route | Purpose |
|-------|---------|
| `GET /vocabulary` | The event types, the payload fields, the headers, the deployment modes, the collection names. |
| `GET /inferences` | Every judgement call, served. |
| `GET /rooms/{room_id}/subscriptions` | The subscriber table. |
| `POST /rooms/{room_id}/subscriptions` | Step 1 and 2: create a subscription. |
| `PATCH /rooms/{room_id}/subscriptions/{id}` | Set the row's status, or change its URL. |
| `DELETE /rooms/{room_id}/subscriptions/{id}` | Per-row delete. |
| `GET /rooms/{room_id}/secret` | The tenant's HMAC signing secret. |
| `POST /rooms/{room_id}/signing-key` | Set the secret, since no vendor screen does. |
| `POST /rooms/{room_id}/events` | Serialise, sign and fan out one meeting event. |
| `GET /rooms/{room_id}/events` | The event log. |
| `GET /rooms/{room_id}/deliveries` | One row per attempt, with its outcome. |
| `POST /rooms/{room_id}/events/{event_id}/redeliver` | Re-attempt one event. |
| `GET /rooms/{room_id}/sample` | The exact bytes and the signature, for a reader to compare. |
| `GET /rooms/{room_id}/summary` | Counts for the page header. |

## The transport is a seam

`Transport.post` has one method. The suite and the seeder supply a fake, so no
test opens a socket and `backend/seed.py` never tries to reach a subscriber from a
machine with no route to one. `UrllibTransport` uses the standard library only,
because `httpx` is a test dependency in this project and not a runtime one.

## What this build does not do

- It does not retry on its own. The research names no retry ladder, and copying a
  sibling feature's would be this build inventing a policy the evidence does not
  state. Redelivery is a route a person calls.
- It does not enforce the replay window. See above.
- It does not send Cal's `Meeting Started` and `Meeting Ended`.
- It does not open a socket during seeding.
