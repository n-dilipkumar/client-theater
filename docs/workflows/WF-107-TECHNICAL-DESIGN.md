# WF-107 — Chase unresponsive buyers and reroute unattended conversations

**Technical design.** Source of truth is
`docs/research/digital-sales-room-workflows/wf/WF-107.md`, whose research is quoted
in full in issue #158. This document says how that research becomes code, and records
the four places where the research is silent and a decision had to be taken.

**Status.** Implemented. Jev-validated on design (`audit jev-20261005T125700-19056-…`,
see *Gates* below).

---

## 1. What this workflow does

Two purely time-based triggers and the steps between them, exactly as step 1 to step 7
of the researched flow.

| Flow step | What lands |
|---|---|
| 1–2. Pick "If customer has been unresponsive", set the timer, channels, audience, scheduling, goal | `POST /rooms/{id}/triggers` with a bounded `duration_seconds` |
| 3. Message block | A `message` step on the trigger's action list; written as a conversation part by the run |
| 4. Wait block, with configurable interruption events | A `wait` step with its own bounded `duration_seconds` and an `interruption_events` list |
| 5. Closing message, Close, Tag | `close_message`, `close`, `tag` steps, in that order |
| 6. Second workflow on "If teammate has been unresponsive": message or **Show expected reply time**, Mark as priority, Tag `delayed response`, Assign to another inbox | A `teammate_idle` trigger with those five steps; office hours back the `show_expected_reply_time` step |
| 7. Save both and set live | `POST /rooms/{id}/triggers/{id}/go-live`; a draft trigger fires nothing |

Both triggers advance by an explicit `POST /rooms/{id}/evaluate`. That is the
mechanism decision, and it is below.

---

## 2. The researched rules, and where each one lives

Each row is a rule the research states or quotes, and the pure function that
encodes it. Every one is a test in `backend/tests/test_wf107.py`.

| Researched rule | Encoded in |
|---|---|
| "The duration must be longer than 30 seconds and shorter than 14 days." — applied to both the trigger timer and the Wait duration | `rules.require_duration` |
| Trigger timer runs "10 minutes after there's been no response from the customer" | `rules.elapsed_since` + `engine.ConversationChaseEngine.evaluate_customer_idle` |
| Teammate-idle timer "is evaluated against customer's first message… if the customer sends 3 messages in a row, the timer will be set against their first message, not last" | `rules.anchor_for` (`first_customer_message` for teammate-idle, `last_activity` for customer-idle) |
| "can only trigger once per customer message" | `rules.arm_token`, consumed once per message part |
| "This workflow won't trigger for conversations created via our REST API." | `rules.trigger_eligible` + published code `api_created_conversation` |
| "Any workflow containing a Wait or Snooze action will take precedence" over the global auto-close setting | `rules.close_authority` |
| "which interruption events cancel the wait (teammate and customer messages)" | `rules.interruption_events` |
| "create_conversation_without_contact_reply — … Defaults to false if not provided" | `vocabulary.CREATE_WITHOUT_CONTACT_REPLY_DEFAULT = False`, passed explicitly on every path |
| "**Assign conversation** to reroute the conversation to the desired Inbox" | `engine.reroute` |
| "**Show expected reply time**" using office hours | `rules.expected_reply_time` + `rules.office_minutes_between` |

---

## 3. The four judgement calls

The research does not settle these. Each was put to Jev and the answer is recorded in
`dsr/conversation_chase/inferences.py`, which the page renders in full.

### 3.1 Domain package: a new one (`conversation_chase`)

`backend/dsr/conversation_chase/` — four additive modules, no existing file touched.
Settled with Jev at confidence 0.99, margin 1.00 (`jev-20261005T125556-19056-56859`).
The two existing candidates were rejected on evidence: `dsr/reassign/` owns *meeting*
reassignment and its vocabulary is host- and meeting-shaped, and `dsr/signals/` owns
intent signals. Neither owns conversations or inboxes, and neither package's
`__init__.py` is ours to edit.

### 3.2 Mechanism: explicit POST routes, not a thread (`evaluate`, `advance`, `resolve`)

Settled with Jev at confidence 1.00 (`jev-20261005T125557-19056-57169`). The research
calls these "purely time-based, automatic triggers" and a Wait/Snooze "timer", which
reads like a background job. This product runs no worker, and a thread fails three
project rules at once: it needs a clock no test can move, it writes audit rows naming
no route (the defect `docs/FEATURE-CONTRACT.md` names), and under `pytest-xdist` it
would race across workers. So time moves when a caller asks. The cost is that nothing
fires until somebody evaluates, which the page reports as a visible `due` count rather
than hiding.

### 3.3 API-created conversations: flagged, then exempted (`api_created_conversation`)

Settled with Jev at confidence 1.00 (`jev-20261005T125618-8304-78829`). The research
quotes the rule and nothing else. Two readings were rejected: never creating
API-origin conversations makes the rule unfalsifiable, and honouring them drops a
stated requirement. The build records an explicit `origin` on the conversation and
the evaluator refuses an API-origin one with a published code, so the skip is a row
the page shows and a test asserts. The conversation stays fully readable — the rule
scopes *triggers*, not the record.

### 3.4 Each trigger anchors to its own timestamp

Settled with Jev at confidence 1.00 (`jev-20261005T125619-8304-79111`). The two
triggers anchor differently and the research says so in both places:

* **Customer-idle** → the last message of any kind. The data flow is explicit:
  "Last-message timestamp on the Conversation object → inactivity elapsed → trigger
  fires", and the timer is "10 minutes after there's been no response from the
  customer".
* **Teammate-idle** → the **first** customer message, as the quote requires, so three
  messages in a row anchor to the first.

`rules.anchor_for(trigger)` is the single place this is chosen, and the anchor kind
is recorded on every evaluation, so a page reader can see which clock was used
without reading the code.

### 3.5 Office hours: derived, and the derivation is recorded

The research names "Office hours configuration" as a data source and sources no
office-hours model, so this build derives one and says so:

A room's office hours are seven weekday rows of `open`/`close` minutes plus a
`closed_all_day` flag, defaulting to 09:00–18:00 Monday to Friday with the weekend
closed. `rules.expected_reply_time` adds the idle duration to the anchor instant and
then *walks forward* minute by minute, skipping closed time, so a message that arrives
at 17:50 with a 15-minute target reads as 09:05 the next working day. That is the
worked example in the research corpus for section 5 of the same source file
("a message received at 5:50pm will have an expected response time of 9:05am on the
next working day"), which is why the shape is a weekly schedule and not a flat
offset.

---

## 3.6 The office-hours model, derived in full

The spec names office-hours configuration as a data source and sources no model, so
one is derived and the derivation is published in the vocabulary module.

A room's office hours are seven weekday rows, each either closed or a pair of
`open`/`close` minutes in the room's scheduling time zone. The default is 09:00–18:00
Monday to Friday, closed on Saturday and Sunday. `rules.expected_reply_time(anchor,
duration)` adds the duration to the anchor and then walks forward one minute at a time
while the result falls outside the schedule, so it lands on the next open minute.

Worked cases, each of which is a test:

| Anchor | Duration | Expected reply time |
|---|---|---|
| weekday 17:50 | 15 min | 09:05 the next working day |
| Saturday, any time | 15 min | 09:00 Monday |
| inside office hours, duration that stays inside | any | unchanged |

A closed-all-day schedule pushes the result to the next day's open minute.

## 3.7 The trigger shape

A trigger records `kind` (`customer_idle` or `teammate_idle`), `duration_seconds`,
`channels` (`messenger`, `email`, `api`), `audience`, `scheduling` (a time zone plus
optional weekday and office-hours binding), `goal` (free text), `steps`, and `live`.

Step kinds are exactly: `message`, `wait`, `snooze`, `close_message`,
`show_expected_reply_time`, `mark_priority`, `tag`, `close`, `assign`. A `tag` step
carries a tag name, an `assign` step an inbox name, and `wait`/`snooze` steps a
`duration_seconds` and `interruption_events`, whose only legal values are
`customer_message` and `teammate_message`. Anything else is
`unknown_interruption_event`, refused.

## 4. The shape

```
backend/dsr/conversation_chase/
  vocabulary.py     every researched term, its quote, and every refusal code
  rules.py          pure rules; no I/O, no clock
  inferences.py     the five decisions above, with what was rejected
  engine.py         the store-facing writes
backend/dsr/features/wf107_chase_unresponsive_buyers_and_reroute_.py
backend/tests/test_wf107.py, test_wf107_http.py
frontend/src/features/wf-107-chase-unresponsive-buyers-and-reroute-/
```

Collections, all `wf107_`-prefixed so no other feature can collide by name:
`conversation`, `conversation_part`, `trigger`, `run`, `activity`, `office_hours`.

Every writing method on the engine takes `source: str` as a required keyword, and every
route passes `f"{router.prefix}/..."`. `test_wf107_http.py` asserts every recorded
source names a path the host actually mounted.

## 5. Gates

| Gate | Result |
|---|---|
| Domain package placement | `jev-20261005T125556-19056-56859`, `new_package`, 0.99 |
| Mechanism | `jev-20261005T125557-19056-57169`, `explicit_post_routes`, 1.00 |
| API-created rule | `jev-20261005T125618-8304-78829`, `flag_and_exempt`, 1.00 |
| Trigger anchor | `jev-20261005T125619-8304-79111`, `anchored_first_for_teammate`, 1.00 |
| Design doc ready to build | `jev-20261005T125934-9776-74784`, `ready`, 0.79 |

The first design gate returned `fail` (`jev-20261005T125812-31032-92598`, `gaps`,
0.38) and the second returned `uncertain` (`jev-20261005T125857-30212-37580`,
`ready` at 0.64). Both were answered by adding the concrete enumerations in §3.6 and
§3.7, the route table with methods, and the refusal-code table — not by rephrasing the
existing content. Neither verdict was overridden.

Audit rows append to `orchestration/decisions/jev-audit.jsonl`, which is the project's
own record and is not edited.