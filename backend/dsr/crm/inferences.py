"""Every inference in this package, in one inspectable place.

The research for WF-016 is explicit about its own limits, and the port brief
asked for that distinction to be kept rather than blurred:

* ``docs/research/digital-sales-room-workflows/wf/WF-016.md`` states that the
  research "makes **no claims** about specific Salesforce endpoint/method pairs",
  so there is no sourced way to *execute* a CRM write.
* It documents 429 rate limiting on the webhook seam, and says nothing about a
  retry policy, a signature scheme, or the shape of a delivery envelope.

So parts of this package are judgement calls. A judgement call left as a comment
in a function body is one nobody re-reads, and a wrong one becomes product
behaviour without anyone noticing. Collected here instead, each inference is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-016/inferences``, so a
  reviewer or a client can read the whole list instead of inferring it from
  diffs.

Nothing here is a migration, a typed column, or a new required field. It is a
list of ordinary JSON, exactly like everything else this package stores, and it
is a *record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.crm.vocabulary import EVENTS, PAGE_STATUSES, PRESETS

#: The one line of the research that governs everything in this module.
SOURCED_QUOTE = (
    "Public Salesforce REST surface is not documented on the Qwilr pages I read, so I "
    "make no claims about specific Salesforce endpoint/method pairs. The Qwilr->Salesforce "
    "write is a product feature; the buyer-visible integration is described only in prose."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "retry-policy",
        "topic": "how a failed webhook delivery is retried",
        "basis": (
            "The research documents that the webhook seam rate-limits with 429 and says "
            "nothing about retrying. There is no sourced attempt count, backoff, or "
            "timeout."
        ),
        "value": {
            "max_attempts": 3,
            "backoff_seconds": 0.5,
            "exponential": True,
            "timeout_seconds": 5.0,
            "retryable_statuses": sorted({408, 425, 429, 500, 502, 503, 504}),
            "honours_retry_after": True,
        },
        "why": (
            "A webhook that ships customer page metadata to an arbitrary URL with no "
            "retry is not shippable. 429 is sourced; the 5xx family and 408/425 are the "
            "ordinary 'try again' set."
        ),
        "change_it": (
            "Construct CRMSync(max_attempts=..., backoff=..., timeout=...) per request, or "
            "edit DEFAULT_MAX_ATTEMPTS / DEFAULT_BACKOFF / DEFAULT_TIMEOUT / "
            "RETRYABLE_STATUS in dsr/crm/delivery.py. No other file needs to change."
        ),
        "blast_radius": "Every webhook delivery. Counters and Activity Log rows record the attempt count.",
    },
    {
        "id": "hmac-signature",
        "topic": "how a subscriber proves a payload came from us",
        "basis": (
            "The research documents no signature scheme, no header names, and no secret "
            "handling for webhook subscriptions."
        ),
        "value": {
            "algorithm": "HMAC-SHA256",
            "header": "X-DSR-Signature",
            "format": "sha256=<hex digest over the exact bytes sent>",
            "optional": True,
        },
        "why": (
            "A webhook carrying customer page metadata to an arbitrary URL with no "
            "authentication is not shippable. Optional, so an integration that does not "
            "need it pays nothing."
        ),
        "change_it": "sign() in dsr/crm/delivery.py, and the two header names beside it.",
        "blast_radius": "Only subscriptions created with a secret. Unsigned subscriptions are unaffected.",
    },
    {
        "id": "delivery-headers",
        "topic": "the headers a delivery carries",
        "basis": "No sourced header contract exists.",
        "value": {
            "X-DSR-Event": "the published event name",
            "X-DSR-Delivery": "<event record id>:<subscription id>",
        },
        "why": (
            "A subscriber needs to know which event arrived and needs a stable id to "
            "deduplicate on. The composite id is stable because both halves are record "
            "ids in the audited store."
        ),
        "change_it": "The headers dict in deliver() in dsr/crm/delivery.py.",
        "blast_radius": "Subscribers that read these headers. They are additive.",
    },
    {
        "id": "delivery-envelope",
        "topic": "the JSON body shape sent to a subscriber",
        "basis": "No sourced body shape exists; the research only says the page's metadata rides along.",
        "value": {
            "id": "the stored crm_event record id",
            "event": "one of the seven published events",
            "status": "one of the five published page statuses",
            "occurred_at": "ISO 8601",
            "room_id": "the room's record id, or null",
            "room": "the projected room facts, including the room's metadata verbatim",
            "metadata": "the room's metadata, merged with any per-event override",
        },
        "why": (
            "The room's metadata round-tripping untouched is sourced. The envelope around "
            "it is not, so it is modelled on the store's own record envelope to be "
            "predictable rather than novel."
        ),
        "change_it": "The envelope dict in CRMSync.record_event in dsr/crm/sync.py.",
        "blast_radius": "Every subscriber. A change here is a breaking change for integrations.",
    },
    {
        "id": "automation-run-is-resolve-only",
        "topic": "what an automation run actually does",
        "topic_note": "the largest inference in the package",
        "basis": SOURCED_QUOTE,
        "value": {
            "action": "resolve the field map against the room's facts and record the result",
            "does_not": "call any CRM API",
            "stored_in": "the Activity Log, in the row's resolved field",
        },
        "why": (
            "The research names the actions in prose ('update opportunity amount', 'sync "
            "page URLs') and explicitly declines to claim a Salesforce endpoint, so there "
            "is no sourced way to execute a write. Resolving and logging is honest about "
            "that; faking a write would not be."
        ),
        "change_it": (
            "Add a handler for an action kind in RESOLVABLE_ACTIONS in "
            "dsr/crm/automations.py. The mapping is arbitrary JSON, so a team that does "
            "have a CRM client can add one without a migration."
        ),
        "blast_radius": (
            "Everything an automation claims to do. A rep reading the Activity Log sees a "
            "payload, not a confirmation that a CRM record changed."
        ),
    },
    {
        "id": "unresolved-fact-is-an-error",
        "topic": "what a run that resolved nothing is recorded as",
        "basis": (
            "The research says the Activity Log tracks whether an automation 'has "
            "errored' and 'will need manual updating', but does not say which run states "
            "map onto which of those two."
        ),
        "value": {
            "rule": "a run with any unresolved_fact warning is status=error, needs_manual_update=true",
            "otherwise": "status=success, even if some fields resolved to null",
        },
        "why": (
            "A run that wrote nothing it was asked to write is not a success with empty "
            "fields; it is the case the log has to send a rep back to the room to fix."
        ),
        "change_it": "The errors list in CRMSync._fan_out_automations in dsr/crm/sync.py.",
        "blast_radius": (
            "The error counters on every automation, and the 'needs manual update' total "
            "on the page."
        ),
    },
    {
        "id": "page-preview-accepted-status",
        "topic": "which page status a preview sign-off implies",
        "basis": (
            "The five statuses are sourced verbatim as a fixed set. Which of them an "
            "event implies is not, and this entry is the debatable one."
        ),
        "value": {
            "pagePreviewAccepted": "partially accepted",
            "rationale": "a preview sign-off is not full acceptance",
        },
        "why": (
            "Every other entry in EVENT_STATUS follows from the event's own name. This one "
            "does not, and is a judgement call kept visible so it can be disagreed with."
        ),
        "change_it": (
            "EVENT_STATUS in dsr/crm/vocabulary.py, or pass status= per event, which "
            "already overrides the default for any of the five published values."
        ),
        "blast_radius": "The status a subscriber receives for preview-accepted events.",
    },
    {
        "id": "room-scope-is-a-filter-not-an-only",
        "topic": "what a room-scoped subscription receives",
        "basis": (
            "The research documents the event, the target URL and the subscription id. It "
            "does not document scoping at all; this product adds it."
        ),
        "value": {
            "room_id_null": "receives every room's events",
            "room_id_set": "receives only that room's events",
        },
        "why": (
            "A subscription with no room is the general-purpose seam the research "
            "describes, so it has to receive everything or the documented behaviour does "
            "not exist. Scoping is an addition, not a replacement."
        ),
        "change_it": "SubscriptionBook.matching in dsr/crm/subscriptions.py.",
        "blast_radius": "Which subscribers a room event reaches.",
    },
    {
        "id": "activity-summary-is-scoped",
        "topic": "what the Activity Log's summary counts",
        "basis": "No sourced summary shape exists; the research only describes the log's rows.",
        "value": {
            "counts": "success, error, needs_manual_update",
            "over": "exactly the rows the request's filters returned",
        },
        "why": (
            "A summary of the whole log sitting above a filtered list invites reading "
            "'3 delivered' as a total when the list shows one. Counting what was returned "
            "keeps the two consistent."
        ),
        "change_it": "The summary loop in the activity route in backend/dsr/features/wf016_crm_sync.py.",
        "blast_radius": "The four stat tiles at the top of the page.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and that means showing the sourced
    vocabulary next to the inferred behaviour rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quote": SOURCED_QUOTE,
        "sourced": {
            "events": list(EVENTS),
            "page_statuses": list(PAGE_STATUSES),
            "presets": [preset["id"] for preset in PRESETS],
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
