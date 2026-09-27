"""The per-row validation badges, which is the research's step 5.

"Admin clicks **Validate mapping**; the sales room reads the CRM's property/type
metadata and flags unknown properties, wrong types, and unsupported option values
**before any data is written**."

Those three findings are the researched ones, and they are grouped as
:data:`METADATA_FLAGS` because each one can only be produced by comparing the
mapping against a read of the CRM's own metadata. Everything else this module
reports is grouped as :data:`MAPPING_FLAGS`, because each one is visible from the
mapping alone - no CRM read is needed to see that two rows target the same
property. The grouping is the honest boundary: a reviewer can disagree with a
mapping-internal rule without arguing with the research.

Every finding carries four things, because a badge an admin cannot act on is a
badge they will work around:

``flag``
    The stable name, which the frontend and the tests both key on.
``severity``
    ``error`` blocks activation; ``warning`` is shown and does not.
``message``
    One sentence naming what is wrong with *this* row.
``detail``
    The structured evidence: the suggestions, the expected types, the internal
    option values, the near-miss label. Machine-readable and quoted verbatim in
    the report so a client renders it without parsing prose.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.db.audited import utcnow
from dsr.fieldmap.metadata import Metadata, Property
from dsr.fieldmap.transforms import REGISTRY, TransformUnavailable
from dsr.fieldmap.vocabulary import (
    DATAV_ELIGIBLE_KEY_TYPES,
    ENUMERATION_HINT,
    UNIQUE_KEY_LIMIT,
    UNIQUE_KEY_LIMIT_QUOTES,
    sends_in,
    sends_out,
    types_for,
)

#: The three findings the research names, in its words.
METADATA_FLAGS: tuple[str, ...] = ("unknown_property", "type_mismatch", "unsupported_option")

#: Findings this build added, each visible without a CRM read.
MAPPING_FLAGS: tuple[str, ...] = (
    "no_target",
    "duplicate_target",
    "transform_unavailable",
    "transform_mismatch",
)

#: Every flag, and whether it blocks activation.
SEVERITY: dict[str, str] = {
    "unknown_property": "error",
    "type_mismatch": "error",
    "unsupported_option": "error",
    "duplicate_target": "error",
    "transform_unavailable": "error",
    "transform_mismatch": "warning",
    "no_target": "warning",
    "no_sync_key": "error",
    "sync_key_unknown_property": "error",
    "sync_key_ineligible_type": "error",
    "sync_key_too_many_properties": "error",
    "sync_key_capacity": "error",
    "sync_key_already_unique": "info",
    "sync_key_already_keyed": "warning",
    "field_type_disagrees": "warning",
}


def finding(
    flag: str,
    message: str,
    *,
    detail: Mapping[str, Any] | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """One badge. The shape the grid, the tests and the client all agree on."""
    return {
        "flag": flag,
        "severity": (severity or SEVERITY.get(flag, "error")),
        "message": message,
        "detail": dict(detail or {}),
    }


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


def as_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A grid row flattened, whether it arrived as a record or already flat.

    A stored record keeps its own fields inside ``data``; the grid, the report and
    the page all want them at the top level. Normalising here means every caller can
    pass either shape, and - more importantly - that a caller *cannot* accidentally
    pass a record to something expecting a flat row and get a report in which every
    field is empty. That is not a hypothetical: it is what happened the first time
    this ran, and it produced a confidently wrong report claiming all five rows had
    no target and no transform.
    """
    flat = dict(row.get("data") or row)
    if "id" not in flat and row.get("id") is not None:
        flat["id"] = row["id"]
    return flat


def validate_row(
    row: Mapping[str, Any],
    metadata: Metadata,
    *,
    taken_targets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Every finding for one grid row, in the order a reader would fix them.

    ``taken_targets`` maps a target property to the source field that claimed it
    first, so a duplicate reports *both* halves - the field that already owns the
    property and the field that is about to fight it for it.
    """
    row = as_row(row)
    source_field = str(row.get("source_field") or "")
    target = str(row.get("target_property") or "")
    direction = str(row.get("direction") or "out")
    room_type = str(row.get("source_type") or "text")
    findings: list[dict[str, Any]] = []

    # -- the transform resolves ------------------------------------------------
    transform_name = str(row.get("transform") or "")
    version = row.get("transform_version")
    try:
        transform = REGISTRY.require(transform_name, int(version) if version is not None else None)
    except TransformUnavailable as exc:
        transform = None
        findings.append(
            finding(
                "transform_unavailable",
                f"Row {source_field}: no transform named {transform_name!r}"
                + (f" at version {version}" if version is not None else "")
                + f" is registered, so the value cannot be read ({exc}).",
                detail={
                    "transform": transform_name,
                    "transform_version": version,
                    "registered": list(REGISTRY.names()),
                    "hint": (
                        "Transforms are named, versioned functions registered in the connector. "
                        "Register the code, or declare it as data and expect this badge until it "
                        "is registered."
                    ),
                },
            )
        )

    # -- an unmapped field is not an error -------------------------------------
    if not target:
        findings.append(
            finding(
                "no_target",
                f"Row {source_field} has no CRM property, so nothing is sent for it.",
                detail={
                    "source_field": source_field,
                    "candidates": list(metadata.names)[:25],
                },
            )
        )
        return _row_result(row, findings)

    # -- unknown property ------------------------------------------------------
    prop = metadata.property(target)
    if prop is None:
        findings.append(
            finding(
                "unknown_property",
                f"Row {source_field}: the {metadata.provider} object "
                f"{metadata.crm_object!r} has no property {target!r}.",
                detail={
                    "target_property": target,
                    "crm_object": metadata.crm_object,
                    "provider": metadata.provider,
                    "exact_match": False,
                    "suggestions": list(metadata.suggest(target)),
                    "property_count": len(metadata.properties),
                    "hint": (
                        "Property names are matched exactly, case included, so a name that only "
                        "differs in case still fails here rather than in the CRM."
                    ),
                },
            )
        )
        return _row_result(row, findings)

    # -- two fields, one property ---------------------------------------------
    owner = (taken_targets or {}).get(target)
    if owner is not None and owner != source_field:
        findings.append(
            finding(
                "duplicate_target",
                f"Row {source_field} targets {target!r}, which row {owner} already targets; "
                "only one of them can be sent.",
                detail={"target_property": target, "claimed_by": owner, "source_field": source_field},
            )
        )

    # -- wrong type ------------------------------------------------------------
    allowed = types_for(metadata.provider, room_type)
    if allowed and prop.value_type not in allowed:
        findings.append(
            finding(
                "type_mismatch",
                f"Row {source_field} is a {room_type} and property {prop.name!r} is a "
                f"{prop.value_type}, so a {room_type} cannot be written there.",
                detail={
                    "source_type": room_type,
                    "target_property": prop.name,
                    "actual_type": prop.value_type,
                    "expected_types": list(allowed),
                    "read_from": "type" if metadata.provider == "hubspot" else "AttributeType",
                },
            )
        )

    # -- unsupported option value ---------------------------------------------
    findings.extend(_option_findings(row, prop, direction))

    # -- HubSpot's second axis -------------------------------------------------
    if not metadata.field_type_fits(prop):
        findings.append(
            finding(
                "field_type_disagrees",
                f"Property {prop.name!r} is a {prop.value_type} presented as a "
                f"{prop.field_type}, which is not a presentation {prop.value_type} takes.",
                detail={
                    "value_type": prop.value_type,
                    "field_type": prop.field_type,
                    "hint": (
                        "type determines the property's type and fieldType how it is presented; a "
                        "pair that disagrees is what HubSpot refuses on create."
                    ),
                },
            )
        )

    if transform is not None and not transform.applies(room_type):
        findings.append(
            finding(
                "transform_mismatch",
                f"Row {source_field}: transform {transform.name!r} is not a transform for a "
                f"{room_type} field.",
                detail={
                    "transform": transform.name,
                    "source_type": room_type,
                    "applies_to": list(transform.applies_to),
                },
            )
        )

    return _row_result(row, findings)


def _option_findings(row: Mapping[str, Any], prop: Property, direction: str) -> list[dict[str, Any]]:
    """The ``unsupported_option`` findings for a row, on the side that travels.

    Outbound the *value* side of a picklist table is what the CRM is sent, so that
    is what must be an internal option value. Inbound the *key* side is the CRM's
    own value arriving, so that is what must be internal. Same rule, opposite side,
    and the research's sentence is quoted on both - an admin told only "bad value"
    on an inbound row has no way to know they typed the label where the name goes.
    """
    if not prop.is_enumeration:
        return []
    config = row.get("transform_config") or {}
    table = config.get("map") if isinstance(config, Mapping) else None
    if not isinstance(table, Mapping) or not table:
        return []

    internal = prop.internal_values
    if not internal:
        return []

    checked_side = "value" if sends_out(direction) else "key"
    offending: list[dict[str, Any]] = []
    for key, value in table.items():
        candidate = str(value if checked_side == "value" else key)
        if candidate in internal:
            continue
        option = prop.option_for_label(candidate)
        offending.append(
            {
                "candidate": candidate,
                "side": checked_side,
                "matched_a_label": option is not None,
                "internal_name": option.value if option is not None else "",
            }
        )
    if not offending:
        return []

    return [
        finding(
            "unsupported_option",
            f"Row {row.get('source_field')}: {len(offending)} value(s) on the {checked_side} side of "
            f"the picklist table are not internal option values of {prop.name!r}.",
            detail={
                "target_property": prop.name,
                "direction": direction,
                "checked_side": checked_side,
                "offending": offending,
                "internal_values": list(internal),
                "labels": [option.label for option in prop.options],
                "hint": ENUMERATION_HINT,
            },
        )
    ]


def _row_result(row: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One row's verdict: the findings, the worst severity, and whether it is clean."""
    severities = {str(item["severity"]) for item in findings}
    status = "ok"
    if "error" in severities:
        status = "error"
    elif "warning" in severities:
        status = "warning"
    return {
        "row_id": row.get("id"),
        "source_field": str(row.get("source_field") or ""),
        "source_type": str(row.get("source_type") or "text"),
        "target_property": str(row.get("target_property") or ""),
        "direction": str(row.get("direction") or "out"),
        "transform": str(row.get("transform") or ""),
        "transform_version": row.get("transform_version"),
        "status": status,
        "findings": list(findings),
        "flags": [str(item["flag"]) for item in findings],
    }


# --------------------------------------------------------------------------- #
# The sync key
# --------------------------------------------------------------------------- #


def validate_sync_key(sync_key: Mapping[str, Any], metadata: Metadata) -> dict[str, Any]:
    """The findings about the pinned sync key, as its own section of the report.

    Separate from the rows because a key is not a mapped field: it carries the
    sales room's own row id, and the research makes it step 4 of the flow rather
    than another row an admin fills in.
    """
    properties = [str(name) for name in (sync_key.get("properties") or []) if str(name).strip()]
    findings: list[dict[str, Any]] = []
    if not properties:
        return {
            "pinned": False,
            "properties": [],
            "status": "error",
            "findings": [
                finding(
                    "no_sync_key",
                    "No sync key is pinned. The research makes it a step of the flow: it is the CRM "
                    "property that carries the sales room's own row id, marked unique so the CRM "
                    "itself rejects collisions.",
                    detail={"expected": "one or more CRM properties, marked unique"},
                )
            ],
            "flags": ["no_sync_key"],
        }

    resolved: list[dict[str, Any]] = []
    for name in properties:
        prop = metadata.property(name)
        if prop is None:
            findings.append(
                finding(
                    "sync_key_unknown_property",
                    f"The sync key names {name!r}, which the {metadata.crm_object!r} object does not carry.",
                    detail={"property": name, "suggestions": list(metadata.suggest(name))},
                )
            )
            continue
        entry: dict[str, Any] = {
            "property": prop.name,
            "label": prop.label,
            "value_type": prop.value_type,
            "unique": prop.unique,
            "keyed": prop.keyed,
            "key_name": prop.key_name,
        }
        if metadata.provider == "dataverse" and not metadata.type_is_key_eligible(prop.value_type):
            findings.append(
                finding(
                    "sync_key_ineligible_type",
                    f"The sync key names {prop.name!r}, a {prop.value_type}; an alternate key may "
                    "only be built from Decimal, String, DateTime, Lookup or Picklist attributes.",
                    detail={
                        "property": prop.name,
                        "attribute_type": prop.value_type,
                        "eligible_types": list(DATAV_ELIGIBLE_KEY_TYPES),
                    },
                )
            )
        if prop.keyed:
            findings.append(
                finding(
                    "sync_key_already_keyed",
                    f"{prop.name!r} is already in alternate key {prop.key_name or 'the table key'}; "
                    "pinning it again is a no-op rather than an error.",
                    detail={"property": prop.name, "key_name": prop.key_name},
                )
            )
        elif prop.unique:
            findings.append(
                finding(
                    "sync_key_already_unique",
                    f"{prop.name!r} already enforces uniqueness, so the CRM will reject collisions "
                    "without any further change.",
                    detail={"property": prop.name, "unique": True},
                )
            )
        resolved.append(entry)

    already = len([item for item in resolved if item["keyed"] or item["unique"]])
    needed = len([item for item in resolved if not (item["keyed"] or item["unique"])])
    used = metadata.unique_key_usage
    if used + needed > UNIQUE_KEY_LIMIT:
        findings.append(
            finding(
                "sync_key_capacity",
                f"This object already holds {used} of the {UNIQUE_KEY_LIMIT} unique keys its CRM "
                f"allows, and this sync key needs {needed} more.",
                detail={
                    "used": used,
                    "needed": needed,
                    "limit": UNIQUE_KEY_LIMIT,
                    "quotes": dict(UNIQUE_KEY_LIMIT_QUOTES),
                },
            )
        )

    severities = {str(item["severity"]) for item in findings}
    status = "error" if "error" in severities else ("warning" if "warning" in severities else "ok")
    return {
        "pinned": True,
        "properties": properties,
        "unique": bool(sync_key.get("unique", True)),
        "resolved": resolved,
        "already_enforced": already,
        "usage": {
            "used": used,
            "needed": needed,
            "limit": UNIQUE_KEY_LIMIT,
            # What is left after this key is honoured, not before it.
            "remaining": max(0, UNIQUE_KEY_LIMIT - used - needed),
        },
        "status": status,
        "findings": findings,
        "flags": [str(item["flag"]) for item in findings],
    }


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def build_report(
    mapping: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    metadata: Metadata,
    *,
    now: str = "",
) -> dict[str, Any]:
    """The whole report, computed from one snapshot of the grid and the metadata.

    ``rows`` and ``metadata`` are passed in rather than read here, so every badge
    in one report is computed against the same read. A CRM sync landing between
    two rows would otherwise produce a report whose own findings disagree with each
    other, which is the fastest way to lose a reader's trust in the whole page.
    """
    taken: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    flat_rows = [as_row(row) for row in rows]
    for row in flat_rows:
        results.append(validate_row(row, metadata, taken_targets=taken))
        target = str(row.get("target_property") or "")
        if target and target not in taken:
            taken[target] = str(row.get("source_field") or "")

    key_section = validate_sync_key(dict(mapping.get("sync_key") or {}), metadata)

    counts = {"ok": 0, "warning": 0, "error": 0, "info": 0}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    # The key's findings join the same tally on purpose: `counts` is the number of
    # things a reader has to fix before this mapping can go live, and a missing sync
    # key is one of them. `sync_key.status` says separately which section a finding
    # came from.
    for item in key_section["findings"]:
        counts[str(item["severity"])] = counts.get(str(item["severity"]), 0) + 1

    errors = counts.get("error", 0)
    covered = [
        item
        for item in results
        if sends_out(str(item["direction"])) and str(item["target_property"])
    ]
    return {
        "mapping_id": mapping.get("id"),
        "connection_id": mapping.get("connection_id") or "",
        "provider": mapping.get("provider") or "",
        "crm_object": mapping.get("crm_object") or "",
        "validated_at": now or utcnow(),
        "metadata": {
            "document": metadata.document,
            "fetched_at": metadata.fetched_at,
            "property_count": len(metadata.properties),
            "notes": list(metadata.notes),
        },
        "rows": results,
        "row_count": len(results),
        "counts": counts,
        "mappable_rows": len(covered),
        "inbound_rows": len(
            [item for item in results if sends_in(str(item["direction"])) and str(item["target_property"])]
        ),
        "sync_key": key_section,
        "can_activate": errors == 0 and key_section["pinned"],
        "blocking": [flag for item in results if item["status"] == "error" for flag in item["flags"]]
        + [str(item["flag"]) for item in key_section["findings"] if item["severity"] == "error"],
        "researched_flags": list(METADATA_FLAGS),
        "added_flags": list(MAPPING_FLAGS),
    }
