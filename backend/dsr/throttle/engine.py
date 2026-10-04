"""The workflow itself: connections, buckets, batches, deferrals and the log.

This is the module that writes. Everything it decides comes from the pure modules
beside it - :mod:`dsr.throttle.classify` says what arrived, :mod:`dsr.throttle.
backoff` says how long to wait, :mod:`dsr.throttle.bucket` says whether to send at
all, :mod:`dsr.throttle.keys` says what a retry is called - and this file's only
jobs are to hold the clock, hold the store, and put the answer on a record.

**The clock is injected.** Every method takes ``now`` or reads ``self.clock()``, so
a test can place a batch at a moment and move it an hour without sleeping, and so
the seed's demo rows have a fixed age rather than "about now".

**``source`` is a required keyword on every writing method.** The audit row names
the route that served the write, and a hardcoded string inside a domain method is a
defect this repository has shipped before: a feature's audit log kept recording a
path the app had stopped serving. Making it required turns that into a
``TypeError`` at the call site rather than an untraceable row in production.

**A batch is one record, not a tree.** Everything a reviewer needs to answer "what
happened to this batch" is on the batch: the decision, the signal, the schedule,
the keys, the counters and the log. A second collection per batch would be a second
thing to keep in step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.store import RecordStore
from dsr.throttle import (
    backoff as backoff_module,
    bucket as bucket_module,
    classify as classify_module,
    keys as keys_module,
    policies as policies_module,
    quota as quota_module,
    vocabulary,
)
from dsr.throttle.errors import InvalidPayload, UnknownBatch, UnknownConnection, UnknownRoom
from dsr.throttle.timestamps import iso, parse_instant, plus_seconds

#: A connection's own clock. Read at call time so a caller can move time without
#: rebuilding the engine.
Clock = Callable[[], datetime]


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class ThrottleEngine:
    """Every write this workflow makes, over one store.

    The engine holds nothing but the store handle, the clock and the policy
    registry, which is why the feature module builds one per request from
    ``StoreDep`` rather than caching it on ``app.state`` - that would be the edit
    to a shared file that the feature host exists to make unnecessary.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        clock: Clock | None = None,
        policies: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.store = store
        self.clock: Clock = clock or _clock
        self._policies = dict(policies or {})

    # -- policy ------------------------------------------------------------ #

    def policy_for(self, connection: Mapping[str, Any]) -> dict[str, Any]:
        """The effective policy for a connection: its patch over its vendor's.

        A stored ``policy`` wins over the registered vendor policy, which is what
        makes "adding a vendor means filling in a policy object" true for a
        connection that arrived before the policy did. The vendor comes from the
        record rather than from the stored patch, because the vendor is what a
        connection *is* and the patch is only its numbers.
        """
        data = connection.get("data") if isinstance(connection.get("data"), Mapping) else connection
        vendor = str(data.get("vendor") or "")
        override = data.get("policy")
        if isinstance(override, Mapping) and override:
            return policies_module.prepare(
                {**dict(self._policies), "vendor": vendor, **dict(override)}
            )
        return policies_module.get(vendor)

    def describe_policy(self, vendor: str) -> dict[str, Any]:
        """The registered policy with its arithmetic, for ``GET /policies/{vendor}``."""
        return policies_module.describe(vendor)

    def catalog(self) -> dict[str, Any]:
        """Every registered policy and which of them can refuse a call pre-emptively."""
        described = {vendor: policies_module.describe(vendor) for vendor in policies_module.VENDORS}
        return {
            "count": len(described),
            "vendors": described,
            "preemptive": sorted(name for name, row in described.items() if row["preemptive"]),
            "reactive_only": sorted(
                name for name, row in described.items() if not row["preemptive"]
            ),
        }

    # -- connections -------------------------------------------------------- #

    def create_connection(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a connector the throttle will keep a bucket for.

        Takes what the researched step 1 needs: the vendor, and optionally the
        policy fields whose numbers this account actually has. A vendor with no
        registered policy is accepted and recorded ``sourced: false``, because the
        extensibility note is that a new vendor is a policy object away.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(
                f"a connection body must be a JSON object; got {type(payload).__name__}"
            )
        vendor = classify_module.require_vendor(payload.get("vendor"))
        if room_id:
            require_room(self.store, room_id)

        override = payload.get("policy")
        policy = policies_module.prepare({"vendor": vendor, **(dict(override) if override else {})})
        now = self.clock()
        record = self.store.create(
            vocabulary.COLLECTIONS["connection"],
            {
                "vendor": vendor,
                "label": str(payload.get("label") or payload.get("name") or vendor),
                "policy": {name: policy[name] for name in policies_module.PATCHABLE},
                "policy_sourced": policy["sourced"] and policies_module.known(vendor),
                "registered": policies_module.known(vendor),
                "preemptive": bool(policy.get("burst")),
                "paused": bool(payload.get("paused", False)),
                "pause_reason": str(payload.get("pause_reason") or ""),
                "gaps": list(policy.get("gaps") or []),
                "declared_at": iso(now),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        bucket = self.store.create(
            vocabulary.COLLECTIONS["bucket"],
            {
                "connection_id": record["id"],
                "vendor": vendor,
                **bucket_module.new_state(policy, now),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        record = self.store.update(
            record["id"],
            {"bucket_id": bucket["id"]},
            actor=actor,
            source=source,
        )
        return self.connection(record["id"])

    def connections(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every declared connection, each with its live bucket."""
        rows = self.store.list(vocabulary.COLLECTIONS["connection"], room_id=room_id, limit=500)
        return [self._connection_view(row) for row in rows]

    def connection(self, connection_id: str) -> dict[str, Any]:
        """One connection, its effective policy and its bucket."""
        return self._connection_view(require_connection(self.store, connection_id))

    def patch_connection(
        self,
        connection_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Change a connection's policy numbers.

        Merged over the stored policy rather than replacing it, so a patch of one
        field does not silently reset the other seven back to the vendor defaults.
        Unknown fields are refused: a policy patch that quietly ignored
        ``daily_limit`` would leave an operator believing they had raised a cap.
        """
        record = require_connection(self.store, connection_id)
        body = dict(patch.get("policy") if isinstance(patch.get("policy"), Mapping) else patch)
        unknown = [name for name in body if name not in policies_module.PATCHABLE]
        if unknown:
            raise InvalidPayload(
                f"{unknown} is not a policy field. The patchable fields are "
                f"{', '.join(policies_module.PATCHABLE)}, and an unrecognised one would be ignored "
                "in a way that reads as accepted."
            )
        current = dict(record["data"].get("policy") or {})
        vendor = str(record["data"].get("vendor") or "")
        merged = policies_module.prepare({"vendor": vendor, **current, **body})
        updated = self.store.update(
            connection_id,
            {
                "policy": {name: merged[name] for name in policies_module.PATCHABLE},
                "preemptive": bool(merged.get("burst")),
                "gaps": list(merged.get("gaps") or record["data"].get("gaps") or []),
            },
            actor=actor,
            source=source,
        )
        self._sync_bucket(updated, actor=actor, source=source)
        return self._connection_view(updated)

    def set_paused(
        self,
        connection_id: str,
        paused: bool,
        *,
        reason: str = "",
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Pause or resume a connection. The researched step 5 control.

        Pausing is a refusal to send, not a refusal to accept: a paused connection
        still answers ``GET /connections/{id}`` and still shows what is left, so
        the person who pressed the button can see the state they created.
        """
        require_connection(self.store, connection_id)
        updated = self.store.update(
            connection_id,
            {
                "paused": bool(paused),
                "pause_reason": str(reason) if paused and reason else "",
                "paused_at": iso(self.clock()) if paused else None,
            },
            actor=actor,
            source=source,
        )
        self._sync_bucket(updated, actor=actor, source=source)
        return self._connection_view(updated)

    def _sync_bucket(
        self, connection: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Keep the stored bucket's shape in step with the connection's policy.

        The tokens are carried over rather than reset. A patch to the daily cap
        must not hand a throttled connection a full bucket, and a resume must not
        either.
        """
        policy = self.policy_for(connection)
        room_id = connection.get("room_id")
        bucket_id = str(connection["data"].get("bucket_id") or "")
        if not bucket_id:
            created = self.store.create(
                vocabulary.COLLECTIONS["bucket"],
                {
                    "connection_id": connection["id"],
                    "vendor": connection["data"].get("vendor"),
                    **bucket_module.new_state(policy, self.clock()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            self.store.update(
                connection["id"],
                {"bucket_id": created["id"]},
                actor=actor,
                source=source,
            )
            return created

        existing = self.store.get(bucket_id)
        if existing is None:
            return self.store.create(
                vocabulary.COLLECTIONS["bucket"],
                {
                    "connection_id": connection["id"],
                    "vendor": connection["data"].get("vendor"),
                    **bucket_module.new_state(policy, self.clock()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        state = {key: existing["data"].get(key) for key in existing["data"]}
        return self.store.update(
            bucket_id,
            bucket_module.refill(state, policy, self.clock()),
            actor=actor,
            source=source,
        )

    def _connection_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        connection_id = str(record["id"])
        policy = self.policy_for(record)
        bucket = self._bucket_for(record)
        state = bucket_module.refill(
            {key: bucket.get(key) for key in bucket} if bucket else {}, policy, self.clock()
        )
        return {
            "id": connection_id,
            "room_id": record.get("room_id"),
            "vendor": record["data"].get("vendor"),
            "label": record["data"].get("label"),
            "paused": bool(record["data"].get("paused")),
            "pause_reason": record["data"].get("pause_reason"),
            "paused_at": record["data"].get("paused_at"),
            "registered": bool(record["data"].get("registered")),
            "policy_sourced": bool(record["data"].get("policy_sourced")),
            "policy": policies_module.describe(str(record["data"].get("vendor") or ""))
            | {key: policy.get(key) for key in policies_module.PATCHABLE},
            "effective": {
                "burst": policy.get("burst"),
                "sustained": policy.get("sustained"),
                "window_seconds": policy.get("window_seconds"),
                "daily": policy.get("daily"),
                "preemptive": bool(policy.get("burst")),
                "tokens_per_second": round(bucket_module.refill_rate(policy), 6),
            },
            "bucket": state,
            "gaps": list(record["data"].get("gaps") or []),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _persist_bucket(
        self,
        connection: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Write the bucket back to the connection's own bucket record.

        Every token the room spends and every refill it credits goes through here,
        so the stored bucket is the decision that was made rather than something
        recomputed later from a policy that may since have been patched.
        """
        bucket_id = str(connection["data"].get("bucket_id") or "")
        if bucket_id and self.store.get(bucket_id) is not None:
            return self.store.update(bucket_id, dict(state), actor=actor, source=source)
        return self._sync_bucket(connection, actor=actor, source=source)

    def _bucket_for(self, connection: Mapping[str, Any]) -> dict[str, Any]:
        """The stored bucket for a connection, by its own pointer or by its key.

        Two lookups because a connection can pre-date its bucket record - a
        connection created before this workflow, or one whose bucket row was
        deleted by an operator - and a throttle engine that raised in that case
        would take a page down with it.
        """
        bucket_id = str(connection["data"].get("bucket_id") or "")
        if bucket_id:
            found = self.store.get(bucket_id)
            if found is not None:
                return dict(found["data"])
        rows = self.store.find(
            vocabulary.COLLECTIONS["bucket"], {"connection_id": str(connection["id"])}, limit=1
        )
        return dict(rows[0]["data"]) if rows else {}

    # -- submitting a batch --------------------------------------------------- #

    def submit(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Step 1: ask the bucket whether this batch may go out now.

        Returns ``proceeding`` or ``deferred``, and never blocks the caller: a
        throttle is a scheduling answer, not an error. A batch the bucket refuses
        is written with its schedule, its keys and the reason, so the queue can
        pick it up later without anybody re-deriving the decision.

        The cost is the number of *vendor calls* the batch makes, which is not
        always its row count: a connection whose transport batches twenty rows
        into one call spends one token, and a policy patch's ``calls_per_batch``
        is how a room says so.
        """
        require_room(self.store, room_id)
        connection_id = str(payload.get("connection_id") or "").strip()
        if not connection_id:
            raise InvalidPayload(
                "connection_id is required: the token bucket is per-connector, so a batch cannot "
                "be sized against a budget nobody named"
            )
        connection = require_connection(self.store, connection_id, room_id=room_id)
        rows = _rows_of(payload)
        policy = self.policy_for(connection)
        now = self.clock()

        object_name = str(payload.get("object_name") or "")
        keyed = keys_module.for_rows(
            rows,
            vendor=str(connection["data"].get("vendor") or ""),
            connection_id=connection_id,
            object_name=object_name,
            room_id=room_id,
        )
        cost = _cost_of(policy, payload, len(rows))

        bucket = self._bucket_for(connection)
        state = bucket_module.refill(bucket, policy, now)
        gate = bucket_module.decide(
            state | {"paused": bool(connection["data"].get("paused"))}, policy, now, cost=cost
        )

        attempt = 1
        if gate["allowed"]:
            state = bucket_module.spend(state, policy, now, cost=cost)
            decision = "proceed"
            schedule = {"seconds": 0, "source": "bucket", "at": iso(now), "basis": gate["detail"]}
        else:
            schedule = backoff_module.schedule(
                attempt,
                headers=None,
                now=now,
                seed=_seed_of(connection_id, keyed),
                cap=int(policy.get("retry_after_cap_seconds") or 0)
                or classify_module.RETRY_AFTER_CAP_SECONDS,
                lock_floor_seconds=int(policy.get("lock_floor_seconds") or 0),
            )
            if schedule["seconds"] is None:
                decision = "needs_action"
            else:
                decision = "deferred"

        record = self.store.create(
            vocabulary.COLLECTIONS["batch"],
            {
                "connection_id": connection_id,
                "vendor": connection["data"].get("vendor"),
                "object_name": object_name,
                "state": "proceeding" if decision == "proceed" else decision,
                "decision": decision,
                "decision_reason": gate["reason"],
                "decision_detail": gate["detail"],
                "attempt": attempt,
                "attempts": attempt if decision == "proceed" else 0,
                "rows": len(rows),
                "keys": keyed,
                "cost": cost,
                "bucket": state,
                "schedule": schedule,
                "signal": None,
                "quota": None,
                "counters": {
                    "rows": len(rows),
                    "attempts": 1 if decision == "proceed" else 0,
                    "deferrals": 0,
                },
                "submitted_at": iso(now),
                "next_attempt_at": schedule["at"],
                "completed_at": None,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._persist_bucket(connection, state, actor=actor, source=source)
        self.log(
            room_id,
            record["id"],
            f"batch_submitted_{decision}",
            gate["detail"],
            actor=actor,
            source=source,
        )
        return self.batch(room_id, str(record["id"]))

    # -- observing what the vendor answered ----------------------------------- #

    def observe(
        self,
        room_id: str,
        batch_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 2 and 3: read the answer, spend the tokens, decide, schedule.

        One call carries the whole response: the status, the vendor's error code,
        its headers and whether the rows were accepted. Three things happen, in
        this order, and the order is the design:

        1. **The headers are read first** and written to the room's quota meter.
           They are the vendor's own count of what is left, and reading them after
           the decision would mean a decision made without them.
        2. **The tokens are spent.** A refused call still came off the limit.
        3. **The signal is classified** and, if it is one, the batch is deferred
           under the wait the vendor or the ladder asks for - keeping the keys it
           was first given.
        """
        record = require_batch(self.store, room_id, batch_id)
        if not isinstance(payload, Mapping):
            raise InvalidPayload(
                f"a response body must be a JSON object; got {type(payload).__name__}"
            )

        connection = require_connection(self.store, str(record["data"]["connection_id"]))
        policy = self.policy_for(connection)
        now = self.clock()
        status = _status_of(payload)
        code = str(payload.get("code") or payload.get("error_code") or "")
        headers = payload.get("headers") if isinstance(payload.get("headers"), Mapping) else {}
        vendor = str(connection["data"].get("vendor") or "")

        reading = quota_module.read(vendor, headers)
        self._write_meter(room_id, connection, reading, payload, actor=actor, source=source)

        bucket = self._bucket_for(connection)
        state = bucket_module.spend(bucket, policy, now, cost=int(record["data"].get("cost") or 1))
        self._persist_bucket(connection, state, actor=actor, source=source)

        signal = classify_module.signal_for(vendor, status, code, headers=headers)
        accepted = bool(payload.get("accepted"))
        data = dict(record["data"])

        if signal is None and (accepted or (status is not None and 200 <= int(status) < 300)):
            data["state"] = "complete"
            data["completed_at"] = iso(now)
            data["next_attempt_at"] = None
            data["counters"] = {**dict(data.get("counters") or {}), "accepted": accepted}
            self.log(
                room_id,
                batch_id,
                "batch_complete",
                "the vendor accepted the batch",
                actor=actor,
                source=source,
            )
        elif signal is None:
            data["state"] = "needs_action"
            data["signal"] = None
            self.log(
                room_id,
                batch_id,
                "batch_needs_action",
                f"the vendor answered {status} with no throttle signal this build recognises, so the "
                "room will not retry it on its own",
                actor=actor,
                source=source,
            )
        else:
            attempts = int(data.get("attempt") or 1)
            schedule = backoff_module.schedule(
                attempts,
                signal=signal,
                headers=headers,
                now=now,
                seed=_seed_of(connection["id"], data.get("keys") or []),
                cap=int(policy.get("retry_after_cap_seconds") or 0)
                or classify_module.RETRY_AFTER_CAP_SECONDS,
                lock_floor_seconds=int(policy.get("lock_floor_seconds") or 0),
            )
            exhausted = not backoff_module.check_attempts(attempts)
            data["signal"] = signal.as_dict()
            data["schedule"] = schedule
            data["attempt"] = attempts + 1
            data["state"] = (
                "needs_action" if schedule["seconds"] is None or exhausted else "deferred"
            )
            data["next_attempt_at"] = schedule["at"]
            data["counters"] = {
                **dict(data.get("counters") or {}),
                "deferrals": int((data.get("counters") or {}).get("deferrals") or 0) + 1,
            }
            self.log(
                room_id,
                batch_id,
                f"batch_deferred_{signal.kind}",
                schedule["basis"],
                actor=actor,
                source=source,
                extra={
                    "signal": signal.id,
                    "status": status,
                    "code": code or None,
                    "wait_seconds": schedule["seconds"],
                    "wait_source": schedule["source"],
                },
            )

        data["last_status"] = status
        data["last_code"] = code or None
        data["quota"] = reading
        data["bucket"] = state
        updated = self.store.update(batch_id, data, actor=actor, source=source)
        return {"batch": self._batch_view(updated), "quota": reading, "bucket": state}

    # -- retrying ------------------------------------------------------------- #

    def retry(
        self,
        room_id: str,
        batch_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Send it again, under the keys it was first given.

        This is the researched promise, so it is worth stating exactly what it
        does: the keys are read from the batch and **not** regenerated, so a row
        the vendor already wrote is updated rather than duplicated. A caller that
        supplies different keys is refused, because "retry with the same
        idempotency key" and "retry with a new key" are opposites.

        A manual retry is not capped by the attempt bound. The bound governs the
        automatic drain; refusing a person's explicit action because a counter ran
        out would make the researched waiting state a dead end.
        """
        record = require_batch(self.store, room_id, batch_id)
        if record["data"].get("state") == "complete":
            raise InvalidPayload(
                f"batch {batch_id} is complete: the vendor accepted every row, so a retry under the "
                "same keys would re-write rows that are already there"
            )

        supplied = (payload or {}).get("keys")
        if supplied is not None:
            wanted = [entry.get("idempotency_key") for entry in (record["data"].get("keys") or [])]
            offered = [
                entry.get("idempotency_key") if isinstance(entry, Mapping) else entry
                for entry in (supplied if isinstance(supplied, list) else [supplied])
            ]
            if offered != wanted:
                raise InvalidPayload(
                    "a retry must carry the keys the batch was first given, because the whole point "
                    "of the retry is that the vendor recognises the row. Refusing rather than "
                    "regenerating them: a fresh key is a duplicate write."
                )

        connection = require_connection(self.store, str(record["data"]["connection_id"]))
        policy = self.policy_for(connection)
        now = self.clock()
        data = dict(record["data"])
        cost = int(data.get("cost") or 1)

        bucket = self._bucket_for(connection)
        state = bucket_module.refill(bucket, policy, now)
        gate = bucket_module.decide(
            state | {"paused": bool(connection["data"].get("paused"))}, policy, now, cost=cost
        )
        forced = bool((payload or {}).get("force"))
        due = parse_instant(data.get("next_attempt_at"))
        early = bool(due and due > now)

        if (not gate["allowed"] and not forced) or (early and not forced):
            data["state"] = "deferred"
            data["schedule"] = {
                **dict(data.get("schedule") or {}),
                "seconds": gate["retry_in_seconds"]
                if not gate["allowed"]
                else max(1, int((due - now).total_seconds())),
                "source": "bucket" if not gate["allowed"] else "schedule",
                "basis": gate["detail"]
                if not gate["allowed"]
                else f"the batch is scheduled for {due.isoformat(timespec='seconds')}, which is "
                f"{int((due - now).total_seconds())}s away",
            }
            data["next_attempt_at"] = (
                iso(plus_seconds(now, data["schedule"]["seconds"]))
                if data["schedule"]["seconds"] is not None
                else None
            )
            self.log(
                room_id,
                batch_id,
                "retry_refused",
                data["schedule"]["basis"],
                actor=actor,
                source=source,
            )
            updated = self.store.update(batch_id, data, actor=actor, source=source)
            return {"sent": False, "batch": self._batch_view(updated)}

        state = bucket_module.spend(state, policy, now, cost=cost)
        self._persist_bucket(connection, state, actor=actor, source=source)
        attempts = int(data.get("attempt") or 1)
        data["state"] = "retrying"
        data["attempt"] = attempts + 1
        data["attempts"] = int(data.get("attempts") or 0) + 1
        data["bucket"] = state
        data["next_attempt_at"] = None
        data["last_retried_at"] = iso(now)
        data["counters"] = {**dict(data.get("counters") or {}), "retries": attempts}
        updated = self.store.update(batch_id, data, actor=actor, source=source)
        self.log(
            room_id,
            batch_id,
            "batch_retry_scheduled",
            f"attempt {attempts + 1} with the same "
            f"{len(data.get('keys') or [])} idempotency key(s) this batch was first given",
            actor=actor,
            source=source,
            extra={"keys_reused": True, "attempt": attempts + 1},
        )
        return {"sent": True, "batch": self._batch_view(updated)}

    def drain(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The queue worker's route: retry everything that is due.

        The research says the throttle "runs inside the queue worker", and this
        repository already has one from WF-045. This is the route it calls. It
        retries only what is due - a batch whose wait has not elapsed is left
        alone and reported as ``scheduled``, because sending one early is the
        immediate retry into a rate limit that produces a second refusal.

        Bounded by ``limit`` because a route a scheduler calls on an interval
        should not be able to walk a year of backlog in one call.
        """
        require_room(self.store, room_id)
        now = self.clock()
        limit = max(1, min(int((payload or {}).get("limit") or 20), 200))
        batch_id = str((payload or {}).get("batch_id") or "")

        candidates = (
            [require_batch(self.store, room_id, batch_id)]
            if batch_id
            else self.store.list(vocabulary.COLLECTIONS["batch"], room_id=room_id, limit=500)
        )
        due: list[str] = []
        scheduled: list[str] = []
        blocked: list[dict[str, Any]] = []
        for record in candidates:
            state = str(record["data"].get("state") or "")
            if state not in ("deferred", "needs_action", "retrying"):
                continue
            due_at = parse_instant(record["data"].get("next_attempt_at"))
            if state != "needs_action" and due_at and due_at > now:
                scheduled.append(str(record["id"]))
                continue
            outcome = self.retry(room_id, str(record["id"]), actor=actor, source=source)
            if outcome["sent"]:
                due.append(str(record["id"]))
            else:
                blocked.append(
                    {"id": str(record["id"]), "reason": outcome["batch"]["decision_reason"]}
                )

        return {
            "at": iso(now),
            "room_id": room_id,
            "limit": limit,
            "retried": due[:limit],
            "retried_count": min(len(due), limit),
            "scheduled": scheduled[:limit],
            "blocked": blocked[:limit],
            "keys_reused": True,
        }

    # -- the meter and the log -------------------------------------------------- #

    def quota(self, room_id: str) -> dict[str, Any]:
        """Step 2 read back: what each connection last reported, and what is left.

        Every number here is either from the vendor's own headers or from the
        room's own bucket. Nothing is an estimate presented as a reading, and a
        half the vendor did not send is ``known: false`` rather than zero.
        """
        require_room(self.store, room_id)
        meters = self.store.list(vocabulary.COLLECTIONS["meter"], room_id=room_id, limit=500)
        latest: dict[str, dict[str, Any]] = {}
        for meter in meters:
            connection_id = str(meter["data"].get("connection_id") or "")
            previous = latest.get(connection_id)
            if previous is None or str(meter["updated_at"]) >= str(previous["updated_at"]):
                latest[connection_id] = meter
        rows = [self._meter_view(record) for record in latest.values()]
        return {
            "room_id": room_id,
            "count": len(rows),
            "meters": rows,
            "note": vocabulary.TRANSPORT_NOTE,
        }

    def _write_meter(
        self,
        room_id: str,
        connection: Mapping[str, Any],
        reading: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        return self.store.create(
            vocabulary.COLLECTIONS["meter"],
            {
                "connection_id": connection["id"],
                "vendor": connection["data"].get("vendor"),
                "observed_at": iso(self.clock()),
                "status": _status_of(payload),
                "known": bool(reading.get("known")),
                "daily": dict(reading.get("daily") or {}),
                "window": dict(reading.get("window") or {}),
                "raw": reading.get("raw"),
                "source_header": reading.get("source"),
                "gap": reading.get("gap"),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _meter_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record["data"]
        daily = dict(data.get("daily") or {})
        window = dict(data.get("window") or {})
        return {
            "connection_id": data.get("connection_id"),
            "vendor": data.get("vendor"),
            "observed_at": data.get("observed_at"),
            "status": data.get("status"),
            "known": bool(data.get("known")),
            "daily_remaining": daily.get("remaining"),
            "daily_total": daily.get("total"),
            "window_remaining": window.get("remaining"),
            "window_total": window.get("total"),
            "window_seconds": window.get("window_seconds"),
            "source_header": data.get("source_header"),
            "gap": data.get("gap"),
        }

    def log(
        self,
        room_id: str,
        batch_id: str | None,
        event: str,
        detail: str,
        *,
        actor: str | None = None,
        source: str,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One line of the throttle log: what happened, and why.

        A log an operator reads to answer "why is this batch sitting there" is a
        story, and the story is in the ``basis`` - which is the vendor's sentence
        or the room's own arithmetic, never a bare status code.
        """
        return self.store.create(
            vocabulary.COLLECTIONS["log"],
            {
                "batch_id": batch_id,
                "event": event,
                "detail": detail,
                "at": iso(self.clock()),
                **dict(extra or {}),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def events(
        self,
        *,
        room_id: str | None = None,
        batch_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """The throttle log, oldest first.

        Oldest first on purpose: a queue an operator reads to find out what
        happened is a story, and a story told backwards is not one.

        **The order comes from the store, not from a sort here.** The store orders
        by ``created_at`` ascending and breaks a tie on ``rowid``, which is the
        true insertion sequence. This module's clock is seconds-precision, so
        every line of one deferral ties - and a re-sort on ``(at, id)`` would then
        order a single decision by two random uuid4 values. That is not a
        hypothetical: an earlier draft sorted here and returned
        ``[deferred, submitted]`` for one batch, which reads as though the batch
        was deferred before anyone sent it.
        """
        rows = self.store.list(
            vocabulary.COLLECTIONS["log"],
            room_id=room_id,
            limit=max(1, min(int(limit), 1000)),
            order_by="created_at",
            descending=False,
        )
        return [
            {"id": row["id"], "room_id": row.get("room_id"), **row["data"]}
            for row in rows
            if batch_id is None or str(row["data"].get("batch_id") or "") == str(batch_id)
        ]

    def batches(
        self,
        *,
        room_id: str | None = None,
        state: str | None = None,
        connection_id: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """This room's batches, newest first, filterable on any JSON path.

        The room is required rather than optional even though the collection can
        be listed across rooms: a caller that named a room and got an empty list
        back has no way to tell a room that never throttled from a room that does
        not exist, so the lookup happens first.
        """
        if room_id:
            require_room(self.store, room_id)
        rows = self.store.list(
            vocabulary.COLLECTIONS["batch"], room_id=room_id, limit=max(1, min(int(limit), 500))
        )
        found = []
        for record in rows:
            data = record["data"]
            if state and str(data.get("state") or "") != str(state):
                continue
            if connection_id and str(data.get("connection_id") or "") != str(connection_id):
                continue
            if where and not _matches(data, where):
                continue
            found.append(self._batch_view(record))
        return found

    def batch(self, room_id: str, batch_id: str) -> dict[str, Any]:
        """One batch, with its log, which is the thing a reviewer actually reads."""
        record = require_batch(self.store, room_id, batch_id)
        view = self._batch_view(record)
        view["log"] = self.events(room_id=room_id, batch_id=batch_id)
        return view

    def _batch_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record["data"])
        state = str(data.get("state") or "")
        counters = dict(data.get("counters") or {})
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "collection": vocabulary.COLLECTIONS["batch"],
            "vendor": data.get("vendor"),
            "connection_id": data.get("connection_id"),
            "object_name": data.get("object_name"),
            "state": state,
            "terminal": state not in vocabulary.LIVE_STATES,
            "decision": data.get("decision"),
            "decision_reason": data.get("decision_reason"),
            "decision_detail": data.get("decision_detail"),
            "attempt": data.get("attempt"),
            "attempts": data.get("attempts"),
            "max_attempts": backoff_module.MAX_ATTEMPTS,
            "rows": data.get("rows"),
            "cost": data.get("cost"),
            "keys": data.get("keys") or [],
            "keys_reused": int(counters.get("retries") or 0) > 0,
            "signal": data.get("signal"),
            "schedule": data.get("schedule"),
            "quota": data.get("quota"),
            "bucket": data.get("bucket"),
            "counters": counters,
            "last_status": data.get("last_status"),
            "last_code": data.get("last_code"),
            "submitted_at": data.get("submitted_at"),
            "last_retried_at": data.get("last_retried_at"),
            "next_attempt_at": data.get("next_attempt_at"),
            "completed_at": data.get("completed_at"),
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over this room's own batches.

        The room is required when one is named. A room-scoped read that answered
        with zeros for a room that does not exist would look identical to a room
        that has simply never throttled anything, and those are different problems.
        """
        if room_id:
            require_room(self.store, room_id)
        rows = self.batches(room_id=room_id, limit=500)
        counts = {state: 0 for state in vocabulary.BATCH_STATES}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        deferred = [row for row in rows if row["state"] == "deferred"]
        waits = [
            row["schedule"]["seconds"]
            for row in deferred
            if (row.get("schedule") or {}).get("seconds")
        ]
        connections = self.connections(room_id=room_id)
        return {
            "room_id": room_id,
            "batches": len(rows),
            "counts": counts,
            "deferred": len(deferred),
            "needs_action": counts.get("needs_action", 0),
            "longest_wait_seconds": max(waits) if waits else None,
            "connections": len(connections),
            "paused": sum(1 for row in connections if row["paused"]),
            "preemptive": sum(1 for row in connections if row["effective"]["preemptive"]),
            "kinds": sorted(
                {str((row.get("signal") or {}).get("kind")) for row in rows if row.get("signal")}
            ),
        }


# --------------------------------------------------------------------------- #
# Lookup helpers. Each raises this package's own error so the feature module can
# map the whole hierarchy with one handler.
# --------------------------------------------------------------------------- #


def require_room(store: RecordStore, room_id: str) -> dict[str, Any]:
    """The room a room-scoped route is acting on, or 404."""
    if not str(room_id or "").strip():
        raise UnknownRoom(str(room_id or ""))
    record = store.get(str(room_id))
    if record is None or record["collection"] != "room":
        raise UnknownRoom(str(room_id))
    return record


def require_connection(
    store: RecordStore, connection_id: str, *, room_id: str | None = None
) -> dict[str, Any]:
    """The connection, or 404 - and 404 when it belongs to another room.

    A room-scoped route that would act on another room's connection is refused
    rather than served, which is the same rule every other room-scoped feature in
    this product applies.
    """
    record = store.get(str(connection_id or ""))
    if record is None or record["collection"] != vocabulary.COLLECTIONS["connection"]:
        raise UnknownConnection(str(connection_id or ""))
    if room_id is not None and record.get("room_id") not in (None, "", room_id):
        raise UnknownConnection(str(connection_id))
    return record


def require_batch(store: RecordStore, room_id: str, batch_id: str) -> dict[str, Any]:
    """The batch, on this room, or 404."""
    record = store.get(str(batch_id or ""))
    if record is None or record["collection"] != vocabulary.COLLECTIONS["batch"]:
        raise UnknownBatch(str(batch_id or ""))
    if record.get("room_id") not in (None, "", str(room_id)):
        raise UnknownBatch(str(batch_id))
    return record


def _rows_of(payload: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    rows = payload.get("rows")
    if rows is None:
        rows = payload.get("items")
    if rows is None:
        return []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise InvalidPayload(f"rows must be a list; got {type(rows).__name__}")
    return rows


def _cost_of(policy: Mapping[str, Any], payload: Mapping[str, Any], row_count: int) -> int:
    """How many tokens a batch of this shape costs.

    A caller may say outright with ``calls``. Otherwise a policy that declares
    ``calls_per_batch`` uses it, because a transport that batches twenty rows into
    one call spends one token and charging it twenty would make the bucket empty
    twenty times too fast. With neither, one token per row.
    """
    declared = payload.get("calls")
    if declared not in (None, ""):
        try:
            return max(1, int(declared))
        except (TypeError, ValueError):
            raise InvalidPayload(
                f"calls must be a whole number of vendor calls; got {declared!r}"
            ) from None
    per_batch = policy.get("calls_per_batch")
    if per_batch:
        return max(1, int(per_batch))
    return max(1, int(row_count))


def _status_of(payload: Mapping[str, Any]) -> int | None:
    status = payload.get("status")
    if status in (None, ""):
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        raise InvalidPayload(f"the vendor status must be a number; got {status!r}") from None


def _seed_of(connection_id: Any, keys: Sequence[Mapping[str, Any]]) -> str:
    """The jitter seed: the batch's keys, so the wait is a property of the batch."""
    return "|".join(str(entry.get("idempotency_key") or "") for entry in keys) or str(connection_id)


def _matches(data: Mapping[str, Any], where: Mapping[str, Any]) -> bool:
    """Dotted-path filter over a stored payload."""
    for path, expected in where.items():
        cursor: Any = data
        for part in str(path).split("."):
            if not isinstance(cursor, Mapping) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        if isinstance(expected, bool):
            if bool(cursor) is not expected:
                return False
        elif cursor != expected:
            return False
    return True


__all__ = ["ThrottleEngine", "require_room", "require_connection", "require_batch"]
