"""The reads and the writes: envelopes, signers, signature events and quota.

Everything the rules decide, this module carries out against the store, and nothing in it
imports HTTP. The rules in :mod:`dsr.quote_acceptance.rules` are pure, so each rule below is
one call into them and one store write, and a test can assert the rule without a server.

Every write here goes through :class:`~dsr.store.RecordStore`, which is the audited wrapper
the HTTP layer hands in, so the audit row is written in the same transaction as the change.
The engine never opens SQLite and never imports the application module.

The clock is a seam, not a global
---------------------------------

:meth:`AcceptanceEngine.now` reads an injected clock, defaulting to UTC now. Every rule that
needs "what time is it" — the one-hour verification window, the quota month — takes that
instant as an argument rather than calling :func:`datetime.now` itself. A test therefore pins
a moment and the one-hour window is testable without sleeping, and a caller can supply a
clock to read the board "as of" a past instant.

The three collections this engine writes
----------------------------------------

* ``wf095_signing_envelope`` — one row per quote's e-signature envelope. It carries the
  acceptance configuration, the document it binds to, the signing status, the verification
  request and token, and the quota month the usage landed in.
* ``wf095_signer`` — one row per party on the envelope, in signing order, carrying whether it
  has signed and when.
* ``wf095_signature_event`` — one row per signature attempt, successful or failed. A failed
  attempt is a row, not an exception, because the research logs
  ``Signing attempt failed`` automatically.
* ``wf095_esign_quota`` — one row per envelope that consumed usage, with the month and the
  cost, so a month's usage can be summed without replaying envelopes.

What this engine reads and does not own
----------------------------------------

**The quote and the document.** WF-086 provisions the quote and WF-093 provisions the
rendered document. This engine reads ``wf086_quote`` and ``wf093_document`` as data and never
writes either, because a signature must not be able to rewrite the document it is bound to.
It exposes a route to create a demo quote and document so the flow is demonstrable before
those land, and when they land they write into the same two collections and this workflow
signs what it finds.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.quote_acceptance import inferences, rules, vocabulary as vocab
from dsr.quote_acceptance.errors import (
    AcceptanceRefused,
    EnvelopeNotFound,
    QuotaRefused,
    SignerNotFound,
)
from dsr.store import RecordStore


class AcceptanceEngine:
    """The reads and the writes for collecting a quote's acceptance by e-signature."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._token = token_factory or (lambda: secrets.token_urlsafe(16))

    # -- clock and tokens ----------------------------------------------------- #

    def now(self) -> datetime:
        """The current instant, through the injected clock."""

        return self._now()

    # -- envelopes ------------------------------------------------------------ #

    def open_envelope(
        self,
        payload: Mapping[str, Any],
        *,
        quote_id: str | None = None,
        room_id: str | None = None,
        is_published: bool = False,
        document_id: str | None = None,
        document_size_bytes: Any = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Validate the acceptance configuration, check the document cap and the quota, and
        write the envelope with its signers.

        The order matters and is the rule: the acceptance configuration is validated first,
        then the document is measured against the 40 MB cap, then the quota is checked, and
        only then is anything written. A caller therefore never has an envelope opened for a
        quote whose configuration was refused, and never consumes quota for one that failed.

        The quota is consumed here, when the envelope is opened for a published quote, because
        the evidence is explicit that usage "will count toward the limit as soon as the
        e-signature option is turned on for a published quote" whether or not it is ever
        signed.
        """

        config = rules.validate_acceptance(
            {**payload, vocab.ROOM_REF: payload.get(vocab.ROOM_REF) or room_id}
        )
        size = rules.check_document_size(document_size_bytes)

        quote_ref = quote_id or payload.get("quote_id")
        moment = self.now()
        month = rules.quota_month(moment)

        method = config["method"]
        consumed = rules.enables_quota(is_published, method)
        if consumed:
            usage = self.quota_usage(month)
            limit = self._stated_limit()
            try:
                rules.assert_quota_not_exceeded(usage["used"], limit, month=month)
            except QuotaRefused:
                raise
            cost = rules.quota_cost({"signers": config["signers"]})
            usage["used"] += cost
        else:
            cost = 0

        ordered = rules.signing_order_signers(config["signers"], config["countersigners"])

        envelope = self.store.create(
            vocab.ENVELOPE_COLLECTION,
            {
                vocab.ROOM_REF: room_id or payload.get(vocab.ROOM_REF),
                "quote_id": quote_ref,
                "document_id": document_id or payload.get("document_id"),
                "method": method,
                vocab.SIGNING_STATUS_FIELD: vocab.STATUS_PENDING_SIGNATURE,
                vocab.SIGNERS_REQUIRED_FIELD: config["signers_required"],
                vocab.REASSIGN_ALLOWED_FIELD: config["reassign_allowed"],
                vocab.VERIFICATION_REQUIRED: config["identity_verification_required"],
                "in_signing_attachments": config["in_signing_attachments"],
                "document_size_bytes": size.get("size_bytes"),
                "signing_provider": vocab.SIGNING_PROVIDER,
                "verification_token": None,
                vocab.VERIFICATION_REQUEST_FIELD: None,
                "is_published": bool(is_published),
                "quota_month": month,
                "quota_cost": cost,
                "opened_at": moment.isoformat(),
                "countersigners_notified": False,
                "sealed": False,
                "accepted_by": None,
                vocab.PAYMENT_STATUS_FIELD: None,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

        signer_rows: list[dict[str, Any]] = []
        for index, signer in enumerate(ordered):
            row = self.store.create(
                vocab.SIGNER_COLLECTION,
                {
                    vocab.ROOM_REF: room_id or payload.get(vocab.ROOM_REF),
                    "envelope_id": envelope["id"],
                    "quote_id": quote_ref,
                    "role": signer["role"],
                    "name": signer.get("name") or signer["email"],
                    "email": signer["email"],
                    "contact_id": signer.get("contact_id"),
                    vocab.SIGNER_ASSOCIATION_TYPE: vocab.SIGNER_ASSOCIATION_TYPE,
                    "signing_order": signer["signing_order"],
                    "position": index + 1,
                    "signed": False,
                    "signed_at": None,
                    "signature_mode": None,
                    "signature_payload": None,
                    "verification_required": bool(
                        config["identity_verification_required"]
                        and signer["role"] == vocab.ROLE_BUYER
                    ),
                    "verified": False,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            signer_rows.append(self._signer_view(row))

        return self._envelope_view(envelope, signer_rows)

    def envelopes(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every signing envelope, each with its signers."""

        rows = self.store.list(vocab.ENVELOPE_COLLECTION, room_id=room_id, limit=200)
        return [self._envelope_view(row, self.signers(row["id"])) for row in rows]

    def envelope(self, envelope_id: str) -> dict[str, Any]:
        """One signing envelope with its signers, or :class:`EnvelopeNotFound`."""

        record = self.store.get(envelope_id)
        if record is None or record.get("collection") != vocab.ENVELOPE_COLLECTION:
            raise EnvelopeNotFound(f"No signing envelope {envelope_id!r}.")
        return self._envelope_view(record, self.signers(envelope_id))

    def _envelope_view(
        self, record: Mapping[str, Any], signers: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        data = record.get("data", {})
        status = data.get(vocab.SIGNING_STATUS_FIELD, vocab.STATUS_PENDING_SIGNATURE)
        document_size = data.get("document_size_bytes")
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "quote_id": data.get("quote_id"),
            "document_id": data.get("document_id"),
            "method": data.get("method"),
            vocab.SIGNING_STATUS_FIELD: status,
            "status_label": vocab.SIGNING_STATUS_LABELS.get(status, status),
            vocab.SIGNERS_REQUIRED_FIELD: data.get(vocab.SIGNERS_REQUIRED_FIELD),
            vocab.REASSIGN_ALLOWED_FIELD: data.get(vocab.REASSIGN_ALLOWED_FIELD, False),
            vocab.VERIFICATION_REQUIRED: data.get(vocab.VERIFICATION_REQUIRED, False),
            "in_signing_attachments": data.get("in_signing_attachments") or [],
            "document_size_bytes": document_size,
            "document_size_mb": (
                round(document_size / rules.BYTES_PER_MB, 3) if document_size is not None else None
            ),
            "pdf_size_cap_mb": vocab.PDF_SIZE_CAP_MB,
            "pdf_size_cap_quote": vocab.PDF_SIZE_CAP_QUOTE,
            "signers": [dict(signer) for signer in signers],
            "signer_count": len(signers),
            "signed_count": sum(1 for signer in signers if signer.get("signed")),
            "is_open": rules.is_open(status),
            "is_accepted": status == vocab.STATUS_ACCEPTED,
            "sealed": data.get("sealed", False),
            "accepted_by": data.get("accepted_by"),
            vocab.PAYMENT_STATUS_FIELD: data.get(vocab.PAYMENT_STATUS_FIELD),
            "countersigners_notified": data.get("countersigners_notified", False),
            "quota_month": data.get("quota_month"),
            "quota_cost": data.get("quota_cost", 0),
            "opened_at": data.get("opened_at"),
            "verification_requested_at": data.get(vocab.VERIFICATION_REQUEST_FIELD),
            "verification_window_minutes": vocab.VERIFICATION_WINDOW_MINUTES,
            "sealed_copy_expiry_quote": vocab.SEALED_COPY_EXPIRES_WITH_QUOTE,
            "pdf_export_is_lossy_quote": vocab.PDF_EXPORT_IS_LOSSY_QUOTE,
            "authentication_owner": vocab.AUTHENTICATION_OWNER,
            "contract_is_downstream": vocab.CONTRACT_IS_DOWNSTREAM,
        }

    # -- signers -------------------------------------------------------------- #

    def signers(self, envelope_id: str) -> list[dict[str, Any]]:
        """Every signer on the envelope, in signing order."""

        rows = self.store.find(
            vocab.SIGNER_COLLECTION, {"envelope_id": envelope_id}, limit=vocab.MAX_SIGNERS * 4
        )
        rows.sort(
            key=lambda row: (
                row.get("data", {}).get("signing_order", 0),
                row.get("created_at") or "",
            )
        )
        return [self._signer_view(row) for row in rows]

    def signer(self, signer_id: str) -> dict[str, Any]:
        """One signer, or :class:`SignerNotFound`."""

        record = self.store.get(signer_id)
        if record is None or record.get("collection") != vocab.SIGNER_COLLECTION:
            raise SignerNotFound(f"No signer {signer_id!r}.")
        return self._signer_view(record)

    def _signer_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data", {})
        role = data.get("role", vocab.ROLE_BUYER)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "envelope_id": data.get("envelope_id"),
            "role": role,
            "role_label": vocab.SIGNER_ROLE_LABELS.get(role, role),
            "name": data.get("name"),
            "email": data.get("email"),
            "contact_id": data.get("contact_id"),
            vocab.SIGNER_ASSOCIATION_TYPE: data.get(vocab.SIGNER_ASSOCIATION_TYPE),
            "signing_order": data.get("signing_order"),
            "signed": data.get("signed", False),
            "signed_at": data.get("signed_at"),
            "signature_mode": data.get("signature_mode"),
            "verification_required": data.get("verification_required", False),
            "verified": data.get("verified", False),
        }

    # -- verification --------------------------------------------------------- #

    def request_verification(
        self,
        envelope_id: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Mint the one-hour verification token, on the buyer's *Verify email* click.

        The window opens now, not at send, per the evidence. Returns the token in a
        ``verification_link`` field so a caller (a test, or the buyer's browser) can present
        it; a real deployment would email it rather than return it, and the research names
        the from-address as a super-admin setting this build does not provision.
        """

        # Read the envelope first so a caller cannot mint a token against a missing row.
        self._envelope_record(envelope_id)
        moment = self.now()
        token = self._token()
        self.store.update(
            envelope_id,
            {vocab.VERIFICATION_REQUEST_FIELD: moment.isoformat(), "verification_token": token},
            actor=actor,
            source=source,
        )
        window = rules.verification_window(moment, moment)
        self.record_event(
            envelope_id,
            vocab.EVENT_VERIFICATION_REQUESTED,
            actor=actor,
            source=source,
            advance=False,
        )
        return {
            "envelope_id": envelope_id,
            "verification_link": token,
            "window": window,
            "verification_window_quote": vocab.VERIFICATION_WINDOW_QUOTE,
        }

    def verify(
        self,
        envelope_id: str,
        token: Any,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Check a verification token and, on a pass, stamp the buyer as verified.

        The check re-reads the window every call, so a token presented after the hour fails
        rather than inheriting an earlier pass. A pass stamps the buyer signer's ``verified``
        flag, which is the session stamp the signature step reads; it grants no standing
        permission beyond that signer.
        """

        envelope = self._envelope_record(envelope_id).get("data", {})
        verdict = rules.verify_token(envelope, token, self.now())
        if verdict.get("verified") and verdict.get("required"):
            for signer in self.signers(envelope_id):
                if signer["role"] == vocab.ROLE_BUYER and not signer.get("verified"):
                    self.store.update(signer["id"], {"verified": True}, actor=actor, source=source)
        return verdict

    # -- viewing and signing -------------------------------------------------- #

    def mark_viewed(
        self,
        envelope_id: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """A buyer opened the quote, which advances pending -> viewed-pending.

        The research's status machine moves on this, and viewing writes no activity row
        because "viewed" is not among the four named activities.
        """

        envelope = self._envelope_record(envelope_id)
        status = envelope.get("data", {}).get(
            vocab.SIGNING_STATUS_FIELD, vocab.STATUS_PENDING_SIGNATURE
        )
        # The strict single step, not the permissive one: a late view on a quote that has
        # already moved on must not rewind it.
        moved = rules.step_status(status, "viewed")
        if moved != status:
            self.store.update(
                envelope_id, {vocab.SIGNING_STATUS_FIELD: moved}, actor=actor, source=source
            )
        self.record_event(envelope_id, "viewed", actor=actor, source=source, advance=False)
        return self.envelope(envelope_id)

    def sign(
        self,
        signer_id: str,
        *,
        signature_mode: str | None = None,
        signature_payload: Any = None,
        verification_token: Any = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Record one party's signature and advance the status.

        The buyer signs first and moves the status to ``pending_countersignature``, and the
        countersigner signs second and moves it to ``accepted``. The refusals here — an
        unverified buyer, an expired window, a countersignature before the buyer's, a second
        signature on a signed signer — are all :class:`AcceptanceRefused` or a returned failed
        attempt, and each either advances nothing or writes a failed-attempt activity.
        """

        signer_record = self._signer_record(signer_id)
        signer = self._signer_view(signer_record)
        envelope_id = signer["envelope_id"]
        envelope_record = self._envelope_record(envelope_id)
        envelope = self._envelope_view(envelope_record, self.signers(envelope_id))
        status = envelope[vocab.SIGNING_STATUS_FIELD]
        role = signer["role"]

        # A signer who has already signed cannot sign again.
        if signer["signed"]:
            return self._record_failure(
                envelope_id,
                signer_id,
                "already_signed",
                f"This signer already signed at {signer['signed_at']}.",
                actor=actor,
                source=source,
            )

        # Order: a countersignature cannot precede the buyer's.
        rules.assert_signature_order(status, role)

        # A buyer who must verify cannot sign until the window clears with the right token.
        if signer.get("verification_required") and not signer.get("verified"):
            verdict = rules.verify_token(
                envelope_record.get("data", {}), verification_token, self.now()
            )
            if not verdict.get("verified"):
                reason = verdict.get("reason", "not_verified")
                details = {
                    "verification_not_requested": (
                        "The buyer has not clicked Verify email on this quote yet."
                    ),
                    "verification_window_expired": (
                        "The one-hour verification window has closed. Ask the buyer to verify "
                        "again to reopen it."
                    ),
                    "verification_token_mismatch": (
                        "The verification link does not match the one sent for this quote."
                    ),
                }
                return self._record_failure(
                    envelope_id,
                    signer_id,
                    reason,
                    details.get(reason, "The buyer has not verified this envelope yet."),
                    actor=actor,
                    source=source,
                )
            self.store.update(signer_id, {"verified": True}, actor=actor, source=source)

        # The signature must be drawn, typed or uploaded.
        mode = str(signature_mode or "").strip().lower()
        if mode not in vocab.SIGNATURE_MODES:
            raise AcceptanceRefused(
                "A signature must be drawn, typed or uploaded.",
                {
                    "signature_mode": (
                        f"signature_mode must be one of {', '.join(vocab.SIGNATURE_MODES)}, not "
                        f"{signature_mode!r}."
                    )
                },
            )

        moment = self.now()
        self.store.update(
            signer_id,
            {
                "signed": True,
                "signed_at": moment.isoformat(),
                "signature_mode": mode,
                "signature_payload": signature_payload,
                "verified": True,
            },
            actor=actor,
            source=source,
        )

        event = "buyer_signed" if role == vocab.ROLE_BUYER else "countersigned"
        written = self.record_event(
            envelope_id,
            event,
            signer_id=signer_id,
            actor=actor,
            source=source,
            advance=True,
        )

        # One shape for both outcomes, so a caller reads a signed attempt and a failed
        # attempt the same way and never has to guess which keys are present.
        return {
            "outcome": "signed",
            "reason": None,
            "detail": None,
            "activity": written["activity"],
            "activity_label": written["activity_label"],
            "event_id": written["id"],
            "envelope": self.envelope(envelope_id),
        }

    def reassign(
        self,
        signer_id: str,
        new_signer: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Reassign one signer's name and email to a new party.

        Refused when the quote has reassign off, or the signer has already signed. On success
        it updates the signer row in place and appends a ``Quote reassigned`` activity. The
        activity log is append-only, so the reassignment is a new event row rather than an
        edit of the previous one.
        """

        signer_record = self._signer_record(signer_id)
        signer = self._signer_view(signer_record)
        envelope_id = signer["envelope_id"]
        envelope_record = self._envelope_record(envelope_id)
        rules.validate_reassignment(envelope_record.get("data", {}), signer_record.get("data", {}))

        name = str(new_signer.get("name") or "").strip()
        email = rules.validate_email(new_signer.get("email"), "email")
        contact_id = new_signer.get("contact_id") or new_signer.get("contact")

        self.store.update(
            signer_id,
            {
                "name": name or email,
                "email": email,
                "contact_id": contact_id,
                # A reassignment clears any verification the old party held: the new party
                # must verify in their own name if the envelope requires it.
                "verified": False,
                "signed": False,
            },
            actor=actor,
            source=source,
        )
        self.record_event(
            envelope_id,
            "reassigned",
            signer_id=signer_id,
            actor=actor,
            source=source,
            advance=False,
            detail={
                "from": {"name": signer["name"], "email": signer["email"]},
                "to": {"name": name or email, "email": email},
            },
        )
        return self.signer(signer_id)

    # -- events --------------------------------------------------------------- #

    def record_event(
        self,
        envelope_id: str,
        event: str,
        *,
        signer_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
        advance: bool = False,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Write one signature event row and, when asked, advance the signing status.

        The activity row this writes comes from :func:`rules.record_activity`, so only the
        four named activities get a label and ``viewed``/``verified`` write the event without
        one. Every event writes a row regardless, so the audit trail shows the attempt.
        """

        envelope_record = self._envelope_record(envelope_id)
        current = envelope_record.get("data", {}).get(
            vocab.SIGNING_STATUS_FIELD, vocab.STATUS_PENDING_SIGNATURE
        )
        moment = self.now()
        activity = rules.record_activity(event)
        # A signature advances permissively, because signing implies viewing. Every other event
        # advances one strict step, because none of them implies a state the quote is not in.
        next_status = (
            rules.advance_status(current, event) if advance else rules.step_status(current, event)
        )

        row = self.store.create(
            vocab.EVENT_COLLECTION,
            {
                vocab.ROOM_REF: envelope_record.get("room_id"),
                "envelope_id": envelope_id,
                "quote_id": envelope_record.get("data", {}).get("quote_id"),
                "signer_id": signer_id,
                "event": event,
                "activity": activity["activity"] or None,
                "status_before": current,
                "status_after": next_status,
                "detail": dict(detail or {}),
                "at": moment.isoformat(),
            },
            room_id=envelope_record.get("room_id"),
            actor=actor,
            source=source,
        )

        if next_status != current:
            patch: dict[str, Any] = {vocab.SIGNING_STATUS_FIELD: next_status}
            if next_status == vocab.STATUS_PENDING_COUNTERSIGNATURE:
                # Countersigners are emailed automatically when the buyer signs.
                patch["countersigners_notified"] = True
            if next_status == vocab.STATUS_ACCEPTED:
                signer = self.signer(signer_id) if signer_id else {}
                patch["sealed"] = True
                patch["accepted_by"] = signer.get("email")
                patch[vocab.PAYMENT_STATUS_FIELD] = "accepted"
                patch["sealed_at"] = moment.isoformat()
            self.store.update(envelope_id, patch, actor=actor, source=source)

        return {
            "id": row["id"],
            "envelope_id": envelope_id,
            "event": event,
            "activity": activity["activity"] or None,
            "activity_label": vocab.ACTIVITY_LABELS.get(activity["activity"] or "", None),
            "writes_activity": activity["writes_activity"],
            "status_before": current,
            "status_after": next_status,
            "at": moment.isoformat(),
        }

    def _record_failure(
        self,
        envelope_id: str,
        signer_id: str | None,
        reason: str,
        detail: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Record a signing attempt that failed, as an event and a returned outcome.

        The research says "Signing attempt failures are logged automatically", so a failed
        attempt writes an ``attempt_failed`` event with the activity
        ``Signing attempt failed`` and is returned to the caller as a normal result. It is
        not an exception, because the attempt happened and produced a definite answer.
        """

        event = self.record_event(
            envelope_id,
            "attempt_failed",
            signer_id=signer_id,
            detail={"reason": reason, "detail": detail},
            advance=False,
            actor=actor,
            source=source,
        )
        return {
            "outcome": "failed",
            "reason": reason,
            "detail": detail,
            "activity": event["activity"],
            "activity_label": event["activity_label"],
            "event_id": event["id"],
            "envelope": self.envelope(envelope_id),
        }

    def events(
        self, envelope_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every signature event, optionally for one envelope."""

        where: dict[str, Any] = {}
        if envelope_id:
            where["envelope_id"] = envelope_id
        rows = self.store.list(vocab.EVENT_COLLECTION, room_id=room_id, limit=500)
        rows = [
            row
            for row in rows
            if not where or row.get("data", {}).get("envelope_id") == envelope_id
        ]
        rows.sort(key=lambda row: row.get("created_at") or "")
        return [
            {
                "id": row.get("id"),
                "envelope_id": row.get("data", {}).get("envelope_id"),
                "quote_id": row.get("data", {}).get("quote_id"),
                "signer_id": row.get("data", {}).get("signer_id"),
                "event": row.get("data", {}).get("event"),
                "activity": row.get("data", {}).get("activity"),
                "activity_label": vocab.ACTIVITY_LABELS.get(
                    row.get("data", {}).get("activity") or "", None
                ),
                "status_before": row.get("data", {}).get("status_before"),
                "status_after": row.get("data", {}).get("status_after"),
                "detail": row.get("data", {}).get("detail") or {},
                "at": row.get("data", {}).get("at"),
            }
            for row in rows
        ]

    # -- quota ---------------------------------------------------------------- #

    def _stated_limit(self) -> int | None:
        """The room's stated e-signature ceiling, or None because the research states none."""

        return None

    def quota_usage(self, month: str | None = None) -> dict[str, Any]:
        """The usage for one month: how many envelopes consumed quota, and at what cost.

        Sums the ``quota_cost`` written on each envelope. The cost is always one per envelope
        that consumed usage, whatever its signer count, per the multi-signature sentence.
        """

        target = month or rules.quota_month(self.now())
        rows = self.store.list(vocab.ENVELOPE_COLLECTION, limit=500)
        used = sum(
            int(row.get("data", {}).get("quota_cost") or 0)
            for row in rows
            if row.get("data", {}).get("quota_month") == target
        )
        envelopes_consuming = sum(
            1
            for row in rows
            if row.get("data", {}).get("quota_month") == target
            and int(row.get("data", {}).get("quota_cost") or 0) > 0
        )
        return {
            "month": target,
            "used": used,
            "envelopes_charged": envelopes_consuming,
            "limit": self._stated_limit(),
            "reset_day": vocab.QUOTA_RESET_DAY,
            "counts_envelope_not_signer_quote": vocab.QUOTA_COUNTS_ENVELOPE_NOT_SIGNER,
            "consumed_on_enable_quote": vocab.QUOTA_CONSUMED_ON_ENABLE,
            "quota_unspecified_quote": vocab.QUOTA_UNSPECIFIED,
        }

    # -- records and views ---------------------------------------------------- #

    def _envelope_record(self, envelope_id: str) -> dict[str, Any]:
        record = self.store.get(envelope_id)
        if record is None or record.get("collection") != vocab.ENVELOPE_COLLECTION:
            raise EnvelopeNotFound(f"No signing envelope {envelope_id!r}.")
        return record

    def _signer_record(self, signer_id: str) -> dict[str, Any]:
        record = self.store.get(signer_id)
        if record is None or record.get("collection") != vocab.SIGNER_COLLECTION:
            raise SignerNotFound(f"No signer {signer_id!r}.")
        return record

    # -- summary and decisions ------------------------------------------------ #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The board's headline numbers, read back from the store.

        Counts envelopes by signing status, signers signed vs unsigned, and the month's
        usage. Reads only; no write, so it is safe to poll.
        """

        envelopes = self.envelopes(room_id)
        by_status = {status: 0 for status in vocab.SIGNING_STATUSES}
        signers_total = 0
        signers_signed = 0
        for envelope in envelopes:
            by_status[envelope[vocab.SIGNING_STATUS_FIELD]] = (
                by_status.get(envelope[vocab.SIGNING_STATUS_FIELD], 0) + 1
            )
            signers_total += envelope["signer_count"]
            signers_signed += envelope["signed_count"]

        month = rules.quota_month(self.now())
        return {
            "envelopes": len(envelopes),
            "by_status": by_status,
            "accepted": by_status.get(vocab.STATUS_ACCEPTED, 0),
            "signers_total": signers_total,
            "signers_signed": signers_signed,
            "signers_outstanding": signers_total - signers_signed,
            "verification_required_envelopes": sum(
                1 for envelope in envelopes if envelope.get(vocab.VERIFICATION_REQUIRED)
            ),
            "quota": self.quota_usage(month),
            "authentication_owner": vocab.AUTHENTICATION_OWNER,
            "contract_is_downstream": vocab.CONTRACT_IS_DOWNSTREAM,
            "countersigner_pool_quote": vocab.COUNTERSIGNER_POOL_IS_THIS_ROOM,
        }

    def vocabulary(self) -> dict[str, Any]:
        """The researched vocabulary this workflow enforces against, served so the page cannot drift."""

        return {
            "acceptance_methods": list(vocab.ACCEPTANCE_METHODS),
            "acceptance_method_labels": dict(vocab.ACCEPTANCE_METHOD_LABELS),
            "signing_statuses": list(vocab.SIGNING_STATUSES),
            "signing_status_labels": dict(vocab.SIGNING_STATUS_LABELS),
            "status_transitions": {
                status: {"next": row[0], "on": row[1]}
                for status, row in vocab.STATUS_TRANSITIONS.items()
            },
            "open_statuses": list(vocab.OPEN_STATUSES),
            "signer_roles": list(vocab.SIGNER_ROLES),
            "signer_role_labels": dict(vocab.SIGNER_ROLE_LABELS),
            "signature_modes": list(vocab.SIGNATURE_MODES),
            "signature_mode_labels": dict(vocab.SIGNATURE_MODE_LABELS),
            "activities": list(vocab.ACTIVITIES),
            "activity_labels": dict(vocab.ACTIVITY_LABELS),
            "verification_window_minutes": vocab.VERIFICATION_WINDOW_MINUTES,
            "verification_window_quote": vocab.VERIFICATION_WINDOW_QUOTE,
            "verification_binds_buyer_only_quote": vocab.VERIFICATION_BINDS_BUYER_ONLY,
            "pdf_size_cap_mb": vocab.PDF_SIZE_CAP_MB,
            "pdf_size_cap_quote": vocab.PDF_SIZE_CAP_QUOTE,
            "in_signing_forces_esignature_quote": vocab.IN_SIGNING_ATTACHMENT_FORCES_ESIGNATURE,
            "pdf_export_is_lossy_quote": vocab.PDF_EXPORT_IS_LOSSY_QUOTE,
            "sealed_copy_expiry_quote": vocab.SEALED_COPY_EXPIRES_WITH_QUOTE,
            "quota_reset_day": vocab.QUOTA_RESET_DAY,
            "quota_unspecified_quote": vocab.QUOTA_UNSPECIFIED,
            "quota_counts_envelope_not_signer_quote": vocab.QUOTA_COUNTS_ENVELOPE_NOT_SIGNER,
            "authentication_owner": vocab.AUTHENTICATION_OWNER,
            "contract_is_downstream": vocab.CONTRACT_IS_DOWNSTREAM,
            "countersigner_pool_quote": vocab.COUNTERSIGNER_POOL_IS_THIS_ROOM,
        }

    def decisions(self) -> list[dict[str, Any]]:
        """Every judgement call this workflow made, with the alternative it rejected."""

        return inferences.decision_list()
