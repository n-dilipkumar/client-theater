# WF-051 design: Route and book a demo request inline from a web form

Technical design for the Concierge Router workflow. Ticket WF-051, issue 148.

## What the workflow does

A router is a declared flow of nodes. A webform post enters at the ``Trigger``
node, its fields are mapped onto Data Fields, the routing rules are evaluated
against those Data Fields and against the room's CRM records, the matched
``Display Calendar`` node names a seller and the Meeting Types to offer, and the
availability engine turns that into a slot list. The prospect picks a slot, the
second call commits it, and the post-booking nodes fire.

This build models that flow and its two API calls as records and rules inside
the room. It does not call Chili Piper. The researched endpoints are held as
data in :mod:`dsr.concierge_router.vocabulary` and served at
``GET /api/wf-051/vocabulary`` so a client renders the mapping rather than
compiling it.

## The researched rules this build enforces

Each of these is a refusal at declaration time, not a convention.

| Rule | Source quote | Enforcement |
|---|---|---|
| Trigger is first | "Trigger will **always** be your first node" | ``TriggerMustBeFirst`` on save |
| Trigger declares an action | "enables ``Webform is submitted``, ``In-app``, and/or ``Router Link``" | ``TriggerActionMissing`` |
| The chain ends in a Catch All | "Each router **must** end with a '**Catch All**' path to make sure you define the routing and acknowledge all inbound Leads." | ``RulesDoNotFallThrough`` |
| Node types are the researched set | the node list in ``features_tools`` | ``UnknownNodeType`` |
| ``Display Calendar`` node carries a **Time Elapsed** timer | "when it expires the meeting will be considered not scheduled" | ``timer_minutes`` on the node, ``timer_expires_at`` on the session, lazy expiry on read |
| ``Redirect To`` has a countdown and a destination | "``Redirect To`` has its own countdown timer before bouncing the prospect" | ``RedirectWithoutUrl`` |
| A post-booking node declares, it does not execute | "The router's default post-booking nodes fire" | the session records which fired; CRM writeback stays WF-042's |
| A published router answers; an unpublished one does not | "The router is published and deployed" | ``RouterNotPublished``, ``RouterUnavailable`` |
| A session commits once | "commit[s] the chosen slot" | ``RouteConsumed`` |
| A time never offered is not bookable | the slot list is what the modal rendered | ``SlotNotOffered`` |

The Catch All rule is what makes ``NoRuleMatched`` unreachable for a router that
saved. That is the point of enforcing it on save rather than at run time.

## Inferences the research left open

The research states these are not fields it publishes. Each is derived here and
recorded.

### ``time-elapsed-makes-a-booking-not-scheduled``

The ``Display Calendar`` node carries a **Time Elapsed** timer. The research
quotes only the consequence: "when it expires the meeting will be considered not
scheduled, and you can notify your rep to follow up". It does not say which
state a not-scheduled booking lands in.

**Decision.** A booking that the timer expires is a state, not a deletion. The
route session stays in the store as ``not_scheduled``, because the follow-up
path needs it: the researched ``Not Scheduled`` path runs ``Assign To``
(distribute the prospect) and ``Send Notification`` (email or Slack), and both
of those need the prospect and the seller the rule assigned. Deleting the
session would destroy the only record that says who to chase. The state is
terminal, so the session cannot be booked afterwards, which is what "the meeting
will be considered not scheduled" means.

**Chosen against.** Treating it as a deletion, which loses the prospect. Treating
it as a third live state, which would let a not-scheduled prospect be booked
later and contradict the research.

### ``routing-link-is-a-relative-path``

The route response sample carries ``"routingLink": "https://your-tenant.
chilipiper.com/concierge-router/[routerSlug]/routing/9413f879-..."``. That is a
tenant URL, so storing it verbatim would put one customer's host into another
customer's record.

**Decision.** Store the path, not the host. The stored ``routingLink`` is the
path component, and the tenant host is held once on the router as
``routing_link_base``. A response renders ``routing_link_base + path``. The
sample's own tenant placeholder is kept in the vocabulary as the shape a client
renders against.

### ``assignment-type-enumeration``

The sample carries ``"assignment": { "userId": "...", "type": "user" }``. The
research names three choices: **Owner**, **Round-Robin**, **Individual user**.

**Decision.** ``type`` carries the vendor's wire spelling, and the three choices
are mapped onto it: ``individual_user -> "user"`` (the sampled value),
``owner -> "owner"``, ``round_robin -> "round_robin"``. ``Owner`` and
``Round-Robin`` name a policy rather than a user, so the researched
extensibility note explains why they carry a policy reference rather than a
resolved user: "Assignment Tables let one path serve every territory". The
resolved user lands beside them once the engine has run the policy.

### ``primary-guest-data-fields``

The research names the payload, ``primaryGuestDataFields``, and no field of it.

**Decision.** Seven fields: ``firstName``, ``lastName``, ``email``,
``company``, ``phone`` from the form, and ``ownerId`` and ``meetingType`` from
the routing result. ``email`` is the only required one, because the CRM rules
read a live object and an object is found by address.

### ``lead-case-opportunity``

The researched ``data_flow`` names five CRM objects. The rule kind names three.

**Decision.** A CRM Ownership rule may check the owner of ``lead``, ``contact``
or ``account`` only, because that is what the rule kind says. ``opportunity`` and
``case`` are reachable as ``Without Ownership`` field values instead.
``require_ownership_object`` refuses the other two with a message naming that
route.

## Automations, specified as behaviour

The research states the automations in one sentence each. This section turns
each into a rule an implementer can write, because Jev judged this the least
concrete area of the design and that judgement was correct.

### The Time Elapsed timer

* **What carries it.** The ``Display Calendar`` node. ``vocabulary.TIMER_NODE_TYPES``
  is ``(display_calendar, redirect_to)``, because the research also says
  "``Redirect To`` has its own countdown timer before bouncing the prospect".
* **When it starts.** At the moment the route call returns ``schedulingAllowed:
  true``, not when the router was published. A timer started at declaration would
  expire against a prospect who never arrived.
* **Its deadline** is ``display_calendar.timer_minutes``, defaulting to
  ``vocabulary.DEFAULT_TIMER_MINUTES`` (60). The research names the behaviour and
  publishes no duration, so the default is a named constant an operator can
  override per node rather than a value written inline.
* **When it fires** the session moves to ``not_scheduled`` and stops being
  bookable. The deadline is stored on the session as ``timer_expires_at``, so a
  late reader sees the deadline it missed instead of re-deriving it.

**Nothing polls.** No background task, no thread, no timer on the server. The
research describes an outcome, not a scheduler, and a workflow plugin cannot
own a background thread without becoming infrastructure. Expiry is computed
when a session is read:

    expired = now >= timer_expires_at and state == PENDING

A read that finds an expired session performs the transition as a real write,
through the audited wrapper, so the state change is in the audit trail and a
follow-up notification can see it. That is a lazy timer, and it is the honest
implementation: the effect the research describes is guaranteed to have happened
by the time anyone observes the session, and never happens speculatively before
the observation that makes it relevant.

**Deadline changes are refused backwards.** A ``Display Calendar`` node whose
``timer_minutes`` is below the minutes already elapsed on a live session would
retire a prospect who is still on the calendar. Publishing such a node is
refused by ``TimerShorterThanElapsed``.

### The Not Scheduled path

The research: "``Not Scheduled`` paths run ``Assign To`` (distribute the
prospect) and ``Send Notification`` (email or Slack)".

* **Trigger.** The transition to ``not_scheduled``, whether it came from the
  timer expiring or from a caller cancelling. Both are the researched outcome.
* **``Assign To``** picks the prospect's new seller. Under a
  ``round_robin`` assignment this is the researched "one path can serve every
  territory", so the rotation advances by one. The new seller is written back
  onto the session as ``reassigned_to``; the original assignment is kept as
  ``assignment`` so the trail shows who first received the lead.
* **``Send Notification``** records the notification on the session as
  ``notification`` with its channel (``email`` or ``slack``), the recipient, and
  the moment. **This build records the notification rather than sending it.**
  The research names the channel and the trigger but no delivery contract, and
  this product has no outbound mail or Slack transport to send through. A
  recorded notification is what an operator can act on and what a test can
  assert. Anything claiming to have sent mail would be an unbacked claim.
* **Idempotence.** The path runs once per session. A second expiry read finds a
  session already in ``not_scheduled`` and does nothing, so a page refresh cannot
  spam a rep.

### The Redirect To countdown

* **What it does.** "``Redirect To`` has its own countdown timer before bouncing
  the prospect." It is a countdown to the thank-you page, not to a refusal.
* **When it fires.** After a successful commit, so the prospect sees the same
  calendar they booked from and is then bounced onward.
* **What it reads.** ``redirect_to.timer_minutes``, defaulting to
  ``DEFAULT_TIMER_MINUTES``. The research publishes no duration for it either.
* **Where the destination lives.** ``redirect_to.url`` on the node. A countdown
  with no destination has nothing to bounce to, so a ``redirect_to`` node with
  no ``url`` is refused at declaration by ``RedirectWithoutUrl``.

### Post-booking nodes

"``Create Event``, ``Update Field``, ``Update Ownership``" fire on a successful
commit. They are **declared, not executed**. The research names the nodes and
says they fire; it publishes no payload for them, and CRM writeback belongs to
WF-042, which owns the CRM records. So a router declares which of the three it
carries, and the session records which fired, so a reader can see what the
router was configured to do. Executing a CRM writeback from this feature would
duplicate WF-042's ownership of those records.

## Module layout

```
backend/dsr/concierge_router/
    __init__.py       package marker
    errors.py         written; the whole hierarchy hangs off RouterError
    vocabulary.py     written; every researched term beside its quote
    inferences.py     each derived decision, its reason, and its rejected reading
    nodes.py          the declaration rules: trigger order, catch-all, field map
    rules.py          rule evaluation against Data Fields and CRM records
    availability.py   the availability engine: slots from a seller's calendar
    engine.py         the two researched calls, and every write
```

The domain package imports nothing but the store. No FastAPI, no
``dsr.deps``, no ``dsr.api``, no ``sqlite3``, no ``AuditedDatabase(``.

## Records

Ordinary JSON in ``records.data``. No migration, no typed column.

| Collection | Written by | Holds |
|---|---|---|
| ``concierge_router`` | ``POST /routers`` | the declaration: slug, name, nodes, trigger actions, field map, publish state, deployment surfaces |
| ``concierge_booking`` | ``POST /route`` and ``POST /route/{id}/schedule-simple`` | the route session and the committed meeting |
| ``concierge_seller`` | ``POST /sellers`` | a seller, their team, and their calendar's busy blocks |

The seller collection is what makes the CRM Ownership rule and the availability
engine testable. WF-042 provisions the CRM records a rule reads; this feature
provisions the calendars a slot list is built from.

## Routes

Prefix ``/api/wf-051``.

| Method | Path | Purpose |
|---|---|---|
| GET | ``/vocabulary`` | every researched term, as data |
| GET | ``/inferences`` | each derived decision, its reason, and what it was chosen against |
| GET | ``/summary`` | counts by publish state and booking state |
| GET | ``/routers`` | every router |
| POST | ``/routers`` | declare a router, with the declaration rules enforced |
| GET | ``/routers/{router_id}`` | one router with its nodes |
| POST | ``/routers/{router_id}/publish`` | change the publish state and the deployment surfaces |
| GET | ``/sellers`` | every seller with a calendar |
| POST | ``/sellers`` | register a seller |
| GET | ``/rooms/{room_id}/sellers`` | the sellers in one room |
| POST | ``/rooms/{room_id}/route`` | the researched first call: route and qualify |
| GET | ``/rooms/{room_id}/route/{route_id}`` | read a session |
| GET | ``/rooms/{room_id}/route/{route_id}/slots`` | the slot list the modal would render |
| POST | ``/rooms/{room_id}/route/{route_id}/schedule-simple`` | the researched second call: commit a slot |
| GET | ``/rooms/{room_id}/bookings`` | every booking in a room |
| POST | ``/rooms/{room_id}/route/{route_id}/expire`` | run the researched Time Elapsed timer now |
| DELETE | ``/rooms/{room_id}/bookings/{booking_id}`` | cancel, which runs the Not Scheduled path |

Every write names a route the app serves. ``source`` is built from
``router.prefix`` at the call site and never written as a literal.

## The design floor

No emoji. Icons by path or by a shared ``PATHS`` name. ``min-h-11`` on every
control. Semantic tokens only, no raw hex. Status carried by a word beside the
colour, never by colour alone. Loading, error and empty states on every read.
``prefers-reduced-motion`` honoured by ``index.css``.

## Testing

``backend/tests/test_wf051.py`` covers the declaration rules, rule evaluation, the
availability engine, both calls, and every inference. ``test_wf051_http.py``
covers the HTTP surface, the audit-source rule against the live route table, and
the no-5xx-with-an-empty-body rule.