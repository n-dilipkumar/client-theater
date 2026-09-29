"""Every judgement call in WF-056, named and served over HTTP.

The research for this workflow is *specific about the wire and silent about
almost everything around it*. It publishes the endpoint paths, the tool names,
the permission vocabulary, the role rule, and four session rules stated in as
many words. It publishes no durations, no grid, no record shapes, and no
behaviour for a caller that sends something the research does not describe.

Each entry here is one of those silences: what this build chose, why, how to
change it, and what it would break. The point of collecting them is that a
judgement call left as a comment in a function body is one nobody re-reads, and
a wrong one becomes product behaviour without anyone noticing.

The four sourced session rules live in :data:`dsr.headless_booking.vocabulary
.SESSION_RULES` and are *not* duplicated here. An entry below that restates a
sourced rule is a rule that can drift from its source; an entry that *resolves*
an ambiguity around one is a decision a reviewer can disagree with.
"""

from __future__ import annotations

from typing import Any

#: The line that governs the failure rule, which is the one most likely to be
#: implemented the other way round.
SINGLE_USE_QUOTE = (
    "Sessions are single-use. On a schedule failure, do not retry the schedule call with the same "
    "routeId - start again from the discover or route step"
)

#: The line that governs the slot rule.
UTC_QUOTE = (
    "Slot times are UTC. The startTime in responses is ISO-8601 UTC; pass it back verbatim on the "
    "book call."
)

#: The line that governs the credential.
TOKEN_QUOTE = (
    "Admin generates a scoped API token in Command Center > Credentials (Generate Token), choosing "
    "the Schedule permission for the relevant section (Concierge / Scheduling-links / Handoff) plus "
    "Read where listing assets is needed. Token is shown once. Admins only."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "failure-consumes-the-session",
        "topic": "whether a *failed* schedule call leaves the session usable",
        "basis": SINGLE_USE_QUOTE,
        "value": {
            "consumes_on_success": True,
            "consumes_on_failure": True,
            "consumes_on_expiry": True,
            "does_not_consume": "a refusal that happens *before* the schedule call is reached",
            "states": ["booked", "failed", "expired"],
        },
        "why": (
            "The research's sentence names the failure case explicitly and prescribes the remedy: "
            "'start again from the discover or route step'. An implementation that consumed the "
            "session only on success would let a caller retry against a slot list that is now "
            "stale, which is exactly the failure the sentence forbids. The distinction that is "
            "*not* consumption is a refusal raised before the schedule call is reached at all - an "
            "unknown session, or a credential without the Schedule permission - because no "
            "schedule attempt was made and there is no result to invalidate."
        ),
        "change_it": "Session.consume in sessions.py, and the failure branch of HeadlessBooking.book.",
        "blast_radius": "Every booking failure. Turning this off is one boolean per branch.",
    },
    {
        "id": "verbatim-means-one-of-the-offered-strings",
        "topic": "how strictly 'pass it back verbatim' is enforced",
        "basis": UTC_QUOTE,
        "value": {
            "accepted": "any string that canonicalises to an offered slot's instant",
            "refused_no_designator": True,
            "refused_not_in_list": True,
            "non_zero_offset": "canonicalised, then refused as not-offered rather than as malformed",
        },
        "why": (
            "Verbatim has two halves and the strict reading of both is 'the string you got'. But a "
            "caller that re-serialises `2026-10-02T09:00:00Z` as `2026-10-02T09:00:00+00:00` has "
            "booked the instant it meant, and refusing it would be pedantry that teaches callers "
            "to stop using the API. So the comparison is on the instant, and a naive value is "
            "refused rather than guessed at - a string with no designator has no single instant, "
            "and choosing one for the caller is the failure the UTC rule exists to prevent."
        ),
        "change_it": "parse_start_time and Session.resolve_slot in sessions.py.",
        "blast_radius": "Every book call whose startTime is not byte-identical to a slot string.",
    },
    {
        "id": "slot-generation-is-a-grid",
        "topic": "how a slot list is computed from an interval and a calendar",
        "basis": (
            "The data flow says 'interval{startsAt,duration} -> routing + availability engine -> "
            "routeId + slot list', and that availability comes from Google/Outlook calendars. It "
            "publishes no grid, no working hours, no lead time and no cap."
        ),
        "value": {
            "grid_anchored_to": "the Unix epoch, so overlapping sessions produce identical strings",
            "default_slot_minutes": "the meeting length",
            "default_meeting_minutes": 30,
            "default_hours_local": "09:00-17:00 Mon-Fri",
            "default_lead_minutes": 30,
            "default_max_slots": 40,
            "configurable_on": "the asset, not the request",
            "overlapping_starts": "allowed if the grid is set finer than the meeting, and refused at the book call as slot_taken",
        },
        "why": (
            "A slot list is the product's whole input at call #1, and an unbounded one is not a "
            "decision a caller can make. The grid is anchored to the epoch rather than to each "
            "request's window so that two sessions over overlapping windows return *identical* "
            "strings for the same instants - a window-anchored grid would shift the lists against "
            "each other and a caller holding both would see one hour under two spellings. The "
            "start step defaults to the meeting length rather than to a fixed 30 minutes, which is "
            "the less obvious half and the more important one: an earlier version refused any "
            "asset whose length was not a multiple of the grid, to stop two overlapping starts "
            "appearing in one list. That forbids 45-minute meetings - the single most common "
            "length of a sales demo - to prevent a corner case, so it was replaced by the guard "
            "that actually matters. A finer grid is allowed, and the second booking is refused "
            "with the researched `slot_taken` reason, which is both a real protection and the "
            "failure the research already names."
        ),
        "change_it": "The defaults in availability.py and the asset fields in assets.py.",
        "blast_radius": "Every session's slot list, and therefore every booking.",
    },
    {
        "id": "local-time-is-one-fixed-offset",
        "topic": "how an asset's working hours are expressed in UTC",
        "basis": (
            "Availability comes from Google/Outlook calendars, which carry a real timezone. The "
            "research names no timezone handling and no working-hours configuration."
        ),
        "value": {"model": "one fixed utc_offset_minutes per asset or path", "dst_aware": False},
        "why": (
            "Working hours are a local wall-clock window, and the comparison has to be between "
            "local datetimes - a 90 minute meeting starting at 16:00 ends at 17:30 local, which "
            "is outside a 09:00-17:00 window, and taking the time-of-day of the local end would "
            "wrap at midnight and silently pass it. What this build does not model is daylight "
            "saving. A per-day offset is a per-asset table keyed on a timezone database, and the "
            "research supplies no timezone at all, so the honest thing is a fixed offset that a "
            "reviewer can see is insufficient rather than a plausible guess. A desk that moves its "
            "clocks twice a year has its working hours move by an hour until an asset is updated."
        ),
        "change_it": "The utc_offset_minutes field, and the local comparison in availability.slots.",
        "blast_radius": "Assets outside UTC, twice a year.",
    },
    {
        "id": "session-ttl-durations",
        "topic": "how long a session lives",
        "basis": (
            "The research establishes a server-side TTL, that Concierge's is settable per request "
            "as timeoutInMS, and that links and handoff use a server-side one. It publishes no "
            "duration for any of them."
        ),
        "value": {
            "concierge_ms": 900_000,
            "links_ms": 600_000,
            "handoff_ms": 900_000,
            "caller_bounds": [30_000, 1_800_000],
            "timeout_in_ms_on_links_or_handoff": "refused, not ignored",
            "source_served_at": "/api/wf-056/vocabulary under `ttl`, flagged sourced=false",
        },
        "why": (
            "A session is a hold on availability, so its lifetime is a promise about the future "
            "this layer cannot keep past the interval the slots were computed for. The numbers are "
            "therefore bounded rather than merely chosen, and the refusal on `timeoutInMS` for a "
            "server-side surface is deliberate: a silently-ignored setting is the kind of thing a "
            "caller debugs for an afternoon."
        ),
        "change_it": "DEFAULT_TTL_MS, MIN_TTL_MS and MAX_TTL_MS in vocabulary.py.",
        "blast_radius": "How long a slot list stays valid between the two calls.",
    },
    {
        "id": "failed-book-writes-nothing-but-its-record",
        "topic": "what a refused schedule call leaves behind",
        "basis": (
            "The research's data flow describes what a *successful* commit produces: 'meeting "
            "record + calendar invites + optional CRM writeback -> webhook Created push'. It says "
            "nothing about the failed case beyond the instruction not to retry."
        ),
        "value": {
            "meeting": "never written",
            "invites": "never written",
            "webhook": "never written",
            "session": "moved to a terminal state, which is the whole point",
            "call_log": "always written, refused or not",
        },
        "why": (
            "A refusal that left a meeting row behind would be counted as a write by anything "
            "reading the store, which is the opposite of what 'the meeting was not booked' means. "
            "The call log is the exception and is written for *every* attempt, because 'nothing "
            "happened, and here is why' is the thing a caller - or a reviewer, or the person "
            "holding the token - actually needs to read."
        ),
        "change_it": "HeadlessBooking.book in engine.py.",
        "blast_radius": "Every failure's footprint in the store.",
    },
    {
        "id": "invites-and-webhook-are-recorded-not-sent",
        "topic": "whether 'calendar invites are sent immediately' is actually performed",
        "basis": (
            "The research states 'Calendar invites are sent immediately' and that 'Bookings "
            "immediately emit the For New Meeting webhook'. It documents no SMTP credential, no "
            "calendar write scope and no reachable endpoint, and this product has none."
        ),
        "value": {
            "invites_written": True,
            "invites_sent": False,
            "webhook_written": True,
            "webhook_delivered": False,
            "delivery_field": "recorded, on every invite and every webhook event",
        },
        "why": (
            "The *commit* is the researched behaviour and it is implemented: an invite record and a "
            "webhook event are written in the same transaction as the meeting, so the two things "
            "the research says happen immediately cannot come apart. What is not implemented is "
            "transmission, and the records say so on every row. A record that claimed an invite was "
            "sent when nothing left the process would be a lie in the audit log, which is the one "
            "artefact this product promises is truthful."
        ),
        "change_it": "The delivery fields in engine.py. A real transport is one new class.",
        "blast_radius": "Every booking's delivery claims. The commit itself is unaffected.",
    },
    {
        "id": "crm-writeback-is-recorded",
        "topic": "what the 'optional CRM writeback' in the data flow does here",
        "basis": (
            "The data flow names 'optional CRM writeback' as part of what a commit produces, and "
            "the research's gaps section says plainly that no CRM endpoint for this was read."
        ),
        "value": {
            "writes_to_a_crm": False,
            "record_created": "yes, when the asset configures one",
            "off_by_default": True,
        },
        "why": (
            "The research lists the CRM as a data source and names a writeback as part of the "
            "commit, so the *record* of that writeback belongs on the meeting. Writing to a CRM "
            "would be a claim the product cannot back and the research cannot cite. Off by default "
            "so a demo does not imply an integration that does not exist."
        ),
        "change_it": "The crm_writeback block in engine.book.",
        "blast_radius": "One field on the meeting record.",
    },
    {
        "id": "token-lookup-by-id-or-by-secret",
        "topic": "how a headless call identifies itself",
        "basis": (
            "The research says a scoped token is generated and shown once, and that the same "
            "workflow is driven by 'a backend process, a custom frontend, or an AI assistant'. It "
            "does not describe an in-product caller."
        ),
        "value": {
            "accepted": ["the token itself", "the credential id"],
            "absent": "the call is made as the installation, at full scope, and the record says so",
            "require_credential": "a per-call flag that refuses the absent case; the switch a deployment flips",
            "both_sent_and_disagreeing": "refused, because quietly honouring one of them is how a permission check gets bypassed",
            "a_token_matching_nothing": "refused, never treated as absent",
            "recorded": "authorised_as, on the session and on every call-log row",
        },
        "why": (
            "A caller outside the app has only the token. A caller inside it has the id, and "
            "refusing the id would mean the in-product console could not exercise the permission "
            "rules at all - and a permission rule nothing exercises is decoration. The absent case "
            "is a *stated* third option rather than a fourth: making it explicit is what stops a "
            "reader from assuming every call was scoped, and `require_credential` is the switch a "
            "deployment flips to refuse it. The two cases that are *not* negotiable are the ones "
            "that would otherwise turn a scoped credential into full access: a token that matches "
            "no active credential, and a token and a credential id that name different ones. Both "
            "are refused rather than resolved, because a caller that sends both and expects the "
            "weaker to win has a bug, and quietly picking a winner is how a permission check gets "
            "bypassed."
        ),
        "change_it": "HeadlessBooking.authorise in engine.py.",
        "blast_radius": "Whether the permission rules can be bypassed by omitting the credential.",
    },
    {
        "id": "list-and-lookup-are-one-permission",
        "topic": "what the Read permission gates",
        "basis": (
            TOKEN_QUOTE + " 'plus Read where listing assets is needed' scopes Read to *listing*, "
            "and does not say whether reading one asset by its id is a different act."
        ),
        "value": {"read_gates": ["list", "read-one"], "schedule_gates": ["discover", "book"]},
        "why": (
            "The research ties Read to listing, but a caller holding a scheduling link's id and no "
            "Read permission has still not been shown anything, and fetching that one asset by id "
            "leaks exactly what a list would. Splitting the two would invent a permission the "
            "research does not name, and inventing a *weaker* one is the dangerous direction: a "
            "caller could enumerate assets one id at a time."
        ),
        "change_it": "The require_scope calls in engine.py.",
        "blast_radius": "Any caller holding a Schedule-only token.",
    },
    {
        "id": "stored-token-is-a-digest",
        "topic": "how 'token is shown once' is made true",
        "basis": TOKEN_QUOTE,
        "value": {
            "stored": "sha-256 digest plus a prefix and last four",
            "returned_once_by": "the create call",
            "readable_again_via": "nothing, including the core records API",
            "comparison": "constant-time",
        },
        "why": (
            "A token stored in the clear can be re-shown, and 'shown once' then means only 'not "
            "shown in this response'. This product's records are readable through the core "
            "API by anyone who can call it, so a cleartext token would be a credential sitting in "
            "a collection anybody can list. A digest makes the researched sentence true in the "
            "only sense that survives an audit."
        ),
        "change_it": "digest and verify in credentials.py.",
        "blast_radius": "Nothing. The token is unrecoverable by design, including here.",
    },
    {
        "id": "handoff-paths-single-path-elsewhere",
        "topic": "why a session holds a list of paths when the flow describes one list of slots",
        "basis": (
            "The WF-056 flow says call #1 'returns a routeId and a list of startTimes under "
            "schedulingData'. The handoff endpoint in the same research returns 'routingId + "
            "routers[].pathResults[].startTimes', and only the handoff schedule URL carries a "
            "{pathId}."
        ),
        "value": {
            "concierge": "one path, pathId null",
            "links": "one path, pathId null",
            "handoff": "one path per declared routing path",
            "pathId_on_a_single_path_session": "refused as path_not_offered",
        },
        "why": (
            "A flat list would have been simpler and would have quietly refused every handoff "
            "booking that named a path, which is the shape the research says handoff callers use. "
            "The two single-path surfaces keep exactly one entry with a null pathId, so a client "
            "that reads pathId and sends it back gets a clear refusal rather than a field that is "
            "silently ignored - the failure mode where a caller believes they filtered the slots "
            "when they did not."
        ),
        "change_it": "Session.scheduling_data and Session.resolve_slot in sessions.py.",
        "blast_radius": "The shape of schedulingData, and handoff bookings.",
    },
    {
        "id": "slot-taken-is-a-refusal-not-a-queue",
        "topic": "what happens when the chosen slot is gone",
        "basis": (
            "User-flow step 5: 'If step 2's session expired or the slot was taken, the caller "
            "re-runs step 1 with a fresh session'. The data flow names no waiting, no alternative "
            "slot and no queue."
        ),
        "value": {
            "on_taken": "refuse with slot_taken",
            "substitutes_another_slot": False,
            "waits": False,
            "consumes_the_session": True,
        },
        "why": (
            "Booking a *different* slot than the caller asked for, even a nearby one, is the worst "
            "of the available behaviours: the caller receives a meetingId and a calendar invite for "
            "a time they did not choose and never agreed to. Refusing and naming the remedy is what "
            "the research says, and it costs one extra call to the caller."
        ),
        "change_it": "The availability recheck in engine.book.",
        "blast_radius": "Only the double-booking case.",
    },
    {
        "id": "no-outbound-call",
        "topic": "whether this feature reaches Chili Piper or any other service",
        "basis": (
            "The research documents Chili Piper's endpoints, MCP tools, calendars, conferencing "
            "providers and a customer webhook endpoint. It documents no credential this product "
            "holds, and the product has no HTTP client for a scheduling vendor."
        ),
        "value": {
            "calls_outbound": False,
            "the_scheduler_is": "the audited store, with calendar blocks filed per host",
            "transport_seam": "HeadlessBooking is the only thing that knows where availability comes from",
        },
        "why": (
            "Writing a fake HTTP client to a real vendor would be a claim the product cannot back, "
            "and the researched part - the two calls, the single-use session, the verbatim UTC "
            "slot - is exercised against real stored rows through the same code path a transport "
            "would use. Swapping the calendar source for a real Google or Outlook read is a change "
            "to one method."
        ),
        "change_it": "HeadlessBooking.busy_by_key in engine.py.",
        "blast_radius": "Nothing today. It is the seam a real connector would fill.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced.

    The endpoint exists to show the reader where the line falls, which means
    showing what is on *both* sides of it. Sourced rules in
    :data:`dsr.headless_booking.vocabulary.SESSION_RULES`; assumptions here.
    """
    from dsr.headless_booking import vocabulary

    return {
        "count": len(INFERENCES),
        "sourced_quote": SINGLE_USE_QUOTE,
        "sourced": {
            "session_rules": [dict(entry) for entry in vocabulary.SESSION_RULES],
            "calls": [dict(entry) for entry in vocabulary.CALLS],
            "on_book": vocabulary.ON_BOOK,
            "webhook": {"name": vocabulary.WEBHOOK_NAME, "event": vocabulary.WEBHOOK_EVENT},
            "ttl": {
                "published": False,
                "caller_settable": ["concierge"],
                "server_side_only": ["links", "handoff"],
            },
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
