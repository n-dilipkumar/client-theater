"""Content-library search and room assembly (WF-010).

Modelled on the researched ``POST /search/v1/content/query`` operation, with the
vendor's identity removed: the store declares no schema, so the fields the query
names resolve through :class:`LibrarySchema` to JSON paths a deployment picks.

The public surface is intentionally small:

``SearchQuery``      parse and validate a request body
``LibrarySearch``    run it against the store, page it, project it
``LibraryAssembler`` attach the chosen documents to a room
``SavedSearches``    keep a named query so a team can report on what it searches
``LibrarySchema``    the logical-field to JSON-path mapping
``CursorCodec``      sign and expire continuation tokens
``scan``/``count``/``audit_count``
                     collection reads, assembled from the store's public API
"""

#: WF-010, ported from ``feature/WF-010-search-the-content-library-to-assemble-a-room``.
#: Nothing in this package imports ``dsr.api``; the HTTP surface lives in
#: ``dsr.features.wf010_library_search``.

from dsr.search.contract import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_RETURN_FIELDS,
    DEFAULT_SEARCH_FIELDS,
    FILTER_TOO_COMPLEX,
    MAX_FILTER_DEPTH,
    MAX_PAGE_SIZE,
    MAX_TERM_LENGTH,
    OPT_IN_RETURN_FIELDS,
    REPOSITORIES,
    SEARCH_OPERATORS,
    TERM_TOO_LONG,
    TOKEN_INVALID,
    Condition,
    Cursor,
    CursorCodec,
    FilterNode,
    Group,
    LibrarySchema,
    SearchError,
    SearchQuery,
    SortKey,
    contract,
    get_path,
    page_size_error,
)
from dsr.search.matching import Scorer, suggest, tokenize
from dsr.search.reads import audit_count, count, scan
from dsr.search.saved_searches import SavedSearches
from dsr.search.service import LibraryAssembler, LibrarySearch

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_RETURN_FIELDS",
    "DEFAULT_SEARCH_FIELDS",
    "FILTER_TOO_COMPLEX",
    "MAX_FILTER_DEPTH",
    "MAX_PAGE_SIZE",
    "MAX_TERM_LENGTH",
    "OPT_IN_RETURN_FIELDS",
    "REPOSITORIES",
    "SEARCH_OPERATORS",
    "TERM_TOO_LONG",
    "TOKEN_INVALID",
    "Condition",
    "Cursor",
    "CursorCodec",
    "FilterNode",
    "Group",
    "LibraryAssembler",
    "LibrarySchema",
    "LibrarySearch",
    "SavedSearches",
    "Scorer",
    "SearchError",
    "SearchQuery",
    "SortKey",
    "audit_count",
    "contract",
    "count",
    "get_path",
    "page_size_error",
    "scan",
    "suggest",
    "tokenize",
]
