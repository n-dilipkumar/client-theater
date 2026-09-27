"""The cursor: one standard record shape for four vendor-specific handles.

The research's extensibility note is the reason this module exists:

    "The cursor is an interface, so a vendor-specific cursor (Bulk job id, delta
    link, ``DataToken``) is swapped for a standard ``{vendor, connectionId,
    cursor, updatedAt}`` record. This is also what makes backfill idempotent
    under retry - a third party can add a new vendor by implementing only
    'create job' and 'read page'."

So the shape is the research's, verbatim, and the four vendor handles are
interchangeable behind it. :func:`build` is the only place one is minted,
:func:`is_expired` is the only place a Dataverse token's seven-day rule is
applied, and the engine is the only caller of either.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dsr.crm_backfill.errors import CursorExpired
from dsr.crm_backfill.vocabulary import CURSOR_KINDS, NUMBERS

#: The research's own field names, kept as the record's top-level keys rather
#: than folded into snake_case, because a third party implementing a new vendor
#: reads this sentence and not this module.
CURSOR_FIELDS: tuple[str, ...] = ("vendor", "connectionId", "cursor", "updatedAt")

#: Which strategy each cursor kind belongs to.
#:
#: Needed because a cursor kind and a strategy are not the same vocabulary: a
#: Dataverse ``data_token`` is read with ``delta_read``, and ``data_token`` is
#: not a strategy name. A run that resumes from a stored cursor has to keep using
#: the strategy that cursor belongs to - switching would abandon the thing it is
#: resuming - so the mapping is stated here rather than guessed at the call site.
CURSOR_STRATEGY: dict[str, str] = {
    "bulk_job_id": "async_job",
    "export_id": "async_job",
    "data_token": "delta_read",
    "odata_delta_link": "delta_read",
    "paging_cookie": "paged_read",
    "none": "paged_read",
}


def strategy_for(kind: str) -> str | None:
    """The strategy a cursor of this kind belongs to, or ``None`` if unknown."""
    return CURSOR_STRATEGY.get(kind)


def default_expiry_days() -> int:
    """The researched default for a cursor the vendor will age out.

    Seven days, from "Changes are returned if the last token is within a default
    value of seven days." A connection may override it, because the research says
    the Organization column "controls this duration and can be changed" - but the
    value it falls back to is the researched one, not a round number of our own.
    """
    return int(NUMBERS["change_tracking_expiry_days"]["value"])


def default_page_size() -> int:
    """The researched default page size: Dataverse's PagingInfo Count of 5000."""
    return int(NUMBERS["dataverse_page_size"]["value"])


def build(
    *,
    vendor: str,
    connection_id: str,
    cursor: str | None,
    updated_at: str | datetime,
    kind: str,
    run_id: str | None = None,
    pages_advanced: int = 0,
    rows_written: int = 0,
) -> dict[str, Any]:
    """Mint the standard cursor record.

    ``updatedAt`` is the room's own clock, not the vendor's. The Dataverse rule
    is about how long *the room* has held the token unprocessed, so the room's
    timestamp is the one that has to be right, and reading it back from the
    vendor would make the expiry test depend on a clock this product does not
    control.
    """
    if kind not in CURSOR_KINDS:
        raise CursorExpired(f"unknown cursor kind {kind!r}")
    stamp = _iso(updated_at)
    record: dict[str, Any] = {
        "vendor": vendor,
        "connectionId": connection_id,
        "cursor": cursor,
        "updatedAt": stamp,
        # The rest is this product's bookkeeping, and it is additive on top of
        # the four researched fields rather than folded into them, so a third
        # party reading the documented four still finds exactly the four.
        #
        # `connection_id` is a deliberate mirror of `connectionId`: the store
        # filters a record on the JSON path a caller writes, and the whole
        # product's listings are snake_case, so a query on `connection_id` has
        # to resolve. It is written here rather than at every read site so the
        # two spellings cannot drift.
        "connection_id": connection_id,
        "kind": kind,
        "resumable": bool(cursor),
        "pages_advanced": pages_advanced,
        "rows_written": rows_written,
    }
    if run_id:
        record["runId"] = run_id
    return record


def is_expired(record: Mapping[str, Any] | None, now: datetime, expiry_days: int | None = None) -> bool:
    """Whether the vendor will no longer answer for this cursor.

    Only a ``data_token`` expires. A Bulk job id, an export id and a paging
    cookie are handles to a thing that is either still there or is not, and
    Dataverse's seven-day rule says nothing about them. Making the test
    kind-aware is what stops a finished Salesforce job from being declared
    unresumable a week later when its id simply no longer resolves.
    """
    if not record or not record.get("cursor"):
        return False
    if record.get("kind") != "data_token":
        return False
    days = default_expiry_days() if expiry_days is None else int(expiry_days)
    return _parse(record.get("updatedAt")) + timedelta(days=days) <= now


def age_days(record: Mapping[str, Any] | None, now: datetime) -> float:
    """How long the room has held this cursor, in days.

    Reported rather than only used as a predicate, because the operator looking at
    a stalled run needs to know *how* stale it is, not just that it is.
    """
    if not record or not record.get("updatedAt"):
        return 0.0
    return round((now - _parse(record["updatedAt"])).total_seconds() / 86400.0, 3)


def describe(record: Mapping[str, Any] | None, now: datetime, expiry_days: int | None = None) -> dict[str, Any]:
    """The cursor plus its expiry verdict, for a run and for the wizard."""
    if not record:
        return {
            "present": False,
            "kind": "none",
            "expiry_days": default_expiry_days() if expiry_days is None else int(expiry_days),
            "expired": False,
            "age_days": 0.0,
        }
    days = default_expiry_days() if expiry_days is None else int(expiry_days)
    expired = is_expired(record, now, days)
    verdict: dict[str, Any] = {
        "present": bool(record.get("cursor")),
        "kind": record.get("kind", "none"),
        "expiry_days": days,
        "applies": record.get("kind") == "data_token",
        "expired": expired,
        "age_days": age_days(record, now),
        "resumable": bool(record.get("cursor")) and not expired,
    }
    if expired:
        verdict["remedy"] = "start a new full-history backfill"
    elif record.get("kind") == "data_token":
        verdict["resumable_until"] = (_parse(record["updatedAt"]) + timedelta(days=days)).isoformat()
    return verdict


def require_resumable(record: Mapping[str, Any] | None, now: datetime, expiry_days: int | None = None) -> None:
    """Refuse to resume from a cursor the vendor has stopped answering for.

    Raising rather than restarting is the whole point. The research promises a
    restart yields "no duplicates, no gaps"; a silent full re-read would write
    every row of the range a second time and make that untrue for the run that
    recorded the cursor. Dataverse itself "throws an exception" in this case, so
    this is the room refusing first, with a message that says what to do.
    """
    if not record or not record.get("cursor"):
        raise CursorExpired("there is no stored cursor to resume from")
    if is_expired(record, now, expiry_days):
        days = default_expiry_days() if expiry_days is None else int(expiry_days)
        raise CursorExpired(
            f"the {record.get('kind')} recorded at {record.get('updatedAt')} is "
            f"{age_days(record, now):g} day(s) old, past the {days} day window "
            f"{record.get('vendor')} will still answer for, and the vendor will throw rather than "
            "return the changes since it. Resuming cannot be made safe, so this run is stalled: "
            "start a new full-history backfill to read the range again."
        )


def _iso(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse(value: Any) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # A cursor whose timestamp cannot be read is treated as infinitely old
        # rather than infinitely new. The error it produces says the run is
        # stalled, which is the safe direction: a run that refuses to resume can
        # be started again, and a run that resumes on a bad clock cannot.
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
