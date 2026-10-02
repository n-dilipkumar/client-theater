"""Recording the occurrences the report is computed from.

The research names two things that produce the numbers, and this module takes
both:

* the ``asset.viewed`` / ``asset.shared`` / ``asset.downloaded`` webhooks, which
  arrive one payload at a time and carry an asset id;
* the product's own ``activity`` collection, which is written by every other
  feature and names the asset by title.

Both land as one row in :data:`~dsr.influence.vocab.EVENT_COLLECTION`, so the
report reads a single shape and neither source can be counted twice against the
other. The projection from ``activity`` is keyed on the source record's id, so
running it twice changes nothing.

Every write here takes ``source`` as a *required* keyword. That is the defect
the port brief names by example: a feature whose audit log kept recording a
path the app had stopped serving. The route builds the string from
``router.prefix``; nothing in this module knows a URL.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.influence.errors import InfluenceError, UnknownAsset, UnknownRoom
from dsr.influence.vocab import (
    ACTION_ALIASES,
    ACTIONS,
    ACTIVITY_COLLECTION,
    AUDIENCES,
    DOWNLOADED,
    EVENT_COLLECTION,
    EXTERNAL,
    INTERNAL,
    ROOM_COLLECTION,
    SHARED,
    VIEWED,
    asset_title,
    parse_time,
    scan,
    utcnow,
)
from dsr.store import RecordStore

#: The core ``activity`` collection says "viewed"; the researched webhooks say
#: "asset.viewed". Both are recognised so the projection and the webhook seam
#: produce identical rows.
_PROJECTABLE = {VIEWED, SHARED, DOWNLOADED}


def normalize_action(action: Any) -> str | None:
    """Map either spelling of an action onto the three canonical ones.

    Returns ``None`` for anything else rather than raising: the caller decides
    whether an unrecognised action is a rejection or a row worth keeping, and
    the projection over ``activity`` wants to *skip* the six other actions that
    live in the same collection (``commented``, ``opened_link``,
    ``completed_section``) rather than fail.
    """
    if action is None:
        return None
    return ACTION_ALIASES.get(str(action).strip().lower())


def known_actions() -> list[dict[str, Any]]:
    """The canonical actions, and every spelling that maps onto each.

    Published at ``/vocabulary`` so a client can validate a payload's action
    without a round trip, and so a new spelling is visible to callers the moment
    it is accepted rather than only in the source.
    """
    grouped: dict[str, list[str]] = {}
    for spelling, action in ACTION_ALIASES.items():
        grouped.setdefault(action, []).append(spelling)
    return [{"action": action, "spellings": sorted(grouped[action])} for action in ACTIONS]


def _require_room(store: RecordStore, room_id: str | None) -> str | None:
    """Check a room id resolves, so a link never points at a room that is gone."""
    if not room_id:
        return None
    record = store.get(room_id)
    if record is None or record.get("collection") != ROOM_COLLECTION:
        raise UnknownRoom(room_id)
    return room_id


def _resolve_asset(
    store: RecordStore,
    *,
    asset_id: Any = None,
    asset: Any = None,
    title: Any = None,
) -> dict[str, Any]:
    """Find the library asset an event is about.

    By id first, then by title, because the two sources name assets
    differently: the researched webhook carries the asset's id, while the
    product's own activity rows carry the document's title in ``target``. Both
    are needed for the report to be computable from either.
    """
    if asset_id:
        record = store.get(str(asset_id))
        if record is None or record.get("collection") != "document":
            raise UnknownAsset(str(asset_id))
        return record

    wanted = asset or title
    if not wanted:
        raise InfluenceError(
            "an asset event needs asset_id, or the asset's title to match the library"
        )
    needle = str(wanted).strip()
    matches = [
        record for record in scan(store, "document").records if asset_title(record) == needle
    ]
    if not matches:
        raise UnknownAsset(needle)
    # Two assets with the same title is a real possibility in a library that
    # was never curated - the research's own caveat - and guessing between them
    # would silently attribute revenue to the wrong one. So the ambiguity is
    # refused and named.
    if len(matches) > 1:
        ids = ", ".join(sorted(str(record["id"]) for record in matches))
        raise InfluenceError(
            f"{len(matches)} library assets are titled {needle!r} ({ids}); "
            "send asset_id instead of a title"
        )
    return matches[0]


def _audience(payload: Mapping[str, Any], action: str) -> str:
    """Internal share or external view, as the researched metrics define them.

    "Content shares" are internal by definition - "the number of times an
    internal person shared the asset a new time" - and "content client views"
    are external. A caller that genuinely observed an internal view says so with
    ``internal: true``; nothing here guesses from a person field.

    A declared ``audience`` outside the two is refused rather than ignored. It
    moves a number the report is read for - internal views stop counting as
    client views - so quietly defaulting would turn a caller's mistake into a
    confident metric.
    """
    declared = payload.get("audience")
    if declared is not None and declared != "":
        text = str(declared).strip().lower()
        if text not in AUDIENCES:
            raise InfluenceError(f"audience {declared!r} is not one of {', '.join(AUDIENCES)}")
        return text
    if "internal" in payload:
        return INTERNAL if payload["internal"] else EXTERNAL
    if payload.get("shared_by") or payload.get("sharedBy"):
        return INTERNAL
    return INTERNAL if action == SHARED else EXTERNAL


def _seconds(payload: Mapping[str, Any]) -> int:
    """Dwell time for one occurrence, in whole seconds.

    The researched "total time spent" is a sum of dwell, and a negative dwell
    is not a thing, so a negative or unparseable value is a refusal rather than
    a zero: a zero would quietly understate the asset's time spent and there is
    no way for a reader to tell it apart from a genuine no-dwell event.
    """
    for key in ("seconds", "seconds_on_page", "dwell_seconds"):
        if key not in payload or payload[key] is None or payload[key] == "":
            continue
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise InfluenceError(f"seconds {value!r} is not a number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise InfluenceError(f"seconds {value!r} is not a number") from exc
        if number < 0:
            raise InfluenceError(f"seconds {value!r} cannot be negative")
        return int(round(number))
    return 0


def _existing_event_id(store: RecordStore, event_id: str) -> dict[str, Any] | None:
    """A previously recorded occurrence with this external id, if any.

    A webhook that is retried must not add a second share. Nothing in the
    researched sources documents redelivery, so the key is caller-supplied
    rather than guessed, and an event with no ``event_id`` is never deduplicated:
    guessing at an identity the caller did not provide would merge two real
    occurrences. Recorded in :mod:`dsr.influence.inferences`.
    """
    if not event_id:
        return None
    found = store.find(EVENT_COLLECTION, {"event_id": event_id}, limit=1)
    return found[0] if found else None


def record_event(
    store: RecordStore,
    payload: Mapping[str, Any] | None = None,
    *,
    room_id: str | None = None,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Record one ``asset.viewed`` / ``asset.shared`` / ``asset.downloaded``.

    The write is one row and one audit row. ``source`` is required: the route
    that served this call is the only thing that knows the path, and an audit row
    that cannot be traced back to a request is not an audit trail.

    Returns the stored event. A repeat of a known ``event_id`` returns the
    original row unchanged and writes nothing, with ``duplicate`` set, so a
    retried webhook is visible to the caller and invisible to the report.
    """
    body = dict(payload or {})

    action = normalize_action(body.get("action") or body.get("event"))
    if action is None:
        raise InfluenceError(
            "action must be one of "
            + ", ".join(f"asset.{name}" for name in ACTIONS)
            + f" (or {', '.join(ACTIONS)}); got {body.get('action') or body.get('event')!r}"
        )

    event_id = str(body.get("event_id") or body.get("eventId") or "").strip() or None
    existing = _existing_event_id(store, event_id)
    if existing is not None:
        return {**existing, "duplicate": True}

    record = _resolve_asset(
        store,
        asset_id=body.get("asset_id") or body.get("assetId"),
        asset=body.get("asset") or body.get("target"),
        title=body.get("title"),
    )

    at = (
        parse_time(
            body.get("occurred_at") or body.get("at") or body.get("timestamp"), "occurred_at"
        )
        or utcnow()
    )

    # The event belongs to a workspace. A share straight from the library has
    # none, and the research's report spans "all workspaces", so an event with
    # no room is a legitimate portfolio-wide occurrence rather than a mistake.
    #
    # The workspace is carried by the record *envelope* only, never in `data`:
    # the audited store strips the reserved keys out of a payload on the way in,
    # so a `data["room_id"]` here would be silently discarded. `load_events` puts
    # it back from the envelope, which is the one place it is actually stored.
    resolved_room = _require_room(store, room_id or record.get("room_id"))

    data: dict[str, Any] = {
        "asset_id": str(record["id"]),
        "asset_title": asset_title(record),
        "action": action,
        "audience": _audience(body, action),
        "at": at.isoformat(timespec="milliseconds"),
        "seconds": _seconds(body),
    }
    for key in ("person", "account", "shared_by", "collection", "link_id"):
        value = body.get(key)
        if value not in (None, ""):
            data[key] = str(value) if not isinstance(value, (int, float)) else value
    if event_id:
        data["event_id"] = event_id
    extra = body.get("metadata")
    if isinstance(extra, Mapping):
        data["metadata"] = dict(extra)

    stored = store.create(EVENT_COLLECTION, data, room_id=resolved_room, actor=actor, source=source)
    return {**stored, "duplicate": False}


def ingest_activity(
    store: RecordStore,
    *,
    room_id: str | None = None,
    limit: int | None = None,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Project the product's ``activity`` rows into the event log.

    The rest of the product records buyer activity in one collection, and the
    researched webhooks are the same three facts arriving a different way. This
    is what lets the report be computed from a database that has never seen a
    webhook, which is the state a fresh deployment is in.

    Only the three asset-relevant actions are projected; ``commented``,
    ``opened_link`` and ``completed_section`` are real rows in the same
    collection and are not asset occurrences. Every projected row is keyed on
    ``activity:<record id>``, so running this twice writes nothing the second
    time and the caller is told how many rows were already there.
    """
    rows = scan(store, ACTIVITY_COLLECTION, room_id=room_id, limit=limit)
    created = 0
    duplicates = 0
    skipped: dict[str, int] = {}

    for row in rows.records:
        data = row.get("data") or {}
        action = normalize_action(data.get("action"))
        if action is None:
            key = str(data.get("action") or "missing")
            skipped[key] = skipped.get(key, 0) + 1
            continue
        target = data.get("target_id") or data.get("asset_id") or data.get("target")
        if not target:
            skipped["no_target"] = skipped.get("no_target", 0) + 1
            continue
        event_id = f"activity:{row['id']}"
        if _existing_event_id(store, event_id) is not None:
            duplicates += 1
            continue
        payload = {
            "event_id": event_id,
            "action": action,
            "asset_id": data.get("target_id") or data.get("asset_id"),
            "asset": target if not data.get("target_id") and not data.get("asset_id") else None,
            "person": data.get("person"),
            "account": data.get("account"),
            "at": data.get("occurred_at") or row.get("created_at"),
            "seconds": data.get("seconds_on_page"),
        }
        try:
            record_event(
                store,
                payload,
                room_id=row.get("room_id"),
                actor=actor or "system",
                source=source,
            )
        except (UnknownAsset, InfluenceError):
            # An activity row naming a document that is not in the library is a
            # data problem for whoever wrote it, not a reason to abandon the
            # other 199 rows. It is counted and reported.
            skipped["unmatched_target"] = skipped.get("unmatched_target", 0) + 1
            continue
        created += 1

    return {
        "scanned": len(rows.records),
        "created": created,
        "already_recorded": duplicates,
        "skipped": skipped,
        "truncated": rows.truncated,
    }


def load_events(
    store: RecordStore,
    *,
    room_id: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Every recorded occurrence, or ``(rows, truncated)``.

    The envelope's ``room_id`` is merged onto the payload, because the store
    keeps it there and nowhere else: every consumer of an event - the report, the
    trend, the revenue evidence - needs to know which workspace it happened in,
    and reading it from the envelope here means none of them has to know that
    reserved keys are stripped out of ``data``.

    Rows missing an ``at``, an ``asset_id`` or a recognisable ``action`` are
    dropped here rather than defaulted. A row with no timestamp cannot be placed
    on the researched chart, and one with no recognisable action cannot be
    attributed to a metric - and the alternative is worse than dropping it,
    because every reader downstream treats "not a share and not a download" as
    a view, so an unrecognised row would quietly become a client view and move
    the one number the report is read for.

    The action is *normalised* on the way out, not just on the way in, so a row
    written by anything other than :func:`record_event` still reads correctly.
    """
    rows = scan(store, EVENT_COLLECTION, room_id=room_id, limit=limit)
    events: list[dict[str, Any]] = []
    for record in rows.records:
        data = record.get("data") or {}
        if not data.get("asset_id"):
            continue
        action = normalize_action(data.get("action"))
        if action is None:
            continue
        at = parse_time(data.get("at"))
        if at is None:
            continue
        events.append(
            {
                **data,
                "action": action,
                "id": record["id"],
                "room_id": record.get("room_id"),
                "_at": at,
            }
        )
    return events, rows.truncated


def summarise_vocabulary() -> dict[str, Any]:
    """The published event vocabulary, served so a client renders from it."""
    return {
        "actions": known_actions(),
        "audiences": list(AUDIENCES),
        "projectable_actions": sorted(_PROJECTABLE),
    }
