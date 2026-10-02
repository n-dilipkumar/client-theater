"""Delivery usage, as ``PlatformEventUsageMetric``.

The research names the source once: "``PlatformEventUsageMetric`` for delivery
usage". It publishes no schema, so the shape below is this build's and is served
at ``GET /api/wf-043/usage`` alongside a statement of that fact - a reviewer can
disagree with a named counter instead of having to find it.

Four of the counters are the research's own vocabulary read off its sentences:

``events_requested``     "The client can control the flow of events received by
                        setting the number of requested events in the FetchRequest
                        parameter." Requested, not received, so the two are
                        counted apart: a subscriber that asks for 100 and the
                        stream delivers 12 has outstanding budget, and a usage
                        report that conflated them would hide that.
``events_delivered``    What the room actually buffered.
``buffer_bytes``        "We recommend you set the buffer size to 3 MB." Compared
                        against that, so a channel that sized itself differently
                        is visible rather than merely permitted.
``transactions_committed``  The commit rule, counted: how many parked
                        transactions reached the replica.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.change_stream import vocabulary

#: A usage row starts empty rather than absent, so the report is a total across
#: every subscription rather than only the ones that have been touched.
EMPTY_USAGE: dict[str, Any] = {
    "events_requested": 0,
    "events_delivered": 0,
    "fetch_requests": 0,
    "fetch_outstanding": 0,
    "transactions_buffered": 0,
    "transactions_committed": 0,
    "replica_writes": 0,
    "buffer_bytes": 0,
    "buffer_high_water_bytes": 0,
    "duplicates_refused": 0,
    "events_committed": 0,
}


def blank() -> dict[str, Any]:
    """A fresh usage row."""
    return dict(EMPTY_USAGE)


def merge(base: Mapping[str, Any] | None, **changes: Any) -> dict[str, Any]:
    """A usage row with ``changes`` applied, ignoring unknown counters.

    Ignoring rather than raising is deliberate: a counter added to
    :data:`EMPTY_USAGE` by a later release must not stop an older stored row from
    being read, which is the same "a team adding a field must not need
    coordination" rule the rest of this product follows.
    """
    merged = blank()
    merged.update({k: v for k, v in (base or {}).items() if k in EMPTY_USAGE})
    for key, value in changes.items():
        if key in merged:
            merged[key] = value
    return merged


def request_fetch(usage: Mapping[str, Any], num_requested: int) -> dict[str, Any]:
    """Record one FetchRequest and what it leaves outstanding.

    The outstanding budget is ``requested - delivered`` and is never allowed to
    go negative. A subscriber that delivers more than it asked for is a defect in
    the transport rather than something the room should model by going negative,
    so the excess is refused at the door by
    :class:`~dsr.change_stream.errors.NoOutstandingFetchRequest` and the counter
    here stays true.
    """
    current = merge(usage)
    requested = int(num_requested)
    if requested <= 0:
        return current
    return merge(
        current,
        events_requested=current["events_requested"] + requested,
        fetch_requests=current["fetch_requests"] + 1,
        fetch_outstanding=current["fetch_outstanding"] + requested,
    )


def record_delivery(usage: Mapping[str, Any], *, buffer_bytes: int) -> dict[str, Any]:
    """Record one event arriving against the outstanding request."""
    current = merge(usage)
    return merge(
        current,
        events_delivered=current["events_delivered"] + 1,
        fetch_outstanding=max(0, current["fetch_outstanding"] - 1),
        buffer_bytes=max(0, int(buffer_bytes)),
        buffer_high_water_bytes=max(current["buffer_high_water_bytes"], int(buffer_bytes)),
    )


def record_duplicate(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Record a sequence number that arrived twice inside one transaction."""
    current = merge(usage)
    return merge(current, duplicates_refused=current["duplicates_refused"] + 1)


def record_buffered(usage: Mapping[str, Any]) -> dict[str, Any]:
    current = merge(usage)
    return merge(current, transactions_buffered=current["transactions_buffered"] + 1)


def record_commit(usage: Mapping[str, Any], *, events: int, writes: int) -> dict[str, Any]:
    """Record one parked transaction reaching the room's replica."""
    current = merge(usage)
    return merge(
        current,
        transactions_committed=current["transactions_committed"] + 1,
        events_committed=current["events_committed"] + max(0, int(events)),
        replica_writes=current["replica_writes"] + max(0, int(writes)),
    )


def describe(subscription: Mapping[str, Any]) -> dict[str, Any]:
    """One subscription's usage, with the researched recommendations beside it.

    ``within_recommendation`` is a note, not a gate: the research says "We
    recommend you set the buffer size to 3 MB" and calls the sizing tunable, so a
    channel that chose differently is permitted and reported.
    """
    usage = merge(subscription.get("usage"))
    limit = int(subscription.get("buffer_limit_bytes") or vocabulary.RECOMMENDED_BUFFER_BYTES)
    current = int(usage["buffer_bytes"])
    return {
        "subscription_id": str(subscription.get("id") or ""),
        "channel": str(subscription.get("channel_name") or ""),
        "transport": str(subscription.get("transport") or ""),
        "state": str(subscription.get("state") or ""),
        **usage,
        "buffer_limit_bytes": limit,
        "recommended_buffer_bytes": vocabulary.RECOMMENDED_BUFFER_BYTES,
        "buffer_within_recommendation": current <= limit,
        "buffer_exceeds_recommendation": current > vocabulary.RECOMMENDED_BUFFER_BYTES,
        "buffer_usage_percent": (round(100.0 * current / limit, 1) if limit else 0.0),
    }


def describe_metric_source() -> dict[str, Any]:
    """The one sentence that justifies this file existing, served as data."""
    return {
        "source": "PlatformEventUsageMetric",
        "researched_for": "delivery usage",
        "schema_published_by_vendor": False,
        "note": (
            "The research names the metric and its purpose and no fields, so the counters "
            "below are this build's. events_requested / events_delivered come from the "
            "FetchRequest sentence, buffer_bytes from the 3 MB recommendation, and "
            "transactions_committed from the commit rule."
        ),
        "counters": sorted(EMPTY_USAGE),
    }
