# WF-128 - In-room live presentation / co-browsing

**Technical design.** The source of truth is
`docs/research/digital-sales-room-workflows/wf/WF-128.md`, quoted in full in
issue #138. This document says how that research becomes code. It records the
decision the issue asks for (one SFU and one deck engine, each with a licence
and a reason) and the places where the research is silent or disagrees with
itself.

**Status.** Implemented and tested. The two design decisions below were put to
Jev and both passed. The audit ids are in *Gates* at the end.

---

## 1. What this workflow does

A rep starts a live session from a room. The buying committee joins, each
member with their own link, and follows the presenter through the room's pages.
A reveal order is respected. The session is recorded and published to the room
as a replay. The questions asked live are logged against the deal.

| Flow step | What lands |
|---|---|
| 1. A rep starts a live session from the room | `POST /api/wf-128/sessions`, then `POST /api/wf-128/sessions/{id}/start` |
| 2. The buyer, or a committee each with their own link, joins and follows | One `wf128_participant` per invitee, one join token each; `POST /api/wf-128/sessions/{id}/sync` records that a follower is following |
| 3. The presenter drives the deck or the actual product | `POST /api/wf-128/sessions/{id}/reveal`, one step forward or back to any earlier step |
| 4. The session is recorded and attached to the room as a replay | `POST /api/wf-128/sessions/{id}/end` creates a `wf128_recording` in the published state |
| 5. Questions asked live are logged against the deal | `POST /api/wf-128/sessions/{id}/questions` writes a `wf128_question` against the room |

## 2. The researched rules, and where each one lives

Each row is a rule the research states, and the pure function that encodes it.
Every one is a test in `backend/tests/test_wf128.py`.

| Researched rule | Encoded in |
|---|---|
| "A rep starts a live session from the room" | `LiveSessionBook.create_session` + `start_session` |
| "a whole buying committee, each with their own link" | `LiveSessionBook._add_participant` issues one `join_token` and one `join_url` per participant |
| "follows the presenter through the deck or the actual product" | `rules.plan_agenda` builds the agenda from WF-002's room pages, or one screen step |
| "a reveal order is respected" | `rules.reveal_decision` - one step forward, any step back, a forward jump is refused |
| "the session is recorded and attached to the room as a replay" | `LiveSessionBook.end_session` publishes the recording |
| "questions asked live are logged against the deal" | `LiveSessionBook.ask` writes the room as the question's `deal_ref` |
| "Auto-record and auto-publish" | `start_session` creates the recording; `end_session` publishes it |
| "auto-notify invitees" | `LiveSessionBook._notify` writes one notification per participant on start |
| "per-participant attendance" | `rules.attendance_state` + `rules.attended_seconds` + `wf128_attendance` |

## 3. The two design decisions

### 3.1 The stack: LiveKit (Apache-2.0) and reveal.js (MIT)

The issue names candidate SFUs and candidate deck engines and asks for one of
each, with the licence and the reason, recorded in a technical design doc. It
also says plainly: do not build against all four.

* **SFU: LiveKit, Apache-2.0.** The researched data flow is "recording composed
  via egress". LiveKit's egress component *is* that path. Jitsi Meet is also
  Apache-2.0, but its recording is an add-on rather than a first-class egress.
  BigBlueButton is purpose-built for presentation and recording, but it is
  LGPL-3.0 copyleft and a heavier deployment. mediasoup is ISC and is a library
  with no egress, so the whole recording path would be custom work.
* **Deck engine: reveal.js, MIT.** The rule this workflow enforces is "a reveal
  order is respected". reveal.js's fragment model is exactly a reveal-per-step
  order, so the rule and the engine agree. Slidev is MIT but needs a Node build
  step per deck, which does not fit a room's stored pages. impress.js is MIT but
  unmaintained, and its model is spatial rather than an ordered reveal.

**Neither is a dependency.** No realtime service runs in this build, and the
room's own pages stand in for the deck. The choice is recorded so a reader knows
what a real transport would be built on, and the vocabulary serves it from
`/api/wf-128/vocabulary`. Settled with Jev at confidence 1.00
(`jev-20261014T082233-9176-53767`).

### 3.2 The destination: the room record

The research disagrees with itself. Its automation line says "attendance written
to CRM". Its data flow says "attendance + questions -> deal record".

This build writes both to **the room record**, because the room is the deal
surface that exists in this product. No CRM connector is provisioned for this
feature, and the CRM-sync workflows own that surface. Choosing the CRM line
would leave the room with no attendance at all.

The choice is recorded in `vocabulary.INFERENCES` and `vocabulary.SOURCED_GAPS`,
and every attendance row carries `written_to: "room"` so a reader can see the
destination rather than infer it. Settled with Jev at confidence 1.00
(`jev-20261014T082307-17416-87092`).

## 4. The mechanism, and why there is no transport

No SFU, no WebSocket and no realtime service exists in this build. A thread or a
background job would fail three project rules at once: it needs a clock no test
can move, it writes audit rows naming no route, and under `pytest-xdist` it
would race across workers. So the session is **state**, **roles** and an
**event log**, and time moves when a caller asks.

A follower reaches the presenter's cursor by polling:

* `GET /api/wf-128/sessions/{id}/state` is read-only, so a poll never writes.
* `POST /api/wf-128/sessions/{id}/sync` is the follower's acknowledgement. It is
  a write, so the audit log names the route that served it.

The cost is that "sync" is recorded state and not a live stream. The page and
the vocabulary say so in the same place a "live" badge would otherwise imply a
working stream. This is recorded as `transport_is_a_stand_in` in
`vocabulary.INFERENCES`.

## 5. The recording artefact

The recording is binary and the store holds JSON. This build stores **no
bytes**. A session records:

* an opaque `replay_ref` (`artefact://wf128/<session id>`), and
* a stand-in `playback_url` under `replay.example.invalid`, which RFC 2606
  reserves so it can never resolve.

Where a real artefact would live is recorded in the vocabulary as
`artefact_store_note`: object storage (S3-compatible), composed via SFU egress,
then transcoded to HLS/MP4. The view carries `hosted: false` so no reader can
mistake the reference for a working player.

## 6. The per-participant link, and the seam it needs

"each with their own link" means one guest identity per invitee. This feature
issues one `join_token` and one `join_url` per participant.

The **access rules** that gate, revoke and expire a link belong to the existing
link-gating and revocation workflows. This feature does not edit their files and
does not re-implement their rules. It records the token and a stand-in URL, and
declares in `vocabulary.SOURCED_GAPS` that it does not enforce a gate itself.

## 7. What was not built, and why

| Named in the research | Built? | Why not |
|---|---|---|
| Auto-translate captions | No | Needs media processing this build does not do, and no caption surface exists. |
| Auto-clip highlights | No | Same: no media processing, no clip surface. |
| Attendance written to CRM | No | No CRM write-back is provisioned; the room record is the destination (3.2). |
| The SFU, the deck engine and the recorder | No | No realtime service exists in this product; the choice is recorded, not installed. |
| The join link's gate | No | Owned by the link-gating and revocation workflows (6). |

## 8. Files

* `backend/dsr/live_sessions/` - the domain: `vocabulary.py`, `errors.py`,
  `rules.py`, `engine.py`. Pure rules plus one module that reads and writes
  through `RecordStore` alone.
* `backend/dsr/features/wf128_live_co_presented_walkthrough_inside_the_room.py`
  - `FEATURE`, the router on `/api/wf-128`, the error mapping and `seed`.
* `backend/tests/test_wf128.py`, `backend/tests/test_wf128_http.py`.
* `frontend/src/features/wf-128-live-presentation/` - `index.jsx`, `api.js`,
  `primitives.jsx` and a test.

No shared file is edited.

## 9. Gates

| Gate | Result | Audit id |
|---|---|---|
| Design decision: SFU + deck engine | pass, LiveKit 1.00 and reveal.js 1.00 | `jev-20261014T082233-9176-53767` |
| Design decision: attendance destination | pass, room_record 1.00 | `jev-20261014T082307-17416-87092` |
| Release bar | recorded at landing | see the pull request |

The audit trail is `orchestration/decisions/wf-128-jev-audit.jsonl`, committed
with this branch.
