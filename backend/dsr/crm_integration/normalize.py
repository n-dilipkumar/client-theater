"""Normalising one vendor response into the room's view model.

The data flow names the step: "paged results with ``totalSize`` / ``done`` /
``nextRecordsUrl`` (or ``@odata.nextLink`` / ``paging.next.after``) -> normalise
into the room's view model -> cache + render."

Three vendors, three continuation shapes, one answer. That is what this module
is for, and nothing else in the package needs to know a vendor's spelling.

The records come out in the room's field names, not the vendor's columns, because
a panel that renders ``StageName`` on one vendor and ``dealstage`` on another is
two panels. Which room field each column carries comes from the field map, so a
deployment that added a column to its write map gets it in the view model with no
change here.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_integration import fieldmap, vocabulary

#: The key suffix Dataverse puts on a column it has annotated.
_SUFFIX = f"@{vocabulary.DATAVERSE_DISPLAY_ANNOTATION}"


def normalise_response(
    system: str,
    object_name: str,
    response: dict[str, Any],
    field_map: dict[str, dict[str, str]],
    *,
    requested_limit: int = 0,
) -> dict[str, Any]:
    """Turn one vendor response into the room's view model for one object.

    ``requested_limit`` is the page the room asked for. It is needed because
    Dataverse and HubSpot's search report no ``done``: with no continuation
    there is nothing to say, and with one there is, so the limit is the only way
    to tell "this page is the whole table" from "this page is the whole table
    because the table is exactly this big" - and the second reading is the one
    that hides a missed continuation.
    """
    vendor = vocabulary.require_system(system)
    target = vocabulary.require_object(object_name)
    mode = vocabulary.paging_mode(vendor)
    fields = vocabulary.PAGING_FIELDS[mode]

    raw_records = _raw_records(mode, response)
    total = _as_int(response.get(fields["total"]), len(raw_records))
    done, cursor = _completion(mode, response, raw_records, total, requested_limit)

    mapping = (field_map or {}).get(target) or {}
    records = [_normalise_record(row, mapping, vendor) for row in raw_records]
    limit = vocabulary.RESULT_LIMITS[vendor]
    id_field = vocabulary.ID_FIELDS[vendor]
    # The id column and each vendor's own metadata envelope are not columns the
    # room failed to map. They are how every vendor names a row, and listing them
    # as unmapped would report a mapping gap on every single read.
    vendor_metadata = {id_field, "attributes"}
    returned_columns = sorted(
        {
            key
            for row in raw_records
            for key in row
            if not key.endswith(_SUFFIX) and key not in vendor_metadata
        }
    )

    return {
        "system": vendor,
        "object": target,
        "paging_mode": mode,
        "total": min(total, limit),
        "total_reported": total,
        "returned": len(records),
        "done": done,
        "next": {"mode": mode, "cursor": cursor} if cursor else None,
        "stalled": _stalled(done, cursor, raw_records, total),
        "read_set": list(mapping),
        "unmapped_returned": fieldmap.unmapped_columns(field_map, target, returned_columns),
        "records": records,
        "truncated_at_vendor_limit": total > limit,
        "unapplied_conditions": list(response.get("unappliedConditions") or []),
        "vendor_paging_fields": {key: response.get(value) for key, value in fields.items()},
    }


def _raw_records(mode: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    """The vendor's records, whichever field it puts them under.

    HubSpot returns each record's fields under a ``properties`` object rather
    than beside its id, so that envelope is lifted here rather than in every
    function below. Doing it once is what keeps "the same field names reach the
    panel whichever vendor spelled the envelope" true by construction.
    """
    if mode == "query_locator":
        return [row for row in (response.get("records") or []) if isinstance(row, dict)]
    if mode == "odata_next_link":
        return [row for row in (response.get("value") or []) if isinstance(row, dict)]
    lifted: list[dict[str, Any]] = []
    if isinstance(response.get("results"), list):
        for row in response["results"]:
            if not isinstance(row, dict):
                continue
            properties = row.get("properties")
            lifted.append(
                {**row, **(properties or {})} if isinstance(properties, dict) else dict(row)
            )
        return lifted
    # `GET /crm/v3/objects/contacts/{email}` answers with the one record itself,
    # not with a `results` array, so a response that names a record is that one
    # record. Reading `results` unconditionally would make the single-record
    # lookup look like a search that matched nothing.
    if response.get("id") is not None or isinstance(response.get("properties"), dict):
        properties = response.get("properties")
        return [
            {**response, **(properties or {})}
            if isinstance(properties, dict)
            else {key: value for key, value in response.items() if key != "status"}
        ]
    return lifted


def _completion(
    mode: str,
    response: dict[str, Any],
    records: list[dict[str, Any]],
    total: int,
    requested_limit: int,
) -> tuple[bool, str]:
    """Whether the read is finished, and the cursor that continues it.

    Salesforce states it. The other two do not, so the room derives it from the
    two things they do publish: a page shorter than the page that was asked for
    is the last page, and a page that reached the total is the last page
    whichever length it is. That is an inference, and
    :data:`dsr.crm_integration.inferences.DERIVED_DONE` records it as one.

    A vendor that returns a full page, reports a larger total, and gives no
    continuation is reported as unfinished with no cursor. That is the one state
    this cannot resolve, so it is surfaced rather than rounded to either answer -
    and :func:`_stalled` marks it so a caller can tell it from a read that is
    genuinely mid-pagination.
    """
    if mode == "query_locator":
        done = bool(response.get("done"))
        cursor = "" if done else _locator_from(str(response.get("nextRecordsUrl") or ""))
        return done, cursor
    cursor = _cursor_for(mode, response)
    if cursor:
        return False, cursor
    if requested_limit and len(records) < requested_limit:
        return True, ""
    return len(records) >= total, ""


def _cursor_for(mode: str, response: dict[str, Any]) -> str:
    """The continuation this vendor publishes, if it published one."""
    if mode == "odata_next_link":
        return str(response.get("@odata.nextLink") or "")
    after = str((((response.get("paging") or {}).get("next")) or {}).get("after") or "")
    return after


def _stalled(done: bool, cursor: str, records: list[dict[str, Any]], total: int) -> bool:
    """Whether the vendor owes the room more rows and offered no way to ask."""
    return not done and not cursor and len(records) < total


def _locator_from(url: str) -> str:
    """The query locator inside a ``nextRecordsUrl``.

    "the response contains the first batch of records, a ``false`` value for
    ``done``, and a query locator. You can use the query locator with the Query
    More Results resource." So the cursor the room holds is the locator, not the
    whole URL: a URL carries the org's own host, and a snapshot cached under a
    URL from one org would send the next page to it.
    """
    return url.rstrip("/").rsplit("/", 1)[-1] if url else ""


def _normalise_record(
    row: dict[str, Any],
    mapping: dict[str, str],
    system: str,
) -> dict[str, Any]:
    """One vendor record, in the room's field names, with its labels beside them.

    A label the vendor returned is kept as a label and the stored option value is
    kept too. A panel that shows only "Proposal sent" cannot show a buyer what
    changed, and a panel that shows only ``2`` is the failure the research's step
    five exists to prevent.
    """
    fields: dict[str, Any] = {}
    labels: dict[str, str] = {}
    external_id = ""
    id_field = vocabulary.ID_FIELDS[system]
    for key, value in row.items():
        if key == id_field:
            external_id = str(value or "")
            continue
        if key.startswith("@") or key == "attributes":
            continue
        if key.endswith(_SUFFIX):
            column = key[: -len(_SUFFIX)]
            room_field = mapping.get(column)
            if room_field:
                labels[room_field] = str(value or "")
            continue
        room_field = mapping.get(key)
        if room_field:
            fields[room_field] = value
    return {
        "external_id": external_id,
        "fields": fields,
        "labels": labels,
        "unmapped": sorted(
            key
            for key in row
            if key not in mapping
            and not key.startswith("@")
            and key not in {id_field, "attributes"}
        ),
    }


def merge_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the pages of one read into a single view model.

    The first page decides ``total`` and the cursor, because both describe the
    read rather than the page: a second page reporting its own total of two
    would claim the table shrank. Records accumulate in arrival order, and a
    row read twice keeps its first appearance, because the vendor orders the read
    and the panel shows the first page it was given.
    """
    if not pages:
        return {
            "total": 0,
            "returned": 0,
            "done": True,
            "next": None,
            "stalled": False,
            "records": [],
            "pages": 0,
            "truncated_at_vendor_limit": False,
            "unapplied_conditions": [],
        }
    first = pages[0]
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    unapplied: list[str] = []
    for page in pages:
        for record in page.get("records") or []:
            key = record.get("external_id") or f"row-{len(records)}"
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
        for condition in page.get("unapplied_conditions") or []:
            if condition not in unapplied:
                unapplied.append(condition)
    # The **last** page decides whether the read finished, not the first: a read
    # whose first page is unfinished and whose second is the last one is a
    # finished read, and averaging the two or taking the first would leave the
    # panel reporting a continuation that no longer exists.
    final = pages[-1]
    done = bool(final.get("done", True))
    return {
        "total": first.get("total", 0),
        "total_reported": first.get("total_reported", 0),
        "done": done,
        "next": None if done else (final.get("next") or first.get("next")),
        "stalled": any(page.get("stalled") for page in pages),
        "records": records,
        "pages": len(pages),
        "truncated_at_vendor_limit": any(page.get("truncated_at_vendor_limit") for page in pages),
        "unapplied_conditions": unapplied,
    }


def deal_panel(
    system: str,
    identity_data: dict[str, Any],
    per_object: dict[str, dict[str, Any]],
    field_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Assemble the panel the room renders: deal, contact and account.

    Every part is optional. "the room still works when a seller authors it
    without CRM context" means the panel has to render with a deal and no
    contact, with a contact and no account, and with none of them - so each part
    reports whether it found a row rather than being omitted when it did not.

    The five fields the user flow names - deal name, stage, amount, primary
    contact, account industry - are read out of the mapped rows by their room
    field names, so a vendor that spells them differently still lands in the same
    five slots.
    """
    vendor = vocabulary.require_system(system)
    deal = _first(per_object.get("deal"))
    contact = _first(per_object.get("contact"))
    account = _first(per_object.get("account"))

    def value(part: dict[str, Any] | None, field_name: str) -> Any:
        return ((part or {}).get("fields") or {}).get(field_name)

    def label(part: dict[str, Any] | None, field_name: str) -> str:
        return str(((part or {}).get("labels") or {}).get(field_name) or "")

    return {
        "system": vendor,
        "buyer": {
            "email": identity_data.get("buyer_email"),
            "name": identity_data.get("buyer_name"),
        },
        "resolved_ids": {
            "deal": identity_data.get("deal_id"),
            "contact": identity_data.get("contact_id"),
            "account": identity_data.get("account_id"),
        },
        "deal": {
            "found": deal is not None,
            "name": value(deal, "deal_name"),
            "stage": {"value": value(deal, "stage"), "label": label(deal, "stage")},
            "amount": value(deal, "amount"),
            "external_id": (deal or {}).get("external_id"),
        },
        "contact": {
            "found": contact is not None,
            "name": value(contact, "contact_name"),
            "title": value(contact, "contact_title"),
            "email": value(contact, "contact_email") or identity_data.get("buyer_email"),
            "external_id": (contact or {}).get("external_id"),
        },
        "account": {
            "found": account is not None,
            "name": value(account, "account_name"),
            "industry": {
                "value": value(account, "account_industry"),
                "label": label(account, "account_industry"),
            },
            "external_id": (account or {}).get("external_id"),
        },
        "read_set": fieldmap.panel_fields(field_map),
        "objects_read": sorted(per_object),
        "unmapped_returned": {
            name: (page.get("unmapped_returned") or []) for name, page in sorted(per_object.items())
        },
    }


def _first(page: dict[str, Any] | None) -> dict[str, Any] | None:
    """The first record of one object's page, or ``None``."""
    if not page:
        return None
    records = page.get("records") or []
    return records[0] if records else None


__all__ = ["deal_panel", "merge_pages", "normalise_response"]


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
