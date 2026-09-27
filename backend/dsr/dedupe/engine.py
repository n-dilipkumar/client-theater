"""The engine: the researched user flow, in order, over the audited store.

Steps 1 to 5 of ``WF-041.md``, and the whole of them:

    1. A new lead/contact arrives from the room.
    2. The connector sends the write with duplicate detection enabled.
    3. The CRM's matching/duplicate rule runs against existing rows and returns
       either a clean create, a duplicate alert with the matching record id, or
       a hard block.
    4. Depending on the admin's configured policy, the connector either (a)
       blocks the write and shows the existing record, (b) updates the existing
       record instead, or (c) creates the duplicate anyway with an
       acknowledgement.
    5. The decision and the matched record id are logged on the room row.

Two design points worth stating outright, because both are constraints rather
than choices:

**``source`` is a required keyword on every write.** The audit row must name the
route that served the write, so the route builds it from ``router.prefix`` and
passes it down. A domain function that hardcoded a URL string would let the
audit log name a path the app had stopped serving - that defect has shipped in
this codebase before, and making the parameter required is what stops it
regressing silently.

**The CRM is a class, not a socket.** :class:`StoreCrm` answers duplicate
queries out of the audited store. The research documents Salesforce, HubSpot and
Dataverse request shapes but no endpoint this product can reach, and the product
has no CRM client; calling a fake HTTP client at a real vendor would be a claim
it cannot back. Everything above this seam is the researched part, so swapping
in a real transport is a change to one class.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from dsr.dedupe.errors import DedupeError
from dsr.dedupe.matching import REGISTRY, Match, MatcherRegistry, decisive, match_rows
from dsr.dedupe.policy import normalise_connection
from dsr.dedupe.rules import (
    BLOCKED,
    CREATED,
    CREATED_DUPLICATE,
    ESCALATED,
    HARD_BLOCKED,
    UPDATED,
    Decision,
    DuplicateResult,
    clean,
    decide,
    matched,
    multiple,
)
from dsr.dedupe.vocabulary import (
    DEFAULT_POLICY,
    DEFAULT_UNIQUE_KEYS,
    DEFAULT_VENDOR,
    DUPLICATE_RULE_HEADER_NAME,
    MATCH_KEYS,
    build_duplicate_rule_header,
    key_spec,
    require_policy,
    require_vendor,
    serialise_duplicate_rule_header,
)
from dsr.db.audited import RecordNotFound
from dsr.store import RecordStore

#: The connection a row belongs to. Distinct from ``crm_*`` because ``crm_record``
#: is the CRM's own table and this is the product's configuration; a reviewer
#: reading the collections list should not have to guess which is which.
CONNECTION_COLLECTION = "crm_dedupe_connection"

#: The rows a duplicate rule matches against. This is the product's stand-in for
#: the CRM's account and contact tables.
RECORD_COLLECTION = "crm_dedupe_record"

#: One decision per inbound row. Room-scoped, because step 5 puts it on the room.
DECISION_COLLECTION = "crm_dedupe_decision"

#: The field on a room row carrying the researched annotation from step 5.
ROOM_FIELD = "dedupe"

#: How many annotations the room row keeps. The decision records are the durable
#: copy; this is the convenience copy a rep reads, and it is capped so a room
#: with a thousand form fills does not grow a thousand-element array.
ROOM_ANNOTATION_LIMIT = 20

#: Fields that are the connector's bookkeeping rather than the CRM record's
#: content, so they are not patched onto a matched record by an `update` policy.
ENGINE_FIELDS = frozenset({"room_id", "synced_from", "duplicate_of", "object_type"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StoreCrm:
    """The CRM seam. Duplicate queries and writes over the audited store.

    Deliberately the only place in the package that knows where rows come from,
    so a real connector replaces this class and nothing else.

    Reads come back as **views**: the row's own fields with the record id merged
    in, rather than the store's envelope. That is what a CRM's own response
    looks like - "return all fields in the duplicate record" - and it means a
    decision records the duplicate's fields, not this product's storage
    envelope. Writes take the envelope, because that is what the store needs.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        #: Every query this instance has answered. The seed and the tests read it
        #: to assert the in-room pre-check really did skip the CRM round trip.
        self.queries: list[dict[str, Any]] = []

    @staticmethod
    def view(record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored record as a CRM-shaped row: its fields, plus its id."""
        data = record.get("data")
        payload = dict(data) if isinstance(data, Mapping) else dict(record)
        payload["id"] = record.get("id")
        payload["room_id"] = record.get("room_id")
        return payload

    def _views(self, records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [self.view(record) for record in records]

    def room_rows(self, room_id: str) -> list[dict[str, Any]]:
        """The rows this room has already synced - the in-room pre-check's scope."""
        self.queries.append({"key": "synced_from", "value": room_id})
        return self._views(self.store.find(RECORD_COLLECTION, {"synced_from": room_id}, limit=1000))

    def all_rows(self) -> list[dict[str, Any]]:
        """Every live row on the connection, which is what the CRM's own rule
        would match against.

        The scan is bounded and it has to be: :func:`matching.match_rows`
        normalises in Python, because a normalisation the index does not know
        about cannot be pushed into SQL. On a very large connection this is the
        part that would need an index-aware rewrite, which is why the CRM sits
        behind this one class.
        """
        self.queries.append({"key": "*", "value": "duplicate_check"})
        return self._views(self.store.list(RECORD_COLLECTION, limit=1000))

    def find(self, match_key: str, value: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """One keyed lookup on the column a unique index would cover.

        A real duplicate rule queries one column. Here the index is the dynamic
        JSON path, so a new key works without a migration. The extra pass exists
        because the index stores the *raw* value: ``"  PRIYA@Example  "`` and
        ``"priya@example"`` are one address to the email normaliser but two
        distinct strings to the index, so the normalised form is re-checked here.
        """
        spec = key_spec(match_key)
        normalised = spec["normalise"](value)
        if not normalised:
            return []
        self.queries.append({"key": match_key, "value": normalised})
        rows = self._views(self.store.find(RECORD_COLLECTION, {match_key: normalised}, limit=limit))
        if rows:
            return rows
        # The index missed, so fall back to a bounded scan that normalises. Only
        # on the miss path; the common case is a single indexed lookup.
        found: list[dict[str, Any]] = []
        for row in self._views(self.store.list(RECORD_COLLECTION, limit=1000)):
            if spec["normalise"](row.get(match_key)) == normalised:
                found.append(row)
                if len(found) >= limit:
                    break
        return found

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.store.get(record_id)

    def create(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None,
    ) -> dict[str, Any]:
        return self.store.create(RECORD_COLLECTION, dict(payload), room_id=room_id, actor=actor, source=source)

    def update(self, record_id: str, patch: Mapping[str, Any], *, actor: str | None, source: str) -> dict[str, Any]:
        return self.store.update(record_id, dict(patch), actor=actor, source=source)


class DedupeEngine:
    """Duplicate detection and blocking, over the audited store.

    Constructed per request from ``StoreDep``, the way :class:`~dsr.crm.CRMSync`
    is in WF-016. The engine holds nothing beyond the store, the CRM seam, the
    matcher registry and a clock, so per-request construction is equivalent and
    leaves every one of those overridable in a test.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        crm: StoreCrm | None = None,
        registry: MatcherRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.crm = crm if crm is not None else StoreCrm(store)
        self.registry = registry if registry is not None else REGISTRY
        self.clock = clock if clock is not None else _utcnow

    # -- connections -------------------------------------------------------- #

    def create_connection(
        self,
        spec: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a connection: the per-connection dedupe policy the research names.

        Validated before a row exists, so a bad policy or an unknown key cannot
        leave a half-configured connection behind.
        """
        payload = normalise_connection(spec)
        payload["enabled"] = bool(dict(spec).get("enabled", True))
        payload["run_as_current_user"] = bool(dict(spec).get("run_as_current_user", False))
        # The room scope goes on the envelope as well as in the payload: the
        # envelope column is what the generic records API and any future indexed
        # room filter would read, and leaving it null would make a room-scoped
        # connection look unscoped to them.
        return self.store.create(
            CONNECTION_COLLECTION, payload, room_id=payload.get("room_id"), actor=actor, source=source
        )

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self.store.get(connection_id)

    def require_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.get_connection(connection_id)
        if record is None:
            raise DedupeError(f"connection {connection_id} not found")
        return record

    def list_connections(
        self,
        *,
        room_id: str | None = None,
        vendor: str | None = None,
        policy: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Live connections, newest first. Filters are JSON paths in the payload."""
        where: dict[str, Any] = {}
        if vendor is not None:
            where["vendor"] = require_vendor(vendor)
        if policy is not None:
            where["policy"] = require_policy(policy)
        records = self.store.find(CONNECTION_COLLECTION, where, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") in (None, room_id)]
        return records

    def update_connection(
        self,
        connection_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Patch a connection, re-validating anything that is not a plain flag.

        A partial patch is merged onto the stored connection and re-run through
        the same validation a create goes through, so a patch cannot leave a
        connection with, say, a unique key that is no longer a configured key.
        """
        current = self.require_connection(connection_id)
        merged = {**current["data"], **dict(patch)}
        payload = normalise_connection(merged)
        for flag in ("enabled", "run_as_current_user"):
            if flag in patch:
                payload[flag] = bool(patch[flag])
        if "enabled" not in payload:
            payload["enabled"] = bool(current["data"].get("enabled", True))
        if "run_as_current_user" not in payload:
            payload["run_as_current_user"] = bool(current["data"].get("run_as_current_user", False))
        return self.store.update(connection_id, payload, actor=actor, source=source)

    def delete_connection(
        self,
        connection_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Soft-delete a connection. Its decisions stay auditable."""
        self.require_connection(connection_id)
        return self.store.delete(connection_id, actor=actor, source=source)

    def default_connection(self) -> dict[str, Any]:
        """The connection an ingest uses when the caller names none.

        Not a stored row: it is the researched default policy and the default
        key set, resolved. Returns a shape with no ``id``, which the engine
        treats as "not persisted" when it logs a decision.
        """
        return {
            "id": None,
            "data": {
                "name": "default",
                "vendor": DEFAULT_VENDOR,
                "policy": DEFAULT_POLICY,
                "keys": [entry["key"] for entry in MATCH_KEYS],
                "unique_keys": list(DEFAULT_UNIQUE_KEYS),
                "min_score": None,
                "run_as_current_user": False,
                "enabled": True,
                "room_id": None,
            },
        }

    def resolve_connection(self, connection_id: str | None) -> dict[str, Any]:
        if not connection_id:
            return self.default_connection()
        return self.require_connection(connection_id)

    # -- the rows a rule matches against ------------------------------------ #

    def create_record(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a row the duplicate rules will match against.

        The stand-in for a row already in the CRM. Seeded by the demo so a
        reviewer can see a real match, and creatable over HTTP so a team can
        populate it from its own import.
        """
        body = dict(payload or {})
        if not any(str(body.get(entry["key"]) or "").strip() for entry in MATCH_KEYS):
            raise DedupeError(
                "a record needs at least one matching key; configured keys are "
                + ", ".join(entry["key"] for entry in MATCH_KEYS)
            )
        body.setdefault("object_type", "contact")
        body.setdefault("synced_from", room_id)
        return self.crm.create(body, actor=actor, source=source, room_id=room_id)

    def list_records(
        self,
        *,
        room_id: str | None = None,
        object_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if room_id is not None:
            where["synced_from"] = room_id
        if object_type is not None:
            where["object_type"] = object_type
        return self.store.find(RECORD_COLLECTION, where, limit=limit)

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        return self.crm.get(record_id)

    # -- evaluation --------------------------------------------------------- #

    def evaluate(
        self,
        inbound: Mapping[str, Any],
        connection: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
    ) -> Decision:
        """Run steps 2 to 4 and return the decision. Writes nothing.

        Two stages, because the research's extensibility note puts matchers
        "in the room *before* calling the CRM":

        1. the in-room pre-check over the rows this room has already synced;
        2. the CRM's own rule over every row.

        The pre-check short-circuits only under a blocking policy, which is the
        one case where the researched behaviour is "do not write". That is where
        "reducing wasted API calls" is actually cashed in, and it is recorded on
        the decision as ``crm_called: false`` so a reviewer can see the call was
        skipped rather than assume it.
        """
        settings = dict((connection or self.default_connection()).get("data") or {})
        policy = require_policy(settings.get("policy"))
        keys = [str(key) for key in (settings.get("keys") or [])] or [
            entry["key"] for entry in MATCH_KEYS
        ]
        unique_keys = {str(key) for key in (settings.get("unique_keys") or [])}
        run_as_current_user = bool(settings.get("run_as_current_user"))
        min_score = settings.get("min_score")
        threshold = float(min_score) if min_score not in (None, "") else None

        # -- stage 1: the in-room pre-check, scoped to this room's own rows.
        local_rows = self.crm.room_rows(room_id) if room_id else []
        local_scope = frozenset(str(row["id"]) for row in local_rows if row.get("id"))
        local_matches, local_by_id = self._query(
            inbound, local_rows, keys, threshold, allowed=local_scope
        )
        if local_matches and policy == "block":
            key, subset = decisive(local_matches, keys)
            result = self._result_for(key, subset, local_by_id, "the in-room check")
            return decide(
                result,
                policy,
                unique_keys=sorted(unique_keys),
                match_key=key,
                crm_called=False,
                run_as_current_user=run_as_current_user,
            )

        # -- stage 2: the CRM's own duplicate rule, over every row.
        all_rows = self.crm.all_rows()
        matches, by_id = self._query(inbound, all_rows, keys, threshold)
        key, subset = decisive(matches, keys)
        result = self._result_for(key, subset, by_id, "the CRM duplicate rule")
        return decide(
            result,
            policy,
            unique_keys=sorted(unique_keys),
            match_key=key,
            crm_called=True,
            run_as_current_user=run_as_current_user,
        )

    def _query(
        self,
        inbound: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        keys: Sequence[str],
        threshold: float | None,
        *,
        allowed: frozenset[str] | None = None,
    ) -> tuple[list[Match], dict[str, dict[str, Any]]]:
        """Match an inbound row against rows, going through the CRM seam.

        Two lookups per configured key: :meth:`StoreCrm.find` for the indexed
        exact hit, then :func:`~dsr.dedupe.matching.match_rows` for the
        registered matchers - the fuzzy ones a third party added, and the
        normalisation the index does not know about.

        The two halves overlap for an exact match, and deduplicating them
        matters: one address found by both an indexed lookup and a matcher is one
        match. Note that the deduplication here is per matcher, so two *different*
        matchers agreeing on one record still produce two entries - the caller
        collapses those by record id, because a multi-match means several records
        and never several matchers.

        ``allowed`` restricts the answer to a set of record ids, and it is what
        makes the in-room pre-check genuinely in-room. :meth:`StoreCrm.find`
        searches the whole connection, so without it a stage-1 query would pick
        up rows belonging to other rooms and answer from the room's own data -
        which is the opposite of what a pre-check is for.

        Returns the matches **and the rows they resolved to**, rather than
        letting the caller rebuild a lookup table. The caller needs the rows to
        report the duplicate's fields, and a caller that rebuilt the table
        itself could miss a row a ``find`` had already matched - which is how a
        match turns into a match with no matching record id, and a hard block
        for the wrong reason.
        """
        found: dict[tuple[str, str, str], Match] = {}
        resolved: dict[str, dict[str, Any]] = {}
        for key in keys:
            value = inbound.get(key)
            if value is None:
                continue
            candidates: list[Mapping[str, Any]] = [*self.crm.find(key, str(value)), *rows]
            if allowed is not None:
                candidates = [row for row in candidates if str(row.get("id") or "") in allowed]
            for row in candidates:
                if row.get("id"):
                    resolved.setdefault(str(row["id"]), dict(row))
            for match in match_rows(inbound, candidates, [key], self.registry, min_score=threshold):
                found[(match.key, match.record_id, match.matcher)] = match
        ordered = sorted(found.values(), key=lambda match: (-match.score, match.key, match.record_id))
        return (
            ordered,
            {match.record_id: resolved[match.record_id] for match in ordered if match.record_id in resolved},
        )

    def _result_for(
        self,
        key: str | None,
        subset: Sequence[Match],
        rows_by_id: Mapping[str, Any],
        where: str,
    ) -> DuplicateResult:
        """Turn a decisive match set into the CRM's three researched answers.

        Rows are deduplicated by record id first, and that is load-bearing rather
        than tidy: two registered matchers finding the *same* record - an exact
        email match and the fuzzy domain+name matcher agreeing - is one existing
        record, and counting the matches instead would report a multi-match and
        turn a clean block into a hard block. A multi-match has to mean several
        *records*.

        ``key is None`` with a non-empty subset means two keys disagreed about the
        record, which :func:`decisive` reports as ambiguous; that is the hardest
        case the research does not cover and is handled as a hard block.
        """
        if not subset:
            return clean(f"no existing record matched any configured key, checked against {where}")
        unique: dict[str, dict[str, Any]] = {}
        for match in subset:
            row = rows_by_id.get(match.record_id)
            if row is not None:
                unique.setdefault(match.record_id, row)
        rows = list(unique.values())
        if not rows:
            return clean(f"no existing record matched any configured key, checked against {where}")
        if key is None:
            return multiple(
                rows,
                match_key=None,
                reason=(
                    "two different matching keys matched two different records, so there is no "
                    f"single matching record id to act on ({where})"
                ),
            )
        if len(rows) > 1:
            return multiple(
                rows,
                match_key=key,
                reason=f"the {key} matches {len(rows)} existing records ({where})",
            )
        return matched(rows, match_key=key, reason=f"{key} matched one existing record ({where})")

    # -- the write ---------------------------------------------------------- #

    def ingest(
        self,
        room_id: str,
        inbound: Mapping[str, Any],
        *,
        connection_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 1 to 5: evaluate, act, and log. The whole workflow in one call.

        Returns the decision record. ``source`` is required and passed through
        unchanged to every row this writes, so the audit log names the route that
        served the request rather than a string the domain invented.
        """
        room = self.store.get(room_id)
        if room is None:
            # RecordNotFound, not DedupeError: a room that does not exist is a
            # 404, and the core app already maps this type. Raising our own error
            # here would answer 400 for what every other route in the product
            # answers 404 for.
            raise RecordNotFound(room_id)
        connection = self.resolve_connection(connection_id)
        settings = dict(connection.get("data") or {})
        if settings.get("enabled") is False:
            raise DedupeError(
                f"connection {connection.get('id')} is disabled; a disabled connection evaluates "
                "no duplicates and writes nothing"
            )

        payload = dict(inbound or {})
        decision = self.evaluate(payload, connection, room_id=room_id)
        at = self.clock().isoformat()

        write = self._apply(decision, payload, room_id=room_id, actor=actor, source=source)

        record = self.store.create(
            DECISION_COLLECTION,
            {
                **decision.to_dict(),
                "connection_id": connection.get("id"),
                "connection_name": settings.get("name"),
                "vendor": settings.get("vendor", DEFAULT_VENDOR),
                "object_type": payload.get("object_type", "contact"),
                "inbound": payload,
                "write": write,
                "at": at,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self.annotate_room(room_id, record, at=at, actor=actor, source=source)
        return record

    def _apply(
        self,
        decision: Decision,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Step 4, the only place a write happens. Outcome decides which.

        A refusal writes nothing at all - not the CRM row, not a stub - so a
        blocked decision cannot be mistaken for a successful one by counting
        rows. The summary of what was attempted still goes on the decision.
        """
        if decision.outcome == CREATED:
            record = self.crm.create(
                {**payload, "synced_from": room_id, "object_type": payload.get("object_type", "contact")},
                actor=actor,
                source=source,
                room_id=room_id,
            )
            return {"kind": "created", "record_id": record["id"]}

        if decision.outcome == CREATED_DUPLICATE:
            record = self.crm.create(
                {
                    **payload,
                    "synced_from": room_id,
                    "object_type": payload.get("object_type", "contact"),
                    "duplicate_of": (decision.matched_ids or [None])[0],
                    "acknowledged": True,
                },
                actor=actor,
                source=source,
                room_id=room_id,
            )
            return {"kind": "created_duplicate", "record_id": record["id"], "of": record["id"]}

        if decision.outcome == UPDATED:
            target = (decision.matched_ids or [None])[0]
            if not target:
                # Unreachable while _apply is only called with a `match` result,
                # but a wrong write to the wrong row is worse than a refusal.
                raise DedupeError("update policy reached without a matched record id")
            patch = {
                key: value
                for key, value in payload.items()
                if key not in ENGINE_FIELDS and value is not None
            }
            self.crm.update(target, patch, actor=actor, source=source)
            return {"kind": "updated", "record_id": target, "fields": sorted(patch)}

        # blocked, hard_blocked, escalated: nothing is written.
        return {"kind": "none"}

    def annotate_room(
        self,
        room_id: str,
        decision_record: Mapping[str, Any],
        *,
        at: str,
        actor: str | None,
        source: str,
    ) -> None:
        """Step 5: log the decision and the matched record id on the room row.

        The research says the annotation lands on the room row, so it does, with
        a bounded history beside the latest values. The full decision stays in
        its own record, because an array on a room is a poor place to keep match
        detail and a capped one cannot answer a question about an older decision.
        """
        data = dict(decision_record.get("data") or {})
        match_id = (data.get("matched_ids") or [None])[0]
        room = self.store.get(room_id)
        if room is None:
            raise RecordNotFound(room_id)
        existing = dict(room["data"].get(ROOM_FIELD) or {})
        history = list(existing.get("history") or [])
        history.append(
            {
                "decision_id": decision_record.get("id"),
                "outcome": data.get("outcome"),
                "match_key": data.get("match_key"),
                "match_record_id": match_id,
                "policy": data.get("policy"),
                "at": at,
            }
        )
        self.store.update(
            room_id,
            {
                ROOM_FIELD: {
                    "last_outcome": data.get("outcome"),
                    "last_decision_id": decision_record.get("id"),
                    "last_match_key": data.get("match_key"),
                    "last_match_record_id": match_id,
                    "last_matched_ids": list(data.get("matched_ids") or []),
                    "updated_at": at,
                    "history": history[-ROOM_ANNOTATION_LIMIT:],
                }
            },
            actor=actor,
            source=source,
        )

    # -- reads -------------------------------------------------------------- #

    def decisions(
        self,
        *,
        room_id: str | None = None,
        outcome: str | None = None,
        policy: str | None = None,
        connection_id: str | None = None,
        needs_human: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Decisions, newest first. Every filter is a JSON path in the payload.

        ``room_id`` is filtered here rather than in the query: the dynamic index
        resolves JSON paths inside ``data`` but not the ``room_id`` column, so
        :meth:`AuditedDatabase.find` cannot scope by room. Filtering in Python
        keeps that a detail of this method instead of a schema change.
        """
        where: dict[str, Any] = {}
        if outcome is not None:
            where["outcome"] = str(outcome)
        if policy is not None:
            where["policy"] = require_policy(policy)
        if connection_id is not None:
            where["connection_id"] = connection_id
        if needs_human is not None:
            where["needs_human"] = needs_human
        records = self.store.find(DECISION_COLLECTION, where, limit=1000)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return records[:limit]

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        return self.store.get(decision_id)

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts over the decisions, for the top of the page.

        A decision that wrote nothing is counted separately from one that
        blocked, because they mean different things to a rep: a block is the
        policy working, a hard block is something needing attention.
        """
        records = self.decisions(room_id=room_id, limit=1000)
        by_outcome: dict[str, int] = {}
        by_policy: dict[str, int] = {}
        crm_calls = 0
        for record in records:
            data = record["data"]
            outcome = str(data.get("outcome"))
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            policy = str(data.get("policy"))
            by_policy[policy] = by_policy.get(policy, 0) + 1
            if data.get("crm_called"):
                crm_calls += 1
        needs_human = sum(1 for record in records if record["data"].get("needs_human"))
        return {
            "room_id": room_id,
            "decisions": len(records),
            "by_outcome": dict(sorted(by_outcome.items())),
            "by_policy": dict(sorted(by_policy.items())),
            "needs_human": needs_human,
            "crm_calls": crm_calls,
            "crm_calls_avoided": len(records) - crm_calls,
            "connections": len(self.list_connections(room_id=room_id)),
            "records": len(self.list_records(room_id=room_id)),
        }

    def header_preview(self, policy: str, *, run_as_current_user: bool = False) -> dict[str, Any]:
        """The header a policy would send, without sending anything."""
        header = build_duplicate_rule_header(policy, run_as_current_user=run_as_current_user)
        return {
            "name": DUPLICATE_RULE_HEADER_NAME,
            "policy": require_policy(policy),
            "options": header,
            "wire": serialise_duplicate_rule_header(header),
        }
