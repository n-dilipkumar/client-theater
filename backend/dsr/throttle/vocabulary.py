"""The words this workflow uses, published as data rather than compiled into a page.

A client renders its pickers from ``GET /vocabulary`` rather than from a list
typed into the page, so a state added here reaches every client at once. The
states are the ones the research distinguishes, and each carries the sentence that
fixes it.
"""

from __future__ import annotations

from typing import Any

from dsr.throttle import (
    backoff as backoff_module,
    classify as classify_module,
    keys as keys_module,
    policies as policies_module,
)

#: Where a batch stands. The split is the researched one: a retryable class drains
#: by itself, and anything else waits for a person.
BATCH_STATES: tuple[str, ...] = (
    "proceeding",
    "deferred",
    "retrying",
    "complete",
    "needs_action",
)

#: The states that still have work the queue can do.
LIVE_STATES: tuple[str, ...] = ("proceeding", "deferred", "retrying")

#: The collections this workflow writes. Published so the seeder, the page and a
#: reader all name the same things.
COLLECTIONS: dict[str, str] = {
    "connection": "throttle_connection",
    "bucket": "throttle_bucket",
    "batch": "throttle_batch",
    "log": "throttle_log",
    "meter": "throttle_meter",
}

#: The flow the research describes, in five steps, with the step each surface serves.
FLOW: tuple[dict[str, Any], ...] = (
    {
        "step": 1,
        "what": "the room's connector keeps a per-connection token bucket sized from the "
        "vendor's published limits",
        "surface": "POST /api/wf-046/rooms/{room_id}/batches answers proceed or defer before "
        "anything is sent",
        "served_by": "dsr.throttle.bucket",
    },
    {
        "step": 2,
        "what": "every response is inspected for the vendor's quota headers and written to the "
        "room's quota meter",
        "surface": "GET /api/wf-046/rooms/{room_id}/quota",
        "served_by": "dsr.throttle.quota",
    },
    {
        "step": 3,
        "what": "on a throttling response the connector applies exponential backoff with jitter, "
        "honours Retry-After where present, and defers the batch",
        "surface": "POST /api/wf-046/rooms/{room_id}/batches/{batch_id}/observe",
        "served_by": "dsr.throttle.classify and dsr.throttle.backoff",
    },
    {
        "step": 4,
        "what": "for HubSpot's high-volume sync locks (423) the room inserts a delay of at least "
        "2 seconds between requests",
        "surface": "the same observe route; the floor is in the signal",
        "served_by": "dsr.throttle.classify.LOCK_FLOOR_SECONDS",
    },
    {
        "step": 5,
        "what": "admin sees remaining budget and the throttle log in the connection's Quota "
        "page, and can manually pause or resume a connection",
        "surface": "GET /api/wf-046/rooms/{room_id}/throttle-log, POST "
        "/api/wf-046/connections/{connection_id}/pause and /resume",
        "served_by": "dsr.throttle.engine",
    },
)

#: The data flow, as the research writes it. Served rather than paraphrased, because
#: the order of these five hops is what a reviewer is checking.
DATA_FLOW: str = (
    "request -> connector token bucket -> vendor quota headers on every response "
    "(Sforce-Limit-Info, X-HubSpot-RateLimit-*) or a 429/423 -> backoff decision -> deferred "
    "queue -> retry with the same idempotency key (so retries never duplicate CRM rows)"
)

#: Why the room holds no vendor credential, which is what makes the quota meter
#: possible at all.
TRANSPORT_NOTE: str = (
    "Every vendor answer in this workflow is handed in by the connector. The room opens no socket "
    "and holds no credential, so a quota reading costs the vendor nothing extra to take."
)


def describe() -> dict[str, Any]:
    """Everything a client needs to render this workflow, in one object."""
    return {
        "flow": [dict(step) for step in FLOW],
        "data_flow": DATA_FLOW,
        "batch_states": [
            {
                "value": state,
                "terminal": state not in LIVE_STATES,
                "what": _state_note(state),
            }
            for state in BATCH_STATES
        ],
        "live_states": list(LIVE_STATES),
        "collections": dict(COLLECTIONS),
        "throttle_kinds": list(classify_module.THROTTLE_KINDS),
        "signals": classify_module.catalogue(),
        "statuses": {
            "throttle": classify_module.THROTTLE_STATUS,
            "request_limit": classify_module.REQUEST_LIMIT_HTTP_STATUS,
            "request_limit_code": classify_module.REQUEST_LIMIT_EXCEEDED,
            "lock": classify_module.LOCK_STATUS,
            "migration": classify_module.MIGRATION_STATUS,
            "transient": list(classify_module.TRANSIENT_STATUSES),
        },
        "backoff": backoff_module.describe(),
        "idempotency": {
            "digest_length": keys_module.DIGEST_LENGTH,
            "prefix": keys_module.PREFIX,
            "derived_from": [
                "vendor",
                "connection_id",
                "object_name",
                "room_id",
                "the row's own external id",
            ],
            "stable_across": "attempts, restarts and processes",
        },
        "bucket_reasons": ["proceed", "paused", "empty", "unknown_capacity"],
        "quota_surfaces": {
            "salesforce": ["Sforce-Limit-Info"],
            "hubspot": ["X-HubSpot-RateLimit-*"],
        },
        "policies": policies_module.all_policies(),
        "policy_fields": list(policies_module.PATCHABLE),
        "vendor_statuses": list(policies_module.VENDORS),
        "transport": TRANSPORT_NOTE,
    }


def _state_note(state: str) -> str:
    return {
        "proceeding": "the bucket allowed it and the caller is expected to send it now",
        "deferred": "a vendor refused it and the queue will send it again under the same "
        "idempotency key",
        "retrying": "a retry has been scheduled and the batch keeps the key it was first given",
        "complete": "the vendor accepted every row and the keys are spent",
        "needs_action": "the room will not retry this on its own. Either the vendor asked for a "
        "wait longer than the cap, or the attempt bound was reached, and a person decides.",
    }[state]


__all__ = [
    "BATCH_STATES",
    "LIVE_STATES",
    "COLLECTIONS",
    "FLOW",
    "DATA_FLOW",
    "TRANSPORT_NOTE",
    "describe",
]
