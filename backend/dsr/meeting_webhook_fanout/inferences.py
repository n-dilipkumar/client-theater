"""Every judgement call this package makes, in one inspectable place.

The research for WF-066 is unusually specific about the *sender's* contract: it
names the three subscription types, quotes the signing rule step by step, gives
both header definitions, lists the payload fields, and states twice what replay
protection belongs to whom. What it does not do is say how this deployment
behaves on the edges of that description, and the edges are where a build has to
decide something.

Those decisions are collected here rather than left as comments in function
bodies, because a judgement call in a comment is one nobody re-reads and a wrong
one becomes product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-066/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

Two entries are not inferences at all. ``hmac-signing-rule`` and the three
subscription types are facts the research states outright, and they are listed
because a register of decisions that hid its own certainties would be useless to
a reader checking whether anything here is load-bearing. The last entry is a
boundary rather than a judgement call, and is listed for that reason: ``not-built``
records what this build deliberately does not do, because a feature whose page
does not show its own edges overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.meeting_webhook_fanout.signing import MAX_AGE_SECONDS
from dsr.meeting_webhook_fanout.transport import DEFAULT_TIMEOUT_SECONDS
from dsr.meeting_webhook_fanout.vocabulary import (
    COLLECTIONS,
    DEFAULT_DEPLOYMENT_MODE,
    DEPLOYMENT_MODES,
    EVENT_TYPES,
    PAYLOAD_TYPES,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
)

#: The sentence from the research that governs the whole sending half.
SOURCED_QUOTE = (
    "Step 2: Construct the signed payload by concatenating the timestamp and the raw "
    "request body, separated by a period: `{timestamp}.{raw_request_body}`"
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "hmac-signing-rule",
        "topic": "the exact bytes a signature covers",
        "topic_note": "a fact the research states outright, not an inference",
        "basis": (
            "Quoted verbatim: 'Step 2: Construct the signed payload by concatenating the "
            "timestamp and the raw request body, separated by a period: "
            "{timestamp}.{raw_request_body}'. The header table adds 'X-Chili-Signature | "
            "HMAC-SHA256 signature of the payload (hex-encoded)' and 'X-Chili-Timestamp | "
            "Unix timestamp (seconds) when the request was signed'. Nothing here is a "
            "choice, so this entry exists to say which part of the build is not negotiable."
        ),
        "value": {
            "algorithm": "HMAC-SHA256",
            "signing_input": "{timestamp}.{raw_body}",
            "separator": "a single period",
            "signature_encoding": "hex",
            "signature_header": SIGNATURE_HEADER,
            "timestamp_header": TIMESTAMP_HEADER,
            "timestamp_unit": "unix seconds",
        },
        "change_it": "dsr/scheduling_meetings/signing.py:signing_input. Do not change it.",
    },
    {
        "id": "cancel-maps-to-deleted",
        "topic": "which payload type a cancellation fires",
        "basis": (
            "The research lists the three subscription names and the three payload values "
            "separately: 'For New Meeting', 'For Meeting Update', 'For Canceled Meeting' "
            "against 'type: Created|Updated|Deleted'. The mapping is positional and the "
            "third name is the only one of the three that is a deletion, so 'For Canceled "
            "Meeting' fires 'Deleted'. The research does not say so in those words."
        ),
        "value": {
            "new_meeting": "Created",
            "meeting_update": "Updated",
            "canceled_meeting": "Deleted",
        },
        "change_it": "dsr/scheduling_meetings/vocabulary.py:EVENT_TYPES.",
    },
    {
        "id": "one-flat-envelope-for-the-three-chili-types",
        "topic": "the shape of the payload this sender emits",
        "basis": (
            "The research quotes two envelopes and warns against mixing them silently: Cal "
            'sends \'{"triggerEvent", "createdAt", "payload": {...}}\' for most events '
            "but 'MEETING_STARTED and MEETING_ENDED are exceptions - they use a flat "
            "payload where booking fields are at the top level alongside triggerEvent, "
            "with no payload wrapper'. The Chili Piper payload field list is flat. This "
            "build fires only the three Chili Piper subscription types and none of Cal's, "
            "so it has no occasion to emit a wrapper. Reproducing Cal's wrapper as well "
            "would put three shapes in one product and turn the research's warning into a "
            "real risk. Jev chose the flat envelope at confidence 0.96, audit "
            "jev-20261004T070110-25984-70596."
        ),
        "value": {
            "envelope": "flat",
            "payload_wrapper": "never sent",
            "event_types": [row["id"] for row in EVENT_TYPES],
            "payload_types": list(PAYLOAD_TYPES),
            "out_of_scope": ["MEETING_STARTED", "MEETING_ENDED", "RECORDING_READY"],
        },
        "change_it": (
            "dsr/scheduling_meetings/payloads.py:build_payload. Adding a wrapper is a "
            "per-subscription envelope mode, and it is a new collection field, not a "
            "migration."
        ),
    },
    {
        "id": "replay-protection-belongs-to-the-consumer",
        "topic": "whether the sender refuses a delivery on age",
        "basis": (
            "Quoted: 'Replay protection is left to the consumer (MAX_AGE_SECONDS = 300)'. "
            "The room ships the timestamp header and publishes the window. It does not "
            "enforce it, because a sender enforcing a freshness window on its own "
            "outbound POSTs refuses a correctly signed delivery whose clock runs fast, "
            "and that is a clock bug wearing the costume of a security control."
        ),
        "value": {
            "sender_enforces_window": False,
            "ship_timestamp_header": True,
            "published_window_seconds": MAX_AGE_SECONDS,
            "window_owner": "the consumer",
        },
        "change_it": (
            "dsr/scheduling_meetings/signing.py:verify takes window_seconds and the "
            "sender never passes one. A subscriber uses verify with the shipped window."
        ),
    },
    {
        "id": "saas-url-policy-is-a-room-setting",
        "topic": "which subscriber URLs this deployment accepts",
        "basis": (
            "The research gives two deployment modes that disagree: 'Cal.com SaaS: Only "
            "HTTPS URLs are accepted. HTTP, private/internal IP addresses (e.g., 10.x.x.x, "
            "192.168.x.x, 127.0.0.1), and localhost are blocked. Self-hosted: Both HTTP and "
            "HTTPS URLs are accepted, and private IP addresses are allowed for internal "
            "webhooks.' It does not say which one this product is."
        ),
        "value": {
            "this_deployment": DEFAULT_DEPLOYMENT_MODE,
            "per_room_setting": "deployment_mode",
            "modes": {row["id"]: row["schemes"] for row in DEPLOYMENT_MODES},
            "private_addresses": {row["id"]: row["private_addresses"] for row in DEPLOYMENT_MODES},
        },
        "change_it": (
            "Set deployment_mode on the room to 'saas' to turn the policy on. "
            "dsr/scheduling_meetings/fanout.py:validate_url."
        ),
    },
    {
        "id": "subscription-identity-is-url-plus-event-type",
        "topic": "what makes two subscriptions the same subscription",
        "basis": (
            "The research states the fan-out is unbounded in both directions: 'You are not "
            "limited by the number of webhooks you have', and 'multiple webhook types may "
            "have the same webhook URL, and multiple webhook URLs for the same type'. So "
            "one URL may carry several event types and one event type may have several "
            "URLs. Neither half identifies a subscription on its own, so the pair does. A "
            "second row for the same pair would deliver the same event twice to the same "
            "address, and a subscriber counting meetings would count them wrong."
        ),
        "value": {
            "identity": ["url", "event_type"],
            "fan_out": "unbounded",
            "duplicate_pair": "refused",
            "not_a_column_on_the_meeting": (
                "a many-to-many is the only shape that expresses both directions"
            ),
        },
        "change_it": (
            "dsr/scheduling_meetings/fanout.py:MeetingWebhookFanout.subscribe. The lookup is "
            "a find() on two JSON paths, so adding a third dimension needs no migration."
        ),
    },
    {
        "id": "the-room-signs-because-no-screen-sets-a-secret",
        "topic": "where the HMAC secret comes from",
        "basis": (
            "Quoted: 'Optionally emails support to obtain the tenant's HMAC signing secret "
            "(not shown in the UI).' No vendor screen in the researched flow sets a secret, "
            "which is a reason the room has to carry one rather than a reason to refuse to "
            "sign. The create route accepts a secret when the admin has one, and the room "
            "supplies one when they do not."
        ),
        "value": {
            "source": "support, per tenant",
            "shown_in_the_vendor_ui": False,
            "room_supplies_one": True,
            "per_room": True,
            "rotation": "a write to the key collection, audited",
        },
        "change_it": "dsr/scheduling_meetings/fanout.py:MeetingWebhookFanout.set_secret.",
    },
    {
        "id": "one-attempt-and-a-person-redelivers",
        "topic": "whether the sender retries on its own",
        "basis": (
            "The three sources name no timeout, no retry count and no backoff ladder. A "
            "sibling feature in this repository has all three, and copying them here would "
            "be this build inventing a policy the evidence does not state. One attempt is "
            "what the evidence supports."
        ),
        "value": {
            "attempts_per_event": 1,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "redelivery": "a route a person calls",
            "retryable_flag": "advice for the person reading the log, never a schedule",
        },
        "change_it": (
            "dsr/scheduling_meetings/fanout.py:MeetingWebhookFanout.emit. A ladder needs a "
            "policy the research does not state, so it would need its own evidence."
        ),
    },
    {
        "id": "the-delivered-count-is-per-subscription-not-per-event",
        "topic": "what one fan-out produces",
        "basis": (
            "The fan-out is unbounded, so one event can produce no rows at all (every "
            "matching row disabled) or many. Counting deliveries rather than events is "
            "what lets a reader tell those apart, and the research's own summary asks for "
            "'the states that are not all successes'."
        ),
        "value": {
            "event_row": "one per serialised and signed event",
            "delivery_rows": "one per enabled subscription it reached",
            "skip_reasons": ["subscription_disabled", "no_enabled_subscriptions"],
        },
        "change_it": "dsr/scheduling_meetings/fanout.py:MeetingWebhookFanout.emit.",
    },
    {
        "id": "not-built",
        "topic": "what this workflow deliberately does not do",
        "topic_note": "a boundary, not a judgement call",
        "basis": (
            "A feature whose page does not show its own edges overstates itself. Each item "
            "below is in the research and out of this build, and saying so here is cheaper "
            "than a reviewer discovering it."
        ),
        "value": {
            "cal_meeting_started_and_ended": (
                "not sent. The research describes them as Cal-side events with a flat "
                "payload, and this build fires the three Chili Piper subscription types."
            ),
            "recording_ready": (
                "not sent. The research names it as an automation surface, not as one of "
                "the three webhook types an admin picks when creating a subscription."
            ),
            "retry_ladder": "not built. No evidence.",
            "replay_window_enforcement": "not built. The research assigns it to the consumer.",
            "dead_letter_queue": "not built. No evidence.",
            "custom_payload_templates": (
                "not built. The research names them for the Cal UI, and a template engine "
                "would put arbitrary text into a signed body."
            ),
            "cloud_metadata_endpoint_blocking": (
                "not built. The research names it for Cal SaaS. This deployment defaults "
                "to self-hosted, where private addresses are allowed by the quoted rule."
            ),
        },
        "change_it": "Any of these needs its own ticket and its own evidence.",
    },
)


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One entry, by name, or None."""
    for entry in INFERENCES:
        if entry["id"] == entry_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole register, as one served document."""
    return {
        "count": len(INFERENCES),
        "sourced_quote": SOURCED_QUOTE,
        "inferences": [dict(entry) for entry in INFERENCES],
        "collections": dict(COLLECTIONS),
    }
