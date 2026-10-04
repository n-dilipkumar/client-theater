"""The reads and the writes: a subscription, a document, a delivery, an artifact, an attempt.

The engine is the only module in this package that writes. It holds the store and a clock
and nothing else, and it is built per request by the HTTP layer for exactly that reason:
both seams stay overridable in a test without hanging a long-lived object off
``app.state``, which is a shared file this feature may not edit.

Every write below carries an ``actor`` and a ``source``, and both reach the audit log in
the same transaction as the change. The ``source`` is the route that served the write,
which is why no string in this module is a literal route: the feature module builds each
one from its own router, and ``tests/test_wf080_http.py`` asserts every source this
workflow can record names a concrete ``(method, path)`` the host mounted.

The order the rules are enforced in
----------------------------------

Validate before writing anything, so a rejected request leaves no trace. That is not a
style choice. The audit log is this product's guarantee, and an audit trail carrying a
row for a request that changed nothing is a trail a reader has to learn to discount.

Two refusals write an attempt row and the rest do not, and the difference is deliberate.
A fetch that was told to wait and a fetch that was told the room was going too fast are
facts about the vendor's answer and about this room's own limit, and both are worth
keeping. A fetch refused for an environment the caller cannot change, or for a document
that has not started generating, records nothing: repeating it produces the same refusal,
and a log of identical refusals is noise rather than evidence.

The retry loop, end to end
--------------------------

The specification's flow is "Completion -> async PDF generation -> e-vault storage ->
``document_completed_pdf_ready`` event -> client GET ``/download-protected`` -> binary PDF
returned, or 202 + Retry-After while generation finishes". So:

1. :meth:`register_document` records the agreement and its state.
2. :meth:`receive_event` takes one delivery, dedupes it on the delivery id, and advances
   the document to ``generating`` when the ready event names one it knows.
3. :meth:`retrieve` calls the vendor. While the state is ``generating`` it stores
   ``back_pressure`` with the wait. Once the state is ``sealed`` it stores the bytes, the
   length and the digest, and answers ``retrieved``.
4. :meth:`serve_protected` streams the stored bytes, or answers 202 with a ``Retry-After``
   header and no body at all, which is the vendor's own shape.

Nothing sleeps between those steps and nothing polls. The clock is injected so a test can
walk the whole loop by hand.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.security_governance import (
    evault_inferences,
    evault_rules as rules,
    evault_vocabulary as vocab,
)
from dsr.store import RecordStore

#: The four honesty fields, in one dict, spread into every projection.
#:
#: One definition rather than four sentences repeated in five places, because a caveat
#: that exists in four of five responses is a caveat a reader learns to skip. The field
#: names live in the vocabulary and the values are the specification's own sentences, so a
#: later edit to the wording lands everywhere at once.
HONESTY: dict[str, Any] = {
    vocab.EFFECT_FIELD: vocab.EFFECT,
    vocab.TRADEOFF_FIELD: vocab.VARIANT_TRADEOFF,
    vocab.SEAL_SCOPE_FIELD: vocab.SEAL_SCOPE,
    vocab.NO_POLLING_FIELD: vocab.NO_POLLING,
}


class EvaultEngine:
    """Every write and read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand. The
    throttle window is measured in seconds, so a test that cannot choose the instant cannot
    test the boundary, and the boundary is the rule most likely to be wrong.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # ----------------------------------------------------------------------- #
    # Subscriptions
    # ----------------------------------------------------------------------- #

    def create_subscription(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create the subscription this workflow depends on.

        Mirrors ``POST /public/v1/webhook-subscriptions`` with
        ``triggers: ["document_completed_pdf_ready"]``.

        The one field this build insists on is the ready trigger, and it is refused rather
        than defaulted: "subscribe to the *ready* event rather than polling status", and
        this package never polls. Everything else in the payload is carried through as
        stored JSON, because the store is schema-flexible by design and a caller may be
        storing fields this workflow does not interpret.
        """

        data = dict(payload or {})
        triggers = rules.coerce_triggers(data.get("triggers"))
        rules.require_ready_trigger(triggers)
        environment = rules.coerce_environment(data.get("environment"))

        vendor_document_id = str(data.get("vendor_document_id") or "").strip()
        body: dict[str, Any] = {
            rules.ROOM_REF: room_id,
            "triggers": list(triggers),
            "environment": environment,
            "active": bool(data.get("active", True)),
            "vendor_document_id": vendor_document_id,
            "shared_key": rules.derive_shared_key(vendor_document_id or room_id),
            "retry_after_seconds": rules.retry_after_seconds(data),
            "state": "active",
            "created_at": rules.stamp(self._now()),
        }
        for key, value in data.items():
            if key not in ("triggers", "environment", "active"):
                body.setdefault(key, value)

        record = self.store.create(
            vocab.SUBSCRIPTION_COLLECTION,
            body,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project_subscription(record)

    def subscriptions(
        self, room_id: str | None = None, include_cancelled: bool = True
    ) -> list[dict[str, Any]]:
        """Every subscription, newest first, optionally narrowed to one room."""
        records = self.store.list(vocab.SUBSCRIPTION_COLLECTION, limit=200)
        rows = []
        for record in records:
            projected = self.project_subscription(record)
            if room_id and projected["room_id"] != room_id:
                continue
            if not include_cancelled and not projected["active"]:
                continue
            rows.append(projected)
        return rows

    def read_subscription(self, subscription_id: str) -> dict[str, Any]:
        """One subscription, or :class:`~dsr.security_governance.evault_rules.SubscriptionNotFound`.

        A row that exists but is not one of this workflow's subscriptions is a 404 rather
        than a projection of somebody else's record: a feature may not read across into
        another workflow's collection.
        """
        record = self._owned(subscription_id, vocab.SUBSCRIPTION_COLLECTION)
        if record is None:
            raise rules.SubscriptionNotFound(subscription_id)
        return self.project_subscription(record)

    def update_subscription(
        self,
        subscription_id: str,
        changes: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Rotate the triggers, the wait or the active flag. Mirrors ``PATCH``.

        Tri-state for the triggers: a body that omits ``triggers`` leaves them alone, a
        body that supplies the key replaces the whole list, and a body that supplies
        ``null`` leaves the field out entirely rather than emptying it. The list is
        validated as a whole before anything is written, so a body that would leave the
        subscription unable to hear the ready event is refused and the stored list is
        untouched.
        """
        record = self._owned(subscription_id, vocab.SUBSCRIPTION_COLLECTION)
        if record is None:
            raise rules.SubscriptionNotFound(subscription_id)
        current = dict(record.get("data") or {})
        changes = dict(changes or {})

        patch: dict[str, Any] = {}
        if "triggers" in changes and changes["triggers"] is not None:
            triggers = rules.coerce_triggers(changes["triggers"])
            rules.require_ready_trigger(triggers)
            patch["triggers"] = list(triggers)
        if "environment" in changes and changes["environment"] is not None:
            patch["environment"] = rules.coerce_environment(changes["environment"])
        if "retry_after_seconds" in changes and changes["retry_after_seconds"] is not None:
            patch["retry_after_seconds"] = rules.retry_after_seconds(
                {**current, "retry_after_seconds": changes["retry_after_seconds"]}
            )
        if "active" in changes and changes["active"] is not None:
            active = bool(changes["active"])
            patch["active"] = active
            patch["state"] = "active" if active else "cancelled"
            if active:
                patch.pop("cancelled_at", None)
            else:
                patch["cancelled_at"] = rules.stamp(self._now())
        for key, value in changes.items():
            if key in ("triggers", "environment", "retry_after_seconds", "active", "state"):
                continue
            patch.setdefault(key, value)

        if not patch:
            return self.project_subscription(record)

        updated = self.store.update(subscription_id, patch, actor=actor, source=source)
        return self.project_subscription(updated)

    def cancel_subscription(
        self,
        subscription_id: str,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Stop listening, keeping the row readable.

        Answers the vendor's ``DELETE`` verb and records a cancellation rather than a
        deletion. The reasoning is in ``DERIVED_CANCEL_KEEPS_THE_ROW``: a soft delete
        would make the record of having listened unreachable, and the first question a
        reader of an e-signature trail asks is which deliveries arrived under a
        subscription that is now gone.

        Cancelling twice is idempotent rather than refused. The caller asked for a state
        the subscription is already in, so there is nothing to refuse and refusing would
        make a retry of a successful request look like a failure.
        """
        record = self._owned(subscription_id, vocab.SUBSCRIPTION_COLLECTION)
        if record is None:
            raise rules.SubscriptionNotFound(subscription_id)
        current = dict(record.get("data") or {})
        if current.get("active") is False:
            return self.project_subscription(record)
        updated = self.store.update(
            subscription_id,
            {"active": False, "state": "cancelled", "cancelled_at": rules.stamp(self._now())},
            actor=actor,
            source=source,
        )
        return self.project_subscription(updated)

    def active_subscriptions(self, room_id: str) -> list[dict[str, Any]]:
        """The subscriptions this room can still hear on."""
        return [row for row in self.subscriptions(room_id) if row["active"]]

    # ----------------------------------------------------------------------- #
    # Executed documents
    # ----------------------------------------------------------------------- #

    def register_document(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record the agreement this room is waiting on.

        The vendor document id is the join key the ready event carries, so it is required
        and it is unique within the room: two rows claiming one vendor id would make the
        join ambiguous, and an ambiguous join in an e-signature trail is the failure this
        workflow most needs to avoid. The refusal carries the existing row's id so the
        caller can find it rather than guess.
        """
        data = dict(payload or {})
        vendor_document_id = str(data.get("vendor_document_id") or "").strip()
        if not vendor_document_id:
            raise rules.DocumentInvalid(
                "An executed document must carry the vendor document id the event will name.",
                {"vendor_document_id": "vendor_document_id is required."},
            )
        state = rules.coerce_state(data.get("state"))
        environment = rules.coerce_environment(data.get("environment"))

        existing = self._document_by_vendor_id(room_id, vendor_document_id)
        if existing is not None:
            raise rules.DocumentInvalid(
                "This room already holds a row for that vendor document id.",
                {
                    "vendor_document_id": (
                        f"{vendor_document_id} is already recorded as {existing['id']}."
                    )
                },
            )

        body: dict[str, Any] = {
            rules.ROOM_REF: room_id,
            "vendor_document_id": vendor_document_id,
            "state": state,
            "environment": environment,
            "subject": str(data.get("subject") or f"Executed agreement {vendor_document_id}"),
            "created_at": rules.stamp(self._now()),
            "updated_state_at": rules.stamp(self._now()),
        }
        for key, value in data.items():
            if key not in ("state", "environment"):
                body.setdefault(key, value)

        record = self.store.create(
            vocab.DOCUMENT_COLLECTION,
            body,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project_document(record)

    def documents(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every executed document, newest first, optionally narrowed to one room."""
        rows = []
        for record in self.store.list(vocab.DOCUMENT_COLLECTION, limit=200):
            projected = self.project_document(record)
            if room_id and projected["room_id"] != room_id:
                continue
            rows.append(projected)
        return rows

    def read_document(self, document_id: str, room_id: str | None = None) -> dict[str, Any]:
        """One executed document, with its artifact summary and its attempt tally.

        The three flags in the projection are the workflow's state read as booleans, and
        they are here rather than in the page because all three are rules about what the
        vendor can do right now, not about what this room chose: whether a PDF is ready,
        whether it is being produced, and whether the signers have finished.
        """
        record = self._owned(document_id, vocab.DOCUMENT_COLLECTION)
        if record is None:
            raise rules.DocumentNotFound(document_id)
        if room_id and (record.get("data") or {}).get(rules.ROOM_REF) != room_id:
            raise rules.DocumentNotFound(document_id)
        return self.document_view(record)

    def set_state(
        self,
        document_id: str,
        state: str,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Move an executed document to another state. An internal write.

        Not a route. The specification gives this workflow exactly one way to learn that a
        PDF exists - the ready event - so a route that let a caller set the state directly
        would be a second mechanism, and a second mechanism is how a room ends up serving a
        PDF that was never announced. The vendor integration calls this when it observes a
        state change; the demo seeder calls it to show a document whose PDF has finished
        being produced, and no HTTP caller can.
        """
        record = self._owned(document_id, vocab.DOCUMENT_COLLECTION)
        if record is None:
            raise rules.DocumentNotFound(document_id)
        updated = self.store.update(
            document_id,
            {"state": rules.coerce_state(state), "updated_state_at": rules.stamp(self._now())},
            actor=actor,
            source=source,
        )
        return self.project_document(updated)

    # ----------------------------------------------------------------------- #
    # The webhook
    # ----------------------------------------------------------------------- #

    def receive_event(
        self,
        room_id: str,
        headers: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """One notification from the vendor: identify it, dedupe it, then apply it once.

        The order is the order of the risks.

        1. **The delivery id.** "process each webhook notification once... even when
           PandaDoc retries delivery". A notification with no id cannot be applied once, so
           it is refused before anything is written.
        2. **The duplicate.** A repeat is answered as a duplicate and the kept row's
           ``deliveries`` counter goes up. Nothing else changes, because the point of the
           header is that a retry must not be applied twice.
        3. **The document.** The vendor document id is resolved against this room's rows.
           A notification naming a document this room does not hold is refused rather than
           matched loosely, because a loose match would attach one agreement's ready event
           to another's trail.
        4. **The state.** The ready event moves a document to ``generating``, which is what
           makes the download route answer 202 with a ``Retry-After`` header. A document
           already ``sealed`` is left where it is: the PDF is immutable once it is in the
           vault, so a second ready event changes nothing.

        The document and state are resolved and validated before the delivery row is
        written, so a refused notification leaves no row at all.
        """
        delivery_id, delivery_source = rules.delivery_id_of(headers, payload)
        event = rules.event_name_of(payload)
        document_key = rules.document_id_of(payload)

        existing = self._delivery(room_id, document_key, delivery_id)
        if existing is not None:
            kept = self.store.update(
                existing["id"],
                {
                    "deliveries": int((existing.get("data") or {}).get("deliveries") or 0) + 1,
                    "last_delivery_at": rules.stamp(self._now()),
                },
                actor=actor,
                source=source,
            )
            report = self.project_delivery(kept)
            report.update({"outcome": vocab.OUTCOME_DUPLICATE, "detail": VOCAB_DUPLICATE_DETAIL})
            return report

        if not document_key:
            raise rules.SubscriptionInvalid(
                "The notification names no document, so it cannot be applied.",
                {"document_id": "no document id in the payload; see the four shapes tried."},
            )

        target = self._document_by_vendor_id(room_id, document_key)
        if target is None:
            raise rules.DocumentInvalid(
                "This room holds no executed document for that vendor document id.",
                {"vendor_document_id": f"{document_key} is not recorded in this room."},
            )

        previous_state = (target.get("data") or {}).get("state")
        new_state = previous_state
        if previous_state != vocab.STATE_SEALED:
            new_state = vocab.STATE_GENERATING
            self.set_state(
                target["id"],
                new_state,
                source=source,
                actor=actor or "vendor",
            )

        record = self.store.create(
            vocab.DELIVERY_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                rules.DOCUMENT_REF: target["id"],
                "vendor_document_id": document_key,
                "delivery_id": delivery_id,
                "delivery_id_source": delivery_source,
                "event": event,
                "deliveries": 1,
                "received_at": rules.stamp(self._now()),
                "last_delivery_at": rules.stamp(self._now()),
                "state_before": previous_state,
                "state_after": new_state,
                "applied": True,
            },
            room_id=room_id,
            actor=actor or "vendor",
            source=source,
        )
        report = self.project_delivery(record)
        report.update(
            {
                "outcome": vocab.OUTCOME_RETRIEVED,
                "detail": (
                    f"applied once; {previous_state} to {new_state}"
                    if previous_state != new_state
                    else f"applied once; {new_state} unchanged"
                ),
                "document_id": target["id"],
            }
        )
        return report

    def deliveries(
        self,
        room_id: str | None = None,
        document_ref: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The webhook history, newest first, with each row's delivery count intact.

        Retries are visible rather than collapsed away, because "process each webhook
        notification once... even when PandaDoc retries delivery" is only demonstrably true
        if the repeat can be seen.
        """
        rows: list[dict[str, Any]] = []
        for record in self.store.list(vocab.DELIVERY_COLLECTION, limit=500):
            projected = self.project_delivery(record)
            if room_id and projected["room_id"] != room_id:
                continue
            if document_ref and projected["document_id"] != document_ref:
                continue
            rows.append(projected)
        return rows[: max(1, min(int(limit), 500))]

    # ----------------------------------------------------------------------- #
    # The retrieval
    # ----------------------------------------------------------------------- #

    def retrieve(
        self,
        document_id: str,
        *,
        room_id: str | None = None,
        variant: str = vocab.VARIANT_SEALED,
        environment: str | None = None,
        watermark: str | None = None,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Call the vendor's download endpoint and record the attempt.

        Mirrors ``GET /public/v1/documents/{id}/download-protected`` and the plain
        equivalent. Four outcomes, and the refusal order is the order of the risks:

        1. **The environment gate.** "Production key only... You'll get a 401 Unauthorized
           error when trying to use a Sandbox key." A sealed retrieval in sandbox is
           refused before anything is written, and the refusal names the plain endpoint,
           because the specification says that is what a sandbox caller should use.
        2. **The throttle.** "429 -> ``throttled``", and the specification forbids
           surfacing it as a generic failure. Only retrievals the vendor answered count
           toward the limit, so a client that keeps retrying recovers rather than
           extending the window that refused it.
        3. **The completion check.** A document still awaiting signatures is refused with
           409 and its state, because back-pressure means work in progress and no work is
           in progress. See ``DERIVED_PRE_COMPLETION_REFUSAL``.
        4. **Back-pressure.** A document whose PDF is still generating is recorded as
           ``back_pressure`` with the wait, which is the vendor's documented signal and a
           first-class outcome rather than an error.

        Only the two refusals write an attempt row. A sandbox key and an unfinished
        document produce the same refusal every time, and a log of identical refusals is
        noise rather than evidence.
        """
        record = self._owned(document_id, vocab.DOCUMENT_COLLECTION)
        if record is None:
            raise rules.DocumentNotFound(document_id)
        owning_room = (record.get("data") or {}).get(rules.ROOM_REF) or record.get("room_id") or ""
        if room_id and owning_room != room_id:
            # Room scoping is enforced on every read that names a room, so a caller cannot
            # retrieve one room's executed agreement through another room's route. The
            # refusal is a 404 rather than a 403: from the caller's side the document does
            # not exist in this room, and a 403 would confirm that it exists elsewhere.
            raise rules.DocumentNotFound(document_id)
        room_id = owning_room
        state = (record.get("data") or {}).get("state") or vocab.DEFAULT_DOCUMENT_STATE
        chosen_variant = rules.coerce_variant(variant)
        env = rules.coerce_environment(
            environment
            if environment is not None
            else (record.get("data") or {}).get("environment")
        )

        if chosen_variant == vocab.VARIANT_SEALED and not rules.sealed_allowed(env):
            raise rules.SandboxKeyRejected(vocab.SANDBOX_REMEDY)

        moment = self._now()
        attempts = self._attempts(document_id)
        counted = rules.counted_attempts(attempts, moment)
        if counted >= vocab.THROTTLE_LIMIT:
            wait = rules.retry_after_from_window(attempts, moment)
            raise rules.Throttled(
                f"{counted} retrievals for this document inside {vocab.THROTTLE_WINDOW_SECONDS}s.",
                wait,
            )

        if not rules.artifact_ready(state) and not rules.is_back_pressure(state):
            raise rules.NotCompleted(
                f"This document is {state}; no PDF is being produced.",
                code=f"not_completed_{state}",
                state=state,
            )

        subscription = self._subscription_for(room_id)
        if rules.is_back_pressure(state):
            wait = rules.retry_after_seconds(subscription)
            artifact = self._record_attempt(
                document_id,
                room_id,
                chosen_variant,
                env,
                vocab.OUTCOME_BACK_PRESSURE,
                wait,
                moment,
                source=source,
                actor=actor,
            )
            return {
                "outcome": vocab.OUTCOME_BACK_PRESSURE,
                "status": vocab.STATUS_ACCEPTED,
                "retry_after": wait,
                "retry_after_header": vocab.RETRY_AFTER_HEADER,
                "document_id": document_id,
                "variant": chosen_variant,
                "attempt": artifact,
                "detail": "The signed document file is not ready yet.",
            }

        # The document is sealed, so the file is in the vault and the retrieval is a
        # fetch rather than a wait. `_build_artifact` returns the stored sealed artifact
        # when one exists and builds the bytes when one does not, so asking twice is
        # idempotent for the sealed variant and produces a new file for the plain one.
        artifact = self._build_artifact(document_id, chosen_variant, watermark, source, actor)
        if artifact is None:  # pragma: no cover - the row was resolved a few lines above
            raise rules.DocumentNotFound(document_id)

        attempt = self._record_attempt(
            document_id,
            room_id,
            chosen_variant,
            env,
            vocab.OUTCOME_RETRIEVED,
            None,
            moment,
            source=source,
            actor=actor,
            watermark=watermark,
        )
        return {
            "outcome": vocab.OUTCOME_RETRIEVED,
            "status": vocab.STATUS_READY,
            "document_id": document_id,
            "variant": chosen_variant,
            "artifact": self.project_artifact(artifact),
            "attempt": attempt,
            "detail": "The PDF was returned and recorded with its digest.",
        }

    def serve_protected(self, document_id: str, room_id: str | None = None) -> dict[str, Any]:
        """Stream the stored sealed bytes, or answer the vendor's 202 shape.

        A read, and it writes nothing: no attempt row, no audit row, no state change. The
        vendor's endpoint is the thing being mirrored here, and mirroring it means
        answering 202 with a ``Retry-After`` header and **no body at all** when the PDF is
        still being produced. See ``DERIVED_EMPTY_BODY_ON_202`` for why the room keeps that
        shape rather than improving on it.

        Room scoping is enforced when a room is named, for the same reason it is on
        :meth:`retrieve`: a read that ignores the room in its path would let one room's
        caller fetch another room's executed agreement.

        The returned mapping is a description of a response, not a response: the feature
        module turns it into one. Keeping the status, the headers and the body together in
        the domain is what lets a test assert the shape without a client.
        """
        record = self._owned(document_id, vocab.DOCUMENT_COLLECTION)
        if record is None:
            raise rules.DocumentNotFound(document_id)
        data = record.get("data") or {}
        state = data.get("state") or vocab.DEFAULT_DOCUMENT_STATE
        owning_room = data.get(rules.ROOM_REF) or record.get("room_id") or ""
        if room_id and owning_room != room_id:
            raise rules.DocumentNotFound(document_id)
        subscription = self._subscription_for(owning_room)

        if rules.is_back_pressure(state):
            wait = rules.retry_after_seconds(subscription)
            return {
                "status": vocab.STATUS_ACCEPTED,
                "headers": {vocab.RETRY_AFTER_HEADER: str(wait)},
                "body": b"",
                "media_type": None,
                "outcome": vocab.OUTCOME_BACK_PRESSURE,
                "retry_after": wait,
            }

        if not rules.artifact_ready(state):
            raise rules.NotCompleted(
                f"This document is {state}; no PDF is being produced.",
                code=f"not_completed_{state}",
                state=state,
            )

        artifact = self._artifact(document_id, vocab.VARIANT_SEALED)
        if artifact is None:
            wait = rules.retry_after_seconds(subscription)
            return {
                "status": vocab.STATUS_ACCEPTED,
                "headers": {vocab.RETRY_AFTER_HEADER: str(wait)},
                "body": b"",
                "media_type": None,
                "outcome": vocab.OUTCOME_BACK_PRESSURE,
                "retry_after": wait,
            }

        return {
            "status": vocab.STATUS_READY,
            "headers": {},
            "body": self._artifact_bytes(artifact),
            "media_type": vocab.PDF_MEDIA_TYPE,
            "outcome": vocab.OUTCOME_RETRIEVED,
            "digest": (artifact.get("data") or {}).get("sha256"),
        }

    def read_artifact(self, artifact_id: str, room_id: str | None = None) -> dict[str, Any]:
        """One artifact, by this room's own record id.

        The record rather than the bytes. :meth:`serve_protected` is the route that streams
        bytes, and it answers the vendor's shape; this one answers the question "which
        artifact does this room hold, and what is its digest", which is what an audit reader
        asks and what a page needs in order to label a stored copy without downloading it.

        Room scoping is enforced when a room is named, for the same reason it is on
        :meth:`retrieve`.
        """
        record = self._owned(artifact_id, vocab.ARTIFACT_COLLECTION)
        if record is None:
            raise rules.ArtifactNotFound(artifact_id)
        if room_id and (record.get("data") or {}).get(rules.ROOM_REF) != room_id:
            raise rules.ArtifactNotFound(artifact_id)
        return self.project_artifact(record)

    def attempts(
        self,
        room_id: str | None = None,
        document_ref: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every recorded retrieval, newest first.

        The page reads the most recent row rather than the 202's body, because the vendor
        sends no body with a 202 and this room does not improve on that.
        """
        rows: list[dict[str, Any]] = []
        for record in self.store.list(vocab.ATTEMPT_COLLECTION, limit=500):
            projected = self.project_attempt(record)
            if room_id and projected["room_id"] != room_id:
                continue
            if document_ref and projected["document_id"] != document_ref:
                continue
            rows.append(projected)
        return rows[: max(1, min(int(limit), 500))]

    def artifacts(
        self, room_id: str | None = None, document_ref: str | None = None
    ) -> list[dict[str, Any]]:
        """Every artifact retrieved, newest first."""
        rows: list[dict[str, Any]] = []
        for record in self.store.list(vocab.ARTIFACT_COLLECTION, limit=500):
            projected = self.project_artifact(record)
            if room_id and projected["room_id"] != room_id:
                continue
            if document_ref and projected["document_id"] != document_ref:
                continue
            rows.append(projected)
        return rows

    # ----------------------------------------------------------------------- #
    # The board and the research
    # ----------------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published term, served so a client cannot disagree with the validator."""
        return rules.vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, with the alternative it rejected."""
        described = evault_inferences.describe()
        return {
            "ticket": "WF-080",
            "count": len(described),
            "decisions": described,
            **HONESTY,
        }

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the room's header, and the invariants beside them.

        Counted over this room's own rows, so a header says what happened in that room. The
        four invariants are here because they are the four things a reader looks for and
        does not find on a page: this room does not poll, the sealed endpoint is production
        only, the sealed bytes are byte-stable, and what this room can verify is a digest
        rather than a signature.
        """
        subscriptions = self.subscriptions(room_id)
        documents = self.documents(room_id)
        deliveries = self.deliveries(room_id)
        attempts = self.attempts(room_id)
        artifacts = self.artifacts(room_id)

        states: dict[str, int] = {}
        for row in documents:
            states[row["state"]] = states.get(row["state"], 0) + 1

        outcomes: dict[str, int] = {}
        for row in attempts:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1

        return {
            "ticket": "WF-080",
            "room_id": room_id,
            "subscriptions": len(subscriptions),
            "active_subscriptions": sum(1 for row in subscriptions if row["active"]),
            "documents": len(documents),
            "documents_by_state": states,
            "sealed_documents": sum(1 for row in documents if row["state"] == vocab.STATE_SEALED),
            "generating_documents": sum(
                1 for row in documents if row["state"] == vocab.STATE_GENERATING
            ),
            "deliveries": len(deliveries),
            "retries_deduped": sum(max(0, row["deliveries"] - 1) for row in deliveries),
            "attempts": len(attempts),
            "attempts_by_outcome": outcomes,
            "artifacts": len(artifacts),
            "sealed_artifacts": sum(
                1 for row in artifacts if row["variant"] == vocab.VARIANT_SEALED
            ),
            "watermarked_artifacts": sum(1 for row in artifacts if row.get("watermark")),
            "invariants": {
                "no_polling": vocab.NO_POLLING,
                "sealed_is_production_only": (
                    f"The sealed endpoint answers only in {vocab.SEALED_ENVIRONMENT}."
                ),
                "sealed_is_byte_stable": vocab.VARIANT_TRADEOFF,
                "verification_scope": vocab.SEAL_SCOPE,
            },
            **HONESTY,
        }

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #

    def _owned(self, record_id: str, collection: str) -> dict[str, Any] | None:
        """One live record of this collection, or ``None``.

        The collection is checked as well as the id. A record that exists under another
        workflow's collection is not this workflow's record, and reading across into one
        would be a feature borrowing another feature's schema.
        """
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            return None
        return record

    def _document_by_vendor_id(
        self, room_id: str, vendor_document_id: str
    ) -> dict[str, Any] | None:
        for record in self.store.list(vocab.DOCUMENT_COLLECTION, limit=200):
            data = record.get("data") or {}
            if data.get(rules.ROOM_REF) != room_id:
                continue
            if data.get("vendor_document_id") == vendor_document_id:
                return record
        return None

    def _delivery(
        self, room_id: str, vendor_document_id: str, delivery_id: str
    ) -> dict[str, Any] | None:
        """The kept row for one delivery id in one room, or ``None``.

        Matched on the room and the vendor document id as well as the delivery id. The
        delivery id alone is the vendor's own uniqueness, but two rooms may legitimately be
        sent notifications that share an id - a test harness, an imported feed, or a vendor
        that restarts its counter - and dropping the second one against the first room's
        row would lose a delivery that this room did receive. Scoping the match to the room
        keeps each room's trail complete, which is what the trail is for.
        """
        for record in self.store.list(vocab.DELIVERY_COLLECTION, limit=500):
            data = record.get("data") or {}
            if data.get(rules.ROOM_REF) != room_id:
                continue
            if data.get("delivery_id") != delivery_id:
                continue
            if vendor_document_id and data.get("vendor_document_id") != vendor_document_id:
                continue
            return record
        return None

    def _subscription_for(self, room_id: str) -> dict[str, Any] | None:
        """The room's first active subscription, or ``None``.

        Read for one value only: the ``Retry-After`` seconds a subscription may raise. A
        room with two subscriptions has one answer rather than two, because the back-
        pressure wait belongs to the document's vault and not to which listener heard the
        event.
        """
        active = self.active_subscriptions(room_id)
        return active[0] if active else None

    def _attempts(self, document_id: str) -> list[dict[str, Any]]:
        rows = []
        for record in self.store.list(vocab.ATTEMPT_COLLECTION, limit=500):
            if (record.get("data") or {}).get(rules.DOCUMENT_REF) == document_id:
                rows.append(record.get("data") or {})
        return rows

    def _artifact(self, document_id: str, variant: str) -> dict[str, Any] | None:
        """The stored artifact for this document and variant, or ``None``.

        For the sealed variant the first stored row wins and the later ones are ignored,
        because that variant is byte-stable: if two rows disagreed the invariant has been
        broken and serving either one at random would hide it. For the plain variant the
        most recent row wins, because a watermark is meant to change the bytes.
        """
        matches = [
            record
            for record in self.store.list(vocab.ARTIFACT_COLLECTION, limit=500)
            if (record.get("data") or {}).get(rules.DOCUMENT_REF) == document_id
            and (record.get("data") or {}).get("variant") == variant
        ]
        if not matches:
            return None
        if variant == vocab.VARIANT_SEALED:
            return sorted(matches, key=lambda r: (r.get("created_at") or "", r.get("id")))[0]
        return sorted(matches, key=lambda r: (r.get("created_at") or "", r.get("id")))[-1]

    def _build_artifact(
        self,
        document_id: str,
        variant: str,
        watermark: str | None,
        source: str | None,
        actor: str | None,
    ) -> dict[str, Any] | None:
        """Build and store the artifact for a document whose vault read succeeded.

        The bytes come from :func:`~dsr.security_governance.evault_rules.build_pdf`, which
        is a pure function of the document id, the variant and the watermark. That is what
        makes the sealed variant's byte-stability a property of the code rather than a
        promise, and it is why the sealed variant is stored once and reused: asking for the
        same sealed artifact twice must not write a second row, because a second row would
        suggest the vault can hold two different sealed copies of one agreement.

        Returns the stored record, or ``None`` for the sealed variant when one already
        exists, which the caller reads as "serve the stored one".
        """
        if variant == vocab.VARIANT_SEALED:
            existing = self._artifact(document_id, vocab.VARIANT_SEALED)
            if existing is not None:
                return existing

        record = self._owned(document_id, vocab.DOCUMENT_COLLECTION)
        if record is None:
            raise rules.DocumentNotFound(document_id)
        data = record.get("data") or {}
        room_id = data.get(rules.ROOM_REF) or record.get("room_id") or ""
        vendor_id = data.get("vendor_document_id") or document_id
        mark = rules.coerce_watermark(watermark) if variant == vocab.VARIANT_PLAIN else ""
        if variant == vocab.VARIANT_SEALED and mark:
            # A watermark on the sealed variant is not a smaller mistake, it is the wrong
            # endpoint. Applying it would return bytes that differ between requests, and
            # the specification says the sealed endpoint "always returns the same digitally
            # sealed PDF file".
            mark = ""

        pdf = rules.build_pdf(vendor_id, rules.artifact_document(vendor_id, variant, mark))
        return self.store.create(
            vocab.ARTIFACT_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                rules.DOCUMENT_REF: document_id,
                "vendor_document_id": vendor_id,
                "variant": variant,
                "byte_stable": rules.byte_stable(variant),
                "watermarkable": rules.watermarkable(variant),
                "watermark": mark,
                "media_type": vocab.PDF_MEDIA_TYPE,
                "byte_length": len(pdf),
                "sha256": rules.sha256_hex(pdf),
                "content_base64": _b64(pdf),
                "generated": True,
                "retrieved_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _record_attempt(
        self,
        document_id: str,
        room_id: str,
        variant: str,
        environment: str,
        outcome: str,
        retry_after: int | None,
        moment: datetime,
        *,
        source: str | None,
        actor: str | None,
        watermark: str | None = None,
    ) -> dict[str, Any]:
        """One retrieval, recorded. Always written for the two vendor-answered outcomes."""

        record = self.store.create(
            vocab.ATTEMPT_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                rules.DOCUMENT_REF: document_id,
                "variant": variant,
                "environment": environment,
                "outcome": outcome,
                "vendor_answered": outcome in vocab.VENDOR_ANSWERED_OUTCOMES,
                "retry_after_seconds": retry_after,
                "watermark": rules.coerce_watermark(watermark)
                if variant == vocab.VARIANT_PLAIN
                else "",
                "at": rules.stamp(moment),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project_attempt(record)

    def _artifact_bytes(self, artifact: dict[str, Any]) -> bytes:
        """The stored bytes, decoded.

        Decoded from what was written rather than rebuilt from the arguments, so a caller
        receives exactly the bytes whose digest the room recorded. Rebuilding would be
        equivalent today and would stop being equivalent the day the builder changed.
        """
        return _unb64(str((artifact.get("data") or {}).get("content_base64") or ""))

    # ----------------------------------------------------------------------- #
    # Projections
    #
    # Every projection carries the four honesty fields, so no caller can read a control
    # here without also reading what it is worth. That is the same rule WF-073 applies to
    # its effect and limitation, and it is why they are applied in one place rather than at
    # each route.
    # ----------------------------------------------------------------------- #

    def project_subscription(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "vendor_document_id": data.get("vendor_document_id", ""),
            "triggers": list(data.get("triggers") or []),
            "hears_ready_event": vocab.PDF_READY_TRIGGER in (data.get("triggers") or []),
            "environment": data.get("environment", vocab.ENVIRONMENT_PRODUCTION),
            "active": bool(data.get("active", True)),
            "state": data.get("state", "active"),
            "shared_key": data.get("shared_key", ""),
            "retry_after_seconds": rules.retry_after_seconds(data),
            "created_at": data.get("created_at", ""),
            "cancelled_at": data.get("cancelled_at"),
            "variant": rules.describe_variant(vocab.VARIANT_SEALED),
            **HONESTY,
        }

    def project_document(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        state = data.get("state") or vocab.DEFAULT_DOCUMENT_STATE
        return {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "vendor_document_id": data.get("vendor_document_id", ""),
            "subject": data.get("subject", ""),
            "state": state,
            "state_label": vocab.STATE_LABELS[state],
            "environment": data.get("environment", vocab.ENVIRONMENT_PRODUCTION),
            "artifact_ready": rules.artifact_ready(state),
            "back_pressure": rules.is_back_pressure(state),
            "sealed_environment": vocab.SEALED_ENVIRONMENT,
            "sealed_allowed_here": rules.sealed_allowed(
                data.get("environment", vocab.ENVIRONMENT_PRODUCTION)
            ),
            "created_at": data.get("created_at", ""),
            "updated_state_at": data.get("updated_state_at", ""),
            **HONESTY,
        }

    def document_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """One document with its artifacts, its deliveries and its latest attempt."""
        view = self.project_document(record)
        document_id = str(record.get("id"))
        artifacts = self.artifacts(None, document_id)
        view["artifacts"] = artifacts
        view["sealed_artifact"] = next(
            (row for row in artifacts if row["variant"] == vocab.VARIANT_SEALED), None
        )
        view["plain_artifact"] = next(
            (row for row in artifacts if row["variant"] == vocab.VARIANT_PLAIN), None
        )
        view["deliveries"] = self.deliveries(None, document_id)
        attempts = self.attempts(None, document_id)
        view["attempts"] = attempts
        view["latest_attempt"] = attempts[0] if attempts else None
        return view

    def project_artifact(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        variant = data.get("variant") or vocab.VARIANT_SEALED
        return {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "document_id": data.get(rules.DOCUMENT_REF),
            "vendor_document_id": data.get("vendor_document_id", ""),
            "variant": variant,
            "byte_stable": bool(data.get("byte_stable", rules.byte_stable(variant))),
            "watermarkable": bool(data.get("watermarkable", rules.watermarkable(variant))),
            "watermark": data.get("watermark", ""),
            "media_type": data.get("media_type", vocab.PDF_MEDIA_TYPE),
            "byte_length": data.get("byte_length", 0),
            "sha256": data.get("sha256", ""),
            "generated": bool(data.get("generated", True)),
            "retrieved_at": data.get("retrieved_at", ""),
            "summary": vocab.VARIANT_SUMMARIES[variant],
            "endpoint": vocab.VARIANT_ENDPOINTS[variant],
            **HONESTY,
        }

    def project_delivery(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "document_id": data.get(rules.DOCUMENT_REF),
            "vendor_document_id": data.get("vendor_document_id", ""),
            "delivery_id": data.get("delivery_id", ""),
            "delivery_id_source": data.get("delivery_id_source", ""),
            "event": data.get("event", ""),
            "deliveries": int(data.get("deliveries") or 1),
            "retries": max(0, int(data.get("deliveries") or 1) - 1),
            "state_before": data.get("state_before", ""),
            "state_after": data.get("state_after", ""),
            "applied": bool(data.get("applied", True)),
            "received_at": data.get("received_at", ""),
            "last_delivery_at": data.get("last_delivery_at", ""),
            **HONESTY,
        }

    def project_attempt(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        outcome = data.get("outcome") or vocab.OUTCOME_BACK_PRESSURE
        return {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "room_id": data.get(rules.ROOM_REF) or record.get("room_id"),
            "document_id": data.get(rules.DOCUMENT_REF),
            "variant": data.get("variant", vocab.VARIANT_SEALED),
            "environment": data.get("environment", vocab.ENVIRONMENT_PRODUCTION),
            "outcome": outcome,
            "vendor_answered": bool(data.get("vendor_answered", False)),
            "retry_after_seconds": data.get("retry_after_seconds"),
            "watermark": data.get("watermark", ""),
            "at": data.get("at", ""),
            "summary": vocab.OUTCOME_SUMMARIES[outcome],
            **HONESTY,
        }


#: What a duplicate delivery is reported as, in one sentence. Named so the page and the
#: API cannot describe a retry differently.
VOCAB_DUPLICATE_DETAIL = "The delivery id was already applied; counted once and not applied twice."


def _b64(payload: bytes) -> str:
    """Base64 for the JSON payload.

    The store is schema-flexible JSON, and a PDF's bytes are not JSON. Base64 is the
    smallest honest encoding that survives a round trip through the payload unchanged,
    which matters because the digest is computed over the bytes and the bytes are what
    the download route serves.
    """

    return base64.b64encode(payload).decode("ascii")


def _unb64(payload: str) -> bytes:
    return base64.b64decode(payload.encode("ascii"))
