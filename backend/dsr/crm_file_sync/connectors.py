"""The outbound seam: the two methods a CRM file connector implements, and one
implementation of them.

Why this is a seam and not an HTTP client
-----------------------------------------
The spec's Extensibility field is the whole design: "Because the CRM side is
just 'store a file, get an id, put the id in a property', a third-party connector
can support any file-capable CRM by implementing a two-method interface
(``upload(bytes) -> fileId``, ``attach(fileId, recordId)``)."

So the two method names, and their argument order, are the research's own and are
kept exactly. What this module widens, and what :mod:`dsr.crm_file_sync.inferences`
records, is the arguments around them: the spec's own ``data_flow`` sends the
upload "with optional ``folderId`` and ``fileName``", so ``upload`` takes a
filename, a content type and an optional folder beside the bytes, and ``attach``
takes the mode the connection chose. The widening is additive. A connector that
implements the two signatures with keyword defaults keeps working.

There is no socket anywhere in this package. The default connector is
:class:`SimulatedConnector`, which holds the vendor's file storage in the same
audited store as everything else, in the ``crm_remote_file`` collection. That is
the same choice :mod:`dsr.crm_provisioning.gateway` makes and for the same
reason: a build that only recorded what it *would* send could assert idempotency
in a comment and prove nothing, while a simulation of the effect makes the second
mirror really find the file already there and really create nothing.

A simulated row says so. :attr:`SimulatedConnector.note` is ``"simulated"``, every
mirror row carries ``simulated: true`` when this connector served it, and
:func:`connector_for` names the connector in the mirror row's own vocabulary. A
reviewer reading a mirror can never mistake a simulated confirmation for one a
real vendor gave.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from dsr.crm_file_sync import vocabulary
from dsr.crm_file_sync.errors import (
    AttachRefused,
    FolderRefused,
    UnsupportedSystem,
    UploadRefused,
)
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# The request and the two answers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UploadRequest:
    """The upload this workflow would send, spelled the way the research spells it.

    Frozen and fully explicit because this object is what the research is about: a
    reviewer should be able to read one and check it against the vendor's guide
    without reading any Python. The path is the research's own
    ``files/2026-09/files`` and the field names are the research's own ``file``,
    ``folderId`` and ``fileName``.
    """

    filename: str
    mime_type: str
    content: bytes
    folder_id: str = ""

    def fields(self) -> dict[str, str]:
        """The multipart field names and their values, as the request would carry them.

        The binary is not in here. A multipart part holds bytes, and the
        research's ``file`` field is that part; this returns the part's filename
        so a log row or a test can assert on it without carrying the payload.
        """
        named = {"file": self.filename, "fileName": self.filename}
        if self.folder_id:
            named["folderId"] = self.folder_id
        return named

    def request(self) -> dict[str, Any]:
        """The request as a reviewer reads it: method, path, content type, fields."""
        return {
            "method": "POST",
            "path": f"/{vocabulary.UPLOAD_PATH}",
            "content_type": vocabulary.UPLOAD_CONTENT_TYPE,
            "required_fields": list(vocabulary.UPLOAD_REQUIRED_FIELDS),
            "optional_fields": list(vocabulary.UPLOAD_OPTIONAL_FIELDS),
            "fields": self.fields(),
            "byte_size": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }


@dataclass(frozen=True)
class UploadedFile:
    """What the vendor's upload answered: a file id, and where it landed."""

    file_id: str
    folder_id: str = ""
    filename: str = ""
    byte_size: int = 0
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "folder_id": self.folder_id,
            "filename": self.filename,
            "byte_size": self.byte_size,
            "simulated": self.simulated,
        }


@dataclass(frozen=True)
class Attachment:
    """What the vendor's attach answered.

    ``mode`` is this workflow's own vocabulary. The note body is not here, and
    that is deliberate: the research quotes no note payload, so a build that
    invented one and put it in the record would be reporting a vendor request it
    never sourced. What is here is what the research does say happened.
    """

    mode: str
    record_id: str
    property_name: str = ""
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "record_id": self.record_id,
            "property_name": self.property_name,
            "simulated": self.simulated,
        }


# --------------------------------------------------------------------------- #
# The interface the spec names
# --------------------------------------------------------------------------- #


@runtime_checkable
class FileConnector(Protocol):
    """The two methods the spec's Extensibility field names.

    ``upload(bytes) -> fileId`` and ``attach(fileId, recordId)``. Everything this
    workflow needs from a CRM travels through these two calls, so a test can
    assert on all of it without a network, and a tenant on a third CRM implements
    these and nothing else.
    """

    #: The vendor this connector serves, in the product's own spelling. It is a
    #: class attribute so :func:`connector_for` can find the connector before any
    #: instance exists.
    system: str

    #: Recorded on every mirror row this connector serves, so a simulated answer
    #: is never read as a real one.
    note: str

    def upload(self, content: bytes, **kwargs: Any) -> UploadedFile:  # pragma: no cover - protocol
        ...

    def attach(self, file_id: str, record_id: str, **kwargs: Any) -> Attachment:  # pragma: no cover
        ...


# --------------------------------------------------------------------------- #
# The simulated HubSpot connector
# --------------------------------------------------------------------------- #


class SimulatedConnector:
    """A HubSpot files connector whose vendor side lives in the audited store.

    The simulated vendor holds what it holds in ``crm_remote_file``, one record
    per uploaded file, carrying the id it returned, the folder it was given, the
    digest of the bytes, and the record it was attached to. The collection is
    named ``crm_remote_`` rather than ``crm_`` because it stands in for the
    vendor's storage, exactly as :mod:`dsr.crm_provisioning.gateway` names its
    three remote collections.

    The effect that matters is idempotency. ``upload`` looks for a stored file
    with the same folder and the same digest. A second mirror of an unchanged
    asset finds it and returns the id it already has, creating nothing and
    writing no new remote row. A mirror of changed bytes does not find it and
    creates a new file, which is the researched rule that "revisions create a new
    file".

    That is a property a test holds the build to rather than a sentence in a
    docstring: the second mirror can be shown to create nothing.
    """

    system = "hubspot"
    note = "simulated"

    def __init__(self, store: RecordStore, *, rng: Any = None) -> None:
        self.store = store
        self.rng = rng

    # -- the researched upload ---------------------------------------------- #

    def upload(self, content: bytes, **kwargs: Any) -> UploadedFile:
        """``POST files/2026-09/files``: store the file and return its id.

        The research's own behaviour. A folder id is optional and is passed
        through when the connection has one. The same bytes in the same folder
        return the id the vendor already gave them, which is what makes a repeat
        mirror a no-op rather than a second file.

        The response carries only what the research quotes: an id, and the folder
        the file landed in. HubSpot's upload response has more fields than that,
        and this simulation does not invent the rest.
        """
        filename = str(kwargs.get("filename") or "")
        folder_id = str(kwargs.get("folder_id") or "")
        digest = hashlib.sha256(content).hexdigest()

        existing = self._find(folder_id, digest)
        if existing is not None:
            return UploadedFile(
                file_id=str(existing["file_id"]),
                folder_id=folder_id,
                filename=filename or str(existing.get("filename") or ""),
                byte_size=len(content),
                simulated=True,
            )

        file_id = self._next_file_id(folder_id, digest)
        self.store.create(
            vocabulary.REMOTE_FILES,
            {
                "file_id": file_id,
                "folder_id": folder_id,
                "filename": filename,
                "sha256": digest,
                "byte_size": len(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
                "attached_records": [],
                "simulated": True,
            },
            actor=self.note,
            source="connector:hubspot:files:upload",
        )
        return UploadedFile(
            file_id=file_id,
            folder_id=folder_id,
            filename=filename,
            byte_size=len(content),
            simulated=True,
        )

    # -- the researched attach ---------------------------------------------- #

    def attach(self, file_id: str, record_id: str, **kwargs: Any) -> Attachment:
        """Write the returned file id onto the record that owns it.

        Both researched modes. A ``file_property`` attach stores the file id in
        the named property, which is what the properties guide documents for a
        ``file`` field. A ``note`` attach records the file id against the record,
        which is what "Attach files to records using the notes APIs" describes,
        without inventing a note body the research never quoted.
        """
        mode = str(kwargs.get("mode") or vocabulary.DEFAULT_ATTACH_MODE)
        property_name = str(kwargs.get("property_name") or "")
        row = self._row_for(file_id)
        if row is None:
            raise AttachRefused(
                f"the CRM has no file with id {file_id!r}, so nothing can be attached to "
                f"{record_id!r}"
            )
        attached = list(row.get("attached_records") or [])
        entry = {"record_id": record_id, "mode": mode, "property_name": property_name}
        if entry not in attached:
            attached.append(entry)
        self.store.update(
            row["id"],
            {"attached_records": attached},
            actor=self.note,
            source="connector:hubspot:files:attach",
        )
        return Attachment(
            mode=mode,
            record_id=record_id,
            property_name=property_name,
            simulated=True,
        )

    # -- the simulated vendor's own state ----------------------------------- #

    def file_rows(self, folder_id: str | None = None) -> list[dict[str, Any]]:
        """Every file the simulated vendor holds, oldest first.

        Sorted by filename rather than by id, so a panel that renders them is a
        stable document rather than one that reshuffles between two runs against
        unchanged state.
        """
        where: dict[str, Any] = {} if folder_id is None else {"folder_id": folder_id}
        rows = self.store.find(vocabulary.REMOTE_FILES, where, limit=1000)
        payloads = [dict(row["data"]) | {"id": row["id"]} for row in rows]
        return sorted(payloads, key=lambda entry: (str(entry.get("filename")), entry["id"]))

    def reset(self) -> None:
        """Forget every file the simulated vendor holds.

        Not something a deployment needs. It is what keeps a test that queues the
        same bytes twice from depending on which test ran first, which is the one
        thing a suite running under ``pytest-xdist`` cannot allow.
        """
        for row in self.store.list(vocabulary.REMOTE_FILES, limit=1000):
            self.store.delete(row["id"], actor=self.note, source="connector:hubspot:files:reset")

    # -- internals ---------------------------------------------------------- #

    def _find(self, folder_id: str, digest: str) -> dict[str, Any] | None:
        for row in self.store.find(
            vocabulary.REMOTE_FILES, {"folder_id": folder_id, "sha256": digest}, limit=1000
        ):
            return dict(row["data"]) | {"id": row["id"]}
        return None

    def _row_for(self, file_id: str) -> dict[str, Any] | None:
        for row in self.store.find(vocabulary.REMOTE_FILES, {"file_id": file_id}, limit=1000):
            return dict(row["data"]) | {"id": row["id"]}
        return None

    def _next_file_id(self, folder_id: str, digest: str) -> str:
        """A file id derived from the folder and the bytes, so it is reproducible.

        Derived rather than drawn from a random source on purpose. Two runs of
        the demo seeder produce the same ids, a test can name the id it expects,
        and an unchanged asset re-mirrored after a process restart finds the same
        file instead of a second one with the same content.
        """
        seed = f"{folder_id}|{digest}".encode("utf-8")
        return f"file-{hashlib.sha256(seed).hexdigest()[:16]}"


# --------------------------------------------------------------------------- #
# The registry the spec's extensibility clause asks for
# --------------------------------------------------------------------------- #


_REGISTRY: dict[str, Any] = {}


def register_connector(system: str, factory: Any) -> None:
    """Register a connector factory for a system.

    This is the extension point the spec describes: a third-party connector for
    any file-capable CRM is a factory that takes the store and returns an object
    with ``upload`` and ``attach``. Nothing in this package changes when one is
    registered, which is the property the research's claim rests on.

    A factory takes the store and returns the connector. A class works because
    calling it with the store constructs one.
    """
    _REGISTRY[str(system).strip().lower()] = factory


def unregister_connector(system: str) -> None:
    """Remove a registered connector. Used by a test that restores the default."""
    _REGISTRY.pop(str(system).strip().lower(), None)


def supported_systems() -> tuple[str, ...]:
    """Every system with a connector, sourced or registered."""
    known = set(_REGISTRY) | set(vocabulary.FILE_CAPABLE_SYSTEMS)
    return tuple(system for system in vocabulary.SYSTEMS if system in known)


def connector_for(store: RecordStore, system: str, *, rng: Any = None) -> Any:
    """The connector for a system, or a refusal naming why there is none.

    A system in :data:`~dsr.crm_file_sync.vocabulary.FILE_CAPABLE_SYSTEMS` with
    no registered factory gets the local :class:`SimulatedConnector`. A system in
    :data:`~dsr.crm_file_sync.vocabulary.UNSOURCED_SYSTEMS` raises
    :class:`~dsr.crm_file_sync.errors.UnsupportedSystem` carrying the gap text, and
    a deployment may clear that by registering its own connector. That is the
    asymmetry the research states: one system is documented and two are not, and
    the two are refused by name rather than invented.
    """
    wanted = str(system or "").strip().lower()
    factory = _REGISTRY.get(wanted)
    if factory is not None:
        return factory(store)
    if wanted in vocabulary.FILE_CAPABLE_SYSTEMS:
        return SimulatedConnector(store, rng=rng)
    gap = vocabulary.UNSOURCED_SYSTEMS.get(wanted)
    if gap:
        raise UnsupportedSystem(gap)
    raise UnsupportedSystem(
        f"no file connector is registered for {wanted!r}. A deployment that supports it "
        f"registers one with register_connector."
    )


# --------------------------------------------------------------------------- #
# The refusals a deployment's own connector needs
# --------------------------------------------------------------------------- #


def refuse_folder(message: str) -> FolderRefused:
    """The refusal for a folder the vendor no longer accepts.

    Its own function so a connector a deployment writes has one import to make,
    and so the 502 that answers it is the same 502 this package answers.
    """
    return FolderRefused(message)


def refuse_upload(message: str) -> UploadRefused:
    """The refusal for an upload the vendor declined."""
    return UploadRefused(message)


def describe_connector(connector: Any) -> dict[str, Any]:
    """The connector as the vocabulary reports it, for a page and for a mirror row."""
    return {
        "system": str(getattr(connector, "system", "")),
        "class": type(connector).__name__,
        "note": str(getattr(connector, "note", "unknown")),
        "implements_upload": callable(getattr(connector, "upload", None)),
        "implements_attach": callable(getattr(connector, "attach", None)),
        "satisfies_the_protocol": isinstance(connector, FileConnector),
    }


# The default registration. HubSpot is the one system the research documents, and
# the default connector is the local simulation because this product holds no CRM
# credentials. A deployment registers its own connector over the top of this one.
register_connector("hubspot", lambda store: SimulatedConnector(store))


def vendor_report(system: str) -> dict[str, Any]:
    """One vendor's file capability, with the gap named where there is one.

    Served as data rather than compiled into a page, so a value added to the
    vocabulary reaches every client at once.
    """
    wanted = str(system or "").strip().lower()
    sourced = wanted in vocabulary.FILE_CAPABLE_SYSTEMS
    return {
        "system": wanted,
        "status": "sourced" if sourced else "unsourced",
        "upload_path": f"/{vocabulary.UPLOAD_PATH}" if sourced else "",
        "content_type": vocabulary.UPLOAD_CONTENT_TYPE if sourced else "",
        "required_fields": list(vocabulary.UPLOAD_REQUIRED_FIELDS) if sourced else [],
        "optional_fields": list(vocabulary.UPLOAD_OPTIONAL_FIELDS) if sourced else [],
        "attach_modes": list(vocabulary.ATTACH_MODES) if sourced else [],
        "attach_mode_notes": dict(vocabulary.ATTACH_MODE_NOTES) if sourced else {},
        "folder_recommendation": vocabulary.FOLDER_RECOMMENDATION,
        "folder_caveat": vocabulary.FOLDER_CAVEAT,
        "connector_methods": ["upload", "attach"],
        "gap": "" if sourced else vocabulary.UNSOURCED_SYSTEMS.get(wanted, ""),
    }


def vendors() -> list[dict[str, Any]]:
    """Every vendor's file capability report, in the product's own order."""
    return [vendor_report(system) for system in vocabulary.SYSTEMS]
