"""The store-facing surface of WF-021: record engagement, classify, prioritise.

:class:`TrendHealth` is the only class in this package that touches a
:class:`~dsr.store.RecordStore`. It holds nothing but the store handle, so it is
built per request from a dependency and the whole workflow is unit-testable
against a temporary database without the app.

Two rules it exists to enforce:

**Every write names the route that served it.** ``source`` is a required keyword
on both write methods, with no default. An audit row that says ``"record event"``
cannot be traced back to the request that caused it, and an audit row naming a
path the app no longer serves is worse than no audit row - the same defect the
feature contract calls out by name. The routes build it from ``router.prefix``.

**Nothing is written by a read.** Classifying a room creates no record, so the
audit log for this collection is exactly the history of engagement that was
actually reported, and a dashboard that was loaded nine hundred times leaves nine
hundred entries in neither the log nor the data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.store import RecordStore

from dsr.trend_health import rules as rules_module
from dsr.trend_health.errors import InvalidSort, TrendError, UnknownRoom
from dsr.trend_health.timestamps import check_not_ahead, parse_timestamp, timestamp_of
from dsr.trend_health.vocabulary import (
    AUDIENCES,
    ENGAGEMENT_COLLECTION,
    ENGAGEMENT_EVENT_TYPES,
    ORDER_FORM_PREFIX,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    ROOM_COLLECTION,
    TREND_LABELS,
    TREND_RULES,
    TREND_VALUES,
    dashboard_fields,
    is_client_view,
    require_audience,
    require_event_type,
)
from dsr.trend_health.windows import EngagementEvent, classify, events_from_records, rank_of, sort_key

#: Where the event's room may be named, in order of preference. ``workspaceId`` is
#: the researched camelCase; the snake_case spelling is here for a client that
#: cannot send camelCase at all.
ROOM_KEYS: tuple[str, ...] = ("room_id", "workspaceId", "workspace_id", "workspace")

#: Where the event type may be named. A Dock webhook puts the name in ``type``.
TYPE_KEYS: tuple[str, ...] = ("type", "event", "event_type", "eventType", "name")

#: Keys the classifier owns. Anything else in a payload is the caller's own and is
#: stored verbatim, which is what keeps a team from needing a migration to add a
#: field to an engagement event.
_RESERVED = frozenset(
    {
        "type",
        "event",
        "event_type",
        "eventType",
        "name",
        "occurred_at",
        "occurredAt",
        "at",
        "timestamp",
        "audience",
        "internal",
        "room_id",
        "workspaceId",
        "workspace_id",
        "workspace",
    }
)

#: Page size for the event scan. The store caps a single read at 1000.
_PAGE = 1000

#: A ceiling on how many events one room's classification will read, so a
#: pathological room cannot turn a dashboard load into an unbounded query. A
#: response that hits it says so rather than quietly reporting a partial count.
_MAX_EVENTS = 50_000

#: The keys the dashboard can sort by, mapped to how each is compared.
SORT_KEYS: dict[str, str] = {
    "trend": "trend",
    "owner": "text",
    "team": "text",
    "name": "text",
    "created": "instant",
    "last_client_view": "instant",
    "engagement": "number",
}

#: The columns the research says the dashboard filters and sorts by: "owner,
#: workspace creation date, recent client activity", plus the Trend column itself
#: and the name a rep is looking at.
DASHBOARD_SORTS: tuple[str, ...] = tuple(SORT_KEYS)


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _text(value: Any) -> str:
    return str(value or "").strip()


class TrendHealth:
    """Engagement health for every workspace, read off one audited store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: Any) -> dict[str, Any]:
        """The room, or a refusal the HTTP layer turns into a 404."""
        text = _text(room_id)
        record = self.store.get(text) if text else None
        if record is None or record.get("collection") != ROOM_COLLECTION:
            raise UnknownRoom(text or "(none)")
        return record

    def list_rooms(self, *, limit: int = 1000) -> tuple[list[dict[str, Any]], bool]:
        """Every live room, and whether the list was cut short.

        Paged on the record id rather than on ``created_at``: ids are unique, so
        an offset page cannot skip or repeat a row when two events share a
        millisecond, which is exactly what an activity importer produces.
        """
        rooms: list[dict[str, Any]] = []
        offset = 0
        while len(rooms) < limit:
            page = self.store.list(
                ROOM_COLLECTION, limit=_PAGE, offset=offset, order_by="id", descending=False
            )
            if not page:
                return rooms, False
            rooms.extend(page)
            if len(page) < _PAGE:
                return rooms, False
            offset += len(page)
        return rooms[:limit], True

    # -- rules -------------------------------------------------------------- #

    def rules(self) -> tuple[dict[str, Any], str]:
        """The rules in force, and whether they came from the defaults."""
        record = self.store.get(RULES_RECORD_ID)
        data = record.get("data") if record and record.get("collection") == RULES_COLLECTION else None
        return rules_module.effective(data if isinstance(data, Mapping) else None)

    def rules_view(self) -> dict[str, Any]:
        rules, origin = self.rules()
        return rules_module.describe(rules, origin)

    def save_rules(
        self,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Store a rules override. Audited, and the row is the whole of it.

        A patch, not a replacement: retuning the Hot floor should not silently
        reset Warm or drop an override somebody else set. ``source`` has no
        default because the route that served this is the only thing that knows
        it.
        """
        current, _origin = self.rules()
        merged = rules_module.merge(current, patch)
        existing = self.store.get(RULES_RECORD_ID)
        if existing is None:
            record = self.store.create(
                RULES_COLLECTION, merged, record_id=RULES_RECORD_ID, actor=actor, source=source
            )
        else:
            record = self.store.update(RULES_RECORD_ID, merged, actor=actor, source=source)
        return {"updated": True, "record": record, **rules_module.describe(merged, "override")}

    # -- events ------------------------------------------------------------- #

    def record_event(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record one researched engagement event.

        The event is stored before anything reads it, so the bucket moves as soon
        as the event lands. What the payload may contain beyond the researched
        fields is not restricted: everything the caller sends that is not one of
        the keys the classifier owns is stored as it arrived, and is immediately
        queryable with ``?where=`` through the dynamic index, because a team that
        wants ``data.billing.seat`` on an event ships a record rather than a
        migration.
        """
        if not isinstance(payload, Mapping):
            raise TrendError(f"the event payload must be a JSON object; got {type(payload).__name__}")

        moment = now or datetime.now(timezone.utc)
        scope = self._room_id_of(payload, room_id)
        room = self.require_room(scope)
        event_type = require_event_type(_first(payload, TYPE_KEYS))
        audience = require_audience(payload.get("audience"), internal=payload.get("internal"))
        occurred_at, defaulted = timestamp_of(payload, default=moment)
        check_not_ahead(occurred_at, now=moment)

        extra = {key: value for key, value in payload.items() if key not in _RESERVED}
        data: dict[str, Any] = {
            **extra,
            "type": event_type,
            "occurred_at": occurred_at.isoformat(timespec="milliseconds"),
            "occurred_at_defaulted": defaulted,
            "audience": audience,
            "engagement": True,
            "client_view": is_client_view(event_type),
        }
        record = self.store.create(
            ENGAGEMENT_COLLECTION, data, room_id=room["id"], actor=actor, source=source
        )
        return record

    def _room_id_of(self, payload: Mapping[str, Any], room_id: str | None) -> str:
        candidate = _text(room_id) or _text(_first(payload, ROOM_KEYS))
        if not candidate:
            raise TrendError(
                "the event is not scoped to a workspace; send room_id, or the researched "
                f"{ROOM_KEYS[1]} in the body"
            )
        return candidate

    def list_events(
        self,
        *,
        room_id: str | None = None,
        event_type: str | None = None,
        audience: str | None = None,
        client_view: bool | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recorded engagement events, newest first.

        The filters are the ones the research's own surfaces need: the room, the
        event shape, whether it was a client view, and a time range. Anything
        else a caller wants is a ``?where=`` against the generic records API,
        because the payloads are arbitrary JSON by design.

        The filters are applied to the row's own values rather than through the
        dynamic index, and derived rather than trusted: ``client_view`` is
        recomputed from the event type and ``audience`` is defaulted the same way
        intake defaults it, so a row written straight through ``POST
        /api/records/workspace_engagement`` - which the schema-flexible store
        invites - lists and filters exactly like one this workflow recorded.
        Indexing on a flag that only some writers set would quietly hide those
        rows from half the views and show them in the others.

        Omitting ``room_id`` reads every room, which is what a portfolio-wide
        activity feed wants; it is bounded by the same scan ceiling the
        classifier uses, and the response says so if the ceiling was reached.
        """
        wanted_type = require_event_type(event_type) if event_type else None
        wanted_audience = require_audience(audience) if audience else None
        start = parse_timestamp(since, required=False) if since else None
        end = parse_timestamp(until, required=False) if until else None

        records, _events, _skipped, _truncated = self._scan(_text(room_id) or None)

        selected: list[dict[str, Any]] = []
        for record in records:
            data = record.get("data") or {}
            if wanted_type is not None and str(data.get("type") or "") != wanted_type:
                continue
            if wanted_audience is not None and require_audience(data.get("audience")) != wanted_audience:
                continue
            if client_view is not None and is_client_view(str(data.get("type") or "")) != client_view:
                continue
            at = parse_timestamp(data.get("occurred_at"), required=False)
            if start is not None and (at is None or at < start):
                continue
            if end is not None and (at is None or at > end):
                continue
            selected.append(record)

        selected.sort(
            key=lambda record: str((record.get("data") or {}).get("occurred_at") or ""), reverse=True
        )
        return selected[: max(1, min(int(limit), _PAGE))]

    def _scan(self, room_id: str | None = None) -> tuple[list[dict[str, Any]], list[EngagementEvent], int, bool]:
        """Every event on one room - or on every room: the rows, the ladder input,
        and what was skipped.

        Paged on the record id rather than on ``created_at``: ids are unique, so
        an offset page cannot skip or repeat a row when two events share a
        millisecond, which is exactly what an activity importer produces. The rows
        and the ladder input come from the same read so the two can never
        disagree about what is on the room.
        """
        records: list[dict[str, Any]] = []
        truncated = False
        offset = 0
        while len(records) < _MAX_EVENTS:
            page = self.store.list(
                ENGAGEMENT_COLLECTION,
                room_id=room_id,
                limit=_PAGE,
                offset=offset,
                order_by="id",
                descending=False,
            )
            if not page:
                break
            offset += len(page)
            records.extend(page)
            if len(page) < _PAGE:
                break
        if len(records) >= _MAX_EVENTS:
            truncated = True
        events = events_from_records(records)
        return records, events, len(records) - len(events), truncated

    def _events_for(self, room_id: str) -> tuple[list[EngagementEvent], int, bool]:
        _records, events, skipped, truncated = self._scan(room_id)
        return events, skipped, truncated

    # -- classification ----------------------------------------------------- #

    def classify_room(self, room_id: str, *, as_of: str | None = None, with_decay: bool = True) -> dict[str, Any]:
        """One room's Trend value, its arithmetic, and what it decays to.

        Writes nothing. See the ``nothing-stored-per-workspace`` entry in
        :mod:`dsr.trend_health.inferences` for why the value is derived on read
        rather than cached per workspace.
        """
        room = self.require_room(room_id)
        moment = parse_timestamp(as_of, required=False) if as_of else datetime.now(timezone.utc)
        rules, origin = self.rules()
        events, skipped, truncated = self._events_for(room["id"])
        reading = classify(events, rules, moment)
        data = room.get("data") or {}

        result = {
            "room_id": room["id"],
            "name": _text(dashboard_fields(data, "name", "title")) or room["id"],
            "account": _text(dashboard_fields(data, "account", "company")),
            "owner": _text(dashboard_fields(data, "owner", "rep", "assigned_to")),
            "team": _text(dashboard_fields(data, "team", "group", "pod")),
            "stage": _text(dashboard_fields(data, "stage", "deal_stage")),
            "created_at": _iso(room.get("created_at")),
            "trend": reading["trend"],
            "label": reading["label"],
            "rule": reading["rule"],
            "reasons": reading["reasons"],
            "events": self._event_counts(events, rules, moment, skipped=skipped, truncated=truncated),
            "last_engagement_at": reading["last_engagement_at"],
            "last_engagement_days_ago": reading["last_engagement_days_ago"],
            "last_client_view": self._last_client_view(events),
            "next_change": reading["next_change"],
            "rules_source": origin,
        }
        if with_decay:
            result["decay"] = reading["decay"]
            result["decay_path"] = reading["decay_path"]
            result["windows"] = reading["windows"]
            result["rules"] = reading["rules"]
        return result

    def _event_counts(
        self,
        events: Sequence[EngagementEvent],
        rules: Mapping[str, Any],
        now: datetime,
        *,
        skipped: int = 0,
        truncated: bool = False,
    ) -> dict[str, Any]:
        """The numbers behind the bucket, always returned next to the bucket.

        Reported in full because the value alone is not enough to act on when a
        floor is what suppressed the bucket above it - which is the case the
        ``volume-floor`` inference exists for.
        """
        from dsr.trend_health.windows import in_window

        external_only = bool(rules.get("count_only_external", True))
        counts: dict[str, Any] = {
            "total": len(events),
            "external": sum(1 for event in events if event.external),
            "internal": sum(1 for event in events if not event.external),
            "client_views": sum(1 for event in events if is_client_view(event.type)),
            "skipped": skipped,
            "truncated": truncated,
        }
        for key in ("hot", "warm", "cold"):
            days = int(rules["windows"][key])
            counts[f"in_{days}_days"] = sum(1 for event in events if in_window(event.at, now, days))
            counts[f"qualifying_in_{days}_days"] = sum(
                1
                for event in events
                if in_window(event.at, now, days) and (event.external or not external_only)
            )
        return counts

    def _last_client_view(self, events: Sequence[EngagementEvent]) -> str | None:
        views = [event.at for event in events if is_client_view(event.type)]
        return views[-1].isoformat(timespec="seconds") if views else None

    # -- portfolio views ---------------------------------------------------- #

    def summary(self, *, room_id: str | None = None, as_of: str | None = None) -> dict[str, Any]:
        """How the portfolio is distributed across the four buckets.

        Scoped to one room when ``room_id`` is given, so the stat row on a room's
        page is not the whole portfolio's.
        """
        rows = self._rows(room_id=room_id, as_of=as_of)
        buckets = {value: 0 for value in TREND_VALUES}
        engagement = 0
        client_views = 0
        hottest: str | None = None
        stalest: str | None = None
        for row in rows:
            buckets[row["trend"]] += 1
            engagement += int(row["events"]["qualifying_in_7_days"] or 0)
            client_views += int(row["events"]["client_views"] or 0)
            if row["last_client_view"]:
                if hottest is None or str(row["last_client_view"]) > hottest:
                    hottest = str(row["last_client_view"])
                if stalest is None or str(row["last_client_view"]) < stalest:
                    stalest = str(row["last_client_view"])
        rules, origin = self.rules()
        return {
            "as_of": rows[0]["as_of"] if rows else None,
            "rooms": len(rows),
            "buckets": buckets,
            "labels": dict(TREND_LABELS),
            "engagement_in_7_days": engagement,
            "client_views": client_views,
            "newest_client_view": hottest,
            "oldest_client_view": stalest,
            "rules": rules,
            "rules_source": origin,
        }

    def dashboard(
        self,
        *,
        room_id: str | None = None,
        trend: str | Sequence[str] | None = None,
        owner: str | None = None,
        team: str | None = None,
        sort: str = "trend",
        order: str = "asc",
        as_of: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """The Trend column: one row per workspace, filterable and sortable.

        The user flow this serves is "sort/filter on Trend plus ``Last Client
        View`` to isolate Hot rooms and Cold rooms, then act". Every filter is
        therefore either a bucket, an owner, a team, or a single room, and the
        sort keys are the columns the research names plus the ones a rep reads
        anyway.
        """
        wanted = self._requested_trends(trend)
        direction = _text(order).lower() or "asc"
        if direction not in ("asc", "desc"):
            raise InvalidSort(f"order must be 'asc' or 'desc'; got {order!r}")
        key = _text(sort).lower() or "trend"
        if key not in SORT_KEYS:
            raise InvalidSort(
                f"unknown sort key {sort!r}; this dashboard sorts by {', '.join(DASHBOARD_SORTS)}"
            )
        if room_id:
            self.require_room(room_id)

        rows = self._rows(room_id=room_id, as_of=as_of)
        if wanted is not None:
            rows = [row for row in rows if row["trend"] in wanted]
        if owner:
            needle = _text(owner).lower()
            rows = [row for row in rows if row["owner"].lower() == needle]
        if team:
            needle = _text(team).lower()
            rows = [row for row in rows if row["team"].lower() == needle]

        kind = SORT_KEYS[key]
        rows = _sorted_with_empties_last(rows, key, kind, descending=(direction == "desc"))
        total = len(rows)
        capped = rows[: max(1, min(int(limit), 1000))]
        return {
            "as_of": capped[0]["as_of"] if capped else None,
            "count": len(capped),
            "total": total,
            "rows": capped,
            "sort": key,
            "order": direction,
            "trend": list(wanted) if wanted is not None else list(TREND_VALUES),
            "sortable": list(DASHBOARD_SORTS),
            "labels": dict(TREND_LABELS),
            "rules_source": rows[0]["rules_source"] if rows else self.rules()[1],
        }

    def _requested_trends(self, trend: str | Sequence[str] | None) -> set[str] | None:
        """Parse a bucket filter, or name the four buckets it accepts.

        Accepts a comma-separated string or a sequence, in any case, so a client
        can send ``?trend=hot,cold`` and get the "isolate Hot rooms and Cold
        rooms" case the research's user flow describes in one call.
        """
        if trend is None:
            return None
        raw = trend.split(",") if isinstance(trend, str) else list(trend)
        values = {_text(item).lower() for item in raw if _text(item)}
        if not values:
            return None
        unknown = sorted(values - set(TREND_VALUES))
        if unknown:
            raise InvalidSort(
                f"unknown trend {', '.join(unknown)}; this workflow classifies into "
                f"{', '.join(TREND_VALUES)}"
            )
        return values

    def _rows(self, *, room_id: str | None, as_of: str | None) -> list[dict[str, Any]]:
        """Classify every room, or just the one asked for.

        ``as_of`` is resolved once here rather than per room: a dashboard that
        evaluated each row against its own clock would put rooms on either side of
        a window edge that a single request is supposed to see as one instant.
        """
        moment = parse_timestamp(as_of, required=False) if as_of else datetime.now(timezone.utc)
        rules, origin = self.rules()
        if room_id:
            rooms = [self.require_room(room_id)]
            truncated = False
        else:
            rooms, truncated = self.list_rooms()

        rows: list[dict[str, Any]] = []
        for room in rooms:
            events, skipped, event_truncated = self._events_for(room["id"])
            reading = classify(events, rules, moment)
            data = room.get("data") or {}
            rows.append(
                {
                    "room_id": room["id"],
                    "name": _text(dashboard_fields(data, "name", "title")) or room["id"],
                    "account": _text(dashboard_fields(data, "account", "company")),
                    "owner": _text(dashboard_fields(data, "owner", "rep", "assigned_to")),
                    "team": _text(dashboard_fields(data, "team", "group", "pod")),
                    "stage": _text(dashboard_fields(data, "stage", "deal_stage")),
                    "created_at": _iso(room.get("created_at")),
                    "trend": reading["trend"],
                    "label": reading["label"],
                    "rank": reading["rank"],
                    "rule": reading["rule"],
                    "reasons": reading["reasons"],
                    "events": self._event_counts(
                        events, rules, moment, skipped=skipped, truncated=event_truncated
                    ),
                    "last_engagement_at": reading["last_engagement_at"],
                    "last_engagement_days_ago": reading["last_engagement_days_ago"],
                    "last_client_view": self._last_client_view(events),
                    "next_change": reading["next_change"],
                    "as_of": reading["as_of"],
                    "rules_source": origin,
                }
            )
        if truncated:
            for row in rows:
                row["rooms_truncated"] = True
        return rows


def _sort_value(row: Mapping[str, Any], key: str) -> Any:
    if key == "trend":
        return row.get("trend")
    if key in ("last_client_view", "created"):
        return row.get(key)
    if key == "engagement":
        return int((row.get("events") or {}).get("total") or 0)
    return row.get(key)


def _sorted_with_empties_last(
    rows: list[dict[str, Any]], key: str, kind: str, *, descending: bool
) -> list[dict[str, Any]]:
    """Sort by one column, keeping rows with no value for it at the bottom.

    Partitioned rather than encoded in the key, because ``reverse=True`` reverses
    a key as well as the order: a room that has never been opened would jump to
    the top of "Last Client View, newest first" and read as the freshest deal in
    the pipeline. It is the opposite of true, and it is the mistake a rep is
    most likely to act on.
    """
    missing = [row for row in rows if _sort_value(row, key) in (None, "")]
    present = [row for row in rows if _sort_value(row, key) not in (None, "")]
    for group in (present, missing):
        group.sort(key=lambda row: sort_key(_sort_value(row, key), kind=kind), reverse=descending)
    return present + missing


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


__all__ = [
    "TrendHealth",
    "DASHBOARD_SORTS",
    "SORT_KEYS",
    "ROOM_KEYS",
    "TYPE_KEYS",
    "ENGAGEMENT_COLLECTION",
    "ENGAGEMENT_EVENT_TYPES",
    "ORDER_FORM_PREFIX",
    "AUDIENCES",
    "RULES_COLLECTION",
    "RULES_RECORD_ID",
    "TREND_RULES",
    "rank_of",
]
