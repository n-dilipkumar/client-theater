"""WF-091: the reads and the writes for standard quote approval.

Pricing, quoting and proposals. This is the module that talks to the store. The
rules it enforces are in :mod:`dsr.quoting_proposals.quote_approval_rules`, the
words in :mod:`dsr.quoting_proposals.quote_approval_vocabulary`, and the decisions
in :mod:`dsr.quoting_proposals.quote_approval_inferences`. Nothing here decides a
rule, so a change to the rules does not touch this file.

Nothing in this package imports ``dsr.api``, and nothing opens SQLite. Every read
and write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in,
so the audit row is written in the same transaction as the change.

What this workflow reads and does not own
-----------------------------------------

**The quote.** WF-086 provisions it. This workflow reads ``wf086_quote`` as data and
writes its state onto its own enrolment records, never onto the quote. The evidence
puts the approval states in the UI ("primarily managed via the UI and approval
workflows"), so this product exposes the transitions through the enrolment rather
than by giving the quote a second writer. See ``DERIVED_THE_QUOTE_IS_READ_AS_DATA``.

**Line items and the related deal.** WF-086 and WF-087 provision those too. A
line-item filter reads them, so :meth:`QuoteApprovalEngine.line_items` and
:meth:`~QuoteApprovalEngine.deal` resolve them from whatever exists and a quote with
neither is still approvable.

The shape of the engine
-----------------------

Built per request from ``StoreDep`` rather than held on ``app.state``, because an
``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the feature
host exists to make unnecessary. It holds nothing but the store handle and a clock,
both constructor arguments, so a test constructs one over rows of its own with a
clock it controls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.quoting_proposals import (
    quote_approval_inferences as inferences,
    quote_approval_rules as rules,
    quote_approval_vocabulary as vocab,
)
from dsr.store import RecordStore

#: The line-item collection this workflow reads. Named rather than required: a quote
#: written by a foreign workflow that stores its lines elsewhere is still approvable
#: by its quote-level filters, and a hard dependency would make the workflow refuse
#: to run at all.
LINE_ITEMS = "wf086_line_item"


class QuoteApprovalEngine:
    """The workflow, over one audited store."""

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._clock = now or rules.utcnow

    def now(self) -> datetime:
        return self._clock()

    # -- lookups ----------------------------------------------------------- #

    def _require(self, collection: str, record_id: str, code: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record["collection"] != collection:
            raise rules.ApprovalNotFound(code, label, record_id)
        return record

    def rule(self, rule_id: str) -> dict[str, Any]:
        """One configured approval rule, by record id."""
        return self._require(
            vocab.APPROVAL_RULES, rule_id, "unknown_approval_rule", "approval rule"
        )

    def enrolment(self, enrolment_id: str) -> dict[str, Any]:
        """One enrolment, by record id."""
        return self._require(
            vocab.APPROVAL_REQUESTS, enrolment_id, "unknown_approval_request", "approval request"
        )

    def quote(self, quote_id: str) -> dict[str, Any]:
        """The quote this workflow approves, read as data."""
        return self._require(vocab.SOURCE_QUOTES, quote_id, "unknown_quote", "quote")

    def quote_payload(self, quote_id: str) -> dict[str, Any]:
        """A quote's own ``data``, with the envelope id folded in.

        The domain rules scope nothing on an envelope field, but a rule may bind to
        ``id`` because "Filters can target any object ... and any standard or custom
        property", and a quote's id is one of them.
        """
        record = self.quote(quote_id)
        return {**(record.get("data") or {}), "id": record["id"]}

    def line_items(self, quote_id: str) -> list[dict[str, Any]]:
        """The quote's line items, read as data.

        Narrowed by ``quote_id`` through the dynamic index rather than by a foreign
        key this workflow does not own. A team that stores lines under a different
        field name writes that field and the filter resolves through the same path.
        """
        found = self.store.find(LINE_ITEMS, {"quote_id": quote_id}, limit=500)
        return [dict(record.get("data") or {}) for record in found]

    def deal(self, quote_id: str) -> dict[str, Any]:
        """The deal this quote relates to, or an empty mapping.

        An empty mapping rather than a refusal, because a quote with no deal is a
        quote a seller is still allowed to price and approve. A deal filter against
        it reports "this quote has no deal to read from" and does not match.
        """
        payload = self.quote_payload(quote_id)
        related = payload.get("deal") or payload.get("deal_id")
        if isinstance(related, Mapping):
            return dict(related)
        if not related:
            return {}
        record = self.store.get(str(related))
        if record is None:
            return {}
        return {**(record.get("data") or {}), "id": record["id"]}

    # -- rules ------------------------------------------------------------- #

    def create_rule(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a configured approval rule.

        The whole payload is validated on save rather than at enrolment, so a seller
        finds out here that a rule names no approver. A half-configured rule is the
        failure mode the research's own UI prevents by refusing to save.
        """
        data = rules.validate_rule(payload)
        data["quote_object"] = data.get("quote_object") or vocab.SOURCE_QUOTES
        return self.store.create(
            vocab.APPROVAL_RULES, data, room_id=room_id, actor=actor, source=source
        )

    def list_rules(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """The configured rules, oldest first, so the page shows them in order."""
        return self.store.list(
            vocab.APPROVAL_RULES,
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
        """Turn a rule's switch on or off, and revalidate every field.

        The merged payload is revalidated, not just the keys that were sent, because
        a half-applied toggle is a rule that reads as enabled and is not. Turning a
        rule off does not enrol anybody: an inactive rule is read as a rule that did
        not match, which is what ``ENROLMENT_EXEMPT_RULE_INACTIVE`` names.
        """
        record = self.rule(rule_id)
        merged = rules.validate_rule({**(record.get("data") or {}), **dict(payload or {})})
        merged["quote_object"] = merged.get("quote_object") or vocab.SOURCE_QUOTES
        return self.store.update(record["id"], merged, actor=actor, source=source)

    def delete_rule(self, rule_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Remove a rule. The enrolments it created are left alone.

        They name the rule by id and carry their own filter report, so a past
        enrolment still explains itself after the rule is gone.
        """
        record = self.rule(rule_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- matching ---------------------------------------------------------- #

    def matching_rules(
        self,
        quote_id: str,
        *,
        enabled_only: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The active rules a quote matches, with each filter's report.

        Evaluated in the domain, against the quote as stored. The report travels
        with the answer rather than being recomputed on a read, because the flow's
        "**View approval conditions**" has to explain why a quote was held at the
        moment it was held.
        """
        quote = self.quote_payload(quote_id)
        items = self.line_items(quote_id)
        related = self.deal(quote_id)
        matched: list[dict[str, Any]] = []
        for record in self.list_rules(limit=limit):
            data = record.get("data") or {}
            if enabled_only and not data.get("enabled", True):
                continue
            report = rules.matches_filters(
                data.get("filters") or [],
                quote,
                items,
                related,
                str(data.get("matchMode") or vocab.FILTER_MATCH_ALL),
            )
            if not report["matched"]:
                continue
            matched.append({"rule": {**data, "id": record["id"]}, "report": report})
        return matched

    def conditions(self, quote_id: str) -> dict[str, Any]:
        """The "**View approval conditions**" answer, before any submission.

        A GET because it checks and writes nothing. It is the same evaluation the
        enrolment performs, so what a seller is shown is what will be enforced.
        """
        matched = self.matching_rules(quote_id)
        creator = self._creator(quote_id)
        plan: list[dict[str, Any]] = []
        required = False
        for entry in matched:
            resolved = rules.resolve_approvers(entry["rule"].get("approvers") or [], creator)
            if not resolved["exempt"]:
                required = True
            plan.append(
                {
                    "rule_id": entry["rule"].get("id"),
                    "rule_label": entry["rule"].get("label"),
                    "requirement": entry["rule"].get("requirement"),
                    "matched_filters": entry["report"]["filters"],
                    "approvers": resolved["approvers"],
                    "configured_approvers": resolved["configured"],
                    "removed_approvers": resolved["removed"],
                    "exempt": resolved["exempt"],
                }
            )
        return {
            "quote_id": quote_id,
            "creator": creator,
            "required": required,
            "rules": plan,
            "matched_rules": len(plan),
            "self_approval": inferences.self_approval(),
            "unlock_targets": list(vocab.UNLOCK_TARGET_STATES),
        }

    def _creator(self, quote_id: str) -> str | None:
        """Who created a quote, by the field names a foreign writer may have used."""
        payload = self.quote_payload(quote_id)
        for field in (
            "creator",
            "created_by",
            "createdBy",
            "owner",
            "hs_owner_id",
            "hubspot_owner_id",
        ):
            value = payload.get(field)
            if isinstance(value, Mapping):
                value = value.get("id") or value.get("email")
            if value not in (None, ""):
                return str(value)
        return None

    # -- enrolment --------------------------------------------------------- #

    def submit(
        self,
        quote_id: str,
        *,
        actor: str | None,
        source: str,
        trigger: str = vocab.TRIGGER_SUBMIT,
        notes_to_approver: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Enrol a quote for approval, and tell the approvers.

        The researched moment: "click **Request approval** -> add **Notes to
        approver** -> the quote moves to *Pending approval*", and "approval enrolment
        and notification fire on the publish/share attempt".

        Four outcomes, and all four are recorded rather than two:

        ``enrolled``
            A rule matched and an approver other than the creator must decide. The
            enrolment is ``PENDING_APPROVAL``, a decision row per approver is created
            outstanding, and a notification row is written per approver per channel.
        ``exempt``
            Every matched rule's only approver is the creator. The research says the
            quote won't require approval, so the enrolment is created in ``DRAFT``
            with the exemption named and no approver is notified.
        ``no_match``
            No rule matched, so approval was never required. No enrolment is written
            and the answer says why, because a seller who asked for approval deserves
            to be told there was no rule to apply.
        ``inactive``
            A rule matched but its switch is off. Same answer, different reason.
        """
        moment = self.now()
        trigger = rules.normalise_trigger(trigger)
        quote = self.quote_payload(quote_id)
        creator = self._creator(quote_id) or actor
        enabled = self.matching_rules(quote_id, enabled_only=True)
        every = self.matching_rules(quote_id, enabled_only=False)
        if not enabled:
            return self._no_enrolment(
                quote_id, creator, trigger, every, moment, actor=actor, source=source
            )

        plan = self._plan(enabled, creator)
        required = [entry for entry in plan if not entry["exempt"]]
        if not required:
            return self._exempt_enrolment(
                quote_id,
                quote,
                creator,
                trigger,
                plan,
                notes_to_approver,
                moment,
                actor=actor,
                source=source,
                room_id=room_id,
            )
        return self._enrol(
            quote_id,
            quote,
            creator,
            trigger,
            required,
            notes_to_approver,
            moment,
            actor=actor,
            source=source,
            room_id=room_id,
        )

    def _plan(
        self, matched: Sequence[Mapping[str, Any]], creator: str | None
    ) -> list[dict[str, Any]]:
        """One entry per matched rule, with the creator already removed."""
        plan: list[dict[str, Any]] = []
        for entry in matched:
            rule = entry["rule"]
            resolved = rules.resolve_approvers(rule.get("approvers") or [], creator)
            plan.append(
                {
                    "rule": dict(rule),
                    "report": entry["report"],
                    "resolved": resolved,
                    "exempt": bool(resolved["exempt"]),
                }
            )
        return plan

    def _enrol(
        self,
        quote_id: str,
        quote: Mapping[str, Any],
        creator: str | None,
        trigger: str,
        plan: Sequence[Mapping[str, Any]],
        notes_to_approver: str | None,
        moment: datetime,
        *,
        actor: str | None,
        source: str,
        room_id: str | None,
    ) -> dict[str, Any]:
        """The pending enrolment, its outstanding decisions and its notifications."""
        head = plan[0]
        rule = {**head["rule"], "_rule_id": head["rule"].get("id")}
        payload = rules.new_enrolment(
            rule,
            quote,
            creator=creator,
            now=moment,
            notes_to_approver=notes_to_approver,
            trigger=trigger,
            matched_filters=head["report"]["filters"],
        )
        # Several rules can match one quote. The first is the head because the
        # research's builder is one rule at a time and the flow reports one set of
        # conditions; the rest are named so a reviewer can see the quote also matched
        # a second policy rather than silently getting the first one's answer.
        payload[vocab.MATCHED_RULES] = [str(entry["rule"].get("id") or "") for entry in plan]
        payload["extra_rules"] = [
            {
                "rule_id": entry["rule"].get("id"),
                "rule_label": entry["rule"].get("label"),
                "requirement": entry["rule"].get("requirement"),
                "approvers": entry["resolved"]["approvers"],
                "matched_filters": entry["report"]["filters"],
            }
            for entry in plan[1:]
        ]
        created = self.store.create(
            vocab.APPROVAL_REQUESTS, payload, room_id=room_id, actor=actor, source=source
        )

        for entry in plan:
            for identity in entry["resolved"]["approvers"]:
                self.store.create(
                    vocab.APPROVAL_DECISIONS,
                    {
                        "enrolment_id": created["id"],
                        "quote_id": quote_id,
                        "approver": identity,
                        "decision": None,
                        "decided_at": None,
                        "reason": None,
                        "outstanding": True,
                    },
                    room_id=room_id,
                    actor="system",
                    source=source,
                )
            for identity in entry["resolved"]["approvers"]:
                for channel in entry["rule"].get("channels") or [
                    vocab.CHANNEL_IN_APP,
                    vocab.CHANNEL_EMAIL,
                ]:
                    self._notify(
                        created,
                        identity,
                        vocab.RECIPIENT_APPROVER,
                        vocab.NOTIFY_APPROVAL_REQUESTED,
                        channel,
                        room_id=room_id,
                        source=source,
                    )
        self._activity(created, vocab.ACTIVITY_REQUESTED, room_id=room_id, source=source)
        return {
            "enrolled": True,
            "required": True,
            "reason": None,
            "explanation": None,
            "quote_id": quote_id,
            "creator": creator,
            "trigger": trigger,
            "evaluated_at": rules.stamp(moment),
            "approvers": payload["approvers"],
            "removed_approvers": payload["removed_approvers"],
            "enrolment": self.enrolment_view(self.enrolment(created["id"])),
        }

    def _exempt_enrolment(
        self,
        quote_id: str,
        quote: Mapping[str, Any],
        creator: str | None,
        trigger: str,
        plan: Sequence[Mapping[str, Any]],
        notes_to_approver: str | None,
        moment: datetime,
        *,
        actor: str | None,
        source: str,
        room_id: str | None,
    ) -> dict[str, Any]:
        """The sole-approver exemption, recorded as a row rather than as nothing.

        "If a designated approver creates a quote, and they're the only approver, the
        quote won't require approval." A no-op would be indistinguishable from a bug,
        so the exemption is written with its reason and it shows on the activity log
        under a name this build marked as its own.
        """
        head = plan[0]
        rule = {**head["rule"], "_rule_id": head["rule"].get("id")}
        payload = rules.new_enrolment(
            rule,
            quote,
            creator=creator,
            now=moment,
            notes_to_approver=notes_to_approver,
            trigger=trigger,
            matched_filters=head["report"]["filters"],
        )
        payload["exemption_reason"] = vocab.ENROLMENT_EXEMPT_SOLE_APPROVER
        payload["extra_rules"] = [
            {"rule_id": entry["rule"].get("id"), "rule_label": entry["rule"].get("label")}
            for entry in plan[1:]
        ]
        created = self.store.create(
            vocab.APPROVAL_REQUESTS, payload, room_id=room_id, actor=actor, source=source
        )
        self._activity(
            created,
            vocab.ACTIVITY_ENROLLED_WITHOUT_APPROVAL,
            room_id=room_id,
            source=source,
        )
        return {
            "enrolled": True,
            "required": False,
            "reason": vocab.ENROLMENT_EXEMPT_SOLE_APPROVER,
            "explanation": vocab.ENROLMENT_EXEMPTION_LABELS[vocab.ENROLMENT_EXEMPT_SOLE_APPROVER],
            "quote_id": quote_id,
            "creator": creator,
            "trigger": trigger,
            "evaluated_at": rules.stamp(moment),
            "approvers": [],
            "removed_approvers": payload["removed_approvers"],
            "enrolment": self.enrolment_view(self.enrolment(created["id"])),
        }

    def _no_enrolment(
        self,
        quote_id: str,
        creator: str | None,
        trigger: str,
        every: Sequence[Mapping[str, Any]],
        moment: datetime,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """No active rule matched, so nothing is written.

        The reason is ``no_rule_matched`` when nothing matched at all and
        ``matched_rule_is_inactive`` when a rule matched and its switch is off. Two
        reasons because the fix is different: the seller writes a filter in one case
        and turns a switch on in the other.
        """
        return {
            "enrolled": False,
            "required": False,
            "reason": (
                vocab.ENROLMENT_EXEMPT_RULE_INACTIVE if every else vocab.ENROLMENT_EXEMPT_NO_MATCH
            ),
            "explanation": (
                vocab.ENROLMENT_EXEMPTION_LABELS[vocab.ENROLMENT_EXEMPT_RULE_INACTIVE]
                if every
                else vocab.ENROLMENT_EXEMPTION_LABELS[vocab.ENROLMENT_EXEMPT_NO_MATCH]
            ),
            "quote_id": quote_id,
            "creator": creator,
            "trigger": trigger,
            "evaluated_at": rules.stamp(moment),
            "inactive_rules": [entry["rule"].get("label") for entry in every],
            "enrolment": None,
        }

    # -- decisions --------------------------------------------------------- #

    def decide(
        self,
        enrolment_id: str,
        decision: str,
        *,
        actor: str | None,
        source: str,
        approver: str | None = None,
        message: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """An approver approves, or requests changes.

        Five checks, each of which is a different sourced rule, and each of which is
        refused with its own code rather than lumped into one refusal:

        * the enrolment exists (:class:`~dsr.quoting_proposals.quote_approval_rules.ApprovalNotFound`);
        * it is still ``PENDING_APPROVAL`` ("quote_is_not_pending");
        * it has no outcome yet ("request_already_decided");
        * the caller is one of its approvers ("approver_is_not_on_this_request");
        * the caller did not create the quote ("approvers_is_the_creator"), which
          follows from the creator having been removed from the list.

        A change request needs a reason. "enter changes -> **Reject**" and a change
        request with nothing in it is not an instruction the seller can act on.

        The outcome is the requirement, not the vote: one approval satisfies an
        "At least one approver required" rule and every approval satisfies an "All
        approvers required" one. A change request ends the round immediately, because
        the research says the seller then edits and re-submits, and the re-submission
        starts a fresh round with no decision carried forward.
        """
        record = self.enrolment(enrolment_id)
        data = dict(record.get("data") or {})
        verdict = rules.normalise_decision(decision)
        identity = str(approver or actor or "").strip()
        if not identity:
            raise rules.refuse(
                "approver_is_not_on_this_request",
                **{"approver": "Name the approver making this decision."},
            )

        if str(data.get("status") or "") != vocab.STATE_PENDING_APPROVAL:
            raise rules.refuse(
                "quote_is_not_pending",
                f"This enrolment is {data.get('status')}, not {vocab.STATE_PENDING_APPROVAL}.",
            )
        if data.get("outcome"):
            raise rules.refuse("request_already_decided")

        approvers = [str(one) for one in data.get("approvers") or []]
        if identity not in approvers:
            raise rules.refuse(
                "approver_is_not_on_this_request",
                f"{identity} is not one of the approvers on {enrolment_id}.",
            )
        if data.get("creator") and identity == str(data.get("creator")):
            raise rules.refuse(
                "approver_is_the_creator",
                f"{identity} created this quote. {vocab.NO_SELF_APPROVAL_QUOTE}",
            )
        if verdict == vocab.DECISION_REQUEST_CHANGES and not str(message or "").strip():
            raise rules.refuse("reason_required_to_request_changes")

        moment = self.now()
        self.store.update(
            self._decision_row(record["id"], identity)["id"],
            {
                "decision": verdict,
                "decided_at": rules.stamp(moment),
                "reason": str(message or "").strip() or None,
                "outstanding": False,
            },
            actor=identity,
            source=source,
        )
        decisions = self.decisions_for(enrolment_id)
        tally = rules.requirement_met(
            str(data.get("requirement") or vocab.REQUIREMENT_ALL),
            approvers,
            [one["approver"] for one in decisions if one.get("decision") == vocab.DECISION_APPROVE],
            [
                one["approver"]
                for one in decisions
                if one.get("decision") == vocab.DECISION_REQUEST_CHANGES
            ],
        )
        state = vocab.STATE_PENDING_APPROVAL
        outcome: str | None = None
        if verdict == vocab.DECISION_REQUEST_CHANGES:
            # A change request ends the round whatever the requirement says. The
            # research's sentence is about needing every approver again on
            # re-submission, which implies the round stopped here.
            state, outcome = vocab.STATE_REJECTED, "changes_requested"
        elif tally["met"]:
            state, outcome = vocab.STATE_APPROVED, "approved"

        patch: dict[str, Any] = {
            "status": state,
            "outcome": outcome,
            "decided_at": rules.stamp(moment),
            "decided_by": identity if outcome else None,
            "tally": tally,
            "decisions": [dict(one) for one in decisions],
            "latest_message": str(message or "").strip() or None,
        }
        updated = self.store.update(record["id"], patch, actor=actor or identity, source=source)

        for row in decisions:
            if row.get("outstanding") and row.get("decision") is None:
                self.store.update(
                    row["decision_id"],
                    {"outstanding": False, "closed_at": rules.stamp(moment)},
                    actor="system",
                    source=source,
                )

        named = rules.activity_for(state, exempt=bool(data.get("exempt")))
        if outcome:
            self._activity(
                updated, named["activity"], room_id=record.get("room_id") or room_id, source=source
            )
            self._notify_creator(
                updated, named["activity"], room_id=record.get("room_id") or room_id, source=source
            )

        return {
            "enrolment": self.enrolment_view(self.enrolment(updated["id"])),
            "decision": verdict,
            "approver": identity,
            "tally": tally,
            "state": state,
            "outcome": outcome,
            "activity": named["activity"],
            "activity_sourced": named["sourced"],
            "shareable": rules.evaluate_share(state)["shareable"],
            "message": str(message or "").strip() or None,
        }

    def _decision_row(self, enrolment_id: str, approver: str) -> dict[str, Any]:
        """The outstanding decision row for one approver, created on enrolment."""
        for row in self.decisions_for(enrolment_id):
            if str(row.get("approver")) == str(approver):
                return {"id": row["decision_id"]}
        # A rule saved with an approver the enrolment did not carry cannot happen,
        # because the enrolment writes a row per resolved approver. So this branch is
        # the "enrolment written before a decision row existed" case, and it is
        # written rather than raised: refusing a legitimate approval because of a
        # bookkeeping gap would be a worse failure than the gap.
        created = self.store.create(
            vocab.APPROVAL_DECISIONS,
            {
                "enrolment_id": enrolment_id,
                "approver": approver,
                "decision": None,
                "outstanding": True,
            },
            actor="system",
            source="seed",
        )
        return {"id": created["id"]}

    def decisions_for(self, enrolment_id: str) -> list[dict[str, Any]]:
        """One approver's decision rows, oldest first.

        Ordered by ``created_at`` because that is the insertion sequence the store
        defines, so the answer does not depend on which row the query plan reached.
        """
        rows = self.store.find(vocab.APPROVAL_DECISIONS, {"enrolment_id": enrolment_id}, limit=200)
        payload = [
            dict(record.get("data") or {})
            | {"_id": record["id"], "decision_id": record["id"], "_room_id": record.get("room_id")}
            for record in rows
        ]
        payload.sort(
            key=lambda row: (str(row.get("decided_at") or ""), str(row.get("approver") or ""))
        )
        return payload

    # -- sharing ----------------------------------------------------------- #

    def share(
        self,
        quote_id: str,
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Share a quote, or refuse with the researched sentence.

        "only on approval can the quote be **Share**d (state ``Shared``) and sent to
        the buyer." The check reads this workflow's own enrolments rather than the
        quote's ``hs_status``, because the quote is a record this workflow does not
        own. A refusal is written to the activity log under a name this build marked
        as its own, so "the product stopped a share" is a row rather than a gap.
        """
        quote = self.quote_payload(quote_id)
        state = str(quote.get(vocab.HS_STATUS) or vocab.STATE_DRAFT).strip().upper()
        latest = self.latest_enrolment(quote_id)
        if latest is not None:
            state = str((latest.get("data") or {}).get("status") or state)
        outcome = rules.evaluate_share(state)
        if not outcome["shareable"]:
            activity = rules.activity_for(state, share_refused=True)
            self.store.create(
                vocab.ACTIVITIES,
                {
                    "activity": activity["activity"],
                    "sourced": activity["sourced"],
                    "quote_id": quote_id,
                    "state": state,
                    "reason": outcome["reason"],
                    "at": rules.stamp(self.now()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            # The share-gate code, not the quote code: the request is well formed and
            # conflicts with the quote's state, so it is a 409 rather than a 422.
            raise rules.ApprovalRefusal("quote_not_approved_to_share", outcome["reason"])
        return {
            "shared": True,
            "quote_id": quote_id,
            "state": vocab.STATE_SHARED,
            "from_state": state,
            "reason": outcome["reason"],
            "evidence": vocab.SHARE_ON_APPROVAL_QUOTE,
        }

    # -- reads for the page ------------------------------------------------ #

    def latest_enrolment(self, quote_id: str) -> dict[str, Any] | None:
        """The most recent enrolment for a quote, or ``None``.

        Sorted on ``created_at`` with the insertion order as the tie-break, because
        two submissions of one quote inside the same millisecond tie and an arbitrary
        answer to "what state is this quote in" is a wrong answer to a money question.
        """
        rows = self.store.find(vocab.APPROVAL_REQUESTS, {"quote_id": quote_id}, limit=200)
        if not rows:
            return None
        rows.sort(key=lambda row: (row.get("created_at") or "", row["id"]))
        return rows[-1]

    def quote_state(self, quote_id: str) -> dict[str, Any]:
        """The state a quote is in for approval purposes, and where it came from.

        The quote's own ``hs_status`` is reported and then overridden by this
        workflow's latest enrolment, because the enrolment is the newer fact. Both are
        returned so a reader can see which one answered.
        """
        quote = self.quote_payload(quote_id)
        stated = str(quote.get(vocab.HS_STATUS) or vocab.STATE_DRAFT).strip().upper()
        latest = self.latest_enrolment(quote_id)
        effective = str((latest.get("data") or {}).get("status") or stated) if latest else stated
        locked = rules.evaluate_locked(effective, quote.get(vocab.HS_LOCKED))
        return {
            "quote_id": quote_id,
            "stated_status": stated,
            "effective_status": effective,
            "authority": "approval_enrolment" if latest else "quote_hs_status",
            "enrolment_id": latest["id"] if latest else None,
            "locked": locked["locked"],
            "unlock_targets": locked["unlock_targets"],
            "share": rules.evaluate_share(effective),
            "evidence": vocab.UNLOCK_TARGET_QUOTE,
        }

    def list_enrolments(
        self,
        *,
        status: str | None = None,
        quote_id: str | None = None,
        approver: str | None = None,
        room_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Enrolments, newest first, filtered through the dynamic index.

        ``approver`` is a nested lookup rather than a filter on the record: the
        approver list lives on the enrolment and each approver also has a decision
        row, so a caller asking "what is waiting on me" gets enrolments whose
        outstanding decision is theirs.
        """
        where: dict[str, Any] = {}
        if status:
            where["status"] = str(status).strip().upper()
        if quote_id:
            where["quote_id"] = str(quote_id)
        rows = (
            self.store.find(vocab.APPROVAL_REQUESTS, where, limit=min(limit, 1000))
            if where
            else self.store.list(vocab.APPROVAL_REQUESTS, room_id=room_id, limit=min(limit, 1000))
        )
        scoped = [row for row in rows if room_id is None or row.get("room_id") == room_id]
        if approver:
            # The approver and the enrolment id both live in the decision row's
            # ``data``, not on its envelope. Reading them off the envelope yields
            # None for every row, so the filter matches nothing and the queue answers
            # "nothing is waiting on you" while it is full.
            mine = {
                str((row.get("data") or {}).get("enrolment_id"))
                for row in self.store.find(
                    vocab.APPROVAL_DECISIONS, {"approver": str(approver)}, limit=500
                )
                if (row.get("data") or {}).get("enrolment_id")
            }
            scoped = [row for row in scoped if row["id"] in mine]
        return [self.enrolment_view(row, approver=approver) for row in scoped[:limit]]

    def enrolment_view(
        self, record: Mapping[str, Any], *, approver: str | None = None
    ) -> dict[str, Any]:
        """One enrolment with its decisions, its tally and its conditions panel.

        The conditions panel is :func:`~dsr.quoting_proposals.quote_approval_rules.conditions_report`
        on the stored filter report, so "**View approval conditions**" is answered
        from the record as it was at enrolment.
        """
        data = dict(record.get("data") or {})
        decisions = self.decisions_for(str(record["id"]))
        tally = rules.requirement_met(
            str(data.get("requirement") or vocab.REQUIREMENT_ALL),
            data.get("approvers") or [],
            [one["approver"] for one in decisions if one.get("decision") == vocab.DECISION_APPROVE],
            [
                one["approver"]
                for one in decisions
                if one.get("decision") == vocab.DECISION_REQUEST_CHANGES
            ],
        )
        payload: dict[str, Any] = {
            **record,
            # Whether this enrolment was created by a submission. The submission
            # envelope carries `enrolled` too, and this flag is what makes a bare read
            # of one enrolment self-describing.
            "enrolled": True,
            "required": not bool(data.get("exempt")),
            "status": data.get("status"),
            # Lifted to the top level for the same reason status and approvers are:
            # a client asks which of the three researched moments created this row
            # without reaching into the envelope's own data blob.
            "trigger": data.get("trigger"),
            # The two researched notes, lifted for the same reason: the rule's own
            # approval note and the request's note to the approver are what a panel
            # shows, and neither should need a dive into the envelope's data.
            vocab.APPROVAL_NOTE: data.get(vocab.APPROVAL_NOTE),
            vocab.NOTES_TO_APPROVER: data.get(vocab.NOTES_TO_APPROVER),
            "exempt": bool(data.get("exempt")),
            "exemption_reason": data.get("exemption_reason"),
            "approvers": list(data.get("approvers") or []),
            "removed_approvers": list(data.get("removed_approvers") or []),
            "decisions": decisions,
            "tally": tally,
            "conditions": rules.conditions_report(data),
            "shareable": rules.evaluate_share(str(data.get("status") or ""))["shareable"],
            "locked": rules.evaluate_locked(str(data.get("status") or ""), data.get("locked"))[
                "locked"
            ],
        }
        if approver:
            # Who may act, computed on the read rather than only refused on the
            # write, so a page can hide a button that would 403 and can label the
            # reason. Both are still enforced by ``decide``.
            mine = next(
                (one for one in decisions if str(one.get("approver")) == str(approver)), None
            )
            payload["for_you"] = {
                "on_this_request": bool(mine),
                "already_decided": bool(mine and mine.get("decision")),
                "is_the_creator": bool(data.get("creator"))
                and str(data.get("creator")) == str(approver),
                "can_decide": bool(mine and not mine.get("decision")),
            }
        return payload

    def activities(self, quote_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """A quote's activity log, newest first."""
        rows = self.store.find(vocab.ACTIVITIES, {"quote_id": quote_id}, limit=min(limit, 1000))
        return [dict(row.get("data") or {}) | {"id": row["id"]} for row in rows]

    def notifications(
        self, *, quote_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The recorded notifications, newest first."""
        rows = (
            self.store.find(
                vocab.NOTIFICATIONS, {"quote_id": str(quote_id)}, limit=min(limit, 1000)
            )
            if quote_id
            else self.store.list(vocab.NOTIFICATIONS, limit=min(limit, 1000))
        )
        return [dict(row.get("data") or {}) | {"id": row["id"]} for row in rows]

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The board: how much is waiting, and on whom."""
        rows = self.store.list(vocab.APPROVAL_REQUESTS, room_id=room_id, limit=1000)
        # The record's own ``data``, not the envelope. The status lives in the
        # payload, and reading it off the envelope would report every quote as
        # DRAFT and hide the one a reviewer is waiting on.
        enrolments = [dict(record.get("data") or {}) for record in rows]
        board = rules.summary_counts(
            self.store.list(vocab.APPROVAL_RULES, room_id=room_id, limit=1000), enrolments
        )
        board["room_id"] = room_id
        # Read from ``data`` for the same reason as the approver filter above.
        board["approvers_waiting"] = {
            str((row.get("data") or {}).get("approver")): 0
            for row in self.store.list(vocab.APPROVAL_DECISIONS, limit=1000)
            if (row.get("data") or {}).get("outstanding")
            and (row.get("data") or {}).get("approver")
        }
        board["notified"] = len(self.store.list(vocab.NOTIFICATIONS, room_id=room_id, limit=1000))
        board["collections"] = [
            vocab.APPROVAL_RULES,
            vocab.APPROVAL_REQUESTS,
            vocab.APPROVAL_DECISIONS,
            vocab.ACTIVITIES,
            vocab.NOTIFICATIONS,
        ]
        board["self_approval"] = inferences.self_approval()
        return board

    # -- notifications and activities -------------------------------------- #

    def _notify(
        self,
        enrolment: Mapping[str, Any],
        recipient: str,
        role: str,
        moment: str,
        channel: str,
        *,
        room_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One notification row, marked recorded and not delivered.

        Every value of ``dispatched_by`` matters: ``recorded`` is what stops a
        reviewer reading this as a delivery receipt, and
        :data:`~dsr.quoting_proposals.quote_approval_vocabulary.RECORDED_NOT_DELIVERED_NOTE`
        says why in the row itself.
        """
        data = dict(enrolment.get("data") or {})
        return self.store.create(
            vocab.NOTIFICATIONS,
            {
                "quote_id": data.get("quote_id"),
                "enrolment_id": enrolment.get("id"),
                "recipient": recipient,
                "recipient_role": role,
                "moment": moment,
                "channel": channel,
                "channel_label": vocab.NOTIFICATION_CHANNEL_LABELS.get(channel, channel),
                "subject": (
                    f"{data.get('quote_name') or data.get('quote_id')} is waiting for your approval."
                ),
                "action": "Go to quote",
                "note": data.get(vocab.APPROVAL_NOTE) or None,
                "dispatched_by": vocab.DISPATCH_DISPATCHED_BY,
                "delivered": False,
                "recorded_only_note": vocab.RECORDED_NOT_DELIVERED_NOTE,
            },
            room_id=room_id,
            actor="system",
            source=source,
        )

    def _notify_creator(
        self, enrolment: Mapping[str, Any], activity: str, *, room_id: str | None, source: str
    ) -> None:
        """Tell the creator the outcome, on the channels the rule names."""
        data = dict(enrolment.get("data") or {})
        creator = data.get("creator")
        if not creator:
            return
        for channel in data.get("channels") or [vocab.CHANNEL_IN_APP, vocab.CHANNEL_EMAIL]:
            self._notify(
                enrolment,
                str(creator),
                vocab.RECIPIENT_CREATOR,
                vocab.NOTIFY_CREATOR_NOTIFIED,
                channel,
                room_id=room_id,
                source=source,
            )

    def _activity(
        self,
        enrolment: Mapping[str, Any],
        activity: str,
        *,
        room_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One quote activity row, carrying the research's own name or this build's."""
        data = dict(enrolment.get("data") or {})
        return self.store.create(
            vocab.ACTIVITIES,
            {
                "activity": activity,
                "quote_id": data.get("quote_id"),
                "enrolment_id": enrolment.get("id"),
                "actor": data.get("creator"),
                "at": rules.stamp(self.now()),
                "state": data.get("status"),
                "sourced": activity in vocab.SOURCED_ACTIVITY_TYPES,
            },
            room_id=room_id,
            actor="system",
            source=source,
        )


def vocabulary() -> dict[str, Any]:
    """Every researched term, served as data."""
    return vocab.catalogue()


def inferences_report() -> dict[str, Any]:
    """Every judgement call, served as data."""
    return {
        "count": inferences.count(),
        "decisions": inferences.describe(),
        "self_approval": inferences.self_approval(),
    }
