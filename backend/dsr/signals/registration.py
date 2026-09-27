"""Registering a signal type, and the rule that stops a registered one changing.

The research fixes the parts of a registration. It is "made up of: a signal type,
a signal name, data shape (metadata about the signal), descriptions, at least one
indicator (Indicators have metadata and description), an attribution", registered
once per integration with "at least one ``indicator`` with ``key`` +
``metadata_shape``" and "an ``attribution`` list".

It also fixes what happens afterwards, and the second half is the part with teeth:
"Per integration, the signal type can only be registered once" and "Globally
installed signals should be considered an immutable API contract with Salesloft
and only additive changes will be allowed."

The amendment rule this module implements
-----------------------------------------
That sentence has to become a decision a function makes, or it is a comment. The
rule chosen is the literal reading, and it is one-way:

    An amendment may only add. It may not remove, and it may not tighten.

Applied field by field:

* **Adding is allowed** wherever the addition cannot invalidate anything that was
  already valid: a new indicator, a new locale, a new attribution type, a new
  optional ``data_shape`` property, and ``broadcast_notification``, which is a
  default for future signals rather than a constraint on existing ones.
* **Removing is refused** everywhere. An amendment is a patch of *additions*, not
  a replacement of the declared sets, so a PATCH that lists one indicator adds
  that indicator rather than replacing the list with it. This is also the reading
  that cannot be got wrong by accident: there is no way to express a removal, so
  there is no way to accidentally perform one.
* **Tightening is refused.** Appending to ``data_shape.required`` makes every
  previously conforming signal invalid, because it now owes a field it did not
  owe before.
* **Rewriting is refused.** Changing an existing field's shape, an indicator's
  ``metadata_shape``, the ``type``, the ``signal_name``, the ``integration_id``, or
  the text of a locale that already exists. The first ones invalidate conforming
  data; the last would change a sentence a seller has already read, and a
  registration is a contract with a human in the loop as well as with the vendor.

``data_shape.required`` is therefore frozen in both directions: it cannot grow
(tightening) and it cannot shrink (removal). To require something new, register a
new signal type - which the one-per-integration rule allows, and which is the same
answer :meth:`dsr.signals.engine.SignalEngine.withdraw` gives for a contract with
signals already under it.

:func:`amendment_findings` returns *every* offending path, not the first, so a
partner correcting a rejected amendment does not have to resubmit once per field.

The emitted fields are a superset of the registered ones
----------------------------------------------------------
A signal may carry more than its registration declares. That is the product's
standing rule - arbitrary JSON, no migration, a team adds a field without
coordinating with anyone - and applying it to a partner-authored ``data_shape``
would mean a registration could never accept a field a later release invented.
The declared shape is therefore a floor: required fields and their types are
enforced, extra fields are allowed unless ``additionalProperties`` is false.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from dsr.signals import indicators, schema
from dsr.signals.errors import RegistrationError, SignalError
from dsr.signals.vocabulary import (
    ATTRIBUTION_TYPES,
    DEFAULT_LOCALE,
    require_attribution_type,
    require_locale_map,
)

#: Both spellings of every multi-word field the research names.
#:
#: The research writes the registration and emission fields in snake_case
#: (``signal_name``, ``data_shape``, ``occurred_at``), but a client written
#: against a JSON:API-shaped vendor will reach for camelCase. Accepting both
#: costs a dict and removes a whole class of "my request was rejected because of a
#: letter".
FIELD_ALIASES: dict[str, str] = {
    "signal_name": "signal_name",
    "signalName": "signal_name",
    "signal_type": "type",
    "type": "type",
    "data_shape": "data_shape",
    "dataShape": "data_shape",
    "indicators": "indicators",
    "description": "description",
    "attribution": "attribution",
    "broadcast_notification": "broadcast_notification",
    "broadcastNotification": "broadcast_notification",
    "idempotency_key": "idempotency_key",
    "idempotencyKey": "idempotency_key",
    "integration_id": "integration_id",
    "integrationId": "integration_id",
    "metadata_shape": "metadata_shape",
    "metadataShape": "metadata_shape",
    "occurred_at": "occurred_at",
    "occurredAt": "occurred_at",
    "email_tracked_content_id": "email_tracked_content_id",
    "emailTrackedContentId": "email_tracked_content_id",
}

#: Fields a caller may never set on a registration, because they are this
#: product's own bookkeeping. Schema flexibility means a team can add a field to
#: ``data``; it does not mean a payload can mark itself as a first-party
#: registration or dictate its own warnings.
RESERVED_FIELDS = frozenset({"id", "registration_id", "warnings", "revision", "created_at", "updated_at"})


def canonical(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fold camelCase onto snake_case and reject anything unrecognised.

    Unrecognised keys are refused rather than ignored. A registration is a
    contract, and a field that arrives misspelled and is dropped is a contract
    quietly missing a clause.
    """
    if not isinstance(payload, Mapping):
        raise RegistrationError("a registration must be a JSON object")
    result: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key)
        if name in RESERVED_FIELDS:
            raise RegistrationError(
                f"{name!r} is set by this product and cannot be supplied by a caller"
            )
        target = FIELD_ALIASES.get(name)
        if target is None:
            raise RegistrationError(
                f"{name!r} is not a field of a signal registration; the registration is a "
                f"contract, so an unrecognised field is refused rather than dropped. "
                f"Known fields: {', '.join(sorted(set(FIELD_ALIASES.values())))}"
            )
        result[target] = value
    return result


def _require_text(payload: Mapping[str, Any], field: str, what: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegistrationError(f"{field} is required: {what}")
    return value.strip()


def _type_name(value: str) -> str:
    if not value.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise RegistrationError(
            f"type {value!r} must be letters, digits, underscores, hyphens or dots; it names "
            "a machine-readable contract, not a sentence"
        )
    return value


def normalise_indicators(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Check the indicator list and lint each entry for specificity.

    "At least one ``indicator`` with ``key`` + ``metadata_shape``" is a hard
    requirement, so an empty list and an entry without a key are both refused. A
    missing *description* is a warning rather than a refusal, because the field
    the research names as required for the registration is ``key`` +
    ``metadata_shape``; the indicator-level description appears in a sentence
    describing what an indicator is, which is a weaker footing for a refusal than
    for a warning that is impossible to miss.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise RegistrationError(
            "a signal must carry at least one indicator; the research describes a signal as "
            "a type, a name, a data shape, descriptions, at least one indicator, and an "
            "attribution"
        )

    normalised: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise RegistrationError(f"indicators[{index}] must be an object")
        folded = canonical({k: v for k, v in entry.items() if k != "key"})

        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            raise RegistrationError(f"indicators[{index}].key is required")
        key = key.strip()
        if key in seen:
            raise RegistrationError(f"indicator key {key!r} is declared twice")
        seen.add(key)

        shape = folded.get("metadata_shape")
        if shape is None:
            raise RegistrationError(
                f"indicators[{index}].metadata_shape is required: the research specifies an "
                "indicator as a key plus a metadata_shape, and the shape is what the "
                "quantified metadata is checked against"
            )
        checked_shape = schema.require_object_shape(shape, f"indicators[{index}].metadata_shape")

        description = folded.get("description")
        templates: dict[str, str] = {}
        if description is not None:
            try:
                templates = require_locale_map(description, f"indicators[{index}].description")
            except SignalError as exc:
                warnings.append(
                    {
                        "code": "indicator_description_unusable",
                        "severity": "warning",
                        "detail": f"{exc}; the registration description will be used instead",
                    }
                )

        record = {
            "key": key,
            "metadata_shape": checked_shape,
            "description": templates,
            "locales": sorted(templates),
        }
        for finding in indicators.specificity_findings(key, checked_shape, description):
            warnings.append({**finding, "indicator": key})
        normalised.append(record)

    return normalised, warnings


def normalise_attribution(value: Any) -> list[str]:
    """The attribution list, drawn from the vocabulary the research names.

    Rejecting an unknown value is the point of the list: "Salesloft will use the
    attribution value to derive the appropriate Salesloft user", so an
    attribution key the receiver cannot resolve is a key that will not reach a
    seller.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise RegistrationError(
            "attribution is required and must name at least one of "
            f"{', '.join(ATTRIBUTION_TYPES)}; the research lists Person, Account, User, "
            "Opportunity and Email Content as the possible choices"
        )
    seen: list[str] = []
    for index, entry in enumerate(value):
        key = require_attribution_type(entry)
        if key not in seen:
            seen.append(key)
    return sorted(seen, key=ATTRIBUTION_TYPES.index)


def normalise_registration(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a registration request into the record this product stores.

    Warnings come back in the record rather than only in the response, because a
    registration nobody re-reads is where a vague indicator goes to live.
    """
    fields = canonical(payload)

    signal_name = _require_text(fields, "signal_name", "the human-readable name a seller sees")
    type_name = _type_name(_require_text(fields, "type", "the machine-readable signal type"))
    integration_id = _require_text(
        fields, "integration_id", "the integration this registration belongs to"
    )

    if "data_shape" not in fields:
        raise RegistrationError("data_shape is required: it is the contract a signal must follow")
    data_shape = schema.require_object_shape(fields["data_shape"], "data_shape")

    try:
        templates = require_locale_map(fields.get("description"), "description")
    except SignalError as exc:
        # Re-raised as the registration's own error so the HTTP layer answers
        # `signal_registration_error` rather than the package-wide one. A caller
        # fixing a registration should be told which half of the workflow refused.
        raise RegistrationError(str(exc)) from exc
    indicators_list, warnings = normalise_indicators(fields.get("indicators"))
    attribution = normalise_attribution(fields.get("attribution"))

    broadcast = fields.get("broadcast_notification")
    if broadcast is None:
        # The research says broadcast_notification "controls Live Feed display"
        # without stating a default. Defaulting it on is the reading that
        # matches the flow it describes - a signal is published to the Live Feed -
        # and the seeded demo carries a signal with it off so the other branch is
        # exercised. Recorded as the `default-broadcast` inference.
        broadcast = True
        warnings.append(
            {
                "code": "broadcast_notification_defaulted",
                "severity": "info",
                "detail": (
                    "broadcast_notification was not set, so it was recorded as true. The "
                    "research says the field controls Live Feed display and does not state a "
                    "default; set it explicitly to control it."
                ),
            }
        )
    elif not isinstance(broadcast, bool):
        raise RegistrationError("broadcast_notification must be true or false")

    idempotency_key = fields.get("idempotency_key")
    if idempotency_key is not None:
        idempotency_key = require_uuid4(idempotency_key, "idempotency_key")

    record: dict[str, Any] = {
        "signal_name": signal_name,
        "type": type_name,
        "integration_id": integration_id,
        "data_shape": data_shape,
        "description": templates,
        "locales": sorted(templates),
        "indicators": indicators_list,
        "indicator_keys": [entry["key"] for entry in indicators_list],
        "attribution": attribution,
        "broadcast_notification": broadcast,
    }
    if idempotency_key is not None:
        record["idempotency_key"] = idempotency_key
    record["warnings"] = warnings
    return record


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #


def amendment_findings(current: Mapping[str, Any], patch: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every reason this patch would break the contract, as a list.

    Empty means the patch is additive and may be applied. The declared sets -
    indicators, locales, attribution types, ``data_shape`` properties - are read
    as *additions* rather than as replacements, so a patch that names one
    indicator adds it. That is the literal reading of "only additive changes will
    be allowed" and it has a practical consequence worth stating: there is no way
    to express a removal here at all, so there is no way to accidentally perform
    one.
    """
    findings: list[dict[str, str]] = []
    fields = canonical(patch)

    def refuse(path: str, change: str) -> None:
        findings.append(
            {
                "path": path,
                "change": change,
                "detail": (
                    "a registered signal type is a contract, and only additive changes are "
                    "allowed. An amendment may add; it may not remove, tighten, or rewrite. "
                    "This change would invalidate a signal that was already valid, or change a "
                    "sentence a seller has already read."
                ),
            }
        )

    for field in ("type", "signal_name", "integration_id"):
        if field in fields and fields[field] != current.get(field):
            refuse(field, f"{current.get(field)!r} -> {fields[field]!r}")

    # description: new locales may be added, existing text may not be rewritten.
    if "description" in fields:
        try:
            proposed = require_locale_map(fields["description"], "description")
        except SignalError as exc:
            findings.append({"path": "description", "change": "unusable", "detail": str(exc)})
            proposed = {}
        for locale, message in (current.get("description") or {}).items():
            if locale in proposed and proposed[locale] != message:
                refuse(f"description.{locale}", "message text rewritten")

    # indicators: additions, and an existing one may gain a locale.
    if "indicators" in fields:
        existing_indicators = {
            str(entry.get("key")): entry for entry in (current.get("indicators") or [])
        }
        proposed_raw = fields["indicators"]
        if not isinstance(proposed_raw, (list, tuple)):
            findings.append({"path": "indicators", "change": "unusable", "detail": "must be a list"})
            proposed_raw = []
        seen: set[str] = set()
        for index, entry in enumerate(proposed_raw):
            if not isinstance(entry, Mapping) or not isinstance(entry.get("key"), str):
                findings.append(
                    {
                        "path": f"indicators[{index}]",
                        "change": "unusable",
                        "detail": "each indicator needs a string key",
                    }
                )
                continue
            key = entry["key"].strip()
            if key in seen:
                findings.append(
                    {
                        "path": f"indicators[{index}].key",
                        "change": "duplicate",
                        "detail": f"indicator key {key!r} appears twice in the patch",
                    }
                )
                continue
            seen.add(key)
            before = existing_indicators.get(key)
            if before is None:
                continue
            if "metadata_shape" in entry and entry["metadata_shape"] != before.get("metadata_shape"):
                refuse(f"indicators.{key}.metadata_shape", "shape rewritten")
            if "description" in entry:
                try:
                    before_text = require_locale_map(before.get("description") or {}, "description")
                    after_text = require_locale_map(entry["description"], "description")
                except SignalError:
                    after_text = {}
                for locale, message in before_text.items():
                    if locale in after_text and after_text[locale] != message:
                        refuse(f"indicators.{key}.description.{locale}", "message text rewritten")

    # attribution: additions only, and a value outside the vocabulary is refused
    # as unusable rather than as a removal.
    if "attribution" in fields:
        try:
            normalise_attribution(fields["attribution"])
        except SignalError as exc:
            findings.append({"path": "attribution", "change": "unusable", "detail": str(exc)})

    # data_shape: new optional properties yes, new requirements no, and no
    # removal of anything already declared.
    if "data_shape" in fields:
        proposed_shape = fields["data_shape"]
        if not isinstance(proposed_shape, Mapping):
            findings.append(
                {"path": "data_shape", "change": "unusable", "detail": "must be a JSON Schema object"}
            )
        else:
            before_shape = current.get("data_shape") or {}
            before_properties = before_shape.get("properties") or {}
            after_properties = proposed_shape.get("properties") or {}
            if not isinstance(before_properties, Mapping) or not isinstance(after_properties, Mapping):
                findings.append(
                    {
                        "path": "data_shape.properties",
                        "change": "unusable",
                        "detail": "properties must be an object of field name to schema",
                    }
                )
            else:
                for name, shape in before_properties.items():
                    if name in after_properties and after_properties[name] != shape:
                        refuse(f"data_shape.properties.{name}", "shape rewritten")
                for key in ("minimum", "maximum", "additionalProperties"):
                    if key in before_shape and key in proposed_shape and before_shape[key] != proposed_shape[key]:
                        refuse(f"data_shape.{key}", "constraint rewritten")
            before_required = before_shape.get("required")
            after_required = proposed_shape.get("required")
            if after_required is not None and not isinstance(after_required, (list, tuple)):
                findings.append(
                    {"path": "data_shape.required", "change": "unusable", "detail": "required must be a list"}
                )
            elif isinstance(before_required, (list, tuple)) and isinstance(after_required, (list, tuple)):
                for name in sorted(set(after_required) - set(before_required)):
                    # Adding a requirement makes every already-conforming signal
                    # invalid: it now owes a field it did not owe before.
                    refuse(f"data_shape.required.{name}", "requirement added")
                for name in sorted(set(before_required) - set(after_required)):
                    # Removing one is a removal, which this rule does not permit.
                    # `required` is therefore frozen in both directions.
                    refuse(f"data_shape.required.{name}", "requirement removed")

    return findings


def apply_amendment(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Fold an accepted additive patch into a registration.

    Every declared set is unioned with the patch rather than replaced by it,
    which is what makes a removal inexpressible. Callers must have run
    :func:`amendment_findings` first; this function assumes the patch is additive
    and re-derives nothing that was already checked.
    """
    fields = canonical(patch)
    merged = dict(current)

    if "description" in fields:
        templates = dict(current.get("description") or {})
        templates.update(require_locale_map(fields["description"], "description"))
        merged["description"] = templates
        merged["locales"] = sorted(templates)

    if "indicators" in fields:
        by_key = {str(entry["key"]): dict(entry) for entry in (current.get("indicators") or [])}
        warnings = list(current.get("warnings") or [])
        for index, entry in enumerate(fields["indicators"]):
            key = str(entry["key"]).strip()
            if key in by_key:
                # Only a description locale may have been added to an existing
                # indicator, since everything else about it was refused above.
                before = by_key[key]
                before_text = dict(before.get("description") or {})
                if entry.get("description"):
                    before_text.update(require_locale_map(entry["description"], "description"))
                before["description"] = before_text
                before["locales"] = sorted(before_text)
                continue
            shape = schema.require_object_shape(
                entry.get("metadata_shape") or {"type": "object"},
                f"indicators[{index}].metadata_shape",
            )
            text = (
                require_locale_map(entry["description"], f"indicators[{index}].description")
                if entry.get("description")
                else {}
            )
            by_key[key] = {
                "key": key,
                "metadata_shape": shape,
                "description": text,
                "locales": sorted(text),
            }
            warnings.extend(
                {**finding, "indicator": key}
                for finding in indicators.specificity_findings(key, shape, text or None)
            )
        ordered = sorted(by_key.values(), key=lambda entry: str(entry["key"]))
        merged["indicators"] = ordered
        merged["indicator_keys"] = [entry["key"] for entry in ordered]
        merged["warnings"] = warnings

    if "attribution" in fields:
        proposed = normalise_attribution(fields["attribution"])
        combined = list(dict.fromkeys([*(current.get("attribution") or []), *proposed]))
        merged["attribution"] = sorted(combined, key=ATTRIBUTION_TYPES.index)

    if "data_shape" in fields:
        before = dict(current.get("data_shape") or {})
        proposed = fields["data_shape"]
        properties = dict(before.get("properties") or {})
        properties.update(proposed.get("properties") or {})
        merged["data_shape"] = {**before, **proposed, "properties": properties}

    if "broadcast_notification" in fields:
        if not isinstance(fields["broadcast_notification"], bool):
            raise RegistrationError("broadcast_notification must be true or false")
        merged["broadcast_notification"] = fields["broadcast_notification"]

    return merged


# --------------------------------------------------------------------------- #
# Shared field validation
# --------------------------------------------------------------------------- #


def require_uuid4(value: Any, field: str) -> str:
    """Check a value is a version 4 UUID and return it in canonical form.

    The research names ``idempotency_key`` twice and specifies it as a UUID4 both
    times, so this checks the version nibble and the variant bits rather than
    merely checking the shape. An idempotency key that is not the kind of
    identifier the contract names is a key the far end will not honour.
    """
    if not isinstance(value, str) or not value.strip():
        raise SignalError(f"{field} is required and must be a UUID4 string")
    try:
        parsed = uuid.UUID(value.strip())
    except (ValueError, AttributeError, TypeError) as exc:
        raise SignalError(f"{field} must be a UUID4 string; got {value!r} ({exc})") from exc
    if parsed.version != 4:
        raise SignalError(
            f"{field} must be a version 4 UUID; this one is version {parsed.version}. The "
            "research names idempotency_key as a UUID4 in both the registration and the "
            "emission field lists."
        )
    return str(parsed)
