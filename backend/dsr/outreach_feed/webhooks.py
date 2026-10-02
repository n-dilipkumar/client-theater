"""The inbound half: reading an Outreach webhook delivery.

The research's ``automations`` field is explicit that intent also travels back
this way, and it is unusually specific about the parts that matter:

* [sourced] the resources are the ``mailing*`` family - "created updated destroyed
  bounced delivered opened replied";
* [sourced] subscriptions are created with ``payloadVersion: 2``;
* [sourced] "deliveries carry an ``Outreach-Webhook-Signature`` HMAC header";
* [sourced] "The timeout while waiting for response is set to 5 seconds";
* [sourced] and, the one that shapes this whole module, **"Outreach does not retry
  webhook deliveries upon receiving any of the Status Codes including 500 Internal
  Server Error and 429 Too Many Requests."**

No retries changes what a receiver is allowed to do
----------------------------------------------------
With no retry, a non-2xx is not "the sender will try again later" - it is
"this delivery is gone". So:

* a payload we can read is **stored before it is acknowledged**, and always
  acknowledged 2xx. Nothing is queued for a retry that will never come;
* a payload carrying a resource outside the documented family is **stored as
  ``ignored``** and still acknowledged 2xx, rather than refused. Refusing an
  undocumented resource would throw away data that a subscription legitimately
  carries, permanently, in exchange for a tidier log;
* the two refusals that *are* real are the ones no amount of persistence fixes -
  a signature that does not verify, and a ``payloadVersion`` this build does not
  speak. Those answer 4xx and write nothing.

The signature
-------------
The research names the header and says it is an HMAC. It does not say the digest
or the encoding, so :func:`compute_signature` picks HMAC-SHA256 in hex with the
common ``sha256=`` prefix, and :func:`verify` accepts the digest with or without
that prefix so a differently-formatted signature from the same secret still
verifies. That choice is recorded as an inference, not as a finding.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

from dsr.outreach_feed.errors import SignatureError, WebhookError
from dsr.outreach_feed.vocabulary import (
    INTENT_RESOURCES,
    MAILING_FAMILY,
    MAILING_RESOURCES,
    PAYLOAD_VERSION,
    SIGNATURE_HEADER,
)

SIGNATURE_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 over the exact bytes received, ``sha256=<hex>``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify(secret: str, body: bytes, presented: str | None) -> bool:
    """Constant-time check of a presented signature against a shared secret."""
    if not secret or not presented:
        return False
    expected = compute_signature(secret, body)
    candidates = {expected, expected[len(SIGNATURE_PREFIX) :]}
    return any(hmac.compare_digest(str(presented).strip(), candidate) for candidate in candidates)


def header_value(headers: Any, name: str) -> str | None:
    """Case-insensitive header lookup that accepts a plain dict.

    Starlette's ``Headers`` is already case-insensitive, but the engine is
    callable directly and a test passes a dict; doing the lookup here means both
    paths behave the same instead of one of them silently matching nothing.
    """
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:  # pragma: no cover - an exotic header container
        return None
    if value:
        return value
    wanted = name.lower()
    try:
        items = list(headers.items())
    except AttributeError:  # pragma: no cover - an exotic header container
        return None
    for key, candidate in items:
        if str(key).lower() == wanted and candidate:
            return candidate
    return None


def require_signature(headers: Any, secrets: list[str], body: bytes) -> str:
    """Prove the delivery came from Outreach, or raise :class:`SignatureError`.

    Every configured app's secret is tried. An installation can register more than
    one Outreach app, and the research does not say which app the webhook
    subscription belongs to, so the honest answer is "any app we know the secret
    for" rather than guessing one.
    """
    presented = header_value(headers, SIGNATURE_HEADER)
    if not presented:
        raise SignatureError(f"{SIGNATURE_HEADER} header is missing")
    for secret in secrets:
        if secret and verify(secret, body, presented):
            return secret
    raise SignatureError(f"{SIGNATURE_HEADER} does not verify against any configured secret")


def parse_body(raw: bytes | str) -> dict[str, Any]:
    """Decode a delivery body as a JSON object, or explain why it is not one."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WebhookError(f"delivery body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise WebhookError(f"delivery body must be a JSON object, got {type(parsed).__name__}")
    return parsed


def require_payload_version(payload: Mapping[str, Any]) -> int:
    """[sourced] The subscription is created with ``payloadVersion: 2``.

    A different version is a real refusal rather than a shrug: a v1 payload has a
    different shape, and storing it as if it were v2 would put wrong data in the
    signal log. Writing nothing is the honest outcome.
    """
    raw = payload.get("payloadVersion", payload.get("payload_version"))
    if raw is None:
        raise WebhookError(
            f"payloadVersion is missing; this integration is configured for "
            f"payloadVersion {PAYLOAD_VERSION}"
        )
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebhookError(f"payloadVersion {raw!r} is not a number") from exc
    if version != PAYLOAD_VERSION:
        raise WebhookError(
            f"payloadVersion {version} is not supported; this integration reads "
            f"payloadVersion {PAYLOAD_VERSION}"
        )
    return version


def _relationship_id(payload: Mapping[str, Any], relationship: str) -> str:
    """A JSON:API relationship id, with a flat attribute as a fallback.

    The outbound body in the research nests the prospect under
    ``data.relationships.prospect.data.id``, so that is tried first; some
    deliveries also carry the id as a plain attribute, and refusing those would
    lose a real signal over a spelling.
    """
    data = payload.get("data")
    if isinstance(data, Mapping):
        relationships = data.get("relationships")
        if isinstance(relationships, Mapping):
            node = relationships.get(relationship)
            if isinstance(node, Mapping):
                inner = node.get("data")
                if isinstance(inner, Mapping) and inner.get("id") not in (None, ""):
                    return str(inner["id"]).strip()
        attributes = data.get("attributes")
        if isinstance(attributes, Mapping):
            for key in (f"{relationship}_id", f"{relationship}Id", relationship):
                if attributes.get(key) not in (None, ""):
                    return str(attributes[key]).strip()
    for key in (f"{relationship}_id", f"{relationship}Id"):
        if payload.get(key) not in (None, ""):
            return str(payload[key]).strip()
    return ""


def before_update_of(payload: Mapping[str, Any]) -> Any:
    """[sourced] The ``beforeUpdate`` block, verbatim.

    The research records that a ``payloadVersion: 2`` subscription "returns a
    ``beforeUpdate`` block". It is stored as it arrives, whatever shape it has, so
    a deployment whose payloads carry more than we anticipated loses nothing.
    """
    data = payload.get("data")
    if isinstance(data, Mapping) and "beforeUpdate" in data:
        return data.get("beforeUpdate")
    if "beforeUpdate" in payload:
        return payload.get("beforeUpdate")
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    if isinstance(attributes, Mapping) and "beforeUpdate" in attributes:
        return attributes.get("beforeUpdate")
    return None


#: Keys a delivery might carry the resource and its type in, most specific first.
#: Outreach names the resource ``mailing.opened``; other spellings split the same
#: two halves across ``resource`` and ``action``, or put the type bare in ``type``.
_RESOURCE_KEYS = ("type", "resource", "action", "event")


def split_resource(payload: Mapping[str, Any]) -> tuple[str, str]:
    """``(family, kind)`` from whatever the delivery happens to call itself.

    Accepts ``{"type": "mailing.opened"}``, ``{"resource": "mailing", "action":
    "opened"}`` and ``{"type": "mailing", "action": "opened"}`` alike, because the
    research names the resources once and does not say which spelling a delivery
    uses. An empty half stays empty rather than being guessed.
    """
    family = ""
    kind = ""
    for key in _RESOURCE_KEYS:
        candidate = str(payload.get(key) or "").strip().lower()
        if not candidate:
            continue
        head, dot, tail = candidate.partition(".")
        if dot and head and tail:
            family = family or head
            kind = kind or tail
        elif candidate in MAILING_RESOURCES:
            kind = kind or candidate
        elif candidate:
            family = family or candidate
    return family, kind


def normalise(payload: Mapping[str, Any]) -> dict[str, Any]:
    """One signal out of a delivery, whatever the resource.

    Every field is read defensively. A delivery is a foreign payload this build
    did not write, and the one sourced guarantee about it is that it will not be
    sent again if we mishandle it.
    """
    family, kind = split_resource(payload)
    resource = family
    event_type = kind

    data = payload.get("data")
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    attributes = attributes if isinstance(attributes, Mapping) else {}

    occurred_at = str(
        payload.get("createdAt") or payload.get("created_at") or attributes.get("createdAt") or ""
    ).strip()

    mailing_id = str(
        data.get("id") if isinstance(data, Mapping) and data.get("id") not in (None, "") else ""
    ).strip() or _relationship_id(payload, "mailing")

    sequence = payload.get("sequence")
    if sequence is None:
        sequence = attributes.get("sequence")

    return {
        "resource": resource,
        "type": event_type,
        "prospect_id": _relationship_id(payload, "prospect"),
        "mailing_id": mailing_id,
        "sequence": sequence,
        "occurred_at": occurred_at,
        "payload_version": payload.get("payloadVersion", payload.get("payload_version")),
        "before_update": before_update_of(payload),
        "attributes": dict(attributes),
        "supported": resource == MAILING_FAMILY and event_type in MAILING_RESOURCES,
        "intent": resource == MAILING_FAMILY and event_type in INTENT_RESOURCES,
    }


def signal_key(
    resource: str, event_type: str, mailing_id: str, sequence: Any, prospect_id: str
) -> str:
    """A stable identity for one delivery, so a repeat is visibly a repeat.

    Not strictly necessary - [sourced] Outreach does not retry, so the same event
    arriving twice is a sender-side duplicate rather than a redelivery. It is
    cheap, and a double-counted "replied" in a rep's signal list is the kind of
    thing nobody trusts twice.
    """
    parts = [str(part) for part in (resource, event_type, mailing_id, sequence, prospect_id)]
    return "|".join(parts)


def describe_subscription() -> dict[str, Any]:
    """What a team needs in order to create the subscription in the portal."""
    return {
        "create": {
            "method": "POST",
            "endpoint": "https://api.outreach.io/api/v2/webhooks",
            "body": {
                "url": "https://your-host.example/api/wf-026/webhooks/outreach",
                "payloadVersion": PAYLOAD_VERSION,
                "signingSecret": "<the secret you put on the app record>",
                "resources": [f"{MAILING_FAMILY}.{resource}" for resource in MAILING_RESOURCES],
            },
        },
        "receive": {
            "path": "/api/wf-026/webhooks/outreach",
            "signature_header": SIGNATURE_HEADER,
            "signature_algorithm": "HMAC-SHA256 over the exact request body",
            "payload_version": PAYLOAD_VERSION,
            "timeout_seconds": 5.0,
            "notes": (
                "The sender waits 5 seconds and then gives up without retrying, so the "
                "handler stores the signal before it answers and never answers "
                "anything but 2xx for a payload it could read."
            ),
        },
    }
