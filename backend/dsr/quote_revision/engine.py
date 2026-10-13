"""The writes and reads for WF-102.

The engine holds a store and a clock and nothing else, so a test can build one
with a frozen clock and exercise every rule without a server. Every write takes
a ``source`` built by the feature module from ``router.prefix``, so each audit
row names a URL the app actually serves.

Two shapes this engine is careful about
---------------------------------------

**A revision is never an edit of the prior record.** :meth:`revise` creates a
new quote and its own line items, then closes the prior quote. The prior row is
never rewritten except for the close fields, which is what "the deal history must
stay readable" means. :meth:`clone` is the other copy path and deliberately does
*not* close anything.

**The draft boundary is the pricing boundary.** :meth:`reprice` adjusts a draft's
unit prices from the catalogue and refuses to adjust anything else, because the
research says the price list is read only in the draft state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.quote_authoring.deals import find_product
from dsr.quote_authoring.pricing import as_number, as_text
from dsr.quote_authoring.vocabulary import LINE_ITEM_COLLECTION, QUOTE_COLLECTION, STATUS_DRAFT
from dsr.quote_revision import rules
from dsr.quote_revision.errors import RevisionConflict, RevisionNotFound
from dsr.quote_revision.vocabulary import (
    ACTIVITY_COLLECTION,
    ACTIVITY_LABELS,
    ACTIVITY_QUOTE_CLOSE,
    ACTIVITY_QUOTE_RECALLED,
    ACTIVITY_QUOTE_REPUBLISHED,
    ARTEFACT_COLLECTION,
    CANCELED,
    FIELD_CLONED_FROM,
    FIELD_CLOSED_ON,
    FIELD_QUOTE_CREATION_METHOD,
    FIELD_REVISION_NUMBER,
    FIELD_ROOT_QUOTE_ID,
    FIELD_STATUS_REASON,
    FIELD_SUPERSEDED_BY,
    FIELD_SUPERSEDES,
    PUBLISHED,
    QUOTE_CREATION_METHOD_UNKNOWN,
    REASON_QUOTE_ALREADY_CLOSED,
    REASON_QUOTE_FROZEN,
    REASON_QUOTE_IS_EMPTY,
    REASON_QUOTE_NOT_PUBLISHED,
    REASON_REPUBLISH_NEEDS_A_RECALL,
    REFUSAL_REASONS,
    REVISED,
    STATUS_LABELS,
    vocabulary,
)

#: Fields the engine owns. A carried header drops them so a new draft cannot
#: inherit a status, a close date or a totals figure from the record it replaces.
MANAGED_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        FIELD_STATUS_REASON,
        FIELD_CLOSED_ON,
        FIELD_REVISION_NUMBER,
        FIELD_ROOT_QUOTE_ID,
        FIELD_SUPERSEDES,
        FIELD_SUPERSEDED_BY,
        FIELD_CLONED_FROM,
        FIELD_QUOTE_CREATION_METHOD,
        "totals",
        "published_at",
        "recalled_at",
        "republished_at",
        "created_at",
        "deal_amount_written",
        "deal_line_items_replaced",
        "hs_locked",
        "hs_quote_link",
    }
)


#: WF-087's own product and price-book-item collections. WF-086's alias list was
#: written before WF-087 shipped and does not name either, so they are read here
#: as a second source rather than through ``find_product``.
CATALOGUE_COLLECTIONS: tuple[str, ...] = ("wf087_product", "wf087_price_book_item")


class QuoteRevisionEngine:
    """Every write and read this workflow performs."""

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now().astimezone())

    # -- the clock ---------------------------------------------------------- #

    def today(self) -> str:
        return self._now().date().isoformat()

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    # -- internals ---------------------------------------------------------- #

    def _require_quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record.get("collection") != QUOTE_COLLECTION:
            raise RevisionNotFound("No such quote.")
        return record

    @staticmethod
    def _data(record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _lines(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=1000)
        shaped: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row["data"] or {})
            data["id"] = row["id"]
            data["room_id"] = row.get("room_id")
            shaped.append(data)
        return sorted(shaped, key=lambda line: as_number(line.get("position"), 0.0))

    def _product_for(self, line: Mapping[str, Any]) -> dict[str, Any] | None:
        """The catalogue product a line prices from, or ``None``.

        Read before a write transaction opens, because the catalogue is a read
        and the transaction is the window in which no other write may happen.
        Three sources, in order: the product the line names, WF-086's alias list
        (``crm_product`` and friends), then WF-087's own collections. WF-087
        merged after WF-086, and its names are not in WF-086's alias list, so
        without the third read a revision would never reprice against the
        catalogue the research says to re-read.
        """

        supplied = as_text(line.get("product_id"))
        if supplied:
            record = self.store.get(supplied)
            if record is not None:
                return dict(record.get("data") or {})
        found = find_product(self.store, as_text(line.get("sku")), as_text(line.get("name")))
        if found:
            return dict(found)
        sku, name = as_text(line.get("sku")), as_text(line.get("name"))
        for collection in CATALOGUE_COLLECTIONS:
            for field, value in (("sku", sku), ("name", name)):
                if not value:
                    continue
                rows = self.store.find(collection, {field: value}, limit=1)
                if rows:
                    return dict(rows[0].get("data") or {})
        return None

    def _header(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """The fields a new draft carries from the quote it is derived from."""

        return {key: value for key, value in data.items() if key not in MANAGED_FIELDS}

    def _activity_payload(
        self,
        action: str,
        *,
        quote_id: str,
        reason: str | None,
        revision_number: int,
        at: str,
        successor_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "quote_id": quote_id,
            "action": action,
            "label": ACTIVITY_LABELS.get(action, action),
            "reason": reason,
            "revision_number": revision_number,
            "successor_quote_id": successor_id,
            "at": at,
        }

    # -- reads -------------------------------------------------------------- #

    def _quote_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        quote_id = str(record["id"])
        lines = self._lines(quote_id)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        mode = rules.pricing_mode(status)
        return {
            "id": quote_id,
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "revision_number": rules.revision_number(data),
            "root_quote_id": rules.root_quote_id(record),
            "open": rules.is_open(status),
            "closed": rules.is_closed(status),
            "pricing": {"mode": mode, "adjusts_to_price_list": mode == "live"},
            "line_items": lines,
            "totals": rules.line_totals(lines, data.get("payment_schedule"), self.today()),
        }

    def read_quote(self, quote_id: str) -> dict[str, Any]:
        return self._quote_view(self._require_quote(quote_id))

    def list_quotes(
        self, room_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.list(
            QUOTE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        )
        views = [self._quote_view(row) for row in rows]
        if status:
            views = [quote for quote in views if quote["status"] == status]
        return views

    def lineage(self, quote_id: str) -> dict[str, Any]:
        """The revision chain, oldest first.

        Walked through ``superseded_by`` from the revision-0 ancestor, so it
        reads as a history rather than a set of records that happen to share a
        title.
        """

        record = self._require_quote(quote_id)
        root = rules.root_quote_id(record)
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current: dict[str, Any] | None = self.store.get(root)
        while (
            current is not None
            and current.get("collection") == QUOTE_COLLECTION
            and current["id"] not in seen
        ):
            seen.add(current["id"])
            data = self._data(current)
            status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
            chain.append(
                {
                    "id": current["id"],
                    "revision_number": rules.revision_number(data),
                    "status": status,
                    "status_label": STATUS_LABELS.get(status, status),
                    "closed_on": as_text(data.get(FIELD_CLOSED_ON)) or None,
                    "title": as_text(data.get("title")),
                    "superseded_by_quote_id": as_text(data.get(FIELD_SUPERSEDED_BY)) or None,
                    "supersedes_quote_id": as_text(data.get(FIELD_SUPERSEDES)) or None,
                }
            )
            nxt = as_text(data.get(FIELD_SUPERSEDED_BY))
            current = self.store.get(nxt) if nxt else None
        return {
            "quote_id": quote_id,
            "root_quote_id": root,
            "count": len(chain),
            "latest_quote_id": chain[-1]["id"] if chain else quote_id,
            "revisions": chain,
        }

    def activity(self, quote_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.find(ACTIVITY_COLLECTION, {"quote_id": quote_id}, limit=limit)
        return [dict(row["data"] or {}) | {"id": row["id"]} for row in rows]

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        quotes = self.list_quotes(room_id)
        by_status: dict[str, int] = {}
        for quote in quotes:
            by_status[quote["status"]] = by_status.get(quote["status"], 0) + 1
        activities = self.store.list(ACTIVITY_COLLECTION, room_id=room_id, limit=1000)
        artefacts = self.store.list(ARTEFACT_COLLECTION, room_id=room_id, limit=1000)
        return {
            "quotes": len(quotes),
            "by_status": by_status,
            "open": sum(1 for quote in quotes if quote["open"]),
            "closed": sum(1 for quote in quotes if quote["closed"]),
            "revised": by_status.get(REVISED, 0),
            "lost": by_status.get("lost", 0),
            "canceled": by_status.get(CANCELED, 0),
            "revision_chains": sum(1 for quote in quotes if quote.get(FIELD_SUPERSEDES)),
            "line_items": sum(len(quote["line_items"]) for quote in quotes),
            "activities": len(activities),
            "artefacts": len(artefacts),
            "catalogue_available": any(
                self.store.list(collection, limit=1)
                for collection in ("wf087_product", "wf087_price_book_item", "crm_product")
            ),
        }

    # -- revise ------------------------------------------------------------- #

    def revise(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Revise a quote into a new draft and close the prior record.

        The new draft carries the next revision id and is repriced from the
        current price list, because a draft is the one state whose prices are
        live. The prior record is closed as Revised with its ``closedon`` stamped
        and a ``quoteclose`` activity written. Nothing on the prior record's
        lines is touched, so the deal history is preserved.
        """

        payload = dict(payload or {})
        record = self._require_quote(quote_id)
        data = self._data(record)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        if not rules.is_open(status):
            raise RevisionConflict(
                REASON_QUOTE_ALREADY_CLOSED, REFUSAL_REASONS[REASON_QUOTE_ALREADY_CLOSED]
            )
        scope_reason = rules.project_quote_reason(data)
        if scope_reason:
            raise RevisionConflict(scope_reason, REFUSAL_REASONS[scope_reason])

        lines = self._lines(quote_id)
        if not lines:
            raise RevisionConflict(REASON_QUOTE_IS_EMPTY, REFUSAL_REASONS[REASON_QUOTE_IS_EMPTY])

        new_number = rules.next_revision(data)
        today, stamp = self.today(), self._stamp()
        schedule = list(data.get("payment_schedule") or [])
        new_lines = [
            rules.revised_line(line, position, self._product_for(line))
            for position, line in enumerate(lines)
        ]
        totals = rules.line_totals(new_lines, schedule, today)
        header = self._header(data)
        title = as_text(payload.get("title")) or self._revision_title(data, new_number)
        room_id = record.get("room_id")

        with self.store.db.transaction(actor=actor, source=source) as tx:
            new_quote = tx.create(
                QUOTE_COLLECTION,
                {
                    **header,
                    "title": title,
                    "status": STATUS_DRAFT,
                    **rules.revision_fields(record, new_number=new_number),
                    "payment_schedule": schedule,
                    "totals": totals,
                    "created_at": stamp,
                },
                room_id=room_id,
            )
            for line in new_lines:
                tx.create(
                    LINE_ITEM_COLLECTION,
                    {**line, "quote_id": new_quote["id"]},
                    room_id=room_id,
                )
            tx.update(
                quote_id,
                rules.close_fields(REVISED, today=today, successor_id=new_quote["id"]),
            )
            tx.create(
                ACTIVITY_COLLECTION,
                self._activity_payload(
                    ACTIVITY_QUOTE_CLOSE,
                    quote_id=quote_id,
                    reason=REVISED,
                    revision_number=rules.revision_number(data),
                    at=stamp,
                    successor_id=new_quote["id"],
                ),
                room_id=room_id,
            )

        return {
            "quote": self._quote_view(self._require_quote(new_quote["id"])),
            "prior": self._quote_view(self._require_quote(quote_id)),
            "new_revision_number": new_number,
            "cloned_line_items": len(new_lines),
            "repriced_line_items": sum(1 for line in new_lines if line["reprice"].get("adjusted")),
            "activity": ACTIVITY_QUOTE_CLOSE,
        }

    def _revision_title(self, data: Mapping[str, Any], new_number: int) -> str:
        base = as_text(data.get("title")) or "Quote"
        return f"{base} (revision {new_number})"

    # -- close -------------------------------------------------------------- #

    def close(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Close a quote as Lost, Canceled or Revised.

        One enumeration and one write path: the reason sets the status, stamps
        ``closedon`` and writes the same ``quoteclose`` activity, because the
        research says the activity is generated whenever a quote is closed. The
        normal way to reach Revised is :meth:`revise`, which also creates the
        successor; closing as Revised here closes without one.
        """

        payload = dict(payload or {})
        record = self._require_quote(quote_id)
        data = self._data(record)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        if not rules.is_open(status):
            raise RevisionConflict(
                REASON_QUOTE_ALREADY_CLOSED, REFUSAL_REASONS[REASON_QUOTE_ALREADY_CLOSED]
            )
        reason = rules.normalise_close_reason(payload.get("reason"))
        today, stamp = self.today(), self._stamp()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            tx.update(quote_id, rules.close_fields(reason, today=today, successor_id=None))
            tx.create(
                ACTIVITY_COLLECTION,
                self._activity_payload(
                    ACTIVITY_QUOTE_CLOSE,
                    quote_id=quote_id,
                    reason=reason,
                    revision_number=rules.revision_number(data),
                    at=stamp,
                ),
                room_id=record.get("room_id"),
            )
        return {
            "quote": self._quote_view(self._require_quote(quote_id)),
            "reason": reason,
            "activity": ACTIVITY_QUOTE_CLOSE,
        }

    # -- clone -------------------------------------------------------------- #

    def clone(
        self,
        quote_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Clone a quote: a new draft with the line prices copied exactly.

        This is the HubSpot analogue the research names. It differs from a
        revision in the two ways the research states: it copies the line-item
        "price, terms, and discount" rather than repricing, and it leaves the
        original record open. A clone is a new quote with its own lineage, so its
        revision id restarts at 0.
        """

        payload = dict(payload or {})
        record = self._require_quote(quote_id)
        data = self._data(record)
        scope_reason = rules.project_quote_reason(data)
        if scope_reason:
            raise RevisionConflict(scope_reason, REFUSAL_REASONS[scope_reason])

        lines = self._lines(quote_id)
        today, stamp = self.today(), self._stamp()
        schedule = list(data.get("payment_schedule") or [])
        new_lines = [rules.cloned_line(line, position) for position, line in enumerate(lines)]
        totals = rules.line_totals(new_lines, schedule, today)
        header = self._header(data)
        title = as_text(payload.get("title")) or f"{as_text(data.get('title')) or 'Quote'} (clone)"
        room_id = record.get("room_id")

        with self.store.db.transaction(actor=actor, source=source) as tx:
            clone = tx.create(
                QUOTE_COLLECTION,
                {
                    **header,
                    "title": title,
                    "status": STATUS_DRAFT,
                    FIELD_REVISION_NUMBER: 0,
                    FIELD_CLONED_FROM: quote_id,
                    FIELD_QUOTE_CREATION_METHOD: QUOTE_CREATION_METHOD_UNKNOWN,
                    "payment_schedule": schedule,
                    "totals": totals,
                    "created_at": stamp,
                },
                room_id=room_id,
            )
            for line in new_lines:
                tx.create(
                    LINE_ITEM_COLLECTION,
                    {**line, "quote_id": clone["id"]},
                    room_id=room_id,
                )

        return {
            "quote": self._quote_view(self._require_quote(clone["id"])),
            "cloned_from_quote_id": quote_id,
            "prior_unchanged": True,
            "cloned_line_items": len(new_lines),
        }

    # -- reprice ------------------------------------------------------------ #

    def reprice(self, quote_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        """Adjust a draft's prices to the current price list.

        A draft is live and a non-draft is frozen, which is the research's whole
        sentence about pricing. The frozen branch returns rather than raising:
        "the prices aren't adjusted" is a correct answer, and the caller reads
        ``frozen: true`` and the reason instead of an error.
        """

        record = self._require_quote(quote_id)
        data = self._data(record)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        if rules.pricing_mode(status) != "live":
            return {
                "adjusted": False,
                "frozen": True,
                "reason": REASON_QUOTE_FROZEN,
                "quote": self._quote_view(record),
            }
        lines = self._lines(quote_id)
        today, stamp = self.today(), self._stamp()
        schedule = list(data.get("payment_schedule") or [])
        shaped = [
            rules.revised_line(line, position, self._product_for(line))
            for position, line in enumerate(lines)
        ]
        totals = rules.line_totals(shaped, schedule, today)
        with self.store.db.transaction(actor=actor, source=source) as tx:
            for original, refreshed in zip(lines, shaped, strict=True):
                tx.update(
                    original["id"],
                    {
                        "unit_price": refreshed["unit_price"],
                        "price_source": refreshed["price_source"],
                        "tier_label": refreshed["tier_label"],
                        "amounts": refreshed["amounts"],
                    },
                )
            tx.update(quote_id, {"totals": totals, "repriced_on": stamp})
        return {
            "adjusted": True,
            "frozen": False,
            "adjusted_lines": sum(1 for line in shaped if line["reprice"].get("adjusted")),
            "checked_lines": len(shaped),
            "quote": self._quote_view(self._require_quote(quote_id)),
        }

    # -- recall and re-publish ---------------------------------------------- #

    def recall(self, quote_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        """Recall a published quote back to draft.

        Refused once the quote is approved, accepted or paid, and refused when it
        has online payments or e-signature enabled, which is the research's
        restore gate. A recall writes the ``Quote recalled`` activity.
        """

        record = self._require_quote(quote_id)
        data = self._data(record)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        if status != PUBLISHED:
            raise RevisionConflict(
                REASON_QUOTE_NOT_PUBLISHED, REFUSAL_REASONS[REASON_QUOTE_NOT_PUBLISHED]
            )
        blocked = rules.recall_block_reason(data)
        if blocked:
            raise RevisionConflict(blocked, REFUSAL_REASONS[blocked])
        stamp = self._stamp()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            tx.update(
                quote_id,
                {"status": STATUS_DRAFT, "recalled_at": stamp, "hs_status": "DRAFT"},
            )
            tx.create(
                ACTIVITY_COLLECTION,
                self._activity_payload(
                    ACTIVITY_QUOTE_RECALLED,
                    quote_id=quote_id,
                    reason=None,
                    revision_number=rules.revision_number(data),
                    at=stamp,
                ),
                room_id=record.get("room_id"),
            )
        return {
            "quote": self._quote_view(self._require_quote(quote_id)),
            "recalled": True,
            "activity": ACTIVITY_QUOTE_RECALLED,
        }

    def republish(self, quote_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        """Re-issue a recalled quote, generating a fresh link and PDF.

        "re-publish re-generates the link/PDF", so the artefacts are new rows
        rather than edits: the sequence number is one more than the quote's
        existing link artefacts, and the quote is published again.
        """

        record = self._require_quote(quote_id)
        data = self._data(record)
        status = as_text(data.get("status"), STATUS_DRAFT) or STATUS_DRAFT
        if status != STATUS_DRAFT:
            raise RevisionConflict(
                REASON_REPUBLISH_NEEDS_A_RECALL, REFUSAL_REASONS[REASON_REPUBLISH_NEEDS_A_RECALL]
            )
        stamp = self._stamp()
        existing = self.store.find(ARTEFACT_COLLECTION, {"quote_id": quote_id}, limit=1000)
        sequence = len([row for row in existing if row["data"].get("kind") == "link"]) + 1
        number = rules.revision_number(data)
        url = f"https://quotes.example/{quote_id}/r{number}-{sequence}"
        room_id = record.get("room_id")
        artefacts = [
            {"kind": "link", "url": url, "sequence": sequence, "regenerated": sequence > 1},
            {"kind": "pdf", "url": url + ".pdf", "sequence": sequence, "regenerated": sequence > 1},
        ]
        with self.store.db.transaction(actor=actor, source=source) as tx:
            for artefact in artefacts:
                tx.create(
                    ARTEFACT_COLLECTION,
                    {
                        "quote_id": quote_id,
                        "revision_number": number,
                        "generated_at": stamp,
                        **artefact,
                    },
                    room_id=room_id,
                )
            tx.update(
                quote_id,
                {
                    "status": PUBLISHED,
                    "republished_at": stamp,
                    "hs_locked": True,
                    "hs_quote_link": url,
                },
            )
            tx.create(
                ACTIVITY_COLLECTION,
                self._activity_payload(
                    ACTIVITY_QUOTE_REPUBLISHED,
                    quote_id=quote_id,
                    reason=None,
                    revision_number=number,
                    at=stamp,
                ),
                room_id=room_id,
            )
        return {
            "quote": self._quote_view(self._require_quote(quote_id)),
            "artefacts": [
                {"kind": artefact["kind"], "url": artefact["url"]} for artefact in artefacts
            ],
            "activity": ACTIVITY_QUOTE_REPUBLISHED,
        }

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocabulary()
