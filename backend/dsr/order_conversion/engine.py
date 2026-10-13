"""The reads and writes of WF-101. No HTTP, and no database handle.

Every read and write goes through the :class:`~dsr.store.RecordStore` the
feature passes in, so the audit row is written in the same transaction as the
change. The ``source`` of every write is supplied by the caller (the route) and
names the URL the app actually serves.

The clock is a seam, not a global: ``now`` is injected and defaults to local
now, so a test pins a moment and a conversion never depends on what day it runs.

The order is a copy, not a view
-------------------------------

Each order line is a new record with its own id. The quote line's id is kept as
``source_quote_line_item_id``, which is provenance and never an identity.
Editing the quote after conversion therefore changes nothing on the order,
which is what a price lock means. See
:data:`dsr.order_conversion.inferences.DERIVED_LOCKED_EDIT` for what a
price-list edit does to a locked order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.order_conversion import inferences, rules, vocabulary as vocab
from dsr.order_conversion.errors import OrderConflict, OrderError, OrderNotFound
from dsr.price_books.errors import CatalogueRefusal
from dsr.price_books.rules import effective_unit_price
from dsr.quote_authoring.pricing import line_amounts

#: Order header fields a generic edit may not touch, because a dedicated route
#: owns each one. A patch that names one is refused rather than silently
#: dropped, so a caller learns which route to call.
PROTECTED_ORDER_FIELDS: tuple[str, ...] = (
    "source_quote_id",
    vocab.FIELD_IS_PRICE_LOCKED,
    "price_lock_branch",
    "price_lock_reason",
    "status",
    "closed_at",
    "closed_reason",
    "cloned_components",
)


class OrderConversionEngine:
    """The domain service. One instance per request, over the shared store."""

    def __init__(
        self,
        store: Any,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now())

    # ------------------------------------------------------------------ #
    # Clock
    # ------------------------------------------------------------------ #

    def now(self) -> datetime:
        return self._now()

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def today(self) -> str:
        return self._now().date().isoformat()

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocab.vocabulary()

    @staticmethod
    def decisions() -> list[dict[str, Any]]:
        return inferences.decision_list()

    # ------------------------------------------------------------------ #
    # The settings
    # ------------------------------------------------------------------ #

    @staticmethod
    def _scope(room_id: str | None) -> str:
        return room_id or "global"

    def _setting_record(self, room_id: str | None) -> dict[str, Any] | None:
        rows = self.store.find(vocab.SETTING_COLLECTION, {"scope": self._scope(room_id)}, limit=1)
        return rows[0] if rows else None

    def settings(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The effective setting for a room, merged over the defaults."""

        record = self._setting_record(room_id)
        stored = dict((record or {}).get("data") or {})
        merged = {
            **vocab.DEFAULT_SETTING,
            **{field: stored[field] for field in vocab.SETTING_FIELDS if field in stored},
        }
        return {
            **merged,
            "setting_id": (record or {}).get("id"),
            "explicit": bool(record),
            "room_id": room_id,
        }

    def update_settings(
        self,
        patch: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Upsert the setting for a room. Unknown keys are ignored, not refused."""

        clean = {
            field: rules.as_bool(patch[field]) for field in vocab.SETTING_FIELDS if field in patch
        }
        record = self._setting_record(room_id)
        if record:
            self.store.update(record["id"], clean, actor=actor, source=source)
        else:
            self.store.create(
                vocab.SETTING_COLLECTION,
                {"scope": self._scope(room_id), **vocab.DEFAULT_SETTING, **clean},
                room_id=room_id,
                actor=actor,
                source=source,
            )
        return self.settings(room_id=room_id)

    # ------------------------------------------------------------------ #
    # Records
    # ------------------------------------------------------------------ #

    def _require_quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if not record or record.get("collection") != vocab.QUOTE_COLLECTION:
            raise OrderNotFound(f"No quote record {quote_id!r}.")
        return record

    def _require_order(self, order_id: str) -> dict[str, Any]:
        record = self.store.get(order_id)
        if not record or record.get("collection") != vocab.ORDER_COLLECTION:
            raise OrderNotFound(f"No order record {order_id!r}.")
        return record

    def _order_lines(self, order_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.ORDER_LINE_COLLECTION, {"order_id": order_id}, limit=1000)
        rows.sort(key=lambda row: rules.as_number((row.get("data") or {}).get("position"), 0.0))
        shaped: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row.get("data") or {})
            data["id"] = row["id"]
            data["room_id"] = row.get("room_id")
            shaped.append(data)
        return shaped

    def _quote_lines(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.QUOTE_LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=1000)
        rows.sort(key=lambda row: rules.as_number((row.get("data") or {}).get("position"), 0.0))
        return rows

    def _order_for_quote(self, quote_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.ORDER_COLLECTION, {"source_quote_id": quote_id}, limit=1)
        return rows[0] if rows else None

    def _ledger(
        self,
        *,
        kind: str,
        room_id: str | None,
        actor: str | None,
        source: str,
        quote_id: str | None = None,
        order_id: str | None = None,
        reason: str = "",
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.CONVERSION_COLLECTION,
            {
                "kind": kind,
                "quote_id": quote_id,
                "order_id": order_id,
                "reason": reason or None,
                "entries": list((extra or {}).get("entries") or []),
                "detail": dict((extra or {}).get("detail") or {}),
                "at": self._stamp(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def _order_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        lines = self._order_lines(str(record["id"]))
        locked = rules.is_locked(data)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            vocab.FIELD_PRICES_LOCKED: "Yes" if locked else "No",
            "locked": locked,
            "closed": rules.is_closed(data),
            "status_label": vocab.ORDER_STATUS_LABELS.get(
                rules.as_text(data.get("status")), rules.as_text(data.get("status"))
            ),
            "pricing_mode": (vocab.PRICING_MODE_LOCKED if locked else vocab.PRICING_MODE_CURRENT),
            "write_in_allowed": rules.can_add_write_in(data),
            "line_items": lines,
            "line_item_count": len(lines),
        }

    def order(self, order_id: str) -> dict[str, Any]:
        return self._order_view(self._require_order(order_id))

    def orders(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store.list(
            vocab.ORDER_COLLECTION, room_id=room_id, limit=limit, order_by="created_at"
        )
        return [self._order_view(row) for row in rows]

    def readiness(self, quote_id: str) -> dict[str, Any]:
        """What would happen if this quote were converted now, and why."""

        record = self._require_quote(quote_id)
        data = dict(record.get("data") or {})
        setting = self.settings(room_id=record.get("room_id"))
        existing = self._order_for_quote(quote_id)
        refusal = rules.conversion_refusal(data, setting, already_converted=existing is not None)
        state = rules.quote_state(data)
        return {
            "quote_id": quote_id,
            "title": rules.as_text(data.get("title")) or rules.as_text(data.get("name")),
            "status": rules.as_text(data.get("status")),
            "activated": state["activated"],
            "activated_by": state["activated_by"],
            "accepted": state["accepted"],
            "accepted_by": state["accepted_by"],
            "convertible": refusal is None and existing is None,
            "refusal": refusal,
            "refusal_reason": vocab.refusal_reasons().get(refusal or "", None),
            "existing_order_id": existing["id"] if existing else None,
            "price_lock": rules.price_lock_for(setting),
            "setting": setting,
            "line_item_count": len(self._quote_lines(quote_id)),
            "clone_components": list(vocab.CLONE_COMPONENTS),
        }

    # ------------------------------------------------------------------ #
    # The conversion
    # ------------------------------------------------------------------ #

    def convert(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Clone the accepted quote onto a new order and stamp the price lock.

        A quote that already produced an order returns that order unchanged and
        records why, so the call is idempotent and the ledger shows a second
        attempt rather than silently doing nothing.
        """

        record = self._require_quote(quote_id)
        data = dict(record.get("data") or {})
        room_id = record.get("room_id")
        setting = self.settings(room_id=room_id)
        price_lock = rules.price_lock_for(setting)

        existing = self._order_for_quote(quote_id)
        if existing is not None:
            self._ledger(
                kind="conversion",
                quote_id=quote_id,
                order_id=existing["id"],
                reason=vocab.REASON_ALREADY_CONVERTED,
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "created": False,
                "order": self._order_view(existing),
                "reason": vocab.REASON_ALREADY_CONVERTED,
                "price_lock": price_lock,
                "cloned_line_items": 0,
            }

        refusal = rules.conversion_refusal(data, setting)
        if refusal is not None:
            self._ledger(
                kind="conversion",
                quote_id=quote_id,
                reason=refusal,
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "created": False,
                "order": None,
                "reason": refusal,
                "price_lock": price_lock,
                "cloned_line_items": 0,
            }

        quote_lines = [
            {**dict(row.get("data") or {}), "id": row["id"]} for row in self._quote_lines(quote_id)
        ]
        won_on = (
            rules.as_text(payload.get("date_won") or payload.get("won_on") or payload.get("won_at"))
            or self.today()
        )
        description = rules.as_text(payload.get("description"))
        header = rules.order_payload(
            data,
            quote_id,
            quote_lines,
            price_lock=price_lock,
            won_on=won_on,
            description=description,
            converted_at=self._stamp(),
            today=self.today(),
        )
        header["converted_via"] = rules.as_text(payload.get("converted_via")) or "convert_quote"

        order = self.store.create(
            vocab.ORDER_COLLECTION, header, room_id=room_id, actor=actor, source=source
        )
        cloned = 0
        for position, line in enumerate(quote_lines):
            shaped = rules.order_line_from_quote(line, position)
            shaped["order_id"] = order["id"]
            self.store.create(
                vocab.ORDER_LINE_COLLECTION, shaped, room_id=room_id, actor=actor, source=source
            )
            cloned += 1

        self._ledger(
            kind="conversion",
            quote_id=quote_id,
            order_id=order["id"],
            room_id=room_id,
            actor=actor,
            source=source,
            extra={
                "detail": {
                    "locked": price_lock["locked"],
                    "price_lock_branch": price_lock["branch"],
                    "cloned_line_items": cloned,
                }
            },
        )
        return {
            "created": True,
            "order": self._order_view(order),
            "reason": "",
            "price_lock": price_lock,
            "cloned_line_items": cloned,
        }

    # ------------------------------------------------------------------ #
    # The order header
    # ------------------------------------------------------------------ #

    def update_order(
        self,
        order_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Edit the order header. A closed order refuses every edit."""

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        rules.assert_order_open(data)

        offenders = [key for key in patch if key in PROTECTED_ORDER_FIELDS]
        if offenders:
            raise OrderError(
                "Those fields are owned by their own route.",
                {key: "This field is set by a dedicated route." for key in offenders},
            )
        clean = {key: value for key, value in patch.items() if key not in ("actor", "room_id")}
        if "won_on" in clean:
            clean["won_on"] = rules.as_text(clean["won_on"]) or None
        if not clean:
            return self._order_view(record)
        self.store.update(order_id, clean, actor=actor, source=source)
        return self.order(order_id)

    # ------------------------------------------------------------------ #
    # Pricing mode
    # ------------------------------------------------------------------ #

    def set_pricing_mode(
        self,
        order_id: str,
        mode: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Lock the price or switch the order to current pricing."""

        record = self._require_order(order_id)
        rules.assert_order_open(record.get("data"))
        if mode not in vocab.PRICING_MODES:
            raise OrderError(
                f"Unknown pricing mode {mode!r}.",
                {"mode": "Use one of: " + ", ".join(vocab.PRICING_MODES)},
            )
        locked = mode == vocab.PRICING_MODE_LOCKED
        self.store.update(
            order_id,
            {
                vocab.FIELD_IS_PRICE_LOCKED: locked,
                "price_lock_mode": mode,
                "price_lock_changed_at": self._stamp(),
            },
            actor=actor,
            source=source,
        )
        return self.order(order_id)

    def _price_for(
        self, order: Mapping[str, Any], line: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve the catalogue price for one order line, or ``None``.

        The product's own price is evaluated first and an active price-book row
        overrides it, which is the precedence WF-087 documents. No currency is
        ever converted: the order carries the quote's currency verbatim.
        """

        product_id = rules.as_text(line.get("product_id"))
        sku = rules.as_text(line.get("sku"))
        currency = rules.as_text(order.get("currency"))

        product: dict[str, Any] | None = None
        if product_id:
            found = self.store.get(product_id)
            if found and found.get("collection") == vocab.PRODUCT_COLLECTION:
                product = {**dict(found.get("data") or {}), "id": found["id"]}
        if product is None and sku:
            rows = self.store.find(vocab.PRODUCT_COLLECTION, {"sku": sku}, limit=1)
            if rows:
                product = {**dict(rows[0].get("data") or {}), "id": rows[0]["id"]}

        price_list = rules.as_text(order.get("price_list") or order.get("price_book_id"))
        book: dict[str, Any] | None = None
        if price_list:
            found = self.store.get(price_list)
            if found and found.get("collection") == vocab.PRICE_BOOK_COLLECTION:
                book = dict(found.get("data") or {})

        book_item: dict[str, Any] | None = None
        if price_list and product_id:
            rows = self.store.find(
                vocab.PRICE_BOOK_ITEM_COLLECTION,
                {"price_book_id": price_list, "product_id": product_id},
                limit=1,
            )
            if rows:
                book_item = dict(rows[0].get("data") or {})

        quantity = rules.as_number(line.get("quantity"), 0.0)
        if quantity <= 0:
            return None
        try:
            return effective_unit_price(
                product,
                quantity,
                price_book=book,
                price_book_item=book_item,
                currency=currency,
            )
        except CatalogueRefusal:
            return None

    def reprice(
        self,
        order_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Apply the current price list to the order's lines.

        A locked order is not repriced: the edit is recorded on the ledger and
        applied to nothing. An overridden line is never touched, in either mode.
        """

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        rules.assert_order_open(data)
        lines = self._order_lines(order_id)
        prices = {
            rules.as_text(line.get("id")): self._price_for(data, line)
            for line in lines
            if not line.get("price_overridden")
        }
        plan = rules.reprice_plan(data, lines, {k: v for k, v in prices.items() if v})

        for applied in plan["applied"]:
            line_id = rules.as_text(applied.get("id"))
            self.store.update(
                line_id,
                {k: v for k, v in applied.items() if k != "id"},
                actor=actor,
                source=source,
            )

        if plan["applied"]:
            self._recompute_totals(order_id, actor=actor, source=source)

        ledger = self._ledger(
            kind="reprice",
            order_id=order_id,
            quote_id=rules.as_text(data.get("source_quote_id")) or None,
            reason=plan["reason"] or "",
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
            extra={
                "detail": {
                    "applied": plan["applied_count"],
                    "skipped": plan["skipped_count"],
                    "locked": plan["locked"],
                }
            },
        )
        return {
            "order": self.order(order_id),
            "applied_lines": plan["applied_count"],
            "skipped_lines": plan["skipped_count"],
            "reason": plan["reason"],
            "ledger_id": ledger["id"],
        }

    def _recompute_totals(self, order_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        lines = self._order_lines(order_id)
        totals = rules.order_totals(lines, data.get("payment_schedule") or [], self.today())
        return self.store.update(
            order_id,
            {"totals": totals, "total_contract_value": totals["total_contract_value"]},
            actor=actor,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # Order lines
    # ------------------------------------------------------------------ #

    def add_line(
        self,
        order_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Add a write-in product to the order.

        A write-in is addable only while Prices Locked is No, and the line it
        creates is marked overridden, so it survives every later reprice.
        """

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        rules.assert_write_in_allowed(data)
        lines = self._order_lines(order_id)
        position = max(
            (rules.as_number(line.get("position"), -1.0) for line in lines), default=-1.0
        )
        shaped = rules.write_in_line(payload, int(position) + 1)
        shaped["order_id"] = order_id
        self.store.create(
            vocab.ORDER_LINE_COLLECTION,
            shaped,
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )
        self._recompute_totals(order_id, actor=actor, source=source)
        return self.order(order_id)

    def update_line(
        self,
        order_id: str,
        line_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Edit one order line.

        A closed order refuses the edit. A locked order refuses a price or
        discount change, because "the Price Per Unit field is locked for all the
        products in the order and is disabled for editing".
        """

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        rules.assert_order_open(data)
        line_record = self.store.get(line_id)
        if (
            not line_record
            or line_record.get("collection") != vocab.ORDER_LINE_COLLECTION
            or rules.as_text((line_record.get("data") or {}).get("order_id")) != order_id
        ):
            raise OrderNotFound(f"No order line {line_id!r} on order {order_id!r}.")

        price_fields = ("unit_price", "discount_type", "discount_value")
        if rules.is_locked(data) and any(field in patch for field in price_fields):
            raise OrderConflict(
                vocab.REASON_PRICES_LOCKED,
                "Prices are locked, so a line's price cannot be edited.",
            )

        merged = {**dict(line_record.get("data") or {}), **dict(patch)}
        if "unit_price" in patch:
            merged["price_overridden"] = True
            merged["price_source"] = rules.PRICE_FROM_MANUAL
        merged["amounts"] = line_amounts(merged)
        self.store.update(
            line_id,
            {k: v for k, v in merged.items() if k != "id"},
            actor=actor,
            source=source,
        )
        self._recompute_totals(order_id, actor=actor, source=source)
        return self.order(order_id)

    # ------------------------------------------------------------------ #
    # Fulfilment
    # ------------------------------------------------------------------ #

    def close_order(
        self,
        order_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Close the order as fulfilled or cancelled.

        The orderclose row records the fulfilment, and the order becomes
        read-only: every later write is refused with ``order_closed``.
        """

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        if rules.is_closed(data):
            raise OrderConflict(
                vocab.REASON_ORDER_CLOSED,
                "This order is already closed or fulfilled.",
            )
        status = rules.as_text(payload.get("status")) or vocab.ORDER_STATUS_FULFILLED
        close = rules.close_payload(
            data,
            status=status,
            reason=rules.as_text(payload.get("reason")),
            closed_at=self._stamp(),
            invoice_expected=rules.as_bool(payload.get("invoice_expected"), True),
        )
        self.store.create(
            vocab.ORDER_CLOSE_COLLECTION,
            {k: v for k, v in close.items() if k != "patch"},
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )
        self.store.update(order_id, close["patch"], actor=actor, source=source)
        self._ledger(
            kind="close",
            order_id=order_id,
            quote_id=rules.as_text(data.get("source_quote_id")) or None,
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
            extra={"detail": {"status": status}},
        )
        return self.order(order_id)

    # ------------------------------------------------------------------ #
    # Get Products
    # ------------------------------------------------------------------ #

    def get_products(
        self,
        order_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Copy an opportunity's products onto the order.

        The related opportunity may be kept open or closed; when it is closed,
        its final revenue is recorded on the order.
        """

        record = self._require_order(order_id)
        data = dict(record.get("data") or {})
        rules.assert_order_open(data)
        if rules.is_locked(data):
            raise OrderConflict(
                vocab.REASON_PRICES_LOCKED,
                "Prices are locked, so products cannot be added to the order.",
            )

        opportunity = (
            payload.get("opportunity")
            if isinstance(payload.get("opportunity"), Mapping)
            else payload
        )
        opportunity_id = rules.as_text(
            payload.get("opportunity_id") or (opportunity or {}).get("id")
        )
        closed = rules.opportunity_is_closed(opportunity)
        revenue = rules.final_revenue(opportunity)

        lines = self._order_lines(order_id)
        position = int(
            max((rules.as_number(line.get("position"), -1.0) for line in lines), default=-1.0)
        )
        added = 0
        for raw in rules.opportunity_lines(opportunity):
            position += 1
            shaped = rules.order_line_from_quote(
                {
                    **dict(raw),
                    "price_source": rules.as_text(raw.get("price_source"))
                    or rules.PRICE_FROM_CATALOG,
                },
                position,
            )
            shaped["order_id"] = order_id
            shaped["from_opportunity_id"] = opportunity_id or None
            self.store.create(
                vocab.ORDER_LINE_COLLECTION,
                shaped,
                room_id=record.get("room_id"),
                actor=actor,
                source=source,
            )
            added += 1

        patch: dict[str, Any] = {"products_source_opportunity_id": opportunity_id or None}
        if closed:
            patch["opportunity_closed"] = True
            patch["final_revenue"] = revenue
        if added:
            self.store.update(order_id, patch, actor=actor, source=source)
            self._recompute_totals(order_id, actor=actor, source=source)
        elif patch:
            self.store.update(order_id, patch, actor=actor, source=source)

        self._ledger(
            kind="get_products",
            order_id=order_id,
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
            extra={
                "detail": {"added": added, "opportunity_closed": closed, "final_revenue": revenue}
            },
        )
        view = self.order(order_id)
        return {
            "order": view,
            "added_lines": added,
            "opportunity_closed": closed,
            "final_revenue": view.get("final_revenue"),
        }

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        orders = self.orders(room_id=room_id, limit=1000)
        conversions = self.store.list(vocab.CONVERSION_COLLECTION, room_id=room_id, limit=1000)
        refusals: dict[str, int] = {}
        for row in conversions:
            data = dict(row.get("data") or {})
            if data.get("kind") == "conversion" and data.get("reason"):
                token = rules.as_text(data.get("reason"))
                refusals[token] = refusals.get(token, 0) + 1
        return {
            "orders": len(orders),
            "locked": sum(1 for order in orders if order.get("locked")),
            "unlocked": sum(1 for order in orders if not order.get("locked")),
            "closed": sum(1 for order in orders if order.get("closed")),
            "fulfilled": sum(
                1 for order in orders if order.get("status") == vocab.ORDER_STATUS_FULFILLED
            ),
            "cancelled": sum(
                1 for order in orders if order.get("status") == vocab.ORDER_STATUS_CANCELLED
            ),
            "line_items": sum(order.get("line_item_count", 0) for order in orders),
            "conversions": sum(
                1 for row in conversions if (row.get("data") or {}).get("kind") == "conversion"
            ),
            "refusals": refusals,
            "settings": self.settings(room_id=room_id),
        }
