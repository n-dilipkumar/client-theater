"""The per-buyer read-through cache and the TTL that governs it.

User-flow step four: "Room caches the result per buyer with a short TTL and
renders the deal panel."

And the automation line: "Read-through cache refresh on a room scheduler;
nothing pushes."

Read-through is the whole behaviour, and it is why a snapshot is not a promise
about freshness. Nothing writes to this collection except a read that just
happened, so a snapshot's age is the only honest statement about how current the
panel is, and the four states below are what the room reports rather than a bare
boolean that says "true" for both "read ten seconds ago" and "read yesterday".

``fresh``      read within the TTL
``expired``    older than the TTL, still served, still refreshable
``stale``      a read that did not complete; served with its reason
``absent``     nothing cached, which is the honest answer for a room whose seller
               authored it without CRM context
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.crm_integration import vocabulary
from dsr.store import RecordStore

#: The collection the cached panels live in. Owned by this feature.
SNAPSHOTS = "crm_read_snapshot"

#: The read outcomes that leave a snapshot servable. Anything else is stored as
#: ``stale``: a panel built from a partial read is worth showing with its gap
#: named, and is not worth serving as though it were complete.
COMPLETE_OUTCOMES: frozenset[str] = frozenset({"complete", "empty", "capability_fallback"})


def utcnow() -> datetime:
    """The clock this package reads. One function, so a test can reason about it."""
    return datetime.now(timezone.utc)


def parse_iso(text: str | None) -> datetime | None:
    """Read an ISO timestamp, returning ``None`` rather than raising."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find(store: RecordStore, identity_id: str, room_id: str | None = None) -> dict[str, Any] | None:
    """The snapshot for one identity, newest first.

    Keyed by ``identity_id`` rather than by buyer email, because the read is
    scoped to a resolved identity and two identities for one buyer in one room
    are refused at registration.
    """
    found = store.list(SNAPSHOTS, room_id=room_id, limit=1000, order_by="created_at")
    for record in found:
        if record["data"].get("identity_id") == identity_id:
            return record
    return None


def snapshots(store: RecordStore, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every cached panel, optionally for one room."""
    return store.list(SNAPSHOTS, room_id=room_id, limit=1000, order_by="created_at")


def ttl_of(data: dict[str, Any]) -> int:
    """The TTL a snapshot was written with, bounded to the short range."""
    return vocabulary.clamp_ttl(data.get("ttl_seconds"))


def age_seconds(data: dict[str, Any], now: datetime | None = None) -> float | None:
    """How long ago the read happened, in seconds, or ``None`` if unreadable.

    A snapshot whose timestamp cannot be parsed reports ``None`` rather than
    zero. Zero would read as "just read", which is the one answer that would let
    a room serve a snapshot of unknown age as though it were current.
    """
    read_at = parse_iso(data.get("read_at"))
    if read_at is None:
        return None
    moment = now or utcnow()
    return max(0.0, (moment - read_at).total_seconds())


def state(record: dict[str, Any] | None, now: datetime | None = None) -> str:
    """Which of the four states this snapshot is in."""
    if record is None:
        return "absent"
    data = record.get("data") or {}
    if data.get("outcome") not in COMPLETE_OUTCOMES:
        return "stale"
    age = age_seconds(data, now)
    if age is None:
        return "expired"
    return "fresh" if age <= ttl_of(data) else "expired"


def is_fresh(record: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Whether a read-through read has to be served from cache."""
    return state(record, now) == "fresh"


def expires_at(data: dict[str, Any], now: datetime | None = None) -> str | None:
    """When this snapshot stops being fresh, as an ISO timestamp."""
    age = age_seconds(data, now)
    if age is None:
        return None
    read_at = parse_iso(data.get("read_at"))
    if read_at is None:
        return None
    return (read_at + timedelta(seconds=ttl_of(data))).isoformat()


def build_snapshot(
    identity: dict[str, Any],
    panel: dict[str, Any],
    reads: dict[str, Any],
    *,
    outcome: str,
    now: datetime | None = None,
    fills: list[str] | None = None,
    unlabelled: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The payload a snapshot stores.

    Built here rather than by the engine so the shape is stated once. It carries
    the read's own facts beside the panel: which outcome, how many pages, which
    display labels were filled from the room's own option sets, and which option
    fields are still showing a raw value. A cached panel with no record of how it
    was built is a panel nobody can audit.
    """
    data = identity.get("data") or {}
    moment = now or utcnow()
    return {
        "identity_id": identity["id"],
        "system": data.get("system"),
        "buyer_email": data.get("buyer_email"),
        "panel": panel,
        "reads": reads,
        "outcome": outcome,
        "pages": sum(int((page or {}).get("pages") or 1) for page in (reads or {}).values()),
        "display_labels": bool(data.get("display_labels")),
        "fallback_fills": sorted(set(fills or [])),
        "unlabelled": unlabelled or [],
        "ttl_seconds": vocabulary.clamp_ttl(data.get("ttl_seconds")),
        "read_at": moment.isoformat(),
        "refresh_mode": vocabulary.REFRESH_MODE,
    }


def describe_cache(record: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    """The cache facts a client renders beside the panel.

    ``state`` is the word, and ``served_from_cache`` is the boolean. They are
    both there because a page that shows only one of them can either hide a stale
    panel or over-explain a fresh one.
    """
    if record is None:
        return {
            "state": "absent",
            "served_from_cache": False,
            "age_seconds": None,
            "ttl_seconds": vocabulary.CACHE_TTL_SECONDS,
            "expires_at": None,
            "read_at": None,
            "refresh_mode": vocabulary.REFRESH_MODE,
            "refresh_alternative": vocabulary.REFRESH_MODE_ALTERNATIVE,
            "ttl_bounds": [
                vocabulary.CACHE_TTL_MIN_SECONDS,
                vocabulary.CACHE_TTL_MAX_SECONDS,
            ],
        }
    data = record.get("data") or {}
    moment = now or utcnow()
    return {
        "state": state(record, moment),
        "served_from_cache": state(record, moment) != "absent",
        "age_seconds": age_seconds(data, moment),
        "ttl_seconds": ttl_of(data),
        "expires_at": expires_at(data, moment),
        "read_at": data.get("read_at"),
        "outcome": data.get("outcome"),
        "pages": data.get("pages"),
        "display_labels": data.get("display_labels"),
        "fallback_fills": data.get("fallback_fills") or [],
        "unlabelled": data.get("unlabelled") or [],
        "refresh_mode": vocabulary.REFRESH_MODE,
        "refresh_alternative": vocabulary.REFRESH_MODE_ALTERNATIVE,
        "ttl_bounds": [vocabulary.CACHE_TTL_MIN_SECONDS, vocabulary.CACHE_TTL_MAX_SECONDS],
    }


def cache_summary(
    store: RecordStore, room_id: str | None, now: datetime | None = None
) -> dict[str, Any]:
    """How the room's cache is doing, by state."""
    moment = now or utcnow()
    counted: dict[str, int] = {name: 0 for name in vocabulary.CACHE_STATES}
    for record in snapshots(store, room_id=room_id):
        counted[state(record, moment)] += 1
    return {
        **counted,
        "default_ttl_seconds": vocabulary.CACHE_TTL_SECONDS,
        "refresh_mode": vocabulary.REFRESH_MODE,
        "ttl_bounds": [vocabulary.CACHE_TTL_MIN_SECONDS, vocabulary.CACHE_TTL_MAX_SECONDS],
    }


__all__ = [
    "COMPLETE_OUTCOMES",
    "SNAPSHOTS",
    "age_seconds",
    "build_snapshot",
    "cache_summary",
    "describe_cache",
    "expires_at",
    "find",
    "is_fresh",
    "parse_iso",
    "snapshots",
    "state",
    "ttl_of",
    "utcnow",
]
