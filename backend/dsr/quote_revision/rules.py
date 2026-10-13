"""The pure rules of WF-102. No store, no HTTP, no clock of its own.

Every function here is a function of its arguments. The engine supplies the
record fields, the catalogue product and today's date, so a rule can be tested
without a database and read on its own to answer "when is a quote frozen".

The two decisions the research fixes, and where they live
----------------------------------------------------------

**A revision is a new record.** :func:`revision_number` and
:func:`revision_fields` are the arithmetic of the increment; the engine is what
writes the new row and closes the old one. Nothing here edits a prior quote.

**Draft pricing is live; active and closed pricing is frozen.**
:func:`pricing_mode` is the whole state boundary, and the engine calls it before
it either reprices or refuses to.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.quote_authoring.pricing import (
    as_number,
    as_text,
    line_amounts,
    money,
    resolve_unit_price,
    totals_for,
)
from dsr.quote_authoring.vocabulary import (
    DISCOUNT_PERCENTAGE,
    PRICE_FROM_CATALOG,
    PRICE_FROM_MANUAL,
)
from dsr.quote_revision.errors import RevisionError
from dsr.quote_revision.vocabulary import (
    CANCELED,
    CLOSE_REASONS,
    CLOSED_STATUSES,
    DRAFT,
    ESIGNATURE_FIELD,
    FIELD_CLOSED_ON,
    FIELD_QUOTE_CREATION_METHOD,
    FIELD_REVISION_NUMBER,
    FIELD_ROOT_QUOTE_ID,
    FIELD_STATUS_REASON,
    FIELD_SUPERSEDED_BY,
    FIELD_SUPERSEDES,
    ONLINE_PAYMENTS_FIELD,
    OPEN_STATUSES,
    PROJECT_QUOTE_SCOPE,
    QUOTE_CREATION_METHOD_REVISION,
    QUOTE_SCOPE_FIELDS,
    REASON_PROJECT_QUOTE_UNSUPPORTED,
    REASON_RECALL_BLOCKED_AFTER_APPROVAL,
    REASON_RECALL_BLOCKED_NO_RESTORE,
    RECALL_BLOCKED_STATES,
    RECALL_STATE_FIELDS,
    REVISED,
    REVISION_NUMBER_ALIASES,
    SALES_QUOTE_SCOPE,
)

#: The fields copied from one quote line to the next. Price, terms and discount
#: are all here, because the research says a clone preserves all three.
COPY_LINE_FIELDS: tuple[str, ...] = (
    "name",
    "sku",
    "description",
    "billing_start",
)


# --------------------------------------------------------------------------- #
# The revision number
# --------------------------------------------------------------------------- #


def revision_number(data: Mapping[str, Any]) -> int:
    """The quote's revision id, read across the spellings in use.

    "When you create a new quote, it is automatically set to Draft, and the
    revision ID is set to 0." A quote that predates this workflow carries no
    field at all and is therefore revision 0, which is the value the research
    starts at. A malformed value reads as 0 rather than raising, so one bad row
    cannot make the whole lineage unreadable.
    """

    for field in REVISION_NUMBER_ALIASES:
        if field in data and data[field] not in (None, ""):
            try:
                return int(data[field])
            except (TypeError, ValueError):
                return 0
    return 0


def next_revision(data: Mapping[str, Any]) -> int:
    """The revision id the next draft takes: one more than this quote's."""
    return revision_number(data) + 1


def root_quote_id(record: Mapping[str, Any]) -> str:
    """The revision-0 ancestor of a quote.

    A quote with no recorded root is its own root. That is what makes the first
    revise set a root and every later revise inherit it, so the chain never
    fragments.
    """

    data = record.get("data") or {}
    return as_text(data.get(FIELD_ROOT_QUOTE_ID)) or as_text(record.get("id"))


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def is_open(status: Any) -> bool:
    """Is the quote still editable, revisable or closable?"""
    return as_text(status) in OPEN_STATUSES


def is_closed(status: Any) -> bool:
    """Has the quote been closed as Revised, Lost or Canceled?"""
    return as_text(status) in CLOSED_STATUSES


def pricing_mode(status: Any) -> str:
    """Whether this status adjusts prices to the price list.

    ``live`` for a draft: "the prices are adjusted to the actual prices on the
    price list when you open or edit the quote". ``frozen`` for everything else:
    "In the active and closed states, the prices aren't adjusted". The boundary
    is a draft against every other status, which is exactly the sentence.
    """

    return "live" if as_text(status) == DRAFT else "frozen"


# --------------------------------------------------------------------------- #
# Close reasons
# --------------------------------------------------------------------------- #


def normalise_close_reason(reason: Any) -> str:
    """Read a close reason, refusing anything that is not one of the three.

    "When you close a quote, you have three choices: Lost, Canceled, or
    Revised." A fourth reason is a refusal rather than a guess, because guessing
    would write a ``quoteclose`` activity whose reason the reader cannot map to
    a choice the product offers.
    """

    token = as_text(reason).lower()
    if token not in CLOSE_REASONS:
        raise RevisionError(
            "A quote closes as Lost, Canceled or Revised.",
            {"reason": f"Use one of: {', '.join(CLOSE_REASONS)}."},
        )
    return token


def close_fields(reason: str, *, today: str, successor_id: str | None) -> dict[str, Any]:
    """The fields a close stamps on the prior record.

    ``closedon`` is written for every reason: the research says it "indicate[s]
    the expiration, revision, or cancellation date", so it is not revision-only.
    A close is also stamped with the close reason under ``status_reason`` so a
    reader can tell the close choice from the status without inferring one from
    the other.

    ``quotecreationmethod`` is stamped ``Revision`` only for a revision. The
    issue says the revised prior record is stamped, not just closed; a Lost or
    Canceled record was not revised, so stamping it Revision would be a false
    statement about how it ended.
    """

    fields: dict[str, Any] = {
        "status": reason,
        FIELD_STATUS_REASON: reason,
        FIELD_CLOSED_ON: today,
        FIELD_SUPERSEDED_BY: successor_id,
    }
    if reason == REVISED:
        fields[FIELD_QUOTE_CREATION_METHOD] = QUOTE_CREATION_METHOD_REVISION
    return fields


def revision_fields(
    record: Mapping[str, Any], *, new_number: int, new_quote_id: str | None = None
) -> dict[str, Any]:
    """The lineage fields the new draft carries.

    Stamped on create, when ``new_quote_id`` is not yet known, and again by the
    caller if it needs the successor link. A revision is "a first-class record
    lineage", so the new draft names what it replaced under its own id.
    """

    parent = as_text(record.get("id"))
    return {
        FIELD_REVISION_NUMBER: new_number,
        FIELD_ROOT_QUOTE_ID: root_quote_id(record),
        FIELD_SUPERSEDES: parent if parent != new_quote_id else None,
        FIELD_QUOTE_CREATION_METHOD: QUOTE_CREATION_METHOD_REVISION,
    }


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def project_quote_reason(data: Mapping[str, Any]) -> str | None:
    """Whether this is a project quote, which does not support revisions.

    "A project quote doesn't support those processes." Recorded rather than
    generalised, because the research states the boundary.
    """

    for field in QUOTE_SCOPE_FIELDS:
        value = data.get(field)
        if isinstance(value, bool):
            if value:
                return REASON_PROJECT_QUOTE_UNSUPPORTED
            continue
        token = as_text(value).lower()
        if token in (PROJECT_QUOTE_SCOPE, "project", "project_operations"):
            return REASON_PROJECT_QUOTE_UNSUPPORTED
    return None


# --------------------------------------------------------------------------- #
# The recall gate
# --------------------------------------------------------------------------- #


def recall_block_reason(data: Mapping[str, Any]) -> str | None:
    """Why a published quote cannot be recalled, or ``None`` when it can.

    Two gates, both quoted. Recall is "blocked once approved, accepted or paid",
    and a published quote "with online payments or e-signature enabled can't be
    restored". The second is checked after the first so an accepted quote
    reports the stronger reason.
    """

    for field in RECALL_STATE_FIELDS:
        token = as_text(data.get(field)).lower()
        if token in RECALL_BLOCKED_STATES:
            return REASON_RECALL_BLOCKED_AFTER_APPROVAL
    if data.get(ONLINE_PAYMENTS_FIELD) or data.get(ESIGNATURE_FIELD):
        return REASON_RECALL_BLOCKED_NO_RESTORE
    return None


# --------------------------------------------------------------------------- #
# Line items
# --------------------------------------------------------------------------- #


def _line_base(line: Mapping[str, Any], position: int) -> dict[str, Any]:
    """The header fields a copied line carries, at its new position."""

    copied: dict[str, Any] = {"position": position}
    for field in COPY_LINE_FIELDS:
        copied[field] = as_text(line.get(field))
    copied["description"] = as_text(line.get("description"))
    copied["quantity"] = as_number(line.get("quantity"), 1.0)
    copied["unit_price"] = money(as_number(line.get("unit_price"), 0.0))
    copied["discount_type"] = as_text(line.get("discount_type"), DISCOUNT_PERCENTAGE)
    copied["discount_value"] = as_number(line.get("discount_value"), 0.0)
    copied["tax_rate"] = as_number(line.get("tax_rate"), 0.0)
    copied["product_id"] = as_text(line.get("product_id"))
    copied["price_source"] = as_text(line.get("price_source"), PRICE_FROM_MANUAL)
    copied["tier_label"] = as_text(line.get("tier_label")) or None
    return copied


def cloned_line(line: Mapping[str, Any], position: int) -> dict[str, Any]:
    """One line, copied with its price, terms and discount exactly as they are.

    This is the Clone behaviour: "Cloned quotes include the original quote's
    record associations and the property values of the line items, including
    price, terms, and discount." The clone keeps the seller's price and does not
    reprice, which is the difference between a clone and a revision.
    """

    copied = _line_base(line, position)
    copied["source_line_item_id"] = as_text(line.get("id")) or as_text(
        line.get("source_line_item_id")
    )
    copied["reprice"] = {"mode": "copied", "adjusted": False}
    copied["amounts"] = line_amounts(copied)
    return copied


def catalogue_price(
    product: Mapping[str, Any] | None, quantity: Any, fallback: Any
) -> tuple[float, str, str | None]:
    """A unit price from the price list, or the fallback when there is none.

    The research says a draft "recalculat[es] from the current price list", and
    WF-087 (the catalogue) is merged. When the product is absent or the catalogue
    prices nothing, the price the line already carried is kept and the source
    says ``manual``, so an empty catalogue degrades rather than zeroing a line.
    """

    if not product:
        return money(as_number(fallback)), PRICE_FROM_MANUAL, None
    price, tier_label, _kind = resolve_unit_price(product, as_number(quantity, 1.0))
    if price <= 0:
        return money(as_number(fallback)), PRICE_FROM_MANUAL, tier_label
    return money(price), PRICE_FROM_CATALOG, tier_label


def revised_line(
    line: Mapping[str, Any], position: int, product: Mapping[str, Any] | None
) -> dict[str, Any]:
    """One line of a new draft, repriced from the current price list.

    The draft is new, so it is priced live: a revision is the one place the
    research says the price list is re-read. The line keeps its own record
    identity as a fresh row; the prior line's id is provenance only.
    """

    copied = _line_base(line, position)
    price, source, tier_label = catalogue_price(product, copied["quantity"], copied["unit_price"])
    copied["unit_price"] = price
    copied["price_source"] = source
    copied["tier_label"] = tier_label
    copied["source_line_item_id"] = as_text(line.get("id"))
    copied["reprice"] = {
        "mode": "repriced",
        "adjusted": source == PRICE_FROM_CATALOG,
        "from": money(as_number(line.get("unit_price"), 0.0)),
        "to": price,
    }
    copied["amounts"] = line_amounts(copied)
    return copied


def line_totals(lines: list[Mapping[str, Any]], schedule: Any, today: str) -> dict[str, Any]:
    """The totals for a set of already-shaped lines. Delegates to WF-086's contract."""

    return totals_for(lines, schedule or [], today)


__all__ = [
    "CANCELED",
    "catalogue_price",
    "close_fields",
    "cloned_line",
    "is_closed",
    "is_open",
    "line_totals",
    "next_revision",
    "normalise_close_reason",
    "pricing_mode",
    "project_quote_reason",
    "recall_block_reason",
    "revision_fields",
    "revision_number",
    "revised_line",
    "root_quote_id",
    "SALES_QUOTE_SCOPE",
]
