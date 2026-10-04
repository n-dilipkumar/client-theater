"""The researched vocabulary, served as data.

Everything here is quoted from or directly derived from
``docs/research/digital-sales-room-workflows/wf/WF-066.md``. A client renders its
subscriber editor and its event log from what :func:`describe` returns rather
than from a list compiled into the page, so a type added server-side reaches
every client at once, and the page can never disagree with the validator about
what is legal.

The one set the research enumerates twice
------------------------------------------
The three subscription types and the three payload ``type`` values are given
separately in the data flow, and they are not spelled the same way. *For New
Meeting* fires ``type: Created``, *For Meeting Update* fires ``Updated``, and
*For Canceled Meeting* fires ``Deleted``. The second mapping is the one that
looks wrong and is not: the research lists the three payload values as
``Created|Updated|Deleted`` against the three subscription names, and
``Deleted`` is the only value left for a cancellation. :data:`EVENT_TYPES` holds
the mapping, and both spellings are served so a reader can see the join rather
than trust it.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The three researched subscription types and the payload value each fires
# --------------------------------------------------------------------------- #

#: Subscription type to payload ``type``. Ordered as the research orders them.
EVENT_TYPES: tuple[dict[str, str], ...] = (
    {
        "id": "new_meeting",
        "label": "For New Meeting",
        "payload_type": "Created",
        "quote": "**For New Meeting**",
    },
    {
        "id": "meeting_update",
        "label": "For Meeting Update",
        "payload_type": "Updated",
        "quote": "**For Meeting Update**",
    },
    {
        "id": "canceled_meeting",
        "label": "For Canceled Meeting",
        "payload_type": "Deleted",
        "quote": "**For Canceled Meeting**",
    },
)

#: The payload values the research lists: ``type: Created|Updated|Deleted``.
PAYLOAD_TYPES: tuple[str, ...] = ("Created", "Updated", "Deleted")

#: The two researched row states. Quoted: *"clicks Create, then sets the row's
#: status to **Enabled**"*. A row exists before it is enabled, so ``disabled`` is
#: the state a new row lands in rather than a third value this build invented.
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUSES: tuple[str, ...] = (STATUS_ENABLED, STATUS_DISABLED)

# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #

#: Quoted: "**X-Chili-Signature** | HMAC-SHA256 signature of the payload
#: (hex-encoded)".
SIGNATURE_HEADER = "X-Chili-Signature"

#: Quoted: "**X-Chili-Timestamp** | Unix timestamp (seconds) when the request was
#: signed".
TIMESTAMP_HEADER = "X-Chili-Timestamp"

# --------------------------------------------------------------------------- #
# The payload fields the research enumerates
# --------------------------------------------------------------------------- #

#: Every documented field on the Chili Piper meeting payload. Served so a reader
#: comparing an incoming body against the contract does not have to find the
#: research document. A field this build does not populate is still listed: the
#: contract is the research's, not this build's.
PAYLOAD_FIELDS: tuple[str, ...] = (
    "type",
    "meetingIdChili",
    "title",
    "description",
    "location",
    "start",
    "end",
    "primaryGuestTimeZone",
    "primaryGuestName",
    "primaryGuestEmail",
    "primaryGuestIdChili",
    "primaryGuestDataFields",
    "hostIdChili",
    "hostName",
    "assigneeIdChili",
    "assigneeName",
    "bookerIdChili",
    "bookerName",
    "additionalGuests",
    "workspaceId",
    "workspaceName",
    "productFeatureType",
    "productFeatureName",
    "productFeatureId",
    "distributionName",
    "distributionId",
    "meetingTypeName",
    "meetingTypeId",
)

#: Quoted: "``productFeatureType`` values ``ConciergeRouter, HandoffRouter,
#: RoundRobinSchedulingLink, ChatPlaybook, DistroRouter, OwnershipSchedulingLink``".
#: A closed list, and one this product already has packages for
#: (``dsr.concierge_router``, ``dsr.round_robin``), so the vocabulary names them
#: rather than the validator inventing new spellings.
PRODUCT_FEATURE_TYPES: tuple[str, ...] = (
    "ConciergeRouter",
    "HandoffRouter",
    "RoundRobinSchedulingLink",
    "ChatPlaybook",
    "DistroRouter",
    "OwnershipSchedulingLink",
)

# --------------------------------------------------------------------------- #
# Subscriber URL policy, per deployment
# --------------------------------------------------------------------------- #

#: Quoted: "**Cal.com SaaS**: Only HTTPS URLs are accepted. HTTP,
#: private/internal IP addresses (e.g., ``10.x.x.x``, ``192.168.x.x``,
#: ``127.0.0.1``), and ``localhost`` are blocked. **Self-hosted**: Both HTTP and
#: HTTPS URLs are accepted, and private IP addresses are allowed for internal
#: webhooks."
DEPLOYMENT_MODES: tuple[dict[str, Any], ...] = (
    {
        "id": "self_hosted",
        "label": "Self-hosted",
        "schemes": ["http", "https"],
        "private_addresses": "allowed",
        "default": True,
        "quote": (
            "Self-hosted: Both HTTP and HTTPS URLs are accepted, and private IP "
            "addresses are allowed for internal webhooks."
        ),
    },
    {
        "id": "saas",
        "label": "Cal.com SaaS",
        "schemes": ["https"],
        "private_addresses": "refused",
        "default": False,
        "quote": (
            "Cal.com SaaS: Only HTTPS URLs are accepted. HTTP, private/internal IP "
            "addresses (e.g., 10.x.x.x, 192.168.x.x, 127.0.0.1), and localhost are blocked."
        ),
    },
)

DEFAULT_DEPLOYMENT_MODE = "self_hosted"

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The four collections this workflow writes. Plain JSON in ``records.data``:
#: no migration and no typed column, so a team adding a field needs nobody's
#: coordination.
COLLECTIONS: dict[str, str] = {
    "subscription": "meeting_webhook_subscription",
    "event": "meeting_webhook_event",
    "delivery": "meeting_webhook_delivery",
    "key": "meeting_webhook_key",
}

# --------------------------------------------------------------------------- #
# Delivery outcomes
# --------------------------------------------------------------------------- #

OUTCOME_DELIVERED = "delivered"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"
OUTCOMES: tuple[str, ...] = (OUTCOME_DELIVERED, OUTCOME_FAILED, OUTCOME_SKIPPED)

#: Why a delivery was skipped rather than attempted. A disabled row and a room
#: with no subscribers are different facts, and a reader looking at an event that
#: reached nobody needs to tell them apart.
SKIP_DISABLED = "subscription_disabled"
SKIP_NO_SUBSCRIBERS = "no_enabled_subscriptions"
SKIP_REASONS: tuple[str, ...] = (SKIP_DISABLED, SKIP_NO_SUBSCRIBERS)


def payload_type_for_event_type(event_type: str) -> str:
    """The payload ``type`` value one subscription type fires."""
    for row in EVENT_TYPES:
        if row["id"] == event_type:
            return str(row["payload_type"])
    raise KeyError(event_type)


def event_type_for_payload(payload_type: str) -> str:
    """The subscription type one payload ``type`` value belongs to."""
    for row in EVENT_TYPES:
        if row["payload_type"] == payload_type:
            return str(row["id"])
    raise KeyError(payload_type)


def is_known_event_type(event_type: str) -> bool:
    """Whether this build serves the subscription type."""
    return any(row["id"] == event_type for row in EVENT_TYPES)


def is_known_status(status: str) -> bool:
    return status in STATUSES


def is_known_deployment_mode(mode: str) -> bool:
    return any(row["id"] == mode for row in DEPLOYMENT_MODES)


def describe() -> dict[str, Any]:
    """Everything above, as one served document.

    The page renders its event-type picker, its status control, its header
    reference and its collection names from this, so a value added server-side
    reaches every client at once and the page cannot disagree with the validator.
    """
    return {
        "event_types": [dict(row) for row in EVENT_TYPES],
        "payload_types": list(PAYLOAD_TYPES),
        "statuses": list(STATUSES),
        "headers": {
            "signature": SIGNATURE_HEADER,
            "timestamp": TIMESTAMP_HEADER,
            "signature_encoding": "hex",
            "timestamp_unit": "unix seconds",
        },
        "signing_rule": "HMAC-SHA256(secret, '{timestamp}.{raw_body}'), hex encoded",
        "payload_fields": list(PAYLOAD_FIELDS),
        "product_feature_types": list(PRODUCT_FEATURE_TYPES),
        "deployment_modes": [dict(row) for row in DEPLOYMENT_MODES],
        "default_deployment_mode": DEFAULT_DEPLOYMENT_MODE,
        "collections": dict(COLLECTIONS),
        "outcomes": list(OUTCOMES),
        "skip_reasons": list(SKIP_REASONS),
        "replay_window_seconds": 300,
        "replay_window_owner": "the consumer",
        "fan_out": "unbounded",
    }
