"""The qualification-capture engine: the only module in this package that reads
or writes.

Every write goes through :class:`~dsr.store.RecordStore` and its
:class:`~dsr.db.audited.AuditedDatabase`. A write that must produce more than one
row - a submission, its captured events and its routing decision - runs inside one
``transaction()`` block, so the set cannot half-succeed.

Two vocabularies are read from the store rather than restated here: a room from
:mod:`dsr.room_lifecycle`, and the buyer's visitor session from
:mod:`dsr.access`, which is WF-015's identity gate. This module mints neither.

The clock is injected. Production passes the wall clock; a test passes a frozen
function, so no test depends on today's date.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.qualification_capture import rules, vocabulary as v
from dsr.qualification_capture.errors import (
    CaptureConflict,
    CaptureNotFound,
)
from dsr.store import RecordStore

Clock = Callable[[], datetime]


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


class QualificationEngine:
    """Forms, buyer submissions, the qualification band, and the routing rule."""

    def __init__(self, store: RecordStore, *, now: Clock | None = None) -> None:
        self.store = store
        self._now = now or _wall_clock

    # -- clock -------------------------------------------------------------- #

    def now_ms(self) -> int:
        return rules.parse_timestamp(self._now(), "now") or 0

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        """Every closed word, scale and rule this workflow enforces."""

        return v.published_vocabulary()

    # -- rooms and visitors, reused from the other workflows ---------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        """The room a form or a submission is bound to.

        The room vocabulary is :mod:`dsr.room_lifecycle`'s; this reads the record
        and asks that module what state it is in, rather than deciding here what
        "active" means.
        """

        record = self.store.get(room_id)
        if record is None or record.get("collection") != v.ROOM_COLLECTION:
            raise CaptureNotFound(room_id, collection="room")
        return record

    def _room_state(self, room_id: str) -> str:
        return v.status_of(self.require_room(room_id))

    def resolve_visitor(self, room_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """The buyer's identity session, from WF-015's store, or ``None``.

        A form inside a room is already behind the room's access gate, so a
        submission may arrive anonymously. When it does name a session, the
        session must belong to this room and be in one of the two states the gate
        grants on, otherwise the capture is refused rather than attributed to
        nobody.
        """

        raw = payload.get(v.VISITOR_SESSION_REF) or payload.get("session_id")
        session_ref = str(raw).strip() if raw else ""
        if not session_ref:
            return None
        record = self.store.get(session_ref)
        if record is None or record.get("collection") != v.VISITOR_SESSION_COLLECTION:
            raise CaptureNotFound(session_ref, collection="visitor_session")
        if record.get("room_id") not in (None, room_id):
            raise CaptureConflict(
                "visitor_session_room_mismatch",
                "the visitor session belongs to a different room",
            )
        state = str(record["data"].get("status") or "").strip().lower()
        if state not in v.CAPTURABLE_VISITOR_STATES:
            raise CaptureConflict(
                "visitor_not_identified",
                f"the visitor session is {state or 'unknown'}, not identified or verified",
            )
        identity = record["data"]
        return {
            "session_ref": record["id"],
            "name": identity.get("name"),
            "email": identity.get("email"),
        }

    # -- forms: reads ------------------------------------------------------- #

    def require_form(self, form_id: str) -> dict[str, Any]:
        record = self.store.get(form_id)
        if record is None or record.get("collection") != v.FORM_COLLECTION:
            raise CaptureNotFound(form_id, collection="form")
        return record

    def get_form(self, form_id: str) -> dict[str, Any]:
        record = self.require_form(form_id)
        return self._form_view(record)

    def list_forms(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if status:
            where["status"] = status
        if where:
            records = self.store.find(v.FORM_COLLECTION, where, limit=limit)
        else:
            records = self.store.list(v.FORM_COLLECTION, limit=limit, room_id=room_id)
        if room_id:
            records = [r for r in records if r.get("room_id") == room_id]
        return [self._form_view(record) for record in records]

    def room_form(self, room_id: str) -> dict[str, Any]:
        """The live form a buyer is shown inside a room, or a 404.

        The buyer's view: it carries the questions and the option labels and no
        qualification configuration a seller would not want a buyer to read.
        """

        self.require_room(room_id)
        records = self.store.find(v.FORM_COLLECTION, {"status": v.FORM_STATUS_LIVE}, limit=50)
        live = [r for r in records if r.get("room_id") == room_id]
        if not live:
            raise CaptureNotFound(room_id, collection="live_form")
        live.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return self._buyer_form_view(live[0])

    # -- forms: writes ------------------------------------------------------ #

    def create_form(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        fields = rules.normalise_form_payload(payload)
        self.require_room(room_id)
        data = self._form_data(fields, room_id)
        data["created_at_ms"] = self.now_ms()
        record = self.store.create(
            v.FORM_COLLECTION,
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._form_view(record)

    def update_form(
        self,
        form_id: str,
        patch: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        record = self.require_form(form_id)
        current = record["data"]
        merged = {
            "name": patch.get("name", current.get("name")),
            "intro": patch.get("intro", current.get("intro")),
            "dimensions": patch.get("dimensions", current.get("dimensions")),
            "required_dimensions": patch.get(
                "required_dimensions", current.get("required_dimensions")
            ),
            "routing": patch.get("routing", current.get("routing")),
            "status": current.get("status"),
            "deal_ref": patch.get("deal_ref", current.get("deal_ref")),
            "account_ref": patch.get("account_ref", current.get("account_ref")),
        }
        fields = rules.normalise_form_payload(merged)
        room_id = record.get("room_id") or current.get(v.ROOM_REF)
        data = self._form_data(fields, str(room_id))
        data["created_at_ms"] = current.get("created_at_ms")
        data["updated_at_ms"] = self.now_ms()
        updated = self.store.update(form_id, data, actor=actor, source=source)
        return self._form_view(updated)

    def publish_form(
        self, form_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Move a form to live. Only an active room may carry a live form."""

        record = self.require_form(form_id)
        room_id = record.get("room_id") or record["data"].get(v.ROOM_REF)
        if room_id and self._room_state(str(room_id)) != v.ACTIVE:
            raise CaptureConflict(
                "room_not_active",
                "a form can only go live on an active room",
                extra={"room_state": v.status_of(self.require_room(str(room_id)))},
            )
        if record["data"].get("status") == v.FORM_STATUS_LIVE:
            raise CaptureConflict("already_live", "this form is already live")
        updated = self.store.update(
            form_id,
            {"status": v.FORM_STATUS_LIVE, "updated_at_ms": self.now_ms()},
            actor=actor,
            source=source,
        )
        return self._form_view(updated)

    def soft_delete_form(
        self, form_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        record = self.require_form(form_id)
        self.store.delete(form_id, actor=actor, source=source)
        view = self._form_view(record)
        view["deleted"] = True
        return view

    def restore_form(
        self, form_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        return self._form_view(self.store.restore(form_id, actor=actor, source=source))

    # -- submissions: reads ------------------------------------------------- #

    def require_submission(self, submission_id: str) -> dict[str, Any]:
        record = self.store.get(submission_id)
        if record is None or record.get("collection") != v.SUBMISSION_COLLECTION:
            raise CaptureNotFound(submission_id, collection="submission")
        return record

    def get_submission(self, submission_id: str) -> dict[str, Any]:
        return self.seller_view(self.require_submission(submission_id))

    def list_submissions(
        self,
        *,
        room_id: str | None = None,
        form_id: str | None = None,
        band: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if form_id:
            where["form_id"] = form_id
        if band:
            where["band"] = band
        if status:
            where["status"] = status
        if where:
            records = self.store.find(v.SUBMISSION_COLLECTION, where, limit=limit)
        else:
            records = self.store.list(v.SUBMISSION_COLLECTION, limit=limit, room_id=room_id)
        if room_id:
            records = [r for r in records if r.get("room_id") == room_id]
        return [self.seller_view(record) for record in records]

    def events(self, submission_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.find(v.EVENT_COLLECTION, {"submission_id": submission_id}, limit=limit)
        # ``find`` returns newest-first, ordered by ``updated_at DESC, rowid DESC``.
        # A submission writes its events inside one transaction, so they all share
        # a millisecond and the ``created_at`` sort below cannot separate them;
        # only the rowid tie-break carries insertion order. Reversing first turns
        # that tie-break ascending, and the stable sort then preserves it, so
        # "oldest first" means the order the events were actually written.
        records.reverse()
        records.sort(key=lambda r: r.get("created_at") or "")
        return [self._event_view(record) for record in records]

    def routes(self, *, room_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        records = self.store.list(v.ROUTE_COLLECTION, limit=limit, room_id=room_id)
        if room_id:
            records = [r for r in records if r.get("room_id") == room_id]
        return [self._route_view(record) for record in records]

    def qualification_summary(self, room_id: str) -> dict[str, Any]:
        """The seller-only summary the rep reads next to the content analytics."""

        self.require_room(room_id)
        submissions = self.list_submissions(room_id=room_id, limit=1000)
        submitted = [s for s in submissions if s["status"] == v.SUBMISSION_STATUS_SUBMITTED]
        by_band = {band: 0 for band in v.BANDS}
        for entry in submitted:
            if entry.get("band") in by_band:
                by_band[entry["band"]] += 1
        routing: dict[str, int] = {}
        for entry in submitted:
            target = entry.get("route_target")
            if target:
                routing[target] = routing.get(target, 0) + 1
        latest = submitted[0] if submitted else None
        return {
            "room_id": room_id,
            "room_ref": room_id,
            "seller_only": True,
            "visibility": v.VISIBILITY_SELLER_ONLY,
            "forms": len(self.list_forms(room_id=room_id, limit=1000)),
            "submissions": len(submitted),
            "in_progress": len(
                [s for s in submissions if s["status"] == v.SUBMISSION_STATUS_DRAFT]
            ),
            "by_band": by_band,
            "qualified": by_band.get(v.BAND_QUALIFIED, 0),
            "routing": routing,
            "latest": latest,
            "note": v.BUYER_VISIBLE_NOTE,
        }

    # -- submissions: writes ------------------------------------------------ #

    def autosave(
        self,
        room_id: str,
        form_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Save a buyer's partial answers without leaving the room.

        The research names "auto-save of answers", so a partial answer is a
        first-class state: it creates or updates one draft submission per
        visitor, and it never scores, because a half-finished ladder is not a
        qualification.
        """

        form = self._require_live_form(room_id, form_id)
        answers = rules.normalise_answers(form, payload.get("answers"), require_all=False)
        visitor = self.resolve_visitor(room_id, payload)
        now = self.now_ms()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            record = self._upsert_submission(
                tx,
                form=form,
                room_id=room_id,
                answers=answers,
                status=v.SUBMISSION_STATUS_DRAFT,
                qualification=None,
                visitor=visitor,
                payload=payload,
                now=now,
                actor=actor,
                source=source,
            )
            self._write_event(tx, record["id"], v.EVENT_AUTOSAVED, now, actor, source=source)
        view = self.seller_view(record)
        view["events"] = self.events(record["id"])
        return view

    def submit(
        self,
        room_id: str,
        form_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Complete a form: score it, attach it to the deal, and route it.

        One transaction writes the submission, its lifecycle events and its
        routing decision, so a scored submission with no route cannot exist.
        """

        form = self._require_live_form(room_id, form_id)
        answers = rules.normalise_answers(form, payload.get("answers"), require_all=True)
        qualification = rules.qualify(form, answers)
        visitor = self.resolve_visitor(room_id, payload)
        now = self.now_ms()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            record = self._upsert_submission(
                tx,
                form=form,
                room_id=room_id,
                answers=answers,
                status=v.SUBMISSION_STATUS_SUBMITTED,
                qualification=qualification,
                visitor=visitor,
                payload=payload,
                now=now,
                actor=actor,
                source=source,
            )
            self._write_event(tx, record["id"], v.EVENT_SUBMITTED, now, actor, source=source)
            self._write_event(
                tx,
                record["id"],
                v.EVENT_QUALIFIED,
                now,
                actor,
                source=source,
                extra={"score": qualification["score"], "band": qualification["band"]},
            )
            self._write_route(tx, record, room_id, qualification, now, actor, source=source)
        view = self.seller_view(record)
        view["events"] = self.events(record["id"])
        return view

    def route_submission(
        self, submission_id: str, *, source: str, actor: str | None = None
    ) -> dict[str, Any]:
        """Re-run the routing rule on a submission's band."""

        record = self.require_submission(submission_id)
        if record["data"].get("status") != v.SUBMISSION_STATUS_SUBMITTED:
            raise CaptureConflict("not_submitted", "only a submitted answer can be routed")
        band = str(record["data"].get("band") or v.BAND_UNQUALIFIED)
        target = rules.routing_target(band, self._form_or_none(record["data"].get("form_id")))
        now = self.now_ms()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            updated = tx.update(
                submission_id,
                {"route_target": target, "routed": True, "routed_at_ms": now},
                actor=actor,
                source=source,
            )
            self._write_route(
                tx,
                updated,
                str(updated.get("room_id") or ""),
                {"band": band, "score": updated["data"].get("score"), "route_target": target},
                now,
                actor,
                source=source,
            )
        view = self.seller_view(updated)
        view["events"] = self.events(submission_id)
        return view

    # -- projections -------------------------------------------------------- #

    def seller_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The seller's view: the answers, the score, the band and the route."""

        data = dict(record["data"])
        view = {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "room_ref": record.get("room_id") or data.get(v.ROOM_REF),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            **data,
            "band_label": v.BAND_LABELS.get(str(data.get("band")), data.get("band")),
            "status_label": v.SUBMISSION_STATUS_LABELS.get(
                str(data.get("status")), data.get("status")
            ),
            "visibility": v.VISIBILITY_SELLER_ONLY,
        }
        return view

    def buyer_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The buyer's view: the same submission with every seller-only field gone."""

        # Accept either a raw record (``data`` nested) or a flattened seller view
        # (``data`` merged in). The routes hand this a seller view, while a test
        # may hand it a raw record, and both must strip the same fields.
        nested = record.get("data")
        data = dict(nested) if isinstance(nested, Mapping) else dict(record)
        view = {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "room_ref": record.get("room_id") or data.get(v.ROOM_REF),
            "form_id": data.get("form_id"),
            "status": data.get("status"),
            "status_label": v.SUBMISSION_STATUS_LABELS.get(
                str(data.get("status")), data.get("status")
            ),
            "answers": dict(data.get("answers") or {}),
            "missing": list(data.get("missing") or []),
            "submitted_at": data.get("submitted_at"),
            "updated_at": data.get("updated_at"),
            "visibility": v.VISIBILITY_BUYER,
            "note": v.BUYER_VISIBLE_NOTE,
        }
        for hidden in v.SELLER_ONLY_FIELDS:
            view.pop(hidden, None)
        return view

    # -- internals ---------------------------------------------------------- #

    def _require_live_form(self, room_id: str, form_id: str) -> dict[str, Any]:
        form = self.require_form(form_id)
        if form.get("room_id") != room_id:
            raise CaptureNotFound(form_id, collection="form")
        if not rules.form_is_live(form):
            raise CaptureConflict("form_not_live", "this form is not live")
        return form

    def _form_or_none(self, form_id: Any) -> dict[str, Any] | None:
        if not form_id:
            return None
        record = self.store.get(str(form_id))
        if record is None or record.get("collection") != v.FORM_COLLECTION:
            return None
        return record

    def _form_data(self, fields: Mapping[str, Any], room_id: str) -> dict[str, Any]:
        return {
            "name": fields["name"],
            "intro": fields["intro"],
            "dimensions": list(fields["dimensions"]),
            "required_dimensions": list(fields["required_dimensions"]),
            "routing": dict(fields["routing"]),
            "status": fields["status"],
            v.ROOM_REF: room_id,
            v.DEAL_REF: fields["deal_ref"],
            v.ACCOUNT_REF: fields["account_ref"],
            "created_at_ms": None,
            "updated_at_ms": None,
        }

    def _submission_data(
        self,
        *,
        form: Mapping[str, Any],
        room_id: str,
        answers: Mapping[str, str],
        status: str,
        qualification: Mapping[str, Any] | None,
        visitor: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
        now: int,
    ) -> dict[str, Any]:
        form_data = form["data"]
        identity = visitor or {}
        session_ref = identity.get("session_ref")
        # A visitor key distinguishes two buyers in one room. A named WF-015
        # session is the strongest key; failing that, a client token from the
        # rendered widget keeps a returning anonymous buyer on their own draft.
        visitor_key = (
            session_ref or str(payload.get("submission_token") or "").strip() or "anonymous"
        )
        contact_ref = (
            payload.get(v.CONTACT_REF) or identity.get("email") or form_data.get(v.ACCOUNT_REF)
        )
        data: dict[str, Any] = {
            "form_id": form["id"],
            "form_name": form_data.get("name"),
            v.ROOM_REF: room_id,
            v.DEAL_REF: payload.get(v.DEAL_REF) or form_data.get(v.DEAL_REF),
            v.ACCOUNT_REF: payload.get(v.ACCOUNT_REF) or form_data.get(v.ACCOUNT_REF),
            v.CONTACT_REF: contact_ref,
            v.VISITOR_SESSION_REF: session_ref,
            "visitor_key": visitor_key,
            "answers": dict(answers),
            "status": status,
            "score": None,
            "max_score": None,
            "band": None,
            "route_target": None,
            "routed": False,
            "routed_at_ms": None,
            "missing": rules.missing_required(form, answers),
            "submitted_at": None,
            "updated_at_ms": now,
        }
        if status == v.SUBMISSION_STATUS_SUBMITTED and qualification is not None:
            data.update(
                {
                    "score": qualification["score"],
                    "max_score": qualification["max_score"],
                    "band": qualification["band"],
                    "route_target": qualification["route_target"],
                    "submitted_at": rules.to_iso(now),
                    "missing": list(qualification["missing"]),
                }
            )
        return data

    def _upsert_submission(
        self,
        writer: Any,
        *,
        form: Mapping[str, Any],
        room_id: str,
        answers: Mapping[str, str],
        status: str,
        qualification: Mapping[str, Any] | None,
        visitor: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
        now: int,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        data = self._submission_data(
            form=form,
            room_id=room_id,
            answers=answers,
            status=status,
            qualification=qualification,
            visitor=visitor,
            payload=payload,
            now=now,
        )
        existing = self._find_draft(form["id"], data["visitor_key"])
        if existing is not None and status == v.SUBMISSION_STATUS_DRAFT:
            return writer.update(existing["id"], data, actor=actor, source=source)
        if existing is not None:
            # A buyer who submits after autosaving reuses their draft row rather
            # than leaving an orphan behind.
            return writer.update(existing["id"], data, actor=actor, source=source)
        return writer.create(
            v.SUBMISSION_COLLECTION,
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _find_draft(self, form_id: str, visitor_key: str) -> dict[str, Any] | None:
        drafts = self.store.find(
            v.SUBMISSION_COLLECTION,
            {"form_id": form_id, "visitor_key": visitor_key},
            limit=1,
        )
        return drafts[0] if drafts else None

    def _write_event(
        self,
        writer: Any,
        submission_id: str,
        event_type: str,
        at_ms: int,
        actor: str | None,
        *,
        source: str,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return writer.create(
            v.EVENT_COLLECTION,
            {
                "submission_id": submission_id,
                "event": event_type,
                "at_ms": at_ms,
                "at": rules.to_iso(at_ms),
                **(dict(extra) if extra else {}),
            },
            actor=actor,
            source=source,
        )

    def _write_route(
        self,
        writer: Any,
        record: Mapping[str, Any],
        room_id: str,
        qualification: Mapping[str, Any],
        at_ms: int,
        actor: str | None,
        *,
        source: str,
    ) -> dict[str, Any]:
        band = str(qualification.get("band") or v.BAND_UNQUALIFIED)
        target = str(
            qualification.get("route_target") or v.ROUTE_TARGET_BY_BAND.get(band, v.TARGET_NURTURE)
        )
        route = writer.create(
            v.ROUTE_COLLECTION,
            {
                "submission_id": record["id"],
                "form_id": record["data"].get("form_id"),
                v.ROOM_REF: room_id,
                "band": band,
                "score": qualification.get("score"),
                "target": target,
                "routed_at_ms": at_ms,
                "routed_at": rules.to_iso(at_ms),
            },
            room_id=room_id or None,
            actor=actor,
            source=source,
        )
        self._write_event(
            writer,
            record["id"],
            v.EVENT_ROUTED,
            at_ms,
            actor,
            source=source,
            extra={"target": target},
        )
        return route

    # -- projections of the other record shapes ----------------------------- #

    def _form_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        room_id = record.get("room_id")
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": room_id,
            "room_ref": room_id or data.get(v.ROOM_REF),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            **data,
            "status_label": v.FORM_STATUS_LABELS.get(str(data.get("status")), data.get("status")),
            "live": rules.form_is_live(record),
            "respondents": self.store.db.count_where(
                v.SUBMISSION_COLLECTION, {"form_id": record["id"]}
            ),
        }

    def _buyer_form_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """What a buyer sees: the questions, the options, and nothing scored."""

        form = self._form_view(record)
        dimensions = rules.form_dimensions(record)
        required = set(rules.required_dimensions(record))
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "room_ref": record.get("room_id") or record["data"].get(v.ROOM_REF),
            "name": record["data"].get("name"),
            "intro": record["data"].get("intro"),
            "dimensions": dimensions,
            "questions": [
                {
                    "id": dimension,
                    "label": v.DIMENSION_LABELS[dimension],
                    "question": v.DIMENSION_QUESTIONS[dimension],
                    "required": dimension in required,
                    "options": [
                        {
                            "value": option,
                            "label": v.ANSWER_LABELS[dimension][option],
                        }
                        for option in v.ANSWER_OPTIONS[dimension]
                    ],
                }
                for dimension in dimensions
            ],
            "visibility": v.VISIBILITY_BUYER,
            "answers_are_visible_to_the_seller": True,
            "seller_only_note": v.BUYER_VISIBLE_NOTE,
            "form_status": form.get("status"),
        }

    def _route_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        return {
            "id": record["id"],
            "submission_id": data.get("submission_id"),
            "form_id": data.get("form_id"),
            "room_id": record.get("room_id"),
            "band": data.get("band"),
            "band_label": v.BAND_LABELS.get(str(data.get("band")), data.get("band")),
            "score": data.get("score"),
            "target": data.get("target"),
            "routed_at": data.get("routed_at"),
            "created_at": record.get("created_at"),
        }

    def _event_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        return {
            "id": record["id"],
            "submission_id": data.get("submission_id"),
            "event": data.get("event"),
            "score": data.get("score"),
            "band": data.get("band"),
            "target": data.get("target"),
            "at_ms": data.get("at_ms"),
            "at": data.get("at"),
            "created_at": record.get("created_at"),
        }


__all__ = ["QualificationEngine"]
