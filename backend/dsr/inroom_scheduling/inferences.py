"""Every judgement call in WF-058, in one inspectable place.

The research for WF-058 is a **build** specification rather than a port: it
records five primary sources and quotes eight sentences, and almost everything else
in it is prose. The prose is the specification. This module is the other half - the
decisions this build made where the research is silent - served at
``/api/wf-058/inferences`` so a reviewer can disagree with a *named* entry instead
of hunting through a diff.

The sourced half is in :mod:`dsr.inroom_scheduling.vocabulary` and
:mod:`dsr.inroom_scheduling.routing`, and :func:`describe` returns both together.
The point of the endpoint is showing the reader where the line falls, which means
showing what is on each side of it.

A judgement call left as a comment in a function body is one nobody re-reads, and a
wrong one becomes product behaviour without anyone noticing. Each entry here is
named, traceable to what the research does and does not say, bounded by a ``value``
saying what this build chose, and carries a ``change_it`` so it can be changed
without editing a function body.
"""

from __future__ import annotations

from typing import Any

#: The sentences the research quotes that this build acts on directly. Published
#: with the registry so a reader can see the sourced side of the line without
#: opening the research document.
SOURCED_QUOTES: dict[str, str] = {
    "atoms": (
        "Cal.com Atoms are customizable React components that let you integrate Cal.com scheduling "
        "functionality directly into your application."
    ),
    "custom_booking_flow": (
        "**Custom booking flow**: Learn how to intercept a booking to introduce your custom flow and "
        "then submit the booking."
    ),
    "custom_slot_selection": (
        "**Custom slot selection flow**: Learn how to use handleSlotReservation for custom slot "
        "selection flows."
    ),
    "dynamic_usernames": (
        "Checking slots by usernames is used mainly for dynamic events where there is no specific "
        "event but we just want to know when 2 or more people are available."
    ),
    "reservation_duration": (
        "Make a slot not available for others to book for a certain period of time ... you can also "
        "specify custom duration for how long the slot should be reserved for (defaults to 5 minutes)."
    ),
    "reschedule_exclusion": (
        "bookingUidToReschedule: When rescheduling an existing booking, provide the booking's unique "
        "identifier to exclude its time slot from busy time calculations."
    ),
    "metadata_limits": (
        "Metadata must have at most 50 keys, each key up to 40 characters, and string values up to "
        "500 characters."
    ),
    "atoms_maintenance": (
        "@calcom/atoms is in maintenance mode ... the next generation of Atoms will be distributed as "
        "copy-and-paste components built on coss ui and API v2."
    ),
}


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "embed-is-rendered-here-not-from-atoms",
        "topic": "whether the Booker is the @calcom/atoms package or this product's own React",
        "basis": SOURCED_QUOTES["atoms_maintenance"]
        + " The same research still lists @calcom/atoms as the embed SDK.",
        "value": {
            "renders": "this product's own React components over API v2",
            "imports_atoms": False,
            "atoms_status": "published as data, not as a dependency",
            "reason": (
                "the research says the package is being retired towards exactly this shape, so "
                "building on it would be building on the part being retired"
            ),
        },
        "why": (
            "The research names the SDK and then says the next generation will be copy-and-paste "
            "components on API v2. Reading those two facts together, the embed belongs in this "
            "product. Importing a package the same document calls a maintenance-mode "
            "copy-and-paste target would be building on the thing being retired, and the package "
            "carries an OAuth redirect surface this product would then have to expose."
        ),
        "change_it": "The feature module's routes and the frontend folder; nothing in dsr.* imports the SDK.",
        "blast_radius": "How the embed looks, and whether a future Atoms release is a dependency change.",
    },
    {
        "id": "routing-fall-through-is-required",
        "topic": "what a routing answer that matches no rule does",
        "basis": (
            "GET /v2/routing-forms/slots: 'It will not actually save the response just return the "
            "routed event type and slots when it can be booked.' The research does not say what "
            "happens when no rule matches."
        ),
        "value": {
            "catch_all": "fallbackEventTypeId is required on every form",
            "on_no_match": "route to the catch-all, with matched_rule null and reason 'fallback'",
            "on_rule_opt_out": "routed false, reason 'rule_declined'",
            "on_no_catch_all": "the form cannot be created",
        },
        "why": (
            "Two answers are possible and both are bad alone. Routing nobody leaves the prospect on a "
            "form that appears broken. Routing to whatever event type happens to be first books the "
            "meeting with the wrong host, and nothing notices until the call. Requiring a catch-all "
            "makes the fall-through explicit, and reporting matched_rule: null distinguishes 'a rule "
            "sent you here' from 'nothing matched, so here is the default' - a distinction the page "
            "needs to show the right next step."
        ),
        "change_it": "normalise_form's fallbackEventTypeId requirement, and the final branch of routing.route.",
        "blast_radius": "Every routing form, and every prospect who answers one.",
    },
    {
        "id": "instant-requires-team-and-no-start",
        "topic": "what an instant booking does when it names no start",
        "basis": (
            "POST /v2/bookings 'supports standard, recurring (recurrenceCount, max 32), and instant "
            '("instant": true, team events only) bookings\'. The research does not say what an '
            "instant booking does when start is absent."
        ),
        "value": {
            "requires_kind": "team",
            "without_start": "book the soonest free slot on the grid",
            "with_start": "behaves as a standard booking on that slot",
            "never": "default start to now",
        },
        "why": (
            "The team-event rule is sourced and enforced. The no-start behaviour is not, and the two "
            "obvious answers are both wrong: defaulting to now creates a meeting in the past, and "
            "refusing outright makes 'instant' a word that only means something when the caller "
            "already knows the time. Taking the soonest free slot is what 'instant' is for, and it "
            "still refuses when the grid is empty rather than inventing a time."
        ),
        "change_it": "resolve_instant_start in bookings.py, and the empty-start branch of read_booking_request.",
        "blast_radius": "Only instant bookings, which are team events only.",
    },
    {
        "id": "recurrence-interval",
        "topic": "how far apart the occurrences of a recurring booking are",
        "basis": "recurrenceCount, max 32. No interval, frequency or end date is documented.",
        "value": {
            "interval": "weekly",
            "stored_as": "one booking record carrying every occurrence start",
            "collisions_on_later_occurrences": "not re-checked at booking time",
        },
        "why": (
            "Weekly is the reading that adds the fewest assumptions to a payload with one number in "
            "it. Storing the occurrences on one record rather than creating 32 records keeps a "
            "booking's history in one place, which matters because a cancel or a reschedule then has "
            "one thing to move. Later occurrences are not re-checked for collisions: a series booked "
            "this month may collide with something booked next month, and refusing the whole series "
            "because a date four weeks out is taken would be worse than booking it and letting the "
            "cancel path handle it."
        ),
        "change_it": "describe_window in bookings.py, and the recurrence block of booking_payload.",
        "blast_radius": "Every recurring booking; the first occurrence is checked like any other.",
    },
    {
        "id": "recurrence-refused-not-truncated",
        "topic": "what a recurrenceCount above 32 does",
        "basis": "recurring (recurrenceCount, max 32).",
        "value": {"action": "refuse with the value and the maximum", "truncates": False},
        "why": (
            "Truncating to 32 would leave a prospect with a calendar holding 32 of the 40 meetings "
            "they asked for, and a truncated success is indistinguishable from a complete one. The "
            "refusal names both numbers so the caller can resubmit with a count they meant."
        ),
        "change_it": "The count check in read_booking_request.",
        "blast_radius": "Only requests above 32, which are mistakes.",
    },
    {
        "id": "metadata-room-context-before-limits",
        "topic": "when the room context is merged into booking metadata",
        "basis": (
            "extensibility: bookingFieldsResponses + metadata (with the three documented limits) to "
            "'carry deal-room context into every booking'."
        ),
        "value": {
            "keys": ["dsr_room_id", "dsr_account", "dsr_booking_source"],
            "merged": "before the three limits are applied",
            "on_overflow": "refuse",
        },
        "why": (
            "Merging afterwards would let a full payload push the booking over a limit the caller was "
            "inside, and the limit that breaks would be one they did not write. It would also mean "
            "the room context is the thing that gets dropped, which defeats the seam's purpose. So "
            "the merge happens first and the refusal names the offending key, which is almost always "
            "the caller's own."
        ),
        "change_it": "room_metadata in attendees.py, and its call site in engine.book.",
        "blast_radius": "Every booking; the three keys are short and well inside the limits.",
    },
    {
        "id": "metadata-non-string-values-checked-as-strings",
        "topic": "whether the 500-character limit applies to a number in metadata",
        "basis": SOURCED_QUOTES["metadata_limits"] + " The limit is stated for string values.",
        "value": {
            "check": "every value's string form",
            "booleans": "true / false",
            "nulls": "empty string",
        },
        "why": (
            "A limit stated for one type that silently did not apply to another would be a limit a "
            "caller could not rely on, and metadata is JSON so a number is as easy to send as a "
            "string. Checking the string form is the reading that makes the documented number mean "
            "something."
        ),
        "change_it": "validate_metadata in attendees.py.",
        "blast_radius": "Metadata values that are not strings; none of the seeded ones are long.",
    },
    {
        "id": "reschedule-moves-rather-than-duplicates",
        "topic": "what bookingUidToReschedule does to the existing booking",
        "basis": SOURCED_QUOTES["reschedule_exclusion"]
        + " The research says what the field is for; it does not say what happens to the old booking.",
        "value": {
            "action": "update the existing record, keeping its uid",
            "records": "moved_from holds the previous start",
            "re_fires_booking_created": False,
        },
        "why": (
            "The field exists to exclude a booking's own slot from busy time. If submitting it created "
            "a second record, the first would still hold the old time and a booking history would "
            "contain a meeting nobody is attending. Moving the record keeps one meeting in one place. "
            "BOOKING_CREATED is not re-fired because this is a move, not a new booking, and a "
            "consumer that treated it as one would notify the prospect twice."
        ),
        "change_it": "The existing-booking branch of engine.book and _move_booking.",
        "blast_radius": "Only requests that name a bookingUidToReschedule.",
    },
    {
        "id": "holds-are-not-hosts",
        "topic": "whether a live hold makes a host busy",
        "basis": (
            "POST /v2/slots/reservations: 'Make a slot not available for others to book for a certain "
            "period of time.' The research says the slot is unavailable, not the host."
        ),
        "value": {
            "hold_blocks": "the slot it was taken on",
            "hold_does_not_block": "every other slot, and the same host's other slots",
            "hold_consumed_only_by": "a booking for the slot the hold names",
        },
        "why": (
            "A hold is a claim on one slot. Treating it as host-busy would make a prospect's hold on "
            "09:00 also block 10:00, 11:00 and the rest of the day, and would make a reschedule "
            "impossible: a prospect moving their own meeting from 09:00 to 10:00 would find 10:00 "
            "blocked by their own hold. So holds and busy time are separate structures, and a "
            "booking consumes a hold only when it is for the same slot."
        ),
        "change_it": "Occupancy in availability.py, and _busy_entries in engine.py.",
        "blast_radius": "Every hold, and every reschedule.",
    },
    {
        "id": "holds-expire-by-the-clock-not-by-a-sweep",
        "topic": "what makes a hold stop protecting its slot",
        "basis": (
            "automations: 'no user action needed for the hold to expire - a reservation auto-expires "
            "after reservationDuration'."
        ),
        "value": {
            "live_while": "now < reservationUntil",
            "reads": "report the computed state beside the stored one, and never write",
            "materialised_on": "creating a hold, creating a booking",
            "fields": ["expired_at (when it lapsed)", "noticed_at (when this build saw it)"],
        },
        "why": (
            "'Auto-expires' with 'no user action needed' is a statement about time, not about a job, so "
            "the answer must be the same on every read with no background process. Reads therefore "
            "never write - a GET that quietly rewrites rows is a surprise nobody can audit - and the "
            "two write paths where a stale row would change an answer are where the row is corrected. "
            "Both timestamps are kept because the hold lapsed at one moment and the product noticed at "
            "another, and conflating them is how a hold looks five minutes longer-lived than it was."
        ),
        "change_it": "read_hold in holds.py, and _reap in engine.py.",
        "blast_radius": "Every hold, and every read of one.",
    },
    {
        "id": "read-only-booking-fields-refuse-rather-than-override",
        "topic": "what happens when a prospect answers a read-only field differently",
        "basis": "features_tools: 'booking fields (prefill / read-only)'.",
        "value": {
            "read_only_without_prefill": "refused at configuration time",
            "submitted_value_differs": "refused, naming the prefilled and submitted values",
            "silent_override": False,
            "undeclared_answer": "refused",
        },
        "why": (
            "Silently keeping the prefilled value would record a booking that differs from the one the "
            "prospect believes they submitted, which is the kind of discrepancy nobody finds until a "
            "quarterly review. A read-only field is also required to be prefilled, or the embed would "
            "show a value the prospect can neither change nor trace. An answer to a field the event "
            "type does not declare is refused rather than stored, because nothing validates it and it "
            "would read back as if the vendor had asked."
        ),
        "change_it": "apply_booking_fields in events.py.",
        "blast_radius": "Event types with prefilled or read-only fields, and every booking through them.",
    },
    {
        "id": "booking-requires-a-live-token",
        "topic": "whether a booking needs a live OAuth token",
        "basis": (
            "user_flow step 1: 'stands up an OAuth client so the app can act on behalf of a scheduling "
            "user'. data_flow: 'OAuth access token -> Booker component calls Cal API v2 ... -> "
            "POST /v2/bookings'."
        ),
        "value": {
            "required_for": ["reserving a slot", "creating a booking"],
            "not_required_for": ["reading the slot grid", "reading holds", "routing"],
            "no_token": "refused, reason no_token",
            "expired": "refused, reason expired",
            "live_but_no_booking_scope": "refused, reason no_booking_scope",
        },
        "why": (
            "The whole data flow starts at the token, so a booking without one is a booking with no "
            "scheduling user behind it. Reads are exempt because a grid a rep cannot preview is a "
            "grid they cannot configure, and refusing a read would make configuring the embed "
            "impossible before the token exists. The three reasons are told apart because two of them "
            "need a reconnect and one needs a different scope - sending somebody to reconnect for a "
            "scope problem wastes an afternoon."
        ),
        "change_it": "token_state and require_live_token in embeds.py, and their call sites in engine.py.",
        "blast_radius": "Every room whose embed's client has no live token.",
    },
    {
        "id": "no-token-value-is-stored",
        "topic": "what this product keeps about an OAuth grant",
        "basis": "data_flow: 'OAuth access token'. The research says the token is the input; it does not say to store it.",
        "value": {
            "stores": ["subject", "scopes", "granted_at", "expires_at"],
            "stores_token_value": False,
            "refuses": "client_secret, access_token, api_key and similar field names",
        },
        "why": (
            "The audit log, the schema explorer and the room page all read these records, and a live "
            "credential stored in a table they can read is a credential three features away from being "
            "displayed. The existence and the expiry are the only two things this product needs to "
            "answer 'can this room book?', so the rest is not stored. A pasted secret is refused by "
            "name rather than dropped, because somebody who pastes a token into client_secret needs to "
            "be told, not quietly ignored."
        ),
        "change_it": "_reject_secrets in embeds.py, and grant_token.",
        "blast_radius": "Any client write that carries a credential-shaped field.",
    },
    {
        "id": "embed-lives-on-the-room-row",
        "topic": "where a room's embed configuration is stored",
        "basis": (
            "user_flow step 5: 'The prospect books entirely in-room'. The research does not say where "
            "the embed configuration is kept."
        ),
        "value": {
            "stored_on": "the room row, under the 'scheduling' field",
            "collections_used": "none for the embed itself",
            "room_also_carries": ["last_hold", "last_booking", "bookings (capped)"],
            "cap": 20,
        },
        "why": (
            "Every write route in this feature is room-scoped because of step 5, and a room-scoped read "
            "that had to be told which embed to use would not be room-scoped in any useful sense. The "
            "capped bookings list is a convenience copy: a rep glances at it, and a capped copy cannot "
            "answer a question about the eleventh booking, which is why the booking records are what "
            "you query."
        ),
        "change_it": "ROOM_FIELD, ROOM_BOOKING_LIMIT, and annotate_room in engine.py.",
        "blast_radius": "Every room-scoped route, and the shape of a room row.",
    },
    {
        "id": "dynamic-usernames-needs-a-known-host",
        "topic": "what a usernames query answers for a host with no known working hours",
        "basis": SOURCED_QUOTES["dynamic_usernames"],
        "value": {
            "unknown_hosts": "refused, naming them",
            "default_schedule": "never invented",
            "min_available_hosts": 2,
        },
        "why": (
            "The researched feature is a way to find when two or more people are available. A default "
            "nine-to-five for a stranger is not a person's availability, it is a fabrication, and it "
            "offers slots nobody can take - a failure that appears minutes later as a booking that "
            "could not be confirmed. Refusing up front is the honest answer and names what to fix. "
            "Two is the researched number, not a configurable knob, because the sentence says '2 or "
            "more' as the definition of the feature."
        ),
        "change_it": "_hosts_for_usernames in engine.py, and read_selector in availability.py.",
        "blast_radius": "Dynamic slot queries only.",
    },
    {
        "id": "embed-event-names-and-css-variables-are-ours",
        "topic": "the names of the embed events and the CSS custom properties",
        "basis": (
            "apis_hit: 'Embed events + CSS custom properties for styling', with two documentation "
            "pages cited. Neither the event names nor the property names are quoted."
        ),
        "value": {
            "events": "published under this product's own names, six of them",
            "css_variables": "--dsr-cal-*",
            "unknown_property": "refused at configuration time",
        },
        "why": (
            "The research cites the documentation and quotes none of the names, so reproducing them "
            "from memory would be a guess presented as a source. Namespacing them under this product "
            "keeps the claim honest and makes a collision with a real Cal variable impossible. An "
            "unknown property is refused rather than ignored, because a typo'd custom property that "
            "is accepted and has no effect is the worst outcome for somebody styling an embed."
        ),
        "change_it": "EMBED_EVENTS and EMBED_CSS_VARIABLES in vocabulary.py.",
        "blast_radius": "Styling an embed; no booking behaviour.",
    },
    {
        "id": "routing-operators",
        "topic": "which comparisons a routing rule can make",
        "basis": "The research names routing forms and says slots are calculated from a routing form response. It never describes a rule.",
        "value": {
            "operators": [
                "equals",
                "not_equals",
                "contains",
                "starts_with",
                "greater_than",
                "less_than",
                "in",
                "exists",
            ],
            "numeric_comparison_on_a_non_number": "does not match; the catch-all handles it",
            "string_comparison": "trimmed and case-insensitive",
        },
        "why": (
            "These are the comparisons a sales qualification form can actually need, and publishing the "
            "set means a client renders its rule editor from the same list the evaluator uses - a "
            "compiled list in the frontend would drift. A numeric operator on a non-number does not "
            "match rather than coercing, so a form answering 'a few' to a headcount question falls "
            "through to the catch-all visibly instead of being silently coerced to zero."
        ),
        "change_it": "OPERATORS and rule_matches in routing.py.",
        "blast_radius": "Routing only.",
    },
    {
        "id": "no-outbound-scheduling-call",
        "topic": "whether this feature calls Cal.com",
        "basis": (
            "The research documents request shapes, headers, versions and field limits precisely. It "
            "documents no endpoint this product can reach, and the product has no Cal account."
        ),
        "value": {
            "calls_outbound": False,
            "the_calendar_is": "the audited store, queried the way a slot query would query Cal",
            "headers_recorded": [
                "cal-api-version on slots and reservations",
                "cal-api-version on bookings",
            ],
        },
        "why": (
            "A fake HTTP client pointed at a real vendor would be a claim the product cannot back, and "
            "the two API versions - 2024-09-04 for slots, 2026-02-25 for bookings - would be two "
            "different behaviours to fake. The availability, hold and booking rules are the researched "
            "part and they are exercised against real rows through the same query path, so swapping "
            "in a transport is a change to this one file rather than a rewrite."
        ),
        "change_it": "Nothing in dsr.* opens a socket; a real transport replaces the reads and writes in engine.py.",
        "blast_radius": "Nothing today. It is the seam a real integration would use.",
    },
    {
        "id": "busy-time-is-global-and-seated-bookings-take-a-seat",
        "topic": "whose bookings and holds make a host busy, and what a seated booking occupies",
        "basis": (
            "user_flow step 5 scopes the *booking page* to a room: 'The prospect books entirely "
            "in-room'. Nothing says a host's calendar is per room. features_tools names 'seated "
            "events' and says nothing about what a seated booking blocks, or about seats on other "
            "kinds."
        ),
        "value": {
            "busy_time": "global: every booking and every live hold, whichever room made it",
            "holds": "global too, because a hold claims a slot on a host's calendar",
            "room_scope_applies_to": "which bookings and holds a room *lists*",
            "a_hold_a_booking_presents": "excluded from that booking's own grid, or the last step refuses",
            "seated_requires_seats": True,
            "seats_on_other_kinds": "refused",
            "a_seated_booking_makes": "one seat taken, not the host busy",
            "held_seats_counted": False,
        },
        "why": (
            "Two rooms sharing an event type share the host's calendar, so scoping availability to one "
            "room would let room B offer 09:00 to a prospect that room A already booked - and the "
            "double-booking would be invisible until the call. The exclusion is the one deliberate "
            "exception, and it is the researched one: a booking presenting its own hold is submitting "
            "*against* that claim, so treating it as somebody else's is exactly what makes 'hold the "
            "slot, then submit' fail at the last step. That exclusion is scoped to the room as well, "
            "so a bookingUidToReschedule naming another room's booking cannot make that booking "
            "invisible. Seated events are several people in one meeting, so a seated booking occupies a "
            "seat rather than the host's calendar; counting the first one as host-busy would make a "
            "four-seat event bookable once, and the failure is invisible because the grid simply stops "
            "offering times. A hold is a claim on a slot rather than a place in the room, so it "
            "contributes no seat: counting it would let five prospects hold the same last seat and "
            "discover it at submit time."
        ),
        "change_it": (
            "_busy_entries and _occupancy in engine.py, the room_id argument to _grid, and the seats "
            "branch of normalise_event_type."
        ),
        "blast_radius": "Every slot grid, every room that shares an event type, and every seated event.",
    },
    {
        "id": "calendar-connections-are-configuration",
        "topic": "what a Google/Outlook/Apple connect actually does",
        "basis": (
            "user_flow step 2 and data_sources both mention calendar-connect buttons and connected "
            "calendars. The research documents no endpoint for them."
        ),
        "value": {
            "stored": ["provider", "host", "label", "read_only", "write_enabled"],
            "affects_booking": False,
            "idempotent": "per provider and host",
        },
        "why": (
            "The buttons are part of the embed, not of the booking path, and nothing in the researched "
            "data flow depends on a connected calendar. Storing them as configuration keeps the page "
            "honest - it can show which providers are connected and which are still to connect - "
            "without claiming a connection this product cannot make. Idempotent per provider and host "
            "because a connect button that stays enabled after a successful connect looks broken."
        ),
        "change_it": "connect_calendar and normalise_calendar_connection.",
        "blast_radius": "The embed page. No booking behaviour.",
    },
    {
        "id": "attendee-required-fields",
        "topic": "which attendee fields a booking cannot do without",
        "basis": "data_flow names 'attendee' and nothing about its shape.",
        "value": {
            "name": "required",
            "email": "required, must look like an address",
            "timeZone": "inherited from the room's embed when absent",
            "language": "optional",
            "phoneNumber": "optional, carried when present",
            "accepts_flat_fields": ["attendeeName", "attendeeEmail"],
        },
        "why": (
            "A calendar invite needs a name and a reachable address, and refusing here is friendlier "
            "than an invite that bounces - but the research does not say so, and the timeZone default "
            "is a choice: inheriting the room's configured zone is better than guessing the prospect's "
            "or silently using UTC, and a booking whose zone is wrong is a meeting at the wrong hour. "
            "The flat spellings are accepted because a browser form posts flat fields and a form that "
            "has to be reshaped before it can book is a form somebody will not finish."
        ),
        "change_it": "normalise_attendee in attendees.py.",
        "blast_radius": "Every booking.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced."""
    from dsr.inroom_scheduling.routing import OPERATORS
    from dsr.inroom_scheduling.vocabulary import (
        BOOKING_API_VERSION,
        EMBED_BOOKING_COMPONENTS,
        EMBED_COMPONENTS,
        EVENT_TYPE_KINDS,
        MAX_RECURRENCE_COUNT,
        METADATA_LIMITS,
        SLOTS_API_VERSION,
        published_vocabulary,
    )

    return {
        "count": len(INFERENCES),
        "sourced": {
            "quotes": dict(SOURCED_QUOTES),
            "api_versions": {"slots": SLOTS_API_VERSION, "bookings": BOOKING_API_VERSION},
            "max_recurrence_count": MAX_RECURRENCE_COUNT,
            "metadata_limits": dict(METADATA_LIMITS),
            "embed_components": list(EMBED_COMPONENTS),
            "embed_booking_components": list(EMBED_BOOKING_COMPONENTS),
            "event_type_kinds": list(EVENT_TYPE_KINDS),
        },
        "vocabulary": published_vocabulary(),
        "routing_operators": dict(OPERATORS),
        "inferences": [dict(entry) for entry in INFERENCES],
    }
