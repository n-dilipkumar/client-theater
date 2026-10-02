"""The researched report: portfolio metrics, the trend, and Top content.

Three reads of one compilation, not three separate aggregations. The researched
pages all slice the same activity the same way - "Number of assets, Content
shares, Content client views, Utilization rate, Engagement rate, Content
engagement over time, Top content" - and a portfolio tile that disagreed with
the table beneath it about how many shares a filter matched would be a bug
report about the report, not about the data.

The two researched rates, verbatim
----------------------------------
"**Utilization rate:** the % of content that has been shared at least once."
"**Engagement rate:** the % of content has been viewed at least once."

Both are *proportions of the asset set in scope*, and the sources do not say
what the denominator is. It is the filtered asset set, and every response
carries ``assets_in_scope`` so a reader can divide it themselves and see that
the denominator is what the report says it is. With no assets the rate is
``None``, not ``0.0``: a rate computed over nothing is not zero, and a Content
Influence report over an empty library is exactly the state the research warns
about ("this report relies on your Content Management Library having been built
out"), so the response says so in ``library_built_out`` rather than reporting
a confident 0%.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from dsr.influence.errors import (
    AssetOutOfScope,
    UnknownAsset,
    UnknownChoice,
    UnknownRoom,
)
from dsr.influence.events import load_events
from dsr.influence.vocab import (
    ASSET_COLLECTION,
    DEFAULT_TOP_CONTENT_SORT,
    DOWNLOADED,
    GRAINS,
    INTERNAL,
    PORTFOLIO_METRICS,
    ROOM_COLLECTION,
    SHARED,
    TOP_CONTENT_COLUMNS,
    Filters,
    asset_collections,
    asset_title,
    bucket_key,
    bucket_span,
    iso,
    scan,
    utcnow,
)
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass
class AssetRow:
    """One asset's influence, in the shape both Top content and the trend read."""

    asset_id: str
    title: str
    kind: str
    collections: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    room_id: str | None = None

    shares: int = 0
    client_views: int = 0
    internal_views: int = 0
    downloads: int = 0
    total_time_seconds: int = 0
    share_events: int = 0

    last_share_at: str | None = None
    last_view_at: str | None = None
    last_download_at: str | None = None
    last_activity_at: str | None = None

    rooms: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "title": self.title,
            "kind": self.kind,
            "collections": list(self.collections),
            "tags": list(self.tags),
            "room_id": self.room_id,
            "shares": self.shares,
            "views": self.client_views,
            "internal_views": self.internal_views,
            "downloads": self.downloads,
            "total_time_seconds": self.total_time_seconds,
            "last_share_at": self.last_share_at,
            "last_view_at": self.last_view_at,
            "last_download_at": self.last_download_at,
            "last_activity_at": self.last_activity_at,
            "workspaces": len(self.rooms),
            "people": len(self.people),
            "accounts": list(self.accounts),
            "utilized": self.shares > 0,
            "engaged": self.client_views > 0,
        }

    def sort_value(self, column: str) -> Any:
        """The value a Top content sort orders on.

        The map is written out rather than ``getattr(self, column)`` because the
        researched column name and the field name differ: the report says
        "views" and the row counts *client* views, with internal views counted
        separately. Letting the column name reach the attribute directly is how
        "views" ends up sorting on nothing.

        Missing timestamps sort as ``""`` rather than ``None`` so a sort by
        ``last_share_at`` does not raise on an asset that was never shared, and
        so the never-shared assets land together at one end of the table
        instead of interleaving with real dates.
        """
        columns = {
            "title": self.title,
            "shares": self.shares,
            "views": self.client_views,
            "downloads": self.downloads,
            "total_time_seconds": self.total_time_seconds,
            "last_share_at": self.last_share_at or "",
            "last_view_at": self.last_view_at or "",
        }
        return columns.get(column, "")


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #


@dataclass
class Report:
    """One filter set, compiled once, read three ways."""

    filters: Filters
    rows: list[AssetRow] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    assets_truncated: bool = False
    room: dict[str, Any] | None = None
    library_size: int = 0
    """How many assets the library holds, before any filter.

    Kept apart from :attr:`rows` on purpose: ``library_built_out`` is a fact
    about the library, and a workspace-scoped report over a workspace that has
    used no content has an empty *scope* without the library being unbuilt. One
    flag for both would tell a reader to go and build a library that exists.
    """

    def row(self, asset_id: str) -> AssetRow | None:
        for candidate in self.rows:
            if candidate.asset_id == asset_id:
                return candidate
        return None

    def countable_events(self) -> list[dict[str, Any]]:
        """The events that a metric over this asset set may count.

        Not every event that passed the window filter: an event naming an asset
        the collection filter excluded has nothing to be counted against. Without
        this the trend graph and the portfolio tiles would answer two different
        questions about the same filter - the graph counting a share of an asset
        the tiles have excluded - and that is exactly the kind of disagreement
        that gets filed as "the numbers do not add up".
        """
        known = {row.asset_id for row in self.rows}
        return [event for event in self.events if str(event.get("asset_id")) in known]

    @property
    def assets_in_scope(self) -> int:
        return len(self.rows)

    def portfolio(self) -> dict[str, Any]:
        """The five researched portfolio metrics, plus what went into them."""
        shares = sum(row.shares for row in self.rows)
        client_views = sum(row.client_views for row in self.rows)
        downloads = sum(row.downloads for row in self.rows)
        utilized = sum(1 for row in self.rows if row.shares > 0)
        engaged = sum(1 for row in self.rows if row.client_views > 0)
        total = self.assets_in_scope
        return {
            # The research's own names, in its own order.
            "number_of_assets": total,
            "content_shares": shares,
            "content_client_views": client_views,
            "utilization_rate": _rate(utilized, total),
            "engagement_rate": _rate(engaged, total),
            # What the two rates are a proportion *of*, and the numerator, so a
            # reader never has to guess which denominator was used.
            "utilization_rate_numerator": utilized,
            "engagement_rate_numerator": engaged,
            "assets_in_scope": total,
            "utilized_assets": utilized,
            "engaged_assets": engaged,
            # Not in the researched list, but every one of these is a column of
            # Top content and a reader of the tiles will ask for the rollup.
            "downloads": downloads,
            "internal_views": sum(row.internal_views for row in self.rows),
            "total_time_seconds": sum(row.total_time_seconds for row in self.rows),
            "workspaces_engaged": len({room for row in self.rows for room in row.rooms}),
            "library_built_out": self.library_size > 0,
            "library_size": self.library_size,
        }


def _rate(numerator: int, denominator: int) -> float | None:
    """A researched rate, or ``None`` when there is nothing to be a rate of."""
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 2)


def _require_room(store: RecordStore, room_id: str) -> dict[str, Any]:
    record = store.get(room_id)
    if record is None or record.get("collection") != ROOM_COLLECTION:
        raise UnknownRoom(room_id)
    return record


def _asset_rows(
    store: RecordStore,
    filters: Filters,
    only: set[str] | None = None,
) -> tuple[list[AssetRow], int, bool]:
    """The asset set in scope, before any event is counted.

    A collection filter is applied here, so an asset that was never engaged with
    is still in the denominator: "the % of content that has been shared at least
    once" is a statement about the library, and dropping the unused assets would
    make every library report a 100% library.

    ``only`` is the workspace case, and it inverts that rule on purpose. Scoped
    to one workspace, the question is "of the content this workspace used, how
    much was shared", so the denominator is the assets that had activity *in that
    workspace* - not the whole library. Keeping the whole library there would
    report "23 assets, 0% utilized" for a room that shared two of them, which
    answers a question nobody asked.

    Returns ``(rows, library_size, truncated)``, where the size counts every
    asset the library holds regardless of the filters.
    """
    scanned = scan(store, ASSET_COLLECTION)
    rows: list[AssetRow] = []
    for record in scanned.records:
        if only is not None and str(record["id"]) not in only:
            continue
        data = record.get("data") or {}
        names = asset_collections(record)
        if filters.collection and filters.collection not in names:
            continue
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        row = AssetRow(
            asset_id=str(record["id"]),
            title=asset_title(record),
            kind=str(data.get("kind") or data.get("format") or ""),
            collections=names,
            tags=[str(tag) for tag in tags],
            room_id=record.get("room_id"),
        )
        rows.append(row)

    rows.sort(key=lambda row: (row.title, row.asset_id))
    return rows, len(scanned.records), scanned.truncated


def compile_report(store: RecordStore, filters: Filters | None = None) -> Report:
    """Read the library and the event log once, and reduce them to one report.

    Every read in this workflow goes through here, which is what makes the
    three surfaces agree: the portfolio tiles, the trend, and the table are
    three projections of the same numbers, and a filter that changes one cannot
    leave the other two describing a different question.

    The event log is read *before* the library, because a workspace-scoped report
    needs the events to know which assets that workspace used, and the library
    read needs to know that to pick a denominator. Doing it the other way round
    would force the denominator to be the whole library.
    """
    active = filters or Filters()
    # Resolved once, here, so an unknown room is a 404 even when the library is
    # empty, and so the room's own record is on the report for a caller to read.
    room = _require_room(store, active.room_id) if active.room_id else None

    events, truncated = load_events(store, room_id=active.room_id if active.room_id else None)
    selected: list[dict[str, Any]] = []
    for event in events:
        if active.room_id and event.get("room_id") != active.room_id:
            # A share straight from the library is not attributable to one
            # workspace, so a room-scoped report cannot claim it.
            continue
        if not active.in_window(str(event.get("action")), event["_at"]):
            continue
        selected.append(event)

    scoped_assets = {str(event.get("asset_id")) for event in selected} if active.room_id else None
    rows, library_size, assets_truncated = _asset_rows(store, active, only=scoped_assets)
    index = {row.asset_id: row for row in rows}

    for event in selected:
        row = index.get(str(event.get("asset_id")))
        if row is None:
            # The event names an asset the collection filter excluded, or one
            # that has since been deleted. It is counted nowhere, and the count
            # of events in scope is reported so the gap is visible.
            continue
        action = str(event.get("action"))
        when = iso(event["_at"])
        room_id = event.get("room_id")
        if action == SHARED:
            row.shares += 1
            row.share_events += 1
            row.last_share_at = _latest(row.last_share_at, when)
        elif action == DOWNLOADED:
            row.downloads += 1
            row.last_download_at = _latest(row.last_download_at, when)
        elif event.get("audience") == INTERNAL:
            row.internal_views += 1
            row.last_view_at = _latest(row.last_view_at, when)
        else:
            row.client_views += 1
            row.last_view_at = _latest(row.last_view_at, when)
        row.last_activity_at = _latest(row.last_activity_at, when)
        row.total_time_seconds += _seconds(event)
        if room_id and room_id not in row.rooms:
            row.rooms.append(str(room_id))
        for key, bucket in (("person", row.people), ("account", row.accounts)):
            value = event.get(key)
            if value and str(value) not in bucket:
                bucket.append(str(value))

    for row in rows:
        row.rooms.sort()
        row.people.sort()
        row.accounts.sort()

    report = Report(
        filters=active,
        rows=rows,
        events=selected,
        truncated=truncated,
        assets_truncated=assets_truncated,
        room=room,
        library_size=library_size,
    )
    return report


def _latest(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def _seconds(event: Mapping[str, Any]) -> int:
    value = event.get("seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value) if value > 0 else 0


# --------------------------------------------------------------------------- #
# Public reads
# --------------------------------------------------------------------------- #


def portfolio(store: RecordStore, filters: Filters | None = None) -> dict[str, Any]:
    """The researched portfolio metrics: tiles 1 to 5 of the report."""
    report = compile_report(store, filters)
    metrics = report.portfolio()
    return {
        "generated_at": iso(utcnow()),
        "filters": report.filters.as_dict(),
        "metrics": metrics,
        "metric_names": list(PORTFOLIO_METRICS),
        "room_id": report.filters.room_id,
        "library_built_out": metrics["library_built_out"],
        "truncated": report.truncated,
        "events_in_scope": len(report.events),
    }


def engagement(
    store: RecordStore,
    *,
    grain: str = "day",
    filters: Filters | None = None,
) -> dict[str, Any]:
    """The researched "Content engagement over time" graph.

    Day / week / month / quarter / year are the researched grains. Buckets with
    no activity are returned as zeros rather than omitted: a graph that drops
    the quiet days between two active ones draws a straight line through them
    and reads as activity that never happened.
    """
    if grain not in GRAINS:
        raise UnknownChoice(grain, "grain", GRAINS)
    report = compile_report(store, filters)
    events = report.countable_events()
    labels: list[str] = []
    for event in events:
        label = bucket_key(event["_at"], grain)
        if label not in labels:
            labels.append(label)
    labels.sort()

    # Zero-fill. Two spans, and both of them matter:
    #
    # * between the first and last event, so a fortnight with activity in its
    #   first and last week draws thirteen columns rather than two - otherwise
    #   the chart draws a straight line through the quiet days and reads as
    #   activity that never happened;
    # * across an explicit filter window, so a filter that selects one busy day
    #   of a quarter still shows the quarter around it.
    span_start = report.filters.activity_from or report.filters.shared_from
    span_end = report.filters.activity_to or report.filters.shared_to
    moments = [event["_at"] for event in events]
    if moments:
        earliest, latest = min(moments), max(moments)
        span_start = min(span_start, earliest) if span_start else earliest
        span_end = max(span_end, latest) if span_end else latest
    span_complete = True
    if span_start and span_end and span_start <= span_end:
        span, complete = bucket_span(span_start, span_end, grain)
        for label in span:
            if label not in labels:
                labels.append(label)
        span_complete = complete
        labels.sort()

    points: dict[str, dict[str, Any]] = {
        label: {
            "bucket": label,
            "views": 0,
            "internal_views": 0,
            "shares": 0,
            "downloads": 0,
            "assets": 0,
        }
        for label in labels
    }
    seen: dict[str, set[str]] = {label: set() for label in labels}
    for event in events:
        label = bucket_key(event["_at"], grain)
        point = points.get(label)
        if point is None:
            continue
        action = str(event.get("action"))
        if action == SHARED:
            point["shares"] += 1
        elif action == DOWNLOADED:
            point["downloads"] += 1
        # The graph's ``views`` series is the researched "content client views",
        # so it counts external views only and an internal view is its own
        # series. Drawing them on one line would quietly restate an internal
        # view as client engagement, which is the number the report is read for.
        elif event.get("audience") == INTERNAL:
            point["internal_views"] += 1
        else:
            point["views"] += 1
        asset_id = str(event.get("asset_id"))
        if asset_id not in seen[label]:
            seen[label].add(asset_id)
            point["assets"] += 1

    return {
        "grain": grain,
        "grains": list(GRAINS),
        "filters": report.filters.as_dict(),
        "room_id": report.filters.room_id,
        "points": [points[label] for label in labels],
        "count": len(labels),
        "span_complete": span_complete,
        "totals": {
            "views": sum(point["views"] for point in points.values()),
            "internal_views": sum(point["internal_views"] for point in points.values()),
            "shares": sum(point["shares"] for point in points.values()),
            "downloads": sum(point["downloads"] for point in points.values()),
        },
        "truncated": report.truncated,
    }


def top_content(
    store: RecordStore,
    *,
    sort: str = DEFAULT_TOP_CONTENT_SORT,
    direction: str = "desc",
    limit: int = 25,
    offset: int = 0,
    filters: Filters | None = None,
) -> dict[str, Any]:
    """ "Top content", sorted by any column.

    "The top content report is organized by most viewed content, but also shows
    additional reporting such as amount of shares, total time spent, download
    amount, last share, and last view" - and step 4 of the user flow is "sort by
    any column", so every one of those is a sort key and an unknown key is
    refused with the list rather than silently ignored.

    Ties break on asset id, always in the same direction-independent way, so
    paging a table with equal values cannot show the same row twice or skip one.
    """
    if sort not in TOP_CONTENT_COLUMNS:
        raise UnknownChoice(sort, "sort column", TOP_CONTENT_COLUMNS)
    if direction not in {"asc", "desc"}:
        raise UnknownChoice(direction, "sort direction", ("asc", "desc"))

    report = compile_report(store, filters)
    rows = list(report.rows)
    # Two passes, and the order matters. Sorting by id first makes asset id the
    # base order; the second sort is stable, so rows tied on the sort column keep
    # that id order. The tiebreak is therefore the same in both directions, which
    # is what makes paging a table full of equal values correct - a descending
    # sort with the id descending would show a row twice and skip another.
    rows.sort(key=lambda row: row.asset_id)
    rows.sort(key=lambda row: row.sort_value(sort), reverse=(direction == "desc"))

    window = rows[offset : offset + max(0, limit)]
    return {
        "sort": sort,
        "sort_columns": list(TOP_CONTENT_COLUMNS),
        "direction": direction,
        "filters": report.filters.as_dict(),
        "room_id": report.filters.room_id,
        "count": len(window),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rows": [row.to_dict() for row in window],
        "truncated": report.truncated,
    }


def asset_detail(
    store: RecordStore,
    asset_id: str,
    *,
    filters: Filters | None = None,
) -> dict[str, Any]:
    """One asset's influence, with the events behind every number on it.

    The evidence is included rather than summarised away because the report's
    whole claim is that content influences revenue, and a number with nothing
    behind it is a number nobody can check.
    """
    active = filters or Filters()
    report = compile_report(store, active)
    row = report.row(str(asset_id))
    if row is None:
        record = store.get(str(asset_id))
        if record is None or record.get("collection") != ASSET_COLLECTION:
            raise UnknownAsset(str(asset_id))
        # The asset is real and the filters excluded it. Saying "not found" here
        # would send a reader looking for a missing document instead of widening
        # the filter that hid it.
        excluded = []
        if active.collection:
            excluded.append(f"collection={active.collection!r}")
        if active.room_id:
            excluded.append(f"room_id={active.room_id!r}")
        if active.bounded:
            excluded.append("the active time windows")
        raise AssetOutOfScope(
            str(asset_id), "excluded by " + (", ".join(excluded) or "the room scope")
        )

    timeline = [
        {
            "id": event.get("id"),
            "action": event.get("action"),
            "audience": event.get("audience"),
            "at": iso(event["_at"]),
            "seconds": event.get("seconds", 0),
            "room_id": event.get("room_id"),
            "person": event.get("person"),
            "account": event.get("account"),
        }
        for event in report.events
        if str(event.get("asset_id")) == str(asset_id)
    ]
    timeline.sort(key=lambda entry: str(entry["at"]))

    return {
        "asset": row.to_dict(),
        "events": timeline,
        "count": len(timeline),
        "filters": report.filters.as_dict(),
        "truncated": report.truncated,
    }


def collections(store: RecordStore) -> dict[str, Any]:
    """Every collection name in the library, with its asset count.

    Served rather than computed in the browser so the filter picker offers the
    names that actually exist, including the ones that only appear in a
    deployment that filed an asset under a single tag.
    """
    scanned = scan(store, ASSET_COLLECTION)
    counts: dict[str, int] = {}
    for record in scanned.records:
        for name in asset_collections(record):
            counts[name] = counts.get(name, 0) + 1
    names = [
        {"collection": name, "assets": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "collections": names,
        "count": len(names),
        "assets": len(scanned.records),
        "unfiled_assets": sum(1 for record in scanned.records if not asset_collections(record)),
        "truncated": scanned.truncated,
    }


def vocabulary() -> dict[str, Any]:
    """Every enumerable choice this surface accepts.

    Published rather than implied so a client renders its pickers from the same
    source the request validation enforces against.
    """
    return {
        "grains": list(GRAINS),
        "metrics": list(PORTFOLIO_METRICS),
        "sort_columns": list(TOP_CONTENT_COLUMNS),
        "default_sort": DEFAULT_TOP_CONTENT_SORT,
        "actions": ["viewed", "shared", "downloaded"],
        "action_spellings": ["asset.viewed", "asset.shared", "asset.downloaded"],
        "windows": {
            "activity": ["activity_from", "activity_to"],
            "shares": ["shared_from", "shared_to"],
        },
        "filters": ["collection", "room_id"],
    }
