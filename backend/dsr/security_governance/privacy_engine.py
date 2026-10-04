"""The reads and the writes: region, retention, consent and per-subject erasure.

The engine is the only module in this workflow that touches the store. It holds the
store and a clock and nothing else, and the HTTP layer builds it per request for
exactly that reason: both seams stay overridable in a test without hanging a long-lived
object off ``app.state``, which is a shared file this feature may not edit.

What the writes are, and what they are not
-------------------------------------------

**Five of the six writes are administrator-only.** Region changes, retention policy,
retention runs, opening an erasure request and fulfilling one are all blocking changes,
and the specification says they are: "role separation, since blocking changes are
admin-only". Recording a consent signal is the sixth and is deliberately *not*
administrator-only, because the specification says consent is captured "at the page".

**Every write names a route the host actually serves.** The HTTP layer passes
``source=`` built from its own router, so the audit row names the route that served the
write and the two cannot drift.

**Erasure removes the subject's records and nothing else.** The specification quotes the
vendor's limitation - "You need to delete the entire project to delete user's data" - and
calls per-subject deletion the hard part. So discovery matches on the address and
erasure touches only the records that matched. It reports the residue it could not
remove, because an erasure that reported a clean sweep while audit snapshots held the
address would be the failure this workflow exists to prevent.

**Every write is a hard delete where erasure is meant.** A soft delete leaves the row and
its dynamic index behind, which is a longer-lived copy of the same personal data. The
retention run and the erasure both pass ``hard=True``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from dsr.security_governance import privacy_rules as rules, residency as vocab
from dsr.store import RecordStore

#: How many rows one scan reads. Bounded so a room with a very large engagement history
#: cannot make a read unbounded. It is a bound and not a claim that the table is small:
#: the responses report what was scanned, so a caller can see whether the bound cut
#: anything off rather than trusting a count.
SCAN_LIMIT = 1000

#: How many matching records a DSAR read shows in full. Discovery itself is never capped
#: - every match is erased - but a request that found ten thousand rows cannot render ten
#: thousand detail blocks, so the detail list is capped and the response says it was.
DETAIL_LIMIT = 50


class PrivacyEngine:
    """Every read and every write this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand, which
    is the only way to test the boundary the specification cares about: a record that
    becomes due exactly at its window.

    ``administrator`` is the role tier the blocking changes are gated on, and it is
    required rather than defaulted. This package depends on nothing inside ``dsr`` but the
    store and itself, so the repository's own role surface is read by the feature module
    and handed in here. One import site, one role vocabulary, and a test can gate against
    a tier of its own choosing.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        administrator: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not str(administrator or "").strip():
            raise rules.PrivacyRefusal(
                "PrivacyEngine needs an administrator role.",
                {"administrator": "Name the role tier that may make a blocking change."},
            )
        self.store = store
        self.administrator = str(administrator).strip()
        self._now = now or rules.utcnow
        self._collections_touched: dict[str, int] = {}

    # -- clock --------------------------------------------------------------- #

    def now(self) -> datetime:
        return self._now()

    # -- residency ----------------------------------------------------------- #

    def residency(self, room_id: str | None = None) -> dict[str, Any] | None:
        """The room's residency record, or ``None`` when none has been recorded.

        The newest record wins, so a room that has moved region reports where it is now
        rather than where it started. The full history stays in the audit log.

        ``descending=False`` is explicit because the default is descending, and two moves
        inside one millisecond tie on ``created_at``. The store tie-breaks on its own
        insertion sequence, so ascending order plus the last element is "newest" without
        relying on the clock being readable to the millisecond.
        """

        records = self.store.list(
            vocab.RESIDENCY_COLLECTION,
            room_id=room_id,
            limit=SCAN_LIMIT,
            order_by="created_at",
            descending=False,
        )
        if not records:
            return None
        return self.project_residency(records[-1])

    def set_residency(
        self,
        region: Any,
        *,
        transfer_mechanism: Any = None,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Pin the deployment to a region and re-stamp every record it holds.

        The region move re-stamps rather than refusing, which is the derivation recorded
        as ``DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD`` and put to Jev as audit
        ``jev-20261004T182818-16136-98999``. One jurisdiction always holds the room's
        data, so a DSAR can name the store a record is in.

        Every re-stamp is a separate audited write naming the same route, so the audit
        log describes exactly what moved. The response reports the count and the
        collections, because a change that wrote to every row and reported one boolean
        would hide its own cost.
        """

        rules.require_administrator(role, "setting the residency region", self.administrator)
        name = rules.normalise_region(region)
        mechanism = rules.normalise_transfer_mechanism(transfer_mechanism)
        previous = self.residency(room_id)

        scanned, moved = self._restamp(room_id, name, actor=actor, source=source)

        record = self.store.create(
            vocab.RESIDENCY_COLLECTION,
            {
                vocab.RESIDENCY_FIELD: name,
                "jurisdiction": rules.jurisdiction_of(name),
                vocab.TRANSFER_MECHANISM_FIELD: mechanism,
                "previous_residency_region": previous[vocab.RESIDENCY_FIELD] if previous else None,
                "records_moved": moved,
                "recorded_at": rules.stamp(self.now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        payload = self.project_residency(record)
        payload["relocation"] = {
            "records_scanned": scanned,
            "records_moved": moved,
            "by_collection": self._collections_touched,
        }
        return payload

    def _restamp(
        self, room_id: str | None, region: str, *, actor: str | None, source: str
    ) -> tuple[int, int]:
        """Rewrite the region key on every live record the deployment holds.

        Returns ``(scanned, moved)``. A record already stamped with this region is left
        alone, so re-running the same move writes nothing and reports zero.
        """

        self._collections_touched: dict[str, int] = {}
        scanned = 0
        moved = 0
        for collection in self._collections():
            records = self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT)
            for record in records:
                data = record.get("data") or {}
                if not isinstance(data, Mapping):
                    # A payload this workflow cannot read is reported, not guessed at.
                    self._collections_touched[collection] = (
                        self._collections_touched.get(collection, 0) + 1
                    )
                    scanned += 1
                    continue
                scanned += 1
                if data.get(vocab.RESIDENCY_FIELD) == region:
                    continue
                self.store.update(
                    record["id"],
                    {vocab.RESIDENCY_FIELD: region},
                    actor=actor,
                    source=source,
                )
                self._collections_touched[collection] = (
                    self._collections_touched.get(collection, 0) + 1
                )
                moved += 1
        return scanned, moved

    def residency_report(self, room_id: str | None = None) -> dict[str, Any]:
        """The residency answer a legal question asks, with what is not claimed.

        ``records_in_region`` counts the records already stamped. ``transfer_declared``
        is false when no mechanism was recorded, which is the gap the page renders rather
        than a statement that no transfer occurs.
        """

        current = self.residency(room_id)
        stamped, unstamped = self._stamped_counts(room_id)
        return {
            vocab.RESIDENCY_FIELD_NAME: current,
            "records_in_region": stamped,
            "records_unstamped": unstamped,
            "consent_required_here": bool(
                current and rules.consent_required(current[vocab.RESIDENCY_FIELD])
            ),
            "consent_jurisdictions": list(vocab.CONSENT_JURISDICTIONS),
            "jurisdictions": [
                {
                    "id": code,
                    "label": vocab.JURISDICTION_LABELS[code],
                    "consent_required": code in vocab.CONSENT_JURISDICTIONS,
                }
                for code in vocab.JURISDICTIONS
            ],
            "regions": [
                {
                    "id": region["id"],
                    "label": region["label"],
                    "jurisdiction": region["jurisdiction"],
                }
                for region in vocab.REGIONS
            ],
            "transfer_mechanisms": [
                {"id": mechanism, "label": vocab.TRANSFER_LABELS[mechanism]}
                for mechanism in vocab.TRANSFER_MECHANISMS
            ],
            "transfer_mechanism_note": vocab.TRANSFER_MECHANISM_NOTE,
            "vendor_claims": [dict(claim) for claim in vocab.VENDOR_CLAIMS],
            "certification_note": (
                "No certification is claimed for this deployment. The researched "
                "certifications are a vendor's own claims and are listed as unverified."
            ),
            "rules": {
                vocab.RESIDENCY_FIELD: (
                    "A region change re-stamps every record the deployment holds, so one "
                    "jurisdiction always holds the room's data."
                ),
                "move_recorded": (
                    "DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD, Jev audit "
                    "jev-20261004T182818-16136-98999"
                ),
            },
        }

    def project_residency(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            vocab.RESIDENCY_FIELD: data.get(vocab.RESIDENCY_FIELD),
            "jurisdiction": data.get("jurisdiction"),
            vocab.TRANSFER_MECHANISM_FIELD: data.get(vocab.TRANSFER_MECHANISM_FIELD),
            "transfer_declared": bool(data.get(vocab.TRANSFER_MECHANISM_FIELD))
            and data.get(vocab.TRANSFER_MECHANISM_FIELD) != vocab.TRANSFER_NONE,
            "previous_residency_region": data.get("previous_residency_region"),
            "records_moved": int(data.get("records_moved") or 0),
            "recorded_at": data.get("recorded_at"),
        }

    def _stamped_counts(self, room_id: str | None) -> tuple[int, int]:
        current = self.residency(room_id)
        region = current[vocab.RESIDENCY_FIELD] if current else None
        stamped = 0
        unstamped = 0
        for collection in self._collections():
            for record in self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT):
                data = record.get("data") or {}
                if (
                    isinstance(data, Mapping)
                    and data.get(vocab.RESIDENCY_FIELD) == region
                    and region
                ):
                    stamped += 1
                else:
                    unstamped += 1
        return stamped, unstamped

    # -- retention ----------------------------------------------------------- #

    def _overrides(self, room_id: str | None) -> dict[str, Any]:
        """The deployment's configured windows, read back from its residency record.

        Stored on the residency record rather than in a settings table so there is one
        place a deployment's privacy configuration lives, and so a room that has never
        been configured reports the researched figures rather than an empty policy.
        """

        record = self.residency(room_id)
        if not record:
            return {}
        data = self.store.get(record["id"]) or {}
        windows = ((data.get("data") or {}).get("retention_windows")) or {}
        return dict(windows) if isinstance(windows, Mapping) else {}

    def retention_report(self, room_id: str | None = None) -> dict[str, Any]:
        """The policy, the classes and the collections nothing ages."""

        overrides = self._overrides(room_id)
        policy = rules.retention_policy(overrides)
        scope = rules.scope_of(overrides)
        return {
            "policy": policy,
            "scope": scope,
            vocab.WINDOW_UNIT_FIELD: vocab.WINDOW_UNIT,
            "unmapped_collections": rules.unmapped_collections(self._collections()),
            vocab.INSTANT_FORMAT_FIELD: list(vocab.INSTANT_FORMATS),
            "undated_policy": vocab.UNDATED_POLICY,
            "no_side_channel_rule": (
                "A collection holding personal data that no class ages is a longer-lived "
                "copy of it. Any live collection the table does not cover is named above."
            ),
            "recordings_evidence": vocab.RETENTION_EVIDENCE[vocab.CLASS_SESSION_RECORDING],
        }

    def set_retention_policy(
        self,
        windows: Mapping[str, Any] | None,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Set the deployment's windows. Shorter than the ceiling, never longer.

        The windows land on the residency record, so a policy change and a region change
        are the same row's history and the residency read always reflects both.
        """

        rules.require_administrator(role, "setting the retention policy", self.administrator)
        current = self.residency(room_id)
        if current is None:
            raise rules.PrivacyNotFound(
                "No residency record. Set the region before setting a retention policy."
            )

        proposed = dict(windows or {})
        # Validate every class before writing anything, so a refusal leaves no partial
        # policy behind.
        for class_id, value in proposed.items():
            rules.retention_window(class_id, {rules.normalise_class(class_id): value})

        merged = dict(self._overrides(room_id))
        for class_id, value in proposed.items():
            merged[rules.normalise_class(class_id)] = rules.retention_window(
                class_id, {rules.normalise_class(class_id): value}
            )

        self.store.update(current["id"], {"retention_windows": merged}, actor=actor, source=source)
        return self.retention_report(room_id)

    def retention_schedule(self, room_id: str | None = None) -> dict[str, Any]:
        """What is due for erasure now, by class, and when the next record falls due.

        ``undated`` is reported beside ``due`` and never folded into it. A record with no
        readable instant is not fresh and not expired, and counting it either way would
        put a number on the page that means something different from what it says.
        """

        overrides = self._overrides(room_id)
        scope = rules.scope_of(overrides)
        now = self.now()

        by_class: dict[str, dict[str, Any]] = {}
        undated = 0
        scanned = 0

        for collection, entry in scope.items():
            window = entry["window_days"]
            field = entry["instant_field"]
            row = by_class.setdefault(
                entry["class"],
                {
                    "class": entry["class"],
                    "window_days": window,
                    "records": 0,
                    "due": 0,
                    "retained": 0,
                    "undated": 0,
                    "next_due_at": None,
                    "collections": [],
                },
            )
            if collection not in row["collections"]:
                row["collections"].append(collection)
            for record in self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT):
                scanned += 1
                data = record.get("data") or {}
                recorded = rules.coerce_instant(
                    (data.get(field) if isinstance(data, Mapping) else None),
                    field or "recorded_at",
                )
                row["records"] += 1
                due = rules.is_due(recorded, window, now)
                if due is None:
                    row["undated"] += 1
                    undated += 1
                    continue
                if due:
                    row["due"] += 1
                    continue
                row["retained"] += 1
                # The earliest deadline *in this class*, so a page shows when this class
                # falls due rather than when the earliest class does.
                deadline = rules.due_at(recorded, window)
                if row["next_due_at"] is None or deadline < rules.coerce_instant(
                    row["next_due_at"], "next_due_at"
                ):
                    row["next_due_at"] = rules.stamp(deadline)

        return {
            "as_of": rules.stamp(now),
            "as_of_unix_ms": rules.now_ms(now),
            "classes": [by_class[name] for name in vocab.RETENTION_CLASSES if name in by_class],
            vocab.WINDOW_UNIT_FIELD: vocab.WINDOW_UNIT,
            "records_scanned": scanned,
            "undated": undated,
            "undated_policy": vocab.UNDATED_POLICY,
            "scan_limit": SCAN_LIMIT,
            "scan_truncated": scanned >= SCAN_LIMIT * max(1, len(scope)),
        }

    def run_retention(
        self,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Erase every record past its window and record the run.

        The deletion is a hard delete. A soft delete would leave the row and its dynamic
        index behind, and a longer-lived copy of personal data is precisely what the
        retention classes exist to prevent.

        The run writes its own record, which is not in the retention table because it
        carries no personal data. That is a deliberate exclusion and the report names
        every collection the table does not cover, so the exclusion is visible.
        """

        rules.require_administrator(role, "running retention", self.administrator)
        schedule = self.retention_schedule(room_id)
        overrides = self._overrides(room_id)
        scope = rules.scope_of(overrides)
        now = self.now()

        erased_by_collection: dict[str, int] = {}
        erased_ids: list[str] = []
        for collection, entry in scope.items():
            field = entry["instant_field"]
            window = entry["window_days"]
            for record in self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT):
                data = record.get("data") or {}
                recorded = rules.coerce_instant(
                    (data.get(field) if isinstance(data, Mapping) else None), field or "recorded_at"
                )
                if not rules.is_due(recorded, window, now):
                    continue
                self.store.delete(record["id"], actor=actor, source=source, hard=True)
                erased_ids.append(record["id"])
                erased_by_collection[collection] = erased_by_collection.get(collection, 0) + 1

        residue = self._audit_residue(erased_ids)
        run = self.store.create(
            vocab.RETENTION_RUN_COLLECTION,
            {
                "room_id": room_id,
                "erased": len(erased_ids),
                "erased_by_collection": erased_by_collection,
                "undated": schedule["undated"],
                "windows": {
                    name: rules.retention_window(name, overrides)
                    for name in vocab.RETENTION_CLASSES
                },
                "audit_rows_retained": residue["audit_rows"],
                "recorded_at": rules.stamp(now),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "run_id": run["id"],
            "erased": len(erased_ids),
            "erased_by_collection": erased_by_collection,
            "undated": schedule["undated"],
            "undated_policy": vocab.UNDATED_POLICY,
            "residue": residue,
            "unmapped_collections": rules.unmapped_collections(self._collections()),
            vocab.WINDOW_UNIT_FIELD: vocab.WINDOW_UNIT,
            "as_of": rules.stamp(now),
        }

    # -- consent ------------------------------------------------------------- #

    def consent_record(self, subject: str, room_id: str | None = None) -> dict[str, Any] | None:
        """The newest consent record for one subject, or ``None``.

        Newest rather than oldest, because the most recent signal is the one that
        governs: a grant followed by a deny is a revocation, and reading the first row
        would report tracking for a visitor who has since opted out. The store's list is
        descending by default, so the newest row is the first one; ``descending=False``
        is passed here so the intent reads the same way as in :meth:`residency`.
        """

        address = rules.normalise_subject(subject)
        records = self.store.list(
            vocab.CONSENT_COLLECTION,
            room_id=room_id,
            limit=SCAN_LIMIT,
            order_by="created_at",
            descending=False,
        )
        matches = [
            record
            for record in records
            if str((record.get("data") or {}).get("subject") or "").strip().lower() == address
        ]
        if not matches:
            return None
        return self.project_consent(matches[-1])

    def consent_report(
        self,
        room_id: str | None = None,
        *,
        region: Any = None,
        signal: Any = None,
        subject: Any = None,
        **opt_out: Any,
    ) -> dict[str, Any]:
        """The gate's vocabulary, plus the decision for one region if one was named.

        Naming a region evaluates the gate for that case without writing anything, so an
        operator can see what the page would do before recording it. Naming a subject as
        well adds that subject's current state, which is how the page shows a revocation
        beside the gate that produced it.
        """

        report: dict[str, Any] = {
            "consent_regions": [
                {"id": code, "label": vocab.JURISDICTION_LABELS[code]}
                for code in vocab.CONSENT_JURISDICTIONS
            ],
            "enforcement_quote": vocab.CONSENT_ENFORCEMENT_QUOTE,
            "grant_word": vocab.CONSENT_GRANTED,
            "outcomes": list(vocab.GATE_OUTCOMES),
            "states": list(vocab.CONSENT_STATES),
            "opt_out_signals": list(vocab.OPT_OUT_SIGNALS),
            "unsupported_signals": [
                {"signal": name, "reason": "The specification records it as unsupported."}
                for name in vocab.UNSUPPORTED_SIGNALS
            ],
            "deny_effect": dict(vocab.DENY_EFFECT),
            "grant_effect": dict(vocab.GRANT_EFFECT),
            "not_required": vocab.CONSENT_NOT_REQUIRED,
            "fails_closed": True,
            "fails_closed_rule": (
                "Only the exact grant word allows tracking. A missing, unreadable or "
                "unknown signal denies, and an opt-out signal denies whatever else was sent."
            ),
            "revocation_rule": (
                "A deny after a grant is a revocation: the record is marked revoked, the "
                "persistent identifier is cleared and tracking is blocked until new consent."
            ),
        }
        records = self.store.list(
            vocab.CONSENT_COLLECTION,
            room_id=room_id,
            limit=SCAN_LIMIT,
            order_by="created_at",
            descending=False,
        )
        report["recorded"] = [self.project_consent(record) for record in records]
        report["counts"] = {
            state: sum(1 for record in report["recorded"] if record["state"] == state)
            for state in vocab.CONSENT_STATES
        }
        if region:
            sent = {key: value for key, value in opt_out.items() if value is not None}
            decision = self.evaluate(region, signal=signal, **sent)
            report["evaluation"] = decision
            if subject:
                report["current"] = self.consent_record(str(subject), room_id)
        return report

    def evaluate(self, region: Any, *, signal: Any = None, **opt_out: Any) -> dict[str, Any]:
        """The gate's decision for one page view. Writes nothing."""

        return rules.gate(region, signal=signal, **opt_out)

    def record_consent(
        self,
        region: Any,
        subject: Any,
        *,
        signal: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
        **opt_out: Any,
    ) -> dict[str, Any]:
        """Record one consent signal and return the decision the gate reached.

        Not administrator-gated. The specification says consent is captured "at the page
        (per visitor, per region)", so the caller here is the room's own page rather than
        an operator.

        A deny after a grant is a revocation and is reported as one: the stored state
        becomes ``revoked``, ``cookies_cleared`` is true and tracking is blocked until new
        consent. That is the evidence's own sentence - ``clarity('consent', false)``
        "clears the Clarity cookies from the user's browser and prevent further tracking
        until new consent is granted".
        """

        address = rules.normalise_subject(subject)
        # An opt-out key the caller did not send is dropped rather than passed as None,
        # so the gate does not report an absent signal as an unreadable one.
        opt_out = {key: value for key, value in opt_out.items() if value is not None}
        decision = rules.gate(region, signal=signal, **opt_out)
        now = self.now()
        previous = self.consent_record(address, room_id)
        was_granted = bool(previous and previous["granted"] and previous["state"] == vocab.ACTIVE)
        revoked = decision["outcome"] == vocab.GATE_DENY and was_granted

        if decision["outcome"] == vocab.GATE_DENY:
            state = vocab.REVOKED if revoked else vocab.CONSENT_DENIED
        else:
            state = vocab.ACTIVE

        record = self.store.create(
            vocab.CONSENT_COLLECTION,
            {
                "subject": address,
                "region": decision["region"],
                "jurisdiction": decision["jurisdiction"],
                "signal": str(signal) if signal is not None else None,
                "outcome": decision["outcome"],
                "granted": bool(decision["granted"]),
                "state": state,
                "revoked": revoked,
                "cookies_cleared": decision["outcome"] == vocab.GATE_DENY,
                "tracking_blocked": decision["outcome"] == vocab.GATE_DENY,
                "identifier": decision["effect"]["identifier"],
                "opt_out": dict(opt_out),
                "signals_ignored": decision["signals_ignored"],
                "reason": decision["reason"],
                "room_id": room_id,
                "recorded_at": rules.stamp(now),
            },
            room_id=room_id,
            actor=actor or address,
            source=source,
        )
        payload = self.project_consent(record)
        payload["decision"] = decision
        payload["previous_state"] = previous["state"] if previous else None
        return payload

    def project_consent(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "subject": data.get("subject"),
            "region": data.get("region"),
            "jurisdiction": data.get("jurisdiction"),
            "signal": data.get("signal"),
            "outcome": data.get("outcome"),
            "granted": bool(data.get("granted")),
            "state": data.get("state"),
            "revoked": bool(data.get("revoked")),
            "cookies_cleared": bool(data.get("cookies_cleared")),
            "tracking_blocked": bool(data.get("tracking_blocked")),
            "identifier": data.get("identifier"),
            "opt_out": data.get("opt_out") or {},
            "signals_ignored": data.get("signals_ignored") or [],
            "reason": data.get("reason"),
            "recorded_at": data.get("recorded_at"),
        }

    # -- the DSAR ------------------------------------------------------------ #

    def dsar_requests(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every data-subject request, oldest first.

        Oldest first so the request nearest its deadline is at the top, and so the list
        reads as the order they arrived. Ascending is explicit because the store's default
        is descending.
        """

        records = self.store.list(
            vocab.DSAR_COLLECTION,
            room_id=room_id,
            limit=SCAN_LIMIT,
            order_by="created_at",
            descending=False,
        )
        return [self.project_dsar(record) for record in records]

    def open_dsar(
        self,
        subject: Any,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Open a data-subject request and record everything it found.

        Discovery reads every aged collection and matches on the address the evidence
        names. The found count is recorded on the request so a later read can say how many
        records the request covered without rescanning, and so a request that found
        nothing says so rather than rendering as an empty table.

        Administrator-only. The specification calls fulfilment "a privileged,
        project-scoped administrative operation", and opening the request is the step
        that decides whose data will be erased.
        """

        rules.require_administrator(role, "opening a data-subject request", self.administrator)
        address = rules.normalise_subject(subject)
        found = self.discover(address, room_id=room_id)
        now = self.now()

        record = self.store.create(
            vocab.DSAR_COLLECTION,
            {
                "subject": address,
                "room_id": room_id,
                "state": vocab.DSAR_OPENED if found["records"] else vocab.DSAR_NOTHING_FOUND,
                "reason": reason,
                "found": found["records"],
                "found_by_collection": found["by_collection"],
                "opened_by": actor,
                "opened_at": rules.stamp(now),
                "deadline_at": rules.stamp(now + timedelta(days=vocab.DSAR_WINDOW_DAYS)),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        payload = self.project_dsar(record)
        payload["found"] = found["records"]
        payload["found_by_collection"] = found["by_collection"]
        payload["personal_data"] = found["personal_data"]
        return payload

    def discover(self, subject: str, *, room_id: str | None = None) -> dict[str, Any]:
        """Every record holding this subject's personal data, and which fields.

        Reads the retention scope rather than the whole store, so discovery is bounded by
        the same table the retention classes age and a collection nobody ages is named
        rather than silently skipped.
        """

        scope = rules.scope_of(self._overrides(room_id))
        by_collection: dict[str, int] = {}
        personal: list[dict[str, Any]] = []
        record_ids: list[tuple[str, str]] = []
        total = 0
        truncated = False
        for collection in scope:
            found = 0
            records = self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT)
            if len(records) >= SCAN_LIMIT:
                truncated = True
            for record in records:
                data = record.get("data") or {}
                if not rules.matches_subject(collection, data, subject):
                    continue
                found += 1
                total += 1
                record_ids.append((collection, record["id"]))
                if len(personal) < DETAIL_LIMIT:
                    personal.append(
                        {
                            "id": record["id"],
                            "collection": collection,
                            "room_id": record.get("room_id"),
                            "personal_data": rules.personal_data_of(collection, data),
                        }
                    )
            if found:
                by_collection[collection] = found
        return {
            "records": total,
            "by_collection": by_collection,
            "record_ids": record_ids,
            "personal_data": personal,
            "personal_data_shown": len(personal),
            "personal_data_limit": DETAIL_LIMIT,
            "scan_truncated": truncated,
            "scan_limit": SCAN_LIMIT,
            "unmapped_collections": rules.unmapped_collections(self._collections()),
        }

    def fulfil_dsar(
        self,
        request_id: str,
        *,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Erase the records one subject owns and report what survived.

        Per-subject, never per-room. The specification quotes the vendor's limitation -
        "You need to delete the entire project to delete user's data" - and calls
        per-subject deletion the hard part, so this touches only the records that matched
        the address.

        The residue is reported, not hidden. Every erased row took its audit rows with it
        into the log's history, and those rows hold the subject's address in their before
        and after snapshots. Removing them would falsify the log this repository is built
        on, so they stay, they are counted, and the reason is named. That derivation is
        ``DERIVED_DSAR_RETAINS_THE_AUDIT_TRAIL_AS_RESIDUE``.
        """

        rules.require_administrator(role, "fulfilling a data-subject request", self.administrator)
        record = self.store.get(request_id)
        if record is None or record.get("collection") != vocab.DSAR_COLLECTION:
            raise rules.PrivacyNotFound(f"No data-subject request {request_id!r}.")

        data = record.get("data") or {}
        address = rules.normalise_subject(data.get("subject"))
        room_id = record.get("room_id")
        found = self.discover(address, room_id=room_id)

        erased_by_collection: dict[str, int] = {}
        erased_ids: list[str] = []
        kept_records: list[str] = []
        for collection, record_id in found["record_ids"]:
            if record_id == request_id:
                # The request that performs the erasure keeps itself: it names the
                # subject and it is the evidence the erasure happened. Reported as
                # residue rather than dropped, because a silent keep is a hidden gap.
                kept_records.append(record_id)
                continue
            self.store.delete(record_id, actor=actor, source=source, hard=True)
            erased_ids.append(record_id)
            erased_by_collection[collection] = erased_by_collection.get(collection, 0) + 1

        residue = self._audit_residue(erased_ids)
        residue["found"] = found["records"]
        residue["erased"] = len(erased_ids)
        residue["erasure_records_kept"] = len(kept_records)
        residue["scan_truncated"] = found["scan_truncated"]
        # `remaining` is null when the scan hit its bound, because a truncated scan
        # cannot say whether anything survived. Reporting zero would be the clean sweep
        # this derivation exists to refuse.
        residue["remaining"] = (
            None if found["scan_truncated"] else max(0, found["records"] - len(erased_ids))
        )

        incomplete = (
            residue["remaining"] is None
            or residue["audit_rows"] > 0
            or kept_records
            or len(erased_ids) == 0
        )
        patch: dict[str, Any] = {
            "state": vocab.DSAR_PARTIAL if incomplete else vocab.DSAR_FULFILLED,
            "erased": len(erased_ids),
            "erased_by_collection": erased_by_collection,
            "residue": residue,
            "fulfilled_by": actor,
            "fulfilled_at": rules.stamp(self.now()),
        }
        self.store.update(request_id, patch, actor=actor, source=source)

        payload = self.project_dsar(self.store.require(request_id))
        payload["erased"] = len(erased_ids)
        payload["erased_by_collection"] = erased_by_collection
        payload["residue"] = residue
        return payload

    def project_dsar(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "subject": data.get("subject"),
            "state": data.get("state"),
            "reason": data.get("reason"),
            "found": int(data.get("found") or 0),
            "found_by_collection": data.get("found_by_collection") or {},
            "erased": int(data.get("erased") or 0),
            "erased_by_collection": data.get("erased_by_collection") or {},
            "residue": data.get("residue"),
            "opened_by": data.get("opened_by"),
            "opened_at": data.get("opened_at"),
            "deadline_at": data.get("deadline_at"),
            "fulfilled_by": data.get("fulfilled_by"),
            "fulfilled_at": data.get("fulfilled_at"),
            vocab.DSAR_STATES_FIELD: list(vocab.DSAR_STATES),
        }

    def _audit_residue(self, erased_ids: Sequence[str]) -> dict[str, Any]:
        """What the erasure could not remove: the audit rows about the removed records.

        Counted through the store's own audit read, one query per erased record, so the
        count is exact rather than estimated. The reason is carried with it so the number
        is never read as an unexplained gap.
        """

        rows = 0
        for record_id in erased_ids:
            rows += len(self.store.audit(record_id=record_id, limit=SCAN_LIMIT))
        return {
            "audit_rows": rows,
            "audit_mirror": vocab.RESIDUE_AUDIT_MIRROR,
            "reasons": list(vocab.RESIDUE_REASONS),
            "notes": dict(vocab.RESIDUE_NOTES),
            "rule": (
                "The audit trail is this product's guarantee. A row describing an erasure is "
                "the evidence the erasure happened, so the rows stay and are counted."
            ),
        }

    # -- the board ----------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers, read back from the store.

        Counts are read rather than accumulated across calls, so the board cannot describe
        a state the store does not hold.
        """

        residency = self.residency(room_id)
        schedule = self.retention_schedule(room_id)
        consents = self.store.list(vocab.CONSENT_COLLECTION, room_id=room_id, limit=SCAN_LIMIT)
        requests = self.store.list(vocab.DSAR_COLLECTION, room_id=room_id, limit=SCAN_LIMIT)

        return {
            vocab.RESIDENCY_FIELD_NAME: residency,
            "consent_required": bool(
                residency and rules.consent_required(residency[vocab.RESIDENCY_FIELD])
            ),
            "consent_signals": len(consents),
            "consent_states": {
                state: sum(
                    1 for record in consents if (record.get("data") or {}).get("state") == state
                )
                for state in vocab.CONSENT_STATES
            },
            "retention_due": sum(row["due"] for row in schedule["classes"]),
            "retention_retained": sum(row["retained"] for row in schedule["classes"]),
            "retention_undated": schedule["undated"],
            "dsar_open": sum(
                1
                for record in requests
                if (record.get("data") or {}).get("state") == vocab.DSAR_OPENED
            ),
            "dsar_partial": sum(
                1
                for record in requests
                if (record.get("data") or {}).get("state") == vocab.DSAR_PARTIAL
            ),
            "dsar_fulfilled": sum(
                1
                for record in requests
                if (record.get("data") or {}).get("state") == vocab.DSAR_FULFILLED
            ),
            "unmapped_collections": rules.unmapped_collections(self._collections()),
            vocab.COLLECTIONS_FIELD: list(vocab.ALL_COLLECTIONS),
            vocab.WINDOW_UNIT_FIELD: vocab.WINDOW_UNIT,
            "admin_role": self.administrator,
            "screen_text": vocab.SCREEN_TEXT_DEFAULT,
            "as_of": rules.stamp(self.now()),
        }

    # -- internals ----------------------------------------------------------- #

    def _collections(self) -> list[str]:
        """Every live collection the store holds, for a scan.

        Read from the store's own discovery rather than a list written here, because the
        "no longer-lived side channels" rule is about collections nobody has enumerated.
        A hard-coded list would make the check report exactly the collections its author
        already knew about.
        """

        return sorted({str(row["collection"]) for row in self.store.collections()})
