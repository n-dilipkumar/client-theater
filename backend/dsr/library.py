"""The content library: ingest a document or deck, and version it.

This module implements the capability researched as WF-007. The source product
exposes ``POST /teamsites/{teamsiteId}/files`` as ``multipart/form-data`` with a
``metadata`` JSON string part and a ``content`` binary part. What is portable is
the *shape* of that contract, and this module keeps every part of it that has
independent value:

============================  ===============================================
Researched behaviour          How it is honoured here
============================  ===============================================
``name`` required             400 when absent. The UI pre-fills it from the
                              chosen file so the human never retypes it.
``format`` required           Derived from the filename extension when not
                              sent, and rejected only when neither is
                              available. Divergence, see the report.
``parentFolderId``            ``root`` or a folder record id in the same room.
``root`` keyword              ``parentFolderId="root"`` addresses the room root.
``resolveNameCollision``      Appends ``(1)``, ``(2)`` ... until the name is
                              free *in the target folder only*. Off means a
                              conflict is a 409, not a silent rename.
``rollbackOnError``           Default true. A binary that fails to land leaves
                              no orphaned draft entry in the folder.
New version, not replace      The binary of an existing document is never
                              overwritten. ``add_version`` appends.
Asynchronous thumbnail        ``thumbnailStatus`` starts ``pending`` and the
                              derived asset appears only when asked for, so a
                              client has to poll, exactly as the source does.
``externalId``                Stored verbatim and indexed, for correlation with
                              an external record such as a CRM deal.
``properties[]``              Accepted as ``{id, value}`` pairs or as a plain
                              object. No registry, so a team can require its own
                              ingest metadata without coordinating with anyone.
``expiresAt``                 Stored verbatim. Expiry is a policy decision, not
                              an ingest one, so nothing is scheduled here.
============================  ===============================================

Two storage decisions are worth stating outright.

**The bytes are not in the database.** A 2 GB file base64-encoded into
``records.data`` would be written to the audit log too, twice, and would make
the audited store unusable. The binary lives on the filesystem under a
configured content root; the audited record holds a storage key, a size, and a
SHA-256 checksum. Jev selected this over the three alternatives (base64 in
``data``, a SQLite BLOB column, external object storage) at confidence 1.00 on
the original branch, and that judgement is why this layout survives the port.

**Every database write still goes through** ``RecordStore`` **->**
``AuditedDatabase``. The blob write is a filesystem step, not a database one, so
it cannot bypass the audit log; the record and its audit row are still written
in a single transaction by the audited wrapper. The ordering is deliberate and
mirrors the source: the draft record is created *first*, then the binary lands,
then the record is completed. That is what makes ``rollbackOnError`` mean the
thing the documentation says it means.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from dsr.db.audited import new_id
from dsr.store import RecordStore

#: The researched upload ceiling. Overridable so a test or a constrained
#: deployment can lower it without touching code.
DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

#: Chunk size for streaming the binary to disk. Large enough to keep syscall
#: overhead irrelevant, small enough that memory use is independent of file size.
CHUNK_BYTES = 1024 * 1024

DOCUMENT_COLLECTION = "document"
FOLDER_COLLECTION = "documentFolder"
ROOM_COLLECTION = "room"

#: The researched keyword for "the top of the teamsite", i.e. the room root.
ROOT_FOLDER = "root"

#: Where the thumbnail route lives, used to build ``thumbnailUrl``. The feature
#: router passes its own prefix so the two cannot drift; the value here is only
#: the default for a library built directly, as the tests do.
DEFAULT_THUMBNAIL_URL_BASE = "/api/library/documents"

#: The repository an ingested item belongs to. A discriminator, not a schema:
#: the ``document`` collection is open, so other teams may have their own
#: records under the same name and the library lists only what it ingested.
LIBRARY_REPOSITORY = "library"

#: A freshly ingested item is an unpublished draft at version 0.1.
INITIAL_VERSION = "0.1"

_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")
_COLLISION_INDEX = re.compile(r"^(?P<stem>.*?)(?:\s\((?P<index>\d+)\))?$")


class LibraryError(RuntimeError):
    """An ingest failure with an HTTP status attached.

    Carrying the status on the exception keeps the router free of translation
    tables and lets the domain be tested without an HTTP client.
    """

    status_code = 400
    code = "library_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ValidationError(LibraryError):
    status_code = 400
    code = "invalid_metadata"


class NotFound(LibraryError):
    status_code = 404
    code = "not_found"


class Conflict(LibraryError):
    status_code = 409
    code = "conflict"


class UploadTooLarge(LibraryError):
    status_code = 413
    code = "upload_too_large"


class BlobStorageError(LibraryError):
    """The filesystem refused the write. Recoverable; never a silent success."""

    status_code = 500
    code = "blob_storage_error"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def derive_format(filename: str) -> str | None:
    """Best-effort format from a filename, e.g. ``Q2 Deck.pptx`` -> ``pptx``."""
    suffix = Path(filename or "").suffix
    return suffix[1:].lower() if len(suffix) > 1 else None


def split_name(name: str) -> tuple[str, str]:
    """Split a document name into ``(stem, suffix)``, extension included.

    ``Q2 Sales Deck.pptx`` -> ``("Q2 Sales Deck", ".pptx")``
    ``Q2 Sales Deck``     -> ``("Q2 Sales Deck", "")``
    """
    path = Path(name)
    return path.stem, path.suffix


def collision_name(name: str, index: int) -> str:
    """The ``index``-th de-collided form of ``name``.

    Mirrors the documented behaviour: an index is appended, the extension is
    kept, and a name that already ends in ``(n)`` does not accumulate
    parentheses, so repeated collisions walk 1, 2, 3 rather than (1) then (1)(2).
    """
    stem, suffix = split_name(name)
    match = _COLLISION_INDEX.match(stem)
    if match and match.group("index") is not None:
        stem = match.group("stem")
    return f"{stem} ({index}){suffix}"


def join_path(prefix: str, name: str) -> str:
    """Build a materialized library path: ``/Q2 Decks`` + ``Deck.pptx``."""
    return f"{prefix.rstrip('/')}/{name.lstrip('/')}" if prefix else f"/{name}"


def _normalise_properties(raw: Any) -> dict[str, Any]:
    """Accept either ``{id: value}`` or ``[{id, value}, ...]`` as properties.

    The source API documents an array of ``{id, value}`` objects, and the open
    form is more natural for a local caller. Both land in the same object so the
    dotted-path index can answer ``properties.audience`` either way.
    """
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    result: dict[str, Any] = {}
    for item in raw:
        if isinstance(item, Mapping) and "id" in item:
            result[str(item["id"])] = item.get("value")
        else:
            raise ValidationError(f"properties entries must be {{id, value}} objects, got {item!r}")
    return result


def _normalise_experts(raw: Any) -> list[dict[str, Any]]:
    """Validate the expert references: ``type`` must be ``user`` or ``group``."""
    if not raw:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValidationError("experts must be a list of {type, id} objects")
    experts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValidationError(f"expert entries must be objects, got {item!r}")
        kind = str(item.get("type") or "").lower()
        if kind not in ("user", "group"):
            raise ValidationError(
                f"expert type must be 'user' or 'group', got {item.get('type')!r}"
            )
        identifier = item.get("id")
        if not identifier:
            raise ValidationError("expert entries require an id")
        experts.append({**dict(item), "type": kind, "id": str(identifier)})
    return experts


def parse_version(version: str) -> tuple[int, int]:
    """Parse ``"0.2"`` into ``(0, 2)``; an unparsable version starts at 1."""
    match = re.match(r"^(\d+)(?:\.(\d+))?$", str(version or ""))
    if not match:
        return 0, 1
    return int(match.group(1)), int(match.group(2) or 0)


def format_version(major: int, minor: int) -> str:
    return f"{major}.{minor}"


def _safe_segment(value: str) -> str:
    """Reduce an identifier to a single filesystem-safe path segment.

    Storage keys are built from ids this module generates, so this is belt and
    braces: no caller-supplied string can ever reach the filesystem path.
    """
    cleaned = _SAFE_KEY.sub("_", value).strip("._-")
    return cleaned or "x"


def _xml_escape(value: Any) -> str:
    text = str(value)
    for needle, replacement in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&apos;"),
    ):
        text = text.replace(needle, replacement)
    return text


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #


class ContentLibrary:
    """Ingest, version, and read documents in the content library.

    All persistence of *metadata* goes through the injected
    :class:`~dsr.store.RecordStore`, so every metadata mutation is audited in the
    same transaction as the change. Binary payloads are streamed to
    ``content_root`` and never into SQLite.
    """

    def __init__(
        self,
        store: RecordStore,
        content_root: str | Path,
        *,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        chunk_bytes: int = CHUNK_BYTES,
        collision_limit: int = 100,
        thumbnail_url_base: str = DEFAULT_THUMBNAIL_URL_BASE,
    ) -> None:
        self.store = store
        self.content_root = Path(content_root)
        self.max_upload_bytes = int(max_upload_bytes)
        self.chunk_bytes = max(1, int(chunk_bytes))
        self.collision_limit = max(1, int(collision_limit))
        # The public URL of the thumbnail route is configuration, not a constant
        # baked into the domain. The router that serves it passes its own prefix
        # here, so a document can never be handed a URL the app does not serve --
        # the same defect the write ``source`` used to have, in a different field.
        self.thumbnail_url_base = str(thumbnail_url_base).rstrip("/")

    # -- lookups ------------------------------------------------------------ #

    def require_room(self, room_id: str) -> dict[str, Any]:
        """Resolve the teamsite equivalent: the room that scopes everything."""
        record = self.store.get(room_id)
        if record is None or record["collection"] != ROOM_COLLECTION:
            raise NotFound(f"room {room_id!r} not found")
        return record

    def get_document(self, document_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self.store.get(document_id)
        if record is None or record["collection"] != DOCUMENT_COLLECTION:
            raise NotFound(f"document {document_id!r} not found")
        if room_id is not None and record["room_id"] != room_id:
            raise NotFound(f"document {document_id!r} is not in room {room_id!r}")
        return record

    def list_documents(
        self,
        room_id: str,
        *,
        parent_folder_id: str | None = None,
        where: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List a room's library documents, optionally scoped to a folder.

        Folder scoping goes through ``find()`` so it resolves the dotted JSON
        path through the dynamic index. That is the mechanism a team relies on
        to filter on a field no schema declares, and this workflow uses it for
        itself rather than keeping a private index.

        ``repository: library`` is a base filter, not a schema constraint. The
        collection is open, so a team may well have other records called
        ``document``; the library lists the ones it ingested and leaves the rest
        alone.
        """
        filters: dict[str, Any] = {"repository": LIBRARY_REPOSITORY, **dict(where or {})}
        if parent_folder_id is not None:
            filters["parentFolderId"] = parent_folder_id
        if filters:
            records = self.store.find(DOCUMENT_COLLECTION, filters, limit=max(limit, 1000))
            return [r for r in records if r["room_id"] == room_id][:limit]
        return self.store.list(DOCUMENT_COLLECTION, room_id=room_id, limit=limit)

    def list_folders(self, room_id: str) -> list[dict[str, Any]]:
        return self.store.list(FOLDER_COLLECTION, room_id=room_id, limit=1000)

    # -- folders ------------------------------------------------------------ #

    def create_folder(
        self,
        room_id: str,
        name: str,
        *,
        parent_folder_id: str = ROOT_FOLDER,
        actor: str | None = None,
        source: str = "library",
        **extra: Any,
    ) -> dict[str, Any]:
        """Create a folder in the library hierarchy.

        The materialized path is computed once, at creation. Folders are not
        re-parented in this workflow, so a path can never become cyclic.
        """
        self.require_room(room_id)
        clean = str(name or "").strip()
        if not clean:
            raise ValidationError("folder name is required")
        parent_path, _ = self._resolve_folder(room_id, parent_folder_id)
        data = {
            "name": clean,
            "parentFolderId": parent_folder_id or ROOT_FOLDER,
            "path": join_path(parent_path, clean),
            **extra,
        }
        return self.store.create(
            FOLDER_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )

    def _resolve_folder(
        self, room_id: str, parent_folder_id: str | None
    ) -> tuple[str, dict[str, Any] | None]:
        """Return ``(materialized_path, folder_record)`` for a folder reference.

        ``None`` and ``root`` both address the room root, which has no record of
        its own and an empty path.
        """
        reference = (parent_folder_id or ROOT_FOLDER).strip()
        if not reference or reference == ROOT_FOLDER:
            return "", None
        record = self.store.get(reference)
        if record is None or record["collection"] != FOLDER_COLLECTION:
            raise ValidationError(f"parentFolderId {reference!r} does not name a library folder")
        if record["room_id"] != room_id:
            raise ValidationError(f"folder {reference!r} belongs to a different room")
        return str(
            record["data"].get("path") or join_path("", str(record["data"].get("name") or ""))
        ), record

    # -- name collisions ---------------------------------------------------- #

    def _names_in_folder(self, room_id: str, parent_folder_id: str) -> set[str]:
        records = self.store.find(
            DOCUMENT_COLLECTION,
            {"repository": LIBRARY_REPOSITORY, "parentFolderId": parent_folder_id},
            limit=1000,
        )
        return {str(r["data"].get("name") or "") for r in records if r["room_id"] == room_id}

    def _apply_name_collision(
        self, room_id: str, parent_folder_id: str, name: str, resolve: bool
    ) -> str:
        """Return the name to store, honouring ``resolveNameCollision``."""
        taken = self._names_in_folder(room_id, parent_folder_id)
        if name not in taken:
            return name
        if not resolve:
            raise Conflict(
                f"a document named {name!r} already exists in this folder; "
                "retry with resolveNameCollision=true to store it as a new version-named copy"
            )
        for index in range(1, self.collision_limit + 1):
            candidate = collision_name(name, index)
            if candidate not in taken:
                return candidate
        raise Conflict(
            f"could not find a free name for {name!r} after {self.collision_limit} attempts"
        )

    # -- ingest ------------------------------------------------------------- #

    def ingest(
        self,
        room_id: str,
        content: BinaryIO,
        filename: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        resolve_name_collision: bool = True,
        rollback_on_error: bool = True,
        actor: str | None = None,
        request_id: str | None = None,
        source: str = "library ingest",
    ) -> dict[str, Any]:
        """Ingest one binary plus its metadata part, and return the record.

        The sequence is create-draft, persist-binary, complete-draft. That
        ordering is the whole point of ``rollback_on_error``: when the binary
        cannot land, either the draft is removed so the folder shows no orphaned
        entry, or the draft is kept and marked failed so a human can retry it.
        """
        self.require_room(room_id)
        fields = dict(metadata or {})

        name = str(fields.pop("name", "") or "").strip()
        if not name:
            raise ValidationError("name is required")

        declared_format = fields.pop("format", None)
        file_format = derive_format(filename) or derive_format(name)
        resolved_format = str(declared_format or file_format or "").strip().lower()
        if not resolved_format:
            raise ValidationError("format is required and could not be derived from the filename")

        parent_folder_id = fields.pop("parentFolderId", None) or ROOT_FOLDER
        folder_path, _ = self._resolve_folder(room_id, parent_folder_id)

        stored_name = self._apply_name_collision(
            room_id, parent_folder_id, name, resolve_name_collision
        )

        properties = _normalise_properties(fields.pop("properties", None))
        experts = _normalise_experts(fields.pop("experts", None))
        passthrough = {k: v for k, v in fields.items() if v is not None}

        document_id = new_id(DOCUMENT_COLLECTION)
        version_id = new_id("version")
        key = f"{_safe_segment(document_id)}/{_safe_segment(version_id)}"

        data: dict[str, Any] = {
            # Anything the caller invented is kept as-is. No migration, no
            # allowlist: a team adding a field must not need coordination with
            # anyone. It goes in *first* so the library's own bookkeeping below
            # cannot be forged by a metadata key, while a new team field still
            # just works.
            **passthrough,
            "name": stored_name,
            "format": resolved_format,
            "formatSource": "metadata" if declared_format else "filename",
            "parentFolderId": parent_folder_id,
            "libraryMaterializedPath": join_path(folder_path, stored_name),
            "type": "file",
            "repository": LIBRARY_REPOSITORY,
            "status": "Draft",
            "version": INITIAL_VERSION,
            "versionId": version_id,
            "majorVersion": 0,
            "minorVersion": 1,
            "versions": [],
            "ingestState": "pending",
            "thumbnailUrl": "",
            "thumbnailStatus": "pending",
            "assignedToProfiles": [],
            "properties": properties,
            "experts": experts,
            "origFilename": Path(filename or stored_name).name,
            "storage": {"key": key, "bytes": None, "checksum": None, "algorithm": "sha256"},
            "resolveNameCollision": bool(resolve_name_collision),
            "rollbackOnError": bool(rollback_on_error),
            "ingestedAt": utcnow(),
        }
        if stored_name != name:
            data["requestedName"] = name
        if declared_format is None:
            data["formatDerived"] = True

        record = self.store.create(
            DOCUMENT_COLLECTION,
            data,
            record_id=document_id,
            room_id=room_id,
            actor=actor,
            source=source,
            request_id=request_id,
        )

        try:
            written = self._write_blob(key, content)
        except Exception as exc:  # noqa: BLE001 - every failure mode must be handled
            self._settle_failed_binary(record, exc, rollback_on_error, actor, request_id, source)
            raise  # unreachable: _settle_failed_binary always raises

        completed = self.store.update(
            record["id"],
            {
                "ingestState": "complete",
                "ingestError": None,
                "size": written["bytes"],
                "storage": {"key": key, **written},
                "versions": [
                    {
                        "versionId": version_id,
                        "version": INITIAL_VERSION,
                        "key": key,
                        "bytes": written["bytes"],
                        "checksum": written["checksum"],
                        "origFilename": data["origFilename"],
                        "createdAt": data["ingestedAt"],
                    }
                ],
            },
            actor=actor,
            source=source,
            request_id=request_id,
        )
        return completed

    def _settle_failed_binary(
        self,
        record: dict[str, Any],
        exc: Exception,
        rollback: bool,
        actor: str | None,
        request_id: str | None,
        source: str,
    ) -> None:
        """Decide what the folder is left holding after a failed binary write.

        ``rollbackOnError=true`` removes the draft, which is exactly the
        documented promise: no orphaned draft entry in the target folder. The
        blob write is a separate step, so the draft is all that is at risk and
        the removal is a plain audited delete.

        With the flag off the draft survives as a failed, retryable entry. Either
        way the caller gets a non-2xx response; only the residue differs, which
        is what the flag is for.

        A client error (400, 409, 413) keeps its own status after the rollback,
        because "the upload was empty" must not be reported as a disk failure.
        This method always raises.
        """
        detail = str(exc)
        if rollback:
            self.store.delete(
                record["id"],
                actor=actor,
                source=source,
                request_id=request_id,
                hard=True,
            )
            if isinstance(exc, LibraryError):
                raise
            raise BlobStorageError(
                f"binary for {record['data'].get('name')!r} could not be stored and the "
                f"draft was rolled back: {detail}"
            ) from exc
        failed = self.store.update(
            record["id"],
            {"ingestState": "failed", "ingestError": detail},
            actor=actor,
            source=source,
            request_id=request_id,
        )
        if isinstance(exc, LibraryError):
            raise
        raise BlobStorageError(
            f"binary for {record['data'].get('name')!r} could not be stored; the draft "
            f"{failed['id']} is retained marked ingestState=failed: {detail}"
        ) from exc

    def _write_blob(self, key: str, content: BinaryIO) -> dict[str, Any]:
        """Stream ``content`` to the content root, enforcing the size ceiling.

        The size check happens while streaming rather than afterwards so an
        oversized upload cannot fill the disk first, and a partial file is always
        removed.
        """
        target = self.content_root / key
        digest = hashlib.sha256()
        total = 0
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                while True:
                    chunk = content.read(self.chunk_bytes)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise UploadTooLarge(
                            f"upload exceeds the {self.max_upload_bytes} byte limit"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        except UploadTooLarge:
            self._discard(key)
            raise
        except OSError as exc:
            self._discard(key)
            raise BlobStorageError(f"could not write blob for {key}: {exc}") from exc
        except Exception:
            self._discard(key)
            raise
        if total == 0:
            self._discard(key)
            raise ValidationError("uploaded file is empty")
        return {"bytes": total, "checksum": digest.hexdigest(), "algorithm": "sha256"}

    def _discard(self, key: str) -> None:
        """Remove a partial or orphaned blob. Failures here are not interesting."""
        try:
            (self.content_root / key).unlink(missing_ok=True)
        except OSError:
            pass

    # -- versions ----------------------------------------------------------- #

    def add_version(
        self,
        document_id: str,
        content: BinaryIO,
        filename: str,
        *,
        actor: str | None = None,
        request_id: str | None = None,
        source: str = "library version",
    ) -> dict[str, Any]:
        """Append a new version. The previous binary is never overwritten.

        The source API is explicit that replacing the binary of an existing file
        is the wrong call and that the add-new-version operation is the right
        one, so the old blob is kept and the version list is append-only.
        """
        record = self.get_document(document_id)
        data = record["data"]
        if data.get("ingestState") != "complete":
            raise Conflict(
                f"document {document_id!r} is {data.get('ingestState') or 'unknown'}, "
                "not complete; a version cannot be added to it"
            )

        major, minor = parse_version(str(data.get("version") or INITIAL_VERSION))
        version = format_version(major, minor + 1)
        version_id = new_id("version")
        key = f"{_safe_segment(document_id)}/{_safe_segment(version_id)}"

        try:
            written = self._write_blob(key, content)
        except Exception as exc:
            # Nothing was recorded, so a failed version leaves the document
            # exactly as it was. This is the same rollback promise as ingest.
            if isinstance(exc, LibraryError):
                raise
            raise BlobStorageError(f"could not store the new version: {exc}") from exc

        history = list(data.get("versions") or [])
        history.append(
            {
                "versionId": version_id,
                "version": version,
                "key": key,
                "bytes": written["bytes"],
                "checksum": written["checksum"],
                "origFilename": Path(filename or "").name or None,
                "createdAt": utcnow(),
            }
        )
        return self.store.update(
            document_id,
            {
                "version": version,
                "versionId": version_id,
                "majorVersion": major,
                "minorVersion": minor + 1,
                "size": written["bytes"],
                "storage": {"key": key, **written},
                "versions": history,
                "origFilename": Path(filename or "").name or data.get("origFilename"),
                # A new binary invalidates the derived preview: the thumbnail of
                # version 0.1 says nothing about version 0.2.
                "thumbnailStatus": "pending",
                "thumbnailUrl": "",
            },
            actor=actor,
            source=source,
            request_id=request_id,
        )

    def read_version(
        self, document_id: str, version_id: str | None = None
    ) -> tuple[Path, str, int | None]:
        """Resolve a version's stored bytes to a path and its size.

        With no ``version_id`` this is the *current* version, taken from
        ``storage.key`` rather than the first entry of the history, so a
        document that has been revised serves the new bytes.
        """
        record = self.get_document(document_id)
        if record["data"].get("ingestState") != "complete":
            raise Conflict(f"document {document_id!r} has no stored binary")
        history = record["data"].get("versions") or []
        if version_id:
            entry = next((v for v in history if v.get("versionId") == version_id), None)
            if entry is None:
                raise NotFound(f"version {version_id!r} not found on document {document_id!r}")
        else:
            current_key = (record["data"].get("storage") or {}).get("key")
            entry = next((v for v in history if v.get("key") == current_key), None)
            if entry is None:
                entry = next((v for v in reversed(history) if v.get("key")), None)
        if entry is None:
            raise NotFound(f"document {document_id!r} has no stored binary")
        path = self.content_root / str(entry["key"])
        if not path.is_file():
            raise NotFound(f"stored bytes for document {document_id!r} are missing")
        return (
            path,
            str(entry.get("origFilename") or record["data"].get("name") or document_id),
            entry.get("bytes"),
        )

    # -- thumbnails --------------------------------------------------------- #

    def thumbnail_path(self, document_id: str) -> Path:
        return self.content_root / _safe_segment(document_id) / "thumbnail.svg"

    def derive_thumbnail(
        self,
        document_id: str,
        *,
        actor: str | None = None,
        request_id: str | None = None,
        source: str = "library thumbnail",
    ) -> dict[str, Any]:
        """Produce the derived preview and mark the document ready.

        The source documents the *lag*, not the renderer: a thumbnail exists by
        the time the record is useful, and until then ``thumbnailUrl`` is empty.
        That is the behaviour reproduced here, with a local placeholder render
        so the library has something honest to display. Replacing the renderer
        is a single method.

        ``source`` is required from the HTTP layer to name the route that
        actually served the write, and the ``thumbnailUrl`` written here is
        built from ``thumbnail_url_base``, which the same router configures.
        """
        record = self.get_document(document_id)
        target = self.thumbnail_path(document_id)
        svg = self._render_preview(record)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8")
        except OSError as exc:
            raise BlobStorageError(f"could not write the derived preview: {exc}") from exc
        return self.store.update(
            document_id,
            {
                "thumbnailUrl": f"{self.thumbnail_url_base}/{document_id}/thumbnail",
                "thumbnailStatus": "ready",
                "thumbnailGeneratedAt": utcnow(),
            },
            actor=actor,
            source=source,
            request_id=request_id,
        )

    def read_thumbnail(self, document_id: str) -> str:
        """Return the derived preview markup, or 404 while it is still pending.

        Deliberately a pure read. A consumer that polls and gets a 404 is inside
        the documented lag; a consumer that polls and gets a 200 has the
        preview. Deriving it here instead would make a GET change state, and a
        read that writes is exactly the thing the audit log would then have to
        record as a mutation. Call :meth:`derive_thumbnail` to produce it.
        """
        record = self.get_document(document_id)
        target = self.thumbnail_path(document_id)
        if not target.is_file() or record["data"].get("thumbnailStatus") != "ready":
            raise NotFound("the thumbnail has not been rendered yet")
        return target.read_text(encoding="utf-8")

    def _render_preview(self, record: Mapping[str, Any]) -> str:
        """A deterministic, dependency-free preview card.

        Deliberately a placeholder, and deliberately safe: every interpolated
        value is XML-escaped, because a document name is attacker-controlled
        input rendered into a document the browser parses.
        """
        data = record["data"]
        name = _xml_escape(data.get("name") or "Untitled")
        fmt = _xml_escape(str(data.get("format") or "file").upper())
        version = _xml_escape(data.get("version") or INITIAL_VERSION)
        size = data.get("size")
        shown_size = f"{int(size) / 1024:.0f} KB" if isinstance(size, (int, float)) else "pending"
        initial = _xml_escape(str(data.get("name") or "?")[:1].upper())
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200" width="320" height="200" '
            'role="img" aria-label="{label}">'
            "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
            "<stop offset='0' stop-color='#1b2336'/><stop offset='1' stop-color='#0f172a'/>"
            "</linearGradient></defs>"
            "<rect width='320' height='200' fill='url(#g)'/>"
            "<rect x='0.5' y='0.5' width='319' height='199' fill='none' stroke='#475569'/>"
            "<rect x='24' y='24' width='56' height='68' rx='6' fill='#272f42' stroke='#475569'/>"
            "<text x='52' y='66' font-family='monospace' font-size='22' fill='#22c55e' "
            "text-anchor='middle'>{initial}</text>"
            "<text x='24' y='126' font-family='sans-serif' font-size='15' fill='#f8fafc'>{name}</text>"
            "<text x='24' y='150' font-family='monospace' font-size='12' fill='#94a3b8'>"
            "{fmt} &#183; v{version} &#183; {size}</text>"
            "<text x='24' y='172' font-family='monospace' font-size='11' fill='#94a3b8'>{path}</text>"
            "</svg>"
        ).format(
            label=f"{name} preview",
            initial=initial,
            name=name,
            fmt=fmt,
            version=version,
            size=_xml_escape(shown_size),
            path=_xml_escape(data.get("libraryMaterializedPath") or ""),
        )

    # -- removal ------------------------------------------------------------ #

    def delete(
        self,
        document_id: str,
        *,
        hard: bool = False,
        actor: str | None = None,
        request_id: str | None = None,
        source: str = "library delete",
    ) -> dict[str, Any]:
        """Soft-delete the record, always. Blobs are kept on purpose.

        A hard delete would remove bytes the audit log still references, and the
        audit log is the product promise. Disk reclamation is a lifecycle
        question, not an ingest one, so it is not answered here.

        ``source`` arrives from the route. Revoking a rendered preview is a
        second audited write, and it is recorded under the route the caller
        actually hit rather than a label the domain invented.
        """
        record = self.get_document(document_id)
        if record["data"].get("thumbnailStatus") == "ready" and not hard:
            self.store.update(
                document_id,
                {"thumbnailStatus": "revoked", "thumbnailUrl": ""},
                actor=actor,
                source=source,
                request_id=request_id,
            )
        return self.store.delete(
            document_id, actor=actor, source=source, request_id=request_id, hard=hard
        )

    def usage(self, room_id: str) -> dict[str, Any]:
        """Counts for the library header. Cheap enough to render on every load."""
        documents = self.list_documents(room_id, limit=1000)
        return {
            "room_id": room_id,
            "documents": len(documents),
            "folders": len(self.list_folders(room_id)),
            "bytes": sum(int(d["data"].get("size") or 0) for d in documents),
            "pending_thumbnails": sum(
                1 for d in documents if d["data"].get("thumbnailStatus") == "pending"
            ),
            "failed": sum(1 for d in documents if d["data"].get("ingestState") == "failed"),
        }


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def content_root_from_env(default: str | Path) -> Path:
    """Resolve the content root lazily, for the same reason the DB path is.

    A module-level constant would be captured at import time and every test
    would share one content store.
    """
    return Path(os.environ.get("DSR_CONTENT_DIR", str(default)))


def max_upload_bytes_from_env() -> int:
    raw = os.environ.get("DSR_MAX_UPLOAD_BYTES")
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_UPLOAD_BYTES


def parse_metadata_part(raw: str | None) -> dict[str, Any]:
    """Parse the ``metadata`` JSON string part of the multipart request.

    Malformed metadata is a 400, matching the source's documented behaviour for
    a missing or invalid metadata part.
    """
    if raw is None or not str(raw).strip():
        raise ValidationError("metadata is required")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"metadata is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("metadata must be a JSON object")
    return parsed
