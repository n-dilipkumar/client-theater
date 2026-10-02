"""WF-001: turn a template into a Digital Sales Room.

The researched flow (`docs/research/digital-sales-room-workflows/wf/WF-001.md`) is
a three-step wizard — pick the account, pick a template, name the room — whose
result is "a new DSR entry bound to a newly created Liferay site (one site per
room; the site is what later supplies the *Site ID* integration key)". Two
consequences shape this module:

* **The account binding is load-bearing.** It is what makes invite-by-email
  resolve without a separate directory lookup, so ``account_id`` is a first-class
  field on the room and is resolved against a live ``account`` record rather than
  taken on trust.
* **Template identity is a pair.** The vendor inventory API the research cites
  returns ``digitalSalesRoomTemplateId`` *and*
  ``digitalSalesRoomTemplateVersionId``, so a room pins the specific version of
  the template it was created from.

Nothing here fixes a schema. Every field is written through
:class:`~dsr.store.RecordStore` into the open JSON ``data`` object, so a team can
send a field the server has never seen and it is stored and indexed without a
migration. The only fixed vocabulary is the record envelope plus the handful of
keys this workflow itself needs, which are documented in
`docs/wf-001.md`.

**Why this module is not `dsr.rooms`.** The source branch
(`feature/WF-001-create-room-from-template`) put this in `backend/dsr/rooms.py`,
and WF-005 adds `backend/dsr/rooms.py` too. It was decided, by measurement and
confirmed by Jev, that **WF-005 keeps that name**: it is the larger module and
defines the room *state machine* — ``RoomWorkflow``, ``status_of``,
``available_actions``, ``capabilities_for``, ``Principal`` — which is a room's own
vocabulary. So this workflow owns ``room_templates`` and deliberately does **not**
import a room's status vocabulary from a module that does not exist yet. The two
``status`` values below are this workflow's own field on its own record, kept
here so the Rooms list has something to filter on, and they are not a state
machine: there are no transitions here, and WF-005 owns those. The storage layer
is schema-flexible by design, so a team that wants a third status writes a
record and a filter of their own without coordinating with anyone.

Two decisions here were made by Jev rather than by taste, and are recorded in
`orchestration/decisions/jev-audit.jsonl`:

* ``room`` and its ``site`` are written in one transaction, so a room can never
  exist without the site it is bound to (audit
  ``jev-20260925T214101-11156-61605``).
* The template catalogue is the shipped set overlaid with any ``template``
  records in the store, so a deployment has templates out of the box and a team
  can still add one with no code change (audit
  ``jev-20260925T214101-11156-61939``).

The transaction primitive those first decision needs,
``AuditedDatabase.transaction()``, is now part of the host (``PR #41``, commit
``182f56e``). This module only uses it; it does not define it and does not edit
the audit core.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from dsr.db.audited import new_id
from dsr.store import RecordStore

ROOM = "room"
SITE = "site"
TEMPLATE = "template"
ACCOUNT = "account"

#: The status a room is created with, and the two values this workflow's own
#: records use. The researched Rooms list filters on one status at a time and
#: defaults to Active. WF-005 owns the room state machine proper; see the module
#: docstring.
DEFAULT_STATUS = "active"
STATUSES = ("active", "archived")

MAX_NAME_LENGTH = 200
MAX_FRIENDLY_URL_LENGTH = 64

# ``find()`` is exact-match only, because that is what the dynamic index can
# answer. Free-text search over the filtered set is done in Python, so it needs a
# ceiling. The same ceiling bounds the template overlay scan.
SCAN_LIMIT = 1000
DERIVED_SUFFIX_LIMIT = 50

#: The template set shipped with the app. ``template_id`` / ``template_version_id``
#: are a pair on purpose: a room pins a specific version of the template it was
#: created from, which is the same shape the vendor inventory API exposes as
#: digitalSalesRoomTemplateId + digitalSalesRoomTemplateVersionId.
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
    "why the wizard refused" to "what the operator sees" is decided in one place
    instead of at the route, and a machine-readable ``code`` the wizard can use to
    send the operator back to the step that caused the refusal.
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
    for record in store.list(TEMPLATE, limit=SCAN_LIMIT, order_by="id", descending=False):
        projected = _template_from_record(record)
        if projected["template_id"]:
            catalogue[projected["template_id"]] = projected
    return list(catalogue.values())


def find_template(store: RecordStore, template_id: str) -> dict[str, Any] | None:
    """One template by ``template_id``, or ``None``."""
    for template in list_templates(store):
        if template["template_id"] == template_id:
            return template
    return None


# --------------------------------------------------------------------------- #
# Friendly URLs
# --------------------------------------------------------------------------- #


def slugify(value: str) -> str:
    """Lowercase ASCII slug, accent-folded, with runs collapsed to one hyphen."""
    folded = unicodedata.normalize("NFKD", value or "")
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return _SLUG_STRIP.sub("-", ascii_text).strip("-")


def _validate_friendly_url(candidate: str, *, source: str) -> str:
    """Refuse a candidate that is not a usable, bounded URL segment.

    ``source`` names the input the candidate came from, so the message tells the
    operator whether their own typing or the room name was the problem.
    """
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


def _derived_base(name: str) -> str:
    """The slug a room's name yields, truncated to the Friendly URL limit.

    Truncating rather than refusing is deliberate, and it is what the branch got
    wrong. The research documents the Friendly URL as **optional**: an operator
    who left it blank has not asked for a URL at all, so a long room name must
    still be creatable. The branch refused the whole creation with
    ``invalid_friendly_url`` whenever the derived slug exceeded the limit, which
    made a 200-character room name impossible — a refusal about a field nobody
    filled in. It was also inconsistent with itself: the *suffixed* candidates
    below were already truncated to fit, so the same name could be created once
    its URL collided and not created when it did not.

    Decided by Jev (``choose_approach``, audit
    ``jev-20260927T140106-864-66335``, confidence 0.99, margin 1.00 over the
    runner-up) against refusing the creation and against lowering the name limit
    to the URL limit.

    Only the *derived* URL is truncated. An operator-typed URL that is over-long
    or malformed is still refused by :func:`_validate_friendly_url`: they asked
    for that exact address, so quietly handing them a different one is the
    failure mode worth avoiding.
    """
    base = slugify(name)
    if not base:
        raise RoomCreationError(
            "invalid_friendly_url",
            "The room name does not contain any characters a friendly URL can use",
        )
    if len(base) > MAX_FRIENDLY_URL_LENGTH:
        # The cut can land on a hyphen, which would leave a trailing separator.
        base = base[:MAX_FRIENDLY_URL_LENGTH].rstrip("-")
    return base


def _resolve_friendly_url(store: RecordStore, name: str, requested: str | None) -> tuple[str, bool]:
    """Return ``(friendly_url, derived)``.

    An operator-supplied URL that collides is an error: silently rewriting what
    someone typed would give them a room at an address they did not ask for. A
    URL derived from the name has no such intent, so it gets a numeric suffix
    until it is free — the same thing a platform does with a site name.

    Must be called inside the transaction that writes the room, so the
    uniqueness check and the insert share one write lock and two operators cannot
    claim the same URL by racing each other.
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

    base = _derived_base(name)
    if not _friendly_url_is_taken(store, base):
        return base, True
    for suffix in range(2, DERIVED_SUFFIX_LIMIT + 2):
        candidate = f"{base[: MAX_FRIENDLY_URL_LENGTH - len(str(suffix)) - 1]}-{suffix}"
        if not _friendly_url_is_taken(store, candidate):
            return candidate, True
    raise RoomCreationError(
        "friendly_url_taken",
        f"could not derive a free friendly URL from {name!r} after {DERIVED_SUFFIX_LIMIT} attempts",
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
            "invalid_name",
            f"room name must be at most {MAX_NAME_LENGTH} characters, got {len(name)}",
        )
    return name


def _resolve_account(store: RecordStore, account_id: Any) -> dict[str, Any]:
    """The live ``account`` record a room is bound to, or a refusal.

    A record that exists but is in another collection is refused rather than
    accepted: the wizard says "select the account", so anything that is not an
    account is a wrong answer, not a lenient one.
    """
    account = store.db.get(str(account_id or "")) if account_id else None
    if account is None or account["collection"] != ACCOUNT or account.get("deleted_at"):
        raise RoomCreationError(
            "account_not_found",
            f"no live {ACCOUNT} record with id {account_id!r}; create one before binding a room to it",
            status=404,
        )
    return account


def create_room(
    store: RecordStore,
    *,
    name: Any,
    account_id: Any,
    template_id: Any,
    source: str,
    friendly_url: Any = None,
    actor: str | None = None,
    created_by: str | None = None,
    created_by_username: str | None = None,
    extra: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Create one room and the one site it is bound to, atomically.

    The room and its site are written through a single ``db.transaction()``
    writer, so a failure after the room is written cannot leave a room that has
    no site. Each write still gets its own audit row, and both are stamped with
    the same ``request_id``, so the pair reads back as a unit.

    ``source`` is a required keyword rather than a constant: it is the route that
    wrote the record, and an audit row that names a route nobody can call is
    worse than no route at all. The caller builds it from its own ``router.prefix``.

    ``extra`` carries the fields this workflow does not own. They are merged
    *first*, so a caller cannot overwrite the bindings this workflow is
    responsible for by accident, and the storage layer strips the reserved
    envelope keys from them.
    """
    room_name = _clean_name(name)
    account = _resolve_account(store, account_id)

    resolved_template = find_template(store, str(template_id or ""))
    if resolved_template is None:
        raise RoomCreationError(
            "template_not_found",
            f"no template with id {template_id!r}; the room-templates route lists the available ids",
        )

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

    with store.db.transaction(actor=actor, source=source, request_id=request_id) as tx:
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
