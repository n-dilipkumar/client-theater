"""Turning what an admin picked into something a vendor can be asked for.

The researched flow's first two steps are both here: "Admin opens **Sync ->
Backfill** and picks a date range / full-history option", and "Room asks the CRM
for an asynchronous extract job (large volumes) or a delta/paged read (moderate
volumes)".

The second step is the only real decision in the workflow, and the research
hands the number for it:

    "Any data operation that includes more than 2,000 records is a good candidate
    for Bulk API 2.0 ... Jobs with fewer than 2,000 records should involve
    'bulkified' synchronous calls in REST."

So :func:`choose_strategy` is a threshold, not a preference. Everything else in
this module is validation: a range that runs backwards, a field map with no key,
a page size above what the vendor will page with.

Nothing here touches the store. A plan is a value, and a plan that cannot be
built is a :class:`~dsr.crm_backfill.errors.PlanError` naming the field to fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.crm_backfill import cursors
from dsr.crm_backfill.errors import InvalidRange, PlanError, UnsupportedStrategy
from dsr.crm_backfill.vocabulary import (
    DIRECTIONS,
    NUMBERS,
    SCOPES,
    STRATEGIES,
    STRATEGY_MEANING,
)

#: A full-history backfill reaches back this far when the admin does not say.
#:
#: Not a researched number, and not a limit: it is the floor for "full history"
#: in the absence of a stated start, so a plan always has a lower bound and the
#: call estimate in :func:`estimate_calls` is never computed against an infinite
#: range. A caller that wants genuinely unbounded history says so with
#: ``from: null`` and accepts an estimate of ``None``.
FULL_HISTORY_FLOOR_DAYS = 3650

#: How the strategies are ranked when a vendor implements more than one and the
#: volume does not decide it.
#:
#: ``async_job`` first because the research's first branch is the async job, and
#: ``delta_read`` before ``paged_read`` because a delta read is the only strategy
#: that can return "nothing changed" - so it is the cheaper of the two to leave
#: polling. A vendor that implements one strategy has no ranking to apply.
STRATEGY_PREFERENCE: tuple[str, ...] = ("async_job", "delta_read", "paged_read")


def bulk_threshold() -> int:
    """The researched volume at which a backfill becomes an async job."""
    return int(NUMBERS["bulk_threshold_records"]["value"])


def normalise_scope(raw: Any, now: datetime) -> dict[str, Any]:
    """Resolve the wizard's range / full-history choice into one comparable shape.

    Both the range and the full-history option come back as ``from``/``to``, so
    every later stage - the vendor, the call estimate, the log - reads one shape
    rather than branching on which option was picked. ``kind`` is carried
    alongside so the wizard can still say which one it was.

    ``from`` is inclusive and ``to`` exclusive. Two adjacent ranges therefore
    neither skip nor double-count the row on their boundary, which is what makes
    "no duplicates, no gaps" hold across a schedule of backfills rather than only
    within one.
    """
    if raw is None:
        raw = {"kind": "full_history"}
    if not isinstance(raw, Mapping):
        raise InvalidRange(f"scope must be an object with a kind of {' or '.join(SCOPES)}")

    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        # A bare from/to with no kind is a range. Accepting it means a client
        # written against the range picker works without a wrapper.
        kind = "range" if raw.get("from") else "full_history"
    if kind not in SCOPES:
        raise InvalidRange(f"scope kind {kind!r} is not understood; pick {' or '.join(SCOPES)}")

    if kind == "full_history":
        start = raw.get("from")
        if start is None:
            start = now - timedelta(days=FULL_HISTORY_FLOOR_DAYS)
            unbounded = True
        else:
            unbounded = False
    else:
        start = raw.get("from")
        unbounded = False
        if start is None:
            raise InvalidRange(
                "a range needs a from; pick the full_history option instead if you want everything"
            )

    start_at = _parse(start, "from") if start is not None else None
    end_raw = raw.get("to")
    end_at = _parse(end_raw, "to") if end_raw is not None else None
    if end_at is None:
        end_at = now

    if start_at is not None and start_at > now:
        # Checked before the ordering rule, because a `from` in the future is a
        # different mistake from a range that runs backwards and the operator
        # needs to be told which one they made.
        raise InvalidRange(
            f"from {start_at.isoformat()} is in the future; there is no history yet to read"
        )
    if start_at is not None and start_at >= end_at:
        raise InvalidRange(
            f"from {start_at.isoformat()} is not before to {end_at.isoformat()}; a range that runs "
            "backwards or spans nothing would backfill nothing and report 100% complete"
        )
    if end_at > now + timedelta(minutes=1):
        # A `to` a minute past now is a rounding artefact, not a mistake; a `to`
        # in the future is either one or a badly-clocked client, and reading
        # future history silently is the kind of thing nobody notices for weeks.
        raise InvalidRange(
            f"to {end_at.isoformat()} is in the future; a backfill reads history, not intent"
        )

    return {
        "kind": kind,
        "from": start_at.isoformat() if start_at is not None else None,
        "to": end_at.isoformat(),
        "unbounded_below": unbounded,
        "days": round((end_at - start_at).total_seconds() / 86400.0, 3) if start_at else None,
    }


def choose_strategy(
    adapter: Any,
    *,
    estimated_records: int | None,
    live_cursor: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Pick the strategy, and say why in words a reviewer can check.

    The volume decides whenever the caller knows it, because that is the rule the
    research states. When it is not known, the adapter's own default ordering
    decides and the run reconsiders as soon as a total arrives - so a run that
    guessed wrong is corrected by the vendor's own record count rather than by
    anybody guessing better.

    A live cursor short-circuits the choice, and the cursor's *kind* decides
    which strategy that is - a Dataverse ``data_token`` belongs to a delta read
    and a Bulk job id to an async job. Switching strategy mid-run would abandon
    the thing the run is resuming, which is the one thing the cursor is for.
    """
    if live_cursor and live_cursor.get("cursor"):
        wanted = cursors.strategy_for(str(live_cursor.get("kind") or ""))
        if wanted and adapter.supports(wanted):
            return wanted, {
                "rule": "live_cursor",
                "kind": live_cursor.get("kind"),
                "detail": (
                    f"a {live_cursor.get('kind')} is stored for this connection, so the run "
                    f"continues as a {wanted} rather than abandoning the handle it is resuming"
                ),
            }

    threshold = bulk_threshold()
    if estimated_records is not None:
        records = int(estimated_records)
        if records < 0:
            raise PlanError("estimated_records cannot be negative")
        wanted = "async_job" if records > threshold else "paged_read"
        if adapter.supports(wanted):
            return wanted, {
                "rule": "volume",
                "threshold": threshold,
                "estimated_records": records,
                "detail": (
                    f"{records} record(s) is {'more' if records > threshold else 'not more'} than "
                    f"{threshold}, which is the volume the research names as the boundary between an "
                    "asynchronous job and bulkified synchronous calls"
                ),
            }
        # The vendor does not implement the strategy the volume asks for, so fall
        # through to preference and say so: this is the one case where the volume
        # rule cannot be obeyed literally, and it must not pass unremarked.
        fallback = _by_preference(adapter)
        if fallback is None:
            raise UnsupportedStrategy(
                f"{adapter.vendor} implements none of {', '.join(STRATEGIES)}"
            )
        return fallback, {
            "rule": "volume_unavailable",
            "threshold": threshold,
            "estimated_records": records,
            "detail": (
                f"the volume asks for {wanted}, which {adapter.vendor} does not implement, so the "
                f"run uses {fallback}: {STRATEGY_MEANING[fallback]}"
            ),
        }

    fallback = _by_preference(adapter)
    if fallback is None:
        raise UnsupportedStrategy(f"{adapter.vendor} implements none of {', '.join(STRATEGIES)}")
    return fallback, {
        "rule": "undetermined_volume",
        "detail": (
            "no volume was supplied, so the run opens with "
            f"{adapter.vendor}'s preferred strategy and reconsiders against the vendor's own "
            "record count as soon as one arrives"
        ),
    }


def _by_preference(adapter: Any) -> str | None:
    for strategy in STRATEGY_PREFERENCE:
        if adapter.supports(strategy):
            return strategy
    for strategy in STRATEGIES:
        if adapter.supports(strategy):
            return strategy
    return None


def normalise_direction(raw: Any) -> str:
    """The direction, defaulting to the one the research narrates."""
    if raw is None or raw == "":
        return "pull"
    value = str(raw).strip().lower()
    if value not in DIRECTIONS:
        raise PlanError(f"direction {value!r} is not understood; pick {' or '.join(DIRECTIONS)}")
    return value


def normalise_field_map(raw: Any) -> dict[str, str]:
    """The transform the data flow names: "transform via field map".

    Accepts either a flat ``{crm_field: replica_field}`` or the researched
    ``{"map": {...}}`` envelope, and refuses anything that is not a flat
    string-to-string mapping - a field map whose values are not strings would
    write a key nobody can query through the dynamic index.
    """
    if raw is None or raw == {}:
        return {}
    if isinstance(raw, Mapping) and "map" in raw:
        raw = raw["map"]
    if not isinstance(raw, Mapping):
        raise PlanError("field_map must be an object mapping a CRM field to a replica field")
    mapping: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            raise PlanError(
                f"field_map[{key!r}] must be a non-empty field name; a map onto a nested path is "
                "written as the name you want, not as a JSON path"
            )
        if not str(key).strip():
            raise PlanError("field_map has an empty source field name")
        mapping[str(key).strip()] = value.strip()
    return dict(sorted(mapping.items()))


def normalise_page_size(raw: Any, ceiling: int) -> int:
    """The page size, bounded by what the vendor will page with.

    The researched default is Dataverse's ``PagingInfo`` ``Count`` of 5000. A
    caller may ask for fewer - a smaller page is the way to bound the blast
    radius of a crash - and may not ask for more, because the vendor would answer
    a different page size than the cursor was issued against and the room would
    be holding a resume point for a page boundary that does not exist.
    """
    default = int(NUMBERS["dataverse_page_size"]["value"])
    if raw is None or raw == "":
        return min(default, ceiling)
    try:
        size = int(raw)
    except (TypeError, ValueError):
        raise PlanError(f"page_size {raw!r} is not a whole number of records") from None
    if size < 1:
        raise PlanError("page_size must be at least 1; a page of nothing is not a page")
    if size > ceiling:
        raise PlanError(
            f"page_size {size} is above the {ceiling} this vendor pages with; a page boundary the "
            "vendor did not issue is a resume point that does not exist"
        )
    return size


def normalise_poll_interval(raw: Any) -> int:
    """How often the poller asks, in seconds.

    The research says only "the room runs a poller on a fixed interval". This is
    the floor at which a run is allowed to ask again, not a schedule the package
    installs: nothing here starts a thread, and a scheduler that calls the poll
    route more often than this is told the run was not due.
    """
    if raw is None or raw == "":
        return 300
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        raise PlanError(f"poll_interval_seconds {raw!r} is not a whole number of seconds") from None
    if seconds < 0:
        raise PlanError("poll_interval_seconds cannot be negative")
    return seconds


def estimate_calls(*, total: int | None, page_size: int, polls: int = 1) -> int | None:
    """How many vendor calls a run of this size is likely to take.

    One call to create the job, ``polls`` to find it ready, and one per page.
    ``None`` when the total is unknown, which is a real answer rather than a
    zero: HubSpot's quota is a *daily* call count, and a plan that cannot be
    sized is a plan that cannot be checked against it.
    """
    if total is None:
        return None
    pages = -(-int(total) // max(1, int(page_size)))  # ceiling division
    return 1 + max(0, int(polls)) + pages


def _parse(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    text = str(value).strip()
    if not text:
        raise InvalidRange(f"{field} is empty; give a timestamp or omit it")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            raise InvalidRange(
                f"{field} {value!r} is not a timestamp; use an ISO 8601 instant such as "
                "2026-01-31T00:00:00Z"
            ) from None
    if parsed.tzinfo is None:
        # A bare date or a naive local time is a real shape in a picker. Reading
        # it as UTC rather than as the server's zone keeps the result the same
        # whatever machine runs the seeder.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
