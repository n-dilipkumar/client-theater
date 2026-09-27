"""Reading a room event's own fields and writing them onto a CRM object.

This is the researched step 3 and the first half of step 4: resolve the buyer's CRM
record "using the field mapping / sync key", then call the create endpoint "with mapped
properties". Both halves live here so that :mod:`dsr.crm_engagement.engine` reads as the
five steps of the researched flow and nothing else.

Three things the research settled, and how they show up below
-----------------------------------------------------------

**Transforms are named, versioned functions, not ad-hoc code.** "Transforms are named,
versioned functions registered in the connector (e.g. ``email.normalize``,
``picklist.map``, ``date.iso8601``), so a deployment can add a transform without touching
the sync engine." So :data:`TRANSFORMS` is a registry of named functions, each with a
version, each returning a value *and* any finding about that value - and a field map
row names one rather than carrying a body.

**A mapping is validated before anything is written, and validation flags rather than
refuses.** "Admin clicks **Validate mapping**; the sales room reads the CRM's
property/type metadata and flags unknown properties, wrong types, and unsupported option
values before any data is written." :func:`map_event` therefore never raises on a value
it cannot use: it omits the property, records a finding naming the field and the reason,
and the create still happens with the rest of the payload. A finding nobody can see is a
finding nobody fixes, so every one of them lands in the Sync log next to the attempt.

**A source that resolves to nothing is not sent as null.** Sending ``"dwell_seconds":
None`` to a CRM is a write that either fails or, worse, succeeds and overwrites a value
with nothing. So an unresolved source is a finding and the property is left out, and the
row says which property is missing and why.

Why unit variants are not synonyms
----------------------------------
``dwell_ms`` is not another spelling of ``dwell_seconds``; it is a different number.
Treating one as a synonym for the other would send a value off by a factor of a thousand
with nothing in the row to say so, so the synonym lists in
:mod:`dsr.crm_engagement.vocabulary` name spellings only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.crm_engagement.errors import InvalidFieldMap
from dsr.crm_engagement.vocabulary import (
    DIRECTIONS,
    FIELD_SYNONYMS,
    TRANSFORM_NAMES,
)

#: The envelope fields a field map may name as a source. These are the store's fixed
#: vocabulary, not the event's payload, so they are read from the envelope rather than
#: from ``data``.
ENVELOPE_SOURCES: tuple[str, ...] = ("id", "room_id", "created_at", "updated_at", "revision")

#: Warning codes a mapping can produce. Split into two classes because they call for
#: different behaviour in the payload: a ``soft`` finding still ships its value, a
#: ``hard`` finding omits it. The two that are not produced by a transform -
#: ``required_field_omitted`` and ``sync_key_unresolved`` - are hard because both mean the
#: create this map produces is incomplete in a way the team declared mandatory, and a
#: finding whose severity says "soft" on a blocking problem is a lie in a column.
HARD_FINDINGS: frozenset[str] = frozenset(
    {
        "source_unresolved",
        "not_a_number",
        "unmapped_option",
        "transform_error",
        "no_value",
        "required_field_omitted",
        "sync_key_unresolved",
    }
)
SOFT_FINDINGS: frozenset[str] = frozenset({"naive_timestamp", "email_without_at", "defaulted"})


@dataclass(frozen=True)
class Transformed:
    """One transform's answer: a value, and anything worth saying about it."""

    value: Any
    findings: tuple[dict[str, Any], ...] = ()

    @property
    def usable(self) -> bool:
        return not any(entry["code"] in HARD_FINDINGS for entry in self.findings)


# --------------------------------------------------------------------------- #
# Value readers. Tolerant, because a room event's payload is arbitrary JSON.
# --------------------------------------------------------------------------- #


def as_text(value: Any) -> str:
    """A scalar as a trimmed string; ``None`` for nothing usable."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    return ""


def as_number(value: Any) -> float | None:
    """A number out of whatever the event stored, or ``None``.

    Booleans are refused: ``True`` is an ``int`` in Python, and a CRM numeric property
    that receives ``1`` because a row said ``true`` is a wrong number that looks right.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = as_text(value)
    if not text:
        return None
    cleaned = text.replace(",", "").replace("_", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def dotted(data: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Read a dotted JSON path out of a payload. ``(False, None)`` when it is not there.

    A literal key containing dots is tried before the path walk, because a team's own
    field may legitimately be called ``buyer.email`` as a single key and splitting it
    would look for a nested object that does not exist.
    """
    if not path:
        return False, None
    if path in data:
        return True, data[path]
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def read_source(event: Mapping[str, Any], source: str) -> tuple[bool, Any, str]:
    """Find one field on a room engagement row.

    Returns ``(found, value, located_at)``. The resolution order is:

    1. an envelope field, since the store's own vocabulary is fixed and a field map
       naming it means the envelope;
    2. the exact dotted path in the event's own payload;
    3. the canonical field's synonym list, each also tried as a dotted path.

    ``located_at`` is the path that actually answered, and it is reported so a field map
    whose source resolved by synonym rather than by its own name is visible rather than
    mysterious.
    """
    if source in ENVELOPE_SOURCES:
        if source in event:
            return True, event[source], source
        return False, None, source
    found, value = dotted(event.get("data") or {}, source)
    if found:
        return True, value, source
    for synonym in FIELD_SYNONYMS.get(source, ()):
        found, value = dotted(event.get("data") or {}, synonym)
        if found:
            return True, value, synonym
    return False, None, source


# --------------------------------------------------------------------------- #
# Transforms
# --------------------------------------------------------------------------- #


def _transform_identity(value: Any, spec: Mapping[str, Any]) -> Transformed:
    del spec
    if value is None or (isinstance(value, str) and not value.strip()):
        return Transformed(None, [{"code": "no_value", "detail": "the source resolved to nothing"}])
    return Transformed(value)


def _transform_email(value: Any, spec: Mapping[str, Any]) -> Transformed:
    """Lowercase and trim, which is what "lowercase email" asks for.

    A result with no ``@`` is a *soft* finding: the value still ships. A CRM that
    rejects it will say so in its own error, which is a better place to learn that an
    event carried a malformed address than a guess made here.
    """
    del spec
    text = as_text(value).lower()
    if not text:
        return Transformed(None, [{"code": "no_value", "detail": "the source resolved to nothing"}])
    findings = []
    if "@" not in text:
        findings.append({"code": "email_without_at", "detail": f"{text!r} has no @"})
    return Transformed(text, findings)


def _transform_iso8601(value: Any, spec: Mapping[str, Any]) -> Transformed:
    """An ISO-8601 timestamp, which is what "ISO-8601 date" asks for.

    A naive value is a *soft* finding rather than a silent assumption: this build does
    not know the room's timezone, and inventing one would move the buyer's clock.
    A number is refused rather than guessed, because whether 1750000000 is seconds or
    milliseconds is a fact about the sender that is not in the row.
    """
    del spec
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return Transformed(
            None,
            [
                {
                    "code": "transform_error",
                    "detail": (
                        f"{value!r} is a number; whether it is seconds or milliseconds is "
                        "not in the row, so it is not converted"
                    ),
                }
            ],
        )
    if isinstance(value, datetime):
        parsed: datetime | None = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = as_text(value)
        if not text:
            return Transformed(None, [{"code": "no_value", "detail": "the source resolved to nothing"}])
        candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return Transformed(
                None,
                [{"code": "transform_error", "detail": f"{text!r} is not an ISO-8601 timestamp"}],
            )
    if parsed.tzinfo is None:
        return Transformed(
            parsed.isoformat(),
            [
                {
                    "code": "naive_timestamp",
                    "detail": (
                        f"{parsed.isoformat()} carries no UTC offset; the room's timezone is "
                        "not in the row, so none is assumed"
                    ),
                }
            ],
        )
    return Transformed(parsed.astimezone(timezone.utc).isoformat())


def _option_table(spec: Mapping[str, Any]) -> dict[str, str]:
    """The label → internal value table, from either supported spelling.

    The research's phrase is "picklist label → internal option value", so a map is the
    primary shape. A list of ``{"label", "value"}`` rows is accepted because that is how
    a CRM's own option set reads when it is pasted in, and a deployment should not have
    to reshape it first.
    """
    options = spec.get("options")
    table: dict[str, str] = {}
    if isinstance(options, Mapping):
        for label, value in options.items():
            table[str(label).strip().lower()] = value
    elif isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        for row in options:
            if isinstance(row, Mapping) and "label" in row and "value" in row:
                table[str(row["label"]).strip().lower()] = row["value"]
    return table


def _transform_picklist(value: Any, spec: Mapping[str, Any]) -> Transformed:
    """A display label translated to the option's internal value.

    An unmapped label is a *hard* finding and the property is left out. The research is
    explicit that an unsupported option value is something to flag "before any data is
    written", and the only honest flag on a create is one that keeps the value off the
    wire. A label that is already an internal value passes through untouched, because a
    team that stored the internal value to begin with has nothing to translate.
    """
    text = as_text(value)
    if not text:
        return Transformed(None, [{"code": "no_value", "detail": "the source resolved to nothing"}])
    table = _option_table(spec)
    key = text.lower()
    if key in table:
        return Transformed(table[key])
    internals = {str(internal).strip().lower() for internal in table.values()}
    if not table or key in internals:
        return Transformed(text)
    return Transformed(
        None,
        [
            {
                "code": "unmapped_option",
                "detail": (
                    f"{text!r} is not in the option table "
                    f"({', '.join(sorted(table)) or 'empty'})"
                ),
            }
        ],
    )


def _transform_number(value: Any, spec: Mapping[str, Any]) -> Transformed:
    """Coerce to a number, which is what "number coercion" asks for.

    An integer input stays an integer: Dataverse and Salesforce both distinguish the
    two on a numeric column, and turning ``3`` into ``3.0`` because a float was easier
    is a silent type change on somebody else's schema.
    """
    del spec
    if value is None or (isinstance(value, str) and not value.strip()):
        return Transformed(None, [{"code": "no_value", "detail": "the source resolved to nothing"}])
    if isinstance(value, bool):
        return Transformed(
            None,
            [{"code": "not_a_number", "detail": f"{value!r} is a boolean, not a number"}],
        )
    number = as_number(value)
    if number is None:
        return Transformed(
            None, [{"code": "not_a_number", "detail": f"{as_text(value)!r} is not a number"}]
        )
    if isinstance(value, int) or (isinstance(value, str) and "." not in value and "," not in value):
        return Transformed(int(number))
    return Transformed(number)


#: The named, versioned transform registry. A field map row names one of these keys.
TRANSFORMS: dict[str, dict[str, Any]] = {
    "identity": {
        "version": 1,
        "label": "Pass the value through unchanged",
        "run": _transform_identity,
    },
    "email.normalize": {
        "version": 1,
        "label": "Lowercase email",
        "run": _transform_email,
    },
    "date.iso8601": {
        "version": 1,
        "label": "ISO-8601 timestamp",
        "run": _transform_iso8601,
    },
    "picklist.map": {
        "version": 1,
        "label": "Picklist label to internal option value",
        "run": _transform_picklist,
        "requires": "options",
    },
    "number": {
        "version": 1,
        "label": "Number coercion",
        "run": _transform_number,
    },
}

#: Accepted under a few spellings, because a team writing a field map by hand reaches for
#: the dotted form, and a team copying from a spec reaches for the flat one.
_TRANSFORM_ALIASES: dict[str, str] = {
    "email": "email.normalize",
    "lowercase_email": "email.normalize",
    "iso8601": "date.iso8601",
    "date": "date.iso8601",
    "picklist": "picklist.map",
    "number_coerce": "number",
    "none": "identity",
}


def canonical_transform(name: Any) -> str:
    """The registry key a transform name means, or the name itself when unknown.

    An unknown name is returned rather than refused: the research says a deployment can
    add a transform to the registry, so a row naming one this build does not carry is a
    *finding* on the create, not a 422 that stops the connector from being saved.
    """
    text = as_text(name).lower() or "identity"
    return _TRANSFORM_ALIASES.get(text, text)


def run_transform(name: Any, value: Any, spec: Mapping[str, Any] | None = None) -> Transformed:
    """Run one named transform, turning an unexpected failure into a finding."""
    key = canonical_transform(name)
    entry = TRANSFORMS.get(key)
    if entry is None:
        return Transformed(
            None,
            [
                {
                    "code": "transform_error",
                    "detail": f"{key!r} is not a transform this build registers",
                }
            ],
        )
    runner: Callable[..., Transformed] = entry["run"]
    try:
        return runner(value, spec or {})
    except Exception as exc:  # noqa: BLE001 - a transform must not take the create down
        return Transformed(
            None,
            [{"code": "transform_error", "detail": f"{key} raised {type(exc).__name__}: {exc}"}],
        )


# --------------------------------------------------------------------------- #
# The field map
# --------------------------------------------------------------------------- #


@dataclass
class Mapped:
    """One event, expressed as CRM properties, plus everything noticed on the way."""

    properties: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    sync_key: dict[str, Any] = field(default_factory=dict)
    located: dict[str, str] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        """Whether every mapped field resolved without a finding."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "properties": dict(self.properties),
            "findings": [dict(entry) for entry in self.findings],
            "sync_key": dict(self.sync_key),
            "located": dict(self.located),
        }


def normalise_field_map(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a field map and return it in canonical shape.

    Refuses only what could not produce a complete, non-colliding create payload. What
    the research calls *validation* - unknown properties, wrong types, unsupported option
    values - depends on a live CRM's metadata, which is W2's API surface and not this
    workflow's, so those become findings on the create rather than a refusal here. See
    the ``unverifiable_properties`` inference.
    """
    event_type = as_text(payload.get("event_type"))
    if not event_type:
        raise InvalidFieldMap("event_type is required: a field map says which event type it covers")

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, Sequence) or isinstance(raw_fields, (str, bytes)) or not raw_fields:
        raise InvalidFieldMap("fields is required: a field map with no fields sends nothing")

    fields: list[dict[str, Any]] = []
    targets: set[str] = set()
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, Mapping):
            raise InvalidFieldMap(f"fields[{index}] is not an object")
        source = as_text(raw.get("source"))
        target = as_text(raw.get("target") or raw.get("target_property"))
        if not target:
            raise InvalidFieldMap(f"fields[{index}] has no target property")
        if target in targets:
            raise InvalidFieldMap(
                f"target property {target!r} is mapped twice; one create cannot send it twice"
            )
        targets.add(target)
        direction = as_text(raw.get("direction")).lower() or "out"
        if direction not in DIRECTIONS:
            raise InvalidFieldMap(
                f"fields[{index}] direction {direction!r} is not one of {list(DIRECTIONS)}"
            )
        transform = canonical_transform(raw.get("transform"))
        field_spec: dict[str, Any] = {
            "source": source or target,
            "target": target,
            "direction": direction,
            "transform": transform,
        }
        if transform == "picklist.map":
            options = raw.get("options", raw.get("option_map"))
            if not _option_table({"options": options}):
                raise InvalidFieldMap(
                    f"fields[{index}] uses picklist.map but declares no options, so no label "
                    "could ever be translated"
                )
            field_spec["options"] = options
        if raw.get("default") is not None:
            field_spec["default"] = raw["default"]
        if raw.get("required"):
            field_spec["required"] = True
        fields.append(field_spec)

    raw_sync = payload.get("sync_key")
    if not isinstance(raw_sync, Mapping):
        raise InvalidFieldMap(
            "sync_key is required: it is the CRM property carrying the room's own row id, "
            "and without it the CRM cannot reject a duplicate"
        )
    sync_property = as_text(raw_sync.get("target_property") or raw_sync.get("target"))
    if not sync_property:
        raise InvalidFieldMap("sync_key.target_property is required")
    if sync_property in targets:
        raise InvalidFieldMap(
            f"sync_key.target_property {sync_property!r} is also a mapped field; the sync key "
            "is injected from the row's own id and must not be mapped twice"
        )
    sync_key = {
        "source": as_text(raw_sync.get("source")) or "id",
        "target_property": sync_property,
        "transform": canonical_transform(raw_sync.get("transform")),
    }

    return {
        "event_type": event_type,
        "connector_id": as_text(payload.get("connector_id")),
        "fields": fields,
        "sync_key": sync_key,
        "label": as_text(payload.get("label")),
        "enabled": bool(payload.get("enabled", True)),
        "notes": as_text(payload.get("notes")),
    }


def _finding(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "detail": detail, "severity": "hard" if code in HARD_FINDINGS else "soft", **extra}


def map_event(event: Mapping[str, Any], field_map: Mapping[str, Any]) -> Mapped:
    """Turn one room engagement row into the properties a create will carry.

    Direction is respected: the research's field map gives each field "its direction
    (in / out / both)", and a create only carries the outbound half. An ``in``-only field
    is not mapped and is not reported, because it was never going to be written.

    Nothing here raises. A value that cannot be used becomes a finding and the property
    is left out, so one bad field on a five-field map does not cost the other four.
    """
    result = Mapped()
    for field_spec in field_map.get("fields") or []:
        if as_text(field_spec.get("direction")) not in ("out", "both"):
            continue
        source = as_text(field_spec.get("source"))
        target = as_text(field_spec.get("target"))
        found, value, located = read_source(event, source)
        if not found or value is None:
            if found:
                code, detail = "no_value", f"{source!r} resolved to nothing"
            else:
                code, detail = "source_unresolved", f"no field {source!r} on this event"
            if field_spec.get("default") is not None:
                result.properties[target] = field_spec["default"]
                result.findings.append(
                    _finding("defaulted", f"used the field map's default for {target!r}", field=target, source=source)
                )
                continue
            result.findings.append(_finding(code, detail, field=target, source=source, located=located))
            if field_spec.get("required"):
                # A field the map says is required, with nothing to build it from. Named
                # separately from the ordinary source finding because "this property is
                # missing" and "this property is required and missing" call for different
                # urgency, and only the second is a configuration mistake.
                result.findings.append(
                    _finding(
                        "required_field_omitted",
                        f"{target!r} is marked required and there is no {source!r} on this event",
                        field=target,
                        source=source,
                    )
                )
            continue

        result.located[target] = located
        transformed = run_transform(field_spec.get("transform"), value, field_spec)
        for entry in transformed.findings:
            result.findings.append(
                _finding(
                    str(entry["code"]),
                    str(entry["detail"]),
                    field=target,
                    source=source,
                    located=located,
                    transform=canonical_transform(field_spec.get("transform")),
                )
            )
        if transformed.usable:
            result.properties[target] = transformed.value
        elif field_spec.get("required"):
            result.findings.append(
                _finding(
                    "required_field_omitted",
                    f"{target!r} is marked required and could not be built from {source!r}",
                    field=target,
                    source=source,
                )
            )

    sync_spec = field_map.get("sync_key") or {}
    sync_property = as_text(sync_spec.get("target_property"))
    sync_source = as_text(sync_spec.get("source")) or "id"
    found, value, located = read_source(event, sync_source)
    reason = ""
    if not found or value is None or as_text(value) == "":
        reason = f"the sync key {sync_property!r} reads {sync_source!r}, which this event does not carry"
    else:
        transformed = run_transform(sync_spec.get("transform"), value, sync_spec)
        if transformed.usable:
            result.properties[sync_property] = transformed.value
            result.sync_key = {"property": sync_property, "value": transformed.value, "source": located}
        else:
            reason = (
                f"the sync key {sync_property!r} could not be built from {sync_source!r}: "
                f"{transformed.findings[0]['detail']}"
            )
    if reason:
        # The sync key is the one property that cannot be optional: it is what lets the CRM
        # reject a duplicate and what the researched step 5 keys future updates on. So its
        # absence is one hard finding against the whole map, whatever the cause, and the
        # engine blocks on exactly that code.
        result.findings.append(
            _finding(
                "sync_key_unresolved",
                reason,
                field=sync_property,
                source=located,
            )
        )
    return result


def describe_transforms() -> list[dict[str, Any]]:
    """The registry, served as data so a client's picker cannot drift from the engine."""
    rows = []
    for name in TRANSFORM_NAMES:
        entry = TRANSFORMS[name]
        rows.append(
            {
                "name": name,
                "version": entry["version"],
                "label": entry["label"],
                "requires": entry.get("requires"),
                "aliases": sorted(alias for alias, target in _TRANSFORM_ALIASES.items() if target == name),
            }
        )
    return rows
