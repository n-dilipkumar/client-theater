"""WF-001: create a Digital Sales Room from an account and a template.

The researched flow (`docs/research/digital-sales-room-workflows/wf/WF-001.md`) is
a three-step wizard — pick the account, pick a template, name the room — that
produces a room bound to exactly one site. The account binding is the part that
matters later: it is what makes invite-by-email resolve without a separate
directory lookup, and the site is what supplies the read-only *Site ID*
integration key.

What this module deliberately does **not** do is fix a schema. Everything below
writes through :class:`~dsr.store.RecordStore` into the open JSON ``data``
object, so a team can send a field the server has never seen and it is stored
and indexed without a migration. The only fixed vocabulary is the envelope plus
the handful of keys the workflow itself needs (``account_id``, ``template_id``,
``status``, ...) which are documented in ``docs/design/WF-001-create-room.md``.

Two decisions here were made by Jev rather than by taste, and are recorded in
``orchestration/decisions/jev-audit.jsonl``:

* ``room`` and its ``site`` are written in one transaction, so a room can never
  exist without the site it is bound to (audit
  ``jev-20260925T214101-11156-61605``).
* The template catalogue is the shipped set overlaid with any ``template``
  records in the store, so a deployment has templates out of the box and a team
  can still add one with no code change (audit
  ``jev-20260925T214101-11156-61939``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Sequence

from dsr.db.audited import new_id
from dsr.store import RecordStore

ROOM = "room"
SITE = "site"
TEMPLATE = "template"
ACCOUNT = "account"

DEFAULT_STATUS = "active"
STATUSES = ("active", "archived")
"""The Rooms list filters on one status at a time and defaults to Active."""

MAX_NAME_LENGTH = 200
MAX_FRIENDLY_URL_LENGTH = 64

# `find()` is exact-match only because that is what the dynamic index can answer.
# Free-text search over the filtered set is done in Python, so it needs a ceiling.
_SCAN_LIMIT = 1000
_DERIVED_SUFFIX_LIMIT = 50

# The template set shipped with the app. `template_id` / `template_version_id`
# are a pair on purpose: a room pins a specific version of the template it was
# created from, which is the same shape the vendor inventory API exposes as
# digitalSalesRoomTemplateId + digitalSalesRoomTemplateVersionId.
SHIPPED_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "tpl_standard",
        "template_version_id": "tpl_standard_v1",
        "name": "Standard Digital Sales Room",
        "description": (
            "The pre-configured layout: overview, documents, pricing, and an activity trail. "
            "Customise the layout later without losing the structure."
        ),
        "sections": ["overview", "documents", "pricing", "activity"],
        "source": "shipped",
    },
    {
        "template_id": "tpl_guided_evaluation",
        "template_version_id": "tpl_guided_evaluation_v1",
        "name": "Guided Evaluation",
        "description": "A staged evaluation path with a checkpoint per section, for longer cycles.",
        "sections": ["overview", "discovery", "proposal", "security", "next-steps"],
        "source": "shipped",
    },
    {
        "template_id": "tpl_technical_review",
        "template_version_id": "tpl_technical_review_v1",
        "name": "Technical Review",
        "description": "Security, architecture, and integration material for a technical buying group.",
        "sections": ["overview", "architecture", "security", "integrations", "faq"],
        "source": "shipped",
    },
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_FRIENDLY_URL = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RoomCreationError(ValueError):
    """A create-room request that cannot be honoured.

    Carries the HTTP status the API should answer with, so the mapping from
    "why the wizard refused" to "what the operator sees" is decided in one
    place instead of at the route.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# Templates: shipped set overlaid with store records
# --------------------------------------------------------------------------- #


def _template_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a schema-flexible ``template`` record into the catalogue shape.

    Only ``template_id`` is required; everything else is whatever the owning
    team stored, which is passed through untouched.
    """
    data = dict(record.get("data") or {})
    template_id = str(data.get("template_id") or record.get("id") or "")
    version_id = str(data.get("template_version_id") or data.get("version_id") or template_id)
    projected = dict(data)
    projected["template_id"] = template_id
    projected["template_version_id"] = version_id
    projected.setdefault("name", template_id)
    projected["source"] = data.get("source") or "store"
    return projected


def list_templates(store: RecordStore) -> list[dict[str, Any]]:
    """Every template an operator can pick, shipped ones plus store records.

    A store record with the same ``template_id`` as a shipped template replaces
    it, so a team can retune a built-in without a deploy and without losing the
    templates nobody has touched.
    """
    catalogue: dict[str, dict[str, Any]] = {
        template["template_id"]: dict(template) for template in SHIPPED_TEMPLATES
    }
    for record in store.list(TEMPLATE, limit=_SCAN_LIMIT, order_by="id", descending=False):
        projected = _template_from_record(record)
        if projected["template_id"]:
            catalogue[projected["template_id"]] = projected
    return list(catalogue.values())


def find_template(store: RecordStore, template_id: str) -> dict[str, Any] | None:
    for template in list_templates(store):
        if template["template_id"] == template_id:
            return template
    return None


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


def list_accounts(
    store: RecordStore, *, query: str | None = None, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    """Accounts a room can be bound to, newest first.

    Accounts are ordinary schema-flexible records, so the record is returned
    as-is: a team that stores domains, tiers, or member graphs gets them back
    without this function knowing they exist.
    """
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    records = store.list(ACCOUNT, limit=_SCAN_LIMIT, order_by="created_at", descending=False)

    needle = (query or "").strip().lower()
    if needle:
        records = [
            record
            for record in records
            if needle in str(record["data"].get("name", "")).lower()
            or needle in str(record["data"].get("domain", "")).lower()
        ]

    records.sort(key=lambda record: record["created_at"], reverse=True)
    return {
        "accounts": records[offset : offset + limit],
        "count": len(records[offset : offset + limit]),
        "total": len(records),
    }


# --------------------------------------------------------------------------- #
# Friendly URLs
# --------------------------------------------------------------------------- #


def slugify(value: str) -> str:
    """Lowercase ASCII slug, accent-folded, with runs collapsed to one hyphen."""
    folded = unicodedata.normalize("NFKD", value or "")
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return _SLUG_STRIP.sub("-", ascii_text).strip("-")


def _validate_friendly_url(candidate: str, *, source: str) -> str:
    if not candidate:
        raise RoomCreationError(
            "invalid_friendly_url",
            f"{source} does not contain any characters a friendly URL can use",
        )
    if len(candidate) > MAX_FRIENDLY_URL_LENGTH:
        raise RoomCreationError(
            "invalid_friendly_url",
            f"friendly URL must be at most {MAX_FRIENDLY_URL_LENGTH} characters, got {len(candidate)}",
        )
    if not _FRIENDLY_URL.match(candidate):
        raise RoomCreationError(
            "invalid_friendly_url",
            "friendly URL must be lowercase words separated by single hyphens, e.g. acme-evaluation",
        )
    return candidate


def _friendly_url_is_taken(store: RecordStore, candidate: str) -> bool:
    """Exact lookup through the dynamic index — no typed column involved."""
    return bool(store.find(ROOM, {"friendly_url": candidate}))


def _resolve_friendly_url(
    store: RecordStore, name: str, requested: str | None
) -> tuple[str, bool]:
    """Return ``(friendly_url, derived)``.

    An operator-supplied URL that collides is an error: silently rewriting what
    someone typed would give them a room at an address they did not ask for. A
    URL derived from the name has no such intent, so it gets a numeric suffix
    until it is free — the same thing a platform does with a site name.
    """
    if requested is not None and str(requested).strip():
        candidate = slugify(str(requested).strip().strip("/"))
        candidate = _validate_friendly_url(candidate, source="The friendly URL")
        if _friendly_url_is_taken(store, candidate):
            raise RoomCreationError(
                "friendly_url_taken",
                f"friendly URL {candidate!r} is already used by another room",
                status=409,
            )
        return candidate, False

    base = _validate_friendly_url(slugify(name), source="The room name")
    if not _friendly_url_is_taken(store, base):
        return base, True
    for suffix in range(2, _DERIVED_SUFFIX_LIMIT + 2):
        candidate = f"{base[: MAX_FRIENDLY_URL_LENGTH - len(str(suffix)) - 1]}-{suffix}"
        if not _friendly_url_is_taken(store, candidate):
            return candidate, True
    raise RoomCreationError(
        "friendly_url_taken",
        f"could not derive a free friendly URL from {name!r} after {_DERIVED_SUFFIX_LIMIT} attempts",
        status=409,
    )


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #


def _clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise RoomCreationError("invalid_name", "a room name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise RoomCreationError(
            "invalid_name", f"room name must be at most {MAX_NAME_LENGTH} characters, got {len(name)}"
        )
    return name


def create_room(
    store: RecordStore,
    *,
    name: Any,
    account_id: Any,
    template_id: Any,
    friendly_url: Any = None,
    actor: str | None = None,
    created_by: str | None = None,
    created_by_username: str | None = None,
    extra: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Create one room and the one site it is bound to, atomically.

    The room and its site are written through a single
    ``db.transaction()`` writer, so a failure after the room is written cannot
    leave a room that has no site. Each write still gets its own audit row, and
    both are stamped with the same ``request_id`` so the pair can be traced.
    """
    room_name = _clean_name(name)

    account = store.db.get(str(account_id or "")) if account_id else None
    if account is None or account["collection"] != ACCOUNT or account.get("deleted_at"):
        raise RoomCreationError(
            "account_not_found",
            f"no live {ACCOUNT} record with id {account_id!r}; create one before binding a room to it",
            status=404,
        )

    resolved_template = find_template(store, str(template_id or ""))
    if resolved_template is None:
        raise RoomCreationError(
            "template_not_found",
            f"no template with id {template_id!r}; GET /api/room-templates lists the available ids",
        )

    # Caller-supplied fields land last so a caller cannot overwrite the bindings
    # this workflow is responsible for by accident.
    payload: dict[str, Any] = {
        **(dict(extra) if extra else {}),
        "name": room_name,
        "status": DEFAULT_STATUS,
        "account_id": account["id"],
        "account_name": account["data"].get("name"),
        "template_id": resolved_template["template_id"],
        "template_version_id": resolved_template["template_version_id"],
        "template_name": resolved_template.get("name"),
        "template_source": resolved_template.get("source"),
        "created_by": created_by or actor or "unknown",
    }
    if created_by_username:
        payload["created_by_username"] = created_by_username

    with store.db.transaction(actor=actor, source="POST /api/rooms", request_id=request_id) as tx:
        # Resolved inside the transaction: the uniqueness check and the insert
        # then share one write lock, so two operators cannot claim the same
        # friendly URL by racing each other.
        url, derived = _resolve_friendly_url(store, room_name, friendly_url)

        # The room id is minted here rather than left to the wrapper so the site
        # can be written room-scoped in the same block. Two writes, one commit:
        # there is no intermediate state in which a room exists without its site.
        # Both ids come from one random token so the room's Site ID key and the
        # site that serves it cannot drift apart.
        token = new_id("")
        room_id = f"{ROOM}_{token}"
        site_id = f"{SITE}_{token}"

        room = tx.create(
            ROOM,
            {
                **payload,
                "friendly_url": url,
                "friendly_url_source": "derived" if derived else "operator",
                "site_id": site_id,
            },
            record_id=room_id,
            actor=actor,
        )
        site = tx.create(
            SITE,
            {
                "name": room_name,
                "friendly_url": url,
                "status": DEFAULT_STATUS,
                "template_id": resolved_template["template_id"],
            },
            record_id=site_id,
            room_id=room["id"],
            actor=actor,
        )

    return {**room, "site": site}


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


def _searchable_text(record: Mapping[str, Any]) -> str:
    data = record.get("data") or {}
    return " ".join(
        str(data.get(key, ""))
        for key in ("name", "friendly_url", "account_name", "template_name")
    ).lower()


def list_rooms(
    store: RecordStore,
    *,
    status: str = DEFAULT_STATUS,
    query: str | None = None,
    account_id: str | None = None,
    template_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """The Rooms list: one status at a time, plus an optional free-text search.

    ``status`` defaults to ``active`` because that is what the list shows by
    default; pass ``all`` to include archived rooms. Exact filters go through
    the dynamic index; the free-text ``query`` is matched in Python because the
    index answers exact matches only, so it is applied to at most
    ``_SCAN_LIMIT`` records.
    """
    normalised_status = (status or DEFAULT_STATUS).strip().lower()
    if normalised_status not in (*STATUSES, "all"):
        raise RoomCreationError(
            "invalid_status", f"status must be one of {list(STATUSES)} or 'all', got {status!r}"
        )

    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    exact: dict[str, Any] = {}
    if normalised_status != "all":
        exact["status"] = normalised_status
    if account_id:
        exact["account_id"] = account_id
    if template_id:
        exact["template_id"] = template_id

    if exact:
        records: Sequence[Mapping[str, Any]] = store.find(ROOM, exact, limit=_SCAN_LIMIT)
    else:
        records = store.list(ROOM, limit=_SCAN_LIMIT, order_by="created_at", descending=False)

    needle = (query or "").strip().lower()
    if needle:
        records = [record for record in records if needle in _searchable_text(record)]

    ordered = sorted(records, key=lambda record: record["created_at"], reverse=True)
    page = list(ordered[offset : offset + limit])
    return {
        "rooms": page,
        "count": len(page),
        "total": len(ordered),
        "status": normalised_status,
        "limit": limit,
        "offset": offset,
    }
