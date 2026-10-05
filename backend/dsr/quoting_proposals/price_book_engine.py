"""WF-088: the reads and the writes for assigning a price book to a deal by rule.

Pricing, quoting and proposals. This is the module that talks to the store. The
rules it enforces are in :mod:`dsr.quoting_proposals.price_book_rules`, the words in
:mod:`dsr.quoting_proposals.price_book_vocabulary`, and the decisions in
:mod:`dsr.quoting_proposals.price_book_inferences`. Nothing here decides a rule, so a
change to the rules does not touch this file.

Nothing in this package imports ``dsr.api``, and nothing opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the
audit row is written in the same transaction as the change.

What this workflow reads and does not own
-----------------------------------------

**The deal.** It is a CRM mirror another workflow writes, and this workflow writes
exactly one JSON key on it: ``price_book``. That is the sourced data flow, and it is
what scopes the product lookup afterwards. It reads the deal by its own id and by the
CRM's own id field, across every collection this product has used for one, because a
workflow that only understood ``crm_deal`` would find nothing in a workspace that
mirrors ``crm_opportunity``. See ``THE_PRICE_BOOK_FIELD_BELONGS_TO_THIS_WORKFLOW``.

**The company.** A filter may target company properties, so the engine resolves the
company a deal points at and reads it from whichever collection holds one. A deal with
no company is still assignable by its deal-level filters.

**Line items.** Read so a change of book can honour the sourced removal. Only the lines
of the previous book are touched, and their ids go on the assignment.

**The quote.** Read, never written. The price book is on the deal and the quote
inherits it.

The shape of the engine
-----------------------

Built per request from ``StoreDep`` rather than held on ``app.state``, because an
``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the feature
host exists to make unnecessary. It holds nothing but the store handle and a clock,
both constructor arguments, so a test constructs one over rows of its own with a clock
it controls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.quoting_proposals import (
    price_book_inferences as inferences,
    price_book_rules as rules,
    price_book_vocabulary as vocab,
)
from dsr.store import RecordStore


class PriceBookEngine:
    """The workflow, over one audited store."""

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._clock = now or rules.utcnow

    def now(self) -> datetime:
        return self._clock()

    # -- resolving what to read --------------------------------------------- #

    def _first_collection(self, names: Sequence[str]) -> str:
        """The first candidate collection that holds a live record.

        Chosen by what exists rather than by declaration, so a workspace that mirrors
        a deal as ``crm_opportunity`` is read without this module naming it as the
        primary. ``limit=1`` because only the answer to "does this hold anything"
        matters, and an empty string is the honest "there is none yet" answer.
        """
        for name in names:
            if self.store.list(name, limit=1):
                return name
        return ""

    def _require(self, collection: str, record_id: str, code: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record["collection"] != collection:
            raise rules.PriceBookNotFound(code, label, record_id)
        return record

    def rule(self, rule_id: str) -> dict[str, Any]:
        """One configured assignment rule, by record id."""
        return self._require(
            vocab.PRICE_BOOK_RULES, rule_id, "unknown_assignment_rule", "assignment rule"
        )

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        """One recorded assignment, by record id."""
        return self._require(vocab.ASSIGNMENTS, assignment_id, "unknown_assignment", "assignment")

    def deal(self, deal_id: str) -> dict[str, Any]:
        """A deal, by this product's record id or by the CRM's own id field.

        Two passes because a caller holding a CRM id is the common case and this
        product's record id is not the CRM's. Raises rather than returning ``None``,
        so a route cannot forget to handle a deal that is not there.
        """
        wanted = str(deal_id or "").strip()
        if not wanted:
            raise rules.PriceBookNotFound("unknown_deal", "deal", deal_id)
        record = self.store.get(wanted)
        if record is not None and record["collection"] in vocab.DEAL_COLLECTION_ALIASES:
            return record
        for name in vocab.DEAL_COLLECTION_ALIASES:
            for field in ("crm_id", "external_id", "source_id", "deal_id", "opportunity_id"):
                found = self.store.find(name, {field: wanted}, limit=1)
                if found:
                    return found[0]
        raise rules.PriceBookNotFound("unknown_deal", "deal", deal_id)

    def deal_payload(self, deal_id: str) -> dict[str, Any]:
        """A deal's own ``data``, with the envelope id folded in.

        The domain rules scope nothing on an envelope field, but a rule may bind to
        ``id`` because a filter may target any property, and a deal's id is one of them.
        """
        record = self.deal(deal_id)
        return {**(record.get("data") or {}), "id": record["id"]}

    def deal_room(self, deal_id: str) -> str | None:
        """The room a deal belongs to, or ``None`` when it has none recorded.

        Rules are scoped to a room and a deal is scoped to a room, so evaluating a
        deal against rules from every room prices it with another room's price book.
        That is not a display problem: the write lands on the deal and the assignment
        row, so the audit trail would attribute a room A rule to a room B deal.

        ``None`` is passed through rather than replaced, because ``store.list`` treats
        ``room_id=None`` as "do not scope". A deal with no room therefore still sees every
        room's rules, which is the pre-existing behaviour and is safer than silently
        pricing a deal from no rules at all.
        """
        return self.deal(deal_id).get("room_id")

    def company(self, deal_id: str) -> dict[str, Any]:
        """The company a deal points at, or an empty mapping.

        An empty mapping rather than a refusal, because a deal with no company is a
        deal a seller is still allowed to price. A company filter against it reports
        "this deal has no company to read from" and does not match.
        """
        payload = self.deal_payload(deal_id)
        for field in vocab.COMPANY_REFERENCE_FIELDS:
            related = payload.get(field)
            if isinstance(related, Mapping):
                return {**related, "id": related.get("id")}
            if not related:
                continue
            record = self.store.get(str(related))
            if record is not None and record["collection"] in vocab.COMPANY_COLLECTION_ALIASES:
                return {**(record.get("data") or {}), "id": record["id"]}
            for name in vocab.COMPANY_COLLECTION_ALIASES:
                found = self.store.find(name, {"id": str(related)}, limit=1)
                if found:
                    return {**(found[0].get("data") or {}), "id": found[0]["id"]}
        return {}

    def quote(self, quote_id: str) -> dict[str, Any]:
        """The quote this workflow prices, read as data."""
        record = self.store.get(quote_id)
        if record is None or record["collection"] != vocab.SOURCE_QUOTES:
            raise rules.PriceBookNotFound("unknown_quote", "quote", quote_id)
        return record

    def line_items(self, deal_id: str) -> list[dict[str, Any]]:
        """The line items on a deal, read as data.

        Narrowed by ``deal_id`` through the dynamic index rather than by a foreign key
        this workflow does not own, and read from whichever collection holds one.
        """
        payload = self.deal_payload(deal_id)
        wanted = payload.get("id")
        for name in vocab.LINE_ITEM_COLLECTION_ALIASES:
            found = self.store.find(name, {"deal_id": wanted}, limit=500)
            if found:
                return [dict(row.get("data") or {}) | {"_id": row["id"]} for row in found]
        return []

    # -- rules -------------------------------------------------------------- #

    def create_rule(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a configured assignment rule.

        The whole payload is validated on save rather than at assignment time, so a
        seller finds out here that a rule names no price book. A half-configured rule
        is the failure mode the research's own UI prevents by refusing to save.
        """
        return self.store.create(
            vocab.PRICE_BOOK_RULES,
            rules.validate_rule(payload),
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def list_rules(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """The configured rules, oldest first, so the page shows them in order."""
        return self.store.list(
            vocab.PRICE_BOOK_RULES,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=False,
        )

    def patch_rule(
        self,
        rule_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Turn a rule's two switches, or any other field, revalidated.

        The merged payload is revalidated rather than only the keys that were sent,
        because a half-applied toggle is a rule that reads as active and is not. The
        two switches are the researched flow's own two toggles: **Inactive** off to
        activate the price book, and **Auto-assigned** on to assign.
        """
        record = self.rule(rule_id)
        merged = rules.validate_rule({**(record.get("data") or {}), **dict(payload or {})})
        return self.store.update(record["id"], merged, actor=actor, source=source)

    def delete_rule(self, rule_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Remove a rule. The assignments it created are left alone.

        They name the rule by id and carry their own filter report, so a past
        assignment still explains itself after the rule is gone.
        """
        record = self.rule(rule_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- matching ----------------------------------------------------------- #

    def rule_reports(self, deal_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every configured rule, evaluated against this deal, matched or not.

        This is the evaluation, without the filtering, and it is what
        :meth:`~dsr.quoting_proposals.price_book_engine.PriceBookEngine.matching_rules`
        selects from. Split out rather than folded into one function with a flag because
        the two callers want opposite things: an assignment wants only the matches,
        while the conditions panel needs the misses as much as the hits.
        """
        deal = self.deal_payload(deal_id)
        company = self.company(deal_id)
        evaluated: list[dict[str, Any]] = []
        for record in self.list_rules(room_id=self.deal_room(deal_id), limit=limit):
            data = record.get("data") or {}
            report = rules.matches_filters(
                data.get("filters") or [],
                deal,
                company,
                str(data.get("matchMode") or vocab.FILTER_MATCH_ALL),
            )
            evaluated.append({"rule": {**data, "id": record["id"]}, "report": report})
        return evaluated

    def matching_rules(
        self, deal_id: str, *, enabled_only: bool = True, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The rules a deal matches, with each filter's report.

        ``enabled_only`` is True by default because a caller asking which rules apply
        means the active ones. The assignment path passes False, because an inactive rule
        that matched is a different answer with a different fix than no rule matching.

        The report travels with the answer rather than being recomputed on a read,
        because the flow's right panel has to "review the matching deals" and has to
        review them as they were when the decision was taken.
        """
        return [
            entry
            for entry in self.rule_reports(deal_id, limit=limit)
            if entry["report"]["matched"]
            and (not enabled_only or entry["rule"].get(vocab.ENABLED, True))
        ]

    def conditions(self, deal_id: str) -> dict[str, Any]:
        """What auto-assignment would do to this deal, before it does it.

        A GET because it checks and writes nothing. It runs the same evaluation the
        assignment runs, so what a seller is shown is what will be enforced.
        """
        deal = self.deal_payload(deal_id)
        company = self.company(deal_id)
        field, current = rules.read_book_field(deal)
        # Every rule is evaluated, not only the ones that matched. The researched panel
        # "review[s] the matching deals in the right panel", and a panel that lists only
        # hits cannot answer the question an admin actually has while building a rule,
        # which is why the rule I just wrote did not match this deal. So each rule is
        # reported with whether it matched and what each of its filters read, and the
        # inactive ones are named separately because their fix is a different switch.
        every = self.rule_reports(deal_id)
        matched = [one for one in every if one["report"]["matched"]]
        decision = rules.decide_assignment(matched, current=current)
        return {
            "deal_id": deal.get("id"),
            "deal_field": field,
            "price_book": rules.book_view(current),
            "price_book_label": rules.book_label(current),
            "outcome": decision["outcome"],
            "explanation": decision["explanation"],
            "writes": decision["writes"],
            "candidates": decision["candidates"],
            "matched_rules": len(matched),
            "rules": [
                {
                    "rule_id": entry["rule"].get("id"),
                    "rule_label": entry["rule"].get("label"),
                    "matched": bool(entry["report"]["matched"]),
                    "enabled": bool(entry["rule"].get(vocab.ENABLED, True)),
                    "auto_assign": bool(entry["rule"].get(vocab.AUTO_ASSIGN, True)),
                    "price_book": rules.book_view(entry["rule"].get("price_book")),
                    "filters": entry["report"]["filters"],
                    "matched_count": entry["report"]["matched_count"],
                    "total": entry["report"]["total"],
                }
                for entry in every
            ],
            "inactive_rules": [
                entry["rule"].get("label")
                for entry in matched
                if not entry["rule"].get(vocab.ENABLED, True)
            ],
            "test_first_rules": [
                entry["rule"].get("label")
                for entry in matched
                if entry["rule"].get(vocab.ENABLED, True)
                and not entry["rule"].get(vocab.AUTO_ASSIGN, True)
            ],
            "state": self._state(decision, current),
            "mode": rules.workspace_mode(
                [
                    row.get("data") or {}
                    for row in self.list_rules(room_id=self.deal_room(deal_id), limit=1000)
                ]
            ),
            "create_only_quote": vocab.CREATE_ONLY_QUOTE,
            "line_items": self.line_items(deal_id),
            # The exact lines an override would remove, computed by the same
            # `lines_for_book` the override itself calls. Sent here so the page can state
            # the count *before* the button, which is the only moment the warning is
            # useful. `line_items` alone would over-count, because a line naming a
            # different price book survives the override and must not be included in a
            # number the seller reads as "these will be deleted".
            "line_items_removed_on_change": [
                dict(row) for row in rules.lines_for_book(self.line_items(deal_id), current)
            ],
            "company_found": bool(company),
        }

    @staticmethod
    def _state(decision: Mapping[str, Any], current: Any) -> str:
        """A deal's price-book state, derived rather than stored.

        Derived so it cannot disagree with what is on the deal. A book on the deal is
        assigned whatever produced it; several candidates and nothing written is
        ``needs_choice``; anything else is unassigned.
        """
        if rules.book_view(current) is not None:
            return vocab.BOOK_STATE_ASSIGNED
        if decision.get("outcome") == vocab.ASSIGNMENT_NEEDS_CHOICE:
            return vocab.BOOK_STATE_NEEDS_CHOICE
        return vocab.BOOK_STATE_UNASSIGNED

    # -- assigning ---------------------------------------------------------- #

    def assign(
        self,
        deal_id: str,
        *,
        actor: str | None,
        source: str,
        trigger: str = vocab.TRIGGER_CREATE,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate the rules for a deal and write the one price book that matched.

        This is the researched moment: a deal row is created, the filters are
        evaluated against its deal and company properties, and the winning price book
        is written onto the deal so the product lookup and the line-item prices are
        scoped to it.

        Every outcome is recorded, including the ones that write nothing, because
        "why does this deal have no price book" is the first question a seller asks and
        an evaluation that returned nothing has no answer to it. The reasons are the
        published codes in :data:`~dsr.quoting_proposals.price_book_vocabulary.ASSIGNMENT_REASONS`.
        """
        moment = self.now()
        trigger = rules.normalise_trigger(trigger)
        deal = self.deal_payload(deal_id)
        field, current = rules.read_book_field(deal)
        # Inactive rules are passed in rather than filtered out, because "a rule matched
        # and its Inactive switch is on" and "no rule matched" are different answers
        # with different fixes. Dropping them here would collapse the two.
        matched = self.matching_rules(deal_id, enabled_only=False)
        decision = rules.decide_assignment(matched, current=current, trigger=trigger)

        assignment = self._record_assignment(
            deal,
            decision,
            field=field,
            moment=moment,
            actor=actor,
            source=source,
            room_id=room_id,
            removed=[],
        )

        if decision["writes"]:
            book = decision["price_book"] or {}
            self.store.update(
                deal["id"],
                {
                    vocab.PRICE_BOOK: {
                        **book,
                        "rule_id": decision.get("rule_id"),
                        "rule_label": decision.get("rule_label"),
                        "assigned_at": rules.stamp(moment),
                        "assigned_by": actor,
                    }
                },
                actor=actor,
                source=source,
            )

        return {
            "assigned": bool(decision["writes"]),
            "written": bool(decision["writes"]),
            "deal_id": deal["id"],
            "outcome": decision["outcome"],
            "explanation": decision["explanation"],
            "price_book": decision["price_book"],
            "price_book_label": rules.book_label(decision["price_book"]),
            "rule_id": decision.get("rule_id"),
            "trigger": trigger,
            "evaluated_at": rules.stamp(moment),
            "candidates": decision["candidates"],
            "matched_rules": decision["matched_rules"],
            "state": self._state(
                decision, decision["price_book"] if decision["writes"] else current
            ),
            "assignment": self._assignment_view(self.assignment(assignment["id"])),
            "create_only_quote": vocab.CREATE_ONLY_QUOTE,
        }

    def _record_assignment(
        self,
        deal: Mapping[str, Any],
        decision: Mapping[str, Any],
        *,
        field: str | None,
        moment: datetime,
        actor: str | None,
        source: str,
        room_id: str | None,
        removed: Sequence[str],
        previous: Any = None,
        outcome: str | None = None,
    ) -> dict[str, Any]:
        """One assignment row, written whichever way the evaluation went."""
        return self.store.create(
            vocab.ASSIGNMENTS,
            {
                "deal_id": deal.get("id"),
                "deal_name": deal.get("name") or deal.get("deal_name") or deal.get("title"),
                "outcome": outcome or decision.get("outcome"),
                "written": bool(decision.get("writes")),
                "price_book": decision.get("price_book"),
                "price_book_label": rules.book_label(decision.get("price_book")),
                "previous_price_book": rules.book_view(previous),
                "deal_field": field,
                "authority": (
                    vocab.AUTHORITY_ASSIGNMENT
                    if decision.get("writes")
                    else (vocab.AUTHORITY_DEAL_FIELD if field else vocab.AUTHORITY_NONE)
                ),
                "rule_id": decision.get("rule_id"),
                "rule_label": decision.get("rule_label"),
                "matched_filters": decision.get("matched_filters") or [],
                "candidates": decision.get("candidates") or [],
                "matched_rules": decision.get("matched_rules", 0),
                "trigger": decision.get("trigger") or vocab.TRIGGER_CREATE,
                "line_items_removed": list(removed),
                "line_items_removed_quote": vocab.LINE_ITEMS_REMOVED_QUOTE,
                "assigned_by": actor,
                "at": rules.stamp(moment),
                "explanation": decision.get("explanation"),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def override(
        self,
        deal_id: str,
        price_book: Any,
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """The researched **Change price book** override.

        Four steps of the flow: "On the deal's *Line items* card click **Price book:
        None** (or the price book name) -> **Price book** dropdown -> **Change price
        book**". This is that dropdown, and it is also how a deal owner resolves a
        deal that matched more than one rule.

        Two sourced sentences meet here. "If the price book is changed, any line items
        associated with the previous price book will be removed" is honoured: the lines
        of the previous book are soft-deleted, their ids go on the assignment, and the
        answer names how many went. And "After a price book is auto-assigned, HubSpot
        won't run auto-assignment again if the deal or associated company properties
        used in the filter are updated" is why this never re-runs the rules: a hand
        choice is not re-derived.

        Setting the same book it already has is refused rather than treated as a
        no-op, because a no-op that removes nothing still looks like a change of book
        on the audit trail.
        """
        moment = self.now()
        target = rules.normalise_book(price_book)
        deal = self.deal_payload(deal_id)
        field, current = rules.read_book_field(deal)
        if rules.book_view(current) is not None and rules.book_identity(
            current
        ) == rules.book_identity(target):
            raise rules.refuse("override_to_the_same_price_book")

        claimed = rules.lines_for_book(self.line_items(deal_id), current)
        removed: list[str] = []
        for line in claimed:
            identifier = str(line.get("_id") or "").strip()
            if not identifier:
                continue
            self.store.delete(identifier, actor=actor, source=source)
            removed.append(identifier)

        previous = rules.book_view(current)
        outcome = vocab.ASSIGNMENT_CHANGED if previous is not None else vocab.ASSIGNMENT_SET
        decision = {
            "outcome": outcome,
            "writes": True,
            "price_book": target,
            "rule_id": None,
            "rule_label": None,
            "matched_filters": [],
            "candidates": [],
            "matched_rules": 0,
            "trigger": vocab.TRIGGER_CREATE,
        }
        assignment = self._record_assignment(
            deal,
            decision,
            field=field,
            moment=moment,
            actor=actor,
            source=source,
            room_id=room_id,
            removed=removed,
            previous=previous,
            outcome=outcome,
        )
        self.store.update(
            deal["id"],
            {
                vocab.PRICE_BOOK: {
                    **target,
                    "rule_id": None,
                    "rule_label": None,
                    "assigned_at": rules.stamp(moment),
                    "assigned_by": actor,
                }
            },
            actor=actor,
            source=source,
        )
        return {
            "changed": True,
            "written": True,
            "deal_id": deal["id"],
            "outcome": outcome,
            "price_book": target,
            "price_book_label": rules.book_label(target),
            "previous_price_book": previous,
            "line_items_removed": removed,
            "line_items_removed_count": len(removed),
            "line_items_removed_quote": vocab.LINE_ITEMS_REMOVED_QUOTE,
            "at": rules.stamp(moment),
            "assignment": self._assignment_view(self.assignment(assignment["id"])),
        }

    # -- reading the state -------------------------------------------------- #

    def deal_price_book(self, deal_id: str) -> dict[str, Any]:
        """The price book on a deal, where it came from, and what else was offered.

        Both the field that answered and the latest assignment are returned, because
        "the deal has no price book" is a different fact from "this workflow assigned
        one and it has since been removed by hand", and a reader needs to be able to
        tell them apart.
        """
        deal = self.deal_payload(deal_id)
        field, current = rules.read_book_field(deal)
        latest = self.latest_assignment(deal_id)
        decision = rules.decide_assignment(self.matching_rules(deal_id), current=current)
        candidates = decision["candidates"]
        return {
            "deal_id": deal["id"],
            "price_book": rules.book_view(current),
            "price_book_label": rules.book_label(current),
            "deal_field": field,
            "authority": (
                vocab.AUTHORITY_DEAL_FIELD
                if field
                else (vocab.AUTHORITY_ASSIGNMENT if latest is not None else vocab.AUTHORITY_NONE)
            ),
            "state": self._state(decision, current),
            "candidates": candidates,
            "needs_a_choice": decision["outcome"] == vocab.ASSIGNMENT_NEEDS_CHOICE,
            "latest_assignment": latest,
            "create_only_quote": vocab.CREATE_ONLY_QUOTE,
        }

    def quote_price_book(self, quote_id: str) -> dict[str, Any]:
        """The price book a quote gets, inherited from its deal.

        "Quotes inherit the price book from the associated deal. Users can't select a
        price book when creating a quote; they must select it on the deal." The first
        sentence is this read; the second is why nothing here writes a quote.
        """
        quote = self.quote(quote_id)
        payload = {**(quote.get("data") or {}), "id": quote["id"]}
        related = payload.get("deal") or payload.get("deal_id")
        if not related:
            return rules.evaluate_inheritance(payload, None)
        try:
            deal = self.deal_payload(str(related))
        except rules.PriceBookNotFound:
            # The quote names a deal that is not there. Reported as its own answer rather
            # than folded into "no associated deal", because a dangling reference is a
            # broken thing somebody has to fix and an absent one is not.
            return rules.evaluate_inheritance(payload, None, deal_missing=str(related))
        field, current = rules.read_book_field(deal)
        return rules.evaluate_inheritance(payload, deal, deal_field=field, deal_price_book=current)

    def latest_assignment(self, deal_id: str) -> dict[str, Any] | None:
        """The most recent assignment for a deal, or ``None``.

        Sorted on ``created_at`` with the insertion order as the tie-break, because
        two evaluations of one deal inside the same millisecond tie and an arbitrary
        answer to "what happened to this deal" is a wrong answer to a money question.
        """
        # The store's own newest-first order is taken as it arrives rather than
        # re-sorted here. `find` orders by `updated_at DESC, rowid DESC`, and `rowid` is
        # SQLite's insertion sequence, so it is the tie-break that holds when two
        # assignments land in the same millisecond -- which is every time an override
        # follows an assignment, because they are written microseconds apart.
        #
        # Re-sorting on `(created_at, id)` would order two such rows by a random uuid4,
        # so "the latest assignment" would be whichever of the two sorted higher. That is
        # a coin flip on the busiest machine and a stable lie on a quiet one, and it is
        # the exact failure recorded in the store's own `list()` docstring. So the store
        # order is the answer, and this method does not invent a second one.
        rows = self.store.find(vocab.ASSIGNMENTS, {"deal_id": deal_id}, limit=200)
        if not rows:
            return None
        return self._assignment_view(rows[0])

    def list_assignments(
        self,
        *,
        deal_id: str | None = None,
        outcome: str | None = None,
        room_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Assignments, newest first, filtered through the dynamic index."""
        where: dict[str, Any] = {}
        if deal_id:
            where["deal_id"] = str(deal_id)
        if outcome:
            where["outcome"] = str(outcome).strip().lower()
        rows = (
            self.store.find(vocab.ASSIGNMENTS, where, limit=min(limit, 1000))
            if where
            else self.store.list(vocab.ASSIGNMENTS, room_id=room_id, limit=min(limit, 1000))
        )
        scoped = [row for row in rows if room_id is None or row.get("room_id") == room_id]
        return [self._assignment_view(row) for row in scoped[:limit]]

    @staticmethod
    def _assignment_view(record: Mapping[str, Any]) -> dict[str, Any]:
        """One assignment, with its report and the researched sentences beside it.

        The fields a page reads are lifted out of the envelope's ``data`` blob rather
        than left inside it, because a client asking "which rule priced this deal" or
        "how many line items went" should not have to know this module's storage
        shape. The envelope itself is still returned, so a caller that wants
        ``revision`` or ``room_id`` has them.
        """
        data = dict(record.get("data") or {})
        outcome = str(data.get("outcome") or "")
        return {
            # The envelope is spread except for its own `data`, which is replaced by the
            # lifted fields below. Keeping both invites the bug this codebase has shipped
            # twice already: a caller reads `record["outcome"]`, finds the envelope's
            # copy rather than the payload's, and gets a field that reads as absent. One
            # shape, from one place, and `record["data"]` is deliberately not one of them.
            **{key: value for key, value in record.items() if key != "data"},
            "outcome": outcome,
            "written": bool(data.get("written")),
            "price_book": data.get("price_book"),
            "price_book_label": data.get("price_book_label"),
            # `deal_id` is lifted for the same reason: it is the field every filter on
            # this list uses, and the envelope's own copy of it is the record's id under
            # a name the payload also claims. Reading the envelope's gives a reader a
            # filter that silently matches nothing.
            "deal_id": data.get("deal_id"),
            "previous_price_book": data.get("previous_price_book"),
            "authority": data.get("authority"),
            "rule_id": data.get("rule_id"),
            "rule_label": data.get("rule_label"),
            "matched_filters": list(data.get("matched_filters") or []),
            "candidates": list(data.get("candidates") or []),
            "matched_rules": data.get("matched_rules", 0),
            "trigger": data.get("trigger"),
            "deal_name": data.get("deal_name"),
            "deal_field": data.get("deal_field"),
            "assigned_by": data.get("assigned_by"),
            "at": data.get("at"),
            "explanation": data.get("explanation") or vocab.ASSIGNMENT_REASON_LABELS.get(outcome),
            "reason_label": vocab.ASSIGNMENT_REASON_LABELS.get(outcome),
            "line_items_removed": list(data.get("line_items_removed") or []),
            "line_items_removed_count": len(data.get("line_items_removed") or []),
            "line_items_removed_quote": vocab.LINE_ITEMS_REMOVED_QUOTE,
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The board a reviewer reads first: how much is priced and what needs a person."""
        configured = self.list_rules(room_id=room_id, limit=1000)
        assignments = [
            dict(row.get("data") or {})
            for row in self.store.list(vocab.ASSIGNMENTS, room_id=room_id, limit=1000)
        ]
        board = rules.summary_counts(configured, assignments)
        board["room_id"] = room_id
        board["catalogue_found"] = bool(self._first_collection(vocab.PRICE_BOOK_COLLECTION_ALIASES))
        board["catalogue_note"] = (
            "A price book is a reference by id or name, so a rule works before the "
            "catalogue exists. Products browsable under an assigned book are scoped by "
            "the workflow that owns the catalogue."
        )
        board["deal_collection"] = self._first_collection(vocab.DEAL_COLLECTION_ALIASES)
        board["multiple_matches"] = inferences.multiple_matches()
        board["override_removes_lines"] = inferences.override_removes_lines()
        return board


def vocabulary() -> dict[str, Any]:
    """Every researched term, served as data."""
    return vocab.catalogue()


def inferences_report() -> dict[str, Any]:
    """Every judgement call, served as data."""
    return {
        "count": inferences.count(),
        "decisions": inferences.describe(),
        "multiple_matches": inferences.multiple_matches(),
        "override_removes_lines": inferences.override_removes_lines(),
    }
