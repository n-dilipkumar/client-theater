"""The writes. An order form is rendered in a room, redlined, signed and filed.

Every method that writes takes a **required keyword-only** ``source``, and the
feature module builds each one from ``router.prefix``. A domain function that
hardcoded a path would leave the audit log naming a route the app might have
stopped serving, and that class of bug has shipped here before. Required rather
than defaulted, so a caller that forgets gets a type error instead of a wrong
audit row.

What this engine writes, and what it only reads
-----------------------------------------------

It **reads** WF-086's ``wf086_quote`` and its ``wf086_line_item`` rows, and the
deal in ``crm_deal``. It **writes** the deal's amount and close date when filing,
which is the one write the research's user flow asks for ("the executed order
form is filed back on the deal"). Everything else it writes is its own record:
the order form, its lines, its redlines, its signature, its audit certificate
and its signed artefact.

The clone rule
--------------

The order form's lines get their own record ids. The quote line's id is kept as
``source_quote_line_item_id``, which is provenance and never an identity. The
alternative -- carrying the quote line's id across -- would make the clone
invisible in the audit log and would let a redline on the document rewrite the
quote it was rendered from.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from dsr.inroom_ordering import rules
from dsr.inroom_ordering.errors import (
    OrderFormConflict,
    OrderFormError,
    OrderFormNotFound,
    refuse,
)
from dsr.inroom_ordering.rules import as_text
from dsr.inroom_ordering.vocabulary import (
    ARTEFACT_COLLECTION,
    CERTIFICATE_COLLECTION,
    DEAL_COLLECTION,
    EDITABLE_STATUSES,
    ORDER_FORM_COLLECTION,
    ORDER_FORM_LINE_COLLECTION,
    PROPOSERS,
    QUOTE_COLLECTION,
    QUOTE_LINE_ITEM_COLLECTION,
    RENDER_FORMAT_JSON,
    RENDER_FORMATS_UNAVAILABLE,
    REVISION_ACCEPTED,
    REVISION_COLLECTION,
    REVISION_PROPOSED,
    REVISION_REJECTED,
    SIGNATURE_COLLECTION,
    SIGNATURE_MODES,
    STATUS_ACCEPTED,
    STATUS_DRAFT,
    STATUS_FILED,
    STATUS_SIGNED,
    vocabulary,
)

#: Collections a deal may live in. WF-086 reads the same set through its alias
#: list; this workflow must read whichever one is present rather than fail.
DEAL_COLLECTION_ALIASES: tuple[str, ...] = (
    DEAL_COLLECTION,
    "crm_opportunity",
    "deal",
    "opportunity",
)


class OrderFormEngine:
    """Order forms, their lines, redlines, signatures and filing."""

    def __init__(
        self,
        store: Any,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- the clock ---------------------------------------------------------- #

    def now(self) -> datetime:
        return self._now()

    def today(self) -> str:
        return self.now().date().isoformat()

    def _stamp(self) -> str:
        return rules.stamp(self.now())

    # -- vocabulary and decisions ------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocabulary()

    @staticmethod
    def decisions() -> list[dict[str, Any]]:
        """Every judgement call the research left open, with what it rejected.

        Served by ``GET /decisions`` so a reviewer reads the derivation rather
        than reconstructing it from the code.
        """
        return [
            {
                "question": "Which amount does filing write back onto the deal?",
                "options": [
                    "The signed order form's own total",
                    "The quote's total contract value",
                    "The deal's existing amount",
                ],
                "chosen": "The signed order form's own total",
                "rejected": "The quote's total contract value",
                "reason": (
                    "The buyer may redline a line before signing, so the executed "
                    "document is the figure both parties agreed to. The research names "
                    "'auto-update CRM amount' and does not choose, so this workflow "
                    "chooses the document the buyer signed over the quote it was "
                    "rendered from."
                ),
            },
            {
                "question": "What selects a signature capture mode?",
                "options": [
                    "The buyer's input, validated against the mode's artefact",
                    "A provider capability list read from the room's settings",
                    "The first mode in the researched list",
                ],
                "chosen": "The buyer's input, validated against the mode's artefact",
                "rejected": "The first mode in the researched list",
                "reason": (
                    "The research names 'typed/drawn/typed-photo' and no selector. A "
                    "mode whose artefact is absent is refused rather than downgraded, "
                    "so a typed-only submission can never be recorded as drawn."
                ),
            },
            {
                "question": "What does the audit certificate contain?",
                "options": [
                    "Document hash, signer, mode, instant, room, and a no-provider statement",
                    "Only the record ids of the order form and the signature",
                    "Every audit row the database holds for the order form",
                ],
                "chosen": (
                    "Document hash, signer, mode, instant, room, and a no-provider statement"
                ),
                "rejected": "Only the record ids of the order form and the signature",
                "reason": (
                    "The research names 'signed artefact + audit certificate' and fixes "
                    "no field. A certificate that named only ids would not prove what "
                    "was signed, and one that dumped every audit row would leak an "
                    "internal trail into a document a buyer reads."
                ),
            },
            {
                "question": "How does a redline on a line conflict with another?",
                "options": [
                    "At most one open proposal per line; the second is refused",
                    "The last proposal wins",
                    "Both proposals are recorded and applied in creation order",
                ],
                "chosen": "At most one open proposal per line; the second is refused",
                "rejected": "The last proposal wins",
                "reason": (
                    "Two open proposals on one line have no defined order of "
                    "application, so 'mutual revisions' would be a race. Refusing the "
                    "second until the first resolves makes it a sequence of accepted "
                    "changes, and each accepted change bumps the document version."
                ),
            },
            {
                "question": "How does the close date advance when an order form is filed?",
                "options": [
                    "To the signing date when the deal has no date or a later one",
                    "Always to the signing date",
                    "Never; the forecast is left alone",
                ],
                "chosen": "To the signing date when the deal has no date or a later one",
                "rejected": "Always to the signing date",
                "reason": (
                    "A signature closes a deal sooner than forecast, so the date moves "
                    "forward. A deal already forecasted to close before the signature "
                    "keeps its earlier date, because filing must not move a forecast "
                    "backwards."
                ),
            },
            {
                "question": "Is payment taken in this flow?",
                "options": [
                    "Out of scope",
                    "A Stripe payment link on the signed order form",
                ],
                "chosen": "Out of scope",
                "rejected": "A Stripe payment link on the signed order form",
                "reason": (
                    "The research hedges with 'Stripe for payment if in-flow' and this "
                    "build decides it is not in-flow: taking money is a separate flow "
                    "with its own rules and its own provider."
                ),
            },
            {
                "question": "Does the order form share WF-101's sales order?",
                "options": [
                    "No; it is its own record under wf129_order_form",
                    "Yes; it writes WF-101's wf101_sales_order",
                ],
                "chosen": "No; it is its own record under wf129_order_form",
                "rejected": "Yes; it writes WF-101's wf101_sales_order",
                "reason": (
                    "WF-101 freezes a price onto a sales order. This workflow renders a "
                    "buyer-editable, signable document inside the room and captures the "
                    "signature there. Sharing the collection would let a redline rewrite "
                    "a frozen price, which is the failure WF-101 exists to prevent."
                ),
            },
        ]

    # -- reads -------------------------------------------------------------- #

    def _require_form(self, form_id: str) -> dict[str, Any]:
        record = self.store.get(form_id) if form_id else None
        if record is None or record.get("collection") != ORDER_FORM_COLLECTION:
            raise OrderFormNotFound("No such order form.")
        return record

    def lines_of(self, form_id: str) -> list[dict[str, Any]]:
        """A form's lines, in the order the document puts them.

        Sorted by stored ``position``. ``find`` returns newest first, so without
        this the last line cloned would appear at the top and the totals would
        still add up while the document read backwards.
        """
        rows = self.store.find(ORDER_FORM_LINE_COLLECTION, {"order_form_id": form_id}, limit=1000)
        shaped = []
        for row in rows:
            data = dict(row["data"] or {})
            data["id"] = row["id"]
            data["room_id"] = row.get("room_id")
            shaped.append(data)
        return sorted(shaped, key=lambda line: rules.as_number(line.get("position"), 0.0))

    def revisions_of(self, form_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(REVISION_COLLECTION, {"order_form_id": form_id}, limit=1000)
        shaped = []
        for row in rows:
            data = dict(row["data"] or {})
            data["id"] = row["id"]
            shaped.append(data)
        return sorted(shaped, key=lambda r: rules.as_text(r.get("proposed_at")))

    def _shape(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        lines = self.lines_of(str(record["id"]))
        totals = rules.totals_for(lines)
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "lines": lines,
            "totals": totals,
            "line_count": len(lines),
            "buyer_editable": as_text(data.get("status")) in EDITABLE_STATUSES,
        }

    def read_order_form(self, form_id: str) -> dict[str, Any]:
        """One order form, with its lines, its redlines and its certificate."""
        shaped = self._shape(self._require_form(form_id))
        shaped["revisions"] = self.revisions_of(form_id)
        certificate = self.certificate_for(form_id)
        if certificate is not None:
            shaped["certificate"] = certificate
        return shaped

    def list_order_forms(
        self, room_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.list(
            ORDER_FORM_COLLECTION,
            room_id=room_id,
            limit=500,
            order_by="created_at",
            descending=False,
        )
        shaped = [self._shape(row) for row in rows]
        if status:
            shaped = [form for form in shaped if form.get("status") == status]
        return shaped

    def certificate_for(self, form_id: str) -> dict[str, Any] | None:
        rows = self.store.find(CERTIFICATE_COLLECTION, {"order_form_id": form_id}, limit=1)
        if not rows:
            return None
        return dict(rows[0]["data"] or {}) | {"id": rows[0]["id"]}

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        forms = self.list_order_forms(room_id)
        by_status = dict.fromkeys((STATUS_DRAFT, STATUS_ACCEPTED, STATUS_SIGNED, STATUS_FILED), 0)
        for form in forms:
            key = as_text(form.get("status"))
            if key in by_status:
                by_status[key] += 1
        revisions = [row for form in forms for row in self.revisions_of(str(form["id"]))]
        return {
            "order_forms": len(forms),
            "by_status": by_status,
            "editable": by_status[STATUS_DRAFT],
            "signed": by_status[STATUS_SIGNED] + by_status[STATUS_FILED],
            "filed": by_status[STATUS_FILED],
            "lines": sum(form["line_count"] for form in forms),
            "revisions": len(revisions),
            "revisions_accepted": sum(
                1 for row in revisions if row.get("state") == REVISION_ACCEPTED
            ),
            "revisions_open": sum(1 for row in revisions if row.get("state") == REVISION_PROPOSED),
            "signed_value": rules.money(
                sum(
                    rules.as_number(form.get("totals", {}).get("total"))
                    for form in forms
                    if form.get("status") in (STATUS_SIGNED, STATUS_FILED)
                )
            ),
            "map_advanced": False,
            "payment_in_scope": False,
            "render_formats_unavailable": list(RENDER_FORMATS_UNAVAILABLE),
        }

    # -- finding the source records ----------------------------------------- #

    def _require_quote(self, reference: str) -> dict[str, Any]:
        record = self.store.get(reference) if reference else None
        if record is None or record.get("collection") != QUOTE_COLLECTION:
            raise OrderFormNotFound("No quote matches that reference.")
        return record

    def _find_deal(self, reference: str) -> tuple[dict[str, Any], str] | None:
        if reference:
            row = self.store.get(reference)
            if row is not None and row.get("collection") in DEAL_COLLECTION_ALIASES:
                return row, str(row.get("collection"))
        for collection in DEAL_COLLECTION_ALIASES:
            rows = self.store.list(collection, limit=1)
            if rows:
                return rows[0], collection
        return None

    # -- creating ----------------------------------------------------------- #

    def create_order_form(
        self,
        room_id: str | None,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Render a quote as an itemised, buyer-editable document inside the room.

        The header is prefilled from the quote and every quote line becomes an
        order-form line with its own record id. The document is delivered inside
        the room, so a room is required: the research's flow is "in the room,
        without a redirect" and a form with no room has nowhere to be signed.
        """
        reference = as_text(payload.get("quote_id") or payload.get("quote"))
        if not reference:
            raise refuse("order_form_needs_a_quote", quote_id="Name the quote to render.")
        quote_record = self._require_quote(reference)
        quote = dict(quote_record.get("data") or {})

        resolved_room = (
            as_text(room_id)
            or as_text(payload.get("room_id"))
            or as_text(quote_record.get("room_id"))
        )
        if not resolved_room:
            raise refuse("order_form_needs_a_room", room_id="Name the room.")

        title = as_text(payload.get("title")) or as_text(quote.get("title")) or "Order form"
        currency = as_text(payload.get("currency")) or as_text(quote.get("currency"))
        buyer = payload.get("buyer") or quote.get("account") or quote.get("company_name")

        form = self.store.create(
            ORDER_FORM_COLLECTION,
            {
                "title": title,
                "quote_id": str(quote_record["id"]),
                "deal_id": as_text(payload.get("deal_id")) or as_text(quote.get("deal_id")) or None,
                "currency": currency,
                "buyer": buyer,
                "buyer_editable": True,
                "status": STATUS_DRAFT,
                "version": 1,
                "history": [],
                "signed_in_room": True,
                "render_format": RENDER_FORMAT_JSON,
                "render_formats_unavailable": list(RENDER_FORMATS_UNAVAILABLE),
                "created_at": self._stamp(),
            },
            room_id=resolved_room,
            actor=actor,
            source=source,
        )

        cloned = 0
        for position, row in enumerate(self._quote_lines(reference)):
            data = dict(row.get("data") or {})
            self._write_line(
                str(form["id"]),
                {
                    "name": as_text(data.get("name")),
                    "sku": as_text(data.get("sku")),
                    "description": as_text(data.get("description")),
                    "quantity": rules.as_number(data.get("quantity"), 1.0),
                    "unit_price": rules.as_number(data.get("unit_price"), 0.0),
                    "billing_frequency": data.get("billing_frequency"),
                    "discount_type": data.get("discount_type"),
                    "discount_value": data.get("discount_value"),
                    "tax_rate": data.get("tax_rate"),
                    "source_quote_line_item_id": str(row["id"]),
                    "position": position,
                },
                room_id=resolved_room,
                source=source,
                actor=actor,
            )
            cloned += 1

        return {"order_form": self._shape(form), "cloned_line_items": cloned}

    def _quote_lines(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(QUOTE_LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=1000)
        return sorted(
            rows, key=lambda row: rules.as_number((row.get("data") or {}).get("position"))
        )

    def _write_line(
        self,
        form_id: str,
        data: Mapping[str, Any],
        *,
        room_id: str | None,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        clean, errors = rules.validate_line(data)
        if errors:
            raise OrderFormError("That line is not valid.", errors)
        clean["order_form_id"] = form_id
        clean["position"] = rules.as_number(data.get("position"), 0.0)
        return self.store.create(
            ORDER_FORM_LINE_COLLECTION,
            clean,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    # -- editing a line ----------------------------------------------------- #

    def _require_line(self, form_id: str, line_id: str) -> dict[str, Any]:
        record = self.store.get(line_id) if line_id else None
        if (
            record is None
            or record.get("collection") != ORDER_FORM_LINE_COLLECTION
            or as_text((record.get("data") or {}).get("order_form_id")) != form_id
        ):
            raise OrderFormNotFound("No such order form line.")
        return record

    def _require_editable(self, form: Mapping[str, Any]) -> None:
        if as_text(form.get("status")) not in EDITABLE_STATUSES:
            raise OrderFormConflict("form_not_editable")

    def update_line(
        self,
        form_id: str,
        line_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Change one line of the document, recomputing its figures.

        Allowed only while the form is a draft. Once the buyer accepts it, the
        document is frozen: it is what the signature binds to.
        """
        form = self._shape(self._require_form(form_id))
        self._require_editable(form)
        record = self._require_line(form_id, line_id)
        existing = dict(record["data"] or {})
        merged = {**existing, **dict(payload)}
        merged["position"] = existing.get("position")
        clean, errors = rules.validate_line(merged)
        if errors:
            raise OrderFormError("That line is not valid.", errors)
        clean["order_form_id"] = form_id
        clean["position"] = existing.get("position")
        self.store.update(line_id, clean, actor=actor, source=source)
        return {"order_form": self._shape(self._require_form(form_id))}

    # -- redlining ---------------------------------------------------------- #

    def propose_revision(
        self,
        form_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Propose a change to one line, without applying it.

        A proposal is not a change. It is recorded as ``proposed`` and applies
        only when it is accepted, which is what makes a redline a negotiation
        the other side can reject rather than a silent edit.
        """
        form = self._shape(self._require_form(form_id))
        self._require_editable(form)
        line_id = as_text(payload.get("line_id"))
        if not line_id:
            raise refuse("revision_needs_a_line", line_id="Name the line.")
        line = self._require_line(form_id, line_id)

        field = as_text(payload.get("field"))
        if not rules.revision_is_known_field(field):
            raise refuse("revision_field_unknown", field="That field cannot be redlined.")
        if "proposed_value" not in payload:
            raise refuse("revision_needs_a_value", proposed_value="Give the proposed value.")

        proposed_by = as_text(payload.get("proposed_by"), "buyer").lower()
        if proposed_by not in PROPOSERS:
            raise refuse("unknown_proposer", proposed_by="The buyer or the seller.")

        if rules.revision_conflict(self.revisions_of(form_id), line_id):
            raise OrderFormConflict("revision_conflict")

        row = self.store.create(
            REVISION_COLLECTION,
            {
                "order_form_id": form_id,
                "line_id": line_id,
                "field": field,
                "from_value": (line.get("data") or {}).get(field),
                "proposed_value": payload.get("proposed_value"),
                "proposed_by": proposed_by,
                "state": REVISION_PROPOSED,
                "proposed_at": self._stamp(),
            },
            room_id=form.get("room_id"),
            actor=actor,
            source=source,
        )
        return {
            "revision": dict(row["data"] or {}) | {"id": row["id"]},
            "order_form": self._shape(self._require_form(form_id)),
        }

    def resolve_revision(
        self,
        revision_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Accept or reject a redline.

        An accepted proposal is applied to its line and bumps the order form's
        version, which is the document's version history: one accepted change is
        one version, with the field, the old value and the new one recorded.
        """
        record = self.store.get(revision_id) if revision_id else None
        if record is None or record.get("collection") != REVISION_COLLECTION:
            raise OrderFormNotFound("No such redline proposal.")
        data = dict(record["data"] or {})
        if as_text(data.get("state")) != REVISION_PROPOSED:
            raise OrderFormConflict("revision_not_open")

        form_id = as_text(data.get("order_form_id"))
        form = self._shape(self._require_form(form_id))
        self._require_editable(form)

        decision = as_text(payload.get("decision"), "accept").lower()
        if decision in ("reject", "rejected", "decline"):
            updated = self.store.update(
                revision_id,
                {"state": REVISION_REJECTED, "resolved_at": self._stamp(), "resolved_by": actor},
                actor=actor,
                source=source,
            )
            return {
                "revision": dict(updated["data"] or {}) | {"id": updated["id"]},
                "order_form": self._shape(self._require_form(form_id)),
            }

        line = self._require_line(form_id, as_text(data.get("line_id")))
        field = as_text(data.get("field"))
        clean, errors = rules.apply_revision(
            dict(line["data"] or {}), field, data.get("proposed_value")
        )
        if errors:
            raise OrderFormError("That redline would make the line invalid.", errors)
        clean["order_form_id"] = form_id
        clean["position"] = (line.get("data") or {}).get("position")

        version = int(rules.as_number(form.get("version"), 1.0)) + 1
        self.store.update(line["id"], clean, actor=actor, source=source)
        updated = self.store.update(
            revision_id,
            {
                "state": REVISION_ACCEPTED,
                "version": version,
                "resolved_at": self._stamp(),
                "resolved_by": actor,
            },
            actor=actor,
            source=source,
        )
        history = list(form.get("history") or [])
        history.append(
            {
                "version": version,
                "line_id": as_text(data.get("line_id")),
                "field": field,
                "from": data.get("from_value"),
                "to": data.get("proposed_value"),
                "by": as_text(data.get("proposed_by")),
                "at": self._stamp(),
            }
        )
        self.store.update(
            form_id, {"version": version, "history": history}, actor=actor, source=source
        )
        return {
            "revision": dict(updated["data"] or {}) | {"id": updated["id"]},
            "order_form": self._shape(self._require_form(form_id)),
        }

    # -- the buyer's steps in the room -------------------------------------- #

    def accept(
        self,
        form_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """The buyer accepts the document, in the room. Freezes it for signing."""
        form = self._require_form(form_id)
        if as_text((form.get("data") or {}).get("status")) != STATUS_DRAFT:
            raise OrderFormConflict("form_not_editable")
        accepted_by = as_text((payload or {}).get("accepted_by")) or actor
        updated = self.store.update(
            form_id,
            {
                "status": STATUS_ACCEPTED,
                "accepted_at": self._stamp(),
                "accepted_by": accepted_by,
            },
            actor=actor,
            source=source,
        )
        return {"order_form": self._shape(updated)}

    def sign(
        self,
        form_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture the buyer's signature in the room, with no redirect.

        Three records are written beside the signature: the signed artefact (the
        frozen document model and its hash), the audit certificate, and the
        signature event itself. No e-sign provider is reached; the certificate
        says so plainly.
        """
        body = dict(payload or {})
        form_record = self._require_form(form_id)
        status = as_text((form_record.get("data") or {}).get("status"))
        if status in (STATUS_SIGNED, STATUS_FILED):
            raise OrderFormConflict("form_already_signed")
        if status != STATUS_ACCEPTED:
            raise OrderFormConflict("form_not_awaiting_signature")

        signer_raw = body.get("signer") if isinstance(body.get("signer"), Mapping) else {}
        signer_name = as_text(signer_raw.get("name")) or as_text(body.get("signer_name"))
        signer_email = as_text(signer_raw.get("email")) or as_text(body.get("signer_email"))
        if not signer_name or not signer_email:
            raise refuse(
                "signature_needs_a_signer",
                signer="Record the signer's name and email.",
            )

        mode = body.get("mode") or body.get("signature_mode")
        normalised, errors = rules.validate_signature(mode, body)
        if normalised not in SIGNATURE_MODES:
            raise refuse(
                "unknown_signature_mode", mode="Sign by typing, drawing or uploading a photo."
            )
        if errors:
            raise OrderFormError("The signature is missing the artefact its mode needs.", errors)

        resolved_room = as_text(room_id) or as_text(form_record.get("room_id"))
        signed_at = self._stamp()
        lines = self.lines_of(form_id)
        totals = rules.totals_for(lines)
        model = rules.document_model(
            dict(form_record.get("data") or {}) | {"id": form_id, "room_id": resolved_room},
            lines,
            totals,
        )
        digest = rules.document_hash(model)

        signature_data = {
            "order_form_id": form_id,
            "signer": {"name": signer_name, "email": signer_email},
            "mode": normalised,
            "signature": self._signature_artefact(normalised, body),
            "signed_at": signed_at,
            "document_sha256": digest,
        }
        signature = self.store.create(
            SIGNATURE_COLLECTION,
            signature_data,
            room_id=resolved_room,
            actor=actor,
            source=source,
        )
        artefact = self.store.create(
            ARTEFACT_COLLECTION,
            {
                "order_form_id": form_id,
                "format": RENDER_FORMAT_JSON,
                "document": model,
                "document_sha256": digest,
                "render_formats_unavailable": list(RENDER_FORMATS_UNAVAILABLE),
                "signed_at": signed_at,
            },
            room_id=resolved_room,
            actor=actor,
            source=source,
        )
        certificate = self.store.create(
            CERTIFICATE_COLLECTION,
            rules.certificate_contents(
                dict(form_record.get("data") or {}) | {"id": form_id},
                signature_data,
                digest,
            )
            | {"issued_at": signed_at},
            room_id=resolved_room,
            actor=actor,
            source=source,
        )
        updated = self.store.update(
            form_id,
            {
                "status": STATUS_SIGNED,
                "signed_at": signed_at,
                "signature_id": signature["id"],
                "artefact_id": artefact["id"],
                "certificate_id": certificate["id"],
                "document_sha256": digest,
            },
            actor=actor,
            source=source,
        )
        return {
            "order_form": self._shape(updated),
            "signature": dict(signature["data"] or {}) | {"id": signature["id"]},
            "artefact": dict(artefact["data"] or {}) | {"id": artefact["id"]},
            "certificate": dict(certificate["data"] or {}) | {"id": certificate["id"]},
        }

    @staticmethod
    def _signature_artefact(mode: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """The capture, stored in the shape its mode produced.

        Strokes and image data are stored as given, because the artefact is what
        the certificate hashes and a summary would not be the thing signed.
        """
        if mode == "typed":
            return {"text": as_text(body.get("text"))}
        if mode == "drawn":
            return {"strokes": body.get("strokes")}
        return {
            "text": as_text(body.get("text")),
            "image": body.get("image") or body.get("photo"),
        }

    # -- filing the executed order form ------------------------------------- #

    def file_back(
        self,
        form_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """File the signed order form on the deal and advance its close date.

        The write-back is narrow: the deal's amount becomes the signed
        document's total, and its close date advances to the signing date. The
        MAP step is reported as not advanced, because this repository owns no
        MAP surface to advance.
        """
        form_record = self._require_form(form_id)
        data = dict(form_record.get("data") or {})
        status = as_text(data.get("status"))
        if status == STATUS_FILED:
            raise OrderFormConflict("form_already_filed")
        if status != STATUS_SIGNED:
            raise OrderFormConflict("form_not_signed")

        lines = self.lines_of(form_id)
        totals = rules.totals_for(lines)
        signed_on = as_text(data.get("signed_at"))[:10] or self.today()

        found = self._find_deal(as_text(data.get("deal_id")))
        writeback = rules.deal_writeback(
            data | {"id": form_id},
            totals,
            signed_on,
            (found[0].get("data") if found else None),
        )
        deal_written = False
        if found is not None:
            self.store.update(str(found[0]["id"]), writeback, actor=actor, source=source)
            deal_written = True

        updated = self.store.update(
            form_id,
            {
                "status": STATUS_FILED,
                "filed_at": self._stamp(),
                "filed_by": actor,
                "filed_to_deal": deal_written,
                "amount_filed": writeback["amount"],
                "close_date": writeback["close_date"],
            },
            actor=actor,
            source=source,
        )
        return {
            "order_form": self._shape(updated),
            "deal": writeback if deal_written else None,
            "deal_filed": deal_written,
            "map_advanced": False,
            "map_step": (
                "MAP tasks are not advanced: this repository owns no MAP surface. "
                "The close date was written onto the deal."
            ),
            "payment_in_scope": False,
        }
