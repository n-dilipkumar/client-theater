"""The workflow: currencies, price lists, quotes, and a pricing run that can refuse.

Everything in this module writes through ``RecordStore``, so every mutation writes its audit
row in the same transaction as the change. Nothing here imports ``dsr.api`` and nothing here
opens SQLite. The dependency direction is one-way: ``api -> features -> quote_currency``,
which is what lets this module be tested with a store and no server.

The event, not a poll
---------------------

The research names "the ``RetrieveExchangeRate`` message/event on the Currency table
(``RetrieveExchangeRateRequest``, \"Event: True\")", so :meth:`QuoteCurrencyEngine.price_quote`
raises that event on every recalculation trigger and stamps the answer onto the quote. The
stamp is what makes a stored ``_Base`` figure a record rather than a view: re-stamping a
currency record does not rewrite what an issued quote said it cost. Every read of a rate this
engine performs writes a ``wf089_exchange_rate_read`` row, so "when do you fetch a rate" is
answered by the data rather than by a comment.

The two refusals are total
--------------------------

A pricing run either produces every total in both currencies, or produces none and a code.
There is no shape in which some totals are present and some are missing, because a caller
cannot tell a half-priced quote from a priced one. :func:`_price` returns the refusal as
data and every money field stays absent.

What this engine reads from records it does not own
---------------------------------------------------

WF-086 provisions the quote and its line items; WF-087 provisions the price lists and price
rows. Neither is implemented, so this engine reads all four as data from its own collections
and exposes routes that create them. It never imports another feature's module, because a
feature must not import another feature. When those two land they write into the same
collections and this engine prices what it finds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from dsr.quote_currency import inferences, rules, vocabulary as vocab
from dsr.quote_currency.errors import (
    CurrencyChangeRefused,
    CurrencyRefusal,
    QuoteNotFound,
    RateUnavailable,
)
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _stamp(value: Any = None) -> str:
    """One ISO instant, in the same format the store writes."""

    moment = value if isinstance(value, datetime) else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _money(value: Any, default: str = "0") -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(default)


def _room_ref(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""

    value = data.get(vocab.ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


class QuoteCurrencyEngine:
    """The workflow, over an audited store and a clock."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ----------------------------------------------------------------- #
    # Currencies
    # ----------------------------------------------------------------- #

    def register_currency(
        self,
        iso_code: Any,
        *,
        exchange_rate: Any = None,
        currency_symbol: Any = None,
        currency_precision: Any = None,
        currency_type: Any = None,
        is_base_currency: bool = False,
        name: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Register one ``transactioncurrency`` record.

        A rate may only be supplied for a ``Custom`` record. That is the reconciliation of
        the research's two statements about ``exchangerate``: it is read-only on a quote, and
        it is writable on a currency record that exists to carry a custom rate. A Standard
        record with a supplied rate is refused rather than quietly accepted, because accepting
        it would leave two sources for one figure with no record of which won.

        Exactly one currency record may claim to be the base currency. A second claim is
        refused: "the system's default currency" is singular, and two base currencies would
        make every ``_Base`` figure ambiguous.
        """

        code = rules.normalise_iso_code(iso_code)
        kind = rules.normalise_currency_type(currency_type)
        precision = rules.validate_precision(currency_precision)
        errors: dict[str, str] = {}

        if is_base_currency and kind != vocab.CURRENCY_TYPE_STANDARD:
            raise CurrencyRefusal(
                "The base currency must be a standard currency record.",
                {
                    "currency_type": (
                        "the base currency is the organisation's own currency and its rate is "
                        "always one, so its record must be Standard."
                    )
                },
            )

        if exchange_rate not in (None, ""):
            if kind != vocab.CURRENCY_TYPE_CUSTOM:
                raise CurrencyRefusal(
                    "Only a Custom currency record accepts a stamped rate.",
                    {
                        "exchange_rate": (
                            "exchange_rate is resolved from the currency record the platform "
                            "maintains. To stamp a rate, set currency_type to Custom."
                        )
                    },
                )
            rate: float | None = rules.as_rate(exchange_rate)
        else:
            rate = None
            if kind == vocab.CURRENCY_TYPE_CUSTOM and not is_base_currency:
                errors["exchange_rate"] = (
                    "a Custom currency record is the record a deployment stamps its own rate on, "
                    "so it needs an exchange_rate."
                )

        if is_base_currency and self.base_currency(room_id=room_id) is not None:
            errors["is_base_currency"] = (
                "another currency record already claims to be the base currency."
            )

        if errors:
            raise CurrencyRefusal(
                "The currency record was refused.",
                errors,
            )

        payload: dict[str, Any] = {
            "iso_code": code,
            "currency_type": kind,
            "currency_precision": precision,
            vocab.BASE_CURRENCY_FIELD: bool(is_base_currency),
            "rate_is_writable": kind == vocab.CURRENCY_TYPE_CUSTOM,
            "recorded_at": _stamp(self._now()),
        }
        if rate is not None:
            payload["exchange_rate"] = rate
        if currency_symbol not in (None, ""):
            payload["currency_symbol"] = str(currency_symbol)
        if name not in (None, ""):
            payload["name"] = str(name)
        else:
            payload["name"] = f"{code} transaction currency"
        if room_id:
            payload[vocab.ROOM_REF] = room_id
        return self.store.create(
            vocab.CURRENCY_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def stamp_rate(
        self,
        currency_id: str,
        exchange_rate: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Stamp a custom rate on a ``Custom`` currency record.

        The only write path to ``exchange_rate`` in this workflow, and it exists because the
        research says a deployment may do it: "``exchangerate`` is writable so a custom rate can
        be stamped". A Standard record is refused here too, for the same reason it is refused
        at registration: one figure must have one source.

        Existing quotes are **not** re-priced. Their base figures were stamped against the
        rate in force when they were priced, and re-pricing them would rewrite the record of
        what an issued quote cost. The response names how many quotes now hold a base figure
        computed from a superseded rate, so the operator can decide what to do about them.
        """

        record = self.store.get(currency_id)
        if record is None or record["collection"] != vocab.CURRENCY_COLLECTION:
            raise QuoteNotFound(f"No currency record {currency_id!r}.")
        data = record["data"]
        kind = rules.normalise_currency_type(data.get("currency_type"))
        if kind != vocab.CURRENCY_TYPE_CUSTOM:
            raise CurrencyRefusal(
                "Only a Custom currency record accepts a stamped rate.",
                {
                    "exchange_rate": (
                        f"currency record {data.get('iso_code')!r} is {kind}, and its rate comes "
                        "from the platform. Set currency_type to Custom to stamp a rate."
                    )
                },
            )
        rate = rules.as_rate(exchange_rate)
        updated = self.store.update(
            currency_id,
            {"exchange_rate": rate, "rate_stamped_at": _stamp(self._now())},
            actor=actor,
            source=source,
        )
        superseded = self.quotes_using_currency(currency_id, room_id=room_id)
        return {
            "currency": self._project_currency(updated, room_id=room_id),
            "previous_rate": data.get("exchange_rate"),
            "exchange_rate": rate,
            "quotes_holding_the_superseded_rate": len(superseded),
            "quotes_holding_the_superseded_rate_ids": [row["id"] for row in superseded],
            "note": (
                "Existing quotes keep the base figures they were priced with. A quote is a "
                "record of what was priced, and a re-stamped currency rate does not rewrite it."
            ),
            "recalculate_to_apply_the_new_rate": list(vocab.RECALCULATION_TRIGGERS),
        }

    def base_currency(self, *, room_id: str | None = None) -> dict[str, Any] | None:
        """The one currency record this deployment reports in.

        Scoped read first, whole table second, for the same reason
        :meth:`currency_by_code` does it: the base currency belongs to the organisation, so a
        room that has not registered one still reports in it.
        """

        for record in self._currencies(room_id=room_id):
            if record["data"].get(vocab.BASE_CURRENCY_FIELD):
                return record
        if room_id:
            for record in self._currencies():
                if record["data"].get(vocab.BASE_CURRENCY_FIELD):
                    return record
        return None

    def currencies(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every currency record, projected."""

        base = self.base_currency(room_id=room_id)
        base_code = (base or {}).get("data", {}).get("iso_code")
        return [
            self._project_currency(record, room_id=room_id, base_iso_code=base_code)
            for record in self._currencies(room_id=room_id)
        ]

    def _currencies(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            return self.store.list(vocab.CURRENCY_COLLECTION, room_id=room_id, limit=500)
        return self.store.list(vocab.CURRENCY_COLLECTION, limit=500)

    def currency_by_code(
        self, iso_code: str, *, room_id: str | None = None
    ) -> dict[str, Any] | None:
        """One currency record by ISO code.

        The lookup falls back to the whole table when a room-scoped read finds nothing, and
        that is not a convenience: ``transactioncurrency`` is an organisation table, so a
        currency a deployment trades in is available to every room rather than registered
        afresh per room. Scoping the read first keeps a room-scoped query fast, and the
        fallback is what stops the second room in a deployment from having no base currency.
        """

        code = str(iso_code or "").strip().upper()
        for record in self._currencies(room_id=room_id):
            if str(record["data"].get("iso_code") or "").strip().upper() == code:
                return record
        if room_id:
            for record in self._currencies():
                if str(record["data"].get("iso_code") or "").strip().upper() == code:
                    return record
        return None

    def _project_currency(
        self,
        record: Mapping[str, Any],
        *,
        room_id: str | None = None,
        base_iso_code: str | None = None,
    ) -> dict[str, Any]:
        data = record["data"]
        kind = rules.normalise_currency_type(data.get("currency_type"))
        precision = rules.validate_precision(data.get("currency_precision"))
        is_base = bool(data.get(vocab.BASE_CURRENCY_FIELD))
        code = str(data.get("iso_code") or "")
        projected: dict[str, Any] = {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "iso_code": code,
            "name": data.get("name"),
            "currency_symbol": data.get("currency_symbol"),
            "currency_precision": precision,
            "currency_type": kind,
            "currency_type_label": vocab.CURRENCY_TYPE_LABELS.get(kind, kind),
            "rate_is_writable": kind == vocab.CURRENCY_TYPE_CUSTOM,
            "exchange_rate": vocab.IDENTITY_RATE if is_base else data.get("exchange_rate"),
            "exchange_rate_present": is_base or data.get("exchange_rate") not in (None, ""),
            "is_base_currency": is_base,
            "recorded_at": data.get("recorded_at"),
            "rate_stamped_at": data.get("rate_stamped_at"),
            "round_trip_example": self._round_trip(data, precision, is_base),
        }
        if base_iso_code and code != str(base_iso_code).upper():
            projected["base_iso_code"] = str(base_iso_code).upper()
        return projected

    @staticmethod
    def _round_trip(data: Mapping[str, Any], precision: int, is_base: bool) -> dict[str, Any]:
        """What 100 units of this currency are worth in the base currency.

        One worked example per currency record, because a rate is a ratio and a ratio is
        much easier to check against a real amount than against another ratio.
        """

        if is_base:
            return {
                "amount": "100",
                "currency": data.get("iso_code"),
                "in_base": "100",
                "note": "The base currency converts to itself at a rate of one.",
            }
        rate = data.get("exchange_rate")
        if rate in (None, ""):
            return {
                "amount": "100",
                "currency": data.get("iso_code"),
                "in_base": None,
                "note": "This currency record carries no rate, so nothing converts yet.",
            }
        amount = rules.MONEY("100")
        return {
            "amount": "100",
            "currency": data.get("iso_code"),
            "in_base": str(rules.to_base(amount, float(rate), precision)),
            "note": "One hundred units converted at this record's rate.",
        }

    # ----------------------------------------------------------------- #
    # Price lists and price rows
    # ----------------------------------------------------------------- #

    def create_price_list(
        self,
        name: Any,
        iso_code: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create one price list in one currency.

        A price list carries exactly one currency. That is the HubSpot sentence that survives
        the choice of the Dynamics model, and it is what makes code 34 reachable: two
        currencies means two price lists, and a caller can pick the wrong one.

        The currency must be a registered currency record. A price list in a currency the
        deployment has no record of has no rate and no precision, so it is refused here rather
        than producing a quote that cannot be priced.
        """

        if name in (None, "") or not str(name).strip():
            raise CurrencyRefusal(
                "A price list needs a name.",
                {"name": "A price list needs a name."},
            )
        code = rules.normalise_iso_code(iso_code)
        currency = self.currency_by_code(code, room_id=room_id)
        if currency is None:
            raise CurrencyRefusal(
                f"No currency record for {code!r}.",
                {
                    "iso_code": (
                        f"a price list in {code!r} needs a registered currency record, because "
                        "that record holds the precision and the rate."
                    )
                },
            )
        payload = {
            "name": str(name).strip(),
            "iso_code": code,
            "currency_record_id": currency["id"],
            "currency_precision": currency["data"].get("currency_precision"),
            "single_currency": vocab.PRICE_LIST_SINGLE_CURRENCY,
            "created_at": _stamp(self._now()),
        }
        if room_id:
            payload[vocab.ROOM_REF] = room_id
        record = self.store.create(
            vocab.PRICE_LIST_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._project_price_list(record, room_id=room_id)

    def create_price_item(
        self,
        price_list_id: str,
        product_code: Any,
        unit_price: Any,
        *,
        tier: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create one price row: a product priced on one price list.

        This is the ``pricelevelproduct`` row the research says unit prices resolve from. The
        price list fixes the currency, so the row carries no currency of its own and cannot
        disagree with its list.
        """

        price_list = self._price_list_record(price_list_id)
        if product_code in (None, "") or not str(product_code).strip():
            raise CurrencyRefusal(
                "A price row needs a product code.",
                {"product_code": "A price row needs a product code."},
            )
        if unit_price in (None, ""):
            raise CurrencyRefusal(
                "A price row needs a price.",
                {
                    "unit_price": (
                        "a price row that carries no price would resolve every quote line that "
                        "uses it to nothing, and a price of zero must be written as 0 so it is a "
                        "decision rather than an omission."
                    )
                },
            )
        precision = rules.validate_precision(price_list["data"].get("currency_precision"))
        amount = rules.as_money(unit_price, precision, "unit_price")
        payload: dict[str, Any] = {
            "price_list_id": price_list_id,
            "product_code": str(product_code).strip(),
            "unit_price": str(amount),
            "iso_code": price_list["data"].get("iso_code"),
            "created_at": _stamp(self._now()),
        }
        if tier not in (None, ""):
            payload["tier"] = str(tier)
        if room_id:
            payload[vocab.ROOM_REF] = room_id
        record = self.store.create(
            vocab.PRICE_ITEM_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._project_price_item(record, room_id=room_id)

    def price_lists(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            self._project_price_list(record, room_id=room_id)
            for record in self._price_list_records(room_id=room_id)
        ]

    def price_items(
        self, price_list_id: str | None = None, *, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = (
            self.store.list(vocab.PRICE_ITEM_COLLECTION, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(vocab.PRICE_ITEM_COLLECTION, limit=1000)
        )
        return [
            self._project_price_item(record, room_id=room_id)
            for record in rows
            if price_list_id is None
            or str(record["data"].get("price_list_id") or "") == str(price_list_id)
        ]

    def _price_list_records(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            return self.store.list(vocab.PRICE_LIST_COLLECTION, room_id=room_id, limit=500)
        return self.store.list(vocab.PRICE_LIST_COLLECTION, limit=500)

    def _price_list_record(self, price_list_id: str) -> dict[str, Any]:
        record = self.store.get(price_list_id)
        if record is None or record["collection"] != vocab.PRICE_LIST_COLLECTION:
            raise QuoteNotFound(f"No price list {price_list_id!r}.")
        return record

    def _project_price_list(
        self, record: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        data = record["data"]
        rows = self.price_items(record["id"], room_id=_room_ref(data, record))
        return {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "name": data.get("name"),
            "iso_code": data.get("iso_code"),
            "currency_precision": data.get("currency_precision"),
            "single_currency": bool(data.get("single_currency", True)),
            "price_rows": len(rows),
            "products": sorted({str(row["product_code"]) for row in rows}),
            "created_at": data.get("created_at"),
            "single_currency_reason": vocab.PRICE_LIST_SINGLE_CURRENCY_REASON,
        }

    def _project_price_item(
        self, record: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "price_list_id": data.get("price_list_id"),
            "product_code": data.get("product_code"),
            "unit_price": data.get("unit_price"),
            "iso_code": data.get("iso_code"),
            "tier": data.get("tier"),
            "created_at": data.get("created_at"),
        }

    # ----------------------------------------------------------------- #
    # Quotes and line items
    # ----------------------------------------------------------------- #

    def create_quote(
        self,
        name: Any,
        iso_code: Any,
        *,
        price_list_id: Any = None,
        deal_iso_code: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create a quote header stamped with a transaction currency.

        ``deal_iso_code`` records the HubSpot inheritance rule without implementing a deal:
        "Quotes created from deals match the associated deal's *Currency* property". It is
        stored beside the header currency and reported, so a reader can see a quote whose
        currency came from the deal rather than from an operator. It never overrides the
        explicit currency.

        A price list in a different currency is allowed here. That is deliberate: the research
        describes the platform refusing and setting ``pricingerrorcode`` when the combination
        is wrong, which is only observable if the wrong combination can exist. The refusal is
        :func:`~dsr.quote_currency.rules.price_quote`, at pricing time, as code 34.
        """

        if name in (None, "") or not str(name).strip():
            raise CurrencyRefusal(
                "A quote needs a name.",
                {"name": "A quote needs a name."},
            )
        code = rules.normalise_iso_code(iso_code)
        currency = self.currency_by_code(code, room_id=room_id)
        if currency is None:
            raise CurrencyRefusal(
                f"No currency record for {code!r}.",
                {
                    "iso_code": (
                        f"a quote in {code!r} needs a registered currency record, because that "
                        "record holds the precision and the rate."
                    )
                },
            )
        price_list_iso = None
        if price_list_id not in (None, ""):
            price_list = self._price_list_record(str(price_list_id))
            price_list_iso = price_list["data"].get("iso_code")

        payload: dict[str, Any] = {
            "name": str(name).strip(),
            vocab.TRANSACTION_CURRENCY_FIELD: code,
            "currency_record_id": currency["id"],
            "currency_precision": rules.validate_precision(
                currency["data"].get("currency_precision")
            ),
            vocab.PRICE_LIST_FIELD: str(price_list_id) if price_list_id else None,
            "price_list_iso_code": price_list_iso,
            "price_list_currency_matches": (
                None if price_list_iso is None else price_list_iso == code
            ),
            vocab.RATE_FIELD: None,
            "base_totals_present": False,
            "priced_at": None,
            "pricing_outcome": None,
            "pricing_error_code": None,
            "created_at": _stamp(self._now()),
        }
        if deal_iso_code not in (None, ""):
            payload["deal_iso_code"] = rules.normalise_iso_code(deal_iso_code, "deal_iso_code")
            payload["currency_inherited_from_deal"] = payload["deal_iso_code"] == code
        if room_id:
            payload[vocab.ROOM_REF] = room_id
        record = self.store.create(
            vocab.QUOTE_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._project_quote(record, room_id=room_id)

    def add_line(
        self,
        quote_id: str,
        product_code: Any,
        quantity: Any = 1,
        *,
        discount_amount: Any = None,
        tax_amount: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Add one line item to a quote.

        No unit price is taken. The research says unit prices resolve from the price row in
        the transaction currency, so a line item is a product and a quantity, and the price is
        read at pricing time. That is what makes "product add" a recalculation trigger: adding
        a line changes what the quote costs without any caller typing a price.
        """

        quote = self._quote_record(quote_id)
        if product_code in (None, "") or not str(product_code).strip():
            raise CurrencyRefusal(
                "A line item needs a product code.",
                {"product_code": "A line item needs a product code."},
            )
        precision = rules.validate_precision(quote["data"].get("currency_precision"))
        payload: dict[str, Any] = {
            "quote_id": quote_id,
            "product_code": str(product_code).strip(),
            "quantity": str(rules.as_money(quantity, 6, "quantity")),
            "iso_code": quote["data"].get(vocab.TRANSACTION_CURRENCY_FIELD),
            "created_at": _stamp(self._now()),
        }
        for key, value in (("discount_amount", discount_amount), ("tax_amount", tax_amount)):
            if value not in (None, ""):
                payload[key] = str(rules.as_money(value, precision, key))
        if room_id:
            payload[vocab.ROOM_REF] = room_id
        record = self.store.create(
            vocab.QUOTE_LINE_COLLECTION,
            payload,
            room_id=_room_ref(quote["data"], quote) or room_id,
            actor=actor,
            source=source,
        )
        return self._project_line(record, room_id=room_id)

    def lines(self, quote_id: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            self._project_line(record, room_id=room_id)
            for record in self._line_records(quote_id, room_id=room_id)
        ]

    def _line_records(self, quote_id: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = (
            self.store.list(vocab.QUOTE_LINE_COLLECTION, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(vocab.QUOTE_LINE_COLLECTION, limit=1000)
        )
        return [
            record for record in rows if str(record["data"].get("quote_id") or "") == str(quote_id)
        ]

    def _project_line(
        self, record: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "quote_id": data.get("quote_id"),
            "product_code": data.get("product_code"),
            "quantity": data.get("quantity"),
            "iso_code": data.get("iso_code"),
            "discount_amount": data.get("discount_amount"),
            "tax_amount": data.get("tax_amount"),
            "unit_price": None,
            "unit_price_note": (
                "The unit price is resolved from the price list row at pricing time and is not "
                "stored on the line."
            ),
            "created_at": data.get("created_at"),
        }

    # ----------------------------------------------------------------- #
    # The currency-in-place constraint
    # ----------------------------------------------------------------- #

    def change_currency(
        self,
        quote_id: str,
        iso_code: Any,
        *,
        price_list_id: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Change a quote's transaction currency, or refuse because it has line items.

        The refusal is the sourced constraint: "You can't change the currency of the base
        record (in this case, an quote), unless you remove all the line items associated with
        the record."

        A quote with no line items may be re-stamped, and its previously computed base figures
        are cleared with it. A quote with line items is refused with 409, naming the line count
        and the remedy, because the alternative is either silent deletion of the quote's
        contents or a quote whose line items are priced in one currency under a header in
        another.
        """

        quote = self._quote_record(quote_id)
        code = rules.normalise_iso_code(iso_code)
        currency = self.currency_by_code(code, room_id=room_id)
        if currency is None:
            raise CurrencyRefusal(
                f"No currency record for {code!r}.",
                {
                    "iso_code": (
                        f"a quote cannot be stamped in {code!r} because no currency record "
                        "registers it."
                    )
                },
            )
        existing = self._line_records(quote_id, room_id=_room_ref(quote["data"], quote))
        if existing:
            raise CurrencyChangeRefused(
                "The currency cannot change while the quote holds line items.",
                quote_id=quote_id,
                line_count=len(existing),
            )

        patch: dict[str, Any] = {
            vocab.TRANSACTION_CURRENCY_FIELD: code,
            "currency_record_id": currency["id"],
            "currency_precision": rules.validate_precision(
                currency["data"].get("currency_precision")
            ),
            "currency_changed_at": _stamp(self._now()),
            vocab.RATE_FIELD: None,
            "base_totals_present": False,
            "priced_at": None,
            "pricing_outcome": None,
            "pricing_error_code": None,
        }
        target_list = price_list_id or quote["data"].get(vocab.PRICE_LIST_FIELD)
        if target_list:
            price_list = self._price_list_record(str(target_list))
            patch[vocab.PRICE_LIST_FIELD] = str(target_list)
            patch["price_list_iso_code"] = price_list["data"].get("iso_code")
            patch["price_list_currency_matches"] = price_list["data"].get("iso_code") == code
        for stale in list(vocab.TRANSACTION_TOTALS) + list(vocab.BASE_TOTALS):
            patch[stale] = None
        updated = self.store.update(quote_id, patch, actor=actor, source=source)
        return self._project_quote(updated, room_id=room_id)

    # ----------------------------------------------------------------- #
    # Pricing
    # ----------------------------------------------------------------- #

    def price_quote(
        self,
        quote_id: str,
        *,
        trigger: str = vocab.TRIGGER_OPEN,
        discount: Any = None,
        tax: Any = None,
        freight: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Run the pricing, and record the run whether it priced or refused.

        One transaction's worth of work:

        1. Read the quote, its line items, its price list and its price rows.
        2. Raise the ``RetrieveExchangeRate`` event and stamp the rate onto the quote.
        3. Price every line in the transaction currency, then derive every base figure.
        4. Write a ``wf089_quote_pricing`` row recording the trigger, the rate, the outcome
           and either the totals or the refusal code.

        Step 4 happens for a refusal too. "Both success and failure produce audit actions" is
        the principle this product already applies to a failed verification, and a refusal that
        left no row would be invisible to anybody reading the audit log.
        """

        if trigger not in vocab.RECALCULATION_TRIGGERS:
            raise CurrencyRefusal(
                f"{trigger!r} is not a recalculation trigger.",
                {"trigger": (f"trigger must be one of {', '.join(vocab.RECALCULATION_TRIGGERS)}.")},
            )

        quote = self._quote_record(quote_id)
        quote_data = quote["data"]
        room = _room_ref(quote_data, quote) or room_id
        precision = rules.validate_precision(quote_data.get("currency_precision"))
        header_iso = str(quote_data.get(vocab.TRANSACTION_CURRENCY_FIELD) or "")

        base = self.base_currency(room_id=room) or self.base_currency()
        base_code = str((base or {}).get("data", {}).get("iso_code") or "")
        base_precision = rules.validate_precision(
            (base or {}).get("data", {}).get("currency_precision"), "base_currency_precision"
        )

        currency_record = self.currency_by_code(header_iso, room_id=room) or self.currency_by_code(
            header_iso
        )
        rate_resolution = rules.resolve_rate(
            (currency_record or {}).get("data"),
            transaction_iso_code=header_iso,
            base_iso_code=base_code,
        )

        price_list_id = str(quote_data.get(vocab.PRICE_LIST_FIELD) or "")
        price_list_iso = None
        if price_list_id:
            try:
                price_list_iso = self._price_list_record(price_list_id)["data"].get("iso_code")
            except QuoteNotFound:
                raise RateUnavailable(
                    f"The quote names price list {price_list_id!r}, which no longer exists.",
                    iso_code=header_iso,
                ) from None

        # The rate event. Every run raises it, so "when do you fetch a rate" is answered by
        # a row in the store rather than by a comment.
        rate_read = self.store.create(
            vocab.RATE_READ_COLLECTION,
            {
                "quote_id": quote_id,
                "iso_code": header_iso,
                "base_iso_code": base_code,
                "rate": rate_resolution.rate,
                "rate_source": rate_resolution.source,
                "event": vocab.RATE_EVENT,
                "trigger": trigger,
                "read_at": _stamp(self._now()),
                vocab.ROOM_REF: room,
            },
            room_id=room,
            actor=actor,
            source=source,
        )

        lines = self._line_records(quote_id, room_id=room)
        price_items = self.price_items(price_list_id or None, room_id=room)

        if price_list_id:
            result = rules.price_quote(
                [record["data"] for record in lines],
                price_items,
                quote_id=quote_id,
                price_list_id=price_list_id,
                price_list_iso_code=str(price_list_iso or ""),
                header_iso_code=header_iso,
                base_iso_code=base_code,
                base_precision=base_precision,
                rate=rate_resolution.rate,
                precision=precision,
                discount=_money(discount),
                tax=_money(tax),
                freight=_money(freight),
            )
        else:
            result = rules.refuse_missing_transaction_currency_price(
                product_code="(no price list selected)",
                price_list_id="(none)",
                quote_id=quote_id,
            ).to_dict()

        stamp: dict[str, Any] = {
            vocab.RATE_FIELD: rate_resolution.rate,
            vocab.STAMPED_RATE_FIELD: rate_resolution.rate,
            "rate_source": rate_resolution.source,
            "rate_read_id": rate_read["id"],
            "rate_read_at": rate_read["data"]["read_at"],
            "base_iso_code": base_code,
            "recalculation_trigger": trigger,
            "priced_at": _stamp(self._now()),
            "pricing_outcome": result["outcome"],
            "pricing_error_code": result.get("pricing_error_code"),
            "pricing_error": result.get("pricing_error"),
            "authoritative": vocab.AUTHORITATIVE,
            "priced_by": actor,
        }

        if result["outcome"] == vocab.OUTCOME_PRICED:
            stamp.update(result["totals"])
            stamp.update(result["totals_base"])
            stamp["base_totals_present"] = True
        else:
            # A refusal clears every figure rather than leaving a stale one behind. Half a
            # quote's totals is worse than none, because it reads as priced.
            for name in list(vocab.TRANSACTION_TOTALS) + list(vocab.BASE_TOTALS):
                stamp[name] = None
            stamp["base_totals_present"] = False

        updated = self.store.update(quote_id, stamp, actor=actor, source=source)
        run = self.store.create(
            vocab.PRICING_COLLECTION,
            {
                "quote_id": quote_id,
                "outcome": result["outcome"],
                "pricing_error_code": result.get("pricing_error_code"),
                "pricing_error": result.get("pricing_error"),
                "trigger": trigger,
                "iso_code": header_iso,
                "base_iso_code": base_code,
                "price_list_id": price_list_id or None,
                "price_list_iso_code": price_list_iso,
                "rate": rate_resolution.rate,
                "rate_source": rate_resolution.source,
                "rate_event": vocab.RATE_EVENT,
                "line_count": len(lines),
                "totals": result.get("totals"),
                "totals_base": result.get("totals_base"),
                "lines": result.get("lines"),
                "detail": result.get("detail"),
                "ran_at": _stamp(self._now()),
                vocab.ROOM_REF: room,
            },
            room_id=room,
            actor=actor,
            source=source,
        )

        projected = self._project_quote(updated, room_id=room_id)
        return {
            "quote": projected,
            "run": {
                "id": run["id"],
                "outcome": result["outcome"],
                "pricing_error_code": result.get("pricing_error_code"),
                "pricing_error": result.get("pricing_error"),
                "detail": result.get("detail"),
                "trigger": trigger,
                "trigger_label": vocab.RECALCULATION_TRIGGER_LABELS.get(trigger, trigger),
                "rate": rate_resolution.rate,
                "rate_source": rate_resolution.source,
                "rate_event": vocab.RATE_EVENT,
                "rate_read_at": rate_read["data"]["read_at"],
                "iso_code": header_iso,
                "base_iso_code": base_code,
                "line_count": len(lines),
                "lines": result.get("lines", []),
                "totals": result.get("totals"),
                "totals_base": result.get("totals_base"),
                "ran_at": run["data"]["ran_at"],
            },
            "refusal": result if result["outcome"] == vocab.OUTCOME_REFUSED else None,
            "rate": rate_resolution.to_dict(),
            "authoritative": vocab.AUTHORITATIVE,
        }

    def pricing_runs(self, quote_id: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = (
            self.store.list(vocab.PRICING_COLLECTION, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(vocab.PRICING_COLLECTION, limit=1000)
        )
        runs = [
            self._project_run(record, room_id=room_id)
            for record in rows
            if str(record["data"].get("quote_id") or "") == str(quote_id)
        ]
        runs.sort(key=lambda row: str(row.get("ran_at") or ""), reverse=True)
        return runs

    def rate_reads(
        self, quote_id: str | None = None, *, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every rate this deployment read, newest first. The answer to "when do you fetch"."""

        rows = (
            self.store.list(vocab.RATE_READ_COLLECTION, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(vocab.RATE_READ_COLLECTION, limit=1000)
        )
        reads = [
            {
                "id": record["id"],
                "quote_id": record["data"].get("quote_id"),
                "iso_code": record["data"].get("iso_code"),
                "base_iso_code": record["data"].get("base_iso_code"),
                "rate": record["data"].get("rate"),
                "rate_source": record["data"].get("rate_source"),
                "event": record["data"].get("event"),
                "trigger": record["data"].get("trigger"),
                "trigger_label": vocab.RECALCULATION_TRIGGER_LABELS.get(
                    str(record["data"].get("trigger")), str(record["data"].get("trigger"))
                ),
                "read_at": record["data"].get("read_at"),
            }
            for record in rows
            if quote_id is None or str(record["data"].get("quote_id") or "") == str(quote_id)
        ]
        reads.sort(key=lambda row: str(row.get("read_at") or ""), reverse=True)
        return reads

    def _project_run(
        self, record: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "quote_id": data.get("quote_id"),
            "outcome": data.get("outcome"),
            "pricing_error_code": data.get("pricing_error_code"),
            "pricing_error": data.get("pricing_error"),
            "detail": data.get("detail"),
            "trigger": data.get("trigger"),
            "trigger_label": vocab.RECALCULATION_TRIGGER_LABELS.get(
                str(data.get("trigger")), str(data.get("trigger"))
            ),
            "iso_code": data.get("iso_code"),
            "base_iso_code": data.get("base_iso_code"),
            "price_list_id": data.get("price_list_id"),
            "price_list_iso_code": data.get("price_list_iso_code"),
            "rate": data.get("rate"),
            "rate_source": data.get("rate_source"),
            "rate_event": data.get("rate_event"),
            "line_count": data.get("line_count"),
            "totals": data.get("totals"),
            "totals_base": data.get("totals_base"),
            "lines": data.get("lines"),
            "ran_at": data.get("ran_at"),
        }

    # ----------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------- #

    def quote(self, quote_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._quote_record(quote_id)
        return self._project_quote(record, room_id=room_id)

    def quotes(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            self._project_quote(record, room_id=room_id)
            for record in self._quote_records(room_id=room_id)
        ]

    def quotes_using_currency(
        self, currency_id: str, *, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            record
            for record in self._quote_records(room_id=room_id)
            if str(record["data"].get("currency_record_id") or "") == str(currency_id)
        ]

    def _quote_records(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            return self.store.list(vocab.QUOTE_COLLECTION, room_id=room_id, limit=500)
        return self.store.list(vocab.QUOTE_COLLECTION, limit=500)

    def _quote_record(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record["collection"] != vocab.QUOTE_COLLECTION:
            raise QuoteNotFound(f"No quote {quote_id!r}.")
        return record

    def _project_quote(
        self, record: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        data = record["data"]
        lines = self._line_records(record["id"], room_id=_room_ref(data, record))
        projected: dict[str, Any] = {
            "id": record["id"],
            "room_id": _room_ref(data, record),
            "name": data.get("name"),
            vocab.TRANSACTION_CURRENCY_FIELD: data.get(vocab.TRANSACTION_CURRENCY_FIELD),
            "iso_code": data.get(vocab.TRANSACTION_CURRENCY_FIELD),
            "currency_precision": data.get("currency_precision"),
            # The currency record this quote was stamped against, so a client can go from a
            # quote to the record whose rate priced it without searching the currency table
            # for a matching code. A quote whose record was deleted still carries the id, and
            # a stamp against it is then a 404 rather than a silent second rate.
            "currency_record_id": data.get("currency_record_id"),
            vocab.PRICE_LIST_FIELD: data.get(vocab.PRICE_LIST_FIELD),
            "price_list_iso_code": data.get("price_list_iso_code"),
            "price_list_currency_matches": data.get("price_list_currency_matches"),
            vocab.RATE_FIELD: data.get(vocab.RATE_FIELD),
            vocab.STAMPED_RATE_FIELD: data.get(vocab.STAMPED_RATE_FIELD),
            "rate_source": data.get("rate_source"),
            "rate_read_at": data.get("rate_read_at"),
            "base_iso_code": data.get("base_iso_code"),
            "recalculation_trigger": data.get("recalculation_trigger"),
            "base_totals_present": bool(data.get("base_totals_present")),
            "priced_at": data.get("priced_at"),
            "pricing_outcome": data.get("pricing_outcome"),
            "pricing_error_code": data.get("pricing_error_code"),
            "pricing_error": data.get("pricing_error"),
            "authoritative": vocab.AUTHORITATIVE,
            "authoritative_reason": vocab.AUTHORITATIVE_REASON,
            "line_count": len(lines),
            "lines": [self._project_line(row, room_id=room_id) for row in lines],
            "totals": {name: data.get(name) for name in vocab.TRANSACTION_TOTALS},
            "totals_base": {name: data.get(name) for name in vocab.BASE_TOTALS},
            "currency_change_requires_no_lines": vocab.CURRENCY_CHANGE_REQUIRES_NO_LINES,
            "created_at": data.get("created_at"),
            "currency_changed_at": data.get("currency_changed_at"),
            "deal_iso_code": data.get("deal_iso_code"),
            "currency_inherited_from_deal": data.get("currency_inherited_from_deal"),
        }
        return projected

    # ----------------------------------------------------------------- #
    # The board
    # ----------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers, read back from the store. Reads only, no audit row.

        ``refused_quotes`` is beside ``priced_quotes`` rather than folded into a total,
        because a refusal is the state the specification asks a reviewer to be able to see and
        a count that added them together would hide which is which.
        """

        quotes = self.quotes(room_id=room_id)
        currencies = self.currencies(room_id=room_id)
        base = self.base_currency(room_id=room_id)
        base_data = dict((base or {}).get("data") or {})
        price_lists = self.price_lists(room_id=room_id)
        runs = self._runs(room_id=room_id)

        priced = [row for row in quotes if row["pricing_outcome"] == vocab.OUTCOME_PRICED]
        refused = [row for row in quotes if row["pricing_outcome"] == vocab.OUTCOME_REFUSED]
        unpriced = [row for row in quotes if row["pricing_outcome"] is None]

        by_code: dict[str, int] = {}
        for row in quotes:
            code = str(row.get(vocab.TRANSACTION_CURRENCY_FIELD) or "(none)")
            by_code[code] = by_code.get(code, 0) + 1

        code_counts = {
            code: sum(1 for row in refused if row.get("pricing_error_code") == code)
            for code in vocab.PRICING_ERROR_CODES
        }

        return {
            "currencies": len(currencies),
            "custom_currencies": sum(
                1 for row in currencies if row["currency_type"] == vocab.CURRENCY_TYPE_CUSTOM
            ),
            "base_currency": base_data.get("iso_code"),
            "base_currency_record_id": (base or {}).get("id"),
            "price_lists": len(price_lists),
            "price_rows": len(self.price_items(room_id=room_id)),
            "quotes": len(quotes),
            "priced_quotes": len(priced),
            "refused_quotes": len(refused),
            "unpriced_quotes": len(unpriced),
            "quotes_by_currency": dict(sorted(by_code.items())),
            "refusals_by_code": dict(sorted(code_counts.items())),
            "pricing_runs": len(runs),
            "rate_reads": len(self.rate_reads(room_id=room_id)),
            "totals_in_base_currency": self._sum_base(
                quotes, str((base or {}).get("data", {}).get("iso_code") or "") or None
            ),
            "product_model": vocab.PRODUCT_MODEL,
            "authoritative": vocab.AUTHORITATIVE,
            "authoritative_reason": vocab.AUTHORITATIVE_REASON,
            "refusal_is_outcome": vocab.REFUSAL_IS_OUTCOME_NOT_ERROR,
            "rate_event": vocab.RATE_EVENT,
            "rate_event_note": vocab.RATE_EVENT_NOTE,
            "recalculation_triggers": list(vocab.RECALCULATION_TRIGGERS),
            "currency_change_requires_no_lines": vocab.CURRENCY_CHANGE_REQUIRES_NO_LINES,
            "outcomes": list(vocab.PRICING_OUTCOMES),
            "pricing_error_codes": dict(vocab.PRICING_ERROR_CODES),
            "decision_count": inferences.count(),
            "jev_audit_ids": inferences.jev_audit_ids(),
        }

    def _runs(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = (
            self.store.list(vocab.PRICING_COLLECTION, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(vocab.PRICING_COLLECTION, limit=1000)
        )
        return rows

    @staticmethod
    def _sum_base(quotes: Sequence[Mapping[str, Any]], base_iso_code: str | None) -> dict[str, Any]:
        """What the priced quotes are worth together, in the base currency.

        Only priced quotes are summed. A refused quote has no figures, and adding a null as a
        zero would put a refused quote into a total as though it cost nothing.
        """

        field = "totalamount_base"
        total = Decimal("0")
        counted = 0
        skipped = 0
        for row in quotes:
            if row.get("pricing_outcome") != vocab.OUTCOME_PRICED:
                continue
            value = (row.get("totals_base") or {}).get(field)
            if value in (None, ""):
                skipped += 1
                continue
            total += Decimal(str(value))
            counted += 1
        return {
            "field": field,
            "iso_code": base_iso_code,
            "amount": str(total),
            "quotes_counted": counted,
            "quotes_without_figures": skipped,
            "note": (
                "Only priced quotes are summed. A refused quote contributes nothing, and is "
                "reported as excluded rather than counted as zero."
            ),
        }


def describe_decisions() -> list[dict[str, Any]]:
    """The judgement calls, for ``GET /decisions``."""

    return inferences.describe()


def describe_vocabulary() -> dict[str, Any]:
    """The researched terms, for ``GET /vocabulary``."""

    return vocab.describe()
