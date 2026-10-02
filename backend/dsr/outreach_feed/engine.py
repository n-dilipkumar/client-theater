"""Write DSR events into the seller activity feed (WF-026).

The researched flow, in four steps
----------------------------------
1. Create an app in the Outreach developer portal and add the *Activity feed
   custom events* feature.
2. Configure one or more custom events: an event name and a template string, with
   ``{{prospect}}`` supported.
3. On each qualifying DSR event, ``POST https://api.outreach.io/api/v2/events``
   with an S2S token, the configured name, an ``externalUrl`` deep link back into
   the DSR, an optional ``body``, and a ``prospect`` relationship.
4. Reps see the event card in the prospect activity feed; the card links back to
   the DSR.

This module is steps 2 and 3 made operable, plus the inbound half the research's
``automations`` field describes. Step 1 happens in Outreach's portal and cannot
be done from here, so what this build can do is record the app, validate a
configured event name against that app, and refuse to write anything for an
installation that has not finished step 1.

The data flow
-------------
DSR engagement event -> mapped to a configured custom event name -> JSON:API
``event`` create with a ``prospect`` relationship -> Outreach prospect activity
feed -> seller sees a chronological, clickable trail of DSR intent.

Every hop of that is a decision this module records, and the one that matters
most is the middle: *which* configured event a given DSR action maps to. It is
configuration, not code, because "if you are aware of interesting events" is a
judgement the seller makes and the research's step 2 is where they make it.

Falling through is the point
---------------------------
A build brief for this programme warns about rules that do not fall through, and
this workflow has three places where it would be easy not to:

* a room with no prospect link - the events are **skipped with a reason and
  stored**, never dropped, and re-evaluated on the next run, so linking the
  prospect and running again sends what was blocked;
* a DSR action no configured event matches - **counted per action name** and
  reported in the ledger and in the room feed, because "the seller has not
  configured that yet" and "the seller configured it and it is broken" are very
  different things to read as silence;
* an inbound webhook carrying a resource outside the documented family -
  **stored as ignored and still acknowledged 2xx**, because the sourced
  no-retry guarantee means a refusal is permanent data loss rather than a nudge.

Schema flexibility
------------------
Nothing here declares a schema. Apps, event types, prospect links, deliveries and
signals are arbitrary JSON in ``records.data``; the only fixed vocabulary is the
envelope. The DSR event stream's field *locations* are discovered through synonym
lists, overridable per app, rather than assumed. There is no migration and no
typed column, so a team adding a field needs no coordination with anyone.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.outreach_feed import webhooks
from dsr.outreach_feed.delivery import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    Transport,
    UrllibTransport,
    post_json,
)
from dsr.outreach_feed.errors import (
    AppError,
    EventTypeError,
    FeedError,
    FeedNotConfiguredError,
    ProspectLinkError,
    UnknownRoom,
)
from dsr.outreach_feed.vocabulary import (
    EVENT_FIELDS,
    EVENTS_ENDPOINT,
    SENDER_TIMEOUT_SECONDS,
    build_event_payload,
    require_absolute_url,
    require_event_name,
    require_localizations,
    require_template,
    room_deep_link,
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

APP_COLLECTION = "outreach_app"
EVENT_TYPE_COLLECTION = "outreach_event_type"
PROSPECT_COLLECTION = "outreach_prospect"
DELIVERY_COLLECTION = "outreach_delivery"
SIGNAL_COLLECTION = "outreach_signal"

COLLECTIONS: dict[str, str] = {
    "apps": APP_COLLECTION,
    "event_types": EVENT_TYPE_COLLECTION,
    "prospects": PROSPECT_COLLECTION,
    "deliveries": DELIVERY_COLLECTION,
    "signals": SIGNAL_COLLECTION,
}

#: ``AuditedDatabase.list`` pages with LIMIT/OFFSET, so scanning in pages of this
#: size keeps aggregates correct well past the per-call cap.
_PAGE = 1000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# --------------------------------------------------------------------------- #
# Delivery outcomes
# --------------------------------------------------------------------------- #

STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

DELIVERY_STATUSES = (STATUS_DELIVERED, STATUS_FAILED, STATUS_SKIPPED)

#: Why a qualifying DSR event was not written to the seller's feed. Every value is
#: something a person can act on, which is the whole point: a skip with a reason
#: is a task, and a skip without one is a shrug.
SKIP_REASONS: dict[str, str] = {
    "no_prospect": "the room is not linked to an Outreach prospect",
    "no_room_base_url": "no app carries a room base URL and the link carries no external_url",
    "app_not_ready": "the app this event is scoped to has no S2S token",
    "app_disabled": "the app this event is scoped to is disabled",
    "no_event_type": "the configured event type no longer exists",
    "bad_link": "the prospect link cannot produce a valid deep link",
}

#: Blockers, for the room's readiness view. A different vocabulary from skip
#: reasons on purpose: these describe the *installation*, those describe *one
#: event*.
BLOCKER_CODES: tuple[str, ...] = (
    "no_prospect_link",
    "no_event_type",
    "app_not_ready",
    "app_disabled",
    "no_room_base_url",
)


# --------------------------------------------------------------------------- #
# Scalar helpers
# --------------------------------------------------------------------------- #


def _as_text(value: Any, default: str = "") -> str:
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on")
    return default


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [_as_text(item) for item in value if _as_text(item)]
    return [_as_text(value)]


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; date-only values are treated as UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _scan(store: Any, collection: str, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every live record in a collection, paged so nothing is truncated."""
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(
            collection,
            room_id=room_id,
            limit=_PAGE,
            offset=offset,
            order_by="created_at",
            descending=False,
        )
        records.extend(page)
        if len(page) < _PAGE:
            return records
        offset += _PAGE


def _identifier_of(record: Mapping[str, Any], key: str) -> str:
    return _as_text((record.get("data") or {}).get(key))


# --------------------------------------------------------------------------- #
# The event stream, read through discovered field locations
# --------------------------------------------------------------------------- #


def normalise_stream_event(record: Mapping[str, Any], fields: Mapping[str, Any]) -> dict[str, Any]:
    """One DSR engagement event, whatever the team calls its fields.

    The store declares no schema, so each concept is looked up through a list of
    candidate paths. An action that resolves to nothing is kept as ``""`` rather
    than dropped: the publisher's ledger reports unmapped actions, and an event it
    could not even read is exactly the thing a person needs to see.
    """
    data = record.get("data") or {}
    if not isinstance(data, Mapping):
        data = {}

    def pick(concept: str) -> str:
        for key in _as_list(fields.get(concept)) or EVENT_FIELDS.get(concept, []):
            if (
                key in data
                and data[key] not in (None, "")
                and not isinstance(data[key], (dict, list))
            ):
                return _as_text(data[key])
        return ""

    occurred = _parse_dt(pick("occurred_at")) or _parse_dt(record.get("created_at"))
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "action": pick("action"),
        "person": pick("person") or "anonymous",
        "target": pick("target"),
        "occurred_at": occurred.isoformat(timespec="seconds") if occurred else None,
        "created_at": record.get("created_at"),
    }


# --------------------------------------------------------------------------- #
# Summaries for reads
# --------------------------------------------------------------------------- #


def app_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """An app as a read returns it: never the S2S token, never the secret.

    The token *is* the ``Authorization`` header, and the delivery log's recorded
    headers are readable by anyone who can read a delivery row. So a read answers
    "is there a token, and what does it end with" - never the token.
    """
    data = record.get("data") or {}
    token = _as_text(data.get("token"))
    secret = _as_text(data.get("webhook_secret"))
    return {
        "id": record.get("id"),
        "app_identifier": _as_text(data.get("app_identifier")),
        "enabled": _as_bool(data.get("enabled")),
        "room_base_url": _as_text(data.get("room_base_url")),
        "has_token": bool(token),
        "token_hint": f"***{token[-4:]}" if token else "",
        "has_webhook_secret": bool(secret),
        "event_type_ids": list(data.get("event_type_ids") or []),
        "field_map": dict(data.get("field_map") or {}),
        "environment": _as_text(data.get("environment")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def event_type_summary(
    record: Mapping[str, Any], *, apps: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """A configured custom event, plus a preview of the card it produces.

    The card is rendered by Outreach from the portal-side template plus the body we
    send, so ``card_preview`` is this build's half of it and says so.
    """
    data = record.get("data") or {}
    app_id = _as_text(data.get("app_id"))
    app = (apps or {}).get(app_id) or {}
    return {
        "id": record.get("id"),
        "app_id": app_id,
        "app_identifier": _identifier_of(app, "app_identifier")
        or _as_text(data.get("app_identifier")),
        "name": _as_text(data.get("name")),
        "event_id": _as_text(data.get("event_id")),
        "template": _as_text(data.get("template")),
        "localizations": dict(data.get("localizations") or {}),
        "body": _as_text(data.get("body")),
        "actions": _as_list(data.get("actions")),
        "documents": _as_list(data.get("documents")),
        "enabled": _as_bool(data.get("enabled")),
        "configured_in_portal": _as_bool(data.get("configured_in_portal")),
        "card_preview": {
            "template": _as_text(data.get("template")),
            "body": _as_text(data.get("body")),
            "note": (
                "The template is configured in the Outreach developer portal and is never "
                "sent. Outreach renders the card from it, replacing {{prospect}} with a "
                "link to the prospect."
            ),
        },
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def prospect_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "prospect_id": _as_text(data.get("prospect_id")),
        "opportunity_id": _as_text(data.get("opportunity_id")),
        "account_id": _as_text(data.get("account_id")),
        "label": _as_text(data.get("label")),
        "external_url": _as_text(data.get("external_url")),
        "active": _as_bool(data.get("active")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def delivery_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "event_key": _as_text(data.get("event_key")),
        "event_name": _as_text(data.get("event_name")),
        "event_type_id": _as_text(data.get("event_type_id")),
        "app_id": _as_text(data.get("app_id")),
        "prospect_id": _as_text(data.get("prospect_id")),
        "status": _as_text(data.get("status")),
        "skip_reason": _as_text(data.get("skip_reason")),
        "skip_detail": _as_text(data.get("skip_detail")),
        "external_url": _as_text(data.get("external_url")),
        "body": _as_text(data.get("body")),
        "attempts": int(data.get("attempts") or 0),
        "http_status": data.get("http_status"),
        "error": data.get("error"),
        "needs_manual_update": _as_bool(data.get("needs_manual_update"), default=False),
        "source_event_id": _as_text(data.get("source_event_id")),
        "source_action": _as_text(data.get("source_action")),
        "occurred_at": data.get("occurred_at"),
        "published_at": data.get("published_at"),
        "runs": int(data.get("runs") or 0),
        "payload": data.get("payload"),
        "attempt_log": list(data.get("attempt_log") or []),
        "attempt_statuses": list(data.get("attempt_statuses") or []),
        "request_headers": dict(data.get("request_headers") or {}),
        "response": data.get("response"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def signal_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "signal_key": _as_text(data.get("signal_key")),
        "resource": _as_text(data.get("resource")),
        "type": _as_text(data.get("type")),
        "intent": _as_bool(data.get("intent"), default=False),
        "status": _as_text(data.get("status")),
        "ignore_reason": _as_text(data.get("ignore_reason")),
        "link_status": _as_text(data.get("link_status")),
        "prospect_id": _as_text(data.get("prospect_id")),
        "mailing_id": _as_text(data.get("mailing_id")),
        "sequence": data.get("sequence"),
        "occurred_at": data.get("occurred_at"),
        "payload_version": data.get("payload_version"),
        "before_update": data.get("before_update"),
        "signature_verified": _as_bool(data.get("signature_verified"), default=False),
        "attributes": dict(data.get("attributes") or {}),
        "payload": data.get("payload"),
        "created_at": record.get("created_at"),
    }


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def matches(type_data: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """Whether one configured custom event claims one DSR engagement event.

    Two filters, both over the event stream as the research describes it:
    ``actions`` claims particular buyer actions, and ``documents`` narrows to
    particular targets. An empty filter matches everything, which is what makes a
    newly configured event type useful before anyone has tuned it.

    Deliberately no thresholds. "If you are aware of interesting events" is a
    judgement, and the research says the seller makes it by *configuring* an event -
    not by a duration the platform guesses at.
    """
    actions = {item.lower() for item in _as_list(type_data.get("actions"))}
    if actions and str(event.get("action") or "").lower() not in actions:
        return False
    documents = {item.lower() for item in _as_list(type_data.get("documents"))}
    if documents and str(event.get("target") or "").lower() not in documents:
        return False
    return True


def delivery_key(room_id: str, source_event_id: str, event_name: str, prospect_id: str = "") -> str:
    """One delivery's identity: the room, the DSR event, the name, the prospect.

    The prospect is part of it because a room may be linked to more than one buyer
    contact, and the researched write is per-prospect: the same DSR event reaching
    two prospects is two cards, not one.

    Readable on purpose. This is the field an operator queries with ``?where=`` when
    they want to know whether one specific thing was sent, and a hash would make
    that question unanswerable without recomputing it.
    """
    parts = [str(room_id), str(source_event_id), str(event_name), str(prospect_id)]
    return "|".join(parts)


def card_text(event_name: Any, body: Any) -> str:
    """The one-line preview of the card, from the two halves this build controls."""
    parts = [str(event_name or ""), str(body or "")]
    return " · ".join(part for part in parts if part)


#: How far along the log a row is, for the room feed's card list. A card the seller
#: would see only exists once the write was attempted, so a ``delivered`` row
#: supersedes a ``skipped`` one for the same DSR event and configured name - which
#: is what happens when a room that had no prospect link gets one and is published
#: again. The skipped row stays in the log; it is history, and the audit trail
#: keeps it either way.
_CARD_RANK = {STATUS_DELIVERED: 3, STATUS_FAILED: 2, STATUS_SKIPPED: 1}


def _collapse_superseded(deliveries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One card per (DSR event, configured name), with the rest named on it."""
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for delivery in deliveries:
        grouped.setdefault(
            (str(delivery.get("source_event_id")), str(delivery.get("event_name"))), []
        ).append(delivery)

    collapsed: list[dict[str, Any]] = []
    for rows in grouped.values():
        ordered = sorted(
            rows,
            key=lambda row: (
                -_CARD_RANK.get(str(row.get("status")), 0),
                -int(row.get("runs") or 0),
                str(row.get("updated_at") or ""),
            ),
        )
        head = dict(ordered[0])
        head["superseded"] = [row["id"] for row in ordered[1:]]
        collapsed.append(head)
    return collapsed


# --------------------------------------------------------------------------- #
# The publisher
# --------------------------------------------------------------------------- #


class FeedPublisher:
    """Everything this workflow does, over one audited store.

    Built per request from :class:`~dsr.store.RecordStore` rather than parked on
    ``app.state``, for the same reason every other feature does it that way: the
    engine holds nothing but the store handle and its transport, so constructing it
    per request is equivalent and leaves the transport a dependency a test can
    override.
    """

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        timeout: float = SENDER_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff: float = DEFAULT_BACKOFF,
        sleep: Callable[[float], None] = time.sleep,
        event_stream_collection: str = "activity",
    ) -> None:
        self.store = store
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.sleep = sleep
        self.event_stream_collection = event_stream_collection

    # -- reads --------------------------------------------------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(str(room_id))
        if record is None or record.get("collection") != "room":
            raise UnknownRoom(str(room_id))
        return record

    def _apps(self) -> list[dict[str, Any]]:
        return _scan(self.store, APP_COLLECTION)

    def _apps_by_id(self) -> dict[str, dict[str, Any]]:
        return {record["id"]: record for record in self._apps()}

    def _enabled_types(self) -> list[dict[str, Any]]:
        return [
            record
            for record in _scan(self.store, EVENT_TYPE_COLLECTION)
            if _as_bool((record.get("data") or {}).get("enabled"))
        ]

    def field_map(self, app: Mapping[str, Any] | None) -> dict[str, Any]:
        """The event-stream field locations this app reads, defaults merged in.

        A team that calls its buyer actions something else overrides ``action``
        (and only ``action``) on its own app record. No migration, no code change,
        no coordination with anyone.
        """
        data = (app or {}).get("data") or {}
        override = data.get("field_map")
        merged = {key: list(values) for key, values in EVENT_FIELDS.items()}
        if isinstance(override, Mapping):
            for concept, paths in override.items():
                listed = _as_list(paths)
                if listed:
                    merged[str(concept)] = listed
        return merged

    def _room_field_map(self, room_id: str) -> dict[str, Any]:
        """Field locations for a room, taken from the app its enabled events use."""
        apps = self._apps_by_id()
        for record in self._enabled_types():
            app = apps.get(_identifier_of(record, "app_id"))
            if app is not None:
                return self.field_map(app)
        return self.field_map(None)

    def list_apps(self) -> list[dict[str, Any]]:
        """Registered Outreach apps. The S2S token and the webhook secret are not in here."""
        return [app_summary(record) for record in self._apps()]

    def list_event_types(self) -> list[dict[str, Any]]:
        apps = self._apps_by_id()
        return [
            event_type_summary(record, apps=apps)
            for record in _scan(self.store, EVENT_TYPE_COLLECTION)
        ]

    def get_event_type(self, event_type_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(event_type_id))
        if record is None or record.get("collection") != EVENT_TYPE_COLLECTION:
            return None
        return event_type_summary(record, apps=self._apps_by_id())

    def list_prospects(self, room_id: str) -> list[dict[str, Any]]:
        self.require_room(room_id)
        return [
            prospect_summary(record)
            for record in _scan(self.store, PROSPECT_COLLECTION, room_id=room_id)
        ]

    def deliveries(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        event_name: str | None = None,
        needs_manual_update: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The outbound log, newest first.

        Every filter is a JSON path in each row's own payload, resolved through the
        dynamic index, so a new status or a new event name needs no change here. A
        room filter is applied on the envelope, which the dynamic index does not
        cover, so it is applied after the fetch rather than smuggled into ``where``.
        """
        where: dict[str, Any] = {}
        if status:
            where["status"] = status
        if event_name:
            where["event_name"] = event_name
        if needs_manual_update is not None:
            where["needs_manual_update"] = needs_manual_update
        cap = max(1, min(int(limit), 1000))
        records = self.store.find(DELIVERY_COLLECTION, where, limit=cap)
        rows = [record for record in records if room_id in (None, record.get("room_id"))]
        rows.sort(key=lambda record: str(record.get("updated_at") or ""), reverse=True)
        return [delivery_summary(record) for record in rows[:cap]]

    def signals(
        self,
        *,
        room_id: str | None = None,
        signal_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Intent that came back out of Outreach, newest first."""
        where: dict[str, Any] = {}
        if signal_type:
            where["type"] = signal_type
        if status:
            where["status"] = status
        cap = max(1, min(int(limit), 1000))
        records = self.store.find(SIGNAL_COLLECTION, where, limit=cap)
        rows = [record for record in records if room_id in (None, record.get("room_id"))]
        rows.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
        return [signal_summary(record) for record in rows[:cap]]

    def readiness(self, room_id: str) -> dict[str, Any]:
        """Can this room write anything to the seller's feed, and if not, why not.

        The point of the view is that a seller who has configured nothing is told so
        specifically, rather than discovering it by watching a feed stay empty.
        """
        self.require_room(room_id)
        links = [
            prospect_summary(record)
            for record in _scan(self.store, PROSPECT_COLLECTION, room_id=room_id)
        ]
        active_links = [link for link in links if link["active"] and link["prospect_id"]]
        apps = self._apps_by_id()
        summaries = self.list_event_types()
        enabled = [entry for entry in summaries if entry["enabled"]]

        blockers: list[dict[str, str]] = []
        if not active_links:
            blockers.append(
                {
                    "code": "no_prospect_link",
                    "detail": (
                        "this room is not linked to an Outreach prospect, so no event can "
                        "carry the prospect relationship the write requires"
                    ),
                }
            )
        if not enabled:
            blockers.append(
                {
                    "code": "no_event_type",
                    "detail": "no custom event is enabled, so no DSR event qualifies",
                }
            )
        for entry in enabled:
            app = apps.get(entry["app_id"])
            if app is None:
                blockers.append(
                    {
                        "code": "app_not_ready",
                        "detail": f"custom event {entry['name']!r} is scoped to an app that is not registered",
                    }
                )
                continue
            identifier = _identifier_of(app, "app_identifier")
            if not _identifier_of(app, "token"):
                blockers.append(
                    {"code": "app_not_ready", "detail": f"app {identifier!r} has no S2S token"}
                )
            if not _as_bool((app.get("data") or {}).get("enabled")):
                blockers.append(
                    {"code": "app_disabled", "detail": f"app {identifier!r} is disabled"}
                )
            if not _identifier_of(app, "room_base_url") and not any(
                link["external_url"] for link in active_links
            ):
                blockers.append(
                    {
                        "code": "no_room_base_url",
                        "detail": (
                            f"app {identifier!r} has no room_base_url and no link carries an "
                            "external_url, so the card would have no link back to the DSR"
                        ),
                    }
                )
        return {
            "room_id": str(room_id),
            "ready": not blockers,
            "blockers": blockers,
            "blocker_codes": sorted({blocker["code"] for blocker in blockers}),
            "apps": [app_summary(record) for record in self._apps()],
            "event_types": summaries,
            "prospects": links,
        }

    def unmapped_actions(self, room_id: str) -> list[dict[str, Any]]:
        """Buyer actions in this room's stream that no enabled custom event claims.

        The fall-through made visible. One entry per action name with a count and a
        couple of examples, so "the seller has not configured comments yet" is
        readable without a delivery row per comment.
        """
        self.require_room(room_id)
        enabled = [(record.get("data") or {}) for record in self._enabled_types()]
        fields = self._room_field_map(room_id)
        grouped: dict[str, dict[str, Any]] = {}
        for record in _scan(self.store, self.event_stream_collection, room_id=room_id):
            event = normalise_stream_event(record, fields)
            if any(matches(data, event) for data in enabled):
                continue
            label = str(event.get("action") or "") or "(unreadable)"
            row = grouped.setdefault(label, {"action": label, "count": 0, "example_events": []})
            row["count"] += 1
            if len(row["example_events"]) < 3:
                row["example_events"].append(event["id"])
        return sorted(grouped.values(), key=lambda row: (-row["count"], row["action"]))

    def room_feed(self, room_id: str, *, limit: int = 50) -> dict[str, Any]:
        """What a rep's activity feed would show for this room, and how it got there.

        The chronological, clickable trail the research describes, assembled from
        the delivery rows: the DSR event behind each card, the configured name the
        rep sees, the body that goes with it, and the link back into the DSR.
        """
        room = self.require_room(room_id)
        readiness = self.readiness(room_id)
        deliveries = self.deliveries(room_id=room_id, limit=max(1, min(int(limit), 500)))
        fields = self._room_field_map(room_id)
        by_event = {
            record["id"]: normalise_stream_event(record, fields)
            for record in _scan(self.store, self.event_stream_collection, room_id=room_id)
        }

        cards: list[dict[str, Any]] = []
        for delivery in _collapse_superseded(deliveries):
            origin = by_event.get(delivery["source_event_id"]) or {}
            cards.append(
                {
                    "delivery_id": delivery["id"],
                    "event_key": delivery["event_key"],
                    "event_name": delivery["event_name"],
                    "status": delivery["status"],
                    "skip_reason": delivery["skip_reason"],
                    "skip_detail": delivery["skip_detail"],
                    "superseded": delivery.get("superseded", []),
                    "needs_manual_update": delivery["needs_manual_update"],
                    "attempts": delivery["attempts"],
                    "external_url": delivery["external_url"],
                    "body": delivery["body"],
                    "card_text": card_text(delivery["event_name"], delivery["body"]),
                    "prospect_id": delivery["prospect_id"],
                    "person": origin.get("person"),
                    "action": origin.get("action") or delivery["source_action"],
                    "target": origin.get("target"),
                    "occurred_at": delivery["occurred_at"] or origin.get("occurred_at"),
                    "published_at": delivery["published_at"],
                }
            )
        cards.sort(key=lambda card: str(card.get("occurred_at") or ""), reverse=True)

        counts = {status: 0 for status in DELIVERY_STATUSES}
        for delivery in deliveries:
            counts[delivery["status"]] = counts.get(delivery["status"], 0) + 1

        return {
            "room": {
                "id": room["id"],
                "name": _as_text((room.get("data") or {}).get("name"), room["id"]),
            },
            "ready": readiness["ready"],
            "blockers": readiness["blockers"],
            "prospects": readiness["prospects"],
            "event_types": readiness["event_types"],
            "cards": cards,
            "counts": counts,
            "needs_manual_update": counts.get(STATUS_FAILED, 0),
            "unmapped_actions": self.unmapped_actions(room_id),
            "signals": self.signals(room_id=room_id, limit=20),
        }

    # -- writes: apps --------------------------------------------------------- #

    def register_app(
        self, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Register the Outreach app the custom events are configured under.

        [sourced] Step 1 of the flow: an app in the developer portal with the
        *Activity feed custom events* feature added. This records the identity and
        the credentials the write needs; the portal configuration itself happens in
        Outreach and is tracked per event type as ``configured_in_portal``.
        """
        identifier = _as_text(payload.get("app_identifier") or payload.get("appIdentifier"))
        if not identifier:
            raise AppError("app_identifier is required: event names are scoped to it")
        token = _as_text(payload.get("token") or payload.get("s2s_token"))
        if not token:
            raise AppError("token is required: the write is authorized with 'Bearer S2S_TOKEN'")
        base_url = _as_text(payload.get("room_base_url") or payload.get("roomBaseUrl"))
        if base_url:
            base_url = require_absolute_url(base_url, field="room_base_url")

        for record in self._apps():
            if _identifier_of(record, "app_identifier") == identifier:
                raise AppError(
                    f"app identifier {identifier!r} is already registered as {record['id']}; "
                    "patch that app instead of registering it twice"
                )

        data: dict[str, Any] = {
            "app_identifier": identifier,
            "token": token,
            "webhook_secret": _as_text(
                payload.get("webhook_secret") or payload.get("webhookSecret")
            ),
            "room_base_url": base_url,
            "enabled": _as_bool(payload.get("enabled")),
            "environment": _as_text(payload.get("environment")),
        }
        override = payload.get("field_map")
        if isinstance(override, Mapping) and override:
            data["field_map"] = {str(k): _as_list(v) for k, v in override.items() if _as_list(v)}
        return app_summary(self.store.create(APP_COLLECTION, data, actor=actor, source=source))

    def update_app(
        self, app_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Patch an app: rotate the token, flip the toggle, set the base URL.

        A patch never has to name the token, so rotating it does not re-send it and
        a read never has to return it.
        """
        record = self.store.get(str(app_id))
        if record is None or record.get("collection") != APP_COLLECTION:
            raise AppError(f"app {app_id} not found")
        current = dict(record.get("data") or {})
        payload: dict[str, Any] = {}

        if "app_identifier" in patch or "appIdentifier" in patch:
            identifier = _as_text(patch.get("app_identifier") or patch.get("appIdentifier"))
            if not identifier:
                raise AppError("app_identifier cannot be blank")
            payload["app_identifier"] = identifier
        if "token" in patch or "s2s_token" in patch:
            token = _as_text(patch.get("token") or patch.get("s2s_token"))
            if not token:
                raise AppError(
                    "token cannot be blank; send enabled=false to switch an app off instead"
                )
            payload["token"] = token
        if "webhook_secret" in patch or "webhookSecret" in patch:
            payload["webhook_secret"] = _as_text(
                patch.get("webhook_secret") or patch.get("webhookSecret")
            )
        if "room_base_url" in patch or "roomBaseUrl" in patch:
            base_url = _as_text(patch.get("room_base_url") or patch.get("roomBaseUrl"))
            payload["room_base_url"] = (
                require_absolute_url(base_url, field="room_base_url") if base_url else ""
            )
        if "enabled" in patch:
            payload["enabled"] = _as_bool(patch.get("enabled"))
        if "environment" in patch:
            payload["environment"] = _as_text(patch.get("environment"))
        override = patch.get("field_map")
        if isinstance(override, Mapping):
            payload["field_map"] = {str(k): _as_list(v) for k, v in override.items() if _as_list(v)}

        merged = _deep_merge(current, payload)
        return app_summary(self.store.update(record["id"], merged, actor=actor, source=source))

    def delete_app(self, app_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete an app. The delivery log outlives it, and stays readable."""
        record = self.store.get(str(app_id))
        if record is None or record.get("collection") != APP_COLLECTION:
            raise AppError(f"app {app_id} not found")
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- writes: custom events ------------------------------------------------ #

    def _app_identifiers(self, apps: Mapping[str, Any]) -> list[str]:
        return [_identifier_of(record, "app_identifier") for record in apps.values()]

    def create_event_type(
        self, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Declare one custom event: the researched name, template and body.

        [sourced] Step 2: "configure one or more custom events (event name +
        template string; ``{{prospect}}`` placeholder supported)". The name has to
        be scoped to an app that exists here, because Outreach matches it against
        the app whose custom event was configured in the portal.
        """
        apps = self._apps_by_id()
        if not apps:
            raise FeedNotConfiguredError(
                "no Outreach app is registered; register one before configuring a custom event"
            )
        name = require_event_name(payload.get("name"), app_identifiers=self._app_identifiers(apps))
        identifier, event_id = name.split(":", 1)

        app_id = _as_text(payload.get("app_id"))
        if not app_id:
            app_id = next(
                record_id
                for record_id, record in apps.items()
                if _identifier_of(record, "app_identifier") == identifier
            )
        elif app_id not in apps:
            raise EventTypeError(f"app {app_id} is not registered")
        elif _identifier_of(apps[app_id], "app_identifier") != identifier:
            raise EventTypeError(
                f"event name {name!r} is scoped to app {identifier!r} but app_id {app_id} is "
                f"{_identifier_of(apps[app_id], 'app_identifier')!r}"
            )

        template = require_template(payload.get("template"), field="template")
        localizations = require_localizations(payload.get("localizations"))
        actions = _as_list(payload.get("actions"))
        if not actions:
            raise EventTypeError(
                "actions is required: a custom event claiming no buyer action would claim "
                "every event in the room, which is not what 'qualifying DSR event' means"
            )
        for record in _scan(self.store, EVENT_TYPE_COLLECTION):
            if _identifier_of(record, "name") == name:
                raise EventTypeError(
                    f"custom event {name!r} is already configured as {record['id']}; patch it instead"
                )

        data = {
            "app_id": app_id,
            "app_identifier": identifier,
            "name": name,
            "event_id": event_id,
            "template": template,
            "localizations": localizations,
            "body": _as_text(payload.get("body")),
            "actions": actions,
            "documents": _as_list(payload.get("documents")),
            "enabled": _as_bool(payload.get("enabled")),
            "configured_in_portal": _as_bool(payload.get("configured_in_portal")),
        }
        record = self.store.create(EVENT_TYPE_COLLECTION, data, actor=actor, source=source)
        return event_type_summary(record, apps=apps)

    def update_event_type(
        self, event_type_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Patch a custom event, including flipping its on/off toggle.

        The toggle lives on the event type, not beside it, because the research ties
        the two together: Outreach renders the card from the portal-side
        configuration for *this* event, so "off" is a property of the event and a
        separate flag could drift out of step with it.
        """
        record = self.store.get(str(event_type_id))
        if record is None or record.get("collection") != EVENT_TYPE_COLLECTION:
            raise EventTypeError(f"custom event {event_type_id} not found")
        apps = self._apps_by_id()
        current = dict(record.get("data") or {})
        payload: dict[str, Any] = {}

        if "name" in patch:
            name = require_event_name(
                patch.get("name"), app_identifiers=self._app_identifiers(apps)
            )
            identifier, event_id = name.split(":", 1)
            payload.update({"name": name, "event_id": event_id, "app_identifier": identifier})
        if "template" in patch:
            payload["template"] = require_template(patch.get("template"), field="template")
        if "localizations" in patch:
            payload["localizations"] = require_localizations(patch.get("localizations"))
        if "actions" in patch:
            actions = _as_list(patch.get("actions"))
            if not actions:
                raise EventTypeError(
                    "actions cannot be emptied; a custom event claiming nothing never qualifies"
                )
            payload["actions"] = actions
        if "documents" in patch:
            payload["documents"] = _as_list(patch.get("documents"))
        if "body" in patch:
            payload["body"] = _as_text(patch.get("body"))
        if "enabled" in patch:
            payload["enabled"] = _as_bool(patch.get("enabled"))
        if "configured_in_portal" in patch:
            payload["configured_in_portal"] = _as_bool(patch.get("configured_in_portal"))
        if "app_id" in patch:
            app_id = _as_text(patch.get("app_id"))
            if app_id not in apps:
                raise EventTypeError(f"app {app_id} is not registered")
            payload["app_id"] = app_id

        merged = _deep_merge(current, payload)
        return event_type_summary(
            self.store.update(record["id"], merged, actor=actor, source=source), apps=apps
        )

    def delete_event_type(
        self, event_type_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete a custom event. The delivery log outlives it."""
        record = self.store.get(str(event_type_id))
        if record is None or record.get("collection") != EVENT_TYPE_COLLECTION:
            raise EventTypeError(f"custom event {event_type_id} not found")
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- writes: prospect links ----------------------------------------------- #

    def link_prospect(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Link a room to the Outreach prospect the activity feed belongs to.

        Every event write carries a ``prospect`` relationship, so without this link
        nothing can be sent. [sourced] The data source is the prospect object "and
        the account/opportunity behind it", so the opportunity and account ids are
        kept beside it as context.
        """
        self.require_room(room_id)
        prospect_id = _as_text(payload.get("prospect_id") or payload.get("prospectId"))
        if not prospect_id:
            raise ProspectLinkError(
                "prospect_id is required: every event carries a prospect relationship"
            )
        external_url = _as_text(payload.get("external_url") or payload.get("externalUrl"))
        if external_url:
            external_url = require_absolute_url(external_url, field="external_url")

        for record in _scan(self.store, PROSPECT_COLLECTION, room_id=room_id):
            data = record.get("data") or {}
            if _as_text(data.get("prospect_id")) == prospect_id and _as_bool(data.get("active")):
                raise ProspectLinkError(
                    f"room {room_id} is already linked to prospect {prospect_id!r} as {record['id']}"
                )

        data = {
            "prospect_id": prospect_id,
            "opportunity_id": _as_text(
                payload.get("opportunity_id") or payload.get("opportunityId")
            ),
            "account_id": _as_text(payload.get("account_id") or payload.get("accountId")),
            "label": _as_text(payload.get("label")),
            "external_url": external_url,
            "active": _as_bool(payload.get("active")),
        }
        record = self.store.create(
            PROSPECT_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )
        return prospect_summary(record)

    def unlink_prospect(
        self, room_id: str, link_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete one link. Deliveries already made keep pointing at the prospect."""
        record = self.store.get(str(link_id))
        if record is None or record.get("collection") != PROSPECT_COLLECTION:
            raise ProspectLinkError(f"prospect link {link_id} not found")
        if str(record.get("room_id")) != str(room_id):
            raise ProspectLinkError(f"prospect link {link_id} is not on room {room_id}")
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- the automation -------------------------------------------------------- #

    def preview(self, room_id: str, *, limit: int = 200) -> dict[str, Any]:
        """What a publish run would do, and why - with nothing written.

        The same ledger as :meth:`publish`, so a seller can see the fall-through
        before it happens: which events would be sent, which would be blocked and on
        what ground, and which buyer actions nobody has configured a custom event for.
        """
        return self._run(room_id, dry_run=True, limit=limit, actor=None, source="preview")

    def publish(
        self,
        room_id: str,
        *,
        actor: str | None = None,
        source: str,
        limit: int = 200,
        only: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Write this room's qualifying DSR events into the seller's activity feed.

        [sourced] "On each qualifying DSR event, POST …" and "No user action on the
        Outreach side once configured - the feed entry appears as events arrive."
        This is that "arrive": a sweep over the room's event stream, one delivery per
        qualifying event, per configured custom event, per linked prospect.

        Safe to run repeatedly. The delivery key of (room, DSR event, name, prospect)
        makes a second run a no-op for anything already delivered, and a blocked
        event is re-evaluated rather than remembered as blocked - so linking the
        prospect and running again sends exactly what was waiting.
        """
        return self._run(room_id, dry_run=False, limit=limit, actor=actor, source=source, only=only)

    def _run(
        self,
        room_id: str,
        *,
        dry_run: bool,
        limit: int,
        actor: str | None,
        source: str,
        only: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        self.require_room(room_id)
        apps = self._apps_by_id()
        enabled = self._enabled_types()
        if only:
            wanted = {str(item) for item in only}
            enabled = [record for record in enabled if record["id"] in wanted]

        links = [
            prospect_summary(record)
            for record in _scan(self.store, PROSPECT_COLLECTION, room_id=room_id)
        ]
        active_links = [link for link in links if link["active"] and link["prospect_id"]]

        fields = self.field_map(self._app_for_apps(apps, enabled))
        events = [
            normalise_stream_event(record, fields)
            for record in _scan(self.store, self.event_stream_collection, room_id=room_id)
        ]
        events.sort(
            key=lambda event: (
                _parse_dt(event.get("occurred_at")) is None,
                _parse_dt(event.get("occurred_at")) or _EPOCH,
                str(event.get("id")),
            )
        )
        budget = max(1, min(int(limit), 1000))
        scanned = events[:budget]

        sent: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        duplicate: list[dict[str, Any]] = []
        unmapped: dict[str, dict[str, Any]] = {}
        touched: list[str] = []

        for event in scanned:
            claimants = [record for record in enabled if matches(record.get("data") or {}, event)]
            if not claimants:
                label = str(event.get("action") or "") or "(unreadable)"
                row = unmapped.setdefault(
                    label, {"action": label, "count": 0, "example_events": []}
                )
                row["count"] += 1
                if len(row["example_events"]) < 3:
                    row["example_events"].append(event["id"])
                continue

            for record in claimants:
                outcomes = self._deliver_event(
                    room_id=room_id,
                    event=event,
                    type_record=record,
                    apps=apps,
                    links=active_links,
                    dry_run=dry_run,
                    actor=actor,
                    source=source,
                )
                for outcome in outcomes:
                    bucket = {
                        "delivered": sent,
                        "failed": failed,
                        "duplicate": duplicate,
                    }.get(outcome["outcome"], skipped)
                    bucket.append(outcome)
                    if outcome.get("delivery_id") and not dry_run:
                        touched.append(outcome["delivery_id"])

        ledger: dict[str, Any] = {
            "room_id": str(room_id),
            "dry_run": dry_run,
            "scanned": len(scanned),
            "scanned_total": len(events),
            "event_types_considered": len(enabled),
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "duplicate": duplicate,
            "unmapped_actions": sorted(
                unmapped.values(), key=lambda row: (-row["count"], row["action"])
            ),
            "deliveries": touched,
            "counts": {
                "sent": len(sent),
                "failed": len(failed),
                "skipped": len(skipped),
                "duplicate": len(duplicate),
                "unmapped_events": sum(row["count"] for row in unmapped.values()),
                "unmapped_actions": len(unmapped),
            },
        }
        readiness = self.readiness(room_id)
        ledger["ready"] = readiness["ready"]
        ledger["blockers"] = readiness["blockers"]
        return ledger

    def _app_for_apps(
        self, apps: Mapping[str, Any], enabled: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any] | None:
        """The app whose field map applies, taken from the enabled custom events.

        The event stream's field locations come from the app those events are scoped
        to, because that is the app whose deployment shaped the stream. With no
        enabled event, the package defaults apply.
        """
        for record in enabled:
            app = apps.get(_identifier_of(record, "app_id"))
            if app is not None:
                return app
        return None

    def _deliver_event(
        self,
        *,
        room_id: str,
        event: Mapping[str, Any],
        type_record: Mapping[str, Any],
        apps: Mapping[str, Any],
        links: Sequence[Mapping[str, Any]],
        dry_run: bool,
        actor: str | None,
        source: str,
        retry_of: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """One (DSR event, configured custom event, prospect) set of decisions.

        Returns a list because a room may be linked to more than one prospect, and
        the researched write is per prospect: one card each.
        """
        type_data = type_record.get("data") or {}
        name = _as_text(type_data.get("name"))
        app_id = _as_text(type_data.get("app_id"))
        app = apps.get(app_id) or {}
        app_data = app.get("data") or {}
        body = _as_text(type_data.get("body"))

        base = {
            "event_name": name,
            "event_type_id": type_record.get("id"),
            "app_id": app_id,
            "source_event_id": event.get("id"),
            "source_action": event.get("action"),
            "source_person": event.get("person"),
            "source_target": event.get("target"),
            "occurred_at": event.get("occurred_at"),
            "published_at": _now_iso(),
            "body": body,
        }

        def resolve_row(prospect_id: str) -> tuple[str, Mapping[str, Any] | None]:
            """Which row does this decision belong to: its (event_key, record).

            Normally the key is computed and the row looked up by it, which is what
            makes a second publish of the same event a duplicate rather than a
            second card.

            But a manual retry names the row it is retrying, and that has to win.
            The key includes the prospect, and a row skipped for want of a prospect
            is keyed with an EMPTY one. Once the prospect is linked the recomputed
            key no longer matches the original, so the lookup misses and the retry
            writes a SECOND row - leaving the original stuck at ``skipped`` forever,
            which is precisely the thing ``retry_delivery`` exists to fix, and which
            its own docstring promises it does.

            So when a row is named, that is the row. It keeps its own key too, so
            the retry lands on the same card rather than forking the history.
            """
            key = delivery_key(room_id, str(event.get("id")), name, prospect_id)
            if retry_of is not None:
                return _as_text((retry_of.get("data") or {}).get("event_key")) or key, retry_of
            return key, self._find_delivery(key)

        def outcome_for(
            kind: str,
            *,
            key: str,
            prospect_id: str = "",
            reason: str = "",
            detail: str = "",
            existing: Mapping[str, Any] | None = None,
            extra: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            row = {
                "outcome": kind,
                "event_key": key,
                "event_name": name,
                "source_event_id": event.get("id"),
                "source_action": event.get("action"),
                "prospect_id": prospect_id,
                "delivery_id": (existing or {}).get("id"),
                "reason": reason,
                "detail": detail,
            }
            row.update(dict(extra or {}))
            return row

        def blocked(reason: str, detail: str, *, prospect_id: str = "") -> dict[str, Any]:
            key, existing = resolve_row(prospect_id)
            if dry_run:
                return outcome_for(
                    "skipped", key=key, prospect_id=prospect_id, reason=reason, detail=detail
                )
            record = self._upsert_delivery(
                existing,
                {
                    **base,
                    "event_key": key,
                    "prospect_id": prospect_id,
                    "status": STATUS_SKIPPED,
                    "skip_reason": reason,
                    "skip_detail": detail or SKIP_REASONS.get(reason, ""),
                    "needs_manual_update": False,
                    "attempts": 0,
                    "attempt_log": [],
                    "attempt_statuses": [],
                    "external_url": "",
                    "payload": None,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return outcome_for(
                "skipped",
                key=key,
                prospect_id=prospect_id,
                reason=reason,
                detail=detail,
                existing=record,
            )

        # -- gate 1: is there anywhere to send it at all? --------------------- #
        if not links:
            return [blocked("no_prospect", SKIP_REASONS["no_prospect"])]
        if not app:
            return [
                blocked(
                    "app_not_ready", f"the app {app_id!r} this event is scoped to is not registered"
                )
            ]
        if not _as_text(app_data.get("token")):
            return [blocked("app_not_ready", SKIP_REASONS["app_not_ready"])]
        if not _as_bool(app_data.get("enabled")):
            return [blocked("app_disabled", SKIP_REASONS["app_disabled"])]

        results: list[dict[str, Any]] = []
        for link in links:
            prospect_id = _as_text(link.get("prospect_id"))
            key, existing = resolve_row(prospect_id)
            if (
                existing is not None
                and _as_text((existing.get("data") or {}).get("status")) == STATUS_DELIVERED
            ):
                results.append(
                    outcome_for(
                        "duplicate",
                        key=key,
                        prospect_id=prospect_id,
                        detail="already delivered; a second publish will not resend it",
                        existing=existing,
                    )
                )
                continue

            external_url = _as_text(link.get("external_url"))
            if not external_url:
                base_url = _as_text(app_data.get("room_base_url"))
                if not base_url:
                    results.append(
                        blocked(
                            "no_room_base_url",
                            SKIP_REASONS["no_room_base_url"],
                            prospect_id=prospect_id,
                        )
                    )
                    continue
                try:
                    external_url = room_deep_link(base_url, room_id)
                except FeedError as exc:
                    # One unlinkable room must not stop the run; it is reported the
                    # same way as any other blocked event.
                    results.append(blocked("bad_link", str(exc), prospect_id=prospect_id))
                    continue

            try:
                payload = build_event_payload(
                    name=name, external_url=external_url, prospect_id=prospect_id, body=body or None
                )
            except FeedError as exc:
                results.append(blocked("bad_link", str(exc), prospect_id=prospect_id))
                continue

            if dry_run:
                results.append(
                    outcome_for(
                        "delivered",
                        key=key,
                        prospect_id=prospect_id,
                        extra={
                            "external_url": external_url,
                            "attempts": 1,
                            "http_status": None,
                            "needs_manual_update": False,
                            "payload": payload,
                        },
                    )
                )
                continue

            report = post_json(
                self.transport,
                EVENTS_ENDPOINT,
                payload,
                headers={"Authorization": f"Bearer {_as_text(app_data.get('token'))}"},
                timeout=self.timeout,
                max_attempts=self.max_attempts,
                backoff=self.backoff,
                sleep=self.sleep,
            )
            record = self._upsert_delivery(
                existing,
                {
                    **base,
                    **report.to_dict(),
                    "event_key": key,
                    "prospect_id": prospect_id,
                    "status": STATUS_DELIVERED if report.ok else STATUS_FAILED,
                    "skip_reason": "",
                    "skip_detail": "",
                    "external_url": external_url,
                    "payload": payload,
                    "needs_manual_update": report.needs_manual_update,
                },
                room_id=room_id,
                actor=actor,
                source=source,
                attempt_offset=len((existing or {}).get("data", {}).get("attempt_log") or []),
            )
            results.append(
                outcome_for(
                    "delivered" if report.ok else "failed",
                    key=key,
                    prospect_id=prospect_id,
                    existing=record,
                    extra={
                        "external_url": external_url,
                        "attempts": report.attempts,
                        "http_status": report.result.status,
                        "error": report.result.error,
                        "needs_manual_update": report.needs_manual_update,
                        "payload": payload,
                    },
                )
            )
        return results

    def _find_delivery(self, key: str) -> dict[str, Any] | None:
        matches = self.store.find(DELIVERY_COLLECTION, {"event_key": key}, limit=1)
        return matches[0] if matches else None

    def _upsert_delivery(
        self,
        existing: Mapping[str, Any] | None,
        data: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None,
        source: str,
        attempt_offset: int = 0,
    ) -> dict[str, Any]:
        """Create or update one delivery row, keeping earlier attempts visible.

        A retry appends to the attempt log rather than replacing it: the reason a
        second attempt happened is the first attempt, and a log that overwrote it
        would be a log of only the last word. ``runs`` counts them.
        """
        payload = dict(data)
        previous = list((existing or {}).get("data", {}).get("attempt_log") or [])
        payload["runs"] = int((existing or {}).get("data", {}).get("runs") or 0) + 1
        if attempt_offset or previous:
            fresh = [
                {**entry, "attempt": int(entry.get("attempt") or 0) + len(previous)}
                for entry in list(payload.get("attempt_log") or [])
            ]
            payload["attempt_log"] = previous + fresh
            payload["attempt_statuses"] = [entry.get("status") for entry in payload["attempt_log"]]
            payload["attempts"] = len(payload["attempt_log"])

        if existing is None:
            return self.store.create(
                DELIVERY_COLLECTION, payload, room_id=room_id, actor=actor, source=source
            )
        return self.store.update(existing["id"], payload, actor=actor, source=source)

    def retry_delivery(
        self, delivery_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Send one delivery again, by hand, and say what happened.

        The researched platform does not retry anything, so a row that is
        ``needs_manual_update`` waits for exactly this. It is also the way a
        ``skipped`` row is cleared: run it once the blocker is fixed and the same
        code path that publishes does the work.
        """
        record = self.store.get(str(delivery_id))
        if record is None or record.get("collection") != DELIVERY_COLLECTION:
            raise FeedError(f"delivery {delivery_id} not found")
        data = dict(record.get("data") or {})
        room_id = str(record.get("room_id"))
        type_record = self.store.get(_as_text(data.get("event_type_id")))
        if type_record is None or type_record.get("collection") != EVENT_TYPE_COLLECTION:
            # Recorded rather than raised: the custom event was retired, the card it
            # produced is still in the seller's feed, and the row should say why it
            # cannot be sent again.
            self._upsert_delivery(
                record,
                {
                    "status": STATUS_SKIPPED,
                    "skip_reason": "no_event_type",
                    "skip_detail": SKIP_REASONS["no_event_type"],
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {
                "outcome": "skipped",
                "delivery_id": record["id"],
                "event_key": _as_text(data.get("event_key")),
                "reason": "no_event_type",
                "detail": SKIP_REASONS["no_event_type"],
                "retried": True,
            }

        links = [
            prospect_summary(item)
            for item in _scan(self.store, PROSPECT_COLLECTION, room_id=room_id)
            if _as_bool((item.get("data") or {}).get("active"))
        ]
        prospect_id = _as_text(data.get("prospect_id"))
        if prospect_id:
            narrowed = [link for link in links if link["prospect_id"] == prospect_id]
            if narrowed:
                links = narrowed

        event = {
            "id": data.get("source_event_id"),
            "action": data.get("source_action"),
            "person": data.get("source_person"),
            "target": data.get("source_target"),
            "occurred_at": data.get("occurred_at"),
        }
        outcomes = self._deliver_event(
            room_id=room_id,
            event=event,
            type_record=type_record,
            apps=self._apps_by_id(),
            links=links,
            dry_run=False,
            actor=actor,
            source=source,
            # This row, named explicitly. Without it the retry recomputes the key
            # with the prospect that has since been linked, misses the row it was
            # asked to clear, and writes a duplicate beside it.
            retry_of=record,
        )
        for entry in outcomes:
            entry["retried"] = True
        return (
            outcomes[0]
            if outcomes
            else {
                "outcome": "skipped",
                "delivery_id": record["id"],
                "reason": "no_prospect",
                "detail": SKIP_REASONS["no_prospect"],
                "retried": True,
            }
        )

    # -- the inbound half ----------------------------------------------------- #

    def receive_webhook(
        self, raw: bytes | str, headers: Any, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Read one Outreach webhook delivery, prove it, and record it.

        The order is the point. The signature is checked against the exact bytes
        received, the body is parsed, the ``payloadVersion`` is confirmed, and only
        then is anything written. [sourced] "Outreach does not retry webhook
        deliveries upon receiving any of the Status Codes including 500 Internal
        Server Error and 429 Too Many Requests", so anything this build cannot
        understand is recorded as ``ignored`` and still acknowledged - a refusal
        would be permanent data loss, not a nudge.
        """
        body = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw or b"")
        secrets = [_identifier_of(record, "webhook_secret") for record in self._apps()]
        secrets = [secret for secret in secrets if secret]
        if not secrets:
            raise FeedNotConfiguredError(
                "no Outreach app carries a webhook_secret, so a delivery cannot be verified; "
                "put the signing secret on the app record the subscription belongs to"
            )
        webhooks.require_signature(headers, secrets, body)

        payload = webhooks.parse_body(body)
        version = webhooks.require_payload_version(payload)
        normal = webhooks.normalise(payload)
        key = webhooks.signal_key(
            normal["resource"],
            normal["type"],
            normal["mailing_id"],
            normal["sequence"],
            normal["prospect_id"],
        )

        existing = self.store.find(SIGNAL_COLLECTION, {"signal_key": key}, limit=1)
        if existing:
            return {
                "status": "duplicate",
                "duplicate": True,
                "signal_key": key,
                "signal_id": existing[0]["id"],
                "resource": normal["resource"],
                "type": normal["type"],
                "prospect_id": normal["prospect_id"],
                "room_id": existing[0].get("room_id"),
                "detail": "this delivery has already been recorded; it was not counted twice",
            }

        link_status, linked_room, matched = self._resolve_prospect(normal["prospect_id"])
        if normal["supported"]:
            status, reason = "recorded", ""
        else:
            status = "ignored"
            reason = (
                f"resource {normal['resource'] or '(none)'!r} type {normal['type'] or '(none)'!r} is "
                "outside the documented mailing family"
            )

        data = {
            "signal_key": key,
            "resource": normal["resource"],
            "type": normal["type"],
            "intent": normal["intent"],
            "status": status,
            "ignore_reason": reason,
            "link_status": link_status,
            "prospect_id": normal["prospect_id"],
            "mailing_id": normal["mailing_id"],
            "sequence": normal["sequence"],
            "occurred_at": normal["occurred_at"] or _now_iso(),
            "payload_version": version,
            "before_update": normal["before_update"],
            "attributes": normal["attributes"],
            "payload": payload,
            "signature_verified": True,
            "matched_links": matched,
        }
        record = self.store.create(
            SIGNAL_COLLECTION, data, room_id=linked_room, actor=actor, source=source
        )
        return {
            "status": status,
            "duplicate": False,
            "signal_key": key,
            "signal_id": record["id"],
            "resource": normal["resource"],
            "type": normal["type"],
            "intent": normal["intent"],
            "prospect_id": normal["prospect_id"],
            "room_id": linked_room,
            "link_status": link_status,
            "ignore_reason": reason,
            "before_update": normal["before_update"] is not None,
        }

    def _resolve_prospect(self, prospect_id: str) -> tuple[str, str | None, int]:
        """Match a delivered prospect id to a room through its link.

        Unmatched is a recorded state, not a failure. The seller platform knows
        about prospects this DSR has never been told about, and a signal naming one
        is still worth keeping.
        """
        if not prospect_id:
            return "unknown_prospect", None, 0
        records = [
            record
            for record in self.store.find(
                PROSPECT_COLLECTION, {"prospect_id": prospect_id}, limit=50
            )
            if _as_bool((record.get("data") or {}).get("active"))
        ]
        if not records:
            return "unlinked", None, 0
        records.sort(key=lambda record: str(record.get("updated_at") or ""), reverse=True)
        return "linked", str(records[0].get("room_id")), len(records)
