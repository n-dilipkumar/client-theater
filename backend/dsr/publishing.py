"""WF-011: take a room from draft to live and hand over the link.

The researched flow (Qwilr help articles 87 and 578, plus the v1 Pages and
Webhooks API reference) is:

1. A new room starts in ``draft`` and its public link is disabled.
2. The seller opens **Share**, picks **Live** in the status drop-down, and the
   public URL becomes live.
3. Setting it live copies the public link to the clipboard; the same pop-up can
   re-copy it later, and carries link expiry, view limit, password and identity
   verification.
4. The system never emails the room. It hands over a URL, and the seller owns
   the send and the tracking of it.

Where the state lives
---------------------
Jev chose ``in_room_data`` for this (see ``orchestration/decisions/jev-audit.jsonl``,
audit ids ``jev-20260925T215651-7120-11430`` and ``jev-20260925T215651-7120-11788``):
publish state is written into the room record's own ``data`` JSON through the
audited store, so there is no migration, no typed column, and a status filter is
an ordinary indexed ``find`` on the ``status`` path. The invariants that must not
be bypassed (which statuses are public, a template never goes live, a password is
never stored in the clear) live here, in one place, rather than in every caller.

Jev also chose ``audited_event_record_plus_sync_delivery`` for the transition
event: a durable ``event`` record written through the audited store, then a
short-timeout HTTP POST to each matching subscriber whose outcome is recorded as
a ``webhook_delivery`` row. Nothing is lost, and a failed POST stays visible
instead of vanishing.

Deliberately out of scope for this ticket, because another ticket owns it:

* Expiry flipping a room to ``declined`` on its own is WF-012. Here an elapsed
  ``access.expires_at`` is surfaced as a warning on the share view and the link,
  and nothing else changes.
* Counting views and enforcing ``access.max_views`` is buyer-engagement work
  (WF-006). The setting is stored and displayed; nothing enforces it here.
* Opening the link as a buyer, and checking a password, is a viewer concern. No
  verification helper ships with this module.

A note on secrets: the store is schema-flexible by design, so a link password is
kept as a salted PBKDF2-HMAC-SHA256 hash inside ``access.password_hash`` and is
stripped from every response this module produces (see :func:`redact`). Nothing
under ``/api/publishing`` can echo it, and the cleartext is never stored.

The hash itself is still readable through ``/api/records/room/{id}`` and through
the complete audit log, because those surfaces return whatever a team stored and
the API has no authentication. That is a property of the API rather than of this
workflow, and closing it means deciding on authentication; redaction here would
have had to cover the audit ``diff`` map as well, and a partial redaction that
misses the diff would look like protection without being it. A deployment that
needs credential isolation should keep link passwords out of the room payload
and hold them in its own collection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from dsr.db.audited import RecordNotFound, utcnow
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Status vocabulary
# --------------------------------------------------------------------------- #

DRAFT = "draft"
LIVE = "live"

#: The six statuses the researched drop-down offers. ``live`` and ``accepting``
#: are mutually exclusive, which the vendor states explicitly.
STATUSES: tuple[str, ...] = ("draft", "live", "accepting", "accepted", "disabled", "declined")

#: Statuses whose public URL is enabled. The vendor states that live and
#: accepting are both shareable; that ``accepted`` stays reachable afterwards is
#: an inference, and the only one in this table.
PUBLIC_STATUSES = frozenset({LIVE, "accepting", "accepted"})

#: Statuses a room that has already been public can be brought back from. The
#: vendor distinguishes a first publish (``pageSetLive``) from bringing a
#: taken-down page back (``pageRevivedLive``); a disable or a decline is what
#: takes a room down.
REVIVABLE_STATUSES = frozenset({"disabled", "declined"})

STATUS_DESCRIPTIONS: dict[str, str] = {
    "draft": "Draft. Editable, and the public link is disabled.",
    "live": "Live. The public link works and the room can be shared.",
    "accepting": "Live with an accept block, waiting on the buyer.",
    "accepted": "The buyer accepted. The room stays reachable.",
    "disabled": "Turned off. The public link stops working.",
    "declined": "Declined. The public link stops working.",
}

#: A room carrying this tag is out of the way rather than deleted. The vendor
#: documents ``tags=archived`` as the way to list exactly these rooms.
ARCHIVED_TAG = "archived"

#: Spellings a team might already use to mark a room as a template. The vendor
#: is explicit that a template never goes live and is never shared.
TEMPLATE_KEYS = ("is_template", "kind", "type")
TEMPLATE_VALUES = ("template",)

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

#: Qwilr's ``pageSetLive``: the first time a room becomes reachable.
EVENT_SET_LIVE = "room.set_live"
#: Qwilr's ``pageRevivedLive``: a room that had been public becomes public again.
EVENT_REVIVED_LIVE = "room.revived_live"
#: Our own generalisation, so a subscriber can follow every transition without
#: one subscription per status.
EVENT_STATUS_CHANGED = "room.status_changed"

EVENT_TYPES: tuple[str, ...] = (EVENT_SET_LIVE, EVENT_REVIVED_LIVE, EVENT_STATUS_CHANGED)

EVENT_DESCRIPTIONS: dict[str, str] = {
    EVENT_SET_LIVE: "A draft room was published and its public link was enabled.",
    EVENT_REVIVED_LIVE: "A previously public room was made public again.",
    EVENT_STATUS_CHANGED: "A room changed status.",
}

#: Collections owned by this workflow. They are ordinary records: the same
#: envelope, the same dynamic index, the same audit row in the same transaction.
EVENT_COLLECTION = "event"
SUBSCRIPTION_COLLECTION = "webhook_subscription"
DELIVERY_COLLECTION = "webhook_delivery"

DEFAULT_DELIVERY_TIMEOUT = 2.0
_PBKDF2_ITERATIONS = 120_000
_PBKDF2_PREFIX = "pbkdf2_sha256"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")

#: (url, body, headers) -> HTTP status code. Injected in tests; the default is a
#: stdlib POST so the project keeps its zero-dependency backend.
Transport = Callable[[str, bytes, Mapping[str, str]], int]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _http_post(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked on subscribe
        return int(response.status)


def hash_password(password: str, *, salt: str | None = None, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash a link password for storage. The cleartext is never persisted.

    The format is self-describing so a future iteration count can be raised
    without a migration: ``pbkdf2_sha256$<iterations>$<salt>$<hex digest>``.
    """
    if not password:
        raise ValueError("password is required")
    salt_bytes = base64.b64decode(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return f"{_PBKDF2_PREFIX}${iterations}${base64.b64encode(salt_bytes).decode()}${digest.hex()}"


def redact(value: Any) -> Any:
    """Return a copy of a payload with the stored password hash removed.

    Applied to every response this module returns, so no endpoint can echo the
    hash by accident. Nested objects are walked because the audit log and the
    generic record API both hand back whole payloads.
    """
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            if key == "password_hash":
                continue
            cleaned[key] = redact(child)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _parse_moment(value: Any, *, end_of_day: bool = False) -> datetime | None:
    """Parse a stored expiry value into an aware UTC datetime.

    Accepts a bare date as well as a full ISO timestamp. A bare date means the
    end of that day, which is what a seller typing "31 December" means.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if end_of_day else time.min)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(text[:10]), time.max if end_of_day else time.min)
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_template(data: Mapping[str, Any]) -> bool:
    """True when the room is a template, which can never be published."""
    for key in TEMPLATE_KEYS:
        value = data.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in TEMPLATE_VALUES:
            return True
    return False


def expiry_state(expires_at: Any) -> tuple[bool, datetime | None]:
    """``(is_expired, expiry_moment)`` for a stored expiry value.

    A bare date means the end of that day, because a seller typing "31 December"
    means the whole day. A value that cannot be parsed is treated as no expiry
    rather than as an error: the field is free text to the schema-flexible store,
    and refusing to show the link over an unparseable string helps nobody.
    """
    moment = _parse_moment(expires_at, end_of_day=True)
    if moment is None:
        return False, None
    return moment < datetime.now(timezone.utc), moment


def _tags(data: Mapping[str, Any]) -> list[str]:
    tags = data.get("tags")
    if isinstance(tags, str):
        return [tags]
    if isinstance(tags, (list, tuple)):
        return [str(tag) for tag in tags]
    return []


def _as_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class PublishConflict(RuntimeError):
    """A requested transition is not allowed from the room's current state."""


class PublishingService:
    """Draft to live, and the handover that follows it.

    A thin, opinionated layer over :class:`~dsr.store.RecordStore`. Every write
    goes through the store, so every write is audited; this module never touches
    SQLite.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        base_url: str = "http://127.0.0.1:8000",
        transport: Transport | None = None,
        timeout: float = DEFAULT_DELIVERY_TIMEOUT,
    ) -> None:
        self.store = store
        self.base_url = base_url.rstrip("/")
        self._transport = transport or _http_post
        self.timeout = timeout

    # -- records ------------------------------------------------------------ #

    def room(self, room_id: str) -> dict[str, Any]:
        """Fetch a room record, or raise ``RecordNotFound``."""
        record = self.store.get(room_id)
        if record is None or record["collection"] != "room":
            raise RecordNotFound(room_id)
        return record

    def status_of(self, data: Mapping[str, Any]) -> str:
        """A room with no stored status is a draft.

        The researched rule is that every new room starts in draft, so absence
        of the field means the same thing as ``draft`` rather than "unknown".
        """
        value = data.get("status")
        return str(value) if value in STATUSES else DRAFT

    def public_url(self, data: Mapping[str, Any], room_id: str) -> str:
        """The room's public URL.

        Derived, never stored: the base URL is deployment configuration, and a
        stored copy would go stale. A team that wants a friendlier path can set
        ``slug`` in the payload and it is used instead.
        """
        slug = data.get("slug")
        if isinstance(slug, str) and _SLUG_RE.match(slug.strip()):
            return f"{self.base_url}/r/{slug.strip()}"
        return f"{self.base_url}/r/{room_id}"

    def is_public(self, data: Mapping[str, Any]) -> bool:
        return self.status_of(data) in PUBLIC_STATUSES

    # -- projections -------------------------------------------------------- #

    def access_of(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """The access settings as the share pop-up shows them. No hash."""
        access = data.get("access")
        access = access if isinstance(access, Mapping) else {}
        expires_at = access.get("expires_at")
        return {
            "expires_at": expires_at,
            "max_views": _as_positive_int(access.get("max_views")),
            "require_password": bool(access.get("require_password")) or bool(access.get("password_hash")),
            "password_protected": bool(access.get("password_hash")),
            "require_identity_verification": bool(access.get("require_identity_verification")),
            "expired": expiry_state(expires_at)[0],
        }

    def expiry_state(self, expires_at: Any) -> tuple[bool, str | None, datetime | None]:
        """``(is_expired, reason, expiry_moment)`` for a stored expiry value."""
        expired, moment = expiry_state(expires_at)
        if not expired:
            return False, None, moment
        return True, "The link expiry date has passed.", moment

    def warnings(self, data: Mapping[str, Any], room_id: str) -> list[str]:
        """User-facing cautions for the share pop-up and the room card."""
        notes: list[str] = []
        status = self.status_of(data)
        template = is_template(data)
        if template:
            notes.append("A template is never published and is never shared.")
        if status == DRAFT:
            notes.append("Draft. The public link is disabled until the room goes live.")
        if self.is_public(data) and template:
            notes.append("This room is marked as a template, so its link will not resolve for a buyer.")
        if ARCHIVED_TAG in _tags(data):
            notes.append("Archived rooms are hidden from the board unless archived is selected.")
        access = self.access_of(data)
        expired, reason, _ = self.expiry_state(access["expires_at"])
        if expired:
            notes.append(f"Link expiry {access['expires_at']} has passed.")
        if self.is_public(data) and access["password_protected"]:
            notes.append("This link is password protected.")
        if self.is_public(data) and access["require_identity_verification"]:
            notes.append("Viewers must verify their identity before the room opens.")
        if status == "accepting":
            notes.append("Accepting means live with an accept block on the room.")
        return notes

    def view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Project a room record for the share pop-up.

        An explicit projection rather than the raw payload, because the payload
        holds fields this workflow knows nothing about and one of them is the
        password hash.
        """
        data = record.get("data") or {}
        status = self.status_of(data)
        template = is_template(data)
        public = self.is_public(data)
        return redact(
            {
                "id": record["id"],
                "name": data.get("name") or "Untitled room",
                "account": data.get("account"),
                "owner": data.get("owner"),
                "status": status,
                "status_source": "explicit" if data.get("status") in STATUSES else "implicit",
                "published": public,
                "public_url": self.public_url(data, record["id"]) if public else None,
                "url_available_when_live": self.public_url(data, record["id"]),
                "is_template": template,
                "tags": _tags(data),
                "archived": ARCHIVED_TAG in _tags(data),
                "metadata": data.get("metadata") if isinstance(data.get("metadata"), (dict, str)) else None,
                "access": self.access_of(data),
                "ever_published": bool(data.get("published_at")),
                "published_at": data.get("published_at"),
                "unpublished_at": data.get("unpublished_at"),
                "status_changed_at": data.get("status_changed_at"),
                "revision": record.get("revision"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "warnings": self.warnings(data, record["id"]),
            }
        )

    def share(self, room_id: str) -> dict[str, Any]:
        """Everything the share pop-up needs for one room."""
        record = self.room(room_id)
        data = record.get("data") or {}
        view = self.view(record)
        view["status_options"] = [
            {"value": value, "label": value, "description": STATUS_DESCRIPTIONS[value], "is_public": value in PUBLIC_STATUSES}
            for value in STATUSES
        ]
        view["can_publish"] = not is_template(data)
        view["handover"] = (
            "This app hands over a link. It never sends the message for you, so the send "
            "and the tracking of it stay in your hands."
        )
        view["event_types"] = [{"event": name, "description": EVENT_DESCRIPTIONS[name]} for name in EVENT_TYPES]
        return view

    # -- board -------------------------------------------------------------- #

    def board(
        self,
        *,
        statuses: Sequence[str] | None = None,
        tag: str | None = None,
        q: str | None = None,
        owner: str | None = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Rooms with a per-room status badge, filtered like the vendor's list.

        Filtering happens here rather than in the store because a room with no
        stored status counts as a draft, and the dynamic index can only match a
        path that exists, so an index-only ``status=draft`` query would silently
        drop every implicit draft. The board is a management view over a single
        collection and is bounded by ``limit``; ``counts`` describes the returned
        window, not the whole collection.
        """
        wanted = {value for value in (statuses or ()) if value in STATUSES}
        records = self.store.list("room", limit=limit, order_by="updated_at", descending=True)
        views = [self.view(record) for record in records]

        if wanted:
            views = [view for view in views if view["status"] in wanted]
        if tag:
            views = [view for view in views if tag in view["tags"]]
        if not include_archived and tag != ARCHIVED_TAG:
            # `tags=archived` is the vendor's way of asking for exactly the
            # archived rooms, so naming the tag is itself the request to see them.
            views = [view for view in views if not view["archived"]]
        if owner:
            views = [view for view in views if str(view.get("owner") or "") == owner]
        if q:
            needle = q.strip().lower()
            views = [
                view
                for view in views
                if needle in str(view.get("name", "")).lower()
                or needle in str(view.get("account") or "").lower()
                or needle in view["id"].lower()
            ]

        counts = {status: sum(1 for view in views if view["status"] == status) for status in STATUSES}
        return {
            "rooms": views,
            "count": len(views),
            "counts": counts,
            "statuses": list(STATUSES),
            "status_options": [
                {"value": value, "label": value, "description": STATUS_DESCRIPTIONS[value], "is_public": value in PUBLIC_STATUSES}
                for value in STATUSES
            ],
            "filters": {
                "status": sorted(wanted),
                "tag": tag,
                "q": q,
                "owner": owner,
                "include_archived": include_archived,
                "limit": limit,
            },
        }

    # -- writes ------------------------------------------------------------- #

    def set_status(
        self,
        room_id: str,
        status: str,
        *,
        source: str,
        actor: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Move a room to ``status`` and emit the matching transition event.

        A transition to the status the room already has is a no-op: it returns
        ``changed: false``, writes nothing, and emits nothing, so the audit log
        never records a change that did not happen.

        ``source`` is required and has no default. The branch hardcoded prose
        here - ``"WF-011 set status draft -> live"`` - which described the change
        but named no route, so a rename of the endpoint could never be noticed
        in the audit log. Every write on this path (the room update, the event,
        and each delivery) is attributed to the route that actually served the
        request, and the route itself builds that string from ``router.prefix``.
        """
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")

        record = self.room(room_id)
        data = record.get("data") or {}
        previous = self.status_of(data)

        if is_template(data) and status in PUBLIC_STATUSES:
            raise PublishConflict("A template cannot go live and cannot be shared.")

        if previous == status:
            return {
                "room": self.view(record),
                "transition": {"from": previous, "to": status, "changed": False},
                "event": None,
                "deliveries": [],
                "share_link": self.link_payload(record) if self.is_public(data) else None,
            }

        now = utcnow()
        published = status in PUBLIC_STATUSES
        patch: dict[str, Any] = {
            "status": status,
            "published": published,
            "status_changed_at": now,
        }
        if published:
            # Kept across a disable, because a room that was public before is a
            # revive rather than a first publish. `unpublished_at` is cleared
            # explicitly: the store's patch merges, so leaving the old value in
            # place would say the room was never public again.
            patch["published_at"] = data.get("published_at") or now
            patch["unpublished_at"] = None
        else:
            patch["unpublished_at"] = now

        updated = self.store.update(
            room_id,
            patch,
            actor=actor,
            source=source,
            expected_revision=expected_revision,
            request_id=request_id,
        )

        event = self._emit(
            updated,
            previous=previous,
            status=status,
            actor=actor,
            request_id=request_id,
            source=source,
        )
        deliveries = self._deliver(event, source=source) if event else []

        return {
            "room": self.view(updated),
            "transition": {"from": previous, "to": status, "changed": True},
            "event": event,
            "deliveries": deliveries,
            "share_link": self.link_payload(updated) if published else None,
        }

    def set_access(
        self,
        room_id: str,
        changes: Mapping[str, Any],
        *,
        source: str,
        actor: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Update the access settings from the share pop-up.

        ``access`` is nested, and the store's patch is a shallow merge, so the
        merge happens here: patching ``access`` directly would drop the sibling
        settings.

        ``source`` is required, for the same reason as in :meth:`set_status`: the
        audit row must name the route that served the write, not a label.
        """
        record = self.room(room_id)
        data = record.get("data") or {}
        current = data.get("access")
        access = dict(current) if isinstance(current, Mapping) else {}

        if "expires_at" in changes:
            value = changes["expires_at"]
            if value in ("", None):
                access["expires_at"] = None
            else:
                moment = _parse_moment(value, end_of_day=True)
                if moment is None:
                    raise ValueError("expires_at must be an ISO date or timestamp")
                access["expires_at"] = str(value)

        if "max_views" in changes:
            value = _as_positive_int(changes["max_views"])
            if changes["max_views"] in (None, "") or value is not None:
                access["max_views"] = value
            else:
                raise ValueError("max_views must be a positive whole number")

        if "require_identity_verification" in changes:
            access["require_identity_verification"] = bool(changes["require_identity_verification"])

        if changes.get("password"):
            # Hashes are salted, so re-setting the same password is still a real
            # change and still produces an audit row.
            access["password_hash"] = hash_password(str(changes["password"]))
            access["require_password"] = True
        elif changes.get("clear_password"):
            access.pop("password_hash", None)
            access["require_password"] = False

        updated = self.store.update(
            room_id,
            {"access": access},
            actor=actor,
            source=source,
            expected_revision=expected_revision,
        )
        return {"room": self.view(updated)}

    # -- link handover ------------------------------------------------------ #

    def link_payload(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """What the seller copies. Never contains the password hash."""
        data = record.get("data") or {}
        room_id = record["id"]
        access = self.access_of(data)
        expired, reason, _ = self.expiry_state(access["expires_at"])
        return redact(
            {
                "room_id": room_id,
                "url": self.public_url(data, room_id),
                "status": self.status_of(data),
                "public_enabled": self.is_public(data),
                "shareable": self.is_public(data) and not is_template(data) and not expired,
                "expired": expired,
                "access": access,
                "warnings": self.warnings(data, room_id),
            }
        )

    def share_link(self, room_id: str) -> dict[str, Any]:
        """The clipboard payload. A read, so it writes no audit row.

        A draft room has no working public link, so asking for one is a
        conflict rather than an empty string: the caller should set the room
        live first.
        """
        record = self.room(room_id)
        data = record.get("data") or {}
        if is_template(data):
            raise PublishConflict("A template has no public link to share.")
        if not self.is_public(data):
            raise PublishConflict(
                f"This room is {self.status_of(data)}, so its public link is disabled. Set it live first."
            )
        return self.link_payload(record)

    # -- webhook subscriptions ---------------------------------------------- #

    def subscribe(
        self,
        *,
        source: str,
        name: str,
        url: str,
        events: Iterable[str],
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Register a webhook subscription for transition events.

        ``source`` is required; see :meth:`set_status` for why.
        """
        target = str(url or "").strip()
        if not target.startswith(("http://", "https://")):
            raise ValueError("url must be an http or https URL")
        chosen = [event for event in dict.fromkeys(events or ()) if event]
        unknown = [event for event in chosen if event not in EVENT_TYPES]
        if unknown:
            raise ValueError(f"unknown event(s) {', '.join(sorted(unknown))}; expected {', '.join(EVENT_TYPES)}")
        if not chosen:
            raise ValueError("at least one event is required")

        record = self.store.create(
            SUBSCRIPTION_COLLECTION,
            {"name": str(name or target), "url": target, "events": chosen, "active": True},
            actor=actor,
            source=source,
        )
        return self.subscription_view(record)

    def subscription_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return redact(
            {
                "id": record["id"],
                "name": data.get("name"),
                "url": data.get("url"),
                "events": data.get("events") or [],
                "active": bool(data.get("active", True)),
                "created_at": record.get("created_at"),
                "cancelled_at": record.get("deleted_at"),
            }
        )

    def subscriptions(self, *, include_cancelled: bool = False) -> list[dict[str, Any]]:
        records = self.store.list(
            SUBSCRIPTION_COLLECTION, limit=200, include_deleted=include_cancelled
        )
        return [self.subscription_view(record) for record in records]

    def cancel(self, subscription_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Cancel a subscription. A soft delete, so the history survives.

        ``source`` is required; see :meth:`set_status` for why.
        """
        record = self.store.get(subscription_id)
        if record is None or record["collection"] != SUBSCRIPTION_COLLECTION:
            raise RecordNotFound(subscription_id)
        self.store.delete(
            subscription_id,
            actor=actor,
            source=source,
        )
        return self.subscription_view(record)

    # -- events and delivery ------------------------------------------------ #

    def events(self, *, room_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Transition events, newest first, optionally for one room.

        Scoped by the envelope's ``room_id`` rather than by a payload key: the
        store reserves ``room_id``, so it is never indexed inside ``data``.
        """
        records = self.store.list(EVENT_COLLECTION, room_id=room_id, limit=limit)
        return [self.event_view(record) for record in records]

    def deliveries(self, subscription_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Recent delivery attempts, including the ones that failed.

        The payload is kept because a delivery you cannot inspect is a delivery
        you cannot debug.
        """
        records = self.store.find(
            DELIVERY_COLLECTION, {"subscription_id": subscription_id}, limit=limit
        )
        return [
            redact(
                {
                    "id": record["id"],
                    "subscription_id": record["data"].get("subscription_id"),
                    "event": record["data"].get("event"),
                    "url": record["data"].get("url"),
                    "status": record["data"].get("status"),
                    "status_code": record["data"].get("status_code"),
                    "error": record["data"].get("error"),
                    "attempts": record["data"].get("attempts"),
                    "payload": record["data"].get("payload"),
                    "created_at": record.get("created_at"),
                }
            )
            for record in records
        ]

    def event_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return redact(
            {
                "id": record["id"],
                "event": data.get("event"),
                "description": EVENT_DESCRIPTIONS.get(str(data.get("event")), ""),
                "room_id": record.get("room_id"),
                "room_name": data.get("room_name"),
                "status": data.get("status"),
                "previous_status": data.get("previous_status"),
                "public_url": data.get("public_url"),
                "metadata": data.get("metadata"),
                "occurred_at": data.get("occurred_at") or record.get("created_at"),
            }
        )

    def _event_name(self, previous: str, status: str) -> str:
        if status == LIVE:
            return EVENT_REVIVED_LIVE if previous in REVIVABLE_STATUSES else EVENT_SET_LIVE
        return EVENT_STATUS_CHANGED

    def _emit(
        self,
        record: Mapping[str, Any],
        *,
        previous: str,
        status: str,
        actor: str | None,
        request_id: str | None,
        source: str,
    ) -> dict[str, Any] | None:
        """Record the transition as an ``event`` row, then deliver it.

        The vendor's ``metadata`` is the documented correlation field, so it is
        carried through verbatim: a CRM id set on the room comes back on every
        event without this workflow knowing anything about it.

        The event row is attributed to the same route as the room update that
        caused it, because that is the request that actually produced it.
        """
        data = record.get("data") or {}
        room_id = record["id"]
        name = self._event_name(previous, status)
        payload = {
            "event": name,
            "room_name": data.get("name"),
            "status": status,
            "previous_status": previous,
            "public_url": self.public_url(data, room_id) if self.is_public(data) else None,
            "metadata": data.get("metadata") if isinstance(data.get("metadata"), (dict, str)) else {},
            "occurred_at": utcnow(),
        }
        event = self.store.create(
            EVENT_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
            request_id=request_id,
        )
        return self.event_view(event)

    def _deliver(self, event: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
        """POST the event to every active subscriber and record the outcome."""
        delivered: list[dict[str, Any]] = []
        for subscription in self.subscriptions():
            if not subscription["active"] or event.get("event") not in subscription["events"]:
                continue
            delivered.append(self._attempt(subscription, event, source=source))
        return delivered

    def _attempt(
        self, subscription: Mapping[str, Any], event: Mapping[str, Any], *, source: str
    ) -> dict[str, Any]:
        name = str(event.get("event"))
        body = json.dumps(dict(event), ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-DSR-Event": name}
        status_code: int | None = None
        error: str | None = None
        try:
            status_code = self._transport(subscription["url"], body, headers, self.timeout)
        except urllib.error.HTTPError as exc:  # 4xx/5xx still means we reached it
            status_code = int(exc.code)
            error = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - a dead subscriber must not fail the publish
            error = f"{type(exc).__name__}: {exc}"

        outcome = "delivered" if status_code is not None and 200 <= status_code < 300 else "failed"
        record = self.store.create(
            DELIVERY_COLLECTION,
            {
                "subscription_id": subscription["id"],
                "subscription_name": subscription["name"],
                "url": subscription["url"],
                "event": name,
                "status": outcome,
                "status_code": status_code,
                "error": error,
                "attempts": 1,
                "payload": dict(event),
            },
            room_id=event.get("room_id"),
            actor="webhook",
            source=source,
        )
        return {
            "subscription_id": subscription["id"],
            "subscription_name": subscription["name"],
            "url": subscription["url"],
            "event": name,
            "status": outcome,
            "status_code": status_code,
            "error": error,
            "delivery_id": record["id"],
        }
