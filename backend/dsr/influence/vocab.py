"""The vocabulary, the collections, and the scanning helpers WF-019 reads.

Everything in this module is a *reading* concern. The researched report
"analyzes activity of library assets across all workspaces", so it spans the
whole library rather than one room, and the numbers are aggregated here rather
than pushed into SQL: the store is schema-flexible and a library collection
membership is a JSON *array*, which the dynamic index cannot answer a
``contains`` question about. :meth:`AuditedDatabase.find` matches a scalar at a
dotted path, so ``find("document", {"collections": "Enterprise"})`` finds
nothing for an asset whose ``collections`` is ``["Enterprise"]``. The
aggregation is therefore done in Python over a paged read, which is also what
lets the report stay correct the day a team adds ``data.region`` to an asset.

Collections, and where each one comes from in the research
-------------------------------------------------------
``document``
    The Content Management Library. The research's caveat - "this report relies
    on your Content Management Library having been built out" - is about this
    collection, and :mod:`dsr.influence.metrics` reports whether it is.
``room``
    A workspace. The research's filters are per-workspace and the revenue join
    is "accounts & deals/opportunities connected to workspaces".
``activity``
    Workspace activity, written by the rest of the product. :mod:`dsr.influence.events`
    projects the three asset-relevant actions out of it.
``content_event``
    This workflow's own normalised event log, one row per occurrence of
    ``asset.viewed`` / ``asset.shared`` / ``asset.downloaded``. Both the researched
    webhooks and the core ``activity`` collection feed it, so every number in the
    report reads one shape.
``crm_account`` / ``crm_deal``
    The CRM side of the join. Names chosen by this feature; see
    :data:`ACCOUNT_COLLECTION` and :data:`DEAL_COLLECTION`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.influence.errors import InvalidWindow, UnparseableTime
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

ASSET_COLLECTION = "document"
"""The Content Management Library. The research's ``GET /v1/assets``."""

ROOM_COLLECTION = "room"
"""A workspace. The research filters by collection and by client activity."""

ACTIVITY_COLLECTION = "activity"
"""Workspace activity written by the rest of the product, and projected back."""

EVENT_COLLECTION = "content_event"
"""This workflow's normalised log of asset share / view / download occurrences."""

ACCOUNT_COLLECTION = "crm_account"
"""A CRM account. Namespaced ``crm_`` because the research's preconditions are
about the CRM, and because the closest existing owner of that idea (WF-016)
already owns ``crm_field`` / ``crm_event`` / ``crm_activity``; a hundred
features landing at once is exactly when two of them quietly share a collection
and each one reads the other's rows."""

DEAL_COLLECTION = "crm_deal"
"""A CRM deal or opportunity, linked to a workspace and naming the assets it is
attributed to. Read tolerantly: a row missing any of those is reported as a
blocker or an unassociated link, never raised on, because a CRM sync writing
into this collection must not be able to take the report down."""

# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #

#: "Content engagement over time graph (day / week / month / quarter / year)".
GRAINS: tuple[str, ...] = ("day", "week", "month", "quarter", "year")

#: The researched webhooks, and the two words "most viewed content" implies.
#:
#: Dock documents ``asset.viewed``, ``asset.shared`` and ``asset.downloaded``.
#: The product's own ``activity`` collection says ``viewed``, ``shared`` and
#: ``downloaded``, and the room-analytics feature in this same codebase writes
#: ``viewed_document`` and ``downloaded_document`` for the same two facts. All
#: of them land in the same three canonical actions, because the research's
#: numbers must be computable from whichever source a deployment happens to have
#: - otherwise the backfill silently drops a third of the activity in the room a
#: reader is looking at. Recording another spelling needs no migration: one entry
#: in this map.
VIEWED = "viewed"
SHARED = "shared"
DOWNLOADED = "downloaded"

ACTIONS: tuple[str, ...] = (VIEWED, SHARED, DOWNLOADED)

ACTION_ALIASES: dict[str, str] = {
    # The researched webhook vocabulary.
    "asset.viewed": VIEWED,
    "asset.shared": SHARED,
    "asset.downloaded": DOWNLOADED,
    # The activity collection this product writes.
    "viewed": VIEWED,
    "shared": SHARED,
    "downloaded": DOWNLOADED,
    "view": VIEWED,
    "share": SHARED,
    "download": DOWNLOADED,
    # The room-analytics taxonomy, which names the same two facts with the
    # object in the verb.
    "viewed_document": VIEWED,
    "downloaded_document": DOWNLOADED,
}

INTERNAL = "internal"
EXTERNAL = "external"
AUDIENCES: tuple[str, ...] = (INTERNAL, EXTERNAL)

#: Actions that are client activity, and so answer to the client-activity window.
CLIENT_ACTIONS: frozenset[str] = frozenset({VIEWED, DOWNLOADED})

#: "the top content report is organized by most viewed content, but also shows
#: additional reporting such as amount of shares, total time spent, download
#: amount, last share, and last view" - and "sort by any column" is step 4 of the
#: user flow, so these are the columns a caller may name.
TOP_CONTENT_COLUMNS: tuple[str, ...] = (
    "title",
    "shares",
    "views",
    "downloads",
    "total_time_seconds",
    "last_share_at",
    "last_view_at",
)

DEFAULT_TOP_CONTENT_SORT = "views"

#: The five portfolio metrics the research enumerates, in its own order.
PORTFOLIO_METRICS: tuple[str, ...] = (
    "number_of_assets",
    "content_shares",
    "content_client_views",
    "utilization_rate",
    "engagement_rate",
)

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

_PAGE = 500
"""Rows per page when scanning. The store caps a page at 1000."""

MAX_EVENTS = 20_000
"""Hard ceiling on the events one report will read.

A report that silently aggregates a truncated slice of the event log would report
confident numbers that are wrong, which is the one thing an influence report
must never do. Past this ceiling the report stops scanning and says so in
``truncated``, so a reader knows the numbers are partial.
"""


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    """Timezone-aware UTC now. Every timestamp this module writes is aware."""
    return datetime.now(timezone.utc)


def parse_time(value: Any, what: str = "timestamp") -> datetime | None:
    """Parse an ISO-8601 timestamp into aware UTC, or raise.

    ``None`` and an empty string mean "not supplied", which is different from
    "unparseable": the researched filters all have an open end by default, and a
    missing bound must not be an error.

    A timestamp with no offset is read as UTC. The store's own timestamps are
    written with an offset by :func:`dsr.db.audited.utcnow`, and the researched
    webhooks carry offsets, so this only matters for a hand-written row - and
    silently reading it as local time would move an event across a bucket
    boundary, so UTC is the documented reading.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # A POSIX timestamp. The product's own activity rows use ISO strings, so
        # this is a convenience, not a documented format.
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise UnparseableTime(value, what) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """Render an aware datetime the way the store writes timestamps."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def bucket_key(moment: datetime, grain: str) -> str:
    """The researched chart's bucket label for one moment.

    Buckets are UTC calendar buckets and a week starts on Monday. Neither is
    documented by the sources; both are recorded in
    :mod:`dsr.influence.inferences` so a reviewer can disagree with the choice
    by name rather than by reading the arithmetic.
    """
    if grain == "day":
        return moment.strftime("%Y-%m-%d")
    if grain == "week":
        monday = moment - timedelta(days=moment.weekday())
        return monday.strftime("%Y-%m-%d")
    if grain == "month":
        return moment.strftime("%Y-%m")
    if grain == "quarter":
        return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"
    if grain == "year":
        return str(moment.year)
    raise UnparseableTime(grain, "grain")


def _add_bucket(start: datetime, grain: str) -> datetime:
    """The start of the bucket after the one beginning at ``start``.

    Walks calendar boundaries rather than adding a fixed number of days, so a
    quarter series rolls Q4 into the next year and a month series handles
    December. It is what makes a zero-filled trend span the filter window
    correctly rather than drifting a day per month.
    """
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    if grain == "month":
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    if grain == "quarter":
        if start.month >= 10:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 3)
    return start.replace(year=start.year + 1)


def bucket_range(first: datetime, last: datetime, grain: str) -> list[str]:
    """Every bucket label from the first event's bucket to the last one's.

    Bounded by the window rather than by the data, so a filter that selects one
    busy day in a quarter still draws a quarter of quiet days around it.
    """
    return bucket_span(first, last, grain)[0]


def bucket_span(first: datetime, last: datetime, grain: str) -> tuple[list[str], bool]:
    """The labels from ``first``'s bucket to ``last``'s, and whether it is all of them.

    The completeness flag is what keeps a bounded walk honest: two events three
    years apart at day grain would be a thousand empty columns, so the walk
    stops at :data:`MAX_EVENTS` and says the series is partial rather than
    drawing a chart that is quietly wrong.
    """
    labels: list[str] = []
    cursor = _floor(first, grain)
    final = _floor(last, grain)
    for _ in range(MAX_EVENTS):
        if cursor > final:
            return labels, True
        labels.append(bucket_key(cursor, grain))
        cursor = _add_bucket(cursor, grain)
    return labels, False


def _floor(moment: datetime, grain: str) -> datetime:
    if grain == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "week":
        start = moment - timedelta(days=moment.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "month":
        return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if grain == "quarter":
        month = (moment.month - 1) // 3 * 3 + 1
        return moment.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return moment.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Filters:
    """The researched filter set, and the only two time windows in the report.

    Step 6 of the user flow is "Filter by collection, client-activity time range,
    and shares time range" - two *independent* ranges, not one. The sources do
    not say whether a view outside the shares window still counts, so each range
    is applied to the events it names: the shares window bounds shares, and the
    client-activity window bounds views and downloads. Recorded in
    :mod:`dsr.influence.inferences`.
    """

    collection: str | None = None
    activity_from: datetime | None = None
    activity_to: datetime | None = None
    shared_from: datetime | None = None
    shared_to: datetime | None = None
    room_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        collection: str | None = None,
        activity_from: Any = None,
        activity_to: Any = None,
        shared_from: Any = None,
        shared_to: Any = None,
        room_id: str | None = None,
    ) -> "Filters":
        start = parse_time(activity_from, "client activity from")
        end = parse_time(activity_to, "client activity to")
        share_start = parse_time(shared_from, "shares from")
        share_end = parse_time(shared_to, "shares to")
        if start and end and start > end:
            raise InvalidWindow("client activity", activity_from, activity_to)
        if share_start and share_end and share_start > share_end:
            raise InvalidWindow("shares", shared_from, shared_to)
        return cls(
            collection=(str(collection).strip() or None) if collection else None,
            activity_from=start,
            activity_to=end,
            shared_from=share_start,
            shared_to=share_end,
            room_id=room_id or None,
        )

    @property
    def bounded(self) -> bool:
        """Whether any bound at all was supplied."""
        return any(
            value is not None
            for value in (
                self.activity_from,
                self.activity_to,
                self.shared_from,
                self.shared_to,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        """The filter set as JSON, so a report says what it was computed over."""
        return {
            "collection": self.collection,
            "activity_from": iso(self.activity_from),
            "activity_to": iso(self.activity_to),
            "shared_from": iso(self.shared_from),
            "shared_to": iso(self.shared_to),
            "room_id": self.room_id,
        }

    def window_for(self, action: str) -> tuple[datetime | None, datetime | None]:
        """The window that bounds one action's events."""
        if action == SHARED:
            return self.shared_from, self.shared_to
        return self.activity_from, self.activity_to

    def in_window(self, action: str, moment: datetime) -> bool:
        """Whether an event at ``moment`` counts toward ``action``'s numbers."""
        start, end = self.window_for(action)
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True


# --------------------------------------------------------------------------- #
# Normalising asset records
# --------------------------------------------------------------------------- #


def asset_collections(record: Mapping[str, Any]) -> list[str]:
    """Every collection name an asset belongs to.

    Accepts a ``collections`` array (the shape a library tool writes), a
    ``collection`` string, and a ``tags`` array, because the research names
    "Library Views & Tags organisation" as part of the same surface and a
    deployment that filed an asset under a single tag rather than a collection
    should still be reachable by filtering. All three are read, none is
    required.
    """
    data = record.get("data") or {}
    names: list[str] = []
    for key in ("collections", "collection", "tags", "tag"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            candidates = [value]
        elif isinstance(value, (list, tuple)):
            candidates = [str(item) for item in value if item is not None]
        else:
            continue
        for candidate in candidates:
            name = candidate.strip()
            if name and name not in names:
                names.append(name)
    return names


def asset_title(record: Mapping[str, Any]) -> str:
    """A human label for an asset, whichever field the deployment used."""
    data = record.get("data") or {}
    for key in ("title", "name", "filename", "label"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(record.get("id", ""))


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


@dataclass
class Scan:
    """The result of a paged read, and whether it had to stop early."""

    records: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


def scan(
    store: RecordStore,
    collection: str,
    *,
    room_id: str | None = None,
    limit: int | None = None,
) -> Scan:
    """Every live record in a collection, paged so nothing is truncated.

    ``limit`` is the ceiling on how many records are read, and it defaults to
    :data:`MAX_EVENTS` for the event log. Hitting the ceiling sets
    ``truncated`` rather than quietly returning a short read, because a report
    that does not know it was cut off reports confident numbers that are wrong.
    """
    ceiling = MAX_EVENTS if limit is None else limit
    result = Scan()
    offset = 0
    while True:
        page = store.list(
            collection, room_id=room_id, limit=_PAGE, offset=offset, order_by="id", descending=False
        )
        for record in page:
            if len(result.records) >= ceiling:
                result.truncated = True
                return result
            result.records.append(record)
        if len(page) < _PAGE:
            return result
        offset += _PAGE
