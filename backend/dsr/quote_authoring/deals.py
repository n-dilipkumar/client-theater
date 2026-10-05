"""Reading the deal and the catalogue a quote is built from.

Three reads, and every one of them is optional:

* :func:`find_deal` locates a deal across the collections this product has used
  for one.
* :func:`deal_line_items` reads the line items that belong to a deal, whether
  they are held in a collection or embedded in the deal record.
* :func:`find_product` looks a product up in the catalogue, and
  :func:`catalogue_available` reports whether there is one at all.

The last two matter because WF-087 has not shipped. A quote created before the
catalogue exists must still be creatable, from the unit prices the deal already
carried, and the response must say the catalogue was absent rather than the
write failing. A workflow that hard-depends on a sibling that has not merged is
a workflow with an empty page.

Synonyms, not a schema
----------------------

A deal is whatever the CRM mirror stored. Every field read here is located by a
list of candidate names, because ``amount`` and ``deal_amount`` and
``hs_amount`` are all in use across the workflows that write a deal, and this
feature must read whichever one it finds. An absent field reads as its default
rather than raising: a quote from a sparse deal is a real quote.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.quote_authoring.pricing import as_number, as_text
from dsr.quote_authoring.vocabulary import (
    DEAL_COLLECTION_ALIASES,
    DEAL_LINE_ITEM_COLLECTION_ALIASES,
    PRODUCT_COLLECTION_ALIASES,
)
from dsr.store import RecordStore

#: Candidate names for each concept on a deal, most specific first. The first
#: one present on the record wins.
DEAL_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("name", "deal_name", "title", "hs_title"),
    "account": ("account", "account_name", "company", "potential_customer", "customer"),
    "owner": ("owner", "owner_name", "hubspot_owner_id", "deal_owner"),
    "currency": ("currency", "deal_currency", "currency_code"),
    "amount": ("amount", "deal_amount", "value", "hs_amount"),
    "stage": ("stage", "deal_stage", "hs_deal_stage"),
    "address": ("address", "billing_address", "shipping_address"),
}

#: The same idea on a line item, for a deal line and a quote line alike. The two
#: record shapes are deliberately read by the same list, because a quote line is
#: a clone of a deal line and a field that moves between them is a field a clone
#: would silently drop.
LINE_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("name", "title", "product_name", "hs_name"),
    "sku": ("sku", "hs_sku", "product_sku"),
    "description": ("description", "hs_description", "notes"),
    "quantity": ("quantity", "qty", "hs_line_item_quantity"),
    "unit_price": ("unit_price", "price", "unit_cost", "hs_price"),
    "discount_type": ("discount_type", "unit_discount_type"),
    "discount_value": ("discount_value", "unit_discount", "discount"),
    "tax_rate": ("tax_rate", "tax", "hs_tax_rate"),
    "billing_start": ("billing_start", "billing_start_date", "start_date"),
    "product_id": ("product_id", "product", "catalog_product_id"),
}


def pick(record: Mapping[str, Any], concept: str, default: Any = None) -> Any:
    """The first synonym of ``concept`` present on a record's data."""
    for name in DEAL_FIELDS.get(concept, LINE_FIELDS.get(concept, (concept,))):
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def _first_collection(store: RecordStore, names: tuple[str, ...]) -> str:
    """The first candidate collection that holds a live record.

    Chosen by what exists rather than by declaration, so a workflow that mirrors
    a deal as ``crm_opportunity`` is read without this file naming it as the
    primary. Returns an empty string when none holds anything, and the caller
    treats that as the honest "there is no deal yet" answer.

    ``limit=1`` because only the answer to "does this collection hold anything"
    matters here. The alternative, counting rows, is the same question asked of
    every candidate on every request for a quantity this product does not need.
    """
    for name in names:
        if store.list(name, limit=1):
            return name
    return ""


# --------------------------------------------------------------------------- #
# The deal
# --------------------------------------------------------------------------- #


def catalogue_available(store: RecordStore) -> bool:
    """Is there a product library to resolve a tier against?"""
    return bool(_first_collection(store, PRODUCT_COLLECTION_ALIASES))


def find_deal(store: RecordStore, reference: str) -> tuple[dict[str, Any], str] | None:
    """Locate a deal by record id, then by the CRM's own id field.

    Two passes because a caller holding a CRM id is the common case and this
    product's own record id is not the CRM's. Returns the record and the
    collection it came from, or ``None``.
    """
    reference = as_text(reference)
    if not reference:
        return None

    names = DEAL_COLLECTION_ALIASES
    collection = _first_collection(store, names) or DEAL_COLLECTION_ALIASES[0]
    record = store.get(reference)
    if record is not None and record["collection"] in names:
        return record, record["collection"]

    for name in names:
        rows = store.find(name, {"deal_id": reference}, limit=1)
        if rows:
            return rows[0], name
    for name in names:
        for field in ("crm_id", "external_id", "source_id", "opportunity_id"):
            rows = store.find(name, {field: reference}, limit=1)
            if rows:
                return rows[0], name
    # The collection may hold rows but this reference may name none of them. The
    # final pass reads the named collection directly so a store whose mirror used
    # no id field at all still resolves.
    rows = store.list(collection, limit=1000)
    for row in rows:
        if reference in {as_text(row["data"].get("id")), as_text(row["id"])}:
            return row, collection
    return None


def read_deal(record: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a deal this workflow reads, as a flat mapping."""
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "name": as_text(pick(data, "name")),
        "account": as_text(pick(data, "account")),
        "owner": as_text(pick(data, "owner")),
        "currency": as_text(pick(data, "currency"), "USD"),
        "amount": as_number(pick(data, "amount"), 0.0),
        "stage": as_text(pick(data, "stage")),
        "address": pick(data, "address", {}),
    }


def deal_line_items(store: RecordStore, deal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every line item that belongs to a deal, oldest first.

    Two sources, both real. HubSpot associates line items with the deal through
    the line item object type rather than storing them inside it, so the
    collection is the primary source. Some mirrors embed the array in the deal
    record instead, and those are read too, because refusing a deal that carries
    its own lines would fail on a shape this repository already produces.

    Ordering is by ``position`` when the rows carry one. ``find`` returns rows
    newest first, so without it a deal whose lines were all written in one
    millisecond comes back in reverse and the quote inherits that order. This is
    the same defect WF-124 found and fixed in its own reader.
    """
    rows: list[dict[str, Any]] = []

    collection = _first_collection(store, DEAL_LINE_ITEM_COLLECTION_ALIASES)
    if collection:
        found = store.find(collection, {"deal_id": deal["id"]}, limit=1000)
        found += store.find(collection, {"parent_id": deal["id"]}, limit=1000)
        seen: set[str] = set()
        for row in found:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)

    data = deal.get("data") or {}
    embedded = data.get("line_items") or data.get("products") or []
    if isinstance(embedded, list):
        for position, item in enumerate(embedded):
            if isinstance(item, Mapping):
                rows.append(
                    {
                        "id": f"embedded:{position}",
                        "collection": collection or "embedded",
                        "room_id": deal.get("room_id"),
                        "data": {**dict(item), "position": item.get("position", position)},
                    }
                )

    return sorted(rows, key=lambda row: as_number((row.get("data") or {}).get("position"), 0.0))


def read_deal_line(row: Mapping[str, Any]) -> dict[str, Any]:
    """One deal line, in the flat shape a quote line is cloned from."""
    data = row.get("data") or {}
    return {
        "source_line_item_id": row.get("id"),
        "name": as_text(pick(data, "name")),
        "sku": as_text(pick(data, "sku")),
        "description": as_text(pick(data, "description")),
        "quantity": as_number(pick(data, "quantity"), 1.0),
        "unit_price": as_number(pick(data, "unit_price"), 0.0),
        "discount_type": as_text(pick(data, "discount_type"), "percentage"),
        "discount_value": as_number(pick(data, "discount_value"), 0.0),
        "tax_rate": as_number(pick(data, "tax_rate"), 0.0),
        "billing_start": as_text(pick(data, "billing_start")),
        "product_id": as_text(pick(data, "product_id")),
    }


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #


def find_product(store: RecordStore, sku: str, name: str = "") -> dict[str, Any] | None:
    """Look a product up by SKU, then by name.

    SKU first because it is the only one of the two that is unique. Name is a
    fallback and an inexact one, so the first match is returned rather than the
    best-scoring one: a quote that guesses a price from the wrong product is
    worse than one that prices from the deal.
    """
    sku = as_text(sku)
    name = as_text(name)
    if not sku and not name:
        return None
    collection = _first_collection(store, PRODUCT_COLLECTION_ALIASES)
    if not collection:
        return None
    for field, value in (("sku", sku), ("name", name)):
        if not value:
            continue
        rows = store.find(collection, {field: value}, limit=1)
        if rows:
            return rows[0]["data"] or {}
    return None


def search_products(store: RecordStore, term: str, limit: int = 25) -> list[dict[str, Any]]:
    """The product library search behind "Select from product library".

    An empty catalogue returns an empty list, which is a correct answer rather
    than an error: the dropdown has nothing in it because WF-087 has not run.
    """
    collection = _first_collection(store, PRODUCT_COLLECTION_ALIASES)
    if not collection:
        return []
    term = as_text(term)
    rows = store.list(collection, limit=1000)
    results: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") or {}
        haystack = " ".join(
            as_text(data.get(key)) for key in ("name", "description", "sku")
        ).lower()
        if term and term.lower() not in haystack:
            continue
        results.append(
            {
                "id": row.get("id"),
                "name": as_text(data.get("name")),
                "sku": as_text(data.get("sku")),
                "description": as_text(data.get("description")),
                "unit_price": as_number(data.get("unit_price") or data.get("price"), 0.0),
                "tier_count": len(data.get("tiers") or []),
            }
        )
        if len(results) >= max(1, min(int(limit), 100)):
            break
    return results
