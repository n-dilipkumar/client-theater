"""The writes and the reads for WF-100.

The engine is the only thing in this package that touches the store. It holds nothing beyond
the store and a clock, so building it per request leaves every seam overridable in a test
instead of hanging a long-lived object off ``app.state``, which is a shared file this feature
may not edit.

Every write goes through :meth:`dsr.store.RecordStore.create` / ``update``, and every one of
those writes an audit row naming the route that served it. The ``source`` is passed in by the
router rather than hardcoded here, so the audit log cannot name a route the app stopped
serving.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from dsr.renewal_quotes import rules, vocabulary as vocab
from dsr.renewal_quotes.errors import RenewalConflict, RenewalNotFound, RenewalRefusal
from dsr.store import RecordStore

CONTRACT = "contract"
TEMPLATE = vocab.TEMPLATE_COLLECTION
QUOTE = "wf100_renewal_quote"
DEAL = "wf100_deal"
WORKFLOW = "wf100_renewal_workflow"
PIPELINE = "wf100_pipeline"

#: The chain. A contract records the contract it replaced, the contract that replaced it, and
#: the quote that finalised it, so the chain is walkable in both directions without a search.
CHAIN_PREDECESSOR = vocab.CHAIN_PREDECESSOR
CHAIN_SUCCESSOR = vocab.CHAIN_SUCCESSOR
CHAIN_QUOTE = vocab.CHAIN_QUOTE
FINALISED_FLAG = vocab.FINALISED_FLAG


class RenewalQuoteEngine:
    """Every write and read this workflow performs."""

    def __init__(self, store: RecordStore, *, now: Callable[[], Any] = rules.utcnow) -> None:
        self.store = store
        self._now = now

    # -- internals --------------------------------------------------------- #

    def _today(self) -> date:
        return self._now().date()

    def _require(self, collection: str, record_id: Any, label: str) -> dict[str, Any]:
        record = self.store.get(str(record_id)) if record_id else None
        if record is None or record.get("collection") != collection:
            raise RenewalNotFound(f"No {label} with id {record_id!r}.")
        return record

    def _data(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return dict(record.get("data") or {})

    def _existing_deal(self, selection: str, deal_id: Any) -> str | None:
        """The deal an Existing deal selection names, checked now so a bad id is a 404.

        The researched Existing deal method attaches the renewal to a deal the seller already
        has, so the deal is named at quote time. It is resolved here rather than at acceptance
        so a seller is not told the renewal is ready when the deal they named no longer exists.
        """

        if selection != "existing_deal" and not deal_id:
            return None
        if not deal_id:
            raise RenewalRefusal(
                "An existing deal selection needs a deal.",
                {"deal_id": "Name the deal this renewal attaches to."},
            )
        return str(self._require(DEAL, deal_id, "renewal deal")["id"])

    # -- reads ------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers, read back from the store. Reads only."""

        contracts = self.store.list(CONTRACT, room_id=room_id, limit=500)
        quotes = self.store.list(QUOTE, room_id=room_id, limit=500)
        by_state: dict[str, int] = {}
        for quote in quotes:
            state = str(self._data(quote).get("state") or "draft")
            by_state[state] = by_state.get(state, 0) + 1
        return {
            "contracts": len(contracts),
            "renewable_contracts": sum(
                1
                for contract in contracts
                if rules.contract_is_renewable(self._data(contract))["renewable"]
            ),
            "templates": len(self.store.list(TEMPLATE, room_id=room_id, limit=500)),
            "quotes": len(quotes),
            "quotes_by_state": by_state,
            "accepted_quotes": by_state.get("accepted", 0),
            "renewal_contracts": sum(
                1 for contract in contracts if self._data(contract).get(CHAIN_PREDECESSOR)
            ),
            "deals": len(self.store.list(DEAL, room_id=room_id, limit=500)),
            "pipelines": len(self.store.list(PIPELINE, room_id=room_id, limit=500)),
            "workflows": len(self.store.list(WORKFLOW, room_id=room_id, limit=500)),
            "rule": vocab.RENEWAL_DATE_RULE,
            "evidence": dict(vocab.EVIDENCE),
        }

    def contracts(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every contract with its term label, its renewal date and its alert date."""

        rows = []
        for record in self.store.list(CONTRACT, room_id=room_id, limit=500):
            data = self._data(record)
            chain = self._accepted_quote_for(data, room_id=room_id)
            rows.append(
                {
                    "id": record["id"],
                    **data,
                    "term_label": rules.term_label(data),
                    "renewal": rules.renewal_date(data, chain),
                    "alert": rules.alert_due_date(data, chain),
                    "renewable": rules.contract_is_renewable(data),
                }
            )
        return rows

    def contract(self, contract_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One contract with its renewal chain in both directions."""

        record = self._require(CONTRACT, contract_id, "contract")
        data = self._data(record)
        previous_id = data.get(CHAIN_PREDECESSOR)
        successor_id = data.get(CHAIN_SUCCESSOR)
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            **data,
            "term_label": rules.term_label(data),
            "renewal": rules.renewal_date(data, self._accepted_quote_for(data, room_id=room_id)),
            "alert": rules.alert_due_date(data, self._accepted_quote_for(data, room_id=room_id)),
            "renewable": rules.contract_is_renewable(data),
            "chain": {
                "renewed_from_contract_id": previous_id,
                "renewed_into_contract_id": successor_id,
                "renewed_into_quote_id": data.get(CHAIN_QUOTE),
                "renewal_finalised": bool(data.get(FINALISED_FLAG)),
                "previous": self._chain_step(previous_id, room_id=room_id),
                "next": self._chain_step(successor_id, room_id=room_id),
            },
            "quotes": [
                q["id"] for q in self.store.find(QUOTE, {"contract_id": record["id"]}, limit=50)
            ],
        }

    def _chain_step(self, contract_id: Any, *, room_id: str | None) -> dict[str, Any] | None:
        if not contract_id:
            return None
        record = self.store.get(str(contract_id))
        if record is None:
            return {"id": contract_id, "name": None, "missing": True}
        return {
            "id": record["id"],
            "name": self._data(record).get("name"),
            "missing": False,
        }

    def _accepted_quote_for(
        self, contract: Mapping[str, Any], *, room_id: str | None
    ) -> dict[str, Any] | None:
        quote_id = contract.get(CHAIN_QUOTE)
        if not quote_id:
            return None
        record = self.store.get(str(quote_id))
        if record is None:
            return None
        return self._data(record)

    def templates(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {"id": record["id"], **self._data(record)}
            for record in self.store.list(TEMPLATE, room_id=room_id, limit=500)
        ]

    def template(self, template_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._require(TEMPLATE, template_id, "renewal template")
        return {"id": record["id"], "room_id": record.get("room_id"), **self._data(record)}

    def quotes(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for record in self.store.list(QUOTE, room_id=room_id, limit=500):
            data = self._data(record)
            rows.append({"id": record["id"], **data, "term_label": data.get("term_label")})
        return rows

    def quote(self, quote_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One renewal quote, with the derived figures a seller needs before sharing it."""

        record = self._require(QUOTE, quote_id, "renewal quote")
        data = self._data(record)
        contract = None
        if data.get("contract_id"):
            found = self.store.get(str(data["contract_id"]))
            contract = self._data(found) if found else None
        resolved = rules.effective_date(
            data, accepted_on=rules.coerce_optional_date(data.get("accepted_on"))
        )
        proration = rules.prorated_charges(
            data,
            from_date=rules.coerce_optional_date(contract.get("start_date") if contract else None),
            to_date=rules.coerce_optional_date(contract.get("end_date") if contract else None),
        )
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            **data,
            "effective_date": resolved,
            "proration": proration,
            "renewal_date": rules.renewal_date(contract or {}, data)
            if contract
            else {
                "renewal_date": None,
                "branch": "if_not_finalised",
                "rule": vocab.RENEWAL_DATE_RULE_IF_NOT_FINALISED,
                "source_quote_id": None,
            },
            "deals": [
                {"id": deal["id"], **self._data(deal)}
                for deal in self.store.find(DEAL, {"quote_id": record["id"]}, limit=20)
            ],
            "new_contract_id": data.get("new_contract_id"),
        }

    def deals(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {"id": record["id"], **self._data(record)}
            for record in self.store.list(DEAL, room_id=room_id, limit=500)
        ]

    def deal(self, deal_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._require(DEAL, deal_id, "renewal deal")
        return {"id": record["id"], "room_id": record.get("room_id"), **self._data(record)}

    def pipelines(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {"id": record["id"], **self._data(record)}
            for record in self.store.list(PIPELINE, room_id=room_id, limit=500)
        ]

    def workflows(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {"id": record["id"], **self._data(record)}
            for record in self.store.list(WORKFLOW, room_id=room_id, limit=500)
        ]

    def workflow(self, workflow_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._require(WORKFLOW, workflow_id, "renewal workflow")
        return {"id": record["id"], "room_id": record.get("room_id"), **self._data(record)}

    # -- templates --------------------------------------------------------- #

    def create_template(
        self,
        name: Any,
        *,
        change_type: Any = "renewal",
        term_months: Any = None,
        discount_format: Any = "total",
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create a renewal or change quote template.

        This workflow owns the collection because the research names the template as an
        input and no pending ticket provisions one. The choice is recorded in
        :mod:`dsr.renewal_quotes.inferences`.
        """

        clean = rules.normalise_name(name, "name")
        kind = str(change_type or "renewal").strip().lower()
        if kind not in ("renewal", "change"):
            raise RenewalRefusal(
                "Unknown template change type.",
                {"change_type": f"{change_type!r} is not renewal or change."},
            )
        months = rules.coerce_positive_int(term_months, "term_months", minimum=1)
        record = self.store.create(
            TEMPLATE,
            {
                "name": clean,
                "change_type": kind,
                "term_months": months,
                "discount_format": str(discount_format or "total"),
                "association_type": vocab.TEMPLATE_ASSOCIATION_TYPE,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {"template": {"id": record["id"], **self._data(record)}}

    # -- the quote --------------------------------------------------------- #

    def create_quote(
        self,
        contract_id: Any,
        *,
        template_id: Any = None,
        effective_date_mode: Any = "on_agreement",
        effective_date_on: Any = None,
        delay_days: Any = None,
        delay_months: Any = None,
        prorate: Any = True,
        deal_pipeline_id: Any = None,
        deal_stage: Any = None,
        deal_selection_method: Any = "new_deal_default_stage",
        deal_id: Any = None,
        deal_name: Any = None,
        name: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create a renewal quote from a contract, prefilled from that contract.

        The seller and buyer data and the commercial content come from the contract, which is
        what the research describes: "You land in the quote editor with seller and buyer
        details auto-populated from the contract."

        The quote records the template as association type 286, because the research states
        that number is attached at creation.
        """

        record = self._require(CONTRACT, contract_id, "contract")
        contract = self._data(record)
        rules.require_renewable(contract)

        template: dict[str, Any] | None = None
        if template_id:
            template = self._data(self._require(TEMPLATE, template_id, "renewal template"))

        mode = rules.normalise_effective_date_mode(effective_date_mode)
        on = rules.coerce_date(effective_date_on, "effective_date_on")
        days = rules.coerce_positive_int(delay_days, "delay_days", minimum=0)
        months = rules.coerce_positive_int(delay_months, "delay_months", minimum=0)
        if mode == "custom_date" and on is None:
            raise RenewalRefusal(
                "A custom date mode needs a date.",
                {"effective_date_on": "Send the change effective date the seller chose."},
            )
        if mode == "delayed_start" and days is None:
            raise RenewalRefusal(
                "A delayed start mode needs a day count.",
                {"delay_days": "Send the number of days after the day of agreement."},
            )
        if mode == "months" and months is None:
            raise RenewalRefusal(
                "A months mode needs a month count.",
                {"delay_months": "Send the number of months after the day of agreement."},
            )

        prorate_flag = rules.coerce_bool(prorate, "prorate", default=True)
        selection = rules.normalise_deal_selection_method(deal_selection_method)
        pipeline = None
        if selection == "new_deal_default_stage" and deal_pipeline_id:
            pipeline = self._data(self._require(PIPELINE, deal_pipeline_id, "deal pipeline"))
        if deal_pipeline_id and not deal_stage:
            raise RenewalRefusal(
                "A deal pipeline needs a deal stage.",
                {"deal_stage": "Name the stage the renewal deal should start in."},
            )

        effective = {
            "mode": mode,
            "on": on.isoformat() if on else None,
            "delay_days": days,
            "delay_months": months,
        }
        line_items = contract.get("line_items") or []
        total = rules.sum_amounts(
            item.get("amount") for item in line_items if isinstance(item, Mapping)
        )
        title = rules.normalise_name(
            name or f"Renewal of {contract.get('name') or record['id']}", "name"
        )
        data = {
            "name": title,
            "contract_id": record["id"],
            "template_id": template_id or None,
            "template_association_type": vocab.TEMPLATE_ASSOCIATION_TYPE,
            "change_type": (template or {}).get("change_type", "renewal"),
            "state": "draft",
            "seller": contract.get("seller"),
            "buyer": contract.get("buyer"),
            "currency": contract.get("currency"),
            "line_items": list(line_items),
            "term_length": (template or {}).get("term_months") or contract.get("term_length"),
            "term_label": rules.term_label(contract),
            "discount_format": (template or {}).get("discount_format", "total"),
            "total": total,
            "prorate": bool(prorate_flag),
            "effective_date": effective,
            "deal_selection_method": selection,
            "deal_pipeline_id": deal_pipeline_id or None,
            "deal_stage": deal_stage or None,
            "deal_pipeline_name": (pipeline or {}).get("name"),
            "deal_id": self._existing_deal(selection, deal_id),
            "vendor_endpoint": vocab.VENDOR_QUOTE_ENDPOINT,
            "created_at": rules.stamp(self._now()),
        }
        quote = self.store.create(
            QUOTE, data, room_id=room_id or record.get("room_id"), actor=actor, source=source
        )
        return {"quote": self.quote(quote["id"], room_id=room_id)}

    def update_effective_date(
        self,
        quote_id: str,
        *,
        mode: Any = None,
        on: Any = None,
        delay_days: Any = None,
        delay_months: Any = None,
        prorate: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Change the Summary module's effective date or proration flag.

        A PATCH rather than a POST, because these are fields of the quote rather than resources
        of their own, and a PATCH on the fields is the shape a caller expects.
        """

        record = self._require(QUOTE, quote_id, "renewal quote")
        data = self._data(record)
        if str(data.get("state")) == "accepted":
            raise RenewalConflict(
                "This quote is accepted, so its effective date cannot be changed.",
                remedy="Create a new renewal quote from the contract instead.",
                evidence=vocab.EVIDENCE["renewal_creates_contract"],
            )
        effective = dict(data.get("effective_date") or {})
        if mode is not None:
            effective["mode"] = rules.normalise_effective_date_mode(mode)
        if on is not None:
            effective["on"] = rules.coerce_date(on, "effective_date_on").isoformat()  # type: ignore[union-attr]
        if delay_days is not None:
            effective["delay_days"] = rules.coerce_positive_int(delay_days, "delay_days", minimum=0)
        if delay_months is not None:
            effective["delay_months"] = rules.coerce_positive_int(
                delay_months, "delay_months", minimum=0
            )
        patch: dict[str, Any] = {"effective_date": effective}
        if prorate is not None:
            patch["prorate"] = bool(rules.coerce_bool(prorate, "prorate"))
        self.store.update(record["id"], patch, actor=actor, source=source)
        return {"quote": self.quote(record["id"], room_id=room_id)}

    def set_state(
        self,
        quote_id: str,
        state: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Move a quote to a non-accepting state: draft, shared or superseded.

        Acceptance has its own route because it creates records. Accepting through this
        route is refused rather than allowed, so a caller can never create a contract as a side
        effect of a state write without going through the route whose audit row names it.
        """

        target = rules.normalise_state(state)
        if target == "accepted":
            raise RenewalRefusal(
                "Use the accept route to accept a quote.",
                {"state": "Accepting creates the new contract and the renewal deal."},
            )
        record = self._require(QUOTE, quote_id, "renewal quote")
        self.store.update(record["id"], {"state": target}, actor=actor, source=source)
        return {"quote": self.quote(record["id"], room_id=room_id)}

    def accept(
        self,
        quote_id: str,
        *,
        accepted_by: Any = None,
        existing_deal_id: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Accept a quote. Create the new contract, the renewal chain link, and the deal.

        This is the researched transition: "When a renewal quote is accepted, a new contract
        is created and automatically associated with the previous contract."

        The acceptance signal is not sourced. The research never says who accepts or how the
        room learns that it happened, so this workflow makes acceptance an explicit room
        action and records the derivation rather than inferring acceptance from a status
        write. See ``wf100-acceptance-signal`` in ``GET /decisions``.
        """

        record = self._require(QUOTE, quote_id, "renewal quote")
        quote = self._data(record)
        if quote.get("state") == "accepted":
            raise RenewalConflict(
                "This quote is already accepted.",
                remedy=("Read the new contract it created, or renew that contract instead."),
                evidence=vocab.EVIDENCE["renewal_creates_contract"],
            )
        contract_record = self._require(CONTRACT, quote.get("contract_id"), "contract")
        contract = self._data(contract_record)

        accepted_on = self._today()
        resolved = rules.effective_date(quote, accepted_on=accepted_on)
        effective_on = rules.require_resolved_effective_date(resolved)

        new_contract = self.store.create(
            CONTRACT,
            {
                "name": quote.get("name") or f"Renewal of {contract.get('name')}",
                "seller": quote.get("seller") or contract.get("seller"),
                "buyer": quote.get("buyer") or contract.get("buyer"),
                "currency": quote.get("currency") or contract.get("currency"),
                "line_items": list(quote.get("line_items") or []),
                "term_length": quote.get("term_length") or contract.get("term_length"),
                "start_date": effective_on,
                "end_date": _end_from_term(effective_on, quote.get("term_length")),
                "alert_offset_days": contract.get("alert_offset_days"),
                CHAIN_QUOTE: record["id"],
                "renewed_by_quote_id": record["id"],
                "accepted_on": accepted_on.isoformat(),
                "accepted_by": accepted_by or actor or "unknown",
                "acceptance_signal_sourced": False,
                "total": quote.get("total"),
                CHAIN_PREDECESSOR: contract_record["id"],
            },
            room_id=room_id or record.get("room_id") or contract_record.get("room_id"),
            actor=actor,
            source=source,
        )
        # The chain is recorded on both sides. The new contract names the one it replaced, and
        # the old contract names the one that replaced it and the quote that finalised it, so
        # the chain is walkable in both directions without a search over the collection.
        self.store.update(
            contract_record["id"],
            {CHAIN_SUCCESSOR: new_contract["id"], CHAIN_QUOTE: record["id"], FINALISED_FLAG: True},
            actor=actor,
            source=source,
        )

        deal = self._create_deal_for_quote(
            record,
            quote,
            new_contract_id=new_contract["id"],
            existing_deal_id=existing_deal_id,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.store.update(
            record["id"],
            {
                "state": "accepted",
                "accepted_on": accepted_on.isoformat(),
                "accepted_by": accepted_by or actor or "unknown",
                "new_contract_id": new_contract["id"],
                "deal_id": deal["id"],
                "effective_date": {**quote.get("effective_date", {}), "resolved_on": effective_on},
            },
            actor=actor,
            source=source,
        )
        # Re-enrolment. The research names the switch and does not describe it, so the
        # behaviour is a recorded decision. See ``wf100-re-enroll``.
        self._maybe_reenroll(contract_record["id"], new_contract["id"], actor=actor, source=source)
        return {
            "quote": self.quote(record["id"], room_id=room_id),
            "new_contract": self.contract(new_contract["id"], room_id=room_id),
            "deal": {"id": deal["id"], **self._data(deal)},
            "renewal_date": self.contract(contract_record["id"], room_id=room_id)["renewal"],
            "evidence": vocab.EVIDENCE["renewal_creates_contract"],
        }

    def _create_deal_for_quote(
        self,
        quote_record: Mapping[str, Any],
        quote: Mapping[str, Any],
        *,
        new_contract_id: str,
        existing_deal_id: Any,
        room_id: str | None,
        actor: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        selection = str(quote.get("deal_selection_method") or "new_deal_default_stage")
        if selection == "existing_deal" or existing_deal_id:
            # The researched Existing deal method attaches the renewal to a deal the seller
            # named. No deal is created, because the research says the deal already exists.
            return self._require(DEAL, existing_deal_id or quote.get("deal_id"), "renewal deal")
        pipeline_id = quote.get("deal_pipeline_id")
        stage = quote.get("deal_stage")
        pipeline = None
        if pipeline_id:
            pipeline = self._data(self._require(PIPELINE, pipeline_id, "deal pipeline"))
        name = quote.get("name") or quote.get("buyer") or "Renewal deal"
        return self.store.create(
            DEAL,
            {
                "name": name,
                "pipeline_id": pipeline_id,
                "pipeline_name": (pipeline or {}).get("name"),
                "stage": stage,
                "deal_type": "renewal",
                "quote_id": quote_record["id"],
                "contract_id": quote_record.get("contract_id"),
                "new_contract_id": new_contract_id,
                "auto_created": True,
            },
            room_id=room_id or quote_record.get("room_id"),
            actor=actor,
            source=source,
        )

    def _maybe_reenroll(
        self,
        previous_contract_id: str,
        new_contract_id: str,
        *,
        actor: str | None,
        source: str | None,
    ) -> None:
        """Move an enrolled renewal workflow onto the new contract when it re-enrols.

        The research names a Re-enroll switch and never describes it. The decision recorded in
        ``wf100-re-enroll`` is: a workflow with the switch on follows the chain onto the new
        contract, and one without it stays on the contract it was enrolled on. Both are
        implemented so a reviewer can see both.
        """

        for record in self.store.list(WORKFLOW, limit=500):
            data = self._data(record)
            if data.get("enrolled_contract_id") != previous_contract_id:
                continue
            if not data.get(vocab.REENROLL_FIELD):
                continue
            self.store.update(
                record["id"],
                {
                    "enrolled_contract_id": new_contract_id,
                    "previous_enrolled_contract_id": previous_contract_id,
                    "cycles": int(data.get("cycles") or 0) + 1,
                },
                actor=actor,
                source=source,
            )

    # -- pipelines and workflows ------------------------------------------- #

    def create_pipeline(
        self,
        name: Any,
        *,
        stages: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Register a deal pipeline with its stages, so a seller can pick one."""

        clean = rules.normalise_name(name, "name")
        list_stages = [str(s).strip() for s in (stages or []) if str(s).strip()]
        if not list_stages:
            raise RenewalRefusal(
                "A pipeline needs at least one stage.", {"stages": "Send the stage names."}
            )
        record = self.store.create(
            PIPELINE,
            {"name": clean, "stages": list_stages},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {"pipeline": {"id": record["id"], **self._data(record)}}

    def create_workflow(
        self,
        *,
        contract_id: Any = None,
        deal_ids: Any = None,
        template_id: Any = None,
        deal_selection_method: Any = "new_deal_default_stage",
        deal_pipeline_id: Any = None,
        deal_stage: Any = None,
        contract_target: Any = "one_contract",
        re_enroll: Any = False,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create a renewal workflow: the deal-based action the research describes.

        The research calls this a deal-based workflow action and explicitly not a documented
        REST endpoint, so it is modelled as a room action backed by a stored definition. There
        is no vendor endpoint to mirror, and inventing one would have made the room look like
        it integrates with a vendor it does not call. See ``wf100-workflow-action-shape``.
        """

        target = rules.normalise_contract_target(contract_target)
        selection = rules.normalise_deal_selection_method(deal_selection_method)
        if target == "one_contract" and not contract_id:
            raise RenewalRefusal(
                "A one-contract workflow needs a contract.",
                {"contract_id": "Name the contract this workflow renews."},
            )
        contract_record = None
        if contract_id:
            contract_record = self._require(CONTRACT, contract_id, "contract")
        if template_id:
            self._require(TEMPLATE, template_id, "renewal template")
        if selection == "new_deal_default_stage" and deal_pipeline_id:
            self._require(PIPELINE, deal_pipeline_id, "deal pipeline")
            if not deal_stage:
                raise RenewalRefusal(
                    "A deal pipeline needs a deal stage.",
                    {"deal_stage": "Name the stage the renewal deal should start in."},
                )
        ids = [str(d) for d in (deal_ids or []) if str(d).strip()]
        data = {
            "name": rules.normalise_name(
                f"Renewal workflow for {contract_record['id'] if contract_record else 'associated contracts'}",
                "name",
            ),
            "contract_target": target,
            "enrolled_contract_id": contract_record["id"] if contract_record else None,
            "deal_ids": ids,
            "template_id": template_id or None,
            "deal_selection_method": selection,
            "deal_pipeline_id": deal_pipeline_id or None,
            "deal_stage": deal_stage or None,
            vocab.REENROLL_FIELD: bool(
                rules.coerce_bool(re_enroll, vocab.REENROLL_FIELD, default=False)
            ),
            "cycles": 0,
            "enabled": True,
            "enrollment_trigger": "contract_renewal_date",
            "vendor_action": vocab.EVIDENCE["workflow_action"],
            "contract_dropdown": vocab.EVIDENCE["workflow_contract_dropdown"],
        }
        record = self.store.create(WORKFLOW, data, room_id=room_id, actor=actor, source=source)
        return {"workflow": self.workflow(record["id"], room_id=room_id)}

    def run_workflow(
        self,
        workflow_id: str,
        *,
        prorate: Any = True,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Run a renewal workflow against its enrolled contract.

        The research distinguishes the two contract scopes, so both are implemented. One
        contract renews that contract. All associated contracts renews every contract
        associated with the deal the workflow is enrolled on, and the response says how many it
        touched so a caller can tell the two apart.
        """

        record = self._require(WORKFLOW, workflow_id, "renewal workflow")
        workflow = self._data(record)
        targets: list[str] = []
        if workflow.get("contract_target") == "all_associated":
            deal_ids = workflow.get("deal_ids") or []
            for deal_id in deal_ids:
                deal = self.store.get(str(deal_id))
                if deal is None:
                    continue
                contract_id = self._data(deal).get("contract_id")
                if contract_id and str(contract_id) not in targets:
                    targets.append(str(contract_id))
        elif workflow.get("enrolled_contract_id"):
            targets.append(str(workflow["enrolled_contract_id"]))

        created, skipped = [], []
        for contract_id in targets:
            try:
                result = self.create_quote(
                    contract_id,
                    template_id=workflow.get("template_id"),
                    deal_pipeline_id=workflow.get("deal_pipeline_id"),
                    deal_stage=workflow.get("deal_stage"),
                    deal_selection_method=workflow.get("deal_selection_method"),
                    prorate=prorate,
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
                created.append(result["quote"])
            except RenewalConflict as exc:
                skipped.append({"contract_id": contract_id, "reason": exc.detail})
        return {
            "workflow_id": record["id"],
            "contract_target": workflow.get("contract_target"),
            "quotes_created": len(created),
            "quotes": created,
            "skipped": skipped,
            "reenroll": bool(workflow.get(vocab.REENROLL_FIELD)),
            "reenroll_decision": vocab.REENROLL_DECISION,
            "reenroll_reason": vocab.REENROLL_DECISION_REASON,
            "evidence": vocab.EVIDENCE["workflow_action"],
        }


def _end_from_term(start: Any, term: Any) -> str | None:
    """The end date a new contract gets from its term length.

    A term given in months advances the month, clamped to the last day of the target month.
    A term that is not a number is stored as given and the end date stays unknown, which the
    renewal date rule's not-finalised branch then reports as null rather than guessing.
    """

    if start in (None, ""):
        return None
    resolved = rules.coerce_optional_date(start)
    if resolved is None:
        return None
    if term in (None, "") or isinstance(term, bool):
        return None
    try:
        months = int(term)
    except (TypeError, ValueError):
        return None
    if months <= 0:
        return None
    advanced = rules._add_months(resolved, months)  # noqa: SLF001 - one implementation, shared
    return advanced.isoformat() if advanced else None
