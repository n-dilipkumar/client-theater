"""Buyer engagement analytics over the audited, schema-flexible record store.

This module implements WF-006, "Review buyer engagement and prioritise
follow-up". It is a *derived* view: nothing here is written as a metric row.
Every number is computed on read from the records a team already keeps, which
is what lets a new field appear in a room or an activity payload without a
migration, a redeploy, or a word of coordination.

The researched flow
-------------------
The workflow is documented in ``docs/research/digital-sales-room-workflows/wf/
WF-006.md`` (source: ``docs/research/raw/room-experience.md`` section 6). It
describes a seller-side *Analytics* view with an aggregate dashboard, a room
scope selector, a per-room drill-down, and a room timeline.

Sourced behaviour
-----------------
* The aggregate view shows "Total active deals", recent buyer activity, and
  "Alerts for rooms with low engagement or approaching deadlines".
* "By default, the dashboard displays data for All Rooms. To view metrics for a
  specific deal, use the drop-down menu at the top right to select a room."
* Per-room Room Stats cover "View Time Viewed (e.g., 5h 32 min), Total Visits,
  Visitors, and Actions (document views, downloads, and comments)".
* "Most Active Visitors" lists "individuals ranked by their total actions".
* "Most Engaged Documents" shows "Total Views, Last Viewed date, Downloads,
  Average Time, and Users Involved".
* "Latest Activity" is "a live feed of the most recent buyer actions, showing
  the user, the action they took, and when it occurred".
* "Visit Frequency" "Charts how often the room is visited by day or week".
* "Room Trend: Indicates the room's engagement health as Cold, Warm, or Hot."
* "Use the Timeline tab to access a chronological log of updates to ensure your
  team remains aligned on deal developments."
* "Every metric on this page requires that connection" to the Liferay Data
  Platform, which is established by an environment token.

Design inferences
-----------------
The research fixes the *vocabulary* of the widgets but not the arithmetic behind
them, and LDP's query surface is explicitly unpublished. The following are
therefore deliberate, documented design decisions rather than sourced facts:

``trends``
    The Hot/Warm/Cold boundaries. Sourced only as a three-state health
    classification; the volume and recency thresholds that produce it are ours
    and are configurable in the ``analytics_config`` record.
``priority``
    A ranked follow-up order combining engagement, deadline pressure, and open
    alerts. The research has no such ranking; "prioritise follow-up" in the
    ticket title is what motivated it.
``visits``
    A visit is a *session*: consecutive events from one visitor inside
    ``visit_gap_minutes``. The research says "how often the room is visited"
    without defining a visit.
``inactive rooms``
    A deal in a terminal stage raises no alerts. "Alerts for rooms with low
    engagement" is about the live pipeline, and there is nothing to follow up
    on a closed deal.
``deadline sign``
    A date that has already passed reads as overdue, and only a date still in
    the future can raise the "approaching deadline" alert.
``suggested_action``
    A short rule-derived nudge per room. Purely a presentation affordance for
    the ranking above.
``timeline merge``
    The research says the Timeline is "a chronological log of updates". We merge
    the team's own notes with buyer activity so one log shows both.
``config defaults``
    Threshold values, and the synonym lists used to locate fields inside
    arbitrary payloads.

Schema flexibility
------------------
Field *locations* are discovered, not declared. ``DEFAULTS`` below lists
synonyms for each concept (``person``/``visitor``/``user``, ``action``/
``event``/``kind``, ...) and a team may override any of them in the stored
``analytics_config`` record. A field nobody has heard of is still carried
through untouched in the ``data`` of the records we return, so nothing is lost
by being unrecognised.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

COLLECTION_ROOM = "room"
COLLECTION_ACTIVITY = "activity"
COLLECTION_TIMELINE = "timeline"
COLLECTION_CONFIG = "analytics_config"
CONFIG_KEY = "default"

#: Every activity record is a buyer action. The taxonomy below only *names* the
#: kinds that appear in the Actions breakdown; an unrecognised action is counted
#: and grouped as "other" rather than dropped, so a new action kind needs no
#: configuration change to show up.
TREND_STATES = ("cold", "warm", "hot")

ACTION_KINDS = ("view", "download", "comment", "other")

DEFAULT_CONFIG: dict[str, Any] = {
    "key": CONFIG_KEY,
    "connection": {
        # Sourced: the environment token is what connects the instance, and
        # "if the token field already holds a value, your instance is
        # connected".
        "token": "",
        "environment": "",
        "connected_at": None,
    },
    "thresholds": {
        "window_days": 30,
        "chart_days": 14,
        "cold_floor": 3,
        "warm_actions": 15,
        "hot_actions": 40,
        "hot_within_days": 2,
        "warm_within_days": 10,
        "inactive_after_days": 14,
        "deadline_within_days": 7,
        "alert_after_days": 3,
        "visit_gap_minutes": 30,
    },
    "taxonomy": {
        "actions": [
            "viewed",
            "downloaded",
            "commented",
            "shared",
            "opened_link",
            "completed_section",
        ],
        "view": ["viewed"],
        "download": ["downloaded"],
        "comment": ["commented"],
    },
    "deal": {
        "name_fields": ["name", "title", "account"],
        "account_fields": ["account", "company", "organisation", "organization"],
        "stage_fields": ["stage", "status", "phase"],
        "owner_fields": ["owner", "sponsor", "assigned_to"],
        # A deal in a terminal stage is not an "active deal".
        "terminal_stages": ["closed", "closed_won", "closed_lost", "archived", "abandoned"],
        "archived_fields": ["archived", "is_archived"],
        "deadline_fields": [
            "expires_at",
            "renewal_date",
            "close_date",
            "decision_date",
            "deadline",
            "won_at",
        ],
    },
    "event": {
        "person_fields": ["person", "user", "visitor", "email", "actor", "by"],
        "action_fields": ["action", "event", "activity", "kind", "type"],
        "target_fields": ["target", "document", "document_title", "title", "page", "page_title"],
        "seconds_fields": [
            "seconds_on_page",
            "seconds",
            "dwell_seconds",
            "duration_seconds",
            "time_on_page",
        ],
        "timestamp_fields": ["occurred_at", "at", "happened_at", "timestamp", "created_at"],
    },
    "note": {
        "summary_fields": ["summary", "note", "body", "message", "title", "text"],
        "actor_fields": ["actor", "author", "person", "by", "owner"],
        "timestamp_fields": ["at", "occurred_at", "happened_at", "created_at", "recorded_at"],
    },
}

#: ``AuditedDatabase.list`` pages with LIMIT/OFFSET, so scanning in pages of
#: this size keeps aggregates correct well past the per-call cap.
_PAGE = 1000


class UnknownRoom(LookupError):
    """Raised when a room id does not resolve to a live room record."""


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #


def _as_text(value: Any, default: str = "") -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, bool):
        return default
    return str(value)


def _as_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on")
    return False


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _pick(data: Mapping[str, Any] | None, keys: Sequence[str], default: Any = None) -> Any:
    """First non-empty value among ``keys`` inside a JSON payload."""
    if not isinstance(data, Mapping):
        return default
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _pick_text(data: Mapping[str, Any] | None, keys: Sequence[str], default: str = "") -> str:
    return _as_text(_pick(data, keys), default)


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; date-only values are treated as UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _signed_days(delta: timedelta) -> int:
    """Whole days, negative in the past.

    Deadlines need the sign: a date that has already passed must read as
    overdue, not as "in 0 day(s)".
    """
    return int(delta.total_seconds() // 86400)


def _whole_days(delta: timedelta) -> int:
    """Whole days elapsed, so a just-now event is 0 rather than -1."""
    return max(0, _signed_days(delta))


def format_duration(seconds: Any) -> str:
    """Human view time in the shape the research quotes: "5h 32 min"."""
    total = int(round(max(0.0, _as_number(seconds))))
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{total}s"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge, so a partial config patch cannot drop sibling keys."""
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _config_id(key: str = CONFIG_KEY) -> str:
    return f"{COLLECTION_CONFIG}_{key}"


def load_config(store: RecordStore) -> dict[str, Any]:
    """Effective configuration: stored overrides layered onto the defaults.

    A missing config record is not an error; the defaults are the documented
    behaviour and are returned verbatim so a client can show them.
    """
    record = store.get(_config_id())
    if record is None:
        matches = store.find(COLLECTION_CONFIG, {"key": CONFIG_KEY}, limit=1)
        record = matches[0] if matches else None
    stored = (record or {}).get("data") or {}
    return _deep_merge(DEFAULT_CONFIG, stored if isinstance(stored, Mapping) else {})


def is_connected(config: Mapping[str, Any]) -> bool:
    """Sourced gate: metrics require a connected analytics environment."""
    connection = config.get("connection") or {}
    return bool(_as_text(connection.get("token")))


def save_config(
    store: RecordStore,
    patch: Mapping[str, Any],
    *,
    actor: str | None = None,
    source: str = "PATCH analytics config",
) -> dict[str, Any]:
    """Merge a partial config patch and write it through the audited store.

    ``source`` is supplied by the caller rather than hardcoded here. The audit
    row records which request caused the write, and only the HTTP layer knows
    its own path; a URL baked into the domain module goes stale the moment the
    route moves, and the audit log then misreports where a change came from.
    """
    existing = store.get(_config_id())
    base = (existing or {}).get("data") or {}
    merged = _deep_merge(base if isinstance(base, Mapping) else {}, patch)
    merged["key"] = CONFIG_KEY
    if is_connected(merged) and not is_connected(load_config(store)):
        merged.setdefault("connection", {})
        if not merged["connection"].get("connected_at"):
            merged["connection"]["connected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if existing is None:
        return store.create(
            COLLECTION_CONFIG,
            merged,
            record_id=_config_id(),
            actor=actor,
            source=source,
        )
    return store.update(_config_id(), merged, actor=actor, source=source)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


def _scan(store: RecordStore, collection: str, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every live record in a collection, paged so nothing is truncated."""
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(collection, room_id=room_id, limit=_PAGE, offset=offset, order_by="id", descending=False)
        records.extend(page)
        if len(page) < _PAGE:
            return records
        offset += _PAGE


def _rooms(store: RecordStore) -> list[dict[str, Any]]:
    return _scan(store, COLLECTION_ROOM)


def require_room(store: RecordStore, room_id: str) -> dict[str, Any]:
    record = store.get(room_id)
    if record is None or record.get("collection") != COLLECTION_ROOM:
        raise UnknownRoom(room_id)
    return record


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def _kind_of(action: str, taxonomy: Mapping[str, Any]) -> str:
    for kind in ("view", "download", "comment"):
        listed = taxonomy.get(kind) or []
        if action in {str(item).strip() for item in listed}:
            return kind
    return "other"


def collect_events(store: RecordStore, room_id: str | None, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalise activity records into one event shape, whatever they call fields."""
    event_config = config.get("event") or {}
    taxonomy = config.get("taxonomy") or {}
    people = list(event_config.get("person_fields") or [])
    actions = list(event_config.get("action_fields") or [])
    targets = list(event_config.get("target_fields") or [])
    seconds_fields = list(event_config.get("seconds_fields") or [])
    stamps = list(event_config.get("timestamp_fields") or [])

    events: list[dict[str, Any]] = []
    for record in _scan(store, COLLECTION_ACTIVITY, room_id=room_id):
        data = record.get("data") or {}
        action = _pick_text(data, actions)
        at = _parse_dt(_pick(data, stamps)) or _parse_dt(record.get("created_at"))
        events.append(
            {
                "id": record.get("id"),
                "room_id": record.get("room_id"),
                "person": _pick_text(data, people) or "anonymous",
                "action": action or "unknown",
                "target": _pick_text(data, targets),
                "seconds": max(0.0, _as_number(_pick(data, seconds_fields))),
                "at": at,
                "occurred_at": _iso(at),
                "kind": _kind_of(action, taxonomy),
                "data": data,
            }
        )
    return events


def count_visits(events: Sequence[Mapping[str, Any]], gap_minutes: Any) -> list[list[Mapping[str, Any]]]:
    """Group events into sessions: one visit per visitor per gap window.

    Sourced only as "how often the room is visited by day or week"; the session
    definition is ours (see the module docstring).
    """
    gap = timedelta(minutes=max(1, _as_int(gap_minutes, 30, minimum=1)))
    by_visitor: dict[tuple[Any, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        by_visitor[(event.get("room_id"), str(event.get("person") or "anonymous"))].append(event)

    visits: list[list[Mapping[str, Any]]] = []
    for items in by_visitor.values():
        ordered = sorted(items, key=lambda e: (e.get("at") is None, e.get("at") or datetime.min.replace(tzinfo=timezone.utc)))
        current: list[Mapping[str, Any]] = []
        previous: datetime | None = None
        for event in ordered:
            at = event.get("at")
            if current and previous is not None and at is not None and (at - previous) > gap:
                visits.append(current)
                current = []
            current.append(event)
            if at is not None:
                previous = at
        if current:
            visits.append(current)
    return visits


# --------------------------------------------------------------------------- #
# Bucketing
# --------------------------------------------------------------------------- #


def bucket_start(value: datetime, grain: str) -> datetime:
    day = value.astimezone(timezone.utc)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "week":
        start -= timedelta(days=start.weekday())
    return start


def _step(grain: str) -> timedelta:
    return timedelta(days=7 if grain == "week" else 1)


def _empty_point() -> dict[str, Any]:
    return {"actions": 0, "visitors": 0, "visits": 0}


def _chart(
    buckets: dict[str, dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    grain: str,
) -> list[dict[str, Any]]:
    """Dense series: one point per bucket, zero-filled so gaps are visible."""
    points: list[dict[str, Any]] = []
    cursor = bucket_start(start, grain)
    limit = bucket_start(end, grain)
    while cursor <= limit:
        key = cursor.date().isoformat()
        point = _empty_point()
        point.update(buckets.get(key) or {})
        points.append({"bucket_start": key, **point})
        cursor += _step(grain)
    return points


def recent_engagement(
    events: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    days: int,
    grain: str = "day",
) -> list[dict[str, Any]]:
    """Actions and distinct visitors per bucket across the chart window."""
    window_start = now - timedelta(days=max(1, days))
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        at = event.get("at")
        if at is None or at < window_start or at > now:
            continue
        key = bucket_start(at, grain).date().isoformat()
        point = buckets.setdefault(key, {"actions": 0, "visitors": 0, "visits": 0})
        point["actions"] += 1
    for events_in_bucket in _group_by_bucket(events, window_start, now, grain):
        key, items = events_in_bucket
        point = buckets.setdefault(key, {"actions": 0, "visitors": 0, "visits": 0})
        point["visitors"] = len({str(item.get("person") or "anonymous") for item in items})
    return _chart(buckets, start=window_start, end=now, grain=grain)


def visit_frequency(
    events: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    days: int,
    grain: str = "day",
    gap_minutes: Any = 30,
) -> list[dict[str, Any]]:
    """Visits per bucket, charted by day or week as the widget allows."""
    window_start = now - timedelta(days=max(1, days))
    buckets: dict[str, dict[str, Any]] = {}
    for visit in count_visits(events, gap_minutes):
        stamps = [item.get("at") for item in visit if item.get("at") is not None]
        if not stamps:
            continue
        start_at = min(stamps)
        if start_at < window_start or start_at > now:
            continue
        key = bucket_start(start_at, grain).date().isoformat()
        point = buckets.setdefault(key, {"actions": 0, "visitors": 0, "visits": 0})
        point["visits"] += 1
        point["visitors"] += len({str(item.get("person") or "anonymous") for item in visit})
        point["actions"] += len(visit)
    return _chart(buckets, start=window_start, end=now, grain=grain)


def _group_by_bucket(
    events: Sequence[Mapping[str, Any]],
    window_start: datetime,
    now: datetime,
    grain: str,
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        at = event.get("at")
        if at is None or at < window_start or at > now:
            continue
        grouped[bucket_start(at, grain).date().isoformat()].append(event)
    return sorted(grouped.items())


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #


def room_stats(events: Sequence[Mapping[str, Any]], visits: Sequence[Any], now: datetime) -> dict[str, Any]:
    """Sourced widget: "View Time Viewed, Total Visits, Visitors, and Actions"."""
    seconds = sum(_as_number(event.get("seconds")) for event in events)
    stamps = [event["at"] for event in events if event.get("at") is not None]
    last = max(stamps) if stamps else None
    by_kind = {kind: 0 for kind in ACTION_KINDS}
    for event in events:
        kind = str(event.get("kind") or "other")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "actions": len(events),
        "visitors": len({str(event.get("person") or "anonymous") for event in events}),
        "total_visits": len(visits),
        "view_seconds": round(seconds, 3),
        "view_time": format_duration(seconds),
        "actions_by_kind": by_kind,
        "last_activity_at": _iso(last),
        "days_since_activity": _whole_days(now - last) if last else None,
    }


def most_active_visitors(
    events: Sequence[Mapping[str, Any]], now: datetime, limit: int = 10
) -> list[dict[str, Any]]:
    """Sourced widget: "individuals ranked by their total actions"."""
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        person = str(event.get("person") or "anonymous")
        row = grouped.setdefault(
            person,
            {"person": person, "actions": 0, "seconds": 0.0, "targets": set(), "last_seen": None},
        )
        row["actions"] += 1
        row["seconds"] += _as_number(event.get("seconds"))
        if event.get("target"):
            row["targets"].add(str(event["target"]))
        at = event.get("at")
        if at is not None and (row["last_seen"] is None or at > row["last_seen"]):
            row["last_seen"] = at

    ranked = sorted(
        grouped.values(),
        key=lambda row: (-row["actions"], -(row["last_seen"].timestamp() if row["last_seen"] else 0.0), row["person"]),
    )[: max(1, limit)]
    return [
        {
            "person": row["person"],
            "actions": row["actions"],
            "view_time": format_duration(row["seconds"]),
            "documents": len(row["targets"]),
            "last_seen_at": _iso(row["last_seen"]),
            "days_since_seen": _whole_days(now - row["last_seen"]) if row["last_seen"] else None,
        }
        for row in ranked
    ]


def most_engaged_documents(events: Sequence[Mapping[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Sourced widget: "Total Views, Last Viewed date, Downloads, Average Time,
    and Users Involved" for each shared asset.

    Derived from the activity log rather than from counters on the document
    record, so every metric is attributable to a buyer event.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        target = str(event.get("target") or "").strip()
        if not target:
            continue
        row = grouped.setdefault(
            target,
            {
                "title": target,
                "views": 0,
                "downloads": 0,
                "comments": 0,
                "actions": 0,
                "seconds": 0.0,
                "timed_events": 0,
                "users": set(),
                "last_viewed": None,
            },
        )
        kind = str(event.get("kind") or "other")
        row["actions"] += 1
        if kind == "view":
            row["views"] += 1
        elif kind == "download":
            row["downloads"] += 1
        elif kind == "comment":
            row["comments"] += 1
        seconds = _as_number(event.get("seconds"))
        if seconds > 0:
            row["seconds"] += seconds
            row["timed_events"] += 1
        row["users"].add(str(event.get("person") or "anonymous"))
        at = event.get("at")
        if at is not None and (row["last_viewed"] is None or at > row["last_viewed"]):
            row["last_viewed"] = at

    ranked = sorted(
        grouped.values(),
        key=lambda row: (-row["views"], -row["actions"], row["title"]),
    )[: max(1, limit)]
    return [
        {
            "title": row["title"],
            "views": row["views"],
            "downloads": row["downloads"],
            "comments": row["comments"],
            "actions": row["actions"],
            "average_seconds": round(row["seconds"] / row["timed_events"], 3) if row["timed_events"] else 0.0,
            "average_time": format_duration(row["seconds"] / row["timed_events"]) if row["timed_events"] else "—",
            "users_involved": len(row["users"]),
            "last_viewed_at": _iso(row["last_viewed"]),
        }
        for row in ranked
    ]


def latest_activity(
    events: Sequence[Mapping[str, Any]],
    limit: int = 15,
    *,
    room_names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Sourced widget: "the user, the action they took, and when it occurred"."""
    ordered = sorted(
        (event for event in events if event.get("at") is not None),
        key=lambda event: event["at"],
        reverse=True,
    )[: max(1, limit)]
    return [
        {
            "id": event.get("id"),
            "room_id": event.get("room_id"),
            "room_name": (room_names or {}).get(event.get("room_id")),
            "person": event.get("person"),
            "action": event.get("action"),
            "kind": event.get("kind"),
            "target": event.get("target"),
            "seconds": event.get("seconds"),
            "occurred_at": event.get("occurred_at"),
        }
        for event in ordered
    ]


# --------------------------------------------------------------------------- #
# Trend, alerts, priority
# --------------------------------------------------------------------------- #


def classify_trend(stats: Mapping[str, Any], thresholds: Mapping[str, Any]) -> dict[str, Any]:
    """Sourced three-state health: "Cold", "Warm", or "Hot".

    Volume and recency thresholds are a design inference; both are configurable.
    """
    actions = int(stats.get("actions") or 0)
    days = stats.get("days_since_activity")
    reasons: list[str] = []

    if days is None:
        classification = "cold"
        reasons.append("no recorded buyer activity")
    elif actions >= _as_int(thresholds.get("hot_actions"), 40, minimum=1) and days <= _as_int(
        thresholds.get("hot_within_days"), 2, minimum=0
    ):
        classification = "hot"
        reasons.append(f"{actions} actions in the last {days}d")
    elif days <= _as_int(thresholds.get("warm_within_days"), 10, minimum=0) or actions >= _as_int(
        thresholds.get("warm_actions"), 15, minimum=1
    ):
        classification = "warm"
        if days is not None:
            reasons.append(f"last buyer action {days}d ago")
        if actions:
            reasons.append(f"{actions} actions in the window")
    else:
        classification = "cold"
        reasons.append(f"last buyer action {days}d ago")
        if actions == 0:
            reasons.append("no actions in the window")

    if days is not None and actions >= _as_int(thresholds.get("cold_floor"), 3, minimum=1):
        reasons.append("above the low-engagement floor")

    return {
        "classification": classification,
        "reasons": reasons,
        "actions": actions,
        "days_since_activity": days,
        "thresholds": {
            "cold_floor": _as_int(thresholds.get("cold_floor"), 3, minimum=0),
            "warm_actions": _as_int(thresholds.get("warm_actions"), 15, minimum=1),
            "hot_actions": _as_int(thresholds.get("hot_actions"), 40, minimum=1),
            "hot_within_days": _as_int(thresholds.get("hot_within_days"), 2, minimum=0),
            "warm_within_days": _as_int(thresholds.get("warm_within_days"), 10, minimum=0),
        },
    }


def _engagement_score(stats: Mapping[str, Any], trend: Mapping[str, Any], thresholds: Mapping[str, Any]) -> float:
    hot = max(1, _as_int(thresholds.get("hot_actions"), 40, minimum=1))
    volume = min(1.0, int(stats.get("actions") or 0) / hot)
    inactive = max(1, _as_int(thresholds.get("inactive_after_days"), 14, minimum=1))
    days = stats.get("days_since_activity")
    recency = 0.0 if days is None else max(0.0, 1.0 - (_as_number(days) / inactive))
    breadth = min(1.0, int(stats.get("visitors") or 0) / max(1.0, hot / 4))
    score = 100 * (0.5 * volume + 0.3 * recency + 0.2 * breadth)
    if trend.get("classification") == "hot":
        score = max(score, 60.0)
    elif trend.get("classification") == "warm":
        score = max(score, 30.0)
    return round(min(100.0, score), 1)


def deadline_of(data: Mapping[str, Any], deal_config: Mapping[str, Any]) -> dict[str, Any] | None:
    """First deadline-shaped field on a room, as the research's deadline alerts use."""
    fields = list(deal_config.get("deadline_fields") or [])
    for field in fields:
        raw = _pick(data, [field])
        parsed = _parse_dt(raw)
        if parsed is not None:
            return {"field": field, "at": _iso(parsed), "value": _as_text(raw)}
    return None


def build_alerts(
    *,
    room_id: str,
    name: str,
    created: datetime | None,
    stats: Mapping[str, Any],
    deadline: Mapping[str, Any] | None,
    now: datetime,
    thresholds: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Sourced automation: "Alerts for rooms with low engagement or approaching
    deadlines".

    ``created`` gates the low-engagement alert: a room opened this morning
    cannot be accused of low engagement yet.
    """
    alerts: list[dict[str, Any]] = []
    actions = int(stats.get("actions") or 0)
    days = stats.get("days_since_activity")
    cold_floor = _as_int(thresholds.get("cold_floor"), 3, minimum=0)
    room_age = _whole_days(now - created) if created else None
    alert_after = _as_int(thresholds.get("alert_after_days"), 3, minimum=0)

    if actions < cold_floor and (room_age is None or room_age >= alert_after):
        alerts.append(
            {
                "kind": "low_engagement",
                "severity": "high" if actions == 0 else "medium",
                "room_id": room_id,
                "room_name": name,
                "message": f"{name}: {actions} buyer action(s) in the window, below the floor of {cold_floor}",
                "detail": {"actions": actions, "cold_floor": cold_floor},
            }
        )

    inactive_after = _as_int(thresholds.get("inactive_after_days"), 14, minimum=1)
    if days is not None and days > inactive_after:
        alerts.append(
            {
                "kind": "inactive",
                "severity": "medium",
                "room_id": room_id,
                "room_name": name,
                "message": f"{name}: no buyer action for {days} days",
                "detail": {"days_since_activity": days, "inactive_after_days": inactive_after},
            }
        )

    if deadline and deadline.get("_parsed"):
        remaining = _signed_days(deadline["_parsed"] - now)
        within = _as_int(thresholds.get("deadline_within_days"), 7, minimum=0)
        if 0 <= remaining <= within:
            alerts.append(
                {
                    "kind": "deadline_approaching",
                    "severity": "high" if remaining <= 2 else "medium",
                    "room_id": room_id,
                    "room_name": name,
                    "message": f"{name}: {deadline['field']} in {remaining} day(s)",
                    "detail": {"field": deadline["field"], "at": deadline["at"], "days_remaining": remaining},
                }
            )
    return alerts


def prioritise(
    *,
    stats: Mapping[str, Any],
    trend: Mapping[str, Any],
    alerts: Sequence[Mapping[str, Any]],
    deadline: Mapping[str, Any] | None,
    now: datetime,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Rank a room for follow-up. A design inference, motivated by the ticket.

    Engagement is the base; deadline pressure and open alerts add to it, and
    every contribution is explained in ``reasons`` so the UI can justify a rank
    rather than assert it.
    """
    score = _engagement_score(stats, trend, thresholds)
    reasons = list(trend.get("reasons") or [])
    urgency = "normal"

    if deadline and deadline.get("_parsed") is not None:
        remaining = _signed_days(deadline["_parsed"] - now)
        within = _as_int(thresholds.get("deadline_within_days"), 7, minimum=0)
        if 0 <= remaining <= within:
            score += 20
            urgency = "high"
            reasons.append(f"deadline in {remaining} day(s)")
        elif remaining < 0:
            # Already past. Not a pressure signal, but it is worth saying.
            reasons.append(f"deadline passed {-remaining} day(s) ago")
        elif remaining <= within * 3:
            score += 10
            reasons.append(f"deadline in {remaining} day(s)")

    kinds = {alert["kind"] for alert in alerts}
    if kinds:
        urgency = "high" if "low_engagement" in kinds or "deadline_approaching" in kinds else urgency
        score += 10 * len(kinds)

    if trend.get("classification") == "hot":
        suggested = "Follow up while interest is high: the buyer is active now."
    elif "deadline_approaching" in kinds:
        suggested = "Chase the decision: the deadline is close and engagement is thin."
    elif "low_engagement" in kinds or "inactive" in kinds:
        suggested = "Re-engage: the room has gone quiet."
    elif deadline and deadline.get("_parsed") is not None:
        suggested = "Keep the deal warm: activity is steady with a date attached."
    else:
        suggested = "Monitor: no deadline pressure and modest engagement."

    return {
        "score": round(min(100.0, score), 1),
        "urgency": urgency,
        "reasons": reasons,
        "alerts": sorted(kinds),
        "suggested_action": suggested,
    }


# --------------------------------------------------------------------------- #
# Deal cards
# --------------------------------------------------------------------------- #


def _deal_summary(store: RecordStore, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """One summary per live room, keyed by room id."""
    deal_config = config.get("deal") or {}
    summaries: dict[str, dict[str, Any]] = {}
    for record in _rooms(store):
        data = record.get("data") or {}
        name = _pick_text(data, list(deal_config.get("name_fields") or [])) or record.get("id")
        stage = _pick_text(data, list(deal_config.get("stage_fields") or [])).lower()
        archived = any(_as_bool(data.get(field)) for field in (deal_config.get("archived_fields") or []))
        terminal = stage in {str(item).strip().lower() for item in (deal_config.get("terminal_stages") or [])}
        deadline = deadline_of(data, deal_config)
        if deadline:
            deadline["_parsed"] = _parse_dt(deadline.get("at"))
        else:
            deadline = None
        summaries[record["id"]] = {
            "record": record,
            "name": name,
            "account": _pick_text(data, list(deal_config.get("account_fields") or [])),
            "stage": _pick_text(data, list(deal_config.get("stage_fields") or [])),
            "owner": _pick_text(data, list(deal_config.get("owner_fields") or [])),
            "active": not archived and not terminal,
            "archived": archived,
            "terminal_stage": terminal,
            "created": _parse_dt(record.get("created_at")),
            "deadline": deadline,
        }
    return summaries


def room_card(
    *,
    summary: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    thresholds = config.get("thresholds") or {}
    gap = thresholds.get("visit_gap_minutes")
    stats = room_stats(events, count_visits(events, gap), now)
    windowed = [
        event
        for event in events
        if event.get("at")
        and event["at"] >= now - timedelta(days=_as_int(thresholds.get("window_days"), 30, minimum=1))
    ]
    window_stats = room_stats(windowed, count_visits(windowed, gap), now)
    trend = classify_trend(window_stats, thresholds)
    # A deal in a terminal stage has no follow-up left to prioritise, so it
    # never raises a low-engagement or inactivity alert. Its engagement is
    # still reported, because the trend of a won deal is worth reading.
    alerts = (
        build_alerts(
            room_id=summary["record"]["id"],
            name=summary["name"],
            created=summary.get("created"),
            stats=window_stats,
            deadline=summary.get("deadline"),
            now=now,
            thresholds=thresholds,
        )
        if summary["active"]
        else []
    )
    priority = prioritise(
        stats=window_stats,
        trend=trend,
        alerts=alerts,
        deadline=summary.get("deadline"),
        now=now,
        thresholds=thresholds,
    )

    deadline = summary.get("deadline")
    deadline_out = None
    if deadline:
        parsed = deadline.get("_parsed")
        deadline_out = {
            "field": deadline["field"],
            "at": deadline["at"],
            "days_remaining": _signed_days(parsed - now) if parsed else None,
            "passed": bool(parsed and parsed < now),
        }

    return {
        "room_id": summary["record"]["id"],
        "name": summary["name"],
        "account": summary["account"],
        "stage": summary["stage"],
        "owner": summary["owner"],
        "active": summary["active"],
        "created_at": _iso(summary.get("created")),
        "trend": trend,
        "priority": {**priority, "rank": None},
        "engagement": {
            **stats,
            "actions_in_window": window_stats["actions"],
            "visitors_in_window": window_stats["visitors"],
            "view_time_in_window": window_stats["view_time"],
        },
        "deadline": deadline_out,
        "alerts": alerts,
    }


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #


def timeline(
    store: RecordStore,
    config: Mapping[str, Any],
    *,
    room_id: str | None = None,
    now: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Sourced widget: "a chronological log of updates" for a room.

    The team's own notes and the buyer activity log are merged into one
    descending log, so internal alignment and buyer movement sit side by side.
    """
    note_config = config.get("note") or {}
    summary_fields = list(note_config.get("summary_fields") or ["summary"])
    actor_fields = list(note_config.get("actor_fields") or ["actor"])
    at_fields = list(note_config.get("timestamp_fields") or ["at"])

    entries: list[dict[str, Any]] = []
    for record in _scan(store, COLLECTION_TIMELINE, room_id=room_id):
        data = record.get("data") or {}
        at = _parse_dt(_pick(data, at_fields)) or _parse_dt(record.get("created_at"))
        entries.append(
            {
                "id": record.get("id"),
                "source": COLLECTION_TIMELINE,
                "kind": _pick_text(data, ["kind", "type"]) or "note",
                "room_id": record.get("room_id"),
                "summary": _pick_text(data, summary_fields),
                "actor": _pick_text(data, actor_fields) or _as_text(record.get("actor"), "system"),
                "at": _iso(at),
                "_at": at,
                "data": data,
            }
        )

    for event in collect_events(store, room_id, config):
        if event.get("at") is None:
            continue
        target = f" · {event['target']}" if event.get("target") else ""
        entries.append(
            {
                "id": event.get("id"),
                "source": COLLECTION_ACTIVITY,
                "kind": "buyer",
                "room_id": event.get("room_id"),
                "summary": f"{event['person']} {event['action']}{target}",
                "actor": event["person"],
                "at": event.get("occurred_at"),
                "_at": event.get("at"),
                "data": event.get("data"),
            }
        )

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    entries.sort(key=lambda entry: (entry.get("_at") or epoch), reverse=True)
    for entry in entries:
        entry.pop("_at", None)
    return entries[: max(1, limit)]


# --------------------------------------------------------------------------- #
# Public views
# --------------------------------------------------------------------------- #


def resolve_now(value: str | None) -> datetime:
    return _parse_dt(value) or datetime.now(timezone.utc)


def _require_connected(config: Mapping[str, Any]) -> None:
    if not is_connected(config):
        raise PermissionError("analytics connection is not configured: set connection.token")


def overview(
    store: RecordStore,
    *,
    room_id: str | None = None,
    grain: str = "day",
    as_of: str | None = None,
) -> dict[str, Any]:
    """The aggregate Analytics view, optionally scoped to one deal.

    Sourced: "Total active deals", "Recent buyer activity and engaged
    documents", and "Alerts for rooms with low engagement or approaching
    deadlines", with "All Rooms" as the default scope.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    if room_id:
        require_room(store, room_id)
    _require_connected(config)

    thresholds = config.get("thresholds") or {}
    summaries = _deal_summary(store, config)
    scoped = [summary for summary in summaries.values() if room_id in (None, summary["record"]["id"])]

    events = collect_events(store, room_id, config)
    cards: list[dict[str, Any]] = []
    for summary in scoped:
        room_events = [event for event in events if event.get("room_id") == summary["record"]["id"]]
        cards.append(room_card(summary=summary, events=room_events, config=config, now=now))
    cards.sort(key=lambda card: (-card["priority"]["score"], card["name"].lower()))
    for index, card in enumerate(cards, start=1):
        card["priority"]["rank"] = index

    active = [card for card in cards if card["active"]]
    alerts = [alert for card in cards for alert in card["alerts"]]
    room_names = {card["room_id"]: card["name"] for card in cards}
    total_seconds = sum(_as_number(card["engagement"].get("view_seconds")) for card in active)

    return {
        "connected": True,
        "as_of": _iso(now),
        "grain": grain,
        "scope": {
            "room_id": room_id,
            "room_name": room_names.get(room_id) if room_id else None,
            "label": room_names.get(room_id) if room_id else "All Rooms",
        },
        "config": {"thresholds": thresholds, "taxonomy": config.get("taxonomy")},
        "total_active_deals": len(active),
        "totals": {
            "deals": len(cards),
            "active_deals": len(active),
            "actions": sum(card["engagement"]["actions"] for card in active),
            "visitors": len({event["person"] for event in events if event.get("person")}),
            "visits": sum(card["engagement"]["total_visits"] for card in active),
            "view_seconds": round(total_seconds, 3),
            "view_time": format_duration(total_seconds),
            "alerts": len(alerts),
            "hot": sum(1 for card in active if card["trend"]["classification"] == "hot"),
            "warm": sum(1 for card in active if card["trend"]["classification"] == "warm"),
            "cold": sum(1 for card in active if card["trend"]["classification"] == "cold"),
        },
        "rooms": cards,
        "alerts": alerts,
        "latest_activity": latest_activity(events, limit=15, room_names=room_names),
        "most_engaged_documents": most_engaged_documents(events, limit=10),
        "most_active_visitors": most_active_visitors(events, now, limit=10),
        "recent_engagement": recent_engagement(events, now=now, days=_as_int(thresholds.get("chart_days"), 14, minimum=1), grain=grain),
        "visit_frequency": visit_frequency(
            events,
            now=now,
            days=_as_int(thresholds.get("chart_days"), 14, minimum=1),
            grain=grain,
            gap_minutes=thresholds.get("visit_gap_minutes"),
        ),
    }


def room_engagement(
    store: RecordStore,
    room_id: str,
    *,
    grain: str = "day",
    as_of: str | None = None,
    timeline_limit: int = 50,
) -> dict[str, Any]:
    """The per-room drill-down: every per-room widget plus the Timeline."""
    now = resolve_now(as_of)
    config = load_config(store)
    _require_connected(config)

    record = require_room(store, room_id)
    summary = _deal_summary(store, config)[room_id]
    events = collect_events(store, room_id, config)
    card = room_card(summary=summary, events=events, config=config, now=now)
    thresholds = config.get("thresholds") or {}
    gap = thresholds.get("visit_gap_minutes")
    stats = room_stats(events, count_visits(events, gap), now)
    chart_days = _as_int(thresholds.get("chart_days"), 14, minimum=1)

    return {
        "connected": True,
        "as_of": _iso(now),
        "grain": grain,
        "room": {
            "id": record["id"],
            "name": card["name"],
            "account": card["account"],
            "stage": card["stage"],
            "owner": card["owner"],
            "active": card["active"],
            "created_at": card["created_at"],
            "data": record.get("data") or {},
        },
        "config": {"thresholds": thresholds, "taxonomy": config.get("taxonomy")},
        "room_stats": stats,
        "room_trend": card["trend"],
        "priority": card["priority"],
        "deadline": card["deadline"],
        "alerts": card["alerts"],
        "most_active_visitors": most_active_visitors(events, now),
        "most_engaged_documents": most_engaged_documents(events),
        "latest_activity": latest_activity(events, limit=15),
        "recent_engagement": recent_engagement(events, now=now, days=chart_days, grain=grain),
        "visit_frequency": visit_frequency(events, now=now, days=chart_days, grain=grain, gap_minutes=gap),
        "timeline": timeline(store, config, room_id=room_id, now=now, limit=timeline_limit),
    }


def alerts(
    store: RecordStore,
    *,
    room_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Alerts only, for a dashboard widget that polls independently."""
    payload = overview(store, room_id=room_id, as_of=as_of)
    return {
        "connected": payload["connected"],
        "as_of": payload["as_of"],
        "scope": payload["scope"],
        "count": len(payload["alerts"]),
        "alerts": payload["alerts"],
    }


# --------------------------------------------------------------------------- #
# Writes (all audited through the store)
# --------------------------------------------------------------------------- #


def record_event(
    store: RecordStore,
    payload: Mapping[str, Any],
    *,
    room_id: str | None = None,
    actor: str | None = None,
    source: str = "POST analytics event",
) -> dict[str, Any]:
    """Ingest one buyer interaction, the entry point of the data flow.

    The payload is stored verbatim: a team that sends a field nobody declared
    still gets it indexed, queryable with ``?where=``, and returned untouched.

    ``source`` comes from the caller for the same reason as in ``save_config``.
    """
    if room_id is not None:
        require_room(store, room_id)
    return store.create(
        COLLECTION_ACTIVITY,
        payload,
        room_id=room_id,
        actor=actor,
        source=source,
    )


def add_note(
    store: RecordStore,
    room_id: str,
    payload: Mapping[str, Any],
    *,
    actor: str | None = None,
    source: str = "POST timeline note",
) -> dict[str, Any]:
    """Append an internal update to a room's Timeline.

    ``source`` comes from the caller for the same reason as in ``save_config``.
    """
    require_room(store, room_id)
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("a timeline entry needs at least one field")
    return store.create(
        COLLECTION_TIMELINE,
        payload,
        room_id=room_id,
        actor=actor,
        source=source,
    )
