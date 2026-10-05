"""The reads and the writes: publish, accept, charge, invoice, subscribe, tax id, void.

Everything the rules decide, this module carries out against the store, and nothing in it
imports HTTP. The rules in :mod:`dsr.quote_payment.rules` are pure, so each method below is one
call into them and one or more store writes, and a test can assert a rule without a server.

Every write here goes through :class:`~dsr.store.RecordStore`, the audited wrapper the HTTP
layer hands in, so the audit row is written in the same transaction as the change. The engine
never opens SQLite and never imports the application module.

The clock is a seam, not a global
---------------------------------

:meth:`QuotePaymentEngine.now` reads an injected clock, defaulting to UTC now. Every rule that
needs "what day is it" — the effective date resolved on agreement, the first invoice's date,
the recurring schedule, the ten-day send lead — takes that instant from this method rather
than calling :func:`datetime.now` itself. A test therefore pins a date and the invoice
schedule is testable without waiting for a month to pass.

The collections this engine writes
----------------------------------

* ``wf096_payment_setup`` — the publish-time payment configuration: the acceptance method, the
  billing and payment switches, the derived payment type, the allowed payment methods, the
  collection process, the net terms and the effective-date configuration.
* ``wf096_acceptance`` — one row per clickwrap acceptance: who accepted, when, and the method.
* ``wf096_charge`` — one row per charge attempt, recorded or declined.
* ``wf096_invoice`` — the immediate first invoice plus the scheduled later invoices.
* ``wf096_subscription`` — one row per recurring line item's subscription.
* ``wf096_tax_id`` — buyer tax identifiers, capped at three.
* ``wf096_activity`` — the human-readable log.

What this engine reads and does not own
---------------------------------------

**The quote and its line items.** WF-086 provisions ``wf086_quote`` and ``wf086_line_item``.
This workflow reads both and patches exactly the properties in
:data:`~dsr.quote_payment.vocabulary.QUOTE_PROPERTIES_WRITTEN` onto the quote, which includes
the two acceptance properties the researched data flow names. It never writes a line item
outside its own demo route and never writes an amount or a total onto the quote.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from dsr.quote_payment import inferences, rules, vocabulary as vocab
from dsr.quote_payment.errors import (
    PaymentRefused,
    QuoteNotFound,
    SetupNotFound,
    StateConflict,
)
from dsr.store import RecordStore

_ACTIVITY_SEQUENCE = 0


class QuotePaymentEngine:
    """The reads and the writes for accepting a quote without a signature and taking payment."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- clock ---------------------------------------------------------------- #

    def now(self) -> datetime:
        """The current instant, through the injected clock."""

        return self._now()

    def _now_iso(self) -> str:
        return self.now().isoformat()

    # -- shaping -------------------------------------------------------------- #

    @staticmethod
    def _shape(record: Mapping[str, Any]) -> dict[str, Any]:
        """A record's envelope fields beside its JSON payload, as the API answers with it."""

        data = dict(record.get("data") or {})
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
        }

    # -- reading the quote ---------------------------------------------------- #

    def _quote_record(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record.get("collection") != vocab.QUOTE_COLLECTION:
            raise QuoteNotFound(f"No quote {quote_id!r}.", quote_id=quote_id)
        return record

    def quote_lines(self, quote_id: str) -> list[dict[str, Any]]:
        """A quote's line items, in the seller's order.

        Sorted by stored ``position`` then by the record id, because an unordered list of lines
        is a different amount due every time it is read. The id tie-break keeps two lines added
        in the same millisecond in a stable order.
        """

        rows = self.store.find(vocab.LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=1000)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda line: (rules.as_number(line.get("position")), line["id"]))

    def _currency(self, quote: Mapping[str, Any]) -> str:
        data = quote.get("data") or {}
        for field in vocab.QUOTE_CURRENCY_FIELDS:
            value = rules.as_text(data.get(field))
            if value:
                return value.upper()
        return vocab.DEFAULT_CURRENCY

    def quote_view(self, quote_id: str) -> dict[str, Any]:
        """The quote as this workflow sees it: identity, acceptance state, lines and amount due."""

        record = self._quote_record(quote_id)
        lines = self.quote_lines(quote_id)
        acceptance = self._acceptance_record(quote_id)
        data = record.get("data") or {}
        return {
            **self._shape(record),
            "status": rules.as_text(data.get("status")),
            "hs_status": rules.as_text(data.get(vocab.FIELD_HS_STATUS)),
            "currency": self._currency(record),
            "accepted": bool(acceptance),
            "acceptance": self._shape(acceptance) if acceptance else None,
            "amount_due": rules.amount_due(lines),
            "line_items": lines,
            "quote_id": quote_id,
        }

    # -- reading this workflow's own collections ------------------------------ #

    def _setup_record(self, quote_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.PAYMENT_SETUP_COLLECTION, {"quote_id": quote_id}, limit=1)
        return rows[0] if rows else None

    def _setup(self, quote_id: str) -> dict[str, Any]:
        record = self._setup_record(quote_id)
        if record is None:
            raise SetupNotFound(
                f"Quote {quote_id!r} is not published with online payments.",
                quote_id=quote_id,
                reason=vocab.REASON_QUOTE_NOT_PROVISIONED,
            )
        return record

    def _acceptance_record(self, quote_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.ACCEPTANCE_COLLECTION, {"quote_id": quote_id}, limit=1)
        return rows[0] if rows else None

    def charges(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.CHARGE_COLLECTION, {"quote_id": quote_id}, limit=100)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda row: (row.get("created_at") or "", row["id"]))

    def invoices(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.INVOICE_COLLECTION, {"quote_id": quote_id}, limit=500)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda row: (row.get("invoice_date") or "", row["id"]))

    def subscriptions(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.SUBSCRIPTION_COLLECTION, {"quote_id": quote_id}, limit=200)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda row: (rules.as_number(row.get("position")), row["id"]))

    def tax_ids(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.TAX_ID_COLLECTION, {"quote_id": quote_id}, limit=10)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda row: (row.get("created_at") or "", row["id"]))

    def activity(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.ACTIVITY_COLLECTION, {"quote_id": quote_id}, limit=500)
        shaped = [self._shape(row) for row in rows]
        return sorted(shaped, key=lambda row: (row.get("at") or "", row["id"]))

    def payment_setup(self, quote_id: str) -> dict[str, Any]:
        """One quote's whole payment picture: the setup, the acceptance, charges and invoices."""

        setup = self._setup(quote_id)
        return {
            "setup": self._shape(setup),
            "quote": self.quote_view(quote_id),
            "charges": self.charges(quote_id),
            "invoices": self.invoices(quote_id),
            "subscriptions": self.subscriptions(quote_id),
            "tax_ids": self.tax_ids(quote_id),
            "activity": self.activity(quote_id),
        }

    def quote_detail(self, quote_id: str) -> dict[str, Any]:
        """One quote with everything this workflow knows about it.

        Unlike :meth:`payment_setup` this does not require a payment setup, because a seller may
        open a plain quote that was never published with payments, and a 404 for a quote that
        exists would be a lie. The setup is ``None`` and the collections are empty, which is the
        truthful answer.
        """

        setup = self._setup_record(quote_id)
        return {
            "quote": self.quote_view(quote_id),
            "setup": self._shape(setup) if setup else None,
            "charges": self.charges(quote_id),
            "invoices": self.invoices(quote_id),
            "subscriptions": self.subscriptions(quote_id),
            "tax_ids": self.tax_ids(quote_id),
            "activity": self.activity(quote_id),
        }

    def setups(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every provisioned quote, newest first, each with its whole payment picture.

        A setup whose quote has since been deleted is skipped rather than rendered as a broken
        row, because a delete is one of the states this workflow's own rules allow.
        """

        rows = self.store.list(
            vocab.PAYMENT_SETUP_COLLECTION,
            room_id=room_id,
            limit=500,
            order_by="created_at",
            descending=False,
        )
        views: list[dict[str, Any]] = []
        for row in rows:
            quote_id = rules.as_text((row.get("data") or {}).get("quote_id"))
            if not quote_id:
                continue
            try:
                views.append(self.payment_setup(quote_id))
            except QuoteNotFound:
                continue
        return views

    # -- demo authoring (WF-086 provisions in production) --------------------- #

    def create_quote(
        self,
        room_id: str | None,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a demo quote so the flow is demonstrable before WF-086 is driven from the UI.

        Written into ``wf086_quote``, the collection WF-086 owns, with the same field names it
        uses. Once WF-086 is exercised from the quote editor this route is simply another
        writer of the same collection, and the engine reads whatever it finds.
        """

        data = {
            "title": rules.as_text(payload.get("title"), "Untitled quote"),
            "currency": rules.as_text(payload.get("currency"), vocab.DEFAULT_CURRENCY).upper(),
            "status": rules.as_text(payload.get("status"), "published"),
            "deal_id": rules.as_text(payload.get("deal_id")),
            "company_name": rules.as_text(payload.get("company_name"), "Demo company"),
            "payment_schedule": list(payload.get("payment_schedule") or []),
        }
        record = self.store.create(
            vocab.QUOTE_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self.quote_view(record["id"])

    def create_line_item(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a demo line item on a quote, priced the way WF-086 prices one."""

        quote = self._quote_record(quote_id)
        name = rules.as_text(payload.get("name"))
        if not name:
            raise PaymentRefused(
                "A line item needs a name.",
                {"name": "Enter a name for the line."},
            )
        existing = self.quote_lines(quote_id)
        position = rules.as_number(payload.get("position"), len(existing))
        frequency = rules.validate_billing_frequency(payload.get("billing_frequency"))
        line = {
            "quote_id": quote_id,
            "name": name,
            "sku": rules.as_text(payload.get("sku")),
            "description": rules.as_text(payload.get("description")),
            "quantity": rules.as_number(payload.get("quantity"), 1.0),
            "unit_price": rules.as_number(payload.get("unit_price"), 0.0),
            "discount_type": rules.as_text(payload.get("discount_type"), vocab.DISCOUNT_PERCENTAGE),
            "discount_value": rules.as_number(payload.get("discount_value"), 0.0),
            "tax_rate": rules.as_number(payload.get("tax_rate"), 0.0),
            "billing_frequency": frequency,
            "billing_start": rules.as_text(payload.get("billing_start")),
            "position": position,
        }
        line["amounts"] = rules.line_amounts(line)
        record = self.store.create(
            vocab.LINE_ITEM_COLLECTION,
            line,
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )
        return self._shape(record)

    # -- activity ------------------------------------------------------------- #

    def _log(
        self,
        quote_id: str,
        activity: str,
        *,
        detail: str = "",
        room_id: str | None = None,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        global _ACTIVITY_SEQUENCE
        _ACTIVITY_SEQUENCE += 1
        return self.store.create(
            vocab.ACTIVITY_COLLECTION,
            {
                "quote_id": quote_id,
                "activity": activity,
                "label": vocab.ACTIVITY_LABELS.get(activity, activity),
                "detail": detail,
                "at": self._now_iso(),
                "sequence": _ACTIVITY_SEQUENCE,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    # -- publishing ----------------------------------------------------------- #

    def publish(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Provision online payments on a quote: the researched publish step.

        The configuration is validated first and nothing is written if any value is refused.
        Then the setup row is created or updated, then the payment properties are patched onto
        the quote, then the activity is logged. A quote that already has an acceptance refuses
        a re-publish with a 409, because re-configuring what the buyer accepted would change
        the terms behind an agreement.
        """

        quote = self._quote_record(quote_id)
        acceptance = self._acceptance_record(quote_id)
        if acceptance:
            raise StateConflict(
                vocab.REASON_IRREVERSIBLE_AFTER_ACCEPTANCE,
                "An accepted quote's payment configuration cannot change.",
                quote_id=quote_id,
            )

        lines = self.quote_lines(quote_id)
        currency = self._currency(quote)
        config = rules.validate_publish(payload, lines, currency=currency)

        data = {
            "quote_id": quote_id,
            "acceptance_method": config["acceptance_method"],
            "acceptance_recipient": rules.as_text(
                payload.get("acceptance_recipient") or payload.get("request_acceptance_from")
            ),
            "acceptance_recipient_mode": rules.as_text(
                payload.get("acceptance_recipient_mode"), "do_not_specify"
            ),
            "billing_enabled": config["billing_enabled"],
            "payment_enabled": config["payment_enabled"],
            "payment_type": config["payment_type"],
            "hs_payment_status": config["payment_status"],
            "allowed_payment_methods": config["allowed_payment_methods"],
            "collect_billing_address": config["collect_billing_address"],
            "collect_shipping_address": config["collect_shipping_address"],
            "collection_process": config["collection_process"],
            "net_payment_terms": config["net_payment_terms"],
            "billing_frequency": config["billing_frequency"],
            "effective_date": config["effective_date"],
            "currency": config["currency"],
            "minimum_charge": config["minimum_charge"],
            "store_payment_method": config["store_payment_method"],
            "automated_sales_tax": config["automated_sales_tax"],
            "published_at": self._now_iso(),
        }

        existing = self._setup_record(quote_id)
        if existing is None:
            self.store.create(
                vocab.PAYMENT_SETUP_COLLECTION,
                data,
                room_id=quote.get("room_id"),
                actor=actor,
                source=source,
            )
        else:
            self.store.update(existing["id"], data, actor=actor, source=source)

        self.store.update(
            quote_id,
            {
                vocab.FIELD_HS_PAYMENT_STATUS: vocab.PAYMENT_STATUS_PENDING,
                vocab.FIELD_HS_BILLING_ENABLED: True,
                vocab.FIELD_HS_PAYMENT_ENABLED: True,
                vocab.FIELD_HS_PAYMENT_TYPE: config["payment_type"],
                vocab.FIELD_HS_ALLOWED_PAYMENT_METHODS: config["allowed_payment_methods"],
                vocab.FIELD_HS_COLLECT_BILLING_ADDRESS: config["collect_billing_address"],
                vocab.FIELD_HS_COLLECT_SHIPPING_ADDRESS: config["collect_shipping_address"],
                vocab.FIELD_HS_COLLECTION_PROCESS: config["collection_process"],
                vocab.FIELD_HS_NET_PAYMENT_TERMS: config["net_payment_terms"],
            },
            actor=actor,
            source=source,
        )
        self._log(
            quote_id,
            vocab.ACTIVITY_PAYMENT_ENABLED,
            detail=(
                f"{vocab.ACCEPTANCE_METHOD_LABELS[config['acceptance_method']]} acceptance, "
                f"{vocab.PAYMENT_TYPE_LABELS[config['payment_type']]}, "
                f"{len(config['allowed_payment_methods'])} payment method(s)."
            ),
            room_id=quote.get("room_id"),
            source=source,
            actor=actor,
        )
        return self.payment_setup(quote_id)

    # -- accepting ------------------------------------------------------------ #

    def accept(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record the buyer's click-to-accept and create the invoices it triggers.

        The acceptance row is written first and flips the quote's ``hs_status`` and
        ``hs_clickwrap_accepted_by``, the two properties the researched data flow names. The
        first invoice and the recurring subscriptions are created after, because the research
        makes them a consequence of acceptance ("The first invoice is also generated and sent
        to the buyer immediately after quote acceptance").

        Payment is not taken here. The buyer's payment is a separate step, so a declined charge
        can never undo an acceptance the buyer already gave.
        """

        quote = self._quote_record(quote_id)
        setup = self._setup(quote_id)
        setup_data = setup.get("data") or {}
        rules.assert_can_accept(self._acceptance_record(quote_id), quote_id=quote_id)
        method = rules.assert_clickwrap(rules.as_text(setup_data.get("acceptance_method")))

        accepted_by = (
            rules.as_text(payload.get("accepted_by"))
            or rules.as_text(payload.get("accepted_by_email"))
            or rules.as_text(setup_data.get("acceptance_recipient"))
            or vocab.CLICKWRAP_ANONYMOUS_BUYER
        )
        accepted_by_email = rules.as_text(payload.get("accepted_by_email"))
        if not accepted_by_email and "@" in accepted_by:
            accepted_by_email = accepted_by

        moment = self.now()
        acceptance = self.store.create(
            vocab.ACCEPTANCE_COLLECTION,
            {
                "quote_id": quote_id,
                "setup_id": setup["id"],
                "method": method,
                "accepted_by": accepted_by,
                "accepted_by_email": accepted_by_email,
                "status": vocab.QUOTE_STATUS_ACCEPTED,
                "accepted_at": moment.isoformat(),
                "accepted_ip": rules.as_text(payload.get("accepted_ip")),
            },
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )

        self.store.update(
            quote_id,
            {
                vocab.FIELD_HS_STATUS: vocab.QUOTE_STATUS_ACCEPTED,
                vocab.FIELD_HS_CLICKWRAP_ACCEPTED_BY: accepted_by,
            },
            actor=actor,
            source=source,
        )

        invoices, subscriptions = self._create_invoices(quote, setup, source=source, actor=actor)
        self._log(
            quote_id,
            vocab.ACTIVITY_QUOTE_ACCEPTED,
            detail=f"Accepted without a signature by {accepted_by}.",
            room_id=quote.get("room_id"),
            source=source,
            actor=actor,
        )
        return {
            "quote_id": quote_id,
            "acceptance": self._shape(acceptance),
            "invoices": invoices,
            "subscriptions": subscriptions,
            "quote": self.quote_view(quote_id),
        }

    def _create_invoices(
        self,
        quote: Mapping[str, Any],
        setup: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The immediate first invoice plus a subscription and schedule per recurring line."""

        quote_id = quote["id"]
        setup_data = setup.get("data") or {}
        lines = self.quote_lines(quote_id)
        currency = rules.as_text(setup_data.get("currency"), vocab.DEFAULT_CURRENCY).upper()
        today = self.now().date()
        config = setup_data.get("effective_date")
        if not isinstance(config, Mapping):
            config = {"mode": vocab.DEFAULT_EFFECTIVE_DATE_MODE}

        created_invoices: list[dict[str, Any]] = []
        created_subscriptions: list[dict[str, Any]] = []

        first = rules.first_invoice(lines, currency=currency, today=today)
        first.update(
            {
                "quote_id": quote_id,
                "setup_id": setup["id"],
                "number": self._next_invoice_number(quote_id),
                "sent_at": self._now_iso(),
            }
        )
        created_invoices.append(
            self._shape(
                self.store.create(
                    vocab.INVOICE_COLLECTION,
                    first,
                    room_id=quote.get("room_id"),
                    actor=actor,
                    source=source,
                )
            )
        )

        default_frequency = rules.as_text(
            setup_data.get("billing_frequency"), vocab.BILLING_ONE_TIME
        )
        for line in lines:
            frequency = rules.line_billing_frequency(line, default_frequency)
            if frequency == vocab.BILLING_ONE_TIME:
                continue
            start_text = rules.line_billing_start(line, config, today)
            start = rules.as_date(start_text) or today
            amounts = rules.line_amounts(line)
            subscription = self.store.create(
                vocab.SUBSCRIPTION_COLLECTION,
                {
                    "quote_id": quote_id,
                    "setup_id": setup["id"],
                    "line_item_id": line["id"],
                    "name": rules.as_text(line.get("name")),
                    "frequency": frequency,
                    "start_date": start_text,
                    "amount": amounts["total"],
                    "currency": currency,
                    "status": vocab.SUBSCRIPTION_ACTIVE,
                    "position": rules.as_number(line.get("position"), 0.0),
                    "created_at": self._now_iso(),
                },
                room_id=quote.get("room_id"),
                actor=actor,
                source=source,
            )
            created_subscriptions.append(self._shape(subscription))

            for slot in rules.next_invoice_dates(frequency, start, today=today):
                invoice = {
                    "quote_id": quote_id,
                    "setup_id": setup["id"],
                    "subscription_id": subscription["id"],
                    "line_item_id": line["id"],
                    "kind": vocab.INVOICE_SCHEDULED,
                    "status": vocab.INVOICE_STATUS_SCHEDULED,
                    "number": self._next_invoice_number(quote_id),
                    "amount": amounts["total"],
                    "subtotal": amounts["subtotal"],
                    "discount": amounts["discount"],
                    "tax": amounts["tax"],
                    "currency": currency,
                    "billing_frequency": frequency,
                    "invoice_date": slot["invoice_date"],
                    "send_on": slot["send_on"],
                    "sent_at": None,
                    "rule": vocab.SUBSEQUENT_INVOICE_QUOTE,
                    "line_item_ids": [line["id"]],
                }
                created_invoices.append(
                    self._shape(
                        self.store.create(
                            vocab.INVOICE_COLLECTION,
                            invoice,
                            room_id=quote.get("room_id"),
                            actor=actor,
                            source=source,
                        )
                    )
                )
        return created_invoices, created_subscriptions

    def _next_invoice_number(self, quote_id: str) -> str:
        return f"INV-{len(self.invoices(quote_id)) + 1:04d}"

    # -- taking payment ------------------------------------------------------- #

    def record_charge(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record the payment processor's outcome for a quote's amount due.

        A declined charge is a row and a 200, not an exception: the minimum-charge constraint is
        the processor's own outcome, and the board has to be able to show that payment was
        attempted. An unknown or disallowed payment method *is* a 400, because that is a value
        this workflow will not accept.
        """

        quote = self._quote_record(quote_id)
        setup = self._setup(quote_id)
        setup_data = setup.get("data") or {}
        acceptance = self._acceptance_record(quote_id)
        rules.assert_can_pay(bool(acceptance), quote_id=quote_id)
        rules.assert_no_charge(self.charges(quote_id), quote_id=quote_id)

        lines = self.quote_lines(quote_id)
        due = rules.amount_due(lines)
        allowed = list(setup_data.get("allowed_payment_methods") or [])
        supplied = payload.get("payment_method") or payload.get("method")
        method = (
            rules.normalise_payment_method(supplied)
            if rules.as_text(supplied)
            else (allowed[0] if allowed else vocab.DEFAULT_PAYMENT_METHODS[0])
        )
        if allowed and not rules.payment_method_allowed(method, allowed):
            raise PaymentRefused(
                "That payment method is not enabled on this quote.",
                {"payment_method": (f"Choose one of: {', '.join(allowed)}.")},
                reason=vocab.REASON_PAYMENT_METHOD_NOT_ALLOWED,
            )

        currency = rules.as_text(setup_data.get("currency"), vocab.DEFAULT_CURRENCY).upper()
        outcome = rules.charge_outcome(due["total"], currency)
        moment = self.now()
        charge = self.store.create(
            vocab.CHARGE_COLLECTION,
            {
                "quote_id": quote_id,
                "setup_id": setup["id"],
                "acceptance_id": acceptance["id"] if acceptance else None,
                "amount": outcome["amount"],
                "subtotal": due["subtotal"],
                "discount": due["discount"],
                "tax": due["tax"],
                "currency": currency,
                "payment_type": rules.as_text(setup_data.get("payment_type")),
                "payment_method": method,
                "outcome": outcome["outcome"],
                "reason": outcome["reason"],
                "minimum": outcome["minimum"],
                "detail": outcome.get("detail", ""),
                "store_payment_method": rules.truthy(
                    payload.get(vocab.FIELD_STORE_PAYMENT_METHOD),
                    bool(setup_data.get("store_payment_method")),
                ),
                "initiated_at": moment.isoformat(),
                "settled_at": moment.isoformat()
                if outcome["outcome"] == vocab.OUTCOME_RECORDED
                else None,
            },
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )
        activity = (
            vocab.ACTIVITY_PAYMENT_RECORDED
            if outcome["outcome"] == vocab.OUTCOME_RECORDED
            else vocab.ACTIVITY_PAYMENT_DECLINED
        )
        self._log(
            quote_id,
            activity,
            detail=outcome.get("detail") or f"{method} charge recorded.",
            room_id=quote.get("room_id"),
            source=source,
            actor=actor,
        )
        return {
            "quote_id": quote_id,
            "charge": self._shape(charge),
            "outcome": outcome["outcome"],
            "reason": outcome["reason"],
            "detail": outcome.get("detail", ""),
            "amount_due": due,
        }

    # -- tax ids -------------------------------------------------------------- #

    def add_tax_id(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add a buyer tax ID, refusing the fourth. "up to three tax IDs"."""

        quote = self._quote_record(quote_id)
        existing = self.tax_ids(quote_id)
        rules.assert_tax_id_capacity(len(existing))
        value = rules.normalise_tax_id(payload.get("value") or payload.get("tax_id"))
        record = self.store.create(
            vocab.TAX_ID_COLLECTION,
            {
                "quote_id": quote_id,
                "value": value,
                "country": rules.as_text(payload.get("country")),
                "type": rules.as_text(payload.get("type")),
                "created_at": self._now_iso(),
            },
            room_id=quote.get("room_id"),
            actor=actor,
            source=source,
        )
        self._log(
            quote_id,
            vocab.ACTIVITY_TAX_ID_ADDED,
            detail=value,
            room_id=quote.get("room_id"),
            source=source,
            actor=actor,
        )
        return {
            "quote_id": quote_id,
            "tax_id": self._shape(record),
            "count": len(existing) + 1,
            "limit": vocab.TAX_ID_LIMIT,
        }

    # -- void and delete ------------------------------------------------------ #

    def void(self, quote_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Void a quote, refused once it has been accepted. "can't be voided ... after accepted"."""

        quote = self._quote_record(quote_id)
        rules.assert_can_void(bool(self._acceptance_record(quote_id)), quote_id=quote_id)
        self.store.update(
            quote_id,
            {vocab.FIELD_HS_STATUS: vocab.QUOTE_STATUS_VOID},
            actor=actor,
            source=source,
        )
        self._log(
            quote_id,
            vocab.ACTIVITY_QUOTE_VOIDED,
            room_id=quote.get("room_id"),
            source=source,
            actor=actor,
        )
        return self.quote_view(quote_id)

    def delete(self, quote_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Delete a quote, refused once it has been accepted. "can't be ... deleted after accepted"."""

        self._quote_record(quote_id)
        rules.assert_can_void(bool(self._acceptance_record(quote_id)), quote_id=quote_id)
        self.store.delete(quote_id, actor=actor, source=source)
        return {"quote_id": quote_id, "deleted": True}

    # -- the board and the research ------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers, read back from the store. Reads only, so safe to poll."""

        views = self.setups(room_id)
        accepted = 0
        charges: list[dict[str, Any]] = []
        invoices: list[dict[str, Any]] = []
        subscriptions = 0
        tax_ids = 0
        amount_due_total = 0.0
        for view in views:
            if view["quote"].get("accepted"):
                accepted += 1
            charges.extend(view["charges"])
            invoices.extend(view["invoices"])
            subscriptions += len(view["subscriptions"])
            tax_ids += len(view["tax_ids"])
            amount_due_total += rules.as_number(view["quote"]["amount_due"]["total"])

        recorded = [c for c in charges if c.get("outcome") == vocab.OUTCOME_RECORDED]
        declined = [c for c in charges if c.get("outcome") == vocab.OUTCOME_DECLINED]
        scheduled = [i for i in invoices if i.get("status") == vocab.INVOICE_STATUS_SCHEDULED]
        by_payment_type: dict[str, int] = {kind: 0 for kind in vocab.PAYMENT_TYPES}
        for view in views:
            kind = rules.as_text((view["setup"] or {}).get("payment_type"))
            by_payment_type[kind] = by_payment_type.get(kind, 0) + 1

        return {
            "quotes": len(views),
            "setups": len(views),
            "accepted": accepted,
            "awaiting_acceptance": len(views) - accepted,
            "charges": len(charges),
            "charges_recorded": len(recorded),
            "charges_declined": len(declined),
            "charged_total": rules.money(sum(rules.as_number(c.get("amount")) for c in recorded)),
            "pending_payment": accepted - len(recorded),
            "invoices": len(invoices),
            "invoices_scheduled": len(scheduled),
            "subscriptions": subscriptions,
            "tax_ids": tax_ids,
            "amount_due_total": rules.money(amount_due_total),
            "by_payment_type": by_payment_type,
            "minimum_charge_usd": vocab.MINIMUM_CHARGE_USD,
            "tax_id_limit": vocab.TAX_ID_LIMIT,
            "clickwrap_quote": vocab.CLICKWRAP_QUOTE,
            "minimum_charge_quote": vocab.MINIMUM_CHARGE_QUOTE,
            "first_invoice_quote": vocab.FIRST_INVOICE_QUOTE,
            "irreversible_quote": vocab.IRREVERSIBLE_QUOTE,
            "set_up_payment_quote": vocab.SET_UP_PAYMENT_QUOTE,
            "not_owned": dict(vocab.NOT_OWNED),
        }

    def vocabulary(self) -> dict[str, Any]:
        """The researched and derived vocabulary this workflow enforces against."""

        return vocab.vocabulary()

    def decisions(self) -> list[dict[str, Any]]:
        """Every judgement call this workflow made, with the alternative it rejected."""

        return inferences.decision_list()
