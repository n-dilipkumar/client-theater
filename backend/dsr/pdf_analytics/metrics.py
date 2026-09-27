"""The arithmetic of the three researched analytics, as pure functions.

Nothing here touches the store. Every function takes plain dicts - the
``data`` payloads a record holds - and returns plain data, so each rule the
research states can be tested against a literal example rather than through a
database, and the book in :mod:`.book` stays a thin read/write shell.

What the research names, and where it lands here:

* "**Time spent per page:** the average amount of time that's spent per page"
  -> :func:`dwell_per_page`.
* "**Drop off per page:** understand when someone stops looking at your
  content" -> :func:`drop_off_per_page`.
* "**Video Analytics** - for self-hosted videos ... the average watch time"
  -> :func:`average_watch_time`.
* "Dock's analytics only show engagement from external users" ->
  :func:`core_counts` and :func:`bucket_series`, with ``shares`` as the one
  metric that inverts.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .vocabulary import GRAINS, INTERNAL_ONLY_METRICS, SESSION_GAP_SECONDS

# --------------------------------------------------------------------------- #
# Buckets
# --------------------------------------------------------------------------- #


def floor(moment: datetime, grain: str) -> str:
    """The start of the bucket ``moment`` belongs to, as an ISO date.

    Weeks start on Monday and months on the 1st. Neither is sourced - the
    research names "Core Analytics bar charts" without an axis vocabulary - so
    the whole set is an inference (see :mod:`.inferences`).
    """
    if grain == "month":
        return moment.date().replace(day=1).isoformat()
    if grain == "week":
        return (moment.date() - timedelta(days=moment.date().weekday())).isoformat()
    return moment.date().isoformat()

# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp, treating a naive one as UTC.

    A viewer that does not send an offset is assumed to be in UTC rather than
    the server's local zone, because every other timestamp in this product is
    UTC and a bucket that silently shifted by the machine's offset would be
    indistinguishable from real traffic.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith(("z", "Z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fallback_ts(value: Any, moment: datetime) -> datetime:
    """A row with an unreadable timestamp sorts last, deterministically."""
    return parse_ts(value) or moment


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Reading sessions
# --------------------------------------------------------------------------- #


def group_sessions(
    rows: Sequence[Mapping[str, Any]], *, gap_seconds: int = SESSION_GAP_SECONDS
) -> list[dict[str, Any]]:
    """Group per-page timing rows into the readings they belong to.

    A drop-off curve is a statement about *readers*, not about rows: "of the
    people who reached page 4, this many never saw page 5" is meaningless
    unless the rows can be attributed to a person who was reading continuously.
    So rows are gathered into a session first, and the curve is computed over
    sessions.

    Two ways into a session, in order of preference:

    1. an explicit ``sessionId``, because a viewer that opened a document knows
       when it closed the document and the research's data flow has the viewer
       emitting the timing;
    2. otherwise the same ``viewer``, split whenever two consecutive timings are
       more than ``gap_seconds`` apart. A reader who closed the tab for lunch
       has started a new reading, and counting them as one reader all the way
       through would flatter the tail of the curve.

    A row that names no viewer at all is grouped under ``anonymous``, which is
    the trackable-link case: an unauthenticated buyer still reads pages, and
    their curve is worth having even though it cannot be attributed.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = str(row.get("sessionId") or f"viewer:{row.get('viewer') or 'anonymous'}")
        grouped.setdefault(key, []).append(row)

    sessions: list[dict[str, Any]] = []
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    for key, group in grouped.items():
        # Ordered by time only. A tie is broken by page so that two rows in the
        # same millisecond still read in document order, and `sort` being stable
        # keeps the caller's order for anything else.
        ordered = sorted(
            group, key=lambda r: (_fallback_ts(r.get("occurredAt"), earliest), int(r.get("page") or 0))
        )
        run: list[Mapping[str, Any]] = []
        for row in ordered:
            if run:
                gap = (
                    _fallback_ts(row.get("occurredAt"), earliest)
                    - _fallback_ts(run[-1].get("occurredAt"), earliest)
                ).total_seconds()
                if gap > gap_seconds:
                    sessions.append(_summarise(key, run))
                    run = []
            run.append(row)
        if run:
            sessions.append(_summarise(key, run))

    sessions.sort(key=lambda s: (str(s.get("startedAt") or ""), s["id"]))
    return sessions


def _summarise(key: str, run: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pages = [int(row.get("page") or 0) for row in run if int(row.get("page") or 0) > 0]
    stamps = [parse_ts(row.get("occurredAt")) for row in run]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return {
        "id": key,
        "viewer": str(run[0].get("viewer") or ""),
        "startedAt": min(stamps).isoformat() if stamps else None,
        "endedAt": max(stamps).isoformat() if stamps else None,
        "rows": len(run),
        "pages_visited": sorted(set(pages)),
        "deepest_page": max(pages) if pages else 0,
        "total_seconds": round(sum(float(row.get("seconds") or 0.0) for row in run), 3),
    }


# --------------------------------------------------------------------------- #
# PDF Analytics
# --------------------------------------------------------------------------- #


def dwell_per_page(
    rows: Sequence[Mapping[str, Any]], *, page_count: int | None = None
) -> list[dict[str, Any]]:
    """**Time spent per page**: the average seconds a reader spends on a page.

    Every page of the document appears, including the ones nobody read. A table
    that quietly stops at the last page somebody opened reads as "the deck ends
    here", which is the opposite of what a seller needs: the point of this
    metric is to find the pages that *did not* land.
    """
    totals: dict[int, dict[str, float]] = {}
    for row in rows:
        page = int(row.get("page") or 0)
        if page < 1:
            continue
        bucket = totals.setdefault(page, {"reads": 0.0, "seconds": 0.0})
        bucket["reads"] += 1
        bucket["seconds"] += float(row.get("seconds") or 0.0)

    highest = max(totals) if totals else 0
    pages = max(int(page_count or 0), highest)
    result: list[dict[str, Any]] = []
    for page in range(1, max(1, pages) + 1):
        bucket = totals.get(page, {"reads": 0.0, "seconds": 0.0})
        reads = int(bucket["reads"])
        seconds = round(bucket["seconds"], 3)
        result.append(
            {
                "page": page,
                "reads": reads,
                "total_seconds": seconds,
                "average_seconds": round(seconds / reads, 1) if reads else None,
            }
        )
    return result


def drop_off_per_page(
    sessions: Sequence[Mapping[str, Any]], *, page_count: int
) -> dict[str, Any]:
    """**Drop off per page**: where readers stop.

    A reader who reached page 7 read pages 1 to 6, so the reach count is
    monotone: ``reached[p]`` is the number of sessions that got to page ``p``
    *or further*. The alternative - counting the rows that name a page - would
    read a reader who scrolled from 1 straight to 5 as a total collapse at pages
    2, 3 and 4, which is a scroll, not a drop-off. The consequence is
    deliberate: a skipped page cannot show up as a drop-off here. It does show
    up in the per-page read counts (:func:`dwell_per_page`'s ``reads``), which
    is why the analytics block reports both.

    **The loss is attributed to the last page the reader reached**, so
    ``dropped[p]`` is ``reached[p] - reached[p+1]``: the share of readers who
    got to page ``p`` and then stopped looking. The last page reports no drop,
    because there is nothing after it to fall off from.

    That choice is the whole point of the metric, and it is not cosmetic. The
    other convention labels the loss at the page that was *not* reached, so a
    reader who stops on the cover is reported as a collapse on page 2 - and a
    seller deciding what to cut would cut page 2 when the real problem is page
    one. "Understand when someone stops looking at your content" is a question
    about the page they were looking at, not the one they never opened.

    The drops therefore sum to ``opening_page_readers - completed_sessions``:
    every reader is accounted for, on the page where they went quiet.

    A session that claims to have gone past ``page_count`` is clamped and
    counted, because that is what a re-uploaded, shortened document leaves
    behind and silently dropping it would make the tail look better than it is.

    The reach count is a binary search over the sorted session depths rather
    than a walk of every session for every page, so the cost is
    ``pages * log(sessions)`` and a heavily-read 400-page document does not
    become a request nobody waits for.
    """
    pages = max(1, int(page_count))
    total = len(sessions)
    depths = sorted(int(session.get("deepest_page") or 0) for session in sessions)
    beyond = total - bisect_right(depths, pages)
    opening = total - bisect_left(depths, 1)

    curve: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        reached = total - bisect_left(depths, page)
        if page < pages:
            dropped = reached - (total - bisect_left(depths, page + 1))
            rate = round(dropped / reached, 4) if reached else 0.0
        else:
            dropped, rate = 0, 0.0
        curve.append(
            {
                "page": page,
                "reached": reached,
                "retained_rate": round(reached / opening, 4) if opening else 0.0,
                "dropped": dropped,
                "drop_off_rate": rate,
                "is_last_page": page == pages,
            }
        )

    return {
        "page_count": pages,
        "sessions": total,
        "opening_page_readers": opening,
        "completed_sessions": total - bisect_left(depths, pages),
        "sessions_past_page_count": beyond,
        "curve": curve,
    }


# --------------------------------------------------------------------------- #
# Video Analytics
# --------------------------------------------------------------------------- #


def average_watch_time(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """**Video Analytics**: the average watch time of the video.

    One row per completed watch, so the average is over watches rather than
    over seconds-of-video, which is what "average watch time" means when a
    reader can pause and come back.
    """
    seconds = [float(row.get("seconds") or 0.0) for row in rows]
    viewers = {str(row.get("viewer") or "") for row in rows if row.get("viewer")}
    if not seconds:
        return {
            "watches": 0,
            "unique_viewers": 0,
            "total_seconds": 0.0,
            "average_seconds": None,
            "shortest_seconds": None,
            "longest_seconds": None,
        }
    return {
        "watches": len(seconds),
        "unique_viewers": len(viewers),
        "total_seconds": round(sum(seconds), 3),
        "average_seconds": round(sum(seconds) / len(seconds), 1),
        "shortest_seconds": round(min(seconds), 1),
        "longest_seconds": round(max(seconds), 1),
    }


# --------------------------------------------------------------------------- #
# Core Analytics
# --------------------------------------------------------------------------- #


#: Which Core Analytics counter each researched webhook event advances.
EVENT_TO_METRIC: dict[str, str] = {
    "asset.viewed": "views",
    "asset.downloaded": "downloads",
    "asset.shared": "shares",
}


def _counts_for(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count the three researched metrics over ``events``.

    The external-only rule lives in one expression, and
    :data:`~.vocabulary.INTERNAL_ONLY_METRICS` is what makes it correct: an
    internal event advances a counter only when that counter is the internal
    one. Sourced - "Dock's analytics only show engagement from external users
    (i.e. buyers and customers). The one exception is 'Shares'".
    """
    counts = {metric: 0 for metric in EVENT_TO_METRIC.values()}
    for event in events:
        metric = EVENT_TO_METRIC.get(str(event.get("event") or ""))
        if metric is None:
            continue
        if event.get("audience") == "internal" and metric not in INTERNAL_ONLY_METRICS:
            continue
        counts[metric] += 1
    return counts


def core_counts(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The flat counts behind the Core Analytics bar charts.

    ``shares`` counts internal engagement and the other two do not, which is
    the one exception the research states: "The one exception is 'Shares'
    which is an internal metric showing how often the internal team shares a
    specific asset."

    ``unique_viewers`` is over the same filtered view events, so it is a count
    of buyers rather than of the team's own previews.
    """
    counts = _counts_for(events)
    viewers = {
        str(event.get("viewer") or "")
        for event in events
        if EVENT_TO_METRIC.get(str(event.get("event") or "")) == "views"
        and event.get("audience") != "internal"
        and event.get("viewer")
    }
    counts["unique_viewers"] = len(viewers)
    return counts


def _advance(bucket: str, grain: str) -> str:
    """The bucket after ``bucket``. Calendar-correct, so months are 28-31 days.

    A fixed 28-day step would put a "monthly" chart's second bar on the 29th and
    silently merge two months into one bin, which is the sort of thing a bar
    chart should never do to a reader deciding which pages to cut.
    """
    start = date.fromisoformat(bucket)
    if grain == "month":
        return date(start.year + (start.month // 12), (start.month % 12) + 1, 1).isoformat()
    return (start + timedelta(days=7 if grain == "week" else 1)).isoformat()


def bucket_series(events: Sequence[Mapping[str, Any]], *, grain: str = "day") -> list[dict[str, Any]]:
    """The same three counts over time, for the bar chart's axis.

    The research names "Core Analytics bar charts" without saying what the
    horizontal axis is, so the grain vocabulary is an inference - see
    :mod:`.inferences`. Bins are emitted in ascending order, and an empty bin
    between the first and the last event is still emitted so the chart has no
    misleading gap in it.
    """
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {list(GRAINS)}, got {grain!r}")

    bins: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        moment = parse_ts(event.get("occurredAt"))
        if moment is not None:
            bins.setdefault(floor(moment, grain), []).append(event)
    if not bins:
        return []

    series: list[dict[str, Any]] = []
    cursor, last = min(bins), max(bins)
    while cursor <= last:
        series.append({"bucket": cursor, **_counts_for(bins.get(cursor, []))})
        cursor = _advance(cursor, grain)
    return series
