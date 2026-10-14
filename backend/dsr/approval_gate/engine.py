"""WF-119's service over the schema-flexible record store.

The only module in ``dsr.approval_gate`` that touches a store. It reads and
writes through :class:`dsr.store.RecordStore`, so every write lands in
:class:`dsr.db.audited.AuditedDatabase` and gets its audit row in the same
transaction. The caller passes the audit ``source``, which the HTTP layer builds
from the route that actually served the request, so the audit row always names a
route the app serves.

Where the state lives, and why
------------------------------
A hold is a row. The pause happens in one process and the human decides in an
external system at an unbounded later time, so keeping the hold in memory would
lose it on the restart the issue warns about. ``records.data`` is the only place
durable enough, and storing it there is also what keeps every write audited.

The rules themselves live in :mod:`dsr.approval_gate.rules` and take plain data
and an explicit moment. This module is the part that persists rows and freezes
nothing: a caller that wants a deterministic answer passes ``now``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.store import RecordStore

from . import rules, vocabulary as vocab
from .errors import (
    BranchUnknown,
    EnvelopeInvalid,
    GateInvalid,
    GateNotFound,
    HoldConflict,
    HoldExpired,
    HoldNotFound,
    KeyTaken,
    OutcomeConflict,
    PauseDisabled,
    ResumeConflict,
)

GATE_IMMUTABLE = ("key",)
HOLD_IMMUTABLE = ("gate_id", "gate_key", vocab.ENVELOPE_EXTRAS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten a stored record into its payload, keeping the record envelope.

    Every record this project stores nests its fields under ``data``, so a read
    that skips this returns an envelope and every field lookup silently misses.
    The envelope fields a caller needs to address the row are merged back in.
    """
    if not record:
        return {}
    data = dict(record.get("data") or {})
    for field in ("id", "collection", "revision", "created_at", "updated_at", "deleted_at"):
        data[field] = record.get(field)
    return data


def _new_token() -> str:
    return uuid.uuid4().hex[: vocab.TOKEN_BYTES * 2]


class ApprovalGate:
    """Configure a pause node, hold a contact at it, and resume on a decision."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        self._now = now or _now

    # ------------------------------------------------------------------ #
    # The clock
    # ------------------------------------------------------------------ #

    def moment(self) -> datetime:
        """The current moment, as an aware UTC datetime."""
        value = self._now() if callable(self._now) else self._now
        return rules.parse_time(value) or _now()

    def _iso_now(self) -> str:
        return rules.to_iso(self.moment())

    # ------------------------------------------------------------------ #
    # Gates
    # ------------------------------------------------------------------ #

    def _gate_record(self, key: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        rows = self.store.find(vocab.GATES, {"key": key}, limit=1, include_deleted=include_deleted)
        return rows[0] if rows else None

    def _gate_record_by_token(self, token: str) -> dict[str, Any] | None:
        rows = self.store.find(vocab.GATES, {"token": token}, limit=1)
        return rows[0] if rows else None

    def gate_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A gate record plus the URLs the app serves for it."""
        data = _payload(record)
        gate_key = data.get("key")
        token = data.get("token")
        branches = rules.branch_map(data)
        return {
            **data,
            "branches": branches,
            "hold_seconds": rules.hold_seconds(data),
            "pause_execution": bool(data.get(vocab.PAUSE_EXECUTION_KEY)),
            "action_url": str(data.get("action_url") or f"{vocab.PREFIX}/gates/{gate_key}/pause"),
            "resume_urls": {
                outcome: f"{vocab.PREFIX}/hooks/{token}/{outcome}" for outcome in vocab.OUTCOMES
            },
        }

    def require_gate(self, key: str) -> dict[str, Any]:
        record = self._gate_record(key)
        if record is None:
            raise GateNotFound(f"no approval gate with key {key!r}")
        return record

    def require_gate_by_token(self, token: str) -> dict[str, Any]:
        record = self._gate_record_by_token(token)
        if record is None:
            raise GateNotFound(f"no approval gate with resume token {token!r}")
        return record

    def create_gate(
        self,
        *,
        key: Any,
        workflow_id: Any,
        action_key: Any,
        actor: str,
        source: str,
        name: Any = None,
        pause_execution: Any = None,
        branches: Any = None,
        hold_seconds: Any = None,
        action_url: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Configure a pause node.

        The pause toggle defaults off, which is the shape the vendor's own
        builder has: the workflow holds the contact only once Pause Execution is
        turned on.
        """
        if isinstance(key, str) and self._gate_record(key, include_deleted=True):
            raise KeyTaken(f"a gate with key {key!r} already exists")
        candidate: dict[str, Any] = {
            "key": key,
            "workflow_id": workflow_id,
            "action_key": action_key,
            "name": name,
            vocab.PAUSE_EXECUTION_KEY: False if pause_execution is None else pause_execution,
            vocab.BRANCHES_KEY: dict(branches) if isinstance(branches, Mapping) else None,
            "hold_seconds": hold_seconds,
            "action_url": action_url,
            "token": _new_token(),
            "created_at": self._iso_now(),
            **dict(extra or {}),
        }
        problems = rules.gate_errors(candidate)
        if problems:
            raise GateInvalid(problems)
        record = self.store.create(vocab.GATES, candidate, actor=actor, source=source)
        return self.gate_view(record)

    def revise_gate(
        self, key: str, patch: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Edit a gate's pause toggle, branches, window or metadata."""
        record = self.require_gate(key)
        current = dict(record.get("data") or {})
        for field in GATE_IMMUTABLE:
            if field in patch and patch[field] != current.get(field):
                raise GateInvalid([f"the {field} is immutable and cannot be changed"])
        candidate = {**current, **dict(patch)}
        problems = rules.gate_errors(candidate)
        if problems:
            raise GateInvalid(problems)
        updated = self.store.update(str(record["id"]), candidate, actor=actor, source=source)
        return self.gate_view(updated)

    def list_gates(self) -> list[dict[str, Any]]:
        return [self.gate_view(record) for record in self.store.list(vocab.GATES, limit=100)]

    # ------------------------------------------------------------------ #
    # Holds
    # ------------------------------------------------------------------ #

    def _holds_for_extras(
        self, extras: Mapping[str, Any], *, state: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = rules.extras_where(extras)
        if state is not None:
            where["state"] = state
        rows = self.store.find(vocab.HOLDS, where, limit=100)
        return [row for row in rows if rules.extras_match(row, extras)]

    def hold_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A hold record plus the two facts a caller has to derive from it."""
        data = _payload(record)
        moment = self.moment()
        return {
            **data,
            "state": data.get("state") or vocab.STATE_HELD,
            "expired": rules.is_expired(record, moment),
            "expires_in_seconds": self._seconds_until(data.get("expires_at")),
            "can_resume": data.get("state") == vocab.STATE_HELD
            and not rules.is_expired(record, moment),
        }

    def _seconds_until(self, deadline: Any) -> int | None:
        parsed = rules.parse_time(deadline)
        if parsed is None:
            return None
        return int((parsed - self.moment()).total_seconds())

    def require_hold(self, hold_id: str) -> dict[str, Any]:
        record = self.store.get(hold_id)
        if record is None or record.get("collection") != vocab.HOLDS:
            raise HoldNotFound(f"no hold with id {hold_id!r}")
        return record

    def list_holds(
        self, *, gate_key: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if gate_key:
            where["gate_key"] = gate_key
        if state:
            where["state"] = state
        rows = (
            self.store.find(vocab.HOLDS, where, limit=500)
            if where
            else self.store.list(vocab.HOLDS, limit=500)
        )
        return [self.hold_view(row) for row in rows]

    def events_for(self, hold_id: str) -> list[dict[str, Any]]:
        rows = self.store.find(vocab.EVENTS, {"hold_id": hold_id}, limit=200)
        return [_payload(row) for row in rows]

    def _record_event(
        self, *, hold_id: str, gate_key: str, event: str, actor: str, source: str, **extra: Any
    ) -> dict[str, Any]:
        return self.store.create(
            vocab.EVENTS,
            {
                "hold_id": hold_id,
                "gate_key": gate_key,
                "event": event,
                "at": self._iso_now(),
                **extra,
            },
            actor=actor,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # The pause
    # ------------------------------------------------------------------ #

    def pause(
        self,
        key: str,
        envelope: Mapping[str, Any],
        *,
        actor: str,
        source: str,
        at: Any = None,
    ) -> dict[str, Any]:
        """Hold a contact at the gate, from the action's documented envelope.

        ``at`` is the moment the action fired. The route lets the server stamp
        it; the seeder passes an earlier moment to create a hold that has already
        lapsed, which is one of the states the demo page has to show.
        """
        record = self.require_gate(key)
        gate_data = dict(record.get("data") or {})
        if not gate_data.get(vocab.PAUSE_EXECUTION_KEY):
            raise PauseDisabled(
                f"the gate {key!r} has pause execution off, so no contact is held at it"
            )
        problems = rules.envelope_problems(envelope)
        if problems:
            raise EnvelopeInvalid(problems)
        extras = rules.normalise_extras(dict(envelope.get(vocab.ENVELOPE_EXTRAS) or {}))
        meta = rules.normalise_meta(envelope.get(vocab.ENVELOPE_META))

        for existing in self._holds_for_extras(extras, state=vocab.STATE_HELD):
            if rules.is_expired(existing, self.moment()):
                self._expire(existing, actor=actor, source=source, reason="lapsed")
            else:
                raise HoldConflict(
                    "the contact is already held at this gate; a resume or a lapse releases it"
                )

        paused_at = rules.to_iso(rules.parse_time(at) or self.moment())
        window = rules.hold_seconds(gate_data)
        hold = self.store.create(
            vocab.HOLDS,
            {
                "gate_id": record.get("id"),
                "gate_key": key,
                "workflow_id": gate_data.get("workflow_id"),
                "action_key": gate_data.get("action_key"),
                vocab.ENVELOPE_EXTRAS: extras,
                vocab.ENVELOPE_META: meta,
                vocab.ENVELOPE_DATA: dict(envelope.get(vocab.ENVELOPE_DATA) or {}),
                "state": vocab.STATE_HELD,
                "outcome": None,
                "branch": None,
                "approver": None,
                "paused_at": paused_at,
                "expires_at": rules.expires_at(paused_at, window),
                "resumed_at": None,
                "resume_id": None,
            },
            actor=actor,
            source=source,
        )
        self._record_event(
            hold_id=str(hold["id"]),
            gate_key=key,
            event=vocab.EVENT_PAUSED,
            actor=actor,
            source=source,
            expires_at=hold["data"]["expires_at"],
        )
        return {
            "hold": self.hold_view_or_refetch(hold),
            "held": True,
            "gate_key": key,
            "action_url": self.gate_view(record)["action_url"],
            "resume_urls": self.gate_view(record)["resume_urls"],
            "resume_mode": vocab.RESUME_MODE,
        }

    def hold_view_or_refetch(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Flatten a just-written record, re-reading it so the view is current."""
        fresh = self.store.get(str(record["id"])) if record.get("id") else None
        return self.hold_view(fresh or record)

    # ------------------------------------------------------------------ #
    # The resume
    # ------------------------------------------------------------------ #

    def resume_by_extras(
        self,
        extras: Mapping[str, Any],
        *,
        url_outcome: Any,
        body_outcome: Any = None,
        body_branch: Any = None,
        approver: Any = None,
        source_url: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Resume the hold this correlation key names.

        The key is verified, not trusted: the three extras fields must match a
        stored hold, and a key that matches nothing is refused rather than
        creating a new hold.
        """
        problems = rules.extras_problems(extras)
        if problems:
            raise EnvelopeInvalid(problems)
        rows = self._holds_for_extras(extras)
        if not rows:
            raise HoldNotFound(
                "no held contact matches this correlation key; the extras object "
                "must name a hold this gate raised"
            )
        return self._resume(
            rows[0],
            url_outcome=url_outcome,
            body_outcome=body_outcome,
            body_branch=body_branch,
            approver=approver,
            source_url=source_url,
            actor=actor,
            source=source,
        )

    def resume_hold(
        self,
        hold_id: str,
        *,
        url_outcome: Any,
        body_outcome: Any = None,
        body_branch: Any = None,
        approver: Any = None,
        source_url: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """Decide a hold addressed by its own id, for an in-app decision."""
        return self._resume(
            self.require_hold(hold_id),
            url_outcome=url_outcome,
            body_outcome=body_outcome,
            body_branch=body_branch,
            approver=approver,
            source_url=source_url,
            actor=actor,
            source=source,
        )

    def _resume(
        self,
        record: Mapping[str, Any],
        *,
        url_outcome: Any,
        body_outcome: Any,
        body_branch: Any,
        approver: Any,
        source_url: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        hold = dict(record.get("data") or {})
        gate_record = self.require_gate(str(hold.get("gate_key")))
        gate_data = dict(gate_record.get("data") or {})

        outcome, problem = rules.resolve_outcome(url_outcome, body_outcome)
        if problem:
            raise OutcomeConflict(problem)
        branch, problem = rules.resolve_branch(gate_data, str(outcome), body_branch)
        if problem:
            raise BranchUnknown(problem)

        decision = rules.hold_decision(record, str(outcome), str(branch), self.moment())
        if decision == rules.DECISION_EXPIRED:
            self._expire(record, actor=actor, source=source, reason="resume_after_lapse")
            raise HoldExpired(
                "the hold lapsed before the decision arrived, so the contact is no longer held"
            )
        if decision == rules.DECISION_CONFLICT:
            raise ResumeConflict(
                f"this hold is already {hold.get('outcome')} on branch "
                f"{hold.get('branch')}; a resume cannot change a recorded decision"
            )

        if decision == rules.DECISION_REPLAY:
            resumed = self.store.get(str(record["id"])) or record
            existing = hold.get("resume_id")
            resume = self.store.get(str(existing)) if existing else None
            self._record_event(
                hold_id=str(record["id"]),
                gate_key=str(hold.get("gate_key")),
                event=vocab.EVENT_REPLAY,
                actor=actor,
                source=source,
                outcome=str(outcome),
                branch=str(branch),
            )
            return {
                "hold": self.hold_view(resumed),
                "resume": _payload(resume) if resume else None,
                "outcome": outcome,
                "branch": branch,
                "replay": True,
                "resumed_at": hold.get("resumed_at"),
            }

        moment = self._iso_now()
        approver_name = str(approver).strip() if isinstance(approver, str) else ""
        resume = self.store.create(
            vocab.RESUMES,
            {
                "hold_id": record.get("id"),
                "gate_id": hold.get("gate_id"),
                "gate_key": hold.get("gate_key"),
                "extras": dict(hold.get(vocab.ENVELOPE_EXTRAS) or {}),
                "outcome": str(outcome),
                "branch": str(branch),
                "approver": approver_name or None,
                "source_url": source_url,
                "at": moment,
            },
            actor=actor,
            source=source,
        )
        updated = self.store.update(
            str(record["id"]),
            {
                "state": vocab.STATE_RESUMED,
                "outcome": str(outcome),
                "branch": str(branch),
                "approver": approver_name or None,
                "resumed_at": moment,
                "resume_id": resume.get("id"),
            },
            actor=actor,
            source=source,
        )
        self._record_event(
            hold_id=str(record["id"]),
            gate_key=str(hold.get("gate_key")),
            event=vocab.EVENT_RESUMED,
            actor=actor,
            source=source,
            outcome=str(outcome),
            branch=str(branch),
        )
        return {
            "hold": self.hold_view(updated),
            "resume": _payload(resume),
            "outcome": outcome,
            "branch": branch,
            "replay": False,
            "resumed_at": moment,
        }

    def resume_by_token(
        self,
        token: str,
        url_outcome: str,
        body: Mapping[str, Any],
        *,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """The resume webhook: one of the two URLs the gate published.

        The gate is resolved from the token, so a token that names no gate is a
        404 before anything is read from the body.
        """
        self.require_gate_by_token(token)
        extras = body.get(vocab.ENVELOPE_EXTRAS)
        return self.resume_by_extras(
            dict(extras) if isinstance(extras, Mapping) else {},
            url_outcome=url_outcome,
            body_outcome=body.get("outcome"),
            body_branch=body.get(vocab.BRANCH_KEY),
            approver=body.get("approver"),
            source_url=f"{vocab.PREFIX}/hooks/{token}/{url_outcome}",
            actor=actor,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # Expiry
    # ------------------------------------------------------------------ #

    def _expire(
        self, record: Mapping[str, Any], *, actor: str, source: str, reason: str
    ) -> dict[str, Any]:
        """Mark a lapsed hold expired and record why."""
        hold = dict(record.get("data") or {})
        updated = self.store.update(
            str(record["id"]),
            {"state": vocab.STATE_EXPIRED, "expired_at": self._iso_now(), "expiry_reason": reason},
            actor=actor,
            source=source,
        )
        self._record_event(
            hold_id=str(record["id"]),
            gate_key=str(hold.get("gate_key")),
            event=vocab.EVENT_EXPIRED,
            actor=actor,
            source=source,
            reason=reason,
        )
        return updated

    def sweep(self, *, actor: str, source: str) -> dict[str, Any]:
        """Expire every hold that has lapsed.

        Expiry is also evaluated lazily on every read and every resume, so this
        route is a convenience for the page rather than the only way a hold
        becomes expired. A restart can therefore never lose the fact that a hold
        lapsed.
        """
        lapsed: list[dict[str, Any]] = []
        for record in self.store.find(vocab.HOLDS, {"state": vocab.STATE_HELD}, limit=500):
            if rules.is_expired(record, self.moment()):
                updated = self._expire(record, actor=actor, source=source, reason="swept")
                lapsed.append(self.hold_view(updated))
        return {"expired": len(lapsed), "holds": lapsed}

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def summary(self) -> dict[str, Any]:
        """Counts the page leads with."""
        holds = self.store.list(vocab.HOLDS, limit=1000)
        by_state: dict[str, int] = {state: 0 for state in vocab.STATES}
        expired_lazily = 0
        for record in holds:
            view = self.hold_view(record)
            state = view["state"]
            if state == vocab.STATE_HELD and view["expired"]:
                expired_lazily += 1
                state = vocab.STATE_EXPIRED
            by_state[state] = by_state.get(state, 0) + 1
        gates = self.store.list(vocab.GATES, limit=100)
        return {
            "gates": len(gates),
            "pause_enabled": sum(
                1 for gate in gates if gate["data"].get(vocab.PAUSE_EXECUTION_KEY)
            ),
            "holds": len(holds),
            "by_state": by_state,
            "expired_lazily": expired_lazily,
            "resumes": len(self.store.list(vocab.RESUMES, limit=1000)),
            "resume_mode": vocab.RESUME_MODE,
        }


def seed(db: Any, context: dict[str, Any]) -> str:
    """Demo data for WF-119: the states the approval page has to show.

    Seeded states: a gate with pause execution on and both resume URLs, which is
    the configured hold; a contact still held and waiting on a human; a contact
    that was approved and resumed on the configured branch; and a contact whose
    hold lapsed with no decision, which is the non-success state the issue asks
    the seed to print. A second gate shows the toggle's other position, paused
    off, so the page renders both.

    Every character of the returned string is ASCII, because a single arrow glyph
    in one recovered feature broke the entire Windows seeder.
    """
    store = RecordStore(db)
    engine = ApprovalGate(store)
    now = rules.parse_time(context.get("now")) or _now()
    if engine.list_gates():
        return "already seeded"

    engine.create_gate(
        key="send_proposal",
        workflow_id="wf-demo-proposal-approval",
        action_key="pause_for_approval",
        name="Approve before the proposal is sent",
        pause_execution=True,
        branches={
            vocab.OUTCOME_APPROVED: "branch_proposal_approved",
            vocab.OUTCOME_REFUSED: "branch_proposal_refused",
        },
        hold_seconds=vocab.DEFAULT_HOLD_SECONDS,
        actor="seed",
        source="seed",
    )
    engine.create_gate(
        key="send_invoice",
        workflow_id="wf-demo-invoice-approval",
        action_key="pause_for_approval",
        name="Approve before the invoice is sent",
        pause_execution=False,
        branches={
            vocab.OUTCOME_APPROVED: "branch_invoice_approved",
            vocab.OUTCOME_REFUSED: "branch_invoice_refused",
        },
        actor="seed",
        source="seed",
    )

    def extras(contact: str) -> dict[str, str]:
        return {
            vocab.EXTRAS_LOCATION: "loc_northwind",
            vocab.EXTRAS_CONTACT: contact,
            vocab.EXTRAS_WORKFLOW: "wf-demo-proposal-approval",
        }

    def envelope(contact: str, amount: int, owner: str) -> dict[str, Any]:
        return {
            vocab.ENVELOPE_DATA: {"amount": amount, "owner": owner},
            vocab.ENVELOPE_EXTRAS: extras(contact),
            vocab.ENVELOPE_META: {vocab.META_KEY: "proposal-approval", vocab.META_VERSION: 1},
        }

    engine.pause(
        "send_proposal",
        envelope("contact_waiting", 24000, "dana"),
        actor="seed",
        source="seed",
        at=now,
    )
    engine.pause(
        "send_proposal",
        envelope("contact_approved", 9000, "dana"),
        actor="seed",
        source="seed",
        at=now,
    )
    engine.pause(
        "send_proposal",
        envelope("contact_lapsed", 1500, "ravi"),
        actor="seed",
        source="seed",
        at=now - timedelta(days=7),
    )

    engine.resume_by_extras(
        extras("contact_approved"),
        url_outcome=vocab.SUCCESS_URL_OUTCOME,
        body_branch="branch_proposal_approved",
        approver="dana",
        source_url=f"{vocab.PREFIX}/hooks/seed-contacted-by-an-approver/approved",
        actor="seed",
        source="seed",
    )

    # The third hold was raised a week ago on a 72-hour window, so this sweep
    # lapses it with no decision: the state the issue asks the seed to show.
    swept = engine.sweep(actor="seed", source="seed")

    summary = engine.summary()
    return (
        f"{summary['gates']} approval gates, {summary['pause_enabled']} with pause on, "
        f"1 contact waiting on a human, 1 resumed on the approved branch, "
        f"{swept['expired']} expired with no decision"
    )


__all__ = ["ApprovalGate", "seed"]
