"""The store-facing surface of WF-049: :class:`IntegrationMonitor`.

:class:`IntegrationMonitor` is the only class in this package that touches a
:class:`~dsr.store.RecordStore`. It holds nothing but the store handle, so it is
built per request from a dependency and the whole workflow is unit-testable
against a temporary database without the app.

Four rules it exists to enforce:

**Every write names the route that served it.** ``source`` is a required keyword
on every write method, with no default. An audit row naming a path the app no
longer serves is worse than no audit row - the same defect the feature contract
calls out by name - and a monitoring dashboard is exactly the feature whose
audit log would quietly fill with stale routes, because everything on it polls.
The routes build it from ``router.prefix``.

**The room transports nothing.** Every ``apis_hit`` in the research is a call a
connector makes, and this product holds no vendor credentials. The quota and
telemetry endpoints take the vendor's answer back rather than fetching it; see
the ``the-room-holds-no-vendor-credentials`` inference.

**Readings are stored, not interpreted.** A quota observation carries the
normalised pair and the vendor/surface/instant it came from; the thresholds live
in alert rules. Recomputing a pair from a stored note would be a second parser,
and two parsers disagree.

**Unknown is not zero.** Every half of every pair carries ``known``, and every
refusal to read carries its reason, because a monitoring dashboard that
answered "0 remaining" for a connection it could not read would be the loudest
false alarm in the product.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.integ_monitor import alerts as alert_module
from dsr.integ_monitor import health
from dsr.integ_monitor import quota as quota_module
from dsr.integ_monitor.errors import (
    InvalidPayload,
    UnknownConnector,
    UnknownRoom,
    UnknownRule,
)
from dsr.integ_monitor.timestamps import first_present, iso, parse_instant  # noqa: F401
from dsr.integ_monitor.timestamps import check_not_ahead
from dsr.integ_monitor.vocabulary import (
    CHANGE_COLLECTION,
    CONNECTOR_COLLECTION,
    QUOTA_COLLECTION,
    ROOM_COLLECTION,
    RULE_COLLECTION,
    STREAM_COLLECTION,
    TELEMETRY_COLLECTION,
    require_vendor,
)
from dsr.integ_monitor.vocabulary import vocabulary as published_vocabulary
from dsr.store import RecordStore

#: Keys of a connector payload this workflow owns. Anything else is the
#: caller's and is stored verbatim, which is what keeps a team from needing a
#: migration to add a field to a monitored connector.
_CONNECTOR_RESERVED = frozenset(
    {"vendor", "label", "room_id", "concurrency", "paused", "limit_name", "quota_policy"}
)

#: Bounds on the per-connector concurrency the researched step 4 lowers.
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 64

#: The connector-declared quota policy each researched vendor ships with.
#: This is the researched extensibility claim made concrete: "because monitoring
#: is fed by a connector-declared quota policy (the same object used in W13),
#: there is exactly one place to teach the system a new vendor's numbers" - and
#: this mapping is that place for the vendors the research gives numbers for.
DEFAULT_POLICIES: dict[str, dict[str, Any]] = {
    "salesforce": {
        "kind": "daily",
        "surfaces": ["limit_info_header", "limits_resource"],
        "window_seconds": None,
    },
    "hubspot": {
        "kind": "daily+window",
        "surfaces": ["rate_limit_headers", "account_information"],
        "window_seconds": 10,
    },
    "dataverse": {
        "kind": "unknown",
        "surfaces": [],
        "window_seconds": None,
    },
}

#: The dashboard's default telemetry window, in seconds.
#: [inference] success-window-is-24h: Salesforce's own usage data covers "the
#: last 24 hours" without Enhanced Usage Metrics, so the room defaults to a
#: window its readings can be compared against.
DEFAULT_WINDOW_SECONDS = 86_400.0

_WINDOW_MIN = 60.0
_WINDOW_MAX = 30.0 * 86_400.0

#: Page size and scan ceiling, as in the other store-facing engines.
_PAGE = 1000
_MAX_RECORDS = 20_000


class IntegrationMonitor:
    """The room metrics store, the dashboard, and the alert rules over it."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        self._now = now or _utc_now

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: Any) -> dict[str, Any]:
        """The room, or a refusal the HTTP layer turns into a 404."""
        text = str(room_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != ROOM_COLLECTION:
            raise UnknownRoom(text or "(none)")
        return record

    # -- connectors ---------------------------------------------------------- #

    def register_connector(
        self, payload: Mapping[str, Any], *, room_id: str | None = None, actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a connector for monitoring.

        [sourced] the dashboard reads "remaining daily + burst quota, sync
        success rate, mean latency, error-class breakdown, and the live
        change-stream lag" - for *a connection*, so the unit of monitoring is a
        connector, registered on the room whose Integrations page owns it.

        The vendor must already have numbers to read: an unregistered vendor is
        refused with the researched extensibility point, which says a new
        vendor is taught in exactly one place (:data:`DEFAULT_POLICIES`), not
        per-connection. A room that passes no vendor gets the connector's
        declared policy of its vendor, which the caller never has to repeat.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the connector payload must be a JSON object; got {type(payload).__name__}")
        room = self.require_room(room_id or payload.get("room_id"))
        vendor = require_vendor(payload.get("vendor"))
        label = str(payload.get("label") or "").strip() or f"{vendor} connector"

        data = {
            **{k: v for k, v in payload.items() if k not in _CONNECTOR_RESERVED},
            "vendor": vendor,
            "label": label,
            "limit_name": str(payload.get("limit_name") or "") or None,
            "concurrency": _concurrency(payload.get("concurrency", 4)),
            "paused": bool(payload.get("paused", False)),
            "quota_policy": self._policy(vendor, payload.get("quota_policy")),
        }
        record = self.store.create(
            CONNECTOR_COLLECTION, data, room_id=room["id"], actor=actor, source=source
        )
        return self.connector_view(record)

    def _policy(self, vendor: str, patch: Any) -> dict[str, Any]:
        base = dict(DEFAULT_POLICIES.get(vendor) or {"kind": "unknown", "surfaces": [], "window_seconds": None})
        if patch is None:
            return base
        if not isinstance(patch, Mapping):
            raise InvalidPayload("quota_policy must be a JSON object")
        base.update({k: v for k, v in patch.items() if k in ("kind", "surfaces", "window_seconds")})
        if not isinstance(base.get("surfaces"), list):
            raise InvalidPayload("quota_policy.surfaces must be a list")
        return base

    def connector(self, connector_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One connector, with its current readings, or :class:`UnknownConnector`."""
        record = self._require_connector(connector_id, room_id=room_id)
        return self.connector_view(record)

    def list_connectors(
        self, *, room_id: str | None = None, vendor: str | None = None, paused: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Monitored connectors, newest first, as dashboard rows."""
        if room_id:
            self.require_room(room_id)
        wanted = require_vendor(vendor) if vendor else None
        records, _truncated = self._scan(CONNECTOR_COLLECTION, room_id=room_id)
        selected = [
            record
            for record in records
            if (not wanted or (record.get("data") or {}).get("vendor") == wanted)
            and (paused is None or bool((record.get("data") or {}).get("paused")) == paused)
        ]
        selected.sort(key=lambda r: (str((r.get("data") or {}).get("label") or ""), str(r["id"])))
        return [self.connector_view(record) for record in selected[: max(1, min(int(limit), 1000))]]

    def update_connector(
        self, connector_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str,
    ) -> dict[str, Any]:
        """Patch a connector: pause, resume, lower concurrency, retarget a limit.

        [sourced] "If a connector is starved, the operator lowers its
        concurrency or pauses it from the same page." That is the whole of the
        researched control surface and it is deliberately here, next to the
        dashboard that shows the starvation, so the operator does not have to
        find another page to act on what they can see.
        """
        if not isinstance(patch, Mapping):
            raise InvalidPayload(f"the patch must be a JSON object; got {type(patch).__name__}")
        record = self._require_connector(connector_id)
        data = dict(record.get("data") or {})
        changed: list[str] = []

        if "paused" in patch:
            wanted = bool(patch["paused"])
            if wanted != bool(data.get("paused")):
                data["paused"] = wanted
                data["paused_at"] = self._now() if wanted else None
                changed.append("paused")
        if "concurrency" in patch:
            data["concurrency"] = _concurrency(patch["concurrency"])
            changed.append("concurrency")
        if "label" in patch:
            label = str(patch["label"] or "").strip()
            if not label:
                raise InvalidPayload("label cannot be empty; delete the connector instead")
            data["label"] = label
            changed.append("label")
        if "limit_name" in patch:
            data["limit_name"] = str(patch["limit_name"] or "").strip() or None
            changed.append("limit_name")
        if "quota_policy" in patch:
            data["quota_policy"] = self._policy(str(data.get("vendor") or ""), patch["quota_policy"])
            changed.append("quota_policy")

        unknown = sorted(set(patch) - {"paused", "concurrency", "label", "limit_name", "quota_policy"})
        if unknown:
            raise InvalidPayload(
                f"unknown patch key(s) {', '.join(unknown)}; the monitored fields are "
                "paused, concurrency, label, limit_name and quota_policy - anything else is "
                "yours to store verbatim at registration"
            )

        updated = self.store.update(record["id"], data, actor=actor, source=source)
        view = self.connector_view(updated)
        view["changed"] = changed
        return view

    def remove_connector(
        self, connector_id: str, *, actor: str | None = None, source: str
    ) -> None:
        """Soft-delete a connector. Its observations outlive it."""
        record = self._require_connector(connector_id)
        self.store.delete(record["id"], actor=actor, source=source)

    # -- quota --------------------------------------------------------------- #

    def record_quota(
        self, connector_id: str, payload: Mapping[str, Any], *, actor: str | None = None,
        source: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        """One quota reading in, one normalised observation stored.

        [sourced] the data flow: "vendor quota metadata (headers + limits
        endpoints) + connector telemetry -> room metrics store".

        The connector hands in what its vendor answered - the header string or
        the resource body - and the room normalises it into the researched
        pair. The observation carries the reading and nothing about the
        connector's own state: a reading is a measurement, not an event.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the quota payload must be a JSON object; got {type(payload).__name__}")
        connector = self._require_connector(connector_id)
        vendor = str((connector.get("data") or {}).get("vendor") or "")
        reading = quota_module.normalise(
            vendor,
            _require(payload, "surface"),
            payload,
            limit_name=(connector.get("data") or {}).get("limit_name"),
            now=now or self._now_datetime(),
        )
        data = {**reading, "connector_id": connector["id"]}
        record = self.store.create(QUOTA_COLLECTION, data, room_id=connector["room_id"], actor=actor, source=source)
        return {"recorded": True, "observation": self._observation_view(record)}

    def quota_view(self, connector_id: str) -> dict[str, Any]:
        """One connector's latest pair, the surfaces it was read from, and its delta."""
        connector = self._require_connector(connector_id)
        records, _truncated = self._scan(QUOTA_COLLECTION)
        mine = [
            record
            for record in records
            if str((record.get("data") or {}).get("connector_id") or "") == connector["id"]
        ]
        mine.sort(key=lambda r: str((r.get("data") or {}).get("observed_at") or ""), reverse=True)
        latest = self._observation_view(mine[0]) if mine else None
        previous = self._observation_view(mine[1]) if len(mine) > 1 else None
        delta = None
        if latest and previous:
            delta = {
                "daily_remaining": _delta(latest, previous, "daily"),
                "window_remaining": _delta(latest, previous, "window"),
            }
        surfaces: dict[str, int] = {}
        for record in mine:
            surface = str((record.get("data") or {}).get("surface") or "")
            surfaces[surface] = surfaces.get(surface, 0) + 1
        return {
            "connector_id": connector["id"],
            "count": len(mine),
            "by_surface": surfaces,
            "latest": latest,
            "previous": previous,
            "delta": delta,
            "limit_name": (connector.get("data") or {}).get("limit_name"),
        }

    # -- telemetry ------------------------------------------------------------ #

    def record_telemetry(
        self, connector_id: str, payload: Mapping[str, Any], *, actor: str | None = None,
        source: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        """The calls a connector made, stored one record per call.

        [sourced] "connector telemetry -> room metrics store", and the
        dashboard reads "sync success rate, mean latency, error-class
        breakdown". The samples are stored individually rather than folded
        into a counter, so a new aggregate is a query and not a backfill.
        """
        connector = self._require_connector(connector_id)
        samples = health.check_samples(payload)
        moment = now or self._now_datetime()
        written: list[dict[str, Any]] = []
        for sample in samples:
            at = parse_instant(sample.get("at")) or moment
            check_not_ahead(at, now=moment)
            data = {
                "connector_id": connector["id"],
                "ok": sample["ok"],
                "status": sample["status"],
                "error_class": sample["error_class"],
                "latency_ms": sample["latency_ms"],
                "at": iso(at),
            }
            written.append(
                self.store.create(
                    TELEMETRY_COLLECTION, data, room_id=connector["room_id"], actor=actor, source=source
                )
            )
        return {
            "recorded": len(written),
            "aggregate": health.aggregate(
                [record.get("data") or {} for record in self._telemetry_records(connector["id"])],
                now=moment,
            ),
        }

    # -- change-stream lag ----------------------------------------------------- #

    def record_stream(
        self, connector_id: str, payload: Mapping[str, Any], *, actor: str | None = None,
        source: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        """One lag observation, stored with how it was computed.

        [sourced] "the live change-stream lag", read through
        :func:`dsr.integ_monitor.health.check_lag`, which takes either a direct
        ``lag_seconds`` or the two instants the lag is the difference between.
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the stream payload must be a JSON object; got {type(payload).__name__}")
        connector = self._require_connector(connector_id)
        moment = now or self._now_datetime()
        lag = health.check_lag(payload, now=moment)
        data = {
            "connector_id": connector["id"],
            "lag_seconds": lag,
            "observed_at": iso(parse_instant(payload.get("observed_at")) or moment),
            "source_event_at": iso(parse_instant(payload.get("source_event_at"))),
        }
        record = self.store.create(
            STREAM_COLLECTION, data, room_id=connector["room_id"], actor=actor, source=source
        )
        previous = self._latest_stream(connector["id"], before=record["id"])
        return {
            "recorded": True,
            "observation": self._stream_view(record),
            "previous_lag_seconds": (previous.get("data") or {}).get("lag_seconds") if previous else None,
        }

    # -- change tracking (the Dataverse drift surface) --------------------------- #

    def record_change_tracking(
        self, payload: Mapping[str, Any], *, room_id: str | None = None, actor: str | None = None,
        source: str, now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record the Dataverse ``EntityDefinitions`` audit, and drift.

        [sourced] "GET /api/data/v9.2/EntityDefinitions?$select=SchemaName&$filter="
        "ChangeTrackingEnabled eq true" to audit which tables are being tracked,
        and "Microsoft.Dynamics.CRM.globalmetadataversion | The value changes when
        any schema change occurs, indicating that you might need to refresh any
        schema data that your application cached."

        The version is compared against the previous record for this room: a
        change is the researched schema-drift signal, recorded as a flag with
        both versions rather than an error, because the vendor's own sentence
        is advisory ("you might need to refresh").
        """
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the payload must be a JSON object; got {type(payload).__name__}")
        vendor = require_vendor(payload.get("vendor") or "dataverse")
        if vendor != "dataverse":
            raise InvalidPayload(
                f"the change-tracking audit is Dataverse's EntityDefinitions surface; {vendor} "
                "has no sourced equivalent"
            )
        room = self.require_room(room_id or payload.get("room_id"))
        entities = _require_entities(payload)
        version = _require(payload, "globalmetadataversion")
        moment = now or self._now_datetime()

        previous, _truncated = self._latest_change_tracking(room["id"])
        previous_version = (previous or {}).get("data", {}).get("globalmetadataversion")
        drift = (
            previous_version is not None
            and str(previous_version) != str(version)
        )
        data = {
            "vendor": vendor,
            "globalmetadataversion": str(version),
            "entities": entities,
            "tracked": sum(1 for entity in entities if entity.get("change_tracking_enabled")),
            "drift": drift,
            "previous_version": str(previous_version) if previous_version is not None else None,
            "observed_at": iso(parse_instant(first_present(payload, ("observed_at", "at"))) or moment),
        }
        record = self.store.create(CHANGE_COLLECTION, data, room_id=room["id"], actor=actor, source=source)
        view = self._change_view(record)
        if drift:
            view["note"] = (
                "the schema version moved from "
                f"{previous_version} to {version}: you might need to refresh any schema "
                "data this room cached (the researched annotation, quoted)"
            )
        return view

    def change_tracking_view(self, room_id: str, *, limit: int = 50) -> dict[str, Any]:
        """The room's change-tracking audits, newest first, drift first."""
        room = self.require_room(room_id)
        records, _truncated = self._scan(CHANGE_COLLECTION, room_id=room["id"])
        records.sort(key=lambda r: str((r.get("data") or {}).get("observed_at") or ""), reverse=True)
        capped = records[: max(1, min(int(limit), 1000))]
        drifts = [record for record in records if (record.get("data") or {}).get("drift")]
        latest = self._change_view(capped[0]) if capped else None
        return {
            "room_id": room["id"],
            "count": len(capped),
            "total": len(records),
            "drift_count": len(drifts),
            "latest": latest,
            "audits": [self._change_view(record) for record in capped],
        }

    # -- alert rules ------------------------------------------------------------ #

    def create_rule(
        self, payload: Mapping[str, Any], *, room_id: str | None = None, actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Store an alert rule. [sourced] "room alert rules (Slack/email/webhook)"."""
        if not isinstance(payload, Mapping):
            raise InvalidPayload(f"the rule payload must be a JSON object; got {type(payload).__name__}")
        room = self.require_room(room_id or payload.get("room_id"))
        data = alert_module.check_rule(payload)
        if payload.get("label"):
            data["label"] = str(payload["label"]).strip()
        record = self.store.create(
            RULE_COLLECTION, data, room_id=room["id"], actor=actor, source=source
        )
        return self._rule_view(record)

    def list_rules(self, room_id: str, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        """The room's alert rules, creation order, as the page reads them."""
        self.require_room(room_id)
        records, _truncated = self._scan(RULE_COLLECTION, room_id=room_id)
        records.sort(key=lambda r: str(r.get("created_at") or ""))
        selected = [
            record
            for record in records
            if enabled is None or bool((record.get("data") or {}).get("enabled", True)) == enabled
        ]
        return [self._rule_view(record) for record in selected]

    def update_rule(
        self, rule_id: str, patch: Mapping[str, Any], *, room_id: str | None = None,
        actor: str | None = None, source: str,
    ) -> dict[str, Any]:
        """Patch a rule. The fire history survives the patch."""
        if not isinstance(patch, Mapping):
            raise InvalidPayload(f"the patch must be a JSON object; got {type(patch).__name__}")
        record = self._require_rule(rule_id, room_id=room_id)
        current = dict(record.get("data") or {})
        merged = alert_module.merge_rule(current, patch)
        if "label" in patch:
            merged["label"] = str(patch["label"] or "").strip() or None
        unknown = set(patch) - {"metric", "threshold", "channels", "cooldown_minutes",
                                 "enabled", "vendor", "label"}
        if unknown:
            raise InvalidPayload(
                f"unknown patch key(s) {', '.join(sorted(unknown))}; a rule's tunable fields are "
                "metric, threshold, channels, cooldown_minutes, enabled, vendor and label"
            )
        merged.update({k: v for k, v in current.items() if k not in merged})
        merged.update({"fires": current.get("fires") or [], "last_fired_at": current.get("last_fired_at"),
                       "fire_count": current.get("fire_count", 0)})
        updated = self.store.update(record["id"], merged, actor=actor, source=source)
        return self._rule_view(updated)

    def delete_rule(
        self, rule_id: str, *, room_id: str | None = None, actor: str | None = None, source: str
    ) -> None:
        record = self._require_rule(rule_id, room_id=room_id)
        self.store.delete(record["id"], actor=actor, source=source)

    # -- evaluation, and the dashboard ------------------------------------------ #

    def evaluate_alerts(
        self, room_id: str, *, actor: str | None = None, source: str, now: datetime | None = None,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> dict[str, Any]:
        """The researched automation, evaluated and fired.

        [sourced] "alert rules fire when remaining budget crosses a threshold
        or when the change-stream lag exceeds N seconds."

        Rules that fire have the fire written onto them - ``last_fired_at``,
        the capped history, the count - which is what starts the cooldown.
        Rules that are disabled, out of scope, or mid-cooldown say so instead.
        """
        room = self.require_room(room_id)
        values = self._metric_values(room["id"], window_seconds=window_seconds)
        rules, _truncated = self._scan(RULE_COLLECTION, room_id=room["id"])
        rules.sort(key=lambda r: str(r.get("created_at") or ""))
        result = alert_module.evaluate_rules(
            [self._rule_entry(record) for record in rules], values, now=now or self._now_datetime()
        )
        fired_by_id = {str(entry["rule_id"]): entry for entry in result["fired"]}
        for record in rules:
            entry = fired_by_id.get(str(record["id"]))
            if entry is None:
                continue
            self.store.update(
                record["id"],
                alert_module.record_fire(record.get("data") or {}, entry, moment=now or self._now_datetime()),
                actor=actor,
                source=source,
            )
        return result

    def dashboard(self, room_id: str, *, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> dict[str, Any]:
        """The researched dashboard, in the order the research lists it.

        [sourced] "Dashboard shows remaining daily + burst quota, sync success
        rate, mean latency, error-class breakdown (validation / throttle / auth
        / vendor-5xx), and the live change-stream lag." Then the controls: the
        connectors an operator can pause or throttle, and the alert rules with
        what they would fire now (a dry run - firing is :meth:`evaluate_alerts`).

        Computed on read, like every monitor in this product: a page that only a
        timer can refresh is a page nobody can debug.
        """
        room = self.require_room(room_id)
        _check_window(window_seconds)
        rows = self.list_connectors(room_id=room["id"], limit=1000)
        values = self._metric_values(room["id"], window_seconds=window_seconds)
        rules = self.list_rules(room["id"])
        preview = alert_module.evaluate_rules(
            [{"id": rule["id"], "data": rule} for rule in rules], values,
            now=self._now_datetime(),
        )
        return {
            "room_id": room["id"],
            "as_of": iso(self._now_datetime()),
            "window_seconds": window_seconds,
            "connectors": rows,
            "by_vendor": {
                vendor: sum(1 for row in rows if row["vendor"] == vendor)
                for vendor in sorted({row["vendor"] for row in rows})
            },
            "paused": sum(1 for row in rows if row["paused"]),
            "alert_rules": rules,
            "alert_preview": preview,
            "quotas": published_vocabulary()["quotas"],
        }

    # -- internals ---------------------------------------------------------------- #

    def _metric_values(
        self, room_id: str, *, window_seconds: float = DEFAULT_WINDOW_SECONDS
    ) -> list[dict[str, Any]]:
        """One reading per connector per metric, for the rules to cross."""
        rows = self.list_connectors(room_id=room_id, limit=1000)
        values: list[dict[str, Any]] = []
        for row in rows:
            if row["paused"]:
                continue
            latest = self._latest_quota(row["id"])
            lag = self._latest_lag(row["id"])
            for metric in ("daily_remaining", "window_remaining"):
                value = alert_module.metric_value(
                    metric,
                    daily=(latest or {}).get("daily"),
                    window=(latest or {}).get("window"),
                )
                values.append(
                    {
                        "connector_id": row["id"],
                        "label": row["label"],
                        "vendor": row["vendor"],
                        "metric": metric,
                        "value": value,
                        "known": value is not None,
                        "observed_at": (latest or {}).get("observed_at"),
                    }
                )
            values.append(
                {
                    "connector_id": row["id"],
                    "label": row["label"],
                    "vendor": row["vendor"],
                    "metric": "stream_lag",
                    "value": lag,
                    "known": lag is not None,
                    "observed_at": None,
                }
            )
        return values

    def _latest_quota(self, connector_id: str) -> dict[str, Any] | None:
        records, _truncated = self._scan(QUOTA_COLLECTION)
        mine = [
            record.get("data") or {}
            for record in records
            if str((record.get("data") or {}).get("connector_id") or "") == connector_id
        ]
        mine.sort(key=lambda d: str(d.get("observed_at") or ""), reverse=True)
        return mine[0] if mine else None

    def _latest_lag(self, connector_id: str) -> float | None:
        records, _truncated = self._scan(STREAM_COLLECTION)
        mine = [
            record.get("data") or {}
            for record in records
            if str((record.get("data") or {}).get("connector_id") or "") == connector_id
        ]
        mine.sort(key=lambda d: str(d.get("observed_at") or ""), reverse=True)
        return mine[0].get("lag_seconds") if mine else None

    def _latest_stream(self, connector_id: str, *, before: str) -> dict[str, Any] | None:
        records, _truncated = self._scan(STREAM_COLLECTION)
        mine = [
            record
            for record in records
            if str((record.get("data") or {}).get("connector_id") or "") == connector_id
            and str(record.get("id")) != str(before)
        ]
        mine.sort(key=lambda r: str((r.get("data") or {}).get("observed_at") or ""), reverse=True)
        return mine[0] if mine else None

    def _latest_change_tracking(self, room_id: str) -> tuple[dict[str, Any] | None, bool]:
        records, truncated = self._scan(CHANGE_COLLECTION, room_id=room_id)
        records.sort(key=lambda r: str((r.get("data") or {}).get("observed_at") or ""), reverse=True)
        return (records[0] if records else None), truncated

    def _telemetry_records(self, connector_id: str) -> list[dict[str, Any]]:
        """A connector's stored samples, newest first, capped at the scan ceiling."""
        records, _truncated = self._scan(TELEMETRY_COLLECTION)
        mine = [
            record
            for record in records
            if str((record.get("data") or {}).get("connector_id") or "") == connector_id
        ]
        mine.sort(key=lambda r: str((r.get("data") or {}).get("at") or ""), reverse=True)
        return mine[: _MAX_RECORDS]

    def telemetry_view(self, connector_id: str, *, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> dict[str, Any]:
        """One connector's aggregate, without recording anything."""
        connector = self._require_connector(connector_id)
        _check_window(window_seconds)
        records = self._telemetry_records(connector["id"])
        aggregate = health.aggregate(
            [record.get("data") or {} for record in records],
            window_seconds=window_seconds,
            now=self._now_datetime(),
        )
        return {"connector_id": connector["id"], **aggregate}

    def _scan(self, collection: str, *, room_id: str | None = None) -> tuple[list[dict[str, Any]], bool]:
        """Every live record in a collection, paged on the id so nothing is skipped."""
        records: list[dict[str, Any]] = []
        offset = 0
        while len(records) < _MAX_RECORDS:
            page = self.store.list(
                collection, room_id=room_id, limit=_PAGE, offset=offset, order_by="id", descending=False
            )
            if not page:
                break
            records.extend(page)
            if len(page) < _PAGE:
                break
            offset += len(page)
        return records, len(records) >= _MAX_RECORDS

    def _require_connector(self, connector_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        text = str(connector_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != CONNECTOR_COLLECTION:
            raise UnknownConnector(text or "(none)")
        if room_id and str(record.get("room_id")) != str(room_id):
            raise UnknownConnector(f"{text} is not on room {room_id}")
        return record

    def _require_rule(self, rule_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        text = str(rule_id or "").strip()
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != RULE_COLLECTION:
            raise UnknownRule(text or "(none)")
        if room_id and str(record.get("room_id")) != str(room_id):
            raise UnknownRule(f"{text} is not on room {room_id}")
        return record

    def connector_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A connector as the dashboard row the page renders."""
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "vendor": data.get("vendor"),
            "label": data.get("label"),
            "limit_name": data.get("limit_name"),
            "concurrency": data.get("concurrency"),
            "paused": bool(data.get("paused")),
            "paused_at": data.get("paused_at"),
            "quota_policy": data.get("quota_policy"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _observation_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": record.get("id"),
            "connector_id": (record.get("data") or {}).get("connector_id"),
            "vendor": (record.get("data") or {}).get("vendor"),
            "surface": (record.get("data") or {}).get("surface"),
            "observed_at": (record.get("data") or {}).get("observed_at"),
            "daily": (record.get("data") or {}).get("daily"),
            "window": (record.get("data") or {}).get("window"),
            "notes": (record.get("data") or {}).get("notes") or [],
        }

    def _stream_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "connector_id": data.get("connector_id"),
            "lag_seconds": data.get("lag_seconds"),
            "observed_at": data.get("observed_at"),
            "source_event_at": data.get("source_event_at"),
        }

    def _change_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "vendor": data.get("vendor"),
            "globalmetadataversion": data.get("globalmetadataversion"),
            "previous_version": data.get("previous_version"),
            "drift": data.get("drift"),
            "tracked": data.get("tracked"),
            "entities": data.get("entities") or [],
            "observed_at": data.get("observed_at"),
            "note": None,
        }

    def _rule_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "label": data.get("label"),
            "metric": data.get("metric"),
            "comparison": data.get("comparison"),
            "threshold": data.get("threshold"),
            "channels": data.get("channels") or [],
            "cooldown_minutes": data.get("cooldown_minutes"),
            "enabled": bool(data.get("enabled", True)),
            "vendor": data.get("vendor"),
            "last_fired_at": data.get("last_fired_at"),
            "fire_count": data.get("fire_count", 0),
            "fires": (data.get("fires") or [])[-10:],
            "created_at": record.get("created_at"),
        }

    @staticmethod
    def _rule_entry(record: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": record.get("id"), "data": record.get("data") or {}}

    # -- convenience: the clock -------------------------------------------------- #

    def _now_datetime(self) -> datetime:
        moment = self._now()
        parsed = parse_instant(moment()) if callable(moment) else parse_instant(moment)
        return parsed or datetime.now(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _concurrency(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidPayload(f"concurrency must be a whole number; got {value!r}") from exc
    if not CONCURRENCY_MIN <= number <= CONCURRENCY_MAX:
        raise InvalidPayload(
            f"concurrency must be between {CONCURRENCY_MIN} and {CONCURRENCY_MAX}; got {number}. "
            "The bounds are refusals rather than clamps, so an operator typing 0 finds out now."
        )
    return number


def _check_window(window_seconds: float) -> None:
    try:
        window = float(window_seconds)
    except (TypeError, ValueError) as exc:
        raise InvalidPayload(f"window_seconds must be a number; got {window_seconds!r}") from exc
    if not _WINDOW_MIN <= window <= _WINDOW_MAX:
        raise InvalidPayload(
            f"window_seconds must be between {_WINDOW_MIN:.0f} and {_WINDOW_MAX:.0f}; got {window}"
        )


def _delta(latest: Mapping[str, Any], previous: Mapping[str, Any], key: str) -> float | None:
    now_half = (latest.get(key) or {}).get("remaining")
    was_half = (previous.get(key) or {}).get("remaining")
    if now_half is None or was_half is None:
        return None
    return round(float(now_half) - float(was_half), 4)


def _require(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, "", [], {}):
        raise InvalidPayload(f"{key} is required; an observation without it cannot be read back")
    return value


def _require_entities(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    entities = payload.get("entities")
    if not isinstance(entities, list) or not entities:
        raise InvalidPayload(
            "entities is required: the researched audit is the EntityDefinitions answer, "
            "and an answer without its tables is not an audit"
        )
    prepared: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, Mapping) or not entity.get("schema_name"):
            raise InvalidPayload(f"entity {index} must be an object with a schema_name")
        prepared.append(
            {
                "schema_name": str(entity["schema_name"]),
                "change_tracking_enabled": bool(entity.get("change_tracking_enabled", False)),
            }
        )
    return prepared


__all__ = [
    "IntegrationMonitor",
    "DEFAULT_POLICIES",
    "DEFAULT_WINDOW_SECONDS",
    "CONCURRENCY_MIN",
    "CONCURRENCY_MAX",
]
