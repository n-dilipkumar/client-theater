"""Source adapters: the seam between this project and someone else's cloud.

An adapter answers one question — *what does the source say about this file
right now?* — and that is deliberately the whole interface. Everything the
workflow needs downstream (title, size, a version string to compare against)
comes back from that one call, so adding a real provider means implementing
:meth:`SourceAdapter.describe` and registering it. No workflow code changes.

Research records that only GoogleDrive is supported "at this time" and that
"additional sources may be added in future API versions". The registry below is
how that statement becomes true here: the set of supported sources is whatever
is registered, and the API reports it rather than hard-coding it.

:class:`SimulatedDrive` ships as the default so the workflow runs offline and
in tests. It is a local stand-in, not a Drive client: it holds sample files in
memory and lets a test or a seed script move a file's version forward to prove
the auto-sync loop actually re-syncs. A real deployment registers an
OAuth-backed adapter in its place and never imports this.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from dsr.external_library.errors import ExternalSyncError

GOOGLE_DRIVE = "GoogleDrive"

#: The file id used as the worked example in the research. Keeping it as a real
#: sample means the demo and the documented flow are the same flow.
SAMPLE_FILE_ID = "1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54"

#: Another sample that starts one version behind, so a freshly seeded database
#: has an item the auto-sync pass can visibly bring up to date.
STALE_FILE_ID = "1Bv2Tn9yQKc0HqZ7mLpR4sWdXaE"


@dataclass(frozen=True)
class SourceFile:
    """What a source knows about one file at one moment.

    ``version`` is the field the whole auto-sync story rests on: the item
    records the version it last applied, and a re-sync is a re-fetch whenever
    the two disagree. Providers spell this differently (Drive revisions,
    SharePoint versions, a checksum), so adapters normalise it to a string and
    only guarantee that a change means the content changed.
    """

    source: str
    file_id: str
    name: str
    mime_type: str
    size_bytes: int
    modified_at: str
    version: str
    web_url: str | None = None
    #: Anything the provider returns that this project does not model. Forwarded
    #: to the library record untouched, which is what keeps the source adapter
    #: from having to be extended before a team can see a new provider field.
    extra: dict[str, Any] = field(default_factory=dict)

    def as_library_metadata(self) -> dict[str, Any]:
        """The provider-independent part stored on the library item."""
        return {
            "title": self.name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "source_modified_at": self.modified_at,
            "source_version": self.version,
            **({"web_url": self.web_url} if self.web_url else {}),
        }


@runtime_checkable
class SourceAdapter(Protocol):
    """The whole provider interface."""

    #: Stable identifier used in requests and stored on the item, e.g. "GoogleDrive".
    source: str

    def describe(self, connection: dict[str, Any], file_id: str) -> SourceFile:
        """Describe a file, or raise ``ExternalContentNotFound`` / ``InvalidParameter``."""
        ...


_REGISTRY: dict[str, SourceAdapter] = {}


def register_source(adapter: SourceAdapter, *, replace_existing: bool = False) -> SourceAdapter:
    """Register an adapter so requests naming ``adapter.source`` resolve.

    A deployment that has real credentials registers its adapter over the
    simulated one with ``replace_existing=True`` and changes nothing else.
    """
    name = (adapter.source or "").strip()
    if not name:
        raise ValueError("adapter.source must be a non-empty identifier")
    if name in _REGISTRY and not replace_existing:
        raise ValueError(f"source {name!r} is already registered")
    _REGISTRY[name] = adapter
    return adapter


def get_source(name: str) -> SourceAdapter:
    """Look up an adapter, or raise the documented invalid-parameter error.

    An unsupported source is a bad request rather than a 404: the endpoint
    exists, the value in it does not.
    """
    adapter = _REGISTRY.get((name or "").strip())
    if adapter is None:
        raise ExternalSyncError(
            "InvalidParameter",
            f"externalSource {name!r} is not a supported source",
            remediation=(
                f"Use one of: {', '.join(supported_sources())}. "
                "Additional sources may be added by registering a source adapter."
            ),
            extra={"supported_sources": supported_sources()},
        )
    return adapter


def unregister_source(name: str) -> None:
    """Remove an adapter from the registry, if it is there.

    The counterpart to :func:`register_source`, and mostly of interest to a test
    that registers a source and must leave the process-wide registry as it found
    it -- a global that a test cannot put back is a global that makes every
    other test depend on test order.
    """
    _REGISTRY.pop((name or "").strip(), None)


def supported_sources() -> list[str]:
    """Every registered source, sorted. The API reports this verbatim."""
    return sorted(_REGISTRY)


def source_registry() -> dict[str, SourceAdapter]:
    """A copy of the registry, so a caller can inspect or override entries."""
    return dict(_REGISTRY)


class SimulatedDrive:
    """An in-memory stand-in for a cloud drive.

    Holds a small catalogue of files and nothing else. It exists so the
    workflow is demonstrable and testable without network access or credentials;
    it is not a Drive client and makes no attempt to look like one.
    """

    source = GOOGLE_DRIVE

    def __init__(self, files: dict[str, SourceFile] | None = None) -> None:
        self._files: dict[str, SourceFile] = dict(files or _sample_files())

    # -- SourceAdapter ------------------------------------------------------ #

    def describe(self, connection: dict[str, Any], file_id: str) -> SourceFile:
        self._check_connection(connection)
        handle = (file_id or "").strip()
        if not handle:
            raise ExternalSyncError("InvalidParameter", "externalContentId is required")
        found = self._files.get(handle)
        if found is None:
            raise ExternalSyncError(
                "ExternalContentNotFound",
                f"no {self.source} file with id {handle!r} is visible to this connection",
                remediation="Check the file id and that the connection can see the file.",
            )
        return found

    # -- simulation helpers ------------------------------------------------- #

    def add(self, file: SourceFile) -> SourceFile:
        """Register a file, so a test or seed can offer something to link."""
        self._files[file.file_id] = replace(file, source=self.source)
        return self._files[file.file_id]

    def advance(self, file_id: str, *, version: str, modified_at: str, name: str | None = None) -> SourceFile:
        """Move a file to a new version: the upstream edit that triggers a re-sync."""
        current = self._files.get(file_id)
        if current is None:
            raise KeyError(file_id)
        moved = replace(current, version=version, modified_at=modified_at, name=name or current.name)
        self._files[file_id] = moved
        return moved

    def remove(self, file_id: str) -> None:
        """Make a file vanish upstream, so an item can be seen to go orphaned."""
        self._files.pop(file_id, None)

    @staticmethod
    def _check_connection(connection: dict[str, Any]) -> None:
        """A provider would 404 here if the caller had no usable connection."""
        if not connection:
            raise ExternalSyncError(
                "ExternalConnectionNotFound",
                f"no connected {GOOGLE_DRIVE} account is configured",
                remediation="Connect a Google account in profile settings before retrying.",
            )


def _sample_files() -> dict[str, SourceFile]:
    """A small, believable catalogue, including the id the research quotes."""
    return {
        SAMPLE_FILE_ID: SourceFile(
            source=GOOGLE_DRIVE,
            file_id=SAMPLE_FILE_ID,
            name="Enterprise Overview Deck.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            size_bytes=18_432_000,
            modified_at="2026-09-20T09:14:00.000+00:00",
            version="rev-7",
            web_url="https://drive.example/file/d/1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54/view",
            extra={"owners": ["dana@northwind.example"], "trashed": False},
        ),
        STALE_FILE_ID: SourceFile(
            source=GOOGLE_DRIVE,
            file_id=STALE_FILE_ID,
            name="Security & Compliance Pack.pdf",
            mime_type="application/pdf",
            size_bytes=4_210_000,
            modified_at="2026-09-25T16:02:00.000+00:00",
            version="rev-3",
            web_url="https://drive.example/file/d/1Bv2Tn9yQKc0HqZ7mLpR4sWdXaE/view",
            extra={"owners": ["sam@contoso.example"], "trashed": False},
        ),
    }


def default_registry() -> dict[str, SourceAdapter]:
    """The registry the app runs with: GoogleDrive, simulated.

    A copy of the registered set, so a process that wants a different adapter
    registers over the top of it instead of mutating shared state.
    """
    return dict(_REGISTRY)


# The simulated drive is the default rather than a fallback, so a fresh install
# has one working source instead of none. Seeding the registry at import keeps
# "which sources exist" a single fact with one answer.
register_source(SimulatedDrive())
