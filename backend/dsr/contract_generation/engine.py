"""The reads and writes of WF-099. No HTTP, and no database handle.

Every read and write goes through the :class:`~dsr.store.RecordStore` the feature
passes in, so the audit row is written in the same transaction as the change. The
``source`` of every write is supplied by the caller (the route) and names the URL
the app actually serves.

The contract this workflow creates is a copy of the quote, not a view of it. On
the Jev decision recorded at audit ``jev-20261013T150332-8132-12677`` the
acceptance signal is this workflow's own ledger, so a generic write of the
quote's ``hs_status`` cannot by itself produce a contract.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping, Sequence

from dsr.contract_generation import rules, vocabulary as vocab
from dsr.contract_generation.errors import ContractConflict, ContractError, ContractNotFound

#: The short spellings a caller may send for the four researched effective-start
#: properties, mapped to the ``hs_`` spelling the quote stores.
_EFFECTIVE_START_ALIASES: tuple[tuple[str, str], ...] = (
    ("effective_start_date", vocab.FIELD_EFFECTIVE_START_DATE),
    ("effective_start_date_type", vocab.FIELD_EFFECTIVE_START_DATE_TYPE),
    ("delayed_by_days", vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS),
    ("delayed_by_months", vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS),
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class ContractEngine:
    """The domain service. One instance per request, over the shared store."""

    def __init__(
        self,
        store: Any,
        *,
        today: Callable[[], date] | None = None,
    ) -> None:
        self.store = store
        self._today = today or date.today

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _now(self) -> date:
        return self._today()

    @staticmethod
    def _data(record: Mapping[str, Any] | None) -> dict[str, Any]:
        return dict(_as_mapping((record or {}).get("data")))

    def _require_quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if not record or record.get("collection") != vocab.QUOTE_COLLECTION:
            raise ContractNotFound(f"No quote record {quote_id!r}.")
        return record

    def _quote_lines(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.QUOTE_LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=500)
        # Deterministic order, because the contract copies them and a contract
        # whose lines reorder between reads is a defect a reviewer would see.
        # WF-093 documents the same sort: position, then name.
        return sorted(rows, key=self._line_order)

    def _line_order(self, record: Mapping[str, Any]) -> tuple[int, str, str]:
        data = self._data(record)
        position = rules.as_int(data.get("position"))
        return (
            position if position is not None else 10**9,
            rules.as_text(data.get("name")),
            str(record.get("id")),
        )

    def _contract_for_quote(self, quote_id: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.CONTRACT_COLLECTION, {"source_quote_id": quote_id}, limit=1)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ #
    # The setting
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
        stored = self._data(record)
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

    @staticmethod
    def _clean_setting(patch: Mapping[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        boolean_fields = (
            vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES,
            vocab.FIELD_CONNECTED_CPQ,
            vocab.FIELD_DIRECT_CONTRACTS,
            vocab.FIELD_DEAL_CREATION,
        )
        for field in vocab.SETTING_FIELDS:
            if field not in patch:
                continue
            if field in boolean_fields:
                clean[field] = rules.as_bool(patch[field])
            elif field == vocab.FIELD_RENEWAL_ALERT_DAYS:
                days = rules.as_int(patch[field])
                if days is None or days < 0:
                    raise ContractError(
                        "The renewal alert offset is not a non-negative whole number.",
                        {field: "Use a whole number of days, 0 or more."},
                    )
                clean[field] = days
            elif field == vocab.FIELD_RENEWAL_ALERT_MODE:
                mode = rules.as_text(patch[field])
                if mode not in vocab.RENEWAL_ALERT_MODES:
                    raise ContractError(
                        f"Unknown renewal alert mode {mode!r}.",
                        {field: "Must be one of: " + ", ".join(vocab.RENEWAL_ALERT_MODES)},
                    )
                clean[field] = mode
            else:
                clean[field] = patch[field]
        return clean

    def update_settings(
        self,
        patch: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Upsert the setting for a room. Unknown keys are ignored, not refused."""

        clean = self._clean_setting(_as_mapping(patch))
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
    # Acceptance
    # ------------------------------------------------------------------ #

    def acceptance_for(self, quote_id: str) -> dict[str, Any] | None:
        """The acceptance row this workflow recorded, or None."""

        rows = self.store.find(vocab.ACCEPTANCE_COLLECTION, {"quote_id": quote_id}, limit=1)
        return rows[0] if rows else None

    def record_acceptance(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record the platform's acceptance event for a quote.

        This is the acceptance path the issue names: the buyer accepted, the
        platform reports it here, and only a row in this ledger authorises a
        contract. The quote is published first, because a published quote is the
        one whose line items are frozen.
        """

        record = self._require_quote(quote_id)
        data = self._data(record)
        if not rules.is_published(data):
            raise ContractConflict(
                vocab.REASON_QUOTE_NOT_PUBLISHED,
                "A contract is created only from a published quote.",
            )

        method = rules.as_text(payload.get("method")) or None
        if method and method not in vocab.ACCEPTANCE_METHODS:
            raise ContractError(
                f"Unknown acceptance method {method!r}.",
                {"method": "Must be one of: " + ", ".join(vocab.ACCEPTANCE_METHODS)},
            )
        accepted_by = (
            rules.as_text(payload.get("accepted_by"))
            or rules.as_text(payload.get(vocab.FIELD_HS_CLICKWRAP_ACCEPTED_BY))
            or None
        )
        moment = self._now()

        patch: dict[str, Any] = {
            vocab.FIELD_HS_STATUS: vocab.QUOTE_STATUS_ACCEPTED,
        }
        mode_from_payload = rules.as_text(
            payload.get(vocab.FIELD_EFFECTIVE_START_DATE_TYPE)
        ) or rules.as_text(payload.get("effective_start_date_type"))
        if mode_from_payload:
            patch[vocab.FIELD_EFFECTIVE_START_DATE_TYPE] = mode_from_payload
        if accepted_by:
            patch[vocab.FIELD_HS_CLICKWRAP_ACCEPTED_BY] = accepted_by
        for long in (
            vocab.FIELD_EFFECTIVE_START_DATE,
            vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS,
            vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS,
        ):
            short = next(s for s, target in _EFFECTIVE_START_ALIASES if target == long)
            value = payload.get(long, payload.get(short))
            if value not in (None, ""):
                patch[long] = value

        legacy, marker = rules.is_legacy(data)
        # Validate the effective start date before any write, so a malformed
        # request leaves no acceptance row and no accepted quote behind. A legacy
        # quote creates no contract, so its effective start is not validated.
        setting = self.settings(room_id=record.get("room_id"))
        plan = rules.generation_plan(setting)
        if plan["create_contract"] and not legacy:
            mode = (
                rules.as_text(patch.get(vocab.FIELD_EFFECTIVE_START_DATE_TYPE))
                or rules.as_text(data.get(vocab.FIELD_EFFECTIVE_START_DATE_TYPE))
                or vocab.DEFAULT_EFFECTIVE_START_DATE_MODE
            )
            rules.resolve_effective_start(mode, accepted_on=moment, quote={**data, **patch})

        self.store.update(quote_id, patch, actor=actor, source=source)

        fields = {
            "quote_id": quote_id,
            "method": method,
            "accepted_by": accepted_by,
            "accepted_on": moment.isoformat(),
            "legacy": legacy,
            "legacy_marker": marker or None,
        }
        existing = self.acceptance_for(quote_id)
        if existing:
            return self.store.update(existing["id"], fields, actor=actor, source=source)
        return self.store.create(
            vocab.ACCEPTANCE_COLLECTION,
            fields,
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )

    def accept(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The platform acceptance path, then contract generation."""

        self.record_acceptance(quote_id, payload, actor=actor, source=source)
        return self._generate(quote_id, actor=actor, source=source)

    def generate(
        self,
        quote_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Create the contract for a quote that already carries an acceptance.

        Refuses when no acceptance was recorded through this workflow, even if
        the quote's ``hs_status`` says ACCEPTED. That is what stops a generic
        write from minting a contract.
        """

        self._require_quote(quote_id)
        if self.acceptance_for(quote_id) is None:
            raise ContractConflict(
                vocab.REASON_ACCEPTANCE_NOT_RECORDED,
                "No acceptance was recorded through the acceptance route.",
            )
        if rules.is_legacy(self._data(self._require_quote(quote_id)))[0]:
            raise ContractConflict(
                vocab.REASON_LEGACY_QUOTE,
                "Accepted legacy quotes do not create contracts.",
            )
        return self._generate(quote_id, actor=actor, source=source)

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    def _record_generation(
        self,
        quote_id: str,
        *,
        flow: str,
        reason: str,
        contract_id: str | None,
        order_id: str | None,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.GENERATION_COLLECTION,
            {
                "quote_id": quote_id,
                "flow": flow,
                "reason": reason or None,
                "contract_id": contract_id,
                "order_id": order_id,
                "at": self._now().isoformat(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _generate(
        self,
        quote_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self._require_quote(quote_id)
        data = self._data(record)
        room_id = record.get("room_id")

        existing = self._contract_for_quote(quote_id)
        if existing is not None:
            return {
                "created": False,
                "contract": self._contract_view(existing),
                "order": None,
                "flow": self._data(existing).get("contract_flow"),
                "reason": vocab.REASON_ALREADY_GENERATED,
                "generation": None,
            }

        legacy, marker = rules.is_legacy(data)
        if legacy:
            generation = self._record_generation(
                quote_id,
                flow=vocab.FLOW_NONE,
                reason=vocab.REASON_LEGACY_QUOTE,
                contract_id=None,
                order_id=None,
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "created": False,
                "contract": None,
                "order": None,
                "flow": vocab.FLOW_NONE,
                "reason": vocab.REASON_LEGACY_QUOTE,
                "generation": generation,
            }

        setting = self.settings(room_id=room_id)
        plan = rules.generation_plan(setting)
        if not plan["create_contract"]:
            generation = self._record_generation(
                quote_id,
                flow=vocab.FLOW_NONE,
                reason=plan["reason"],
                contract_id=None,
                order_id=None,
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "created": False,
                "contract": None,
                "order": None,
                "flow": vocab.FLOW_NONE,
                "reason": plan["reason"],
                "generation": generation,
            }

        acceptance = self.acceptance_for(quote_id) or {}
        accepted_on = rules.parse_date(_as_mapping(acceptance).get("accepted_on")) or self._now()
        mode = (
            rules.as_text(data.get(vocab.FIELD_EFFECTIVE_START_DATE_TYPE))
            or vocab.DEFAULT_EFFECTIVE_START_DATE_MODE
        )
        effective = rules.resolve_effective_start(mode, accepted_on=accepted_on, quote=data)
        term = rules.term_length(data)
        lines = [self._data(row) for row in self._quote_lines(quote_id)]

        payload = rules.contract_payload(
            quote=data,
            quote_id=quote_id,
            quote_lines=lines,
            plan=plan,
            effective=effective,
            term=term,
        )
        contract = self.store.create(
            vocab.CONTRACT_COLLECTION, payload, room_id=room_id, actor=actor, source=source
        )

        order: dict[str, Any] | None = None
        if plan["create_order"]:
            order = self.store.create(
                vocab.ORDER_COLLECTION,
                {
                    "contract_id": contract["id"],
                    "source_quote_id": quote_id,
                    "name": payload["name"],
                    "currency": payload["currency"],
                    "line_items": payload["line_items"],
                    "billing_flow": payload["billing_flow"],
                    "accepted_on": payload["accepted_on"],
                    "status": "open",
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            self.store.update(contract["id"], {"order_id": order["id"]}, actor=actor, source=source)
            contract = self.store.get(contract["id"]) or contract

        generation = self._record_generation(
            quote_id,
            flow=plan["flow"],
            reason="",
            contract_id=contract["id"],
            order_id=(order or {}).get("id"),
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "created": True,
            "contract": self._contract_view(contract),
            "order": order,
            "flow": plan["flow"],
            "reason": "",
            "generation": generation,
        }

    # ------------------------------------------------------------------ #
    # Standalone contracts
    # ------------------------------------------------------------------ #

    def create_standalone(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """A contract with no quote, from the Direct Create beta.

        The beta "allows contracts without a quote". It is gated by its own
        setting, because the research describes it as a separate beta.
        """

        setting = self.settings(room_id=room_id)
        if not rules.as_bool(setting.get(vocab.FIELD_DIRECT_CONTRACTS)):
            raise ContractConflict(
                vocab.REASON_DIRECT_CONTRACTS_DISABLED,
                "Standalone contracts need the Direct Create, Edit, and Renew beta.",
            )
        name = rules.as_text(payload.get("name"))
        if not name:
            raise ContractError("A standalone contract needs a name.", {"name": "Required."})
        start = rules.parse_date(payload.get("start_date")) or self._now()
        term = rules.as_int(payload.get("term_length"))
        lines = rules.contract_line_items(
            payload.get("line_items") if isinstance(payload.get("line_items"), Sequence) else []
        )
        contract = self.store.create(
            vocab.CONTRACT_COLLECTION,
            {
                "name": name,
                "seller": rules.as_text(payload.get("seller")) or None,
                "buyer": rules.as_text(payload.get("buyer")) or None,
                "currency": rules.as_text(payload.get("currency")) or None,
                "line_items": lines,
                "terms": payload.get("terms"),
                "term_length": term,
                "start_date": start.isoformat(),
                "end_date": rules.contract_end_date(start.isoformat(), term),
                "source_quote_id": None,
                "contract_flow": "standalone",
                "billing_flow": vocab.BILLING_SUBSCRIPTION
                if rules.as_bool(payload.get("hs_billing_enabled"))
                else vocab.BILLING_NONE,
                "status": rules.as_text(payload.get("status")) or "active",
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._contract_view(contract)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def _contract_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = self._data(record)
        order_id = data.get("order_id")
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "data": data,
            "derived": {
                "term_label": self._term_label(data),
                "renewable": bool(data.get("end_date")) and not bool(data.get("renewal_finalised")),
                "has_order": bool(order_id),
                "order_id": order_id,
            },
        }

    @staticmethod
    def _term_label(data: Mapping[str, Any]) -> str:
        line_items = data.get("line_items") or []
        renewals = {
            rules.as_text(item.get("auto_renew"))
            for item in line_items
            if isinstance(item, Mapping)
        }
        if renewals == {"until_canceled"}:
            return "Evergreen"
        term = data.get("term_length")
        return f"{term} months" if isinstance(term, int) else "unset"

    def contracts(self, *, room_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return [
            self._contract_view(record)
            for record in self.store.list(vocab.CONTRACT_COLLECTION, room_id=room_id, limit=limit)
        ]

    def contract(self, contract_id: str) -> dict[str, Any]:
        record = self.store.get(contract_id)
        if not record or record.get("collection") != vocab.CONTRACT_COLLECTION:
            raise ContractNotFound(f"No contract record {contract_id!r}.")
        return self._contract_view(record)

    def quote_readiness(self, quote_id: str) -> dict[str, Any]:
        """What would happen if this quote were accepted now, and why.

        This is the preview the page shows: the quote's state, whether it is
        legacy, whether acceptance was recorded, the plan the setting produces
        and the effective start year the quote's properties resolve to.
        """

        record = self._require_quote(quote_id)
        data = self._data(record)
        legacy, marker = rules.is_legacy(data)
        acceptance = self.acceptance_for(quote_id)
        existing = self._contract_for_quote(quote_id)
        setting = self.settings(room_id=record.get("room_id"))
        plan = rules.generation_plan(setting)
        mode = (
            rules.as_text(data.get(vocab.FIELD_EFFECTIVE_START_DATE_TYPE))
            or vocab.DEFAULT_EFFECTIVE_START_DATE_MODE
        )
        effective: dict[str, Any] | None = None
        effective_error: dict[str, Any] | None = None
        try:
            effective = rules.resolve_effective_start(mode, accepted_on=self._now(), quote=data)
        except ContractError as exc:
            effective_error = exc.as_dict()
        return {
            "quote_id": quote_id,
            "title": rules.contract_name(data, quote_id),
            "status": rules.as_text(data.get("status")),
            "published": rules.is_published(data),
            "legacy": legacy,
            "legacy_marker": marker or None,
            "accepted": acceptance is not None,
            "acceptance": (
                {
                    "id": acceptance["id"],
                    "method": self._data(acceptance).get("method"),
                    "accepted_by": self._data(acceptance).get("accepted_by"),
                    "accepted_on": self._data(acceptance).get("accepted_on"),
                }
                if acceptance
                else None
            ),
            "existing_contract_id": existing["id"] if existing else None,
            "setting": setting,
            "flow": plan["flow"],
            "plan": plan,
            "effective_start_date_mode": mode,
            "effective_start": effective,
            "effective_start_error": effective_error,
            "billing_flow": rules.billing_flow(data),
            "line_item_count": len(self._quote_lines(quote_id)),
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts a seller needs before opening the page."""

        contracts = self.contracts(room_id=room_id, limit=1000)
        orders = self.store.list(vocab.ORDER_COLLECTION, room_id=room_id, limit=1000)
        acceptances = self.store.list(vocab.ACCEPTANCE_COLLECTION, room_id=room_id, limit=1000)
        return {
            "contracts": len(contracts),
            "with_order": sum(1 for c in contracts if c["derived"]["has_order"]),
            "orders": len(orders),
            "acceptances": len(acceptances),
            "legacy_acceptances": sum(1 for row in acceptances if self._data(row).get("legacy")),
            "setting": self.settings(room_id=room_id),
        }
