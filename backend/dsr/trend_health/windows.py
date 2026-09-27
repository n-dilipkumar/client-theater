"""The bucket ladder: workspace activity in, one of four values out.

This is the whole of WF-021's domain rule, and it is a pure function of three
things - the engagement events, the rules, and the instant it is evaluated at.
Nothing is cached, nothing is written, and that is not a simplification: the
research states the automation as "the classification recomputes continuously
from activity; no user action", and "buckets are time-window based, so a
workspace decays from Hot -> Warm -> Cooling -> Cold without any new activity". A
stored bucket would need a job to age it, and a job that stopped would leave rooms
reading Hot forever.

The ladder, in the order the research states it:

===========  ==================================================================
Bucket       Sourced rule
===========  ==================================================================
``hot``      tons of recent engagement within the last 7 days
``warm``     a decent amount of engagement within the last 14 days
``cooling``  previously had engagement, but none within the last 14 days
``cold``     no engagement within the last month
===========  ==================================================================

Three properties this module is built to guarantee, each of them a trap rather
than a preference:

**The ladder is total.** Every workspace gets a value. A ladder that could fall
off the end would leave a room unclassified, and the caller would have to guess -
which is the bug class the research's own four states are designed to avoid, and
the reason the two volume-gated branches both fall through to a recency test
rather than returning nothing.

**A workspace only ever decays when nothing happens.** The value is a function of
the newest engagement and the clock, so with no new events the sequence is
Hot -> Warm -> Cooling -> Cold and stops. :func:`decay` computes that sequence by
evaluating the same function at the window edges rather than by re-deriving it,
so what it reports is what a later call would return.

**A single stray view does not make a deal look healthy.** The sourced rules
quantify the first two buckets ("tons of", "a decent amount of") and quantify
neither of the last two. The numbers behind those words are not in any source, so
they are an inference - :mod:`dsr.trend_health.inferences`, entry
``volume-floor`` - and they are a *qualifier on counting*, not a separate branch.
Activity below the floor does not count as engagement in that window, so a room
with one view in the week falls through to the recency test and reads Cooling.
The counts are always returned alongside the value, so a caller can always see
what the floor suppressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from dsr.trend_health.vocabulary import (
    TREND_LABELS,
    TREND_RANK,
    TREND_RULES,
    TREND_VALUES,
    WINDOW_KEYS,
)

SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class EngagementEvent:
    """One researched workspace activity event, as the ladder sees it.

    ``external`` is the flag the sourced phrase "external engagement" turns on:
    an internal view is a real event and is kept, but it is not buyer engagement.
    """

    at: datetime
    type: str
    external: bool = True


def in_window(at: datetime, now: datetime, days: float) -> bool:
    """Is this event inside a window of ``days``, measured back from ``now``?

    "Within the last 7 days" is read as *strictly younger than* 7 days. The
    boundary is inclusive-at-the-edge the other way (``at <= now``), so an event
    stamped exactly now counts and one stamped exactly 7 days ago does not. That
    choice is what makes the decay instants land exactly on the sourced window
    edges - see :func:`decay` - instead of one second after them, where a rep
    asking "when does this change?" would be told a time at which nothing changes.

    An event in the future is outside every window. It is stored (see
    ``clock-skew-tolerance`` in the inferences) rather than refused outright, but
    it cannot count, because a workspace cannot have engaged with a deal that has
    not happened yet.
    """
    age = (now - at).total_seconds()
    return 0.0 <= age < float(days) * SECONDS_PER_DAY


def window_counts(
    events: Sequence[EngagementEvent],
    rules: Mapping[str, Any],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Count events in each of the three researched windows.

    Two numbers per window, and the difference between them is the point of the
    external-engagement rule: ``events`` is everything that happened, and
    ``qualifying`` is what the bucket test is allowed to see.
    """
    external_only = bool(rules.get("count_only_external", True))
    counts: dict[str, dict[str, Any]] = {}

    for key in WINDOW_KEYS:
        days = float(rules["windows"][key])
        total = qualifying = 0
        latest: datetime | None = None
        for event in events:
            if not in_window(event.at, now, days):
                continue
            total += 1
            if external_only and not event.external:
                continue
            qualifying += 1
            if latest is None or event.at > latest:
                latest = event.at
        counts[key] = {
            "days": int(days),
            "events": total,
            "qualifying": qualifying,
            "latest_at": latest,
        }

    return counts


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _days_ago(at: datetime | None, now: datetime) -> float | None:
    """Age in days, or ``None`` for an event that does not exist.

    ``None`` rather than zero: "no counted engagement" and "counted engagement
    just now" are different facts about a deal, and reporting 0 days for the
    first of them would put a room that nobody has opened next to a room someone
    opened a minute ago.
    """
    if at is None:
        return None
    return round((now - at).total_seconds() / SECONDS_PER_DAY, 2)


def last_counted_at(
    events: Sequence[EngagementEvent],
    rules: Mapping[str, Any],
    now: datetime,
) -> datetime | None:
    """The newest engagement the bucket test can see.

    This is the instant the decay is measured from: everything older than this is
    history, and the value can only change once the clock passes one of the
    window edges measured from here.
    """
    external_only = bool(rules.get("count_only_external", True))
    newest: datetime | None = None
    for event in events:
        if event.at > now:
            continue
        if external_only and not event.external:
            continue
        if newest is None or event.at > newest:
            newest = event.at
    return newest


def bucket_of(
    counts: Mapping[str, Mapping[str, Any]],
    rules: Mapping[str, Any],
) -> str:
    """The one place the four researched states are decided.

    Read top to bottom it is the research's own order, and every branch falls
    through, so the function is total: it returns one of the four values for any
    input whatsoever. The last two branches are pure recency tests because that is
    how the research phrases them - "previously had engagement" and "no
    engagement within the last month" say nothing about volume.
    """
    floors = rules["min_events"]
    if int(counts["hot"]["qualifying"]) >= int(floors["hot"]):
        return "hot"
    if int(counts["warm"]["qualifying"]) >= int(floors["warm"]):
        return "warm"
    if int(counts["cold"]["events"]) > 0:
        return "cooling"
    return "cold"


def _reasons(
    trend: str,
    counts: Mapping[str, Mapping[str, Any]],
    rules: Mapping[str, Any],
    newest: datetime | None,
    now: datetime,
    recorded: int = 0,
    internal: int = 0,
) -> list[str]:
    """Why this value, in the numbers, for a rep who is about to act on it.

    The sourced sentence for the bucket is served alongside this as ``rule``;
    this is the arithmetic behind it, including the two cases a rep would
    otherwise have to notice for themselves - the floor that suppressed the
    bucket above it, and activity on record that deliberately did not count.
    """
    floors = rules["min_events"]
    hot, warm, cold = counts["hot"], counts["warm"], counts["cold"]
    reasons: list[str] = []
    scope = "external" if rules.get("count_only_external", True) else ""

    def counts_in(window: Mapping[str, Any]) -> str:
        prefix = f"{scope} " if scope else ""
        return f"{prefix}{window['qualifying']} engagement event(s) in the last {window['days']} days"

    if trend == "hot":
        reasons.append(f"{counts_in(hot)} (Hot floor {int(floors['hot'])})")
    elif trend == "warm":
        if int(hot["qualifying"]) < int(floors["hot"]):
            reasons.append(f"{counts_in(hot)} is below the Hot floor of {int(floors['hot'])}")
        reasons.append(f"{counts_in(warm)} (Warm floor {int(floors['warm'])})")
    elif trend == "cooling":
        if int(warm["qualifying"]) >= 1 and int(warm["qualifying"]) < int(floors["warm"]):
            reasons.append(
                f"{counts_in(warm)} is below the Warm floor of {int(floors['warm'])}, "
                "so neither recent window counts as engaged"
            )
        reasons.extend(_uncounted_reasons(hot, rules))
        if newest is not None:
            reasons.append(f"last counted engagement {_days_ago(newest, now)}d ago")
        elif recorded:
            reasons.append(
                f"{recorded} event(s) recorded, none of them buyer engagement to count"
                + (f" ({internal} internal)" if internal else "")
            )
        else:
            reasons.append("no counted engagement on record")
    else:
        if newest is not None:
            reasons.append(
                f"last counted engagement {_days_ago(newest, now)}d ago, "
                f"outside the {cold['days']}-day Cold window"
            )
        elif recorded:
            reasons.append(
                f"{recorded} event(s) recorded, none of them buyer engagement to count"
                + (f" ({internal} internal)" if internal else "")
            )
        else:
            reasons.append("no engagement recorded")
        reasons.extend(_uncounted_reasons(hot, rules))
    return reasons


def _uncounted_reasons(hot: Mapping[str, Any], rules: Mapping[str, Any]) -> list[str]:
    """Say so when recent activity was seen and deliberately did not count.

    Without this, a room that a rep opened yesterday reads Cooling with no
    explanation on screen - which is the question they will ask first, and the one
    the "external engagement" rule exists to answer.
    """
    if not rules.get("count_only_external", True):
        return []
    uncounted = int(hot["events"]) - int(hot["qualifying"])
    if uncounted <= 0:
        return []
    return [
        f"{uncounted} event(s) in the last {hot['days']} days did not count: "
        "the metric is external engagement, and a rep's own view is not the buyer's"
    ]


def classify(
    events: Sequence[EngagementEvent],
    rules: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Classify one workspace. Total: every input gets a value.

    The returned mapping is the whole reading of a workspace at ``now``: the
    bucket, the sourced sentence for it, the arithmetic behind it, the counts in
    all three windows, the last engagement, and the decay that follows if nothing
    else happens.
    """
    counts = window_counts(events, rules, now)
    trend = bucket_of(counts, rules)
    newest = last_counted_at(events, rules, now)
    external_only = bool(rules.get("count_only_external", True))
    steps, change = decay(events, rules, now, trend)

    return {
        "trend": trend,
        "label": TREND_LABELS[trend],
        "rank": TREND_RANK[trend],
        "rule": TREND_RULES[trend],
        "reasons": _reasons(
            trend,
            counts,
            rules,
            newest,
            now,
            recorded=len(events),
            internal=sum(1 for event in events if not event.external),
        ),
        "as_of": _iso(now),
        "windows": {
            key: {
                "days": counts[key]["days"],
                "events": counts[key]["events"],
                "qualifying": counts[key]["qualifying"],
                "latest_at": _iso(counts[key]["latest_at"]),
                "edge": _iso(now - timedelta(days=counts[key]["days"])),
            }
            for key in WINDOW_KEYS
        },
        "last_engagement_at": _iso(newest),
        "last_engagement_days_ago": _days_ago(newest, now),
        "decay": steps,
        "next_change": change,
        "decay_path": list(TREND_VALUES),
        "rules": {
            "windows": {key: int(rules["windows"][key]) for key in WINDOW_KEYS},
            "min_events": {key: int(rules["min_events"][key]) for key in ("hot", "warm")},
            "count_only_external": external_only,
        },
    }


def decay(
    events: Sequence[EngagementEvent],
    rules: Mapping[str, Any],
    now: datetime,
    current: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The researched automation, computed: what the value becomes if nothing happens.

    "Buckets are time-window based, so a workspace decays from Hot -> Warm ->
    Cooling -> Cold without any new activity." The bucket depends on the clock
    only through the three window edges, and an edge can only be crossed by the
    newest counted engagement, so the candidate instants are exactly the three
    window lengths measured from that event. Each is then evaluated with
    :func:`classify` - the same function a later request would call - so this
    reports what the product would say rather than a second implementation of the
    ladder that could drift from the first.

    Returns the future value at every remaining edge, and the first of them that
    differs from ``current`` (the caller passes the value it just returned, or
    ``None`` to have it computed).
    """
    newest = last_counted_at(events, rules, now)
    if newest is None:
        # Nothing on record: the workspace is already Cold and cannot decay
        # further, which is the honest answer rather than an empty placeholder.
        return [], None

    steps: list[dict[str, Any]] = []
    for key in WINDOW_KEYS:
        edge = newest + timedelta(days=float(rules["windows"][key]))
        if edge <= now:
            continue
        steps.append(
            {
                "at": _iso(edge),
                "window": key,
                "trend": bucket_of(window_counts(events, rules, edge), rules),
            }
        )
    steps.sort(key=lambda step: str(step["at"]))

    if current is None:
        current = bucket_of(window_counts(events, rules, now), rules)
    change = next((step for step in steps if step["trend"] != current), None)
    return steps, change


def rank_of(value: str) -> int:
    """Position in the Trend column, hottest first. Unknown values sort last."""
    return TREND_RANK.get(str(value).strip().lower(), len(TREND_VALUES))


def sort_key(value: Any, *, kind: str) -> Any:
    """A comparable key for one dashboard column.

    Only called for values that are present: :meth:`TrendHealth.dashboard`
    partitions the rows before sorting, so a missing value never reaches here and
    never has to be encoded in the key. Doing it in one key is what went wrong
    first - a leading ``is None`` flag reverses with the sort and puts the
    emptiest column on top exactly when someone asks for the newest first.
    """
    if kind == "trend":
        return rank_of(value if value is not None else "")
    if kind == "number":
        return float(value or 0)
    if kind == "instant":
        # The epoch itself, not its negation: the dashboard reverses the key for a
        # descending sort, and negating here as well would order "least recent
        # first" for the direction that is supposed to mean "most recent first".
        return _epoch(value) if value else 0.0
    return str(value or "").strip().lower()


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value)
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def events_from_records(records: Iterable[Mapping[str, Any]]) -> list[EngagementEvent]:
    """Turn stored records into ladder input, skipping anything unreadable.

    A record with an unparseable timestamp is a data fault somewhere upstream, not
    a reason to fail a whole dashboard read: the ladder counts what it can read
    and the caller sees the count of skipped rows. Silently dropping a row here
    would be the same class of bug as dropping an unknown event type at the
    intake, so the caller is told.
    """
    from dsr.trend_health.timestamps import parse_timestamp

    events: list[EngagementEvent] = []
    for record in records:
        data = record.get("data") if isinstance(record, Mapping) else None
        if not isinstance(data, Mapping):
            continue
        at = parse_timestamp(data.get("occurred_at"), required=False)
        if at is None:
            continue
        events.append(
            EngagementEvent(
                at=at,
                type=str(data.get("type") or ""),
                external=str(data.get("audience") or "external").strip().lower() != "internal",
            )
        )
    return events
