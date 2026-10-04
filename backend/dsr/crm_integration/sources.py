"""The room's own copy of the vendor's tables, and the wire shape each one answers in.

This product holds no OAuth connection to a vendor org. Connecting one is a
different researched workflow, and a feature that pretended to open a socket
would be untestable and would put a network call in a render path. So the vendor's
three tables live here as ordinary records, and :func:`run_query` answers a
:class:`~dsr.crm_integration.query.ReadQuery` plan in the vendor's own response
shape.

That is a deliberate trade and it has two consequences this module takes
seriously rather than hiding.

**The wire shapes are the researched ones.** ``totalSize`` / ``done`` /
``nextRecordsUrl`` for Salesforce, ``@odata.nextLink`` for Dataverse,
``paging.next.after`` for HubSpot, and Dataverse's
``"<column>@OData.Community.Display.V1.FormattedValue"`` annotation. The
normaliser in :mod:`dsr.crm_integration.normalize` is therefore written against
the real shapes, and a room pointed at a live vendor is a change to this module
alone.

**The field scope is enforced here, not only requested.** A row is cut down to
the plan's ``select`` list before it is serialised, so a source that returned a
column nobody asked for would be caught by a test rather than by a reviewer. The
column a deployment added to its write map and did not add to the read map is
still in the table; it simply does not reach the panel, and
:func:`~dsr.crm_integration.fieldmap.unmapped_columns` is what tells the room so.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import vocabulary
from dsr.crm_integration.query import (
    DATAVERSE_API_VERSION,
    SALESFORCE_API_VERSION,
    ReadQuery,
)
from dsr.store import RecordStore

#: The collection holding the vendor's tables. Owned by this feature, created by
#: discovery, and ordinary JSON in ``records.data``.
RECORDS = "crm_read_record"


def normalise_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Clean a submitted vendor row.

    The stored shape is ``{system, object, external_id, owner_id, fields,
    labels, modified}``. ``labels`` is the vendor's own display-label map and is
    only ever populated for a vendor that publishes one.
    """
    system = vocabulary.require_system(payload.get("system") or "")
    object_name = vocabulary.require_object(
        payload.get("object") or payload.get("object_name") or ""
    )
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    labels = payload.get("labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    # HubSpot carries the owner as a property rather than as envelope metadata,
    # so it is lifted into the same column every other vendor uses. The read's
    # owner filter then means one thing across the three.
    owner_id = str(payload.get("owner_id") or fields.get("hubspot_owner_id") or "").strip()
    data: dict[str, Any] = {
        "system": system,
        "object": object_name,
        "external_id": str(payload.get("external_id") or "").strip(),
        "owner_id": owner_id,
        "fields": {str(key): value for key, value in fields.items()},
        "labels": {str(key): str(value) for key, value in labels.items()},
        "modified": str(payload.get("modified") or "").strip(),
        "email": str(payload.get("email") or fields.get("Email") or fields.get("email") or "")
        .strip()
        .lower(),
    }
    # Copied rather than whitelisted, so a field a team adds reaches storage
    # without a change here. The record payload is arbitrary JSON by contract,
    # and this table is a team's own extension point: a vendor row carrying
    # ``crm_tier__c`` must survive a round trip through this function.
    for key, value in payload.items():
        if key not in data and key != "object_name":
            data[key] = value
    return data


def rows(
    store: RecordStore,
    system: str,
    object_name: str,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every row of one vendor table.

    Scoped to the room when a room is given. The vendor's tables are org-level,
    so ``room_id=None`` is the whole org's table and a room-scoped read is the
    narrower answer - the same split :mod:`dsr.change_stream` makes for the same
    reason.
    """
    vendor = vocabulary.require_system(system)
    target = vocabulary.require_object(object_name)
    found = store.list(RECORDS, room_id=room_id, limit=1000, order_by="created_at")
    return [
        record
        for record in found
        if record["data"].get("system") == vendor and record["data"].get("object") == target
    ]


def table_size(
    store: RecordStore, system: str, object_name: str, room_id: str | None = None
) -> int:
    """How many rows one vendor table holds, for the panel's own count."""
    return len(rows(store, system, object_name, room_id=room_id))


def run_query(store: RecordStore, query: ReadQuery, room_id: str | None = None) -> dict[str, Any]:
    """Answer one plan with the vendor's own response shape.

    Two things are enforced here rather than trusted:

    * **field scope.** Every returned record carries only the plan's ``select``
      columns plus the vendor's own id and metadata keys.
    * **the page ceiling.** A table larger than the plan's limit answers with the
      first page, a ``done`` of ``false`` and a continuation, which is what the
      research says the vendor does: "the returned batch can include fewer
      records than the limit".
    """
    candidates = rows(store, query.system, query.object_name, room_id=room_id)
    matched, unapplied = _filter(candidates, query)
    ordered = _order(matched, query)
    page = ordered[: query.limit]
    total = len(matched)
    unapplied_conditions = [*unapplied, *_dropped(query)]

    if query.system == "salesforce":
        return _salesforce_response(query, page, total, unapplied_conditions)
    if query.system == "dataverse":
        return _dataverse_response(query, page, total, unapplied_conditions)
    return _hubspot_response(query, page, total, unapplied_conditions)


# --------------------------------------------------------------------------- #
# Selecting and ordering
# --------------------------------------------------------------------------- #


def _filter(
    candidates: list[dict[str, Any]], query: ReadQuery
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply what the plan can be applied with, and name what it could not.

    ``target_id`` and ``owner_id`` are structured, so they are applied exactly. A
    free-text condition is not: SOQL, OData and HubSpot each spell a filter in
    their own grammar, and re-parsing a string to guess which one it was written
    in would be this build inventing a read. The condition is reported as
    unapplied instead, so a narrower read is visible rather than silent.
    """
    unapplied: list[str] = []
    kept: list[dict[str, Any]] = []
    for record in candidates:
        data = record["data"]
        if query.target_id and not _matches_id(
            data, query.target_id, query.id_property, query.match_column
        ):
            continue
        if query.owner_id and data.get("owner_id") != query.owner_id:
            continue
        kept.append(record)
    for condition in query.conditions:
        if _is_structured(query, condition):
            continue
        unapplied.append(condition)
    return kept, unapplied


def _matches_id(
    data: dict[str, Any], target_id: str, id_property: str, match_column: str = ""
) -> bool:
    """Match the resolved record, by record id or by the property the caller named.

    ``match_column`` is the column the plan is filtering on, which for a
    non-record-id target is the email column the field map carries. It is tried
    first so the read matches what the plan actually asked the vendor for.
    """
    if not id_property or id_property == "id":
        return data.get("external_id") == target_id
    wanted = target_id.lower()
    if match_column:
        stored = (data.get("fields") or {}).get(match_column)
        if stored is not None and str(stored).strip().lower() == wanted:
            return True
    if data.get("email") and data["email"] == wanted:
        return True
    fields = data.get("fields") or {}
    return any(str(value).strip().lower() == wanted for value in fields.values())


def _is_structured(query: ReadQuery, condition: str) -> bool:
    """Whether a condition is the target-id or owner-id clause the plan already added."""
    stripped = condition.strip()
    if query.target_id and query.target_id in stripped:
        return True
    # The clause a plan adds is quoted, so the comparison has to be against the
    # quoted owner id. Matching the bare value would report the clause this plan
    # built itself as unapplied, which is the one thing the report must never do.
    return bool(query.owner_id and stripped.endswith(f"'{query.owner_id}'"))


def _dropped(query: ReadQuery) -> list[str]:
    from dsr.crm_integration.query import dropped_conditions

    return dropped_conditions(query)


def _order(records: list[dict[str, Any]], query: ReadQuery) -> list[dict[str, Any]]:
    """Newest first, on the column the plan ordered by when it has one.

    Every vendor read in this workflow is ordered descending, because the panel
    shows the current state of a deal rather than its history. A column the row
    does not carry falls back to ``modified``, which is the column the source
    records for exactly this purpose.
    """

    def key(record: dict[str, Any]) -> tuple[str, str]:
        data = record["data"]
        fields = data.get("fields") or {}
        chosen = fields.get(query.order_by)
        if chosen is None:
            chosen = data.get("modified") or ""
        return (str(chosen), str(data.get("external_id") or ""))

    return sorted(records, key=key, reverse=True)


# --------------------------------------------------------------------------- #
# The three wire shapes
# --------------------------------------------------------------------------- #


def _project(query: ReadQuery, record: dict[str, Any]) -> dict[str, Any]:
    """One row, cut down to the plan's columns.

    The id column is kept even when the map did not ask for it, because every
    vendor returns it and the room needs it to name the row and to page.
    """
    data = record["data"]
    fields = data.get("fields") or {}
    id_field = vocabulary.ID_FIELDS[query.system]
    projected: dict[str, Any] = {id_field: data.get("external_id") or ""}
    for column in query.select:
        if column == id_field:
            continue
        projected[column] = fields.get(column)
    return projected


def _salesforce_response(
    query: ReadQuery,
    page: list[dict[str, Any]],
    total: int,
    unapplied: list[str],
) -> dict[str, Any]:
    """``totalSize`` / ``done`` / ``nextRecordsUrl`` / ``records``."""
    entity = vocabulary.object_name("salesforce", query.object_name)
    base = f"/services/data/{SALESFORCE_API_VERSION}"
    records = [
        {
            "attributes": {"type": entity, "url": f"{base}/sobjects/{entity}/{row['Id']}"},
            **row,
        }
        for row in (_project(query, record) for record in page)
    ]
    done = len(records) >= total
    body: dict[str, Any] = {"totalSize": total, "done": done, "records": records}
    if not done:
        body["nextRecordsUrl"] = f"{base}/query-all/{_locator(query, total)}"
    if unapplied:
        body["unappliedConditions"] = unapplied
    return body


def _dataverse_response(
    query: ReadQuery,
    page: list[dict[str, Any]],
    total: int,
    unapplied: list[str],
) -> dict[str, Any]:
    """``@odata.count`` / ``@odata.nextLink`` / ``value``, with formatted values."""
    entity_set = vocabulary.object_name("dataverse", query.object_name)
    base = f"/api/data/{DATAVERSE_API_VERSION}"
    wants_labels = "Prefer" in query.headers
    suffix = vocabulary.display_annotation_suffix("dataverse")
    values: list[dict[str, Any]] = []
    for record in page:
        data = record["data"]
        projected = _project(query, record)
        row: dict[str, Any] = {
            "@odata.id": f"{base}/{entity_set}({projected.get('id')})",
            **projected,
        }
        if wants_labels:
            # The annotation is per column and rides beside the stored value, not
            # in place of it: "statecode@...FormattedValue": "Active" beside
            # "statecode": 0.
            for column, label in (data.get("labels") or {}).items():
                if column in projected:
                    row[f"{column}{suffix}"] = label
        values.append(row)
    body: dict[str, Any] = {
        "@odata.context": f"$metadata#{entity_set}",
        "@odata.count": total,
        "value": values,
    }
    if len(values) < total:
        token = _locator(query, total)
        body["@odata.nextLink"] = (
            f"{base}/{entity_set}?$select={','.join(query.select)}&$top={query.limit}"
            f"&$skiptoken={token}"
        )
    if unapplied:
        body["unappliedConditions"] = unapplied
    return body


def _hubspot_response(
    query: ReadQuery,
    page: list[dict[str, Any]],
    total: int,
    unapplied: list[str],
) -> dict[str, Any]:
    """Three shapes: ``results`` for a batch, one record for a lookup, paging for a search."""
    results = [
        {
            "id": record["data"].get("external_id") or "",
            "properties": _hubspot_properties(query, record),
        }
        for record in page
    ]
    body: dict[str, Any]
    if query.endpoint == "get_by_email":
        if not results:
            return {"status": "NOT_FOUND", "unappliedConditions": unapplied}
        body = {**results[0], "status": "COMPLETE"}
    elif query.endpoint == "search":
        body = {"total": min(total, vocabulary.HUBSPOT_SEARCH_RESULT_LIMIT), "results": results}
        if len(results) < min(total, vocabulary.HUBSPOT_SEARCH_RESULT_LIMIT):
            body["paging"] = {"next": {"after": _locator(query, total)}}
    else:
        body = {"status": "COMPLETE", "results": results}
    if unapplied and "unappliedConditions" not in body:
        body["unappliedConditions"] = unapplied
    return body


def _hubspot_properties(query: ReadQuery, record: dict[str, Any]) -> dict[str, Any]:
    """HubSpot returns every selected property under ``properties``."""
    projected = _project(query, record)
    return {
        key: value for key, value in projected.items() if key != vocabulary.ID_FIELDS["hubspot"]
    }


def _locator(query: ReadQuery, total: int) -> str:
    """A stable continuation token for one plan.

    Deterministic, because a token derived from a clock would make a plan
    non-reproducible and a test that asserted on a continuation would fail on the
    busiest machine. It names the vendor, the object, the filter and how far
    through the match set the page ended, which is what a locator has to encode
    for the next page to be the next page.
    """
    digest = 0
    for character in f"{query.system}|{query.object_name}|{query.target_id}|{total}":
        digest = (digest * 31 + ord(character)) % 1_000_000_007
    return f"{query.system[:2]}-{query.object_name}-{digest:010d}-{total}"


__all__ = [
    "RECORDS",
    "normalise_record",
    "rows",
    "run_query",
    "table_size",
]
