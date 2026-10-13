"""WF-047 refusals: every way this workflow says no, and with which status.

The hierarchy has one base, :class:`CrmFileSyncError`, and the status rides on the
exception rather than being decided by the route. That is what lets one handler
answer every refusal: a field the caller sent wrong is a 400, a connection that
names a record the room does not hold is a 409, a request for a system the
research could not source is a 422, and a vendor that refused the upload is a
502. Only the caller can fix the first three; only the vendor or the deployment
can fix the last.

:class:`~dsr.db.audited.RecordNotFound` is deliberately not a member of this
hierarchy. The core app already maps it to 404, and two handlers for one type is
a collision the feature host refuses.
"""

from __future__ import annotations


class CrmFileSyncError(Exception):
    """Base of every refusal this workflow raises.

    ``status`` is the HTTP status this refusal deserves and ``code`` is the
    machine-readable word a client switches on. Both are on the instance, so a
    handler does not have to know one refusal from another.
    """

    status = 400
    code = "crm_file_sync_error"

    def __init__(self, detail: str, *, code: str | None = None, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class UnknownConnection(CrmFileSyncError):
    """No connection carries that id."""

    status = 404
    code = "unknown_connection"


class UnknownAsset(CrmFileSyncError):
    """No asset carries that id."""

    status = 404
    code = "unknown_asset"


class UnknownMirror(CrmFileSyncError):
    """No mirror attempt carries that id."""

    status = 404
    code = "unknown_mirror"


class AssetRefused(CrmFileSyncError):
    """The asset the caller sent cannot be mirrored.

    A 400: the payload is the caller's to fix. The three rules are a filename, a
    content type, and at least one byte. Nothing here is a vendor limit, because
    the research quotes no vendor limit: it says only that "Uploaded files are
    subject to the limits and supported formats" and gives no number, so this
    build publishes no number either.
    """

    status = 400
    code = "asset_refused"


class NoOwnerRecord(CrmFileSyncError):
    """The connection names no CRM record to own the attachment.

    A 409, not a 404. The record is not missing from the product; it is missing
    from the connection, and the state that has to change is the connection's.
    This is the dependency the issue names: without a deal, account or contact
    record "the returned file id has nowhere to be written and the rep never
    sees the file".
    """

    status = 409
    code = "no_owner_record"


class UnsupportedSystem(CrmFileSyncError):
    """The named system's file support could not be sourced.

    A 422. The request is well formed and the system is one this product knows,
    but the research could not document its file API, so this build refuses
    rather than inventing a payload. The refusal carries the gap text.
    """

    status = 422
    code = "unsupported_system"


class FolderRefused(CrmFileSyncError):
    """The vendor rejected the folder this connection named.

    A 502. The vendor's own words are "Folder requirements at upload are subject
    to change", so a folder that worked yesterday can fail today. The mirror row
    records the failure by name rather than falling back to the root directory,
    because a silent fall back would hide the change that just happened.
    """

    status = 502
    code = "folder_refused"


class UploadRefused(CrmFileSyncError):
    """The vendor refused the upload."""

    status = 502
    code = "upload_refused"


class AttachRefused(CrmFileSyncError):
    """The vendor refused the attachment.

    A 502 for the same reason as an upload refusal: the file may be in the CRM
    while nothing points at it from the record, and a rep who sees no attachment
    needs to be told the room knows why.
    """

    status = 502
    code = "attach_refused"


class DuplicateConnection(CrmFileSyncError):
    """A second connection for the same room, system and owning record.

    A 409. Two connections that push the same document into the same record twice
    are the duplicate-file problem the idempotency rule exists to prevent, and
    the cheapest place to refuse it is where the second one is created.
    """

    status = 409
    code = "duplicate_connection"


def system_for(system: str) -> str:
    """Check a system name against the vocabulary, or raise.

    Raised rather than clamped: a system this build has never heard of is a
    caller mistake, and defaulting it to HubSpot would upload a tenant's files
    to a vendor they did not choose.
    """
    from dsr.crm_file_sync import vocabulary

    wanted = str(system or "").strip().lower()
    if wanted not in vocabulary.SYSTEMS:
        raise CrmFileSyncError(
            f"{system!r} is not one of the systems this product knows: "
            f"{', '.join(vocabulary.SYSTEMS)}",
            code="unknown_system",
            status=400,
        )
    return wanted


def attach_mode_for(mode: str) -> str:
    """Check an attach mode, or default it to the note the vendor documents.

    An empty mode is not an error: the researched default is the note, because
    the vendor's guide says "Attach files to records using the notes APIs".
    """
    from dsr.crm_file_sync import vocabulary

    wanted = str(mode or "").strip().lower()
    if not wanted:
        return vocabulary.DEFAULT_ATTACH_MODE
    if wanted not in vocabulary.ATTACH_MODES:
        raise CrmFileSyncError(
            f"{mode!r} is not an attach mode this workflow writes: "
            f"{', '.join(vocabulary.ATTACH_MODES)}",
            code="unknown_attach_mode",
            status=400,
        )
    return wanted


def owner_object_for(owner: str) -> str:
    """Check the CRM object that owns the attachment, or default it to the deal."""
    from dsr.crm_file_sync import vocabulary

    wanted = str(owner or "").strip().lower()
    if not wanted:
        return vocabulary.DEFAULT_OWNER_OBJECT
    if wanted not in vocabulary.OWNER_OBJECTS:
        raise CrmFileSyncError(
            f"{owner!r} is not a CRM object that can own an attachment: "
            f"{', '.join(vocabulary.OWNER_OBJECTS)}",
            code="unknown_owner_object",
            status=400,
        )
    return wanted
