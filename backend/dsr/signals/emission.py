"""Emitting a live signal, and resolving who receives it.

The research describes the emission once, field by field: "On each qualifying DSR
interaction, emit a live signal: ``POST https://api.salesloft.com/v2/signals.json``
with ``type``, ``data``, ``indicators[]``, ``urgency`` (high/medium/low),
``occurred_at``, ``idempotency_key`` (UUID4), ``attribution`` object, and
``broadcast_notification``." And it states the one behaviour that has real
consequences: "If we receive two signals with the same ``idempotency_key`` one of
them will be dropped. The first one wins."

Three rules in this module are worth stating before the code, because each is a
decision rather than a transcription.

*Validation is strict on this route.* "Signals must follow the structure defined
on the Signal Registration" is treated as a hard rule: the ``type`` must be
registered on the integration, ``data`` must satisfy ``data_shape``, every
indicator must be declared, its metadata must satisfy that indicator's
``metadata_shape``, and its own evidence must satisfy the bound its key states. A
specific claim whose evidence contradicts it is a sentence a seller would read and
believe, so it is refused here. The *classifier* route in
:mod:`dsr.signals.engine` is where a non-qualifying interaction belongs, and it
reports the decision instead of raising.

*The duplicate is not an error.* "The first one wins" describes a dropped signal,
not a failed request, so a repeated ``idempotency_key`` answers with the signal
that was kept, marked ``outcome: "dropped"``, and increments that signal's
``duplicate_attempts``. The count is the visible half of the rule: a sender that
retries should be able to see that its retry landed and was ignored, and the
increment is an audited write naming the route that made it.

*Who receives it is derived, and the derivation is reported.* "Salesloft will use
the attribution value to derive the appropriate Salesloft user to receive the
signal." Which of the five values wins when several are present is not stated in
this workflow, so :data:`~dsr.signals.vocabulary.ATTRIBUTION_PRECEDENCE` decides
and the result names the value that decided it. :func:`resolve_receiver` never
guesses a seller: it reports the attribution chain it walked and, when it cannot
name one, says so.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.signals import indicators, schema
from dsr.signals.errors import (
    EmissionError,
    IndicatorNotQualified,
    UndeclaredIndicator,
)
from dsr.signals.registration import require_uuid4
from dsr.signals.vocabulary import (
    ATTRIBUTION_OBJECT,
    ATTRIBUTION_PRECEDENCE,
    ATTRIBUTION_TYPES,
    DEFAULT_URGENCY,
    require_attribution_type,
    require_urgency,
)

#: The fields a signal carries, under both spellings.
#:
#: A separate table from the registration's because the two are different field
#: lists: the research enumerates the registration's fields and the emission's
#: fields separately, and folding them into one table would let a signal carry
#: ``data_shape``, which is a registration's declaration and not a signal's
#: payload.
EMISSION_ALIASES: dict[str, str] = {
    "type": "type",
    "signal_type": "type",
    "data": "data",
    "indicators": "indicators",
    "urgency": "urgency",
    "occurred_at": "occurred_at",
    "occurredAt": "occurred_at",
    "idempotency_key": "idempotency_key",
    "idempotencyKey": "idempotency_key",
    "attribution": "attribution",
    "broadcast_notification": "broadcast_notification",
    "broadcastNotification": "broadcast_notification",
    "locale": "locale",
    "description_locale": "locale",
    # Accepted on a signal to disambiguate two integrations that registered the
    # same type, which the research's "per integration" rule makes legal. The
    # stored value still comes from the registration, so a signal cannot claim to
    # belong to an integration its registration does not name.
    "integration_id": "integration_id",
    "integrationId": "integration_id",
    # Recognised by the alias table so the interaction route can fold a camelCase
    # request, but *refused* by normalise_emission. An interaction carries
    # observations and lets the engine derive the indicator from them; the strict
    # emit route takes the indicator directly and has no field for the raw
    # measurement, because a caller naming both is asserting a match this build
    # should be making.
    "observations": "observations",
    "metadata": "metadata",
}

#: Fields the interaction route consumes and the emit route refuses.
OBSERVATION_FIELDS = frozenset({"observations", "metadata"})

#: A signal's indicator is a key and its evidence. Nothing else: ``metadata_shape``
#: belongs to the registration, and letting a signal restate it would let a sender
#: relax the contract it is being checked against.
INDICATOR_ALIASES: frozenset[str] = frozenset({"key", "metadata"})

#: Fields this product sets on a signal. A caller may not supply them, for the
#: same reason a registration may not set its own warnings: schema flexibility
#: means a team can add a field, not that a payload can dictate bookkeeping.
RESERVED_EMISSION_FIELDS = frozenset(
    {
        "id",
        "registration_id",
        "signal_name",
        "receiver",
        "actionable",
        "actionability_note",
        "duplicate_attempts",
        "warnings",
        "revision",
        "created_at",
        "updated_at",
        "rendered",
    }
)


def canonical_emission(payload: Any) -> dict[str, Any]:
    """Fold an emission request onto the researched field names."""
    if not isinstance(payload, Mapping):
        raise EmissionError("a signal must be a JSON object")
    result: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key)
        if name in RESERVED_EMISSION_FIELDS:
            raise EmissionError(
                f"{name!r} is set by this product and cannot be supplied by a caller"
            )
        target = EMISSION_ALIASES.get(name)
        if target is None:
            raise EmissionError(
                f"{name!r} is not a field of a signal. The research names type, data, "
                f"indicators, urgency, occurred_at, idempotency_key, attribution and "
                f"broadcast_notification. Known fields: {', '.join(sorted(EMISSION_ALIASES))}"
            )
        result[target] = value
    return result


def canonical_indicator_entry(entry: Any, index: int) -> tuple[str, Any]:
    """Pull the key and the metadata out of one signal indicator."""
    if not isinstance(entry, Mapping):
        raise EmissionError(f"indicators[{index}] must be an object")
    unknown = sorted(str(key) for key in entry if str(key) not in INDICATOR_ALIASES)
    if unknown:
        raise EmissionError(
            f"indicators[{index}] carries {', '.join(unknown)}. A signal's indicator is a key "
            "and its metadata; metadata_shape belongs to the registration, and a signal that "
            "could restate it would be able to relax the contract it is checked against."
        )
    return str(entry.get("key", "")).strip(), entry.get("metadata")


def parse_timestamp(value: Any, field: str) -> str:
    """Parse an ISO 8601 instant and store it as UTC with millisecond precision.

    A signal's ``occurred_at`` is when the buyer did the thing, which is not when
    the sender noticed. Normalising to UTC makes the Live Feed orderable, and
    accepting a ``Z`` suffix or an offset means a sender does not have to
    pre-convert before it gets a timestamp it can rely on.
    """
    if not isinstance(value, str) or not value.strip():
        raise EmissionError(f"{field} is required and must be an ISO 8601 timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmissionError(
            f"{field} must be an ISO 8601 timestamp; got {value!r} ({exc})"
        ) from exc
    if parsed.tzinfo is None:
        # A naive timestamp is a real hazard rather than a nit: a signal with no
        # zone sorts wrongly in the feed and reads wrongly to a seller in another
        # one. Refused rather than assumed to be UTC, because assuming is how a
        # feed ends up eight hours out for half its users.
        raise EmissionError(
            f"{field} must carry a UTC offset, as in 2026-09-26T14:05:00Z; got {value!r}. "
            "The buyer's action time is compared against other signals' times, and a "
            "timestamp with no zone cannot be placed in that order."
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def normalise_attribution(value: Any, declared: list[str]) -> tuple[dict[str, str], list[str]]:
    """Check the attribution object against the registration's declared list.

    Returns the normalised object plus any warnings. Warnings rather than a
    refusal for a value the registration did not declare: the registration is
    additive, so a partner who registered ``person_id`` and then starts sending
    ``opportunity_id`` has done something the contract can accept, and dropping
    the signal would be worse than recording that the contract has not caught up.
    """
    if not isinstance(value, Mapping) or not value:
        raise EmissionError(
            "attribution is required and must be an object naming at least one of "
            f"{', '.join(ATTRIBUTION_TYPES)}"
        )
    result: dict[str, str] = {}
    warnings: list[str] = []
    for key, raw in value.items():
        name = str(key)
        if name not in ATTRIBUTION_TYPES:
            raise EmissionError(
                f"{name!r} is not one of the researched attribution values "
                f"({', '.join(ATTRIBUTION_TYPES)}). Salesloft derives the receiving user "
                "from this object, so a key it does not understand reaches no seller."
            )
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raise EmissionError(f"attribution.{name} is present but empty; omit it instead")
        result[require_attribution_type(name)] = str(raw).strip()
        if name not in declared:
            warnings.append(
                f"attribution.{name} is not declared by this registration. It was carried "
                "through because the contract is additive, but the registration should "
                "declare it so a receiver can rely on it."
            )
    if not result:
        raise EmissionError("attribution carried no usable value")
    return result, warnings


def resolve_receiver(
    attribution: Mapping[str, str], room: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Derive the receiving seller from the attribution, and report the derivation.

    The research says the receiving user is *derived* from the attribution and
    names the five values, but not which of them wins when a signal carries
    several. :data:`ATTRIBUTION_PRECEDENCE` is the decision, and the result names
    the value that decided it so a reader can check the reasoning rather than
    trust it.

    The seller itself comes from the room, because in this product the room is
    what a buyer is looking at and the room has an owner. When the room has no
    owner the answer says ``resolved: false`` rather than inventing one: a signal
    with no named recipient is a fact worth surfacing, and a Live Feed entry
    addressed to nobody is a bug someone should see.
    """
    considered = [key for key in ATTRIBUTION_PRECEDENCE if attribution.get(key)]
    decided = considered[0] if considered else None

    data = (room or {}).get("data") or {}
    seller = data.get("owner") if isinstance(data, Mapping) else None
    seller = seller.strip() if isinstance(seller, str) and seller.strip() else None

    return {
        "seller": seller,
        "resolved": bool(seller),
        "attributed_by": decided,
        "object": ATTRIBUTION_OBJECT.get(decided) if decided else None,
        "value": attribution.get(decided) if decided else None,
        "considered": considered,
        "unattributed": [key for key in sorted(attribution) if key not in considered],
        "basis": (
            "The research states that Salesloft derives the receiving user from the "
            "attribution value and does not publish an order across the five values. The "
            "order used here is recorded as the attribution-precedence inference. The "
            "seller itself is the owner of the room the interaction happened in; a signal "
            "whose room has no owner is reported as unresolved rather than addressed to "
            "nobody."
        ),
    }


def check_indicators(declared: list[Mapping[str, Any]], supplied: Any) -> list[dict[str, Any]]:
    """Check the indicators on a signal against the registration.

    A signal with no indicators is refused. The data flow matches every
    interaction "to a registered indicator", so a signal that matched nothing has
    nothing specific to say - and "A signal should have high value and should
    drive a seller to act" is the research's own reason to refuse it.
    """
    if not isinstance(supplied, (list, tuple)) or not supplied:
        raise EmissionError(
            "indicators is required and must carry at least one indicator. Every signal is "
            "matched to a registered indicator, and an indicator is what a seller reads; "
            "a signal that matched none has nothing to show."
        )
    by_key = {str(entry.get("key")): entry for entry in declared}

    checked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(supplied):
        key, metadata = canonical_indicator_entry(entry, index)
        if not key:
            raise EmissionError(f"indicators[{index}].key is required")
        if key in seen:
            raise EmissionError(f"indicator {key!r} is claimed twice on one signal")
        seen.add(key)
        if key not in by_key:
            raise UndeclaredIndicator(
                f"indicator {key!r} is not declared by this registration. Declared: "
                f"{', '.join(sorted(by_key))}. A signal must follow the structure defined on "
                "its registration, and an undeclared indicator cannot be rendered."
            )
        indicator = by_key[key]
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise EmissionError(f"indicators[{index}].metadata must be an object")

        decision = indicators.evaluate(indicator, metadata)
        if decision["reason"] == "metadata_mismatch":
            detail = "; ".join(
                f"{finding['path']} {finding['message']}" for finding in decision["findings"]
            )
            raise EmissionError(
                f"indicator {key!r} metadata does not satisfy its metadata_shape: {detail}"
            )
        if not decision["qualifies"]:
            observation = decision["observation"] or {}
            raise IndicatorNotQualified(
                f"indicator {key!r} claims "
                f"{decision['claim']['comparative']} {decision['claim']['threshold']}"
                f"{decision['claim']['unit']} but its own metadata reports "
                f"{observation.get('field')}={observation.get('value')}. A specific "
                "indicator whose evidence contradicts it is a sentence a seller would read "
                "and believe."
            )

        warnings: list[str] = []
        if decision["reason"] in ("no_bound_claimed", "bound_unresolvable", "bound_unverifiable"):
            warnings.append(
                f"indicator {key!r} qualified as {decision['reason']}: "
                + indicators.QUALIFICATION_REASONS[decision["reason"]]
            )
        checked.append(
            {
                "key": key,
                "metadata": dict(metadata),
                "qualification": {
                    "reason": decision["reason"],
                    "checked": decision["reason"]
                    not in ("no_bound_claimed", "bound_unresolvable", "bound_unverifiable"),
                    "claim": decision["claim"],
                    "observation": decision["observation"],
                },
                "warnings": warnings,
            }
        )
    return checked


def normalise_emission(
    payload: Mapping[str, Any],
    registration: Mapping[str, Any],
    *,
    room: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn an emission request into the record this product stores.

    ``registration`` is the record the type resolved to, already looked up. This
    function's job is everything that depends on it: the shape check, the
    indicator check, the attribution check, the urgency, and the timestamps.
    """
    fields = canonical_emission(payload)
    carried = sorted(OBSERVATION_FIELDS & set(fields))
    if carried:
        raise EmissionError(
            f"{', '.join(carried)} is not a field of a signal. The research names type, data, "
            "indicators, urgency, occurred_at, idempotency_key, attribution and "
            "broadcast_notification. Use the interactions route to send a measurement and "
            "have it matched against the registered indicators."
        )

    type_name = fields.get("type")
    if not isinstance(type_name, str) or not type_name.strip():
        raise EmissionError(
            "type is required: it names the signal registration this signal follows"
        )
    if type_name.strip() != str(registration.get("type")):
        raise EmissionError(
            f"type {type_name.strip()!r} does not match the registration it was resolved "
            f"through, which is {registration.get('type')!r}"
        )

    data = fields.get("data")
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise EmissionError("data must be an object; the registration's data_shape describes it")
    findings = schema.validate(registration.get("data_shape") or {}, dict(data))
    if findings:
        detail = "; ".join(f"{finding['path']} {finding['message']}" for finding in findings)
        raise EmissionError(f"data does not satisfy the registration's data_shape: {detail}")

    checked_indicators = check_indicators(
        list(registration.get("indicators") or []), fields.get("indicators")
    )

    urgency = require_urgency(fields.get("urgency"))
    warnings: list[str] = []
    if "urgency" not in fields:
        warnings.append(
            f"urgency was not supplied, so it was recorded as {DEFAULT_URGENCY!r}. The "
            "research fixes the three accepted values and does not state a default."
        )

    occurred_at = parse_timestamp(fields.get("occurred_at"), "occurred_at")
    idempotency_key = require_uuid4(fields.get("idempotency_key"), "idempotency_key")

    attribution, attribution_warnings = normalise_attribution(
        fields.get("attribution"), list(registration.get("attribution") or [])
    )
    warnings.extend(attribution_warnings)
    warnings.extend(warning for entry in checked_indicators for warning in entry["warnings"])

    broadcast = fields.get("broadcast_notification")
    if broadcast is None:
        broadcast = bool(registration.get("broadcast_notification", True))
    elif not isinstance(broadcast, bool):
        raise EmissionError("broadcast_notification must be true or false")

    locale = fields.get("locale")

    return {
        "registration_id": registration.get("id"),
        "signal_name": registration.get("signal_name"),
        "type": registration.get("type"),
        "integration_id": registration.get("integration_id"),
        "data": dict(data),
        "indicators": checked_indicators,
        "urgency": urgency,
        "occurred_at": occurred_at,
        "idempotency_key": idempotency_key,
        "attribution": attribution,
        "broadcast_notification": broadcast,
        "locale": locale,
        "duplicate_attempts": 0,
        "receiver": resolve_receiver(attribution, room),
        # Stored on the record rather than implied by the field being absent, so a
        # reader of a signal never has to guess whether absence meant "no" or
        # "this product does not know". The research is explicit that the sender
        # does not decide actionability.
        "actionable": False,
        "actionability_note": "governed by the end user's Play configuration, not by the sender",
        "warnings": warnings,
    }
