"""The report's filters: what a caller may ask for, and what it means.

Sourced: the research's user flow ends "Filter by date range, CRM stage, owners, teams",
so those four - plus ``from``/``to`` as a pair - are the whole vocabulary. Anything else
is ignored rather than refused, so a client written against a newer vocabulary degrades
instead of breaking.

The one thing worth being careful about is what a date range *ranges over*. A single
range has to mean two different things in one report, because the two halves are
populated by different things: a deal has a date it was created, and an engagement event
has a date it happened. :meth:`ReportFilter.in_deal_range` and
:meth:`ReportFilter.in_event_range` are therefore separate predicates rather than one,
and the research says only "filter by date range" without saying over what.

A deal whose created date is absent is **never** excluded by the range. Dropping it
would quietly shrink a total, and a total that shrinks for a reason nobody can see is
the failure mode the report exists to avoid. It is kept and reported in
``data_warnings`` as ``unranged_deal`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from dsr.salesimpact.errors import InvalidFilter
from dsr.salesimpact.vocabulary import STAGE_CLASSES, normalise

#: A list filter is "unset" rather than "matches nothing", so a request with no filter
#: and a request filtering on a value that happens to match nothing behave differently
#: in the echo and stay distinguishable in a screenshot.
_UNSET: tuple[str, ...] = ()


def _split(value: Any) -> tuple[str, ...]:
    """A comma-separated query value, or a list, as a tuple of trimmed strings."""
    if value is None:
        return _UNSET
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        parts: list[str] = []
        for item in value:
            parts.extend(_split(item))
        return tuple(parts)
    text = str(value).strip()
    return (text,) if text else _UNSET


def parse_date(value: Any, *, field_name: str) -> date | None:
    """Parse an ISO-8601 date or datetime into a UTC date, or ``None`` if absent.

    A bare date is read as UTC midnight, which is the reading that makes an inclusive
    ``to`` bound include everything that happened on that day. A datetime is read as the
    UTC date of its own calendar day rather than shifted, so a filter typed in a local
    time does not move by a day depending on where the caller is.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalised).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise InvalidFilter(f"{field_name} is not an ISO-8601 date: {value!r}") from exc


@dataclass(frozen=True)
class ReportFilter:
    """One applied set of report filters.

    Immutable, so a filter cannot be mutated by a caller halfway through a rollup and
    leave two tiles computed over different populations.
    """

    date_from: date | None = None
    date_to: date | None = None
    #: Raw stage strings the caller named, normalised.
    stages: tuple[str, ...] = _UNSET
    #: Stage classes the caller named, from :data:`STAGE_CLASSES`.
    stage_classes: tuple[str, ...] = _UNSET
    owners: tuple[str, ...] = _UNSET
    teams: tuple[str, ...] = _UNSET
    bucket: str = "day"
    limit: int = 100
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def any_stage(self) -> bool:
        return bool(self.stages or self.stage_classes)

    def echo(self) -> dict[str, Any]:
        """What the applied filter was, for every aggregating response to return.

        A tile above a filtered list that cannot be read against the filter is how a
        reader ends up quoting "3 deals" for a report that is showing one.
        """
        return {
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "stage": list(self.stages),
            "stage_class": list(self.stage_classes),
            "owner": list(self.owners),
            "team": list(self.teams),
            "bucket": self.bucket,
        }

    # -- date predicates ---------------------------------------------------- #

    def in_date_range(self, moment: date | None) -> bool:
        """Whether a date falls inside the range. A missing date is always inside.

        See the module docstring: dropping a row whose date nobody recorded would
        quietly shrink a total, so an undated row is kept and reported instead.
        """
        if moment is None:
            return True
        if self.date_from and moment < self.date_from:
            return False
        if self.date_to and moment > self.date_to:
            return False
        return True

    in_deal_range = in_date_range
    in_event_range = in_date_range

    # -- value predicates --------------------------------------------------- #

    def matches_stage(self, stage_class: str, stage_text: str) -> bool:
        """Whether a deal's stage survives the filter.

        A caller may name a raw stage string (``"Closed Won"``) or a class (``"won"``),
        and may mix the two in one list. A raw string is compared against the deal's own
        stage text; a class against the deal's classification.
        """
        if not self.any_stage:
            return True
        if self.stage_classes and stage_class in self.stage_classes:
            return True
        if self.stages:
            wanted = normalise(stage_text)
            for candidate in self.stages:
                if wanted == candidate:
                    return True
                if wanted.startswith(candidate) or candidate in wanted:
                    return True
        return False

    def matches_owner(self, owner: str) -> bool:
        if not self.owners:
            return True
        return normalise(owner) in {normalise(value) for value in self.owners}

    def matches_team(self, team: str) -> bool:
        if not self.teams:
            return True
        return normalise(team) in {normalise(value) for value in self.teams}

    def matches_deal(
        self, *, created: date | None, stage_class: str, stage_text: str, owner: str, team: str
    ) -> bool:
        """The whole filter, as one predicate, for a deal."""
        return (
            self.in_deal_range(created)
            and self.matches_stage(stage_class, stage_text)
            and self.matches_owner(owner)
            and self.matches_team(team)
        )


def parse_filters(
    params: Mapping[str, Any] | None,
    *,
    limit: int | None = None,
    default_limit: int = 100,
) -> ReportFilter:
    """Build a :class:`ReportFilter` from query parameters.

    Raises :class:`~dsr.salesimpact.errors.InvalidFilter` for a range that runs
    backwards or a date that is not a date. Everything else is either applied or
    ignored; a parameter this version does not know about is not an error, because a
    client sending one is newer than this server and should still get a report.
    """
    params = params or {}
    date_from = parse_date(params.get("from"), field_name="from")
    date_to = parse_date(params.get("to"), field_name="to")
    if date_from and date_to and date_from > date_to:
        raise InvalidFilter(f"from {date_from.isoformat()} is after to {date_to.isoformat()}")

    raw_stages = _split(params.get("stage"))
    stages: list[str] = []
    classes: list[str] = []
    for value in raw_stages:
        folded = normalise(value)
        if folded in STAGE_CLASSES:
            classes.append(folded)
        elif folded:
            stages.append(folded)

    owners = tuple(normalise(value) for value in _split(params.get("owner")))
    teams = tuple(normalise(value) for value in _split(params.get("team")))

    bucket = normalise(params.get("bucket")) or "day"
    if bucket not in BUCKETS:
        raise InvalidFilter(f"bucket must be one of {list(BUCKETS)}, got {params.get('bucket')!r}")

    requested_limit = limit
    if requested_limit is None and params.get("limit") not in (None, ""):
        try:
            requested_limit = int(str(params["limit"]).strip())
        except (TypeError, ValueError) as exc:
            raise InvalidFilter(f"limit is not a whole number: {params['limit']!r}") from exc
    resolved_limit = max(1, min(int(requested_limit or default_limit), 1000))

    known = {"from", "to", "stage", "owner", "team", "limit", "bucket", "room_id", "actor"}
    extras = {
        key: value for key, value in params.items() if key not in known and value not in (None, "")
    }

    return ReportFilter(
        date_from=date_from,
        date_to=date_to,
        stages=tuple(stages),
        stage_classes=tuple(classes),
        owners=owners,
        teams=teams,
        bucket=bucket,
        limit=resolved_limit,
        extras=extras,
    )


#: How a time series is grouped into buckets. ``day`` is the default because it is the
#: finest reading of "over time" and therefore the one that loses nothing; a report over a
#: six-month range is 180 daily points, so a caller renders ``week`` or ``month``. Recorded
#: as inference ``series-bucketing`` in :mod:`dsr.salesimpact.inferences`: the research
#: names "Deals Created Over Time" and "Buyer Views Over Time" and says nothing about the
#: granularity, and a chart nobody can read is not a report.
BUCKETS = ("day", "week", "month")


def bucket_start(moment: date, bucket: str = "day") -> date:
    """The first date of ``moment``'s bucket.

    Weeks start on Monday, matching ISO-8601, so a bucket label is unambiguous rather
    than dependent on the locale of whoever rendered it.
    """
    if bucket == "month":
        return moment.replace(day=1)
    if bucket == "week":
        return moment - timedelta(days=moment.weekday())
    return moment


def bucket_key(moment: date, bucket: str = "day") -> str:
    """The ISO label for ``moment``'s bucket.

    A week and a month are labelled by their first day rather than inventing a
    ``2026-W07`` notation, so the labels sort as plain strings and read as the dates a
    reader can check against a calendar.
    """
    return bucket_start(moment, bucket).isoformat()


def date_range(
    date_from: date | None,
    date_to: date | None,
    moments: Iterable[date | None],
    *,
    bucket: str = "day",
) -> list[dict[str, Any]]:
    """Buckets for a time series, ascending, with the gaps inside the range filled.

    A bar chart that omits a quiet day draws the quiet day as busy, and a series that
    omits a day with no activity at all is worse. So the buckets run from the earlier of
    the two bounds to the later, inclusive, and every bucket in between is present with a
    zero where nothing happened.

    With no bounds given, the series covers exactly the buckets that carry data, so an
    unbounded report is not a report of four thousand bars. A span past ten years is not
    bucketed at all: at a daily bucket that is 3653 rows of JSON for one panel, and a
    caller who needs that should ask for weeks or months instead of receiving it.
    """
    present = sorted({moment for moment in moments if moment is not None})
    start = (
        bucket_start(date_from, bucket)
        if date_from
        else (bucket_start(present[0], bucket) if present else None)
    )
    end = date_to or (present[-1] if present else None)
    if start is None or end is None or end < (date_from or start):
        return []
    # Both ends are rounded down to their bucket, not just the first. A range of
    # 2026-02-01 (a Sunday) to 2026-02-04 in weeks is really 2026-01-26 to 2026-02-02,
    # and stepping from the first to the unrounded end stops one bucket early - so the
    # events in the final week were counted nowhere and the series came back short.
    end = bucket_start(end, bucket)
    if (end - start).days > _MAX_SPAN_DAYS:
        return []

    step = {"day": 1, "week": 7}.get(bucket, 1)
    buckets: list[dict[str, Any]] = []
    if bucket == "month":
        cursor = start
        while cursor <= end:
            buckets.append({"date": cursor.isoformat(), "deals": 0, "amount": 0.0})
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        return buckets
    while start <= end:
        buckets.append({"date": start.isoformat(), "deals": 0, "amount": 0.0})
        start += timedelta(days=step)
    return buckets


#: The widest span a series will enumerate at any bucket. Past this the caller gets an
#: empty list and the named warning rather than thousands of rows.
_MAX_SPAN_DAYS = 3650


def unique_preserving_order(values: Sequence[str]) -> list[str]:
    """Deduplicate without reordering, for a deterministic label list."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
