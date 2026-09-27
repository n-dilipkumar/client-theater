"""The manifest: a sales-room object descriptor, validated before it goes anywhere.

The research calls the descriptor "a sales-room package manifest (versioned)" and
lists its parts: "name, labels, field types, option sets". That makes it *data*,
and the extensibility section says why that matters: "a deployment ships its own
manifest, so 'install CRM fields for rooms' is a config change." So this module
normalises a manifest out of arbitrary JSON and publishes what it finds wrong with
it, and it never imports a default from anywhere: the shipped engagement manifest
is seeded as a record, like any other team would ship theirs.

Three severities, and the split is the interesting part:

``blocking``   the manifest cannot be installed at all, and the run is refused
``property``   one property cannot be created for this vendor and is skipped
``advisory``   the manifest is installable and the finding is reported anyway

A single unreadable property does not stop the nine beside it from being
installed, because the research is explicit that "the installer is idempotent by
construction so it can be run on every deploy and on every new tenant without a
human" - and a deploy pipeline that fails on one field nobody has needed yet
would not be run without a human. What it must never do is skip quietly: a
skipped property is counted on the run record, is visible in the diff view, and
leaves the object marked incomplete so a CI job can assert on it.

The two sourced limits are checked here, not in a vendor: "900 bytes per key and
16 columns per key". A key that violates either is refused with 422 before a
single property is created.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from dsr.crm_provisioning.errors import KeyConstraintError, ManifestError
from dsr.crm_provisioning.vendors import VendorAdapter
from dsr.crm_provisioning.vocabulary import (
    KEY_MAX_BYTES,
    KEY_MAX_COLUMNS,
    PROPERTY_TYPES,
    FINDING_SEVERITIES,
)

#: What a manifest's own key is called. One accepted spelling, so a manifest
#: cannot carry a ``sync_key`` and a ``key`` that disagree.
SYNC_KEY_FIELD = "sync_key"

#: A version is compared as an opaque string. Semver ordering is not required by
#: the research and imposing it would refuse a deployment whose manifest is
#: versioned by release date.
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

#: A name that has to survive being a path segment, a HubSpot internal name and a
#: Dataverse schema name at once. Deliberately strict: a manifest is created once
#: and provisioned into a CRM, and a name the CRM rejects is a name the
#: installer would discover at 3am on a new tenant.
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")

#: Dataverse string column formats this workflow will set. ``Email`` and
#: ``Phone`` are not in the research, so a manifest that wants them gets
#: ``String`` and a length, and an unrecognised format is an advisory rather
#: than a silent coercion.
DATAVerse_STRING_FORMATS: tuple[str, ...] = ("String", "Memo")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _finding(
    severity: str,
    code: str,
    message: str,
    *,
    path: str = "",
    prop: str = "",
) -> dict[str, Any]:
    assert severity in FINDING_SEVERITIES, severity
    return {"severity": severity, "code": code, "message": message, "path": path, "property": prop}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def normalise_manifest(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Coerce a manifest body into the one shape this package reads.

    Accepts either the neutral spelling or the researched vendor spelling for the
    parts that have both, because the descriptor is going to be written by a team
    and ``sync_key`` and ``key`` are both natural things to reach for. The
    normalised form is what is stored, so a client never has to know which
    spelling a given manifest used.

    This raises only on what makes a manifest unreadable. Everything that makes
    it *uninstallable* for a given vendor is a finding, and a finding is what the
    diff view renders.
    """
    _require(isinstance(payload, Mapping), "manifest must be a JSON object")
    body = dict(payload or {})

    manifest_id = _text(body.get("manifest_id") or body.get("id"))
    _require(manifest_id, "manifest_id is required")
    _require(
        NAME_PATTERN.match(manifest_id) is not None,
        f"manifest_id {manifest_id!r} must start with a letter and hold only "
        "letters, digits and underscores",
    )

    version = _text(body.get("version")) or "1"
    _require(
        VERSION_PATTERN.match(version) is not None,
        f"version {version!r} must be 1-64 characters of letters, digits, dot, dash, plus or underscore",
    )

    obj = body.get("object")
    _require(isinstance(obj, Mapping), "object is required, as an object with at least a name")
    object_name = _text(obj.get("name")) or manifest_id
    _require(
        NAME_PATTERN.match(object_name) is not None,
        f"object.name {object_name!r} must start with a letter and hold only "
        "letters, digits and underscores",
    )
    object_label = _text(obj.get("label")) or object_name.replace("_", " ").title()

    properties_in = body.get("properties")
    _require(isinstance(properties_in, list), "properties is required, as a list")
    properties: list[dict[str, Any]] = []
    for position, entry in enumerate(properties_in):
        _require(isinstance(entry, Mapping), f"properties[{position}] must be a JSON object")
        name = _text(entry.get("name"))
        _require(name, f"properties[{position}].name is required")
        _require(
            NAME_PATTERN.match(name) is not None,
            f"property {name!r} must start with a letter and hold only letters, "
            "digits and underscores",
        )
        properties.append(
            {
                "name": name,
                "label": _text(entry.get("label")) or name.replace("_", " ").title(),
                "type": _text(entry.get("type")) or "string",
                "group_name": _text(entry.get("group_name") or entry.get("groupName")),
                "length": entry.get("length"),
                "description": _text(entry.get("description")),
                "options": normalise_options(entry.get("options")),
                "required": bool(entry.get("required", False)),
            }
        )

    _require(properties, "properties is required, and must name at least one field")

    # The research's data flow ends with "records the mapping of room-object-id
    # -> CRM-object-id", so a manifest states which room-side object it
    # describes. The default is the manifest's own id, which is the only id a
    # manifest always has.
    room_object_id = _text(body.get("room_object_id")) or manifest_id

    sync_key_in = body.get(SYNC_KEY_FIELD) or body.get("key")
    sync_key: dict[str, Any] | None = None
    if sync_key_in is not None:
        _require(isinstance(sync_key_in, Mapping), f"{SYNC_KEY_FIELD} must be a JSON object")
        columns_in = sync_key_in.get("columns")
        _require(
            isinstance(columns_in, list) and columns_in,
            f"{SYNC_KEY_FIELD}.columns is required, as a non-empty list of property names",
        )
        columns: list[dict[str, Any]] = []
        for position, raw in enumerate(columns_in):
            _require(isinstance(raw, Mapping), f"{SYNC_KEY_FIELD}.columns[{position}] must be a JSON object")
            column = _text(raw.get("name") or raw.get("field"))
            _require(column, f"{SYNC_KEY_FIELD}.columns[{position}].name is required")
            length = raw.get("length")
            _require(
                length is None or (isinstance(length, int) and not isinstance(length, bool) and length > 0),
                f"{SYNC_KEY_FIELD} column {column!r} needs a positive integer length, or none",
            )
            columns.append({"name": column, "length": length})
        sync_key = {"columns": columns}

    return {
        "manifest_id": manifest_id,
        "version": version,
        "name": _text(body.get("name")) or object_label,
        "description": _text(body.get("description")),
        "room_object_id": room_object_id,
        "object": {
            "name": object_name,
            "label": object_label,
            "description": _text(obj.get("description")),
        },
        "properties": properties,
        SYNC_KEY_FIELD: sync_key,
    }


def normalise_options(raw: Any) -> list[dict[str, Any]]:
    """An option set. The research names "option sets" as a descriptor part."""
    if raw is None:
        return []
    _require(isinstance(raw, list), "options must be a list")
    options: list[dict[str, Any]] = []
    for position, entry in enumerate(raw):
        if isinstance(entry, Mapping):
            label = _text(entry.get("label") or entry.get("name") or entry.get("value"))
            value = _text(entry.get("value") or entry.get("name") or label)
        else:
            label = _text(entry)
            value = label
        _require(label, f"options[{position}] is empty")
        options.append({"label": label, "value": value})
    return options


def key_bytes(manifest: Mapping[str, Any]) -> int:
    """The indexed width of a declared key, in bytes.

    The research quotes Dataverse validating "the total key size ... like 900
    bytes per key", so the *number* is sourced. How a column's contribution is
    measured is this build's judgement and is recorded as the
    ``key-length-must-be-declared`` inference: a string column contributes its
    declared ``length``, a number contributes 8, a datetime 8, a boolean 1, and a
    picklist 4. The one thing deliberately not done is inventing a default string
    width - a column that does not declare one is refused, because a guess here
    either under-counts a key the vendor will reject or over-counts one it would
    accept.
    """
    by_name = {prop["name"]: prop for prop in manifest.get("properties") or []}
    sync_key = manifest.get(SYNC_KEY_FIELD) or {}
    total = 0
    for column in sync_key.get("columns") or []:
        prop = by_name.get(column["name"], {})
        kind = str(prop.get("type") or "string")
        if kind == "string":
            declared = column.get("length") or prop.get("length")
            total += int(declared or 0)
        elif kind == "number":
            total += 8
        elif kind == "datetime":
            total += 8
        elif kind == "bool":
            total += 1
        else:
            total += 4
    return total


def check_key(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse a key the vendor cannot build, before anything is created.

    Every offending column is named, and the counts are in the message, because a
    deploy that fails on the first key it tried and passes on a smaller one is a
    deploy nobody trusts.
    """
    sync_key = manifest.get(SYNC_KEY_FIELD)
    if not sync_key:
        return {}
    columns = sync_key.get("columns") or []
    by_name = {prop["name"]: prop for prop in manifest.get("properties") or []}

    unknown = [column["name"] for column in columns if column["name"] not in by_name]
    if unknown:
        raise KeyConstraintError(
            f"sync key names properties the manifest does not declare: {', '.join(sorted(unknown))}"
        )

    if len(columns) > KEY_MAX_COLUMNS:
        raise KeyConstraintError(
            f"sync key has {len(columns)} columns; the vendor's index constraint is "
            f"{KEY_MAX_COLUMNS} columns per key"
        )

    undeclared = [
        column["name"]
        for column in columns
        if str(by_name[column["name"]].get("type") or "string") == "string"
        and not (column.get("length") or by_name[column["name"]].get("length"))
    ]
    if undeclared:
        raise KeyConstraintError(
            "sync key string columns must declare a length, because the 900 byte "
            f"limit cannot be checked without one: {', '.join(sorted(undeclared))}"
        )

    total = key_bytes(manifest)
    if total > KEY_MAX_BYTES:
        raise KeyConstraintError(
            f"sync key is {total} bytes; the vendor's index constraint is "
            f"{KEY_MAX_BYTES} bytes per key"
        )

    return {
        "columns": [column["name"] for column in columns],
        "bytes": total,
        "max_bytes": KEY_MAX_BYTES,
        "max_columns": KEY_MAX_COLUMNS,
    }


#: The group field, under the names the two researched vendors use for it. HubSpot's
#: ``groupName`` is a required property field per the research; Dataverse's
#: ``AttributeGroupName`` is not in this workflow's required set at all, so the
#: asymmetry is what makes one field required on one vendor and absent on the other.
GROUP_FIELDS = frozenset({"groupName", "AttributeGroupName", "group_name"})


def missing_required_reason(
    prop: Mapping[str, Any], field_name: str, adapter: VendorAdapter
) -> str | None:
    """Why this vendor cannot create this property, or ``None`` if it can.

    Returned as a sentence fragment rather than a boolean because the finding is
    only useful if it says which field is missing: "skipped" sends the reader back
    to the manifest to look, "has no group_name" does not.

    The adapter declares which of its required fields it fills in from the
    manifest's own neutral descriptor, so this stays a lookup rather than a second
    copy of both vendors' rules. What is left is the fields a manifest genuinely
    has to be told, and there is exactly one of them: ``groupName``.
    """
    if field_name in adapter.derivable_fields:
        return None
    if field_name in GROUP_FIELDS:
        return None if prop.get("group_name") else "has no group_name"
    return f"has no {field_name}"


def validate(manifest: Mapping[str, Any], adapter: VendorAdapter) -> list[dict[str, Any]]:
    """Everything wrong with this manifest for this vendor, all at once.

    A caller gets the whole list rather than the first problem, because a
    manifest is written once and corrected once, and one attempt per field would
    be the slowest possible way to converge on a valid descriptor.
    """
    findings: list[dict[str, Any]] = []
    names: set[str] = set()

    for position, prop in enumerate(manifest.get("properties") or []):
        name = prop["name"]
        if name in names:
            findings.append(
                _finding(
                    "blocking",
                    "duplicate_property",
                    f"property {name!r} is declared twice; the CRM would have two "
                    "requests for one field",
                    path=f"properties.{position}",
                    prop=name,
                )
            )
        names.add(name)

        kind = prop["type"]
        if kind not in PROPERTY_TYPES:
            findings.append(
                _finding(
                    "property",
                    "unknown_type",
                    f"property {name!r} declares type {kind!r}, which is not one of "
                    f"{', '.join(PROPERTY_TYPES)}; it will be skipped for {adapter.vendor}",
                    path=f"properties.{position}.type",
                    prop=name,
                )
            )
        elif not adapter.supports(kind):
            findings.append(
                _finding(
                    "property",
                    "type_not_mapped",
                    f"property {name!r} declares type {kind!r}, which has no "
                    f"{adapter.vendor} mapping in this build; it will be skipped rather "
                    "than created with a guessed field type",
                    path=f"properties.{position}.type",
                    prop=name,
                )
            )

        for field_name in adapter.required_property_fields:
            missing_reason = missing_required_reason(prop, field_name, adapter)
            if missing_reason is None:
                continue
            findings.append(
                _finding(
                    "property",
                    "missing_required_field",
                    f"property {name!r} {missing_reason}; it will be skipped for {adapter.vendor}",
                    path=f"properties.{position}",
                    prop=name,
                )
            )

        if kind == "enumeration":
            if not prop.get("options"):
                findings.append(
                    _finding(
                        "advisory",
                        "no_options",
                        f"property {name!r} is an enumeration with no option set, so "
                        "the CRM will store a value no picker offers",
                        path=f"properties.{position}.options",
                        prop=name,
                    )
                )
            else:
                seen_labels: set[str] = set()
                seen_values: set[str] = set()
                for option in prop["options"]:
                    if option["label"] in seen_labels or option["value"] in seen_values:
                        findings.append(
                            _finding(
                                "advisory",
                                "duplicate_option",
                                f"property {name!r} repeats an option label or value; "
                                "the CRM will keep only the first",
                                path=f"properties.{position}.options",
                                prop=name,
                            )
                        )
                        break
                    seen_labels.add(option["label"])
                    seen_values.add(option["value"])

        if kind == "string" and not prop.get("length"):
            findings.append(
                _finding(
                    "advisory",
                    "no_length",
                    f"property {name!r} is a string with no length; the vendor will "
                    "apply its own default, which may be shorter than the data",
                    path=f"properties.{position}.length",
                    prop=name,
                )
            )

    if manifest.get("object") and not adapter.object_label_required and manifest["object"].get("label"):
        # Nothing to do, and deliberately not a finding: Dataverse's table
        # definition carries no display label at the object level, so a manifest
        # that declares one is not wrong, it is just carrying a label this vendor
        # has nowhere to put. Recorded here so the asymmetry with HubSpot is
        # stated rather than left for a reader to infer from a missing field.
        pass

    sync_key = manifest.get(SYNC_KEY_FIELD)
    if sync_key and not adapter.key_create:
        findings.append(
            _finding(
                "advisory",
                "key_not_supported",
                f"this manifest declares a sync key and {adapter.vendor} has no "
                "sourced alternate-key call in this build, so the object will be "
                "installed without one; the key request is reported, not sent",
                path=SYNC_KEY_FIELD,
            )
        )

    return findings


def findings_for(manifest: Mapping[str, Any], adapter: VendorAdapter) -> list[dict[str, Any]]:
    """Every finding for this manifest against this vendor, most severe first.

    Both halves of the install contract in one list: what would refuse the run,
    and what would merely be reported. A caller that wants "can I install this"
    reads the first group; a caller that wants "what should I fix" reads all of
    them in one request.
    """
    ordered = sorted(
        validate(manifest, adapter),
        key=lambda f: (FINDING_SEVERITIES.index(f["severity"]), f["code"], f["property"]),
    )
    return ordered


def blocking(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(f) for f in findings if f.get("severity") == "blocking"]


def property_level(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(f) for f in findings if f.get("severity") == "property"]


def advisory(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(f) for f in findings if f.get("severity") == "advisory"]
