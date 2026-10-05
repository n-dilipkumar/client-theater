"""WF-103: the reads and the writes, behind one façade.

:class:`ProposalEngine` is what the HTTP layer calls. It holds nothing but a
:class:`~dsr.store.RecordStore` handle, which is why the feature module builds one per
request from ``StoreDep`` rather than hanging it on ``app.state`` - an ``app.state``
entry would mean editing ``dsr/api.py``, and the whole point of the feature host is
that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration. The
audit row is the product's guarantee, and an audit row that names a string rather than
a route cannot be traced back to the request that caused it. A required keyword means
the omission is a ``TypeError`` at the call site rather than a silently untraceable row
in production.

The path a document walks
-------------------------
:meth:`create_document` is the start and it is *asynchronous by research*: the create
call returns ``document.uploaded`` and the document becomes ``document.draft`` later.
:meth:`render` is that second step, and nothing may be sent before it - "Attempting to
send a document that is still in `document.uploaded` status returns a `409 Conflict`
response", which is why :meth:`send` raises a 409 rather than a 500.

:meth:`send` is the gate. On a template with an approval workflow it moves the document
to ``waiting_approval``; once approved, the *second* send is what moves it to ``sent``.
:meth:`consume_webhook` is the writeback, and it is the only path that changes a
document from outside this room.

Nothing here reaches a vendor. The document, its id and its links are derived; see
:mod:`dsr.proposal_documents.inferences` for why, and
:data:`~dsr.proposal_documents.vocabulary.DOCUMENT_DERIVED_NOTE` for the sentence the
page shows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.proposal_documents import rules, vocabulary as vocab
from dsr.proposal_documents.errors import ProposalNotFound, ProposalRefusal

#: The eight collections this workflow owns, named here so a filter and a route cannot
#: disagree about which one they mean.
DOCUMENTS = vocab.DOCUMENTS
STATE_CHANGES = vocab.STATE_CHANGES
RECIPIENTS = vocab.RECIPIENTS
APPROVALS = vocab.APPROVALS
PRICING_TABLES = vocab.PRICING_TABLES
CRM_SYNC = vocab.CRM_SYNC
WEBHOOK_EVENTS = vocab.WEBHOOK_EVENTS
ACTIVITY = vocab.ACTIVITY
SOURCE_DEALS = vocab.SOURCE_DEALS

#: The actor every write is attributed to, so a review of the activity log reads
#: "the workflow" rather than a person who did not do it.
ACTOR = "wf-103"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _payload(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten a stored record into its payload, keeping the record id.

    Every record this project stores nests its fields under ``data``, so a read that
    skips this returns an envelope and every field lookup silently misses. The id is
    merged back in because the payload has no column of its own for it, and a caller
    needs to address the record it just read.
    """
    if not record:
        return {}
    return {**dict(record.get("data") or {}), "id": record.get("id")}


def _payloads(rows: Any) -> list[dict[str, Any]]:
    return [_payload(row) for row in rows]


class ProposalEngine:
    """Build a proposal from a deal, gate it, deliver it, and sync the status back."""

    def __init__(self, store: Any, *, now: Any = None) -> None:
        self.store = store
        self._now = now or _now

    # ----------------------------------------------------------------- #
    # Activity
    # ----------------------------------------------------------------- #

    def log(
        self,
        document_id: str,
        activity: str,
        detail: str = "",
        *,
        source: str,
        room_id: str | None = None,
        actor: str = ACTOR,
    ) -> dict[str, Any]:
        """One row in the activity log, sourced against a name in the vocabulary.

        ``sourced`` travels with the row so a reader can tell a name the research
        states from one this build chose, rather than having to guess from the wording.
        """
        return _payload(
            self.store.create(
                ACTIVITY,
                {
                    "document_id": document_id,
                    "activity": activity,
                    "detail": str(detail or ""),
                    "sourced": activity in vocab.SOURCED_ACTIVITY_TYPES,
                    "at": self._now(),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        )

    def activity_for(self, document_id: str) -> list[dict[str, Any]]:
        rows = _payloads(self.store.find(ACTIVITY, {"document_id": document_id}))
        return sorted(rows, key=lambda one: str(one.get("at") or ""), reverse=True)

    def _transition(
        self,
        document_id: str,
        state: str,
        detail: str = "",
        *,
        source: str,
        room_id: str | None = None,
        actor: str = ACTOR,
    ) -> dict[str, Any]:
        """Write the state change and the history row for it, in two audited writes.

        The history is a separate collection rather than a field on the document so
        that "how did this document get to completed" is a query rather than a replay
        of the audit log.
        """
        self.store.create(
            STATE_CHANGES,
            {
                "document_id": document_id,
                "state": state,
                "vendor_state": vocab.vendor_state(state),
                "detail": str(detail or ""),
                "at": self._now(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return _payload(
            self.store.update(document_id, {"state": state}, actor=actor, source=source)
        )

    # ----------------------------------------------------------------- #
    # Creating a proposal
    # ----------------------------------------------------------------- #

    def find_deal(self, deal_id: str) -> dict[str, Any] | None:
        """The CRM record the document is built from, read as data.

        The user flow starts "In your CRM, the deal/opportunity is won or a quote is
        created", so the record already exists when this workflow runs. This workflow
        reads it and writes the sync back; it never authors the deal.
        """
        return _payload(self.store.get(deal_id)) or None

    def list_deals(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return _payloads(
            self.store.list(SOURCE_DEALS, limit=limit, order_by="created_at", descending=False)
        )

    def line_items_for(self, deal_id: str) -> list[dict[str, Any]]:
        """The line items a deal carries.

        Read as data from the deal's own payload rather than from a second collection,
        because the research says the pricing table is built from "CRM deal/quote
        record + product catalog" and a deal record that carries no line items is a
        real case rather than a bug.
        """
        deal = self.find_deal(deal_id) or {}
        items = deal.get("line_items")
        return (
            [dict(one) for one in items if isinstance(one, Mapping)]
            if isinstance(items, list)
            else []
        )

    def create_document(
        self,
        deal_id: str,
        *,
        source: str,
        name: str = "",
        template_uuid: str = "",
        recipients: Sequence[Mapping[str, Any]] = (),
        currency: str = "USD",
        discount: Any = 0,
        content_placeholders: Sequence[Mapping[str, Any]] = (),
        actor: str = ACTOR,
        room_id: str | None = None,
        shared_key: str = "",
    ) -> dict[str, Any]:
        """Build the create body from a deal and write the document in ``uploaded``.

        The researched sequence, step one: the create call returns ``document.uploaded``
        and the document is not a draft yet. That is the state this writes, because a
        room that reported a freshly created document as a draft would let a send
        through the gate the research says refuses it.
        """
        deal = self.find_deal(deal_id)
        if deal is None:
            raise ProposalNotFound("unknown_deal", "deal", deal_id)

        body = rules.build_document_request(
            name=name or deal.get("name") or f"Proposal for {deal_id}",
            template_uuid=template_uuid or deal.get("template_uuid") or "",
            recipients=recipients,
            line_items=self.line_items_for(deal_id),
            currency=currency,
            discount=discount,
            content_placeholders=content_placeholders,
            room_id=room_id,
        )

        table = body["pricing_tables"][0]
        record = self.store.create(
            DOCUMENTS,
            {
                "deal_id": deal_id,
                "name": body["name"],
                "template_uuid": body["template_uuid"],
                "state": vocab.STATE_UPLOADED,
                "vendor_state": vocab.vendor_state(vocab.STATE_UPLOADED),
                "gate": vocab.GATE_OPEN,
                "has_approval_workflow": True,
                "request_body": body,
                "placement": body["placement"],
                "recipients": body["recipients"],
                "grand_total": table["grand_total"],
                # Set from the record id below, in a second patch, because the id is
                # minted by the store and the derived link has to carry the same one a
                # webhook would.
                vocab.DOCUMENT_URL_IS_DERIVED: True,
                "created_at": self._now(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        document = _payload(
            self.store.update(
                record["id"],
                {"details_url": vocab.derived_document_url(record["id"])},
                actor=actor,
                source=source,
            )
        )

        self.store.create(
            PRICING_TABLES,
            {"document_id": document["id"], **table},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        for recipient in body["recipients"]:
            self.store.create(
                RECIPIENTS,
                {"document_id": document["id"], **recipient, vocab.RECIPIENT_HAS_COMPLETED: False},
                room_id=room_id,
                actor=actor,
                source=source,
            )
        self._transition(
            document["id"],
            vocab.STATE_UPLOADED,
            "Created from the deal record; the document API has not rendered it yet.",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        self.log(
            document["id"],
            vocab.ACTIVITY_DOCUMENT_CREATED,
            document["name"],
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return {
            "document": self.document_view(document["id"]),
            "request_body": body,
            "note": vocab.DOCUMENT_DERIVED_NOTE,
        }

    def render(
        self,
        document_id: str,
        *,
        source: str,
        actor: str = ACTOR,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Move a document from ``uploaded`` to ``draft``.

        Step two of the researched sequence. Kept as its own call because the research
        is explicit that the transition is asynchronous, and a room that collapsed it
        into the create would have nothing to refuse a too-early send with.
        """
        document = self.require_document(document_id)
        if vocab.normalise_state(document.get("state")) != vocab.STATE_UPLOADED:
            raise ProposalRefusal(
                "document_already_sent",
                f"The document is {document.get('vendor_state')}, so it is past the uploaded "
                "state and there is nothing to render.",
            )
        self._transition(
            document_id,
            vocab.STATE_DRAFT,
            "Rendered from the template with the pricing table merged.",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        self.log(
            document_id,
            vocab.ACTIVITY_DOCUMENT_RENDERED,
            vocab.vendor_state(vocab.STATE_DRAFT),
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return self.document_view(document_id)

    # ----------------------------------------------------------------- #
    # The gate
    # ----------------------------------------------------------------- #

    def require_document(self, document_id: str) -> dict[str, Any]:
        found = _payload(self.store.get(document_id))
        if not found:
            raise ProposalNotFound("unknown_document", "document", document_id)
        return found

    def approvals_for(self, document_id: str) -> list[dict[str, Any]]:
        return _payloads(self.store.find(APPROVALS, {"document_id": document_id}))

    def decide_approval(
        self,
        document_id: str,
        decision: str,
        *,
        source: str,
        approver: str = "",
        reason: str = "",
        message: str = "",
        actor: str = ACTOR,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one approver's decision on the internal gate.

        A rejection needs a reason, and the refusal happens before any row is written,
        so a refused decision never leaves a half-recorded approval behind. The gate
        state is recomputed from the stored decisions on every read rather than kept as
        a counter, so it cannot drift from its own history.
        """
        document = self.require_document(document_id)
        verdict = rules.normalise_decision(decision)
        recorded = rules.record_approval(
            document.get("gate") or vocab.GATE_OPEN,
            verdict,
            approver=approver,
            reason=reason,
            message=message,
        )

        self.store.create(
            APPROVALS,
            {"document_id": document_id, "deal_id": document.get("deal_id"), **recorded},
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.store.update(document_id, {"gate": recorded["gate"]}, actor=actor, source=source)
        self.log(
            document_id,
            vocab.ACTIVITY_GATE_APPROVED
            if verdict == vocab.APPROVAL_DECISION_APPROVE
            else vocab.ACTIVITY_GATE_REJECTED,
            str(
                recorded.get(vocab.APPROVAL_REASON_KEY)
                or recorded.get(vocab.APPROVAL_NOTE_KEY)
                or ""
            ),
            source=source,
            room_id=room_id,
            actor=actor,
        )
        if verdict == vocab.APPROVAL_DECISION_REJECT:
            self._transition(
                document_id,
                vocab.STATE_REJECTED,
                f"Refused for send by {recorded.get(vocab.APPROVAL_ACTOR_KEY) or 'an approver'}.",
                source=source,
                room_id=room_id,
                actor=actor,
            )
        return {
            "document": self.document_view(document_id),
            "decision": recorded["decision"],
            "gate": recorded["gate"],
            "approvals": self.approvals_for(document_id),
        }

    def send(
        self, document_id: str, *, source: str, actor: str = ACTOR, room_id: str | None = None
    ) -> dict[str, Any]:
        """Send, or hold at the internal gate.

        Two refusals, never merged. A document still in ``uploaded`` is a 409 quoting
        the research; a document that has not been approved is the gate, which is the
        vendor's ``waiting_approval`` rather than a room error. Once approved, this is
        the *second* send, and that is what moves the document to ``sent``.
        """
        document = self.require_document(document_id)
        rules.require_sendable(
            document.get("state"),
            gate=str(document.get("gate") or vocab.GATE_OPEN),
            has_approval_workflow=bool(document.get("has_approval_workflow", True)),
        )
        target = rules.after_send(document.get("state"))
        self._transition(
            document_id,
            target,
            "Held for internal approval."
            if target == vocab.STATE_WAITING_APPROVAL
            else "Sent for signature after internal approval.",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        self.log(
            document_id,
            vocab.ACTIVITY_GATE_OPENED
            if target == vocab.STATE_WAITING_APPROVAL
            else vocab.ACTIVITY_SENT,
            vocab.vendor_state(target),
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return {
            "document": self.document_view(document_id),
            "state": target,
            "vendor_state": vocab.vendor_state(target),
            "second_send": target == vocab.STATE_SENT,
            "delivery": vocab.DELIVERY_SIMULATED,
            "note": vocab.DELIVERY_NOTE,
        }

    # ----------------------------------------------------------------- #
    # The quote update, and its destructive default
    # ----------------------------------------------------------------- #

    def update_quote(
        self,
        document_id: str,
        sections: Any,
        *,
        source: str,
        acknowledge: bool = False,
        actor: str = ACTOR,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the pricing table's sections, and say what that removed.

        "Any section or item omitted from the payload will be deleted." The deletions
        are computed first and an unacknowledged destructive update is refused, so the
        researched behaviour is available without being a surprise.
        """
        document = self.require_document(document_id)
        if not isinstance(sections, (list, tuple)):
            raise ProposalRefusal(
                "quote_update_needs_sections",
                vocab.ERROR_CODES["quote_update_needs_sections"][1],
            )
        if vocab.normalise_state(document.get("state")) not in (
            vocab.STATE_DRAFT,
            vocab.STATE_UPLOADED,
        ):
            raise ProposalRefusal(
                "document_already_sent",
                "Only a document in document.uploaded or document.draft may have its quote "
                "section updated.",
            )

        table = _payload(self.store.find(PRICING_TABLES, {"document_id": document_id})[0])
        current = table.get("sections") or []
        effect = rules.require_quote_update(current, sections, acknowledge=acknowledge)

        rebuilt = rules.build_pricing_table(
            [
                dict(item.get("data") or {})
                for section in sections
                if isinstance(section, Mapping)
                for item in (section.get("items") or [])
                if isinstance(item, Mapping)
            ]
        )
        self.store.update(
            self.store.find(PRICING_TABLES, {"document_id": document_id})[0]["id"],
            {
                "sections": sections,
                "subtotal": rebuilt["subtotal"],
                "grand_total": rebuilt["grand_total"],
            },
            actor=actor,
            source=source,
        )
        self.log(
            document_id,
            vocab.ACTIVITY_QUOTE_UPDATED,
            f"{len(effect['removed_sections'])} section(s) and {len(effect['removed_items'])} "
            "item(s) removed by omission.",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return {"document": self.document_view(document_id), "effect": effect}

    # ----------------------------------------------------------------- #
    # The webhook, and the writeback
    # ----------------------------------------------------------------- #

    def event_ids(self, document_id: str) -> list[str]:
        return [
            str(row["data"].get("event_id"))
            for row in self.store.find(WEBHOOK_EVENTS, {"document_id": document_id})
        ]

    def consume_webhook(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
        event_id: str = "",
        signature: str = "",
        shared_key: str = "",
        actor: str = ACTOR,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply one ``document_state_changed`` webhook, idempotently.

        Three gates before anything is written, in the order the research implies them:
        the signature is verified, the event id is de-duplicated, and the document must
        be one this room created. A replayed event changes nothing at all, which is
        what "retries are handled by de-duplicating on `X-PandaDoc-Webhook-Event-Id`"
        requires.
        """
        if not signature:
            raise ProposalRefusal(
                "webhook_signature_missing",
                vocab.ERROR_CODES["webhook_signature_missing"][1],
            )
        if shared_key and not rules.verify_signature(payload, signature, shared_key):
            raise ProposalRefusal(
                "webhook_signature_invalid", vocab.ERROR_CODES["webhook_signature_invalid"][1]
            )
        if str(payload.get("event") or vocab.WEBHOOK_EVENT) != vocab.WEBHOOK_EVENT:
            raise ProposalRefusal(
                "unknown_webhook_event", vocab.ERROR_CODES["unknown_webhook_event"][1]
            )

        document_id = str(payload.get("document_id") or payload.get("id") or "")
        document = self.require_document(document_id)

        if rules.is_duplicate(event_id, self.event_ids(document_id)):
            return {
                "outcome": vocab.WEBHOOK_DUPLICATE,
                "event_id": event_id,
                "document": self.document_view(document_id),
                "applied": False,
                "reason": (
                    f"Event {event_id} was already applied. Retries are de-duplicated on "
                    f"{vocab.WEBHOOK_EVENT_ID_HEADER}, so a replay writes nothing."
                ),
            }

        patch = rules.apply_webhook(payload, document)
        self.store.create(
            WEBHOOK_EVENTS,
            {
                "document_id": document_id,
                "event_id": str(event_id or ""),
                "event": vocab.WEBHOOK_EVENT,
                "state": patch["state"],
                "at": self._now(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.store.update(
            document_id,
            {
                "state": patch["state"],
                "vendor_state": patch["vendor_state"],
                "grand_total": patch["grand_total"],
                "recipients": patch["recipients"],
                vocab.LINKED_OBJECT_KEY: patch[vocab.LINKED_OBJECT_KEY],
                "metadata": patch["metadata"],
                "all_recipients_completed": patch["all_recipients_completed"],
            },
            actor=actor,
            source=source,
        )
        if patch["changed"]:
            self._transition(
                document_id,
                patch["state"],
                f"Reported by {vocab.WEBHOOK_EVENT}.",
                source=source,
                room_id=room_id,
                actor=actor,
            )
        self._writeback(document_id, source=source, room_id=room_id, actor=actor)
        self.log(
            document_id,
            vocab.ACTIVITY_WEBHOOK_APPLIED,
            f"{vocab.vendor_state(patch['state'])}",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return {
            "outcome": vocab.WEBHOOK_APPLIED,
            "event_id": event_id,
            "applied": True,
            "patch": patch,
            "document": self.document_view(document_id),
        }

    def _writeback(
        self, document_id: str, *, source: str, room_id: str | None, actor: str
    ) -> dict[str, Any]:
        """Write the document's state back to the deal, and record what was written.

        This is the one half of the workflow that happens inside this product, so
        unlike the document it is a real write. The stage, the note and the
        ``linked_objects`` entry come from :func:`~dsr.proposal_documents.rules.sync_from_document`.
        """
        document = self.require_document(document_id)
        sync = rules.sync_from_document(document)
        deal_id = str(sync.get("deal_id") or "")

        row = _payload(
            self.store.create(
                CRM_SYNC,
                {"document_id": document_id, "deal_id": deal_id, **sync},
                room_id=room_id,
                actor=actor,
                source=source,
            )
        )
        if deal_id and self.find_deal(deal_id) is not None:
            patch: dict[str, Any] = {"stage": sync["stage"], "proposal_note": sync["note"]}
            if sync.get("close_date"):
                patch["close_date"] = self._now()
            self.store.update(deal_id, patch, actor=actor, source=source)
        self.log(
            document_id,
            vocab.ACTIVITY_CRM_SYNCED,
            f"stage={sync['stage']}",
            source=source,
            room_id=room_id,
            actor=actor,
        )
        return row

    # ----------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------- #

    def list_documents(
        self, *, state: str = "", room_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = _payloads(self.store.list(DOCUMENTS, room_id=room_id, limit=limit))
        wanted = vocab.normalise_state(state)
        if wanted:
            rows = [row for row in rows if vocab.normalise_state(row.get("state")) == wanted]
        return rows

    def document_view(self, document_id: str) -> dict[str, Any]:
        """One document with its history, approvals, pricing table and activity.

        A composite read, so a page never has to make five calls to render one row.
        """
        document = self.require_document(document_id)
        table_rows = self.store.find(PRICING_TABLES, {"document_id": document_id})
        state = vocab.normalise_state(document.get("state"))
        return {
            **document,
            "state": state,
            "vendor_state": vocab.vendor_state(state),
            "sendable": rules.evaluate_send(
                state,
                gate=str(document.get("gate") or vocab.GATE_OPEN),
                has_approval_workflow=bool(document.get("has_approval_workflow", True)),
            ),
            "pricing_table": _payload(table_rows[0]) if table_rows else None,
            "history": _payloads(
                sorted(
                    self.store.find(STATE_CHANGES, {"document_id": document_id}),
                    key=lambda one: str(one["data"].get("at") or ""),
                )
            ),
            "approvals": self.approvals_for(document_id),
            "activity": self.activity_for(document_id),
            "links": [
                rules.shared_link_for(document, recipient)
                for recipient in (document.get("recipients") or [])
            ],
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The board: where every document stands, and what the room derived.

        The derived and recorded counters are kept apart on purpose. Nothing was sent
        to a vendor, so a single "sent" number would invite a reader to believe a
        document reached a buyer.
        """
        rows = self.list_documents(room_id=room_id, limit=1000)
        by_state: dict[str, int] = {}
        for row in rows:
            name = vocab.normalise_state(row.get("state")) or "unknown"
            by_state[name] = by_state.get(name, 0) + 1
        return {
            "room_id": room_id,
            "count": len(rows),
            "by_state": by_state,
            "awaiting_approval": by_state.get(vocab.STATE_WAITING_APPROVAL, 0),
            "delivered": sum(by_state.get(name, 0) for name in vocab.DELIVERED_STATES),
            "derived_documents": sum(1 for row in rows if row.get(vocab.DOCUMENT_URL_IS_DERIVED)),
            "delivered_to_vendor": 0,
            "delivery_marker": vocab.DELIVERY_SIMULATED,
            "note": vocab.DOCUMENT_DERIVED_NOTE,
        }
