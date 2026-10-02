"""The four researched request shapes, and the rules a row must satisfy to enter one.

This is the heart of WF-038. The research's data flow names the payload
constraints exactly:

    room engagement rows -> chunked payloads (up to 200, single object type,
    ``attributes.type`` per item, **no ``id`` field**, external-ID field only) ->
    CRM upsert

Every rule below exists because a sentence in the research demands it, and each
carries the quote it comes from. They are enforced in two places, deliberately:

* **Before the request** (:func:`preflight_row`) - a row with no key value, or a
  key that is a record id, or a HubSpot ``email`` key carrying a partial
  property set, is refused and appears in the sync log with its reason. It never
  reaches the wire.
* **While building it** (:func:`build_payload`) - the builder re-checks what it
  produced, so a field map that targets a record id is caught even if it slipped
  past a preflight written by somebody else.

Vendors
-------

``attributes`` (Salesforce)
    ``PATCH /services/data/vXX.X/composite/sobjects/{SobjectName}/
    {ExternalIdFieldName}``. "Each object in the request body must contain an
    attributes map. The map must contain a value for ``type``." and "The list can
    contain objects only of the type indicated in the request URI." The
    ``allOrNone`` parameter rides in the query string, as the research's
    "``allOrNone`` parameter" does.

``id_property`` (HubSpot)
    ``POST /crm/v3/objects/contacts/batch/upsert`` with "the ``idProperty``
    parameter to identify the unique identifier property you're using". The
    researched trap is enforced here: "Partial upserts are not supported when
    using ``email`` as the ``idProperty`` for contacts."

``odata`` (Dataverse)
    ``POST {entityset}/Microsoft.Dynamics.CRM.UpsertMultiple`` with "a
    ``Targets`` collection where each item carries ``@odata.type`` and
    ``@odata.id`` using the alternate key", addressing "an alternate key defined
    using a column named ``sample_keyattribute``".

Single-row forms
----------------

The researched fallback is "auto-falls back from ``UpsertMultiple`` to per-row
``PATCH``", and Salesforce's own second endpoint puts the key **in the path**:
"``PATCH /services/data/vXX.X/sobjects/{sObject}/{fieldName}/{fieldValue}``",
with "an optional ``updateOnly=true``". So in a single-row body the key value is
deliberately absent - it is the address, not a field.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.crm_upsert.capabilities import Capability, is_record_id_field
from dsr.crm_upsert.errors import MixedObjectTypes, UpsertError
from dsr.crm_upsert.transport import (
    DEFAULT_API_VERSION,
    OutboundRequest,
    odata_string,
    path_segment,
)

#: The Salesforce API version placeholder the research writes as ``vXX.X``.
API_VERSION = DEFAULT_API_VERSION

#: Per-item outcome vocabulary, published so a client renders one set of labels.
OUTCOMES: tuple[str, ...] = (
    "created",  # the key was not found and a new record was created
    "updated",  # the key was found and the record was updated
    "submitted",  # sent; the vendor returns no per-item result to confirm it
    "failed",  # the vendor returned an error for this row
    "rejected",  # refused here, before any request was sent
    "rolled_back",  # a sibling row failed under allOrNone, so nothing was written
)

#: Why a row was rejected before the request. Published as data so a client can
#: label them, and so a new reason is a new name rather than a new wording.
REJECTION_REASONS: dict[str, str] = {
    "no_key_value": (
        "the row has no value for the connection's key source field, and an upsert "
        "with no external id has nothing to key on"
    ),
    "no_mapped_fields": ("the row maps to no CRM fields, so there is nothing to write"),
    "partial_upsert_unsupported": (
        "this vendor does not support a partial upsert with this idProperty, so a row "
        "that cannot supply the complete property set is refused rather than sent: "
        "the vendor would treat the missing properties as empty and blank the record"
    ),
    "missing_required_property": (
        "the row does not supply every property this connection declares required, and "
        "a partial upsert is not supported for this idProperty"
    ),
    "record_id_key": (
        "the key field is a record id. Only external ids are supported - a record id is "
        "what an upsert returns, so keying on it cannot create"
    ),
}


class RowRejected(UpsertError):
    """One row cannot be sent. Carries a :data:`REJECTION_REASONS` key.

    Raised per row rather than per run: the researched flow says a run reports
    "per-row outcomes" and that "failures appear in the sync log with the row's
    error text". One unusable row must not stop the other 199.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REJECTION_REASONS:
            raise KeyError(f"unknown rejection reason {reason!r}")
        self.reason = reason
        self.detail = detail or REJECTION_REASONS[reason]
        super().__init__(self.detail)


# --------------------------------------------------------------------------- #
# Reading a row
# --------------------------------------------------------------------------- #


def key_value(row: Mapping[str, Any], key_source: str) -> str:
    """The external-id value for a row, or "" when it has none.

    Read by name from the row's own ``data`` with no fallback chain: the
    connection names the field, so two connections can key on two different
    fields of the same rows without either guessing.
    """
    value = row.get(key_source)
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = str(value).strip()
    return text


def mapped_fields(row: Mapping[str, Any], field_map: Mapping[str, str]) -> dict[str, Any]:
    """Translate a row's own fields into CRM field names.

    Only fields the room actually holds are carried, and a field the map sends to
    a record id is dropped rather than forwarded - see :func:`_assert_no_record_id`.
    A team that adds a field to its engagement rows adds a line to the map and
    nothing else: no migration, no typed column, no code change here.
    """
    payload: dict[str, Any] = {}
    for source, target in dict(field_map).items():
        if target in (None, ""):
            continue
        if is_record_id_field(target):
            # Refused in the builder; dropped here so one bad map line cannot take
            # a whole chunk out. The preflight reports it as record_id_key.
            continue
        if source in row and row[source] is not None:
            payload[str(target)] = row[source]
    return payload


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


def preflight_row(
    row: Mapping[str, Any],
    *,
    key_source: str,
    field_map: Mapping[str, str],
    key_field: str,
    vendor: str,
    partial_upserts_supported: bool = True,
    required_properties: Sequence[str] = (),
) -> str:
    """The key value for a row that may be sent, or raise :class:`RowRejected`.

    This is where the researched refusal rules live, before a single byte goes
    out. The checks, in the order they change what a reader sees:

    1. **The key field is not a record id.** "Only external ids are supported.
       Don't use record ids." Checked on the field *name*, because a map that
       points a room field at ``Id`` looks like an ordinary field until the
       vendor rejects it.
    2. **The row has a key value.** An upsert with no external id is not an
       upsert; it is an anonymous create, which is the other thing the research
       warns against by saying the payload carries the "external-ID field only".
    3. **The row maps to at least one field.** There is nothing to write.
    4. **The property set is complete, when the vendor requires it.** "Partial
       upserts are not supported when using ``email`` as the ``idProperty`` for
       contacts." A row that supplies only the key would be sent as a
       near-empty record; the vendor cannot do a partial write, so the connector
       refuses instead of letting the contact be blanked.
    """
    if is_record_id_field(key_field):
        raise RowRejected("record_id_key")

    value = key_value(row, key_source)
    if not value:
        # The reason's own sentence leads, so the row's stored error text always
        # carries the *rule* as well as the specifics. A log line that said only
        # "no value in 'engagement_id'" would tell a reader what happened without
        # telling them why the connector refused to invent a key.
        raise RowRejected(
            "no_key_value", f"no value in {key_source!r}. " + REJECTION_REASONS["no_key_value"]
        )

    fields = mapped_fields(row, field_map)
    if not fields:
        raise RowRejected(
            "no_mapped_fields",
            f"row {row.get('id', '?')} maps to no CRM field. "
            + REJECTION_REASONS["no_mapped_fields"],
        )

    if not partial_upserts_supported:
        missing = [name for name in required_properties if not fields.get(name)]
        if missing:
            raise RowRejected(
                "missing_required_property",
                f"row {row.get('id', '?')} is missing {missing}. "
                + REJECTION_REASONS["missing_required_property"],
            )
    return value


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #


def _assert_no_record_id(nodes: Sequence[Mapping[str, Any]], key_field: str) -> None:
    """Refuse a request whose *mapped field* names are a CRM record id.

    The research states the payload has "**no ``id`` field**, external-ID field
    only", and separately "**Only external ids are supported. Don't use record
    ids.**" The second sentence is about the *key*; this is about the fields.

    The concrete way it goes wrong here is a feedback loop: the room writes
    ``crm_record_id`` back onto every synced row, so naively mapping the row
    through would echo last run's record id into this run's request and turn the
    next upsert into an update-by-record-id - which cannot create, and which the
    vendor documents as unsupported. A guard that only checked the key field
    would miss it, so the built field maps are checked too.

    ``nodes`` is the *field* containers, not the whole envelope. That distinction
    is load-bearing: HubSpot's input legitimately carries an ``id`` key holding
    the ``idProperty``'s **value** ("include the ``idProperty`` parameter to
    identify the unique identifier property you're using"), and that is the whole
    point of the call rather than a record id. Checking the envelope would refuse
    every HubSpot request, so each payload style passes its own field container.
    """
    for node in nodes:
        for crm_field in _iter_field_names(node):
            if is_record_id_field(crm_field) and crm_field != key_field:
                raise UpsertError(
                    f"the built request carries a {crm_field!r} field, which is a record id. "
                    "The payload must carry the external-id field only - drop it from the "
                    "connection's field map."
                )


def _iter_field_names(node: Any) -> list[str]:
    """Every object key in a request body, at any depth.

    Walks into ``attributes`` and ``properties`` because that is where a mapped
    field can hide, and stops descending into lists of scalars.
    """
    names: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            names.append(str(key))
            names.extend(_iter_field_names(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            names.extend(_iter_field_names(item))
    return names


def _assert_single_type(items: Sequence[Mapping[str, Any]], object_name: str) -> None:
    """ "The list can contain objects only of the type indicated in the request URI."

    A chunk that mixed two object types would be a request the vendor rejects as a
    whole, taking every other row in the chunk down with it. Refusing before the
    request means the diagnosis is one row, not two hundred.
    """
    types = {str(item.get("attributes", {}).get("type") or "") for item in items}
    types.discard("")
    if len(types) > 1:
        raise MixedObjectTypes(
            f"one request would carry {sorted(types)}, but the request URI names a single "
            f"object type ({object_name!r}). Split the rows or use one connection per type."
        )


def salesforce_item(
    fields: Mapping[str, Any], *, object_name: str, key_field: str, key_value_: str
) -> dict[str, Any]:
    """One Salesforce collection item.

    "Each object in the request body must contain an attributes map. The map must
    contain a value for ``type``." and the payload carries the external-ID field
    and "no ``id`` field".
    """
    return {
        "attributes": {"type": object_name},
        key_field: key_value_,
        **{k: v for k, v in fields.items() if k != key_field},
    }


def hubspot_item(fields: Mapping[str, Any], *, key_field: str, key_value_: str) -> dict[str, Any]:
    """One HubSpot batch-upsert input.

    The research names the ``idProperty`` parameter; the ``inputs`` envelope
    around it is a local reading, listed in
    :mod:`dsr.crm_upsert.inferences`. The ``id`` here is the *idProperty's
    value*, not a record id - which is why :func:`_assert_no_record_id` is
    applied to the mapped ``properties`` rather than to the whole envelope.
    """
    return {
        "idProperty": key_field,
        "id": key_value_,
        "properties": dict(fields),
    }


def dataverse_item(
    fields: Mapping[str, Any],
    *,
    entity_set: str,
    logical_name: str,
    key_field: str,
    key_value_: str,
) -> dict[str, Any]:
    """One Dataverse ``Targets`` item.

    "You must specify the ``@odata.type`` annotation with every item in the
    ``Targets`` parameter" and "The ``@odata.id`` annotation identifies the record
    with a relative URL" using "an alternate key defined using a column named
    ``sample_keyattribute``".

    The ``@odata.type`` is required on *every* item, so it is set per item here
    rather than once for the collection.
    """
    return {
        "@odata.type": f"#Microsoft.Dynamics.CRM.{logical_name}",
        "@odata.id": f"{entity_set}({key_field}='{odata_string(key_value_)}')",
        **{k: v for k, v in fields.items() if k not in ("@odata.type", "@odata.id")},
    }


def build_payload(
    capability: Capability,
    *,
    object_name: str,
    key_field: str,
    entries: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build one bulk request body from ``(key value, mapped fields)`` pairs.

    The order of ``entries`` is preserved exactly, because the researched
    guarantee is positional: "Objects are created or updated in the order they're
    listed in the request body. The ``UpsertResult`` objects are returned in the
    same order." That is the only thing tying a result back to a room row, so the
    builder must not sort, group, or deduplicate.
    """
    style = capability.payload_style
    if style == "attributes":
        items = [
            salesforce_item(fields, object_name=object_name, key_field=key_field, key_value_=value)
            for value, fields in entries
        ]
        _assert_single_type(items, object_name)
        # Every key in an item is a CRM field name, so the items are the fields.
        _assert_no_record_id(items, key_field)
        body: dict[str, Any] = {"records": items}
    elif style == "id_property":
        items = [
            hubspot_item(fields, key_field=key_field, key_value_=value) for value, fields in entries
        ]
        # Only `properties` holds mapped fields; the envelope's own `id` is the
        # idProperty's value, which is the researched point of the call.
        _assert_no_record_id([item["properties"] for item in items], key_field)
        body = {"inputs": items}
    elif style == "odata":
        logical = _logical_name(object_name)
        items = [
            dataverse_item(
                fields,
                entity_set=object_name,
                logical_name=logical,
                key_field=key_field,
                key_value_=value,
            )
            for value, fields in entries
        ]
        _assert_no_record_id(items, key_field)
        body = {"Targets": items}
    else:
        raise UpsertError(f"unknown payload style {style!r} for {capability.vendor}")

    return body


def _logical_name(object_name: str) -> str:
    """The Dataverse logical type name for an entity set.

    ``Contacts`` -> ``Contact``: Dataverse's ``@odata.type`` carries the singular
    logical name, while the ``@odata.id`` segment uses the plural entity set. A
    wrong value here is a 400 on every row of the chunk, so a plural that is
    already singular is passed through rather than mangled.
    """
    name = str(object_name)
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def build_bulk_request(
    capability: Capability,
    *,
    object_name: str,
    key_field: str,
    entries: Sequence[tuple[str, Mapping[str, Any]]],
    all_or_none: bool,
    api_version: str = API_VERSION,
    chunk_index: int = 0,
    row_indexes: Sequence[int] = (),
) -> OutboundRequest:
    """One chunk's outbound request, exactly as the research describes it.

    ``allOrNone`` goes in the query string because the research calls it a
    parameter of the request URI: "You can choose whether to roll back the entire
    request when an error occurs", and the endpoint is
    "``.../{SobjectName}/{ExternalIdFieldName}``" with "an ``allOrNone``
    parameter". The body therefore carries no policy flag - a flag in the body
    would be a guess about where a vendor reads it.

    The verb is the vendor's own, from the researched endpoints: Salesforce's
    sObject Collections upsert is a ``PATCH``, while HubSpot's batch upsert and
    Dataverse's ``UpsertMultiple`` are both a ``POST``. Sending Dataverse's
    documented POST as a PATCH would be a 405 from a real CRM, so the verb is
    chosen per payload style rather than assumed from Salesforce alone.
    """
    body = build_payload(capability, object_name=object_name, key_field=key_field, entries=entries)
    path = _fill(
        capability.bulk_path,
        object=object_name,
        # Dataverse's template names the collection `{entityset}` where the other
        # two name it `{object}`. Both are supplied so either template resolves:
        # an unsubstituted placeholder left in a path is a 404 that looks like a
        # missing table rather than a template bug.
        entityset=object_name,
        key_field=key_field,
        api_version=api_version,
    )
    method = "PATCH" if capability.payload_style == "attributes" else "POST"
    query: dict[str, str] = {}
    if capability.supports_all_or_none:
        query["allOrNone"] = "true" if all_or_none else "false"
    return OutboundRequest(
        method=method,
        path=path,
        body=body,
        query=query,
        chunk_index=chunk_index,
        row_indexes=tuple(row_indexes),
    )


def build_single_request(
    capability: Capability,
    *,
    object_name: str,
    key_field: str,
    key_value_: str,
    fields: Mapping[str, Any],
    update_only: bool,
    api_version: str = API_VERSION,
    chunk_index: int = 0,
    row_indexes: Sequence[int] = (),
) -> OutboundRequest:
    """One row's outbound request, for the researched per-row ``PATCH`` fallback.

    "PATCH /services/data/vXX.X/sobjects/{sObject}/{fieldName}/{fieldValue}" -
    the key value is the **address**, so it is percent-encoded into the path and
    deliberately absent from the body. "To prevent a new record from being
    created, use the ``updateOnly`` parameter", so it is a query parameter when
    the vendor supports it and omitted when the vendor does not, rather than
    sent to a vendor that would ignore it.
    """
    if capability.payload_style == "attributes":
        body: dict[str, Any] = salesforce_item(
            fields, object_name=object_name, key_field=key_field, key_value_=key_value_
        )
        # The key is the address. Leaving it in the body as well would be the
        # "external-ID field only" rule satisfied twice over and would put an
        # external id in a field the vendor is about to treat as a payload value.
        body.pop(key_field, None)
        _assert_no_record_id([body], key_field)
    elif capability.payload_style == "id_property":
        body = {"properties": dict(fields)}
        _assert_no_record_id([body["properties"]], key_field)
    elif capability.payload_style == "odata":
        item = dataverse_item(
            fields,
            entity_set=object_name,
            logical_name=_logical_name(object_name),
            key_field=key_field,
            key_value_=key_value_,
        )
        item.pop("@odata.id", None)
        body = item
        _assert_no_record_id([body], key_field)
    else:
        raise UpsertError(
            f"unknown payload style {capability.payload_style!r} for {capability.vendor}"
        )

    path = _fill(
        capability.single_path,
        object=object_name,
        entityset=object_name,
        key_field=key_field,
        key_value=key_value_,
        api_version=api_version,
    )

    query: dict[str, str] = {}
    if capability.supports_update_only and update_only:
        query["updateOnly"] = "true"
    return OutboundRequest(
        method="PATCH",
        path=path,
        body=body,
        query=query,
        chunk_index=chunk_index,
        row_indexes=tuple(row_indexes),
    )


def _fill(template: str, **values: Any) -> str:
    """Substitute ``{name}`` placeholders, percent-encoding each value exactly once.

    Every substituted value goes through :func:`path_segment` except
    ``api_version``, which is a path component like any other but is set by the
    connection rather than by row data. Encoding the *key value* is what stops a
    key containing a slash from addressing a different record.

    Callers pass **raw** values. Encoding here and again at the call site would
    turn a key containing a slash into ``%252F``, which the vendor would read as a
    literal ``%2F`` in the key - a mismatch that looks exactly like "no such
    record" and is far harder to trace than the bug it replaced.
    """
    out = template
    for name, value in values.items():
        encoded = value if name == "api_version" else path_segment(value)
        out = out.replace("{" + name + "}", str(encoded))
    return out
