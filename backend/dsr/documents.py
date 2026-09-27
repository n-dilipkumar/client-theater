"""The room document library (WF-003).

Every Digital Sales Room has a Documents view listing "the files in the room's
documents folder", and each row shows "a thumbnail, who last modified it, and
its workflow status". This module is that library: it decides what the folder
contains, who may write to it, and what a row renders.

Shape of the data
-----------------
Documents are ordinary records in the ``document`` collection, scoped to a
room by the envelope's ``room_id`` field, with a free-form JSON payload. There
is no document table, no ``status`` column, and no migration to add one: a team
that needs ``contract_value`` or ``legal_reviewer`` on its documents writes
those keys and the dynamic index makes them queryable the same day.

Because a room can hold other assets, each document also carries ``folder``.
Only the canonical documents folder is listed. This is the documented
behaviour worth copying: "Images added through other fragments are stored
outside the room's documents folder and don't appear in the Documents view."
One canonical library, deliberately not a catch-all.

Sourced vs. inferred
--------------------
Rules marked *sourced* come from the primary documentation recorded in
``docs/research/digital-sales-room-workflows/wf/WF-003.md``. Rules marked
*inference* have no primary source and are a design choice:

* Ingestion returns ``status: "Draft"`` and publishing moves it to
  ``Published`` (sourced, from the Seismic add-a-file and publish operations).
  ``in_review`` and the transition graph between statuses are an *inference*.
* ``thumbnail_url`` is empty until rendering finishes: "thumbnail rendering is
  asynchronous and may lag the upload by several seconds" (sourced).
* ``expires_at`` is a stored date, not a job: "After this date the file is
  treated as expired in consumer surfaces. Must be a future date when set."
  (sourced).
* The Document Gallery Block has "a fixed set of four document selectors" and
  showing more than four means adding another block (sourced).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.permissions import ROOM_ARCHIVED, Capabilities, capabilities
from dsr.store import RecordStore

COLLECTION = "document"
GALLERY_COLLECTION = "document_gallery"

#: The room's canonical documents folder. Only documents filed here appear in
#: the Documents view.
FOLDER = "documents"

#: Sourced: the Document Gallery Block has four fixed document selectors.
GALLERY_SLOTS = 4

STATUS_DRAFT = "draft"
STATUS_IN_REVIEW = "in_review"
STATUS_PUBLISHED = "published"

#: A documented vocabulary, used for display and for the transition map below.
#: It is deliberately *not* enforced: an unknown status is accepted, stored,
#: and rendered, because a team must be able to add one without us.
KNOWN_STATUSES: frozenset[str] = frozenset({STATUS_DRAFT, STATUS_IN_REVIEW, STATUS_PUBLISHED})
DEFAULT_STATUS = STATUS_DRAFT

#: Inference: the graph between the documented states. ``draft -> published``
#: is sourced (Seismic publish). ``in_review`` and the way back to ``draft``
#: are not. A status outside this map may move to anything, so a team's own
#: vocabulary is never blocked by ours.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_IN_REVIEW, STATUS_PUBLISHED}),
    STATUS_IN_REVIEW: frozenset({STATUS_DRAFT, STATUS_PUBLISHED}),
    STATUS_PUBLISHED: frozenset({STATUS_DRAFT}),
}

#: Keys the library owns. A caller may add anything else to a document, but
#: these are set by the library so a row cannot lie about who uploaded it.
_LIBRARY_KEYS = frozenset(
    {
        "folder",
        "name",
        "title",
        "format",
        "status",
        "uploaded_by",
        "uploaded_at",
        "last_modified_by",
        "last_modified_at",
        "thumbnail_url",
        "expires_at",
        "open_in_new_tab",
    }
)

#: Never patchable by a caller: identity of the uploader is what the delete
#: gate is computed from, so letting a request rewrite it would let any
#: contributor delete anyone else's file.
_IMMUTABLE_KEYS = frozenset({"uploaded_by", "uploaded_at"})

_FORMAT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "doc": ("doc", "docx"),
    "sheet": ("xls", "xlsx", "csv"),
    "deck": ("ppt", "pptx", "key"),
    "image": ("png", "jpg", "jpeg", "gif", "svg", "webp"),
    "video": ("mp4", "mov", "webm"),
    "audio": ("mp3", "wav", "m4a"),
    "archive": ("zip", "tar", "gz"),
}
_EXTENSION_FORMATS = {ext: name for name, exts in _FORMAT_EXTENSIONS.items() for ext in exts}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class LibraryError(Exception):
    """Base class for document-library refusals."""


class RoomNotFound(LibraryError, LookupError):
    """The room whose library was addressed does not exist."""


class DocumentNotFound(LibraryError, LookupError):
    """The document id does not resolve inside this room's library."""


class PermissionDenied(LibraryError, PermissionError):
    """The caller's role does not permit this write."""


class InvalidDocument(LibraryError, ValueError):
    """The payload cannot be accepted as written."""


class InvalidTransition(LibraryError, ValueError):
    """The requested workflow status change is not permitted from here."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def guess_format(name: str, explicit: str | None = None) -> str:
    """Best-effort file type from an explicit value or the file extension.

    The Documents view badges each row with its file type, and a team can set
    ``format`` explicitly when the guess is wrong. Guessing is a convenience;
    it never rejects a payload.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip().lower()
    suffix = str(name or "").rsplit(".", 1)
    if len(suffix) == 2:
        return _EXTENSION_FORMATS.get(suffix[1].strip().lower(), suffix[1].strip().lower() or "file")
    return "file"


def _parse_moment(value: str) -> datetime | None:
    """Parse a date or ISO-8601 datetime; return ``None`` if unusable."""
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(text), datetime.min.time())
    except ValueError:
        return None


def _validate_expiry(value: Any, *, now: datetime | None = None) -> str | None:
    """Enforce the documented rule: an expiry must be a future date when set.

    Sourced: ``expiresAt`` "After this date the file is treated as expired in
    consumer surfaces. Must be a future date when set."
    """
    if value is None or value == "":
        return None
    moment = _parse_moment(str(value))
    if moment is None:
        raise InvalidDocument(f"expires_at must be an ISO-8601 date or datetime, got {value!r}")
    reference = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment <= reference:
        raise InvalidDocument("expires_at must be a future date when set")
    return str(value).strip()


def _is_expired(value: Any, *, now: datetime | None = None) -> bool:
    moment = _parse_moment(str(value or ""))
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= (now or datetime.now(timezone.utc))


def _matches_search(data: Mapping[str, Any], needle: str) -> bool:
    haystack = " ".join(
        str(data.get(key, "")) for key in ("name", "title", "description", "format", "uploaded_by")
    ).lower()
    return needle in haystack


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class DocumentLibrary:
    """Read and write one room's document library through the audited store.

    The class holds no state of its own. Every mutation goes through
    :class:`RecordStore`, which is :class:`AuditedDatabase` behind a thin
    façade, so each write lands in the audit log inside the same transaction.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- room ------------------------------------------------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None or room["collection"] != "room":
            raise RoomNotFound(f"room {room_id} not found")
        return room

    @staticmethod
    def room_status(room: Mapping[str, Any]) -> str:
        """The room's lifecycle state, defaulting to active.

        Only ``archived`` is meaningful to the gates; a room with any other
        status is treated as active, so a team that adds lifecycle states does
        not accidentally lock itself out.
        """
        return str(room.get("data", {}).get("status") or "active").strip().lower()

    def capabilities_for(
        self,
        room_id: str,
        *,
        role: Any,
        actor: str | None = None,
        uploaded_by: str | None = None,
    ) -> Capabilities:
        room = self.require_room(room_id)
        return capabilities(
            role,
            room_status=self.room_status(room),
            uploaded_by=uploaded_by,
            actor=actor,
        )

    # -- read -------------------------------------------------------------- #

    def list_documents(
        self,
        room_id: str,
        *,
        search: str = "",
        status: str | None = None,
        include_expired: bool = True,
        limit: int = 100,
        offset: int = 0,
        role: Any = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """The Documents view: rows in the room's documents folder.

        A status filter goes through ``find()`` so it resolves the dotted JSON
        path through the dynamic index, which is how a team's own status field
        stays filterable without a migration. Room scoping and the free-text
        search are applied on the way out, because ``find()`` filters on
        payload paths only.
        """
        room = self.require_room(room_id)
        room_status = self.room_status(room)
        needle = (search or "").strip().lower()

        if status:
            candidates = self.store.find(
                COLLECTION, {"status": status, "folder": FOLDER}, limit=1000
            )
            records = [r for r in candidates if r.get("room_id") == room_id]
        else:
            records = self.store.list(
                COLLECTION,
                room_id=room_id,
                limit=1000,
                order_by="created_at",
                descending=False,
            )

        rows = []
        for record in records:
            data = record.get("data", {})
            if str(data.get("folder") or FOLDER) != FOLDER:
                continue  # stored outside the documents folder, by design
            if needle and not _matches_search(data, needle):
                continue
            rows.append(self._row(record, role=role, actor=actor, room_status=room_status))

        total = len(rows)
        page = rows[max(0, offset) : max(0, offset) + max(1, limit)]
        return {
            "room_id": room_id,
            "room_status": room_status,
            "documents": page,
            "count": len(page),
            "total": total,
            "search": search or "",
            "status": status,
            "capabilities": capabilities(
                role, room_status=room_status, actor=actor
            ).to_dict(),
        }

    def get_document(
        self, room_id: str, document_id: str, *, role: Any = None, actor: str | None = None
    ) -> dict[str, Any]:
        room = self.require_room(room_id)
        record = self._require_in_room(room_id, document_id)
        return self._row(record, role=role, actor=actor, room_status=self.room_status(room))

    def _row(
        self,
        record: Mapping[str, Any],
        *,
        role: Any,
        actor: str | None,
        room_status: str = "active",
    ) -> dict[str, Any]:
        """One Documents-view row: the raw payload plus a derived view.

        ``data`` is returned untouched, because the library makes no promise
        about which fields a team stores. ``library`` is what the view renders,
        computed on read so nothing has to be kept in step.
        """
        data = record.get("data", {})
        name = str(data.get("name") or data.get("title") or record["id"])
        thumbnail_url = str(data.get("thumbnail_url") or "")
        expires_at = data.get("expires_at")
        return {
            "id": record["id"],
            "collection": record["collection"],
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            "data": data,
            "library": {
                "name": name,
                "title": str(data.get("title") or name),
                "folder": str(data.get("folder") or FOLDER),
                "format": guess_format(name, data.get("format")),
                "status": str(data.get("status") or DEFAULT_STATUS),
                "status_known": str(data.get("status") or DEFAULT_STATUS) in KNOWN_STATUSES,
                "uploaded_by": data.get("uploaded_by"),
                "uploaded_at": data.get("uploaded_at"),
                "last_modified_by": data.get("last_modified_by") or data.get("uploaded_by"),
                "last_modified_at": data.get("last_modified_at") or record.get("updated_at"),
                # Sourced: rendering is asynchronous, so an empty url is a real
                # state rather than a missing value.
                "thumbnail": {"state": "ready" if thumbnail_url else "pending", "url": thumbnail_url or None},
                "expires_at": expires_at,
                "expired": _is_expired(expires_at),
                "open_in_new_tab": bool(data.get("open_in_new_tab", True)),
            },
            "permissions": capabilities(
                role,
                room_status=room_status,
                uploaded_by=data.get("uploaded_by"),
                actor=actor,
            ).to_dict(),
        }

    def _require_in_room(self, room_id: str, document_id: str) -> dict[str, Any]:
        record = self.store.get(document_id)
        if record is None or record["collection"] != COLLECTION:
            raise DocumentNotFound(f"document {document_id} not found")
        if record.get("room_id") != room_id:
            # Per-room folder isolation: a document id from another room is not
            # a document of this room, and saying so is the useful answer.
            raise DocumentNotFound(f"document {document_id} is not in room {room_id}")
        return record

    # -- write ------------------------------------------------------------- #

    def add_document(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        role: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """The *New* button's write path.

        Sourced gate: Room Collaborators and Content Contributors only, and in
        an archived room only an instance administrator.

        ``source`` is the audit trail's record of which route served the write,
        so it is a required argument rather than a default: a domain function
        that hard-codes a path records a route the app may not be serving. The
        router passes the path it actually mounted.
        """
        room = self.require_room(room_id)
        room_status = self.room_status(room)
        gate = capabilities(role, room_status=room_status, actor=actor)
        if not gate.can_upload:
            raise PermissionDenied(
                f"role {gate.role_label!r} cannot add documents to this room"
                + (" (room is archived)" if room_status == ROOM_ARCHIVED else "")
            )

        data = dict(payload or {})
        name = str(data.get("name") or data.get("title") or "").strip()
        if not name:
            raise InvalidDocument("a document needs a name or a title")

        status = str(data.get("status") or DEFAULT_STATUS).strip() or DEFAULT_STATUS
        now = utcnow()
        record = self.store.create(
            COLLECTION,
            {
                **data,
                "folder": FOLDER,
                "name": name,
                "format": guess_format(name, data.get("format")),
                "status": status,
                "uploaded_by": actor,
                "uploaded_at": now,
                "last_modified_by": actor,
                "last_modified_at": now,
                "expires_at": _validate_expiry(data.get("expires_at")),
                "open_in_new_tab": bool(data.get("open_in_new_tab", True)),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._row(record, role=role, actor=actor, room_status=room_status)

    def update_document(
        self,
        room_id: str,
        document_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        role: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Edit a document. Any key the library does not own is accepted."""
        room = self.require_room(room_id)
        room_status = self.room_status(room)
        record = self._require_in_room(room_id, document_id)
        gate = capabilities(role, room_status=room_status, actor=actor)
        if not (gate.can_manage_status or _is_owner(record, actor)):
            raise PermissionDenied(f"role {gate.role_label!r} cannot edit documents in this room")

        data = {k: v for k, v in dict(patch or {}).items() if k not in _IMMUTABLE_KEYS}
        if "status" in data:
            self._check_transition(str(record.get("data", {}).get("status") or DEFAULT_STATUS), str(data["status"]))
        if "expires_at" in data:
            data["expires_at"] = _validate_expiry(data["expires_at"])

        data["last_modified_by"] = actor
        data["last_modified_at"] = utcnow()
        updated = self.store.update(
            document_id,
            data,
            actor=actor,
            source=source,
        )
        return self._row(updated, role=role, actor=actor, room_status=room_status)

    def set_status(
        self,
        room_id: str,
        document_id: str,
        status: str,
        *,
        actor: str | None = None,
        role: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Move a document's workflow status, enforcing the transition graph."""
        room = self.require_room(room_id)
        room_status = self.room_status(room)
        record = self._require_in_room(room_id, document_id)
        gate = capabilities(role, room_status=room_status, actor=actor)
        if not gate.can_manage_status:
            raise PermissionDenied(
                f"role {gate.role_label!r} cannot change the workflow status in this room"
            )
        self._check_transition(str(record.get("data", {}).get("status") or DEFAULT_STATUS), status)

        now = utcnow()
        updated = self.store.update(
            document_id,
            {"status": status, "last_modified_by": actor, "last_modified_at": now},
            actor=actor,
            source=source,
        )
        return self._row(updated, role=role, actor=actor, room_status=room_status)

    def remove_document(
        self,
        room_id: str,
        document_id: str,
        *,
        actor: str | None = None,
        role: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Remove a document. Soft delete, so the audit trail keeps its history.

        Sourced gate: neither a Room Collaborator nor a Content Contributor can
        delete a document someone else uploaded.
        """
        room = self.require_room(room_id)
        room_status = self.room_status(room)
        record = self._require_in_room(room_id, document_id)
        gate = capabilities(
            role,
            room_status=room_status,
            uploaded_by=record.get("data", {}).get("uploaded_by"),
            actor=actor,
        )
        if not gate.can_delete:
            if not gate.can_delete_others:
                raise PermissionDenied(
                    f"role {gate.role_label!r} cannot delete a document uploaded by someone else"
                )
            raise PermissionDenied(f"role {gate.role_label!r} cannot delete documents in this room")
        self.store.delete(
            document_id,
            actor=actor,
            source=source,
        )
        return {"id": document_id, "room_id": room_id, "deleted": True, "hard": False}

    # -- workflow status vocabulary ----------------------------------------- #

    @staticmethod
    def _check_transition(current: str, target: str) -> None:
        target = str(target or "").strip()
        if not target:
            raise InvalidTransition("a workflow status is required")
        allowed = ALLOWED_TRANSITIONS.get(current)
        if allowed is None:
            # A status outside our vocabulary is not our state machine to police.
            return
        if target not in allowed:
            raise InvalidTransition(f"cannot move a document from {current!r} to {target!r}")

    def workflow_vocabulary(self) -> dict[str, Any]:
        """The status vocabulary and transitions, for a client that renders them."""
        return {
            "default": DEFAULT_STATUS,
            "known": sorted(KNOWN_STATUSES),
            "transitions": {state: sorted(next_states) for state, next_states in ALLOWED_TRANSITIONS.items()},
            "open": bool(ALLOWED_TRANSITIONS),
        }

    # -- Document Gallery Block -------------------------------------------- #

    def list_galleries(
        self, room_id: str, *, role: Any = None, actor: str | None = None
    ) -> dict[str, Any]:
        """The Document Gallery Blocks placed on the room's pages."""
        self.require_room(room_id)
        blocks = self.store.list(GALLERY_COLLECTION, room_id=room_id, limit=200)
        return {
            "room_id": room_id,
            "slots": GALLERY_SLOTS,
            "blocks": [self._gallery_block(b, room_id) for b in blocks],
            "count": len(blocks),
        }

    def save_gallery(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        block_id: str | None = None,
        actor: str | None = None,
        role: Any = None,
        source: str,
    ) -> dict[str, Any]:
        """Create or replace a gallery block's Document 1-4 selectors.

        Sourced: the block has a fixed set of four selectors, and each one
        "takes one file from the room's documents, the same files listed in the
        room's Documents view". Anything beyond four needs another block.

        One ``source`` covers both outcomes: the caller is a single route that
        answers POST to create and PUT to replace, and it passes the path it is
        actually serving rather than leaving this function to guess.
        """
        room = self.require_room(room_id)
        gate = capabilities(role, room_status=self.room_status(room), actor=actor)
        if not gate.can_manage_status:
            raise PermissionDenied(f"role {gate.role_label!r} cannot edit room pages")

        selected: list[str] = []
        for value in _as_sequence(payload.get("documents")):
            document_id = str(value or "").strip()
            if not document_id or document_id in selected:
                continue
            record = self._require_in_room(room_id, document_id)
            if str(record.get("data", {}).get("folder") or FOLDER) != FOLDER:
                raise InvalidDocument(
                    f"document {document_id} is not in the room's documents folder"
                )
            selected.append(document_id)
        if len(selected) > GALLERY_SLOTS:
            raise InvalidDocument(
                f"a gallery block holds {GALLERY_SLOTS} documents; add another block for the rest"
            )

        body = {
            "label": str(payload.get("label") or "Document Gallery Block"),
            "page": payload.get("page"),
            "documents": selected,
            "open_in_new_tab": bool(payload.get("open_in_new_tab", True)),
        }
        if block_id:
            existing = self.store.get(block_id)
            if existing is None or existing["collection"] != GALLERY_COLLECTION:
                raise DocumentNotFound(f"gallery block {block_id} not found")
            if existing.get("room_id") != room_id:
                raise DocumentNotFound(f"gallery block {block_id} is not in room {room_id}")
            record = self.store.update(
                block_id,
                body,
                actor=actor,
                source=source,
            )
        else:
            record = self.store.create(
                GALLERY_COLLECTION,
                body,
                room_id=room_id,
                actor=actor,
                source=source,
            )
        return self._gallery_block(record, room_id)

    def _gallery_block(self, record: Mapping[str, Any], room_id: str) -> dict[str, Any]:
        data = record.get("data", {})
        documents = []
        for document_id in _as_sequence(data.get("documents")):
            found = self.store.get(str(document_id))
            if found is None or found.get("room_id") != room_id:
                continue
            documents.append(
                {
                    "id": found["id"],
                    "name": str(found.get("data", {}).get("name") or found["id"]),
                    "title": str(found.get("data", {}).get("title") or found.get("data", {}).get("name") or found["id"]),
                    "format": guess_format(
                        str(found.get("data", {}).get("name") or ""),
                        found.get("data", {}).get("format"),
                    ),
                    "thumbnail_url": found.get("data", {}).get("thumbnail_url") or None,
                    "status": str(found.get("data", {}).get("status") or DEFAULT_STATUS),
                }
            )
        return {
            "id": record["id"],
            "room_id": room_id,
            "revision": record.get("revision"),
            "label": data.get("label"),
            "page": data.get("page"),
            "slots": GALLERY_SLOTS,
            "open_in_new_tab": bool(data.get("open_in_new_tab", True)),
            "documents": documents,
            "empty_slots": max(0, GALLERY_SLOTS - len(documents)),
        }


def _as_sequence(value: Any) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    if isinstance(value, str):
        return [part for part in re.split(r"[,\s]+", value) if part]
    return (value,)


def _is_owner(record: Mapping[str, Any], actor: str | None) -> bool:
    uploader = record.get("data", {}).get("uploaded_by")
    return bool(actor) and uploader is not None and uploader == actor


__all__ = [
    "COLLECTION",
    "GALLERY_COLLECTION",
    "GALLERY_SLOTS",
    "FOLDER",
    "KNOWN_STATUSES",
    "STATUS_DRAFT",
    "STATUS_IN_REVIEW",
    "STATUS_PUBLISHED",
    "ALLOWED_TRANSITIONS",
    "DocumentLibrary",
    "DocumentNotFound",
    "InvalidDocument",
    "InvalidTransition",
    "LibraryError",
    "PermissionDenied",
    "RoomNotFound",
    "capabilities",
    "guess_format",
]
