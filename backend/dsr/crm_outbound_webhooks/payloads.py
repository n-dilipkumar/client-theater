"""The versioned payload contract, and the reader for both researched body shapes.

The research says the rep chooses the body:

* "To include all properties, select **Include all [object] properties**."
* "To include only specific properties: Select **Customize request body** ... To
  customize the request body using a HubSpot property, enter the Key and select a
  property ... To add a static field, enter the Key and Value."

Those are two *shapes* on the wire as much as two settings: one nests the record's
properties under an object, the other is a flat table of key/value pairs. The
research does not publish the JSON each produces, so this reader does not guess at
a vendor's field names. It reads both shapes with one rule -

    properties = body["properties"] if that is an object, else everything in the
    body that is not a reserved control key

- and reports which shape it saw, so a reviewer can see the endpoint's setting and
the payload's actual shape side by side and tell whether the CRM-side action was
configured the way the room thinks it was.

**Everything not reserved is a property and is kept verbatim.** This product's
contract is that a field nobody coordinated with us still gets stored, and a
property nobody has heard of is exactly that. The room maps the two things it
acts on - the stage, and the external id - out of the property set, and stores the
rest untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qsl

from dsr.crm_outbound_webhooks.errors import MalformedPayload, UnsupportedPayloadVersion
from dsr.crm_outbound_webhooks.vocabulary import (
    BODY_MODES,
    CONTRACT_VERSION,
    ID_KEYS,
    RESERVED_KEYS,
)

__all__ = ["InboundPayload", "coerce_body", "read_payload"]


@dataclass(frozen=True)
class InboundPayload:
    """One request, read against the versioned contract.

    A plain value, so a test can construct one and a route can return one without
    a serializer. ``properties`` is the whole property set, unfiltered; ``stage``
    and ``external_id`` are the two things the room acts on.
    """

    version: int
    version_declared: bool
    object_declared: str | None
    external_id: str | None
    id_source: str | None
    stage: Any
    stage_source: str | None
    properties: dict[str, Any]
    control: dict[str, Any]
    delivery_id: str | None
    automation: str | None
    occurred_at: str | None
    body_mode_observed: str
    missing_keys: tuple[str, ...] = ()
    flat: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "version_declared": self.version_declared,
            "object_declared": self.object_declared,
            "external_id": self.external_id,
            "id_source": self.id_source,
            "stage": self.stage,
            "stage_source": self.stage_source,
            "delivery_id": self.delivery_id,
            "automation": self.automation,
            "occurred_at": self.occurred_at,
            "body_mode_observed": self.body_mode_observed,
            "missing_keys": list(self.missing_keys),
            "property_count": len(self.properties),
            "properties": self.properties,
            "control": self.control,
        }


def coerce_body(raw: Any) -> dict[str, Any]:
    """Turn whatever arrived into a mapping, or say why it cannot be one.

    A POST carries a JSON object. A GET carries a query string, because that is
    where a body would be - the research's "You can send both POST and GET
    requests" is honoured by reading the same contract out of both. A string that
    is not JSON is therefore read as a query string rather than refused, because
    that is what a GET body is.
    """
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise MalformedPayload(f"the request body is not valid JSON: {exc}") from exc
            if not isinstance(parsed, Mapping):
                raise MalformedPayload("the request body is JSON but not an object")
            return {str(key): value for key, value in parsed.items()}
        pairs = parse_qsl(text, keep_blank_values=True)
        # A string with no ``=`` in it is not a query string; ``parse_qsl`` keeps it
        # as a single empty-valued pair, and accepting that would make any sentence
        # a valid payload with one property called after the sentence.
        if not pairs or not any("=" in part for part in text.split("&")):
            raise MalformedPayload("the request body is neither JSON nor a query string")
        return {key: value for key, value in pairs}
    raise MalformedPayload(
        f"the request body must be an object, a query string, or JSON; got {type(raw).__name__}"
    )


def _first_present(source: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return key, source[key]
    return None, None


def read_payload(
    raw: Any,
    *,
    endpoint: Mapping[str, Any],
    version: int = CONTRACT_VERSION,
) -> InboundPayload:
    """Read one request against the endpoint's configured contract.

    ``endpoint`` supplies the object, the id property and the stage property, so
    a room whose CRM calls its fields something else names them once in the
    endpoint record and nothing here changes.
    """
    body = coerce_body(raw)
    nested = body.get("properties")
    if nested is not None and not isinstance(nested, Mapping):
        raise MalformedPayload("`properties` is present but is not an object")
    if isinstance(nested, Mapping):
        properties = {str(key): value for key, value in nested.items()}
        observed = "include_all"
        flat_source = body
    else:
        properties = {key: value for key, value in body.items() if key not in RESERVED_KEYS}
        observed = "customize"
        flat_source = body
    if observed not in BODY_MODES:  # pragma: no cover - the two above are the two
        raise MalformedPayload(f"unreachable body mode {observed!r}")

    # The version lives in the endpoint path. A body that declares one must agree,
    # because a versioned contract exists so that a mismatch is loud rather than
    # ambiguous - and a body that declares none is read as the path's version.
    declared_raw = body.get("v", body.get("version"))
    if declared_raw in (None, ""):
        declared_version, version_declared = version, False
    else:
        try:
            declared_version = int(declared_raw)
        except (TypeError, ValueError) as exc:
            raise UnsupportedPayloadVersion(
                f"the payload declares version {declared_raw!r}, which is not a number"
            ) from exc
        version_declared = True
    if declared_version != version:
        raise UnsupportedPayloadVersion(
            f"this endpoint serves payload contract v{version}; the payload declares "
            f"v{declared_version}"
        )

    object_declared = body.get("object") or body.get("objectType") or None
    if object_declared is not None:
        object_declared = str(object_declared)

    id_key = str(endpoint.get("id_key") or "id")
    stage_key = str(endpoint.get("stage_key") or "stage")
    candidate_keys: list[str] = [key for key in ID_KEYS if key != id_key]
    candidate_keys.insert(0, id_key)

    id_source, external_id = _first_present(flat_source, tuple(candidate_keys))
    if external_id is None:
        id_source, external_id = _first_present(properties, (id_key, *ID_KEYS))
    external_id = str(external_id) if external_id not in (None, "") else None

    stage_source, stage = _first_present(properties, (stage_key,))
    if stage is None:
        stage_source, stage = _first_present(flat_source, (stage_key,))

    control = {
        key: value
        for key, value in body.items()
        if key in RESERVED_KEYS and key not in ("properties",)
    }
    definition = endpoint.get("body")
    rows = definition.get("keys") if isinstance(definition, Mapping) else None
    # The body definition is a table of rows, each naming a key. What a payload is
    # checked against is the key *names*, so that is what is read out - comparing
    # against the rows themselves would report every configured key as missing.
    expected = [
        str(row.get("key")) if isinstance(row, Mapping) else str(row)
        for row in (rows or [])
        if (row.get("key") if isinstance(row, Mapping) else row)
    ]
    present = {**properties, **{k: v for k, v in body.items() if k not in RESERVED_KEYS}}
    missing = tuple(key for key in expected if key not in present and key not in body)

    delivery_id = body.get("delivery_id")
    automation = body.get("automation")
    occurred_at = body.get("occurred_at") or body.get("timestamp")

    return InboundPayload(
        version=declared_version,
        version_declared=version_declared,
        object_declared=object_declared,
        external_id=external_id,
        id_source=id_source,
        stage=stage,
        stage_source=stage_source,
        properties=properties,
        control=control,
        delivery_id=str(delivery_id) if delivery_id not in (None, "") else None,
        automation=str(automation) if automation not in (None, "") else None,
        occurred_at=str(occurred_at) if occurred_at not in (None, "") else None,
        body_mode_observed=observed,
        missing_keys=missing,
        flat={str(key): value for key, value in body.items()},
    )


def sample_body(
    endpoint: Mapping[str, Any], *, deal: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The payload the CRM's built-in **Test** control would send.

    Step 5 of the researched flow is "uses the built-in **Test** control to send a
    sample payload", and that control lives in the CRM - there is no button on the
    room's side of the wire to press. What the room *can* do, and what this
    function is for, is show the exact body its configured automation will produce,
    so a rep can compare it against the CRM's own test send. It is a preview, and
    the page says so.
    """
    properties: dict[str, Any] = dict((deal or {}).get("properties") or {})
    stage_key = str(endpoint.get("stage_key") or "stage")
    if stage_key not in properties and (deal or {}).get("stage") is not None:
        properties[stage_key] = (deal or {}).get("stage")
    if not properties:
        properties = {stage_key: "Contract Sent"}
    id_key = str(endpoint.get("id_key") or "id")
    external_id = str((deal or {}).get("external_id") or "1001")
    return {
        "v": CONTRACT_VERSION,
        "object": endpoint.get("object") or "deals",
        "delivery_id": "sample-delivery-id",
        "automation": (endpoint.get("automation") or {}).get("name") or "Deal stage changed",
        "occurred_at": (deal or {}).get("stage_at"),
        "properties": {id_key: external_id, **properties},
    }
