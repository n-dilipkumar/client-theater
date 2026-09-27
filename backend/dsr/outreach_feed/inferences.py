"""Every inference this package makes, in one inspectable place.

The research for WF-026 is specific about the wire contract and silent about
almost everything around it. It gives an endpoint, a body, an event-name grammar,
a placeholder, a resource list, a payload version, a signature header, and a
timeout. It does not give a retry policy, a digest encoding, a deep-link shape, a
matching rule, or a policy for what to do with an event that matches nothing.

A judgement call left as a comment in a function body is one nobody re-reads, and
a wrong one becomes product behaviour without anyone noticing. Collected here
instead, each inference is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-026/inferences``, so a
  reviewer or a client reads the whole list instead of inferring it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this package stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.outreach_feed.delivery import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    RETRYABLE_STATUS,
)
from dsr.outreach_feed.vocabulary import (
    EVENTS_ENDPOINT,
    EXAMPLE_EVENT_NAME,
    INTENT_RESOURCES,
    MAILING_RESOURCES,
    SENDER_TIMEOUT_SECONDS,
    SIGNATURE_HEADER,
)

#: The lines of the research that govern the package.
SOURCED_QUOTES: tuple[str, ...] = (
    "If you are aware of interesting events happening to Outreach prospects you can "
    "send custom events to indicate these changes inside prospect activity feed.",
    "Add the 'Activity feed custom events' feature to your app, then configure one or "
    "more custom events. The event name and template will be displayed in the activity "
    "feed. In the payload you can additionally send accompanying text that will also "
    "appear in the event card.",
    "The event card will also contain the template string you have configured for the "
    "event. In the template string you can use the {{prospect}} placeholder which "
    "Outreach will replace with a link to the prospect.",
    "On each qualifying DSR event, POST https://api.outreach.io/api/v2/events with an "
    "S2S token, the configured name, an externalUrl deep link back into the DSR, an "
    "optional body, and a prospect relationship.",
    "Outreach does not retry webhook deliveries upon receiving any of the Status Codes "
    "including 500 Internal Server Error and 429 Too Many Requests.",
    "The timeout while waiting for response is set to 5 seconds.",
)


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "retry-policy",
        "topic": "how a failed outbound event write is retried",
        "basis": (
            "The research gives the endpoint, the authorization header and a 5 second "
            "timeout, and says nothing about retrying. It does state that Outreach does "
            "not retry webhook deliveries, so the platform's posture is known even if "
            "this direction's ladder is not."
        ),
        "value": {
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "backoff_seconds": DEFAULT_BACKOFF,
            "exponential": True,
            "timeout_seconds": SENDER_TIMEOUT_SECONDS,
            "retryable_statuses": sorted(RETRYABLE_STATUS),
            "honours_retry_after": True,
        },
        "why": (
            "An activity feed that silently drops a buyer's 'replied' because one "
            "5xx is not shippable, and nobody downstream will resend it. The sourced "
            "no-retry statement is about the inbound direction but the reasoning "
            "carries: whatever we do not record, nobody re-sends."
        ),
        "change_it": (
            "Construct the publisher per request with max_attempts / backoff, or edit "
            "DEFAULT_MAX_ATTEMPTS and DEFAULT_BACKOFF in dsr/outreach_feed/delivery.py. "
            "No other file needs to change."
        ),
        "blast_radius": (
            "Every outbound write. The delivery row records the attempt count and the "
            "full per-attempt log either way."
        ),
    },
    {
        "id": "webhook-signature-encoding",
        "topic": "which HMAC the Outreach-Webhook-Signature header carries",
        "basis": (
            "[sourced] The header name, and the word HMAC. The digest algorithm, the "
            "encoding and any prefix are not documented in the research."
        ),
        "value": {
            "header": SIGNATURE_HEADER,
            "algorithm": "HMAC-SHA256",
            "encoding": "lowercase hex",
            "prefix": "sha256=",
            "prefix_optional_on_verify": True,
        },
        "why": (
            "SHA-256 in hex is the convention this header name carries everywhere else "
            "it appears. Verification accepts the digest with or without the prefix, so "
            "a differently-formatted signature from the same secret still passes - the "
            "one thing that must not happen is rejecting a genuine delivery."
        ),
        "change_it": (
            "compute_signature / verify in dsr/outreach_feed/webhooks.py. Nothing else "
            "reads the digest."
        ),
        "blast_radius": "Only the inbound webhook endpoint. The outbound write is unaffected.",
    },
    {
        "id": "deep-link-shape",
        "topic": "how the externalUrl deep link back into the DSR is built",
        "basis": (
            "[sourced] The flow requires 'an externalUrl deep link back into the DSR' "
            "and that 'the card links back to the DSR'. It does not say what the room's "
            "URL looks like."
        ),
        "value": {
            "shape": "{room_base_url}/{room_id}",
            "room_base_url": "a field on the app record, required for any write",
            "per_link_override": "a prospect link may carry its own external_url",
        },
        "why": (
            "The base has to come from somewhere and only the deployment knows it. "
            "Requiring it on the app record, rather than defaulting to a hostname, "
            "means a misconfigured integration produces a stated blocker rather than a "
            "feed of links nobody can open."
        ),
        "change_it": (
            "room_deep_link in dsr/outreach_feed/vocabulary.py, or set external_url on "
            "the prospect link, which wins outright."
        ),
        "blast_radius": "The externalUrl attribute on every outbound write.",
    },
    {
        "id": "event-name-charset",
        "topic": "which characters an app identifier and an event id may hold",
        "basis": (
            "[sourced] Names are '<app identifier>:<event id>' and the example is "
            f"{EXAMPLE_EVENT_NAME!r}. The research does not constrain the character set."
        ),
        "value": {
            "pattern": "[A-Za-z0-9][A-Za-z0-9_.-]*",
            "separators_allowed": 1,
            "whitespace_allowed": False,
        },
        "why": (
            "The strictly-supported reading. A name is an identifier that reaches an "
            "app's configuration, so punctuation beyond dot, dash and underscore is far "
            "more likely to be a paste accident than an intent, and the rejection "
            "message quotes the format so a correct guess is one retry away."
        ),
        "change_it": "EVENT_NAME_SHAPE in dsr/outreach_feed/vocabulary.py.",
        "blast_radius": "Creating and updating a custom event type, and the event name sent on the wire.",
    },
    {
        "id": "prospect-linkage",
        "topic": "how a DSR room is matched to an Outreach prospect",
        "basis": (
            "[sourced] The data sources are the Outreach prospect object '(and the "
            "account/opportunity behind it)' and 'the DSR event stream as the source'. "
            "No matching algorithm is described, and the related Salesloft "
            "'live website tracking parameter' endpoint - the usual identity mechanism - "
            "is gated behind a Salesloft allowlist this build cannot obtain."
        ),
        "value": {
            "resolution": "explicit link from a room to a prospect id",
            "multi_link": "a room may hold several links, one per buyer contact",
            "on_unlinked_room": "skipped with reason no_prospect, recorded not dropped",
        },
        "why": (
            "Guessing a prospect id would put one buyer's activity under another "
            "buyer's name, which is worse than sending nothing. An explicit link is also "
            "auditable: who decided this room is this prospect, and when."
        ),
        "change_it": (
            "Declare a link through POST /api/wf-026/rooms/{room_id}/prospects. The "
            "link's prospect_id, opportunity_id and account_id are ordinary JSON."
        ),
        "blast_radius": "Every outbound write. With several links, one event fans out to each of them.",
    },
    {
        "id": "event-fallthrough",
        "topic": "what happens to a DSR event that matches no configured custom event",
        "basis": (
            "[sourced] The flow says 'On each qualifying DSR event' and the evidence "
            "begins 'If you are aware of interesting events' - so the qualification is "
            "the configuration, and the research is silent on the unqualifying case."
        ),
        "value": {
            "recorded_as": "one summarised count per unmatched action name",
            "delivery_row": False,
            "reason_if_known": "no_prospect | app_not_ready | app_disabled | type_disabled",
        },
        "why": (
            "The alternative - dropping them - is the failure a build brief for this "
            "programme names explicitly: a rule that does not fall through is a bug "
            "someone hits in production. The other alternative, one delivery row per "
            "unmatched event, would bury the log under every buyer action nobody "
            "configured. So the count is surfaced per action name, and the blocked cases "
            "a person can act on get their own row with the reason attached."
        ),
        "change_it": "The ledger built by FeedPublisher.publish; no other file.",
        "blast_radius": "The publish response, the room feed view, and the blockers list.",
    },
    {
        "id": "s2s-token-storage",
        "topic": "how the S2S token is kept and shown",
        "basis": (
            "[sourced] The request carries 'Authorization: Bearer S2S_TOKEN'. The "
            "research says nothing about storage or display."
        ),
        "value": {
            "stored": "verbatim in the app record's data, as every field here is",
            "returned_by_reads": False,
            "returned_as": "has_token plus a masked hint of the last four characters",
            "stored_in_delivery_rows": False,
        },
        "why": (
            "There is no envelope field to put a secret in and this package may not add "
            "a column, so the token is ordinary JSON. What is controllable is not "
            "leaking it: a read never returns it, and the delivery log's recorded "
            "headers have the Authorization value redacted, because that header is the "
            "token."
        ),
        "change_it": (
            "The redaction is redact_headers in dsr/outreach_feed/delivery.py; the "
            "summary is app_summary in dsr/outreach_feed/engine.py."
        ),
        "blast_radius": "Every app read and every delivery row.",
    },
    {
        "id": "before-update-block",
        "topic": "what the research's beforeUpdate block is read as",
        "basis": (
            "[sourced] 'POST https://api.outreach.io/api/v2/webhooks with "
            "payloadVersion: 2 returns a beforeUpdate block.' The sentence does not say "
            "whether the block is on the subscription or on each delivery."
        ),
        "value": {
            "reading": "each payload carries a beforeUpdate block holding the prior state",
            "stored": "verbatim, whatever shape it has",
            "also_served": "describe_subscription() tells a team what to create",
        },
        "why": (
            "Storing the block wherever it appears - on the payload, on data, or on the "
            "attributes - costs nothing and loses nothing if the reading is wrong; "
            "ignoring it would lose it if the reading is right. Reading it as prior "
            "state is the reading that makes it useful: a signal whose beforeUpdate says "
            "the mailing was still in the queue explains why it never opened."
        ),
        "change_it": "before_update_of in dsr/outreach_feed/webhooks.py.",
        "blast_radius": "The signal row's before_update field, and nothing else.",
    },
    {
        "id": "inbound-idempotency",
        "topic": "what to do if the same webhook delivery arrives twice",
        "basis": (
            "[sourced] 'Outreach does not retry webhook deliveries upon receiving any of "
            "the Status Codes' - so a repeat is not a redelivery, and no reliability "
            "guarantee is being leaned on here."
        ),
        "value": {
            "key": "resource|type|mailing id|sequence|prospect id",
            "on_repeat": "recorded as duplicate, answered 200, no second signal row",
        },
        "why": (
            "A double-counted 'replied' in a list a rep reads is the kind of thing that "
            "makes a feed get switched off. The key is only as strong as the fields the "
            "delivery carries, so a delivery with no mailing id and no sequence still "
            "produces a key - and if that makes two genuinely different deliveries "
            "collide, the second is marked duplicate rather than counted twice."
        ),
        "change_it": "signal_key in dsr/outreach_feed/webhooks.py.",
        "blast_radius": "The signal log. Nothing outbound.",
    },
    {
        "id": "poll-not-push",
        "topic": "when the outbound write happens",
        "basis": (
            "[sourced] 'No user action on the Outreach side once configured - the feed "
            "entry appears as events arrive.' The research does not say what makes an "
            "event 'arrive' on this side."
        ),
        "value": {
            "trigger": "an explicit publish run over a room's event stream",
            "replay_protection": "one delivery key per (room, DSR event, event name)",
            "delivered_is_terminal": True,
            "skipped_is_retryable": True,
        },
        "why": (
            "The workflow is a push into someone else's feed, and 'the feed entry "
            "appears as events arrive' describes the result, not a scheduler. So the "
            "engine exposes the sweep and refuses to duplicate what it already "
            "delivered, which makes running it repeatedly - from a hook, a cron, or a "
            "human - safe. A skipped row is re-evaluated on the next run, so linking the "
            "prospect and running again sends the events that were blocked."
        ),
        "change_it": (
            "FeedPublisher.publish; the delivery key is the record's event_key field, "
            "queryable with ?where=event_key=…"
        ),
        "blast_radius": "Every outbound write. No inbound behaviour depends on it.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, plus the sourced facts it is measured against."""
    return {
        "sourced_quotes": list(SOURCED_QUOTES),
        "sourced": {
            "write_endpoint": EVENTS_ENDPOINT,
            "example_event_name": EXAMPLE_EVENT_NAME,
            "mailing_resources": list(MAILING_RESOURCES),
            "intent_resources": list(INTENT_RESOURCES),
            "signature_header": SIGNATURE_HEADER,
            "timeout_seconds": SENDER_TIMEOUT_SECONDS,
        },
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)
