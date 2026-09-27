"""The arithmetic of the Sales Impact report.

Everything in this module is a pure function over already-loaded records, or over the
:class:`~dsr.salesimpact.deals.DealBook` that loads them. Two consequences worth stating
because they are the reason for the shape:

* **The report writes nothing.** Every figure is computed on read, and a stored metric
  row would be a cache that goes stale against a CRM sync - and the research says
  explicitly that "Deal stage/amount sync keeps the rollup fresh".
* **Nothing here opens a database connection.** The store handle arrives as an argument,
  so the whole of the researched arithmetic is testable with plain dicts.

The population
--------------
Sourced: "The Sales Impact report pulls in any workspace designated as a 'Sales' type
that has a CRM opportunity." That is a two-part conjunction, and :func:`population`
implements it as one. A room that fails either part is returned with the reason it
failed, because the research also makes the workspace type the user's *first step* and
warns that a Sales room with no deal attached makes the report "missing data" - a
number that is quietly too small cannot be acted on, and the rooms that caused it have
to be nameable.

The eight tiles
---------------
Sourced: the researched insights list names exactly eight. Each one's definition is in
``WF-023-design.md`` §4.2, and every one that the research does not define is listed as a
named inference in :mod:`dsr.salesimpact.inferences` and served at
``GET /api/wf-023/inferences``, so a reviewer disagrees with a name rather than with a
number they found in a diff.

Two conventions run through all of it. **Every list is ordered by an explicit key**, never
by dictionary or database iteration order, so two calls over the same data produce the
same bytes. And **a missing number is ``None``, not zero**: a close rate over a
population where nothing has closed is ``0/0``, and reporting ``0%`` would assert that
every deal was lost, which is a different and wrong claim.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.salesimpact.deals import view_deal
from dsr.salesimpact.filters import ReportFilter, bucket_key, date_range
from dsr.salesimpact.vocabulary import (
    FIELD_SYNONYMS,
    VIEW_ACTIONS,
    as_number,
    as_text,
    classify_stage,
    is_sales_type,
    normalise,
    pick,
)

#: The researched tile order. The page renders tiles in this order, and a reader who has
#: seen the researched report expects them here rather than alphabetically.
TILES = (
    "total_deals",
    "total_pipeline_touched",
    "active_deals",
    "active_pipeline",
    "closed_won_deals",
    "revenue",
    "close_rate",
    "days_to_close",
)


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #


def as_date(value: Any) -> date | None:
    """An ISO-8601 date or datetime as a date, tolerantly and without shifting it.

    Tolerant because a CRM sends ``2026-11-04``, ``2026-11-04T00:00:00Z`` and
    ``2026-11-04 09:14:00+05:30`` for the same fact. Not shifted, because a close date
    read back as the previous day would quietly put a deal's duration a day out, and a
    days-to-close average that is wrong by one day per deal is wrong.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = as_text(value)
    if not text:
        return None
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalised).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def days_between(start: date | None, end: date | None) -> float | None:
    """Whole days from ``start`` to ``end``, or ``None`` when either is missing or the
    interval runs backwards.

    ``None`` for a backwards interval rather than a negative number: a close date before
    the created date is a data error, and a negative day count in an average is not a
    finding, it is arithmetic that swallowed an error. The caller reports it instead.
    """
    if start is None or end is None:
        return None
    delta = (end - start).days
    if delta < 0:
        return None
    return float(delta)


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #


def project_deal(record: Mapping[str, Any], *, stage_sets: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
    """One deal as the rollup needs it: raw values, no derived cache.

    Accepts **either** shape - a stored record, which is enveloped and carries its payload
    under ``data``, or an already-projected row, which is flat. Taking both is what makes
    this safe to call twice, and the first implementation was called twice: the report
    re-projected its input, read ``record["data"]`` off a dict that had none, and every
    deal arrived at the tiles with no stage, no amount and no date. The report quietly
    returned three unknown-stage deals worth nothing, with no error anywhere. A projection
    that empties its input when handed its own output is a trap, so it no longer does.

    ``created_at`` is deliberately **not** in the creation-date synonym list. It is one
    of the store's reserved envelope keys, so ``AuditedDatabase.create`` strips it out of
    a payload before the row is written; a deal that sent ``created_at`` meaning "when the
    CRM created this opportunity" would have it silently discarded. The envelope's own
    ``created_at`` is used as the fallback and the row is marked ``created_source:
    "recorded"`` so the report can say the interval was measured from when the deal was
    first recorded here rather than from when the opportunity was created.

    No ``in_scope`` here. Scope is a property of the *workspace*, decided once by
    :func:`population`, and re-deciding it per deal is how a deal and its room end up
    disagreeing about whether they are in the report.
    """
    nested = record.get("data")
    data: Mapping[str, Any] = nested if isinstance(nested, Mapping) else record
    created = as_date(pick(data, (), concept="created_at"))
    created_source = "crm" if created is not None else "recorded"
    if created is None:
        created = as_date(record.get("created_at"))
    return {
        "id": str(record.get("id") or ""),
        "room_id": record.get("room_id"),
        "crm_deal_id": as_text(pick(data, (), concept="crm_deal_id")) or None,
        "name": as_text(pick(data, (), concept="name")),
        "account": as_text(pick(data, (), concept="account")),
        "stage": as_text(pick(data, (), concept="stage")),
        "amount": as_number(pick(data, (), concept="amount")),
        "currency": as_text(pick(data, (), concept="currency")) or None,
        "owner": as_text(pick(data, (), concept="owner")),
        "team": as_text(pick(data, (), concept="team")),
        "created": created,
        "created_source": created_source,
        "closed": as_date(pick(data, (), concept="closed_at")),
    }


def project_room(record: Mapping[str, Any]) -> dict[str, Any]:
    """One workspace record as the population test needs it."""
    data = record.get("data") or {}
    return {
        "id": str(record.get("id") or ""),
        "room_id": str(record.get("id") or ""),
        "name": as_text(pick(data, (), concept="room_name")),
        "account": as_text(pick(data, (), concept="room_account")),
        "type": as_text(pick(data, (), concept="room_type")),
        "owner": as_text(pick(data, (), concept="room_owner")),
    }


def project_event(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """One buyer engagement event, or ``None`` when it names no buyer.

    A buyer is identified by email, lowercased. That is the researched behaviour - an
    uninvited contact "shows up by their email" - and it is the only identifier the
    research gives, so an event with no resolvable person is skipped rather than filed
    under an empty key that would rank above every real buyer.
    """
    data = record.get("data") or {}
    person = as_text(pick(data, (), concept="person")).lower()
    if not person:
        return None
    action = normalise(pick(data, (), concept="action"))
    occurred = as_date(pick(data, (), concept="occurred_at"))
    return {
        "id": str(record.get("id") or ""),
        "room_id": record.get("room_id"),
        "buyer": person,
        "action": action,
        "is_view": action in VIEW_ACTIONS,
        "occurred": occurred,
    }


# --------------------------------------------------------------------------- #
# The population
# --------------------------------------------------------------------------- #


def population(
    rooms: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    *,
    stage_sets: Mapping[str, Sequence[str]] | None = None,
    sales_type: str | None = None,
) -> dict[str, Any]:
    """Which workspaces are in the report, and which are not, with the reason.

    The two admission tests are the researched ones and they are conjunctive: a room is
    in scope when its type is ``sales`` **and** at least one live deal is attached to it.
    A room failing either is returned with ``not_sales`` or ``no_deal``, which is what
    turns a report that is quietly too small into one a reader can act on.

    A deal attached to no workspace, or to an excluded one, is in neither list: it is in
    ``deals_not_attached`` and contributes to no figure at all.
    """
    deals_by_room: dict[str, list[Mapping[str, Any]]] = {}
    unattached: list[Mapping[str, Any]] = []
    for deal in deals:
        room_id = deal.get("room_id")
        if not room_id:
            unattached.append(deal)
        else:
            deals_by_room.setdefault(str(room_id), []).append(deal)

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for room in sorted(rooms, key=lambda entry: str(entry.get("id") or "")):
        room_id = str(room.get("id") or "")
        attached = sorted(deals_by_room.get(room_id, []), key=lambda entry: str(entry.get("id") or ""))
        is_sales = is_sales_type(room.get("type"), sales_type=sales_type)
        if not is_sales:
            excluded.append(
                {
                    "room_id": room_id,
                    "name": room.get("name", ""),
                    "reason": "not_sales",
                    "type": room.get("type", ""),
                    "deals": len(attached),
                }
            )
        elif not attached:
            excluded.append(
                {
                    "room_id": room_id,
                    "name": room.get("name", ""),
                    "reason": "no_deal",
                    "type": room.get("type", ""),
                    "deals": 0,
                }
            )
        else:
            included.append({**room, "deals": attached})

    return {
        "included": included,
        "excluded": sorted(excluded, key=lambda entry: (entry["reason"], entry["room_id"])),
        "deals_not_attached": sorted(
            unattached, key=lambda entry: str(entry.get("id") or "")
        ),
        "rooms_total": len(rooms),
        "sales_typed_rooms": sum(
            1 for room in rooms if is_sales_type(room.get("type"), sales_type=sales_type)
        ),
        "in_scope_rooms": len(included),
    }


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


def choose_currency(deals: Sequence[Mapping[str, Any]], *, default: str = "USD") -> str:
    """The one currency the report's money figures are in.

    Summing EUR into USD would produce a Revenue figure that is a fiction, so a single
    number is only ever returned alongside the currency it is in, and the full
    per-currency split is returned beside it. The currency carrying the most deals wins,
    because that is the one most of the figures are in; a tie is broken
    lexicographically so the choice is deterministic rather than dependent on which deal
    happened to be read first.

    The research says nothing about currency. This is a correctness guard on the
    researched ``Revenue`` tile, not a new requirement, and it is recorded as inference
    ``money-currency`` in :mod:`dsr.salesimpact.inferences`.
    """
    counts: dict[str, int] = {}
    for deal in deals:
        code = normalise(deal.get("currency")) or default.lower()
        counts[code] = counts.get(code, 0) + 1
    if not counts:
        return default.upper()
    best = max(counts.values())
    return sorted(code for code, count in counts.items() if count == best)[0].upper()


def money_by_currency(
    deals: Sequence[Mapping[str, Any]], *, stages: Iterable[str] = (), currency: str | None = None
) -> dict[str, dict[str, float]]:
    """The per-currency split behind every money figure.

    Keyed by an upper-cased currency code, with a deal that names none filed under
    :data:`choose_currency`'s default rather than dropped: a deal with an amount and no
    currency is still money in the report.
    """
    wanted = {normalise(value) for value in stages} if stages else None
    out: dict[str, dict[str, float]] = {}
    for deal in deals:
        stage_class = deal.get("stage_class", "unknown")
        if wanted is not None and (
            stage_class if stage_class in ("won", "lost") else "open"
        ) not in wanted:
            continue
        code = (normalise(deal.get("currency")) or (currency or "usd")).upper()
        bucket = out.setdefault(
            code, {"deals": 0.0, "pipeline_touched": 0.0, "active_pipeline": 0.0, "revenue": 0.0}
        )
        amount = deal.get("amount") or 0.0
        bucket["deals"] += 1
        bucket["pipeline_touched"] += amount
        # "open" means "not closed", matching tiles(); classify_stage never returns it.
        if stage_class not in ("won", "lost"):
            bucket["active_pipeline"] += amount
        elif stage_class == "won":
            bucket["revenue"] += amount
    return {code: {key: round(value, 2) for key, value in bucket.items()} for code, bucket in sorted(out.items())}


# --------------------------------------------------------------------------- #
# The tiles
# --------------------------------------------------------------------------- #


def classify_deal(deal: Mapping[str, Any], *, stage_sets: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
    """A projected deal with its stage classification and resolved owner attached.

    The owner falls back to the workspace's owner, and ``owner_source`` records which of
    the two it was. A *Deals By Owner* panel where half the rows read "unassigned"
    because a CRM payload omitted the field is unreadable, and a borrowed owner that does
    not say it was borrowed is a lie. Recorded as inference ``owner-fallback``.
    """
    stage_text = deal.get("stage", "")
    stage_class = classify_stage(
        stage_text,
        won=set((stage_sets or {}).get("won") or ()) or None,
        lost=set((stage_sets or {}).get("lost") or ()) or None,
    )
    own_owner = deal.get("owner") or ""
    room_owner = deal.get("room_owner") or ""
    owner = own_owner or room_owner
    return {
        **deal,
        "stage_class": stage_class,
        "owner": owner,
        "owner_source": "deal" if own_owner else ("room" if room_owner else "unassigned"),
    }


def tiles(deals: Sequence[Mapping[str, Any]], *, currency: str = "USD") -> dict[str, Any]:
    """The eight researched tiles, in the researched order.

    * ``total_pipeline_touched`` is the whole in-scope population's value and
      ``active_pipeline`` is the open subset's. They only mean different things under
      that reading, and ``active_deals`` is the count of the same open subset, which is
      what makes the four tiles mutually consistent.
    * ``close_rate`` is the researched fraction, ``closed_won / (closed_won +
      closed_lost)``, with open deals in neither arm - which is what the researched
      denominator says. ``None`` when nothing has closed.
    * ``days_to_close`` averages every *closed* deal, won and lost, because "days to
      close" is a cycle length and the researched close rate already treats both as
      closed. A close date before the created date is excluded rather than counted
      negative; the caller reports it in ``data_warnings``.
    """
    wanted = normalise(currency)
    total_deals = len(deals)
    pipeline_touched = 0.0
    active_pipeline = 0.0
    revenue = 0.0
    closed_won = 0
    closed_lost = 0
    active_deals = 0
    durations: list[float] = []

    for deal in deals:
        in_currency = (normalise(deal.get("currency")) or wanted) == wanted
        amount = deal.get("amount") or 0.0
        stage_class = deal.get("stage_class", "unknown")
        # "open" here means "not closed", not the literal string. classify_stage returns
        # won, lost or unknown, and an unknown stage is an open deal because nothing says
        # it closed - so every classification other than won or lost is the open subset.
        # Checking for the literal "open" instead made active_pipeline permanently zero on
        # a report whose every deal was mid-negotiation.
        is_open = stage_class not in ("won", "lost")
        if in_currency:
            pipeline_touched += amount
            if is_open:
                active_pipeline += amount
            elif stage_class == "won":
                revenue += amount
        if stage_class == "won":
            closed_won += 1
        elif stage_class == "lost":
            closed_lost += 1
        else:
            active_deals += 1
        if not is_open:
            span = days_between(deal.get("created"), deal.get("closed"))
            if span is not None:
                durations.append(span)

    decided = closed_won + closed_lost
    close_rate = (closed_won / decided) if decided else None
    days_to_close = (sum(durations) / len(durations)) if durations else None

    return {
        "total_deals": total_deals,
        "total_pipeline_touched": round(pipeline_touched, 2),
        "active_deals": active_deals,
        "active_pipeline": round(active_pipeline, 2),
        "closed_won_deals": closed_won,
        "revenue": round(revenue, 2),
        "close_rate": round(close_rate, 4) if close_rate is not None else None,
        "days_to_close": round(days_to_close, 1) if days_to_close is not None else None,
    }


def funnel(deals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every stage present, with its class, count, value and revenue contribution.

    This is the researched stage filter made visible. Its counts reconcile with the
    tiles by construction - they are the same deals, grouped - which is what lets a
    reader check the close rate against the funnel instead of taking it on trust.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for deal in deals:
        stage = deal.get("stage") or "(unset)"
        bucket = grouped.setdefault(
            stage,
            {
                "stage": stage,
                "class": deal.get("stage_class", "unknown"),
                "deals": 0,
                "amount": 0.0,
                "revenue": 0.0,
                "won": 0,
                "lost": 0,
                "open": 0,
            },
        )
        bucket["deals"] += 1
        amount = deal.get("amount") or 0.0
        bucket["amount"] += amount
        stage_class = deal.get("stage_class", "unknown")
        if stage_class == "won":
            bucket["won"] += 1
            bucket["revenue"] += amount
        elif stage_class == "lost":
            bucket["lost"] += 1
        else:
            bucket["open"] += 1
    rows = list(grouped.values())
    for row in rows:
        row["amount"] = round(row["amount"], 2)
        row["revenue"] = round(row["revenue"], 2)
    return sorted(rows, key=lambda row: (-row["deals"], row["stage"]))


# --------------------------------------------------------------------------- #
# The two panel charts
# --------------------------------------------------------------------------- #


def deals_created_over_time(
    deals: Sequence[Mapping[str, Any]],
    report_filter: ReportFilter,
    *,
    currency: str = "USD",
) -> list[dict[str, Any]]:
    """The researched **Deals Created Over Time** panel, ascending, gaps filled.

    Ranged on the deal's own created date, because that is the date the panel is named
    for, and a deal with no created date is counted in ``data_warnings`` rather than
    dropped from a chart that would then be quietly short. Bucketed by
    ``report_filter.bucket``.
    """
    wanted = normalise(currency)
    bucket = report_filter.bucket
    buckets = {
        row["date"]: row
        for row in date_range(
            report_filter.date_from,
            report_filter.date_to,
            (deal.get("created") for deal in deals),
            bucket=bucket,
        )
    }
    for deal in deals:
        created = deal.get("created")
        if created is None:
            continue
        row = buckets.get(bucket_key(created, bucket))
        if row is None:
            continue
        row["deals"] += 1
        if (normalise(deal.get("currency")) or wanted) == wanted:
            row["amount"] += deal.get("amount") or 0.0
    rows = [buckets[key] for key in sorted(buckets)]
    for row in rows:
        row["amount"] = round(row["amount"], 2)
    return rows


def deals_by_owner(deals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The researched **Deals By Owner** panel, biggest first, deterministic.

    ``owner_source`` is carried per row so a reader can tell a genuinely unassigned deal
    from one that borrowed its workspace's owner, which is a different problem with a
    different fix.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for deal in deals:
        owner = deal.get("owner") or "(unassigned)"
        bucket = grouped.setdefault(
            owner,
            {
                "owner": owner,
                "owner_source": deal.get("owner_source", "unassigned"),
                "deals": 0,
                "amount": 0.0,
                "won": 0,
                "lost": 0,
                "open": 0,
            },
        )
        bucket["deals"] += 1
        bucket["amount"] += deal.get("amount") or 0.0
        stage_class = deal.get("stage_class", "unknown")
        if stage_class == "won":
            bucket["won"] += 1
        elif stage_class == "lost":
            bucket["lost"] += 1
        else:
            bucket["open"] += 1
        # A group of deals can mix provenances; report the weaker one, which is the one
        # a reader has to act on.
        if bucket["owner_source"] == "deal" and deal.get("owner_source") != "deal":
            bucket["owner_source"] = deal.get("owner_source", "unassigned")
    rows = list(grouped.values())
    for row in rows:
        row["amount"] = round(row["amount"], 2)
    return sorted(rows, key=lambda row: (-row["amount"], row["owner"]))


# --------------------------------------------------------------------------- #
# Buyer engagement
# --------------------------------------------------------------------------- #


def engagement(
    events: Sequence[Mapping[str, Any]],
    report_filter: ReportFilter,
    *,
    in_scope_rooms: int,
) -> dict[str, Any]:
    """The researched **Buyer Engagement** half of the report.

    ``buyer_actions`` includes ``buyer_views``, because the researched gloss of an action
    is a client "interacting with a space", and clicking into a page is a view. Two
    overlapping counters with one relationship stated is more honest than two that
    silently differ by a number nobody can explain.

    Both are counted **only in in-scope workspaces**. The report exists to show the impact
    on pipeline, and a view in a workspace with no deal attached is not evidence that the
    workspace moved any. Recorded as inference ``engagement-is-scoped``.

    ``average_buyers_per_workspace`` divides by the in-scope room count, not by the rooms
    that happen to have engagement: averaging over the rooms that were touched is the way
    to make a thinly engaged pipeline look densely engaged.

    The series is zero-filled inside the requested range, and every row carries ``views``
    and ``actions`` even at zero. The shared bucket builder returns deal-shaped rows, so a
    series whose quiet days were handed through unchanged came back missing the keys
    entirely - a ``KeyError`` on the page rather than a gap in the chart.
    """
    in_range = [event for event in events if report_filter.in_event_range(event.get("occurred"))]
    views = sum(1 for event in in_range if event["is_view"])
    actions = len(in_range)

    buyers: dict[str, dict[str, Any]] = {}
    per_bucket: dict[str, dict[str, Any]] = {}
    for event in in_range:
        row = buyers.setdefault(
            event["buyer"],
            {"buyer": event["buyer"], "views": 0, "actions": 0, "workspaces": set(), "last_view_at": None},
        )
        row["actions"] += 1
        if event["room_id"]:
            row["workspaces"].add(str(event["room_id"]))
        if event["is_view"]:
            row["views"] += 1
            occurred = event["occurred"]
            if occurred is not None and (row["last_view_at"] is None or occurred > row["last_view_at"]):
                row["last_view_at"] = occurred
        if occurred_key := (bucket_key(event["occurred"], report_filter.bucket) if event["occurred"] else None):
            slot = per_bucket.setdefault(occurred_key, {"date": occurred_key, "views": 0, "actions": 0})
            slot["actions"] += 1
            if event["is_view"]:
                slot["views"] += 1

    ranked = sorted(
        (
            {
                "buyer": row["buyer"],
                "views": row["views"],
                "actions": row["actions"],
                "workspaces": len(row["workspaces"]),
                "last_view_at": row["last_view_at"].isoformat() if row["last_view_at"] else None,
            }
            for row in buyers.values()
        ),
        key=lambda row: (-row["actions"], -row["views"], row["buyer"]),
    )

    series = {
        row["date"]: {"date": row["date"], "views": 0, "actions": 0}
        for row in date_range(
            report_filter.date_from,
            report_filter.date_to,
            [as_date(key) for key in per_bucket],
            bucket=report_filter.bucket,
        )
    }
    for key, row in per_bucket.items():
        if key in series:
            series[key]["views"] = row["views"]
            series[key]["actions"] = row["actions"]

    return {
        "buyer_views": views,
        "buyer_actions": actions,
        "unique_buyers": len(buyers),
        "average_buyers_per_workspace": (
            round(len(buyers) / in_scope_rooms, 2) if in_scope_rooms else None
        ),
        "buyer_views_over_time": [series[key] for key in sorted(series)],
        "most_engaged_buyers": ranked,
    }


# --------------------------------------------------------------------------- #
# Coverage - the researched incompleteness, made nameable
# --------------------------------------------------------------------------- #


def coverage(
    scope: Mapping[str, Any],
    *,
    crm_connected: bool,
    provider: str = "",
) -> dict[str, Any]:
    """The report's completeness, and the rooms responsible for it.

    Sourced: "CRM integration must be on and deals attached or the report is incomplete -
    unless you are requiring reps attach a deal to each space, it's possible this report
    is missing data." That sentence is about a report that is *incomplete*, and a bare
    number cannot say which rooms made it so, so the rooms are named.

    ``complete`` is true only when the integration is on **and** every Sales-typed
    workspace has a deal attached. The report still answers when it is false, because a
    refusal would hide the very rooms a reader needs in order to fix it.
    """
    excluded = list(scope.get("excluded") or [])
    without_deal = [row for row in excluded if row["reason"] == "no_deal"]
    untyped = [row for row in excluded if row["reason"] == "not_sales"]
    warnings: list[dict[str, Any]] = []

    if not crm_connected:
        warnings.append(
            {
                "code": "crm_integration_off",
                "message": (
                    "The CRM integration is off, so no deal is reaching this report. The "
                    "research states the report is incomplete in that case."
                ),
                "room_id": None,
            }
        )
    if without_deal:
        warnings.append(
            {
                "code": "sales_room_without_deal",
                "message": (
                    f"{len(without_deal)} Sales-typed workspace(s) have no CRM deal "
                    "attached. Unless reps are required to attach a deal to every space, "
                    "this report is missing data for them."
                ),
                "room_id": None,
            }
        )
    if untyped:
        warnings.append(
            {
                "code": "workspace_not_typed_sales",
                "message": (
                    f"{len(untyped)} workspace(s) are not typed 'Sales' in their Internal "
                    "settings, so they are excluded from the report."
                ),
                "room_id": None,
            }
        )
    if scope.get("deals_not_attached"):
        warnings.append(
            {
                "code": "deal_not_attached",
                "message": (
                    f"{len(scope['deals_not_attached'])} deal(s) are attached to no "
                    "workspace, so they contribute to no figure in the report."
                ),
                "room_id": None,
            }
        )

    return {
        "crm_connected": crm_connected,
        "provider": provider,
        "rooms_total": scope.get("rooms_total", 0),
        "sales_typed_rooms": scope.get("sales_typed_rooms", 0),
        "in_scope_rooms": scope.get("in_scope_rooms", 0),
        "with_deal": scope.get("in_scope_rooms", 0),
        "without_deal": len(without_deal),
        "untyped_rooms": len(untyped),
        "deals_not_attached": len(scope.get("deals_not_attached") or []),
        "complete": bool(crm_connected) and not without_deal and not (scope.get("deals_not_attached") or []),
        "rooms_without_deal": [
            {"room_id": row["room_id"], "name": row["name"]} for row in without_deal
        ],
        "untyped": [{"room_id": row["room_id"], "name": row["name"]} for row in untyped],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def report(
    rooms: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    report_filter: ReportFilter,
    *,
    stage_sets: Mapping[str, Sequence[str]] | None = None,
    crm_connected: bool = False,
    provider: str = "",
) -> dict[str, Any]:
    """The whole researched report, computed on read and written nowhere.

    ``rooms``, ``deals`` and ``events`` are **already projected** - rows produced by
    :func:`project_room`, :func:`project_deal` and :func:`project_event`. This function
    does not project them again; see :func:`project_deal` for what happens when it does.

    The order of operations is the order the research implies: decide the population,
    classify what is in it, then count. Nothing downstream is allowed to widen the
    population, which is why the engagement side is filtered by the in-scope room ids
    rather than by the room's type at read time - one decision, made once.
    """
    scope = population(rooms, deals, stage_sets=stage_sets)
    in_scope_rooms = scope["included"]
    room_owner = {str(room["id"]): room.get("owner", "") for room in rooms}

    classified: list[dict[str, Any]] = []
    data_warnings: list[dict[str, Any]] = []
    for entry in in_scope_rooms:
        for deal in entry["deals"]:
            projected = classify_deal(
                {**deal, "room_owner": room_owner.get(str(entry["id"]), "")},
                stage_sets=stage_sets,
            )
            if not report_filter.matches_deal(
                created=projected["created"],
                stage_class=projected["stage_class"],
                stage_text=projected["stage"],
                owner=projected["owner"],
                team=projected["team"],
            ):
                continue
            if projected.get("created_source") == "recorded":
                data_warnings.append(
                    {
                        "code": "recorded_created_date",
                        "deal_id": projected["id"],
                        "message": (
                            "the deal carries no CRM creation date, so its interval is "
                            "measured from when it was first recorded here"
                        ),
                    }
                )
            elif projected["created"] is None:
                data_warnings.append(
                    {
                        "code": "no_created_date",
                        "deal_id": projected["id"],
                        "message": "the deal has no created date, so it is in every total and in no date bucket",
                    }
                )
            if projected["stage_class"] in ("won", "lost"):
                if projected["closed"] is None:
                    data_warnings.append(
                        {
                            "code": "closed_stage_without_close_date",
                            "deal_id": projected["id"],
                            "message": "the deal's stage is closed but it has no close date, so it is in no days-to-close average",
                        }
                    )
                elif days_between(projected["created"], projected["closed"]) is None:
                    data_warnings.append(
                        {
                            "code": "close_before_create",
                            "deal_id": projected["id"],
                            "message": "the deal's close date is before its created date, so it is excluded from days to close",
                        }
                    )
            classified.append(projected)

    currency = choose_currency(classified)
    money = money_by_currency(classified, currency=currency)
    in_scope_ids = {str(room["id"]) for room in in_scope_rooms}
    scoped_events = [
        event
        for event in events
        if event.get("room_id") and str(event["room_id"]) in in_scope_ids
    ]

    coverage_block = coverage(scope, crm_connected=crm_connected, provider=provider)
    currencies = sorted(money) or [currency]
    if len(currencies) > 1:
        coverage_block["warnings"].append(
            {
                "code": "mixed_currency",
                "message": (
                    f"deals span {len(currencies)} currencies ({', '.join(currencies)}); the "
                    f"money figures on this report are for {currency} only, and the split "
                    "is in 'money'"
                ),
                "room_id": None,
            }
        )
        data_warnings.append(
            {
                "code": "mixed_currency",
                "message": f"deals span {len(currencies)} currencies; totals are reported for {currency}",
            }
        )

    return {
        "filters": report_filter.echo(),
        "currency": currency,
        "currencies": currencies,
        "money": money,
        "tiles": tiles(classified, currency=currency),
        "scope": {
            "rooms_total": scope["rooms_total"],
            "sales_typed_rooms": scope["sales_typed_rooms"],
            "in_scope_rooms": scope["in_scope_rooms"],
            "excluded": scope["excluded"],
        },
        "funnel": funnel(classified),
        "deals_created_over_time": deals_created_over_time(classified, report_filter, currency=currency),
        "deals_by_owner": deals_by_owner(classified),
        "engagement": engagement(scoped_events, report_filter, in_scope_rooms=len(in_scope_rooms)),
        "coverage": coverage_block,
        "warnings": coverage_block["warnings"],
        "data_warnings": sorted(data_warnings, key=lambda row: (row["code"], str(row.get("deal_id") or ""))),
    }


def deal_rows(
    rooms: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    report_filter: ReportFilter,
    *,
    stage_sets: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """The filtered deal list behind the drill-in tiles, with its scope and totals.

    Returns the rows, the scope they were drawn from, and the report currency and
    totals, so the route can project each row for the wire with the workspace's owner
    available for the fallback. The totals are over the whole filtered population and
    never over the returned page, so ``limit`` cannot change a number a reader would
    quote.
    """
    scope = population(rooms, deals, stage_sets=stage_sets)
    room_owner = {str(room["id"]): room.get("owner", "") for room in rooms}
    rows: list[dict[str, Any]] = []
    for entry in scope["included"]:
        for deal in entry["deals"]:
            projected = classify_deal(
                {**deal, "room_owner": room_owner.get(str(entry["id"]), "")},
                stage_sets=stage_sets,
            )
            if report_filter.matches_deal(
                created=projected["created"],
                stage_class=projected["stage_class"],
                stage_text=projected["stage"],
                owner=projected["owner"],
                team=projected["team"],
            ):
                rows.append(projected)
    rows.sort(key=lambda row: (row["created"] or date.min, row["id"]), reverse=True)
    currency = choose_currency(rows)
    totals = tiles(rows, currency=currency)
    return rows, scope, {"currency": currency, "totals": totals}


def room_report(
    room: Mapping[str, Any],
    rooms: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    report_filter: ReportFilter,
    *,
    stage_sets: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """One workspace's contribution to the report, and whether it is in it at all.

    ``room``, ``rooms``, ``deals`` and ``events`` are all already projected; see
    :func:`project_deal`.

    An existing workspace that is simply out of scope is a **200 with a reason**, never a
    404. The research makes typing a workspace as ``Sales`` the user's very first step, so
    the single most likely thing a reader arrives here wanting is an explanation of why
    their room is not in the numbers - and a 404 says "no such room", which is a different
    and wrong answer.
    """
    scope = population(rooms, deals, stage_sets=stage_sets)
    room_id = str(room.get("id") or "")
    entry = next((item for item in scope["included"] if str(item["id"]) == room_id), None)
    excluded = next((entry_row for entry_row in scope["excluded"] if entry_row["room_id"] == room_id), None)

    rows: list[dict[str, Any]] = []
    if entry is not None:
        for deal in entry["deals"]:
            projected = classify_deal(
                {**deal, "room_owner": room.get("owner", "")}, stage_sets=stage_sets
            )
            if report_filter.matches_deal(
                created=projected["created"],
                stage_class=projected["stage_class"],
                stage_text=projected["stage"],
                owner=projected["owner"],
                team=projected["team"],
            ):
                rows.append(projected)
        rows.sort(key=lambda row: (row["created"] or date.min, row["id"]), reverse=True)

    currency = choose_currency(rows)
    scoped_events = [
        event
        for event in events
        if event.get("room_id") and str(event["room_id"]) == room_id
    ]
    engagement_block = engagement(scoped_events, report_filter, in_scope_rooms=1 if entry else 0)
    engagement_block.pop("buyer_views_over_time", None)
    reason = None if entry else (excluded["reason"] if excluded else "not_sales")
    return {
        "room": {
            "id": room_id,
            "name": room.get("name", ""),
            "account": room.get("account", ""),
            "type": room.get("type", ""),
            "owner": room.get("owner", ""),
        },
        "in_scope": entry is not None,
        "reason": reason,
        "currency": currency,
        "deals": rows,
        "tiles": tiles(rows, currency=currency),
        "engagement": engagement_block,
    }


__all__ = [
    "TILES",
    "as_date",
    "choose_currency",
    "classify_deal",
    "classify_stage",
    "coverage",
    "days_between",
    "deal_rows",
    "deals_by_owner",
    "deals_created_over_time",
    "engagement",
    "funnel",
    "is_sales_type",
    "money_by_currency",
    "population",
    "project_deal",
    "project_event",
    "project_room",
    "report",
    "room_report",
    "tiles",
    "utc",
    "view_deal",
]

utc = timezone.utc
