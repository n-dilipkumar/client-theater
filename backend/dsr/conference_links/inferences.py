"""Every judgement call in WF-059, in one inspectable place.

The research for WF-059 fixes a great deal by name - seven Location options,
thirty integration values, eight location types, four appsStatus fields, two
dynamic tags, one prohibition on reusing conference data. What it does not do is
say what happens at the edges of those, and the edges are where product
behaviour is actually decided.

Sourced and assumed are kept apart, and this module is the "assumed" half, served
at ``GET /api/wf-059/inferences`` so a reviewer can disagree with a *named*
entry instead of hunting through a diff. The sourced half is in
:mod:`dsr.conference_links.vocabulary`, and the engine's ``/vocabulary`` returns
both together - the point of the endpoint is showing the reader where the line
falls, which means showing what is on each side of it.

A judgement call left as a comment in a function body is one nobody re-reads,
and a wrong one becomes product behaviour without anyone noticing. Each entry
here is named, traceable to what the research does and does not say, bounded by a
``value`` saying what this build chose, and carries a ``change_it`` so it can be
changed without editing a function body.
"""

from __future__ import annotations

from typing import Any

from dsr.conference_links import provider_status, vocabulary as vocab

#: The line that governs the whole workflow: Google's warning.
REUSE_WARNING = vocab.REUSE_WARNING

#: The line that governs the mandatory connection.
CONNECTION_MANDATORY = vocab.CONNECTION_MANDATORY_QUOTE

#: The line that governs the swap, and the webhook that gives it a history.
SWAP_LINE = vocab.SWAP_PROVISIONS_QUOTE
PREVIOUS_LOCATION_LINE = vocab.PREVIOUS_LOCATION_QUOTE

#: The line that says what a failure report is *for*, without saying how many
#: retries are right.
APPS_STATUS_LINE = provider_status.APPS_STATUS_EVIDENCE

#: The research's own statement that it did not read the providers' own
#: meeting-creation endpoints. This is the gap that keeps a Zoom request body
#: out of this package.
PROVIDER_API_GAP = (
    "I did not read Zoom's `POST /users/{userId}/meetings` or Graph `POST /me/events` "
    "reference. The video-link workflow (#9) is evidenced via Google Calendar "
    "`conferenceData` + Cal.com's documented integration enum + Chili Piper's Meeting Type "
    "location options, not via Zoom's own API."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "gong-enum-value",
        "topic": "what integration value a Gong Location sends, given Cal's enum has no Gong",
        "basis": (
            "The research documents Cal's integration enum by name and it contains no `gong`. "
            "Gong is offered by the researched picker - 'Gong: This one generates a one-time Gong "
            "link; however, when clicked, Gong will redirect you to Zoom' - and is a Chili Piper "
            "provider, not a Cal one."
        ),
        "value": {"integration": "gong", "redirects_to": "zoom", "in_cal_enum": False},
        "why": (
            "Step 1 offers Gong as a first-class option and dropping it would drop a requirement "
            "the research states plainly, so the value is carried. `redirects_to: zoom` is "
            "recorded from the researched redirect sentence, because 'a Gong link that redirects "
            "to Zoom' and 'a Zoom link' are different links to the same meeting and a page that "
            "cannot tell them apart would tell a guest the wrong thing. `in_cal_enum: False` is "
            "published rather than hidden, so nobody reading a provisioned record believes this "
            "value would be accepted by Cal."
        ),
        "change_it": "vocabulary.PROVIDER_WIRE, connections.describe_provider.",
        "blast_radius": "Every Gong Location and every conference minted through one.",
    },
    {
        "id": "location-type-mapping",
        "topic": "which Cal location type the two non-obvious Location options produce",
        "basis": (
            "Cal accepts `address, attendeeAddress, attendeeDefined, attendeePhone, integration, "
            "link, phone, organizersDefaultApp`. The research names the Location option `Custom` "
            "and `In-Person Meeting` but does not say which type either produces."
        ),
        "value": {
            "google-meet": "integration",
            "zoom": "integration",
            "gong": "integration",
            "conference-details": "link",
            "in-person": "address",
            "custom": "address",
            "attendee-defined": "attendeeDefined",
        },
        "why": (
            "Four of the seven are named by the research or forced by it: three integration "
            "providers, `attendeeDefined` for Ask the Guest, and `link` for Conference Details, "
            "which extensibility calls 'a `link` escape hatch'. The other two describe a place "
            "rather than a call, and `address` is the only researched type that carries a "
            "human-entered location - `phone` and `organizersDefaultApp` both assert something "
            "specific that a free-text location is not. `In-Person Meeting` and `Custom` therefore "
            "share a type, which is correct: both are words a human typed."
        ),
        "change_it": "vocabulary.LOCATION_TYPE_WIRE, and locations.wire_location which reads it.",
        "blast_radius": "The wire preview, and any transport built on it.",
    },
    {
        "id": "conference-identity-is-derived",
        "topic": "how a conference's identity is chosen, and therefore how reuse is detected",
        "basis": REUSE_WARNING,
        "value": {
            "identity": "sha256(provider:booking_uid)[:16], prefixed conf_",
            "unique": True,
            "scope": "per-booking",
        },
        "why": (
            "Google's instruction is to 'always generate a unique conference for each event by "
            "using the `createRequest` field', and its warning is about reuse 'across different "
            "events'. A random id would make the prohibition a store lookup that a caller can "
            "forget; a derived id means both the minter and the checker compute the same answer "
            "with no shared state, so the create-then-retry path converges on one conference "
            "instead of racing to mint two."
        ),
        "change_it": "minting.conference_id, and minting.claim which reads the result.",
        "blast_radius": "Every conference identity, and therefore every join URL.",
    },
    {
        "id": "reuse-across-bookings-is-refused",
        "topic": "what happens when a caller offers a conference another booking already holds",
        "basis": REUSE_WARNING,
        "value": {"claim": "refused", "status": 409, "same_booking": "permitted"},
        "why": (
            "The warning describes a security failure - exposing meeting details to unintended "
            "users - so the answer is a refusal rather than a warning, and the message names both "
            "bookings because the difference between a correct retry and the failure the warning "
            "describes is entirely in which booking holds the id. The same booking re-claiming its "
            "own conference is the create-then-retry path and is permitted, because the warning is "
            "specifically about reuse 'across different events'."
        ),
        "change_it": "minting.claim, and ConferenceReuse in errors.py.",
        "blast_radius": "Only swaps and re-provisions that supply a conference id explicitly.",
    },
    {
        "id": "static-text-gaps-are-reported-not-refused",
        "topic": "whether a Conference Details Location with no text is refused",
        "basis": (
            "'Conference Details: This is a text field where you can manually enter the Location "
            "details. This option is normally used to include links, like static Zoom ones, for "
            "those who don't want to use one-time links.' The research does not say a blank one is "
            "refused."
        ),
        "value": {
            "conference_details": "reported",
            "connection_id": "enforced",
            "name": "enforced",
        },
        "why": (
            "An admin saving an empty Conference Details and filling it in later is a real "
            "workflow and a hard refusal would block it, so the gap is reported on the record and "
            "on the page. The one thing that is enforced is the connection on a one-time kind, "
            "because the connection is the researched mandatory step and a Location that cannot "
            "be provisioned is not a Location. The two differ because one is a missing fact the "
            "research requires and the other is a field the admin may fill in tomorrow."
        ),
        "change_it": "locations.missing_for.",
        "blast_radius": "Location create and patch responses only; nothing is refused over it.",
    },
    {
        "id": "exactly-one-default",
        "topic": "what happens when a Meeting Type's Location list has zero or two defaults",
        "basis": "features_tools: 'Meeting Type `Location` picker (multiple locations with a \"Set as Default\")'.",
        "value": {"zero_defaults": "refused", "two_defaults": "refused", "status": 409},
        "why": (
            "The researched feature is the Set as Default control, which only means something if "
            "exactly one Location is in force. A picker with two defaults answers 'which one?' with "
            "whichever row the store returned last, and one with none has no answer at all; both "
            "are refused rather than silently resolved, because a booking that inherited the wrong "
            "default is a prospect on the wrong link."
        ),
        "change_it": "DefaultLocationRequired in errors.py, raised from the engine's set-default route.",
        "blast_radius": "Any Location create, patch or remove on a Meeting Type that has more than one.",
    },
    {
        "id": "unchanged-swap-is-refused",
        "topic": "what happens when a swap would leave the booking on the same link",
        "basis": SWAP_LINE,
        "value": {
            "rule": "refused",
            "status": 409,
            "compared_on": "the resulting join URL, not the named provider",
        },
        "why": (
            "The research does not mention the case, and the researched sentence is 'Attendees are "
            "notified of the location change by email'. This build derives a conference's identity "
            "from the provider, the room and the booking, so swapping a Zoom booking to Zoom "
            "re-provisions onto the *same* conference and the *same* URL - a swap that would email "
            "every attendee that the meeting moved while handing back the link they already had. "
            "The comparison is therefore on the resulting URL rather than on the provider name, "
            "which is what makes the rule mean what it says: Gong to Zoom is a change because the "
            "link differs, and Zoom to Zoom is not one because it does not."
        ),
        "change_it": "swapping.same_location, the resulting_url argument of swapping.plan_swap, and engine._resulting_url.",
        "blast_radius": "Every swap whose resulting join URL matches the one the booking already has.",
    },
    {
        "id": "gong-to-zoom-is-a-swap",
        "topic": "whether moving a booking from Gong to Zoom counts as a location change",
        "basis": vocab.GONG_REDIRECT_QUOTE,
        "value": {"compare_on": ["provider", "meetingLocation"], "notifies": True},
        "why": (
            "'when clicked, Gong will redirect you to Zoom' means the two are different links to "
            "one meeting. A guest holding the Gong link and a guest holding the Zoom link are "
            "joining the same call by different routes, and the researched swap emails attendees "
            "when the link changes - which it does. Comparing on provider alone would have missed "
            "it, because both are 'a Zoom meeting' by another name."
        ),
        "change_it": "swapping.same_location, and the conference patch in minting.apply_to_booking.",
        "blast_radius": "Swaps to and from Gong only; every other provider is unaffected.",
    },
    {
        "id": "retry-budget-and-spacing",
        "topic": "how many times a failed provision is retried, and what happens after",
        "basis": APPS_STATUS_LINE,
        "value": {
            "retry_attempts": provider_status.RETRY_ATTEMPTS,
            "then": "fall back to the researched Conference Details static link, else Ask the Guest",
        },
        "why": (
            "The research gives the report's four fields and says an integration should 'retry or "
            "fall back'. It does not give a count, so the number is this build's and is named in "
            "provider_status rather than written into a loop. The fallback is chosen from the "
            "researched options rather than invented: a static link is one a rep configured "
            "precisely because they are willing to share a room that is not per-booking, and Ask "
            "the Guest is the researched option for when nobody has."
        ),
        "change_it": "provider_status.RETRY_ATTEMPTS and provider_status.fallback_for.",
        "blast_radius": "Every failed provision and every booking whose budget is spent.",
    },
    {
        "id": "connection-readiness",
        "topic": "what makes an Integrations connection usable rather than merely present",
        "basis": CONNECTION_MANDATORY,
        "value": {
            "required": ["provider", "host", "token_present", "state:connected"],
            "credential_stored": False,
            "link_host": "optional; defaults to the researched example host",
        },
        "why": (
            "The research says the connection is mandatory and that 'OAuth tokens per host' is a "
            "data source; it does not enumerate what makes one usable. Those four are this build's. "
            "The credential itself is deliberately not a field: this product writes an audit row "
            "in the same transaction as every change and mirrors it to JSONL, so a token on a "
            "record would be a token in the audit log, in the mirror, and in every export. "
            "`token_present` records that a credential exists without recording it. `link_host` is "
            "separate from `calendar_id` on purpose: a Workspace account id is not a domain, and "
            "the researched example link is `https://example.zoom.us/j/1234567890`, so the domain a "
            "guest's link is built on is its own setting with the researched host as the default."
        ),
        "change_it": "connections.REQUIRED_FIELDS, connections.readiness, and connections.normalise.",
        "blast_radius": "Every provision attempt, every join URL, and every connection write.",
    },
    {
        "id": "outbound-requests-are-built-not-sent",
        "topic": "whether this package calls Google, Zoom, Cal or Gong",
        "basis": PROVIDER_API_GAP,
        "value": {
            "sends": False,
            "builds": "the exact request, stored on the conference",
            "google_body": "sourced",
            "other_provider_bodies": "not asserted",
        },
        "why": (
            "The research states plainly that it did not read Zoom's or Graph's meeting-creation "
            "reference, so a Zoom request body here would assert a shape no source in this project "
            "supports. Google's body is sourced verbatim and is stored on every conference, so a "
            "reviewer can see the researched call - including the `conferenceDataVersion=1` and the "
            "`createRequest` the warning depends on - without a network call happening. The "
            "decision, the identity and the write are real; the send is the vendor's."
        ),
        "change_it": "minting.outbound_request, and minting.mint which stores its result.",
        "blast_radius": "Every conference record. A real transport would consume this field.",
    },
    {
        "id": "unresolved-dynamic-tag-is-left-visible",
        "topic": "what the invite body does with a tag it cannot resolve",
        "basis": (
            "user_flow step 3: 'the invite body can embed reschedule/cancel URLs via dynamic "
            "tags'. features_tools names `CP.Meeting.RescheduleUrl` and `CP.Meeting.CancelUrl`."
        ),
        "value": {"unresolved": "left in the body and reported", "blank_substitution": False},
        "why": (
            "Both researched tags resolve here, so this is about the path that would not. A body "
            "that renders `CP.Meeting.RescheduleUrl` is one a rep spots and fixes; a body that "
            "renders nothing is one that ships a blank link to a prospect and is found weeks "
            "later. Reported in `unresolved` so the page can warn before the invite is sent."
        ),
        "change_it": "swapping.render_invite.",
        "blast_radius": "The invite preview and any send that consumes it.",
    },
    {
        "id": "room-scope-is-a-key-not-an-acl",
        "topic": "what a room-scoped route means for access",
        "basis": "The product's rooms carry no ACL; see the note in dsr.conference_links.engine.",
        "value": {"scoping": "bookings are keyed to a room", "authorisation": "not claimed"},
        "why": (
            "A booking belongs to the room the buyer was looking at, so a room-scoped path is real "
            "and a booking in another room is a 404 here. Claiming that this is access control "
            "would be claiming something the store cannot back - there is no membership table to "
            "check - so the routes are scoped and the 404 says 'not in this room', which is what "
            "is actually true."
        ),
        "change_it": "engine._require_booking, and the /rooms/ paths on the router.",
        "blast_radius": "Every room-scoped read and write.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, served, with the sourced half beside the inferred one."""
    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "sourced_quote": {
            "conference_reuse": REUSE_WARNING,
            "connection_mandatory": CONNECTION_MANDATORY,
            "swap_provisions_and_notifies": SWAP_LINE,
            "previous_location": PREVIOUS_LOCATION_LINE,
            "apps_status": APPS_STATUS_LINE,
            "provider_api_gap": PROVIDER_API_GAP,
        },
        "rule": (
            "Every entry names a decision the research does not make, the sentence it rests on, "
            "the value this build chose, and where to change it. Nothing in this package is "
            "otherwise a judgement call."
        ),
    }


__all__ = ["INFERENCES", "describe"]
