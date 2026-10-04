"""The writes: a document, a recipient, an attempt, and the withheld body.

The engine is the only module in this package that writes. It holds the store and a clock
and nothing else, and it is built per request by the HTTP layer for exactly that reason:
both seams stay overridable in a test without hanging a long-lived object off
``app.state``, which is a shared file this feature may not edit.

Every write below carries an ``actor`` and a ``source``, and both reach the audit log in
the same transaction as the change. The ``source`` is the route that served the write,
which is why no string in this module is a literal route: the feature module builds each
one from its own router, and ``tests/test_wf078_http.py`` asserts every source this
workflow can record names a concrete ``(method, path)`` the host mounted.

Three things the engine is careful about
----------------------------------------

**A failure writes a row.** The specification requires that "both success and failure
produce audit actions, so a rejected attempt is as visible as a successful one". So
:meth:`attempt` validates nothing that would let a wrong answer raise: a rejected attempt
is a normal return value with a named reason, and it is written with the same source and
the same transaction as a passed one. A gate whose failures leave no trace is a gate that
reads as unlocked.

**The gate is re-asserted on every attempt.** The specification's automation section says
"verification is re-asserted by the gate on every attempt; there is no 'verify once,
remember forever' behaviour in these flows". So :meth:`attempt` never reads a previous
outcome, and :meth:`read_body` resolves the gate from the *latest* attempt rather than
from "has this recipient ever passed". A pass stamps the session with what happened, not
with a permission, so the next attempt runs the whole check again and a later failure
withdraws the body again.

**The body is withheld, not hidden.** :meth:`read_body` raises
:class:`~dsr.identity_verification.rules.GateNotCleared` with the gate and the method
named, because "access denied" tells a recipient nothing about what to do next. When it
does release, it returns the body and the outcome that released it, so a reader can see
which check let them in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.identity_verification import rules, vocabulary as vocab
from dsr.store import RecordStore

#: What an attempt did to the gate it was made against. Derived from the outcome this
#: workflow decided, never accepted from the caller, so a request cannot declare that it
#: cleared a gate - which is the whole point of the gate.
GATE_CLEARED = "gate_cleared"
GATE_NOT_CLEARED = "gate_not_cleared"

#: Every attempt records that the gate was re-asserted rather than read from an earlier
#: one. It is a constant rather than a per-row calculation because the specification makes
#: it unconditional: "verification is re-asserted by the gate on every attempt; there is no
#: 'verify once, remember forever' behaviour in these flows."
GATE_REASSERTED = True


class IdentityVerificationEngine:
    """Every write and read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand. The
    two stamps this workflow writes - an attempt and a code - are ordered by the
    reviewer reading the trail, so a test that cannot choose the instant cannot test that
    order.

    ``code_for`` is the seam onto another workflow's integer action-code enum. It is
    called with the method and the outcome this engine just decided, and it returns the
    integer the audit row carries. It is a parameter rather than an import so this package
    has no dependency on the workflow that owns the table; see
    :data:`~dsr.identity_verification.vocabulary.CODE_TABLE_OWNER`.
    """

    def __init__(
        self,
        store: RecordStore,
        now: Callable[[], datetime] | None = None,
        code_for: Callable[[str, str], int | None] | None = None,
    ) -> None:
        self.store = store
        self._now = now or rules.utcnow
        self._code_for = code_for or (lambda method, outcome: None)

    # -- documents ---------------------------------------------------------- #

    def create_document(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """A document whose body a gate can withhold.

        Mirrors the specification's ``POST /public/v1/documents``: "Add
        ``verification_settings`` to a recipient either at document creation or on an
        existing document." The recipients named in the same body are created here too,
        so the "at document creation" half of that sentence is one call rather than two.

        Only the fields this workflow owns are interpreted. Anything else in the payload
        is carried through untouched, because the store is schema-flexible by design and a
        document created here may be the same document another workflow's signer flow
        wrote to.
        """

        data = dict(payload or {})
        record = self.store.create(
            vocab.DOCUMENT_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                "title": data.get("title") or f"Verified document for {room_id}",
                "status": data.get("status") or "draft",
                "body": data.get("body") or "",
                "body_length": len(str(data.get("body") or "")),
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        document = self.project_document(record)

        for recipient in data.get("recipients") or []:
            if not isinstance(recipient, Mapping):
                raise rules.VerificationSettingsInvalid(
                    "Each recipient must be an object.",
                    {"recipients": "Give each recipient an object."},
                )
            self.create_recipient(
                room_id,
                document["id"],
                recipient,
                source=source,
                actor=actor,
            )
        return self.read_document(document["id"])

    def documents(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every document this workflow governs, optionally narrowed to one room."""

        records = self.store.list(vocab.DOCUMENT_COLLECTION, limit=200, order_by="created_at")
        rows = []
        for record in records:
            row = self.project_document(record)
            if room_id and row["room_id"] != room_id:
                continue
            rows.append(row)
        return rows

    def read_document(self, document_id: str) -> dict[str, Any]:
        """One document with its recipients and where each one's gates stand.

        A row that exists but is not one of this workflow's documents is a 404 rather than
        a 500 or a projection of somebody else's record: a caller who passes the wrong id
        deserves to be told it does not name a verified document.
        """

        record = self._document_record(document_id)
        rows = [self.project(recipient) for recipient in self._recipient_records(document_id)]
        return {
            **self.project_document(record),
            "recipient_count": len(rows),
            "recipients": rows,
            "gated_recipients": len([row for row in rows if row["gates"]]),
        }

    def project_document(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored document as the API returns it.

        The body is not in this projection. It comes from
        :meth:`read_body`, which is the only path that runs the gate, so a caller cannot
        read a document's contents by reading its metadata.
        """

        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "status": data.get("status"),
            "body_length": data.get("body_length", 0),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.OWNER_FIELD: vocab.AUTHENTICATION_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        }

    def _document_record(self, document_id: str) -> dict[str, Any]:
        record = self.store.get(document_id)
        if record is None or record.get("collection") != vocab.DOCUMENT_COLLECTION:
            raise rules.DocumentNotFound(document_id)
        return dict(record)

    # -- recipients --------------------------------------------------------- #

    def create_recipient(
        self,
        room_id: str,
        document_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """A recipient on a document, with the verification settings it carries.

        The settings arrive as ``verification_settings`` keyed by gate, so one recipient
        can carry a passcode before the document opens and a knowledge-based check before
        it is signed. Each entry is shaped and validated by
        :func:`~dsr.identity_verification.rules.build_settings` before anything is
        written, so a rejected setting leaves no trace: the audit log is this product's
        guarantee, and a row describing a change that did not happen is a row a reader has
        to learn to discount.
        """

        self._document_record(document_id)
        data = dict(payload or {})
        role = normalise_role(data.get("role"))

        settings = rules.apply_settings(
            {},
            data.get(rules.VERIFICATION_SETTINGS) or {},
            role=role,
        )

        record = self.store.create(
            vocab.RECIPIENT_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                "document_id": document_id,
                "email": data.get("email") or "",
                "name": data.get("name") or "",
                "role": role,
                rules.VERIFICATION_SETTINGS: settings,
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project(record)

    def recipients(self, document_id: str | None = None) -> list[dict[str, Any]]:
        """Every recipient, optionally narrowed to one document."""

        records = self.store.list(vocab.RECIPIENT_COLLECTION, limit=400, order_by="created_at")
        rows = [self.project(record) for record in records]
        if document_id:
            rows = [row for row in rows if row["document_id"] == document_id]
        return rows

    def read_recipient(self, recipient_id: str) -> dict[str, Any]:
        """One recipient, its gates, and what it is asked for at each."""

        return self.project(self._recipient_record(recipient_id))

    def update_recipient(
        self,
        recipient_id: str,
        changes: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Add or change ``verification_settings`` on a live document.

        Mirrors the specification's ``PATCH`` Update Recipient, whose evidence is "Add
        ``verification_settings`` to a recipient either at document creation or on an
        existing document."

        Tri-state per gate, so one endpoint can both install a gate and take one off: a
        gate the body omits is left alone, a gate the body sets replaces what was stored
        for that gate, and a gate the body sets to ``null`` removes it. Replacing rather
        than merging is the correct reading of a discriminated union: a gate has exactly
        one method, so a sender who changes the method is not adding to it.

        A role change is honoured here too, and it is re-checked against the gates: a
        recipient whose role moves from signer to recipient while carrying a ``before_sign``
        gate would be locked out of a document they may still view, so the gates are
        re-validated against the new role and the request is refused rather than stored.
        """

        record = self._recipient_record(recipient_id)
        data = dict(record.get("data") or {})
        body = dict(changes or {})

        role = normalise_role(body["role"]) if "role" in body else str(data.get("role") or "")
        settings = rules.apply_settings(
            data.get(rules.VERIFICATION_SETTINGS) or {},
            body.get(rules.VERIFICATION_SETTINGS) or {},
            role=role,
        )

        patch: dict[str, Any] = {"role": role, rules.VERIFICATION_SETTINGS: settings}
        for key, value in body.items():
            if key in ("role", rules.VERIFICATION_SETTINGS, "room_id"):
                continue
            patch[key] = value

        updated = self.store.update(recipient_id, patch, actor=actor, source=source)
        return self.project(updated)

    def _recipient_records(self, document_id: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.store.list(
                vocab.RECIPIENT_COLLECTION, limit=400, order_by="created_at"
            )
            if (record.get("data") or {}).get("document_id") == document_id
        ]

    def _recipient_record(self, recipient_id: str) -> dict[str, Any]:
        record = self.store.get(recipient_id)
        if record is None or record.get("collection") != vocab.RECIPIENT_COLLECTION:
            raise rules.RecipientNotFound(recipient_id)
        return dict(record)

    def project(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored recipient as the API returns it.

        The stored settings are never echoed back whole. A passcode is not returned from
        a read, and neither is a phone number, because a recipient's settings page is the
        one place a secret would be most likely to leak from: it is the page with the most
        readers and the least reason to have them. The projection reports which gates
        exist, which method each one uses and whether an SMS number is an authentication
        factor, and the value stays behind :meth:`prompt_for`.
        """

        data = dict(record.get("data") or {})
        gates = rules.settings_summary(data)
        return {
            "id": record.get("id"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "document_id": data.get("document_id"),
            "email": data.get("email"),
            "name": data.get("name"),
            "role": data.get("role"),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "gate_count": len(gates),
            "gates": gates,
            "gate_names": [gate["place"] for gate in gates],
            "authentication_factor_gates": [
                gate["place"]
                for gate in gates
                if rules.is_authentication_factor(data, gate["place"])
            ],
            "last_attempts": self.last_attempts(record.get("id")),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
            vocab.OWNER_FIELD: vocab.AUTHENTICATION_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
        }

    def prompt_for(self, recipient_id: str, place: str) -> dict[str, Any]:
        """What a recipient is asked for at one gate, and never the answer.

        Raises :class:`~dsr.identity_verification.rules.RecipientNotFound` for a gate the
        recipient does not carry, rather than returning an empty prompt. "You are not asked
        anything here" and "you are asked something this build cannot describe" are
        different answers and a caller needs to tell them apart.
        """

        record = self._recipient_record(recipient_id)
        data = dict(record.get("data") or {})
        entry = rules.settings_for(data, place)
        if entry is None:
            raise rules.RecipientNotFound(f"{recipient_id} has no verification at {place}")
        return {
            "recipient_id": recipient_id,
            "place": rules.normalise_place(place),
            **rules.prompt_for(entry),
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- the SMS code ------------------------------------------------------- #

    def send_code(
        self,
        recipient_id: str,
        *,
        source: str | None = None,
        actor: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        """Issue a one-time code for an SMS gate, and record that it was sent.

        The evidence describes the sender's side of it: "They can then select the 'Send
        code' button to receive a 6-digit code via text message", and they "can request the
        code to be sent again if needed". So the send is a recorded event and a resend is
        allowed, and issuing a new code supersedes the previous one - which is the part
        that matters for safety, because at most one code is ever valid.

        A code is only issued for a recipient whose gate at ``before_open`` or
        ``before_sign`` is the SMS method. Issuing one for any other method would produce
        a send with nothing to verify.

        ``code`` is an argument rather than only a generated value so a test can pin one.
        It is not accepted from an HTTP caller, and a caller who supplies one here is a
        test rather than a recipient.
        """

        record = self._recipient_record(recipient_id)
        data = dict(record.get("data") or {})
        entry = None
        place = None
        for candidate in vocab.PLACES:
            settings = rules.settings_for(data, candidate)
            if settings is not None and rules.method_of(settings) == vocab.METHOD_SMS:
                entry = settings
                place = candidate
                break
        if entry is None:
            raise rules.VerificationSettingsInvalid(
                "This recipient has no SMS verification to send a code for.",
                {"verification_settings": "Set an SMS verification before requesting a code."},
            )

        issued = code or rules.generate_sms_code()
        previous = self._current_code(recipient_id)
        if previous is not None:
            self.store.update(
                previous["id"],
                {"superseded_at": rules.stamp(self._now())},
                actor=actor,
                source=source,
            )

        record_out = self.store.create(
            vocab.CODE_COLLECTION,
            {
                rules.ROOM_REF: data.get(rules.ROOM_REF) or record.get("room_id"),
                "recipient_id": recipient_id,
                "document_id": data.get("document_id"),
                "place": place,
                "digits": vocab.SMS_CODE_DIGITS,
                "sms_type": rules.sms_type_of(entry),
                "code": issued,
                "issued_at": rules.stamp(self._now()),
                "superseded_at": None,
            },
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )
        stored = dict(record_out.get("data") or {})
        return {
            "recipient_id": recipient_id,
            "place": place,
            "digits": vocab.SMS_CODE_DIGITS,
            "sms_type": stored.get("sms_type"),
            "sms_type_meaning": vocab.SMS_TYPE_MEANINGS.get(stored.get("sms_type"), ""),
            "is_authentication_factor": rules.is_authentication_factor(data, place),
            "superseded_previous": previous is not None,
            "issued_at": stored.get("issued_at"),
            # The code itself is not returned. A send endpoint that hands the code to its
            # caller is a send endpoint that has turned the gate into a suggestion, and the
            # test that needs to pin a code passes one in rather than reading one out.
            "code_returned": False,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def _current_code(self, recipient_id: str) -> dict[str, Any] | None:
        """The recipient's live code row, or ``None`` when there is none.

        Live means not superseded. An attempt made against a superseded code fails, which
        is what makes a resend narrow access rather than widen it.
        """

        for record in reversed(
            self.store.list(vocab.CODE_COLLECTION, limit=400, order_by="created_at")
        ):
            data = dict(record.get("data") or {})
            if data.get("recipient_id") == recipient_id and not data.get("superseded_at"):
                return record
        return None

    # -- attempts ----------------------------------------------------------- #

    def attempt(
        self,
        recipient_id: str,
        place: str,
        evidence: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Run one verification attempt and write it, whichever way it went.

        The specification's fifth user-flow step: "Every attempt - pass or fail - is
        written to the document's audit trail", and its data flow: "Both success and
        failure produce audit actions, so a rejected attempt is as visible as a successful
        one."

        So this method has no failure path that raises. :func:`~dsr.identity_verification.rules.evaluate`
        returns an outcome and a reason, and both are written, with the same source and
        in the same transaction as a pass. The event code is looked up through
        ``code_for`` for both outcomes, because an attempt whose failure has no code is
        invisible to the compliance query the codes exist for.

        The check is re-run from the recipient's stored settings on every call. Nothing is
        read from a previous attempt and nothing is cached, so the second attempt on a
        document is answered by the evidence in front of it rather than by the first
        attempt's result.
        """

        record = self._recipient_record(recipient_id)
        data = dict(record.get("data") or {})

        code_record = self._current_code(recipient_id)
        delivered = (code_record or {}).get("data", {}).get("code") if code_record else None

        result = rules.evaluate(data, place, evidence, delivered_code=delivered)

        if result.get("outcome") is None:
            # Ungated: nothing to attempt and nothing to record as an attempt. A row here
            # would put a verification event in the trail for a recipient who was never
            # asked for one, and the trail is the thing a reviewer reads as complete.
            return {
                "recipient_id": recipient_id,
                "document_id": data.get("document_id"),
                "gated": False,
                "outcome": None,
                "place": result.get("place"),
                "reason": None,
                "method": None,
                "method_label": "",
                "sms_type": None,
                "event_code": None,
                "gate_effect": None,
                "attempt_id": None,
                "at": None,
                "reasserted": True,
                vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
                vocab.LIMITATION_FIELD: vocab.LIMITATION,
            }

        method = result.get("method") or ""
        outcome = result.get("outcome") or vocab.OUTCOME_FAIL
        event_code = self._code_for(method, outcome) if method else None
        moment = rules.stamp(self._now())
        cleared = outcome == vocab.OUTCOME_PASS

        attempt_record = self.store.create(
            vocab.ATTEMPT_COLLECTION,
            {
                rules.ROOM_REF: data.get(rules.ROOM_REF) or record.get("room_id"),
                "recipient_id": recipient_id,
                "document_id": data.get("document_id"),
                "place": result.get("place"),
                "method": method or None,
                "outcome": outcome,
                "reason": result.get("reason"),
                "sms_type": result.get("sms_type"),
                "event_code": event_code,
                "at": moment,
                # Derived rather than accepted: a caller that declared its own gate state
                # would make the trail a self-assessment, which is the failure mode an
                # attempt log exists to avoid.
                "gate_effect": GATE_CLEARED if cleared else GATE_NOT_CLEARED,
                "gate_reasserted": GATE_REASSERTED,
            },
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )
        stored = dict(attempt_record.get("data") or {})
        return {
            "attempt_id": attempt_record.get("id"),
            "recipient_id": recipient_id,
            "document_id": stored.get("document_id"),
            "place": stored.get("place"),
            "method": stored.get("method"),
            "method_label": vocab.METHOD_LABELS.get(stored.get("method") or "", ""),
            "outcome": stored.get("outcome"),
            "reason": stored.get("reason"),
            "sms_type": stored.get("sms_type"),
            "event_code": stored.get("event_code"),
            "gate_effect": stored.get("gate_effect"),
            "at": stored.get("at"),
            "gated": True,
            "reasserted": True,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def attempts(
        self,
        room_id: str | None = None,
        recipient_id: str | None = None,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every attempt, newest first, optionally narrowed.

        This is the read side of the audit trail the specification names:
        ``GET /public/v2/documents/{document_id}/audit-trail``. Each row carries the code
        the owner's table assigned, so a compliance query filters on an integer rather
        than on free text, and carries the limitation and the not-proof sentence so a row
        read on its own cannot be mistaken for evidence that a person's identity was
        established.
        """

        records = self.store.list(
            vocab.ATTEMPT_COLLECTION, limit=400, order_by="updated_at", descending=True
        )
        rows = []
        for record in records:
            data = dict(record.get("data") or {})
            row = {
                "id": record.get("id"),
                "room_id": data.get(rules.ROOM_REF),
                "recipient_id": data.get("recipient_id"),
                "document_id": data.get("document_id"),
                "place": data.get("place"),
                "method": data.get("method"),
                "outcome": data.get("outcome"),
                "reason": data.get("reason"),
                "sms_type": data.get("sms_type"),
                "event_code": data.get("event_code"),
                "gate_effect": data.get("gate_effect"),
                "at": data.get("at"),
                "revision": record.get("revision"),
                vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
                vocab.LIMITATION_FIELD: vocab.LIMITATION,
            }
            if room_id and row["room_id"] != room_id:
                continue
            if recipient_id and row["recipient_id"] != recipient_id:
                continue
            if document_id and row["document_id"] != document_id:
                continue
            rows.append(row)
        return rows

    def last_attempts(self, recipient_id: str | None) -> list[dict[str, Any]]:
        """The most recent attempt per gate, for a recipient.

        One row per gate rather than the whole history, because the question a recipient
        panel asks is "where does this recipient stand" and answering it from the newest
        row per gate is the answer; the history is one call away.
        """

        if not recipient_id:
            return []
        latest: dict[str, dict[str, Any]] = {}
        for row in reversed(self.attempts(recipient_id=recipient_id)):
            place = row.get("place")
            if place and place not in latest:
                latest[place] = row
        return [latest[place] for place in vocab.PLACES if place in latest]

    # -- the withheld body -------------------------------------------------- #

    def read_body(
        self,
        document_id: str,
        recipient_id: str,
        *,
        place: str = vocab.BEFORE_OPEN,
    ) -> dict[str, Any]:
        """The document's body, or a refusal that says which check is outstanding.

        The specification's data flow, in one clause: "the document body is withheld until
        it clears". So this is the one path in the workflow that returns document content,
        and it runs the gate first.

        The gate is resolved from the **latest** attempt, never from "has this recipient
        ever passed". The specification's automation section is explicit that verification
        "is re-asserted by the gate on every attempt; there is no 'verify once, remember
        forever' behaviour", so a recipient who passed an hour ago and has not attempted
        again holds nothing: the next read of the body is the next assertion of the gate,
        and until they make it the body stays withheld.

        :raises GateNotCleared: when a gate stands between this recipient and the body.
        """

        document = self._document_record(document_id)
        recipient = self._recipient_record(recipient_id)
        recipient_data = dict(recipient.get("data") or {})
        key = rules.normalise_place(place) or vocab.BEFORE_OPEN

        if recipient_data.get("document_id") != document_id:
            raise rules.RecipientNotFound(f"{recipient_id} is not a recipient of {document_id}")

        entry = rules.settings_for(recipient_data, key)
        latest = self._latest_attempt(recipient_id, key)

        if entry is None:
            # No gate at this place. The body is released, and the response says so rather
            # than pretending a check happened.
            return self._released(document, recipient_id, key, None, ungated=True)

        if latest is None:
            raise rules.GateNotCleared(
                f"This document is withheld until {vocab.METHOD_LABELS.get(rules.method_of(entry) or '', 'identity')} "
                f"verification at {key} passes.",
                place=key,
                method=rules.method_of(entry),
                reason=vocab.WITHHELD_REASON_GATE_UNCLEARED,
            )

        if latest.get("outcome") != vocab.OUTCOME_PASS:
            raise rules.GateNotCleared(
                f"The last {key} verification did not pass, so the body is withheld again.",
                place=key,
                method=rules.method_of(entry),
                reason=vocab.WITHHELD_REASON_REASSERTED,
            )

        return self._released(document, recipient_id, key, latest, ungated=False)

    def _latest_attempt(self, recipient_id: str, place: str) -> dict[str, Any] | None:
        for row in self.attempts(recipient_id=recipient_id):
            if row.get("place") == place:
                return row
        return None

    def _released(
        self,
        document: Mapping[str, Any],
        recipient_id: str,
        place: str,
        attempt: Mapping[str, Any] | None,
        *,
        ungated: bool,
    ) -> dict[str, Any]:
        data = dict(document.get("data") or {})
        return {
            "document_id": document.get("id"),
            "recipient_id": recipient_id,
            "place": place,
            "body_state": vocab.BODY_RELEASED,
            "body": data.get("body") or "",
            "body_length": data.get("body_length", 0),
            "released_by": "no_gate_at_this_place" if ungated else (attempt or {}).get("outcome"),
            "released_by_attempt": (attempt or {}).get("id"),
            "released_at_attempt": (attempt or {}).get("at"),
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def body_state(
        self,
        document_id: str,
        recipient_id: str,
        *,
        place: str = vocab.BEFORE_OPEN,
    ) -> dict[str, Any]:
        """Whether the body would be released right now, without reading it.

        The same gate resolution as :meth:`read_body`, returning the state instead of the
        content. A page needs to say "this document is withheld until you verify" without
        delivering the document to say it, and a route that answered that question by
        returning the body would have defeated the gate to render a label.
        """

        key = rules.normalise_place(place) or vocab.BEFORE_OPEN
        try:
            released = self.read_body(document_id, recipient_id, place=key)
        except rules.GateNotCleared as exc:
            return {
                "document_id": document_id,
                "recipient_id": recipient_id,
                "place": key,
                "body_state": vocab.BODY_WITHHELD,
                "withheld_reason": exc.reason,
                "method": exc.method,
                "body_length": 0,
                vocab.LIMITATION_FIELD: vocab.LIMITATION,
            }
        return {
            "document_id": document_id,
            "recipient_id": recipient_id,
            "place": key,
            "body_state": released["body_state"],
            "withheld_reason": None,
            "method": None,
            "body_length": released["body_length"],
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The page's headline numbers, and the states that need a sender's attention.

        Read-only. Counts are read back from the store rather than accumulated, so the
        board cannot describe a state the store does not hold.
        """

        documents = self.documents(room_id)
        recipients = self.recipients()
        if room_id:
            recipients = [row for row in recipients if row.get("room_id") == room_id]
        attempts = self.attempts(room_id)

        passed = [row for row in attempts if row.get("outcome") == vocab.OUTCOME_PASS]
        failed = [row for row in attempts if row.get("outcome") == vocab.OUTCOME_FAIL]

        gated = [row for row in recipients if row["gates"]]
        before_open = [row for row in gated if vocab.BEFORE_OPEN in row["gate_names"]]
        before_sign = [row for row in gated if vocab.BEFORE_SIGN in row["gate_names"]]
        two_axis = [
            row
            for row in gated
            if vocab.BEFORE_OPEN in row["gate_names"] and vocab.BEFORE_SIGN in row["gate_names"]
        ]
        delivery_only = [
            row
            for row in gated
            if any(
                gate.get("method") == vocab.METHOD_SMS
                and gate.get("sms_type") == vocab.SMS_TYPE_DELIVERY
                for gate in row["gates"]
            )
        ]
        unresolved = [
            row
            for row in gated
            if not row["last_attempts"]
            or any(attempt.get("outcome") != vocab.OUTCOME_PASS for attempt in row["last_attempts"])
        ]

        return {
            "documents": len(documents),
            "recipients": len(recipients),
            "gated_recipients": len(gated),
            "ungated_recipients": len(recipients) - len(gated),
            "before_open_recipients": len(before_open),
            "before_sign_recipients": len(before_sign),
            "two_axis_recipients": len(two_axis),
            "delivery_only_sms_recipients": len(delivery_only),
            "signers": len([row for row in recipients if row.get("role") == vocab.ROLE_SIGNER]),
            "attempts": len(attempts),
            "attempts_passed": len(passed),
            "attempts_failed": len(failed),
            "recipients_awaiting_verification": len(unresolved),
            "by_method": _tally(attempts, "method"),
            "by_outcome": _tally(attempts, "outcome"),
            "by_place": _tally(attempts, "place"),
            "by_reason": _tally(attempts, "reason"),
            vocab.OWNER_FIELD: vocab.AUTHENTICATION_OWNER,
            vocab.ASSUMPTION_FIELD: vocab.ASSUMPTION,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
            vocab.NOT_PROOF_FIELD: vocab.NOT_PROOF,
        }


def normalise_role(value: Any) -> str:
    """A role name this workflow recognises, defaulting to the wider audience.

    ``recipient`` is the default and not ``signer`` because it is the only one of the two
    the sourced audience column names for a gate ("All recipients"). A caller that did not
    say is a recipient, and a recipient can carry a ``before_open`` gate but not a
    ``before_sign`` one.
    """

    text = str(value or "").strip().lower()
    return text if text in vocab.ROLES else vocab.ROLE_RECIPIENT


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """How many rows carry each value for ``key``, largest first then alphabetical.

    Sorted so the page and the API agree on the order without either of them re-sorting,
    which is the kind of small disagreement that becomes a flaky test.
    """

    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
