"""The pure rules of a change-stream repair. No store, no framework, no clock.

Everything here is a function of its arguments, and every function is here because
a sentence in the research fixes it. The engine in
:mod:`dsr.crm_integration.reconcile_engine` owns the collections and the writes;
this module owns the decisions, so a test can pin each decision without a database
and without a vendor.

The four decisions
------------------

**What kind of event is this?** :func:`classify` answers with two words, because
the two repair shapes are what every rule keys on. A gap event "contain[s] the
record ID", an overflow event carries "no record data and no record ID".

**Is this change after the gap?** "To ensure that the change is after the gap event,
compare the ``commitTimestamp`` fields of both events."

**Did the change already land in the re-read?** "To ensure that the change
occurred before the data is reconciled, compare the ``LastModifiedDate`` fields on
the change event and the record retrieved in the next step."

**Is the cursor still resumable?** "Changes are returned if the last token is within
a default value of seven days. ... If unprocessed changes are older than the
configured value, the system throws an exception."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.crm_integration import gap_vocabulary as vocab
from dsr.crm_integration.gap_errors import (
    MalformedGapEvent,
    MissingEntity,
    MissingRecordId,
    UnknownGapType,
    UnsupportedVendor,
)
from dsr.crm_integration.gap_sources import DELETED_ENTITY_SUFFIX

#: Why a change event was not applied, in the order the engine checks them.
#:
#: The order matters and is not alphabetical. The research's own sentence is the
#: order: a change that is not after the gap is dropped before anything else is
#: asked, and only a change that survived that is measured against the re-read.
DROP_REASONS: tuple[str, ...] = (
    "no_dirty_marker",
    "older_than_the_gap",
    "covered_by_the_re_read",
    "dirty_and_newer_than_the_read",
)

#: The two Dataverse cursor states, and what the room does about each.
CURSOR_RESUMABLE = "resumable"
CURSOR_EXPIRED = "expired"


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def parse_timestamp(value: Any, *, field: str = "commit_timestamp") -> str:
    """An ISO-8601 instant, normalised to UTC and always with an offset.

    The research names ``commitTimestamp`` and nothing about its format, so the
    parsing is deliberately generous - a bare ``Z``, an offset, a naive value
    assumed to be UTC - and the *result* is strict. Two dirty markers are compared
    by these timestamps, and a naive local time is not orderable across two
    operators.
    """
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    raise MalformedGapEvent(
        f"{field} is not an ISO-8601 instant; got {value!r}. "
        "The room compares two of these to decide whether a change is after a gap, "
        "so it names the zone rather than guessing it."
    )


def _instant(value: Any, field: str) -> datetime:
    return datetime.fromisoformat(parse_timestamp(value, field=field))


def age_days(held_since: Any, now: datetime, *, field: str = "updatedAt") -> float:
    """How long the room has held a cursor, in days."""
    return round((now - _instant(held_since, field)).total_seconds() / 86400.0, 3)


# --------------------------------------------------------------------------- #
# The header
# --------------------------------------------------------------------------- #


def _pick(body: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in body and body[name] is not None:
            return body[name]
    return None


def _record_ids(value: Any) -> list[str]:
    """The record ids a header names, as a list of strings.

    ``recordIds`` is plural in the research's data flow and singular in the gap
    event's prose ("they contain the record ID"), so a caller may send one id, a
    list, or a comma-separated string and all three mean the same thing.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def normalise_gap_event(body: Mapping[str, Any], *, vendor: str = "salesforce") -> dict[str, Any]:
    """One gap or overflow header, in the shape this package stores.

    Raised as a :class:`~dsr.crm_integration.gap_errors.GapEventError` rather than
    returning a half-filled row, because the marker's whole value is the gap's
    commit timestamp and a row without one would mark a record dirty as of an
    unknown date.
    """
    if not isinstance(body, Mapping):
        raise MalformedGapEvent(f"a gap event must be a JSON object; got {type(body).__name__}")

    raw_type = _pick(body, vocab.HEADER_FIELDS["change_type"])
    change_type = vocab.normalise_change_type(raw_type)
    if change_type is None:
        raise UnknownGapType(
            f"changeType must be one of {', '.join(vocab.CHANGE_TYPES)}; got {raw_type!r}. "
            "Those five values are the whole of what a gap or overflow header may carry."
        )

    if not vocab.cursor_supports_gap(vendor):
        raise UnsupportedVendor(
            f"{vendor!r} has no gap or overflow mechanism this workflow implements. "
            f"{vocab.UNSUPPORTED_VENDOR_QUOTE}. "
            f"The vendors that do are: {', '.join(vocab.GAP_VENDORS)}."
        )

    entity = _pick(body, vocab.HEADER_FIELDS["entity"])
    if not isinstance(entity, str) or not entity.strip():
        raise MissingEntity(
            "the header must name the entity type. An overflow emits one event for "
            "each entity type in the transaction, so an event with no entity cannot "
            "say which one it lost."
        )

    kind = vocab.classify(change_type)
    record_ids = _record_ids(_pick(body, vocab.HEADER_FIELDS["record_ids"]))
    if kind == "gap":
        if not record_ids:
            record_ids = _record_ids(_pick(body, vocab.RECORD_ALIASES))
        if not record_ids:
            raise MissingRecordId(
                "a gap event must name the record it is about. Gap events do not "
                "contain record data, but they contain the record ID, which enables "
                "you to retrieve the record from Salesforce."
            )

    replay_id = _pick(body, vocab.HEADER_FIELDS["replay_id"])
    event: dict[str, Any] = {
        "change_type": change_type,
        "kind": kind,
        "operation": vocab.operation_for(change_type),
        "vendor": vendor.strip().casefold(),
        "entity": entity.strip(),
        "record_ids": record_ids,
        "commit_timestamp": parse_timestamp(
            _pick(body, vocab.HEADER_FIELDS["commit_timestamp"]),
            field="commit_timestamp",
        ),
        "transaction_key": _text(_pick(body, vocab.HEADER_FIELDS["transaction_key"])),
        "sequence_number": _number(_pick(body, vocab.HEADER_FIELDS["sequence_number"])),
        "change_count": _number(_pick(body, vocab.HEADER_FIELDS["change_count"])),
        "exceeds_overflow_threshold": vocab.exceeds_overflow_threshold(
            _pick(body, vocab.HEADER_FIELDS["change_count"])
        ),
        "cursor_kind": vocab.cursor_kind_for(vendor),
        "replay_id": str(replay_id).strip() if replay_id is not None else None,
    }

    # An overflow names no record, so an id arriving with one is the caller's error
    # rather than something to store: it would make the event look repairable per
    # record, and the repair for an overflow is a whole entity.
    if kind == "overflow":
        event["record_ids"] = []
        if event["replay_id"] is None:
            event["replay_id"] = None
    return event


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# The two comparisons
# --------------------------------------------------------------------------- #


def change_is_after_the_gap(gap_commit_timestamp: Any, change_commit_timestamp: Any) -> bool:
    """Whether a change event committed after the gap it sits behind.

    "To ensure that the change is after the gap event, compare the
    ``commitTimestamp`` fields of both events."

    Strictly greater. A change committed at the same instant as the gap is not
    evidence of anything the gap did not already cover, so it is treated as part of
    the gap rather than as news after it.
    """
    gap_at = _instant(gap_commit_timestamp, "gap commit_timestamp")
    change_at = _instant(change_commit_timestamp, "change commit_timestamp")
    return change_at > gap_at


def change_is_already_in_the_re_read(change_last_modified: Any, record_last_modified: Any) -> bool:
    """Whether the record the room just re-read already carries this change.

    "To ensure that the change occurred before the data is reconciled, compare the
    ``LastModifiedDate`` fields on the change event and the record retrieved in the
    next step."

    Greater or equal. The record came from the vendor *now*, so a record at least as
    new as the change means the re-read already wrote that change. A record strictly
    older than the change means the vendor is behind the stream, and the room must
    wait rather than apply a delta on top of a stale read.
    """
    change_at = _instant(change_last_modified, "change LastModifiedDate")
    record_at = _instant(record_last_modified, "record LastModifiedDate")
    return record_at >= change_at


def last_modified_of(record: Mapping[str, Any] | None) -> str | None:
    """A record's ``LastModifiedDate``, or ``None`` when it carries none.

    Named by the research rather than guessed, so a room whose records spell the
    field differently has one place to change.
    """
    if not record:
        return None
    value = record.get(vocab.LAST_MODIFIED_FIELD)
    if value is None:
        return None
    return parse_timestamp(value, field=vocab.LAST_MODIFIED_FIELD)


def decide_change(
    *,
    dirty_marker: Mapping[str, Any] | None,
    commit_timestamp: Any,
    last_modified: Any,
    record: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Whether to apply a change event to a record, and why.

    Returns ``(applied, reason)`` where ``reason`` is one of
    :data:`DROP_REASONS`. The four cases are checked in the research's order:

    1. no dirty marker. The record is clean, so the change is applied. Nothing to
       reconcile, nothing to protect.
    2. comparison A is false. The change is not after the gap, so it is already
       inside the window the re-read is about to cover. Dropped.
    3. comparison A true, comparison B true. The re-read already carries this
       change, so applying it again would write the same row twice and re-open a
       flag the repair just cleared. Applied, and the reason says so.
    4. comparison A true, comparison B false. The change is newer than the record
       the vendor returned, so it has *not* reached the room and the vendor is
       behind the stream. Dropped, and the dirty marker stays open, because the
       next re-read has to see this change too.

    Case 4 is the one the research's sentence exists for. Applying it would produce
    a row that was never true.
    """
    if not dirty_marker:
        return True, DROP_REASONS[0]
    if not change_is_after_the_gap(dirty_marker.get("gap_commit_timestamp"), commit_timestamp):
        return False, DROP_REASONS[1]
    record_modified = last_modified_of(record)
    if record_modified is not None and last_modified is not None:
        if change_is_already_in_the_re_read(last_modified, record_modified):
            return True, DROP_REASONS[2]
    return False, DROP_REASONS[3]


# --------------------------------------------------------------------------- #
# The deleted set
# --------------------------------------------------------------------------- #


def deleted_by_difference(held_ids: Sequence[str], returned_ids: Sequence[str]) -> list[str]:
    """The deleted set of the overflow procedure's option (a).

    "Get the non-deleted records from Salesforce, and synchronize." What the room
    held that the read did not return has been deleted upstream. Sorted so the
    result is a set difference a test can write down.
    """
    returned = {str(record_id) for record_id in returned_ids}
    return sorted({str(record_id) for record_id in held_ids} - returned)


def deleted_by_recycle_bin(recycle_bin: Sequence[Mapping[str, Any]]) -> list[str]:
    """The deleted set of the overflow procedure's option (b).

    "Query all records for the entity with ``isDeleted=true``. You get all the
    soft-deleted records for that entity that are in the Recycle Bin." The ids come
    from the query, so nothing is inferred.
    """
    return sorted({_row_id(row) for row in recycle_bin if _row_id(row)})


def delta_page(page: Mapping[str, Any] | None) -> dict[str, Any]:
    """A Dataverse delta response, split into what is live and what is gone.

    The research's own example, kept as the shape this package reads::

        {"@odata.deltaLink": ..., "value": [{...},
         {"@odata.context": ".../$deletedEntity", "id": "2e451703-...",
          "reason": "deleted"}]}

    A row counts as a deletion when it says ``reason`` is ``deleted`` or when its
    context names the deleted-entity set. Both tests are there because the research
    shows both markers on the same row and a room should not depend on a vendor
    sending only one.
    """
    if not isinstance(page, Mapping):
        return {"live": [], "deleted": [], "delta_link": None}
    live: list[dict[str, Any]] = []
    deleted: list[str] = []
    for row in page.get("value") or []:
        if not isinstance(row, Mapping):
            continue
        context = str(row.get("@odata.context") or "")
        if str(row.get("reason") or "").casefold() == "deleted" or context.endswith(
            DELETED_ENTITY_SUFFIX
        ):
            record_id = _row_id(row)
            if record_id:
                deleted.append(record_id)
            continue
        live.append(dict(row))
    return {
        "live": live,
        "deleted": sorted(set(deleted)),
        "delta_link": page.get("@odata.deltaLink"),
    }


def _row_id(row: Mapping[str, Any]) -> str:
    for key in ("Id", "id", "recordId", "record_id"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


# --------------------------------------------------------------------------- #
# The cursors
# --------------------------------------------------------------------------- #


def cursor_verdict(
    record: Mapping[str, Any] | None, now: datetime, expiry_days: int | None = None
) -> dict[str, Any]:
    """A stored cursor plus whether the vendor will still answer for it.

    Only a Dataverse ``delta_link`` expires. A Salesforce ``replay_id`` is a
    position in an event stream, and the research's seven-day sentence is about a
    "last token" Dataverse holds, so the test is kind-aware. Making it kind-blind
    would declare a perfectly good Replay ID dead a week after it was stored, and
    the overflow would be unrecoverable for no reason the vendor gave.

    An expired link answers ``resumable: False`` and names the remedy in
    ``fallback``, because the research calls that window "the hard deadline after
    which recovery must fall back to a full re-read".
    """
    days = vocab.default_expiry_days() if expiry_days is None else int(expiry_days)
    if not record or not record.get("cursor"):
        return {
            "present": False,
            "kind": "none",
            "held_since": None,
            "age_days": 0.0,
            "expiry_days": days,
            "applies": False,
            "resumable": False,
            "state": "absent",
            "fallback": "full_reread",
        }
    kind = str(record.get("kind") or "none")
    applies = kind == "delta_link"
    held_since = record.get("updatedAt")
    aged = age_days(held_since, now) if applies and held_since else 0.0
    expired = bool(
        applies and held_since and _instant(held_since, "updatedAt") + timedelta(days=days) <= now
    )
    verdict: dict[str, Any] = {
        "present": True,
        "kind": kind,
        "vendor": record.get("vendor", ""),
        "scope": record.get("scope", ""),
        "scope_value": record.get("scope_value", ""),
        "position": record.get("cursor"),
        "held_since": held_since,
        "age_days": aged,
        "expiry_days": days,
        "applies": applies,
        "resumable": not expired,
        "state": CURSOR_EXPIRED if expired else CURSOR_RESUMABLE,
    }
    if expired:
        verdict["fallback"] = "full_reread"
        verdict["reason"] = (
            f"the {kind} was stored {aged:g} day(s) ago, past the {days} day window "
            "the vendor will still answer for. It throws rather than return the "
            "changes since it, so this room falls back to a full re-read."
        )
    return verdict


def is_expired(
    record: Mapping[str, Any] | None, now: datetime, expiry_days: int | None = None
) -> bool:
    """Whether this stored cursor is past its window."""
    return str(cursor_verdict(record, now, expiry_days).get("state")) == CURSOR_EXPIRED


def full_reread_cursor(now: datetime) -> dict[str, Any]:
    """The cursor a full re-read leaves behind, so the next overflow can resume.

    A Replay ID is "the starting point for the data reconciliation", so the position
    the room reconciles *from* is the one it must store, not the one the overflow
    carried. This returns a record with that shape and an explicit kind of
    ``replay_id``, which is what makes it resumable for the next one.
    """
    return {
        "vendor": "salesforce",
        "kind": "replay_id",
        "cursor": f"reconciled:{now.astimezone(timezone.utc).isoformat()}",
        "updatedAt": now.astimezone(timezone.utc).isoformat(),
    }
