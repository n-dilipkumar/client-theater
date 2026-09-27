"""External content library (WF-008): add a cloud file, and keep it current.

The public surface is :class:`~dsr.external_library.sync.ExternalLibrarySync`. The
supporting modules are deliberately separate and independently testable:

``errors``      the documented error vocabulary, each code carrying a remedy
``ratelimit``   the documented one-request-per-second-per-token limit
``sources``     the provider seam, plus the simulated drive that keeps this
                runnable offline
``sync``        the workflow itself
"""

from dsr.external_library.errors import ERROR_CATALOGUE, ExternalSyncError
from dsr.external_library.ratelimit import RateLimiter
from dsr.external_library.sources import (
    GOOGLE_DRIVE,
    SAMPLE_FILE_ID,
    STALE_FILE_ID,
    SimulatedDrive,
    SourceAdapter,
    SourceFile,
    default_registry,
    get_source,
    register_source,
    source_registry,
    supported_sources,
    unregister_source,
)
from dsr.external_library.sync import (
    CONNECTION_COLLECTION,
    FOLDER_COLLECTION,
    IN_SYNC,
    ITEM_COLLECTION,
    LINKED,
    ORPHANED,
    ROOT,
    SNAPSHOT,
    ExternalLibrarySync,
)

__all__ = [
    "CONNECTION_COLLECTION",
    "ERROR_CATALOGUE",
    "ExternalLibrarySync",
    "ExternalSyncError",
    "FOLDER_COLLECTION",
    "GOOGLE_DRIVE",
    "IN_SYNC",
    "ITEM_COLLECTION",
    "LINKED",
    "ORPHANED",
    "ROOT",
    "RateLimiter",
    "SAMPLE_FILE_ID",
    "SNAPSHOT",
    "STALE_FILE_ID",
    "SimulatedDrive",
    "SourceAdapter",
    "SourceFile",
    "default_registry",
    "get_source",
    "register_source",
    "source_registry",
    "supported_sources",
    "unregister_source",
]
