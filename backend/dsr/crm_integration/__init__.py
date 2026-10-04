"""WF-042: pull CRM deal, account and contact records into the room for display.

The inbound CRM read for the Digital Sales Room, kept in its own package so no
two features claim one path. This is the machinery behind a room's **deal
panel**: it resolves which CRM records a buyer's view belongs to, issues a
read-only field-scoped read against the vendor, normalises the vendor's paged
response into one view model, puts a display label on every option value, caches
the result for a short TTL, and renders a panel that still works when the seller
authored the room without CRM context.

The module layout, and why each piece is separate:

``vocabulary``  the values the research fixes by name, and the vendor limits
``errors``      one hierarchy, so the feature module exports one handler
``identity``    resolving a buyer's CRM identity, the research's step two
``fieldmap``    deriving the read set from the same map the writes use
``query``       the field-scoped, paged, read-only plan, per vendor
``sources``     the room's own copy of the vendor's three tables
``normalize``   one vendor response in, the room's view model out
``labels``      the vendor's display annotation, or the room's option sets
``cache``       the per-buyer read-through cache and its four states
``inferences``  every judgement call, named and served
``engine``      the façade the HTTP layer calls; owns the five collections

:data:`dsr.crm_integration.inferences.SOURCED_QUOTE` is the sentence from the
research that governs the field-scoped read, and
:func:`dsr.crm_integration.inferences.describe` is served at the feature's
``/inferences`` route so a reviewer can see which parts are sourced and which are
this build's judgement without reading the diff.
"""

from __future__ import annotations

from dsr.crm_integration import (
    cache,
    errors,
    fieldmap,
    identity,
    inferences,
    labels,
    normalize,
    query,
    sources,
    vocabulary,
)
from dsr.crm_integration.cache import SNAPSHOTS, describe_cache, state as cache_state
from dsr.crm_integration.engine import (
    OWNED_COLLECTIONS,
    QUERIES,
    CrmReadEngine,
)
from dsr.crm_integration.errors import (
    AmbiguousIdentity,
    BatchTooLarge,
    CrmIntegrationError,
    DuplicateIdentity,
    DuplicateOptionSet,
    EmptyReadSet,
    FieldMapError,
    FieldNotMapped,
    IdentityError,
    LabelError,
    MissingBuyerEmail,
    NoCrmIdentity,
    PageTooLarge,
    QueryTooLong,
    TooManyConditions,
    UnknownCursor,
    UnknownIdentity,
    UnknownObject,
    UnknownOptionSet,
    UnknownSystem,
    VendorLimitError,
)
from dsr.crm_integration.fieldmap import (
    DEFAULT_FIELD_MAP,
    default_field_map,
    normalise_field_map,
    panel_fields,
    read_set,
    require_mapped,
)
from dsr.crm_integration.identity import (
    IDENTITIES,
    normalise as normalise_identity,
    resolve as resolve_identity,
    resolve_or_refuse as resolve_identity_or_refuse,
)
from dsr.crm_integration.inferences import INFERENCES, describe as describe_inferences
from dsr.crm_integration.labels import LABELLABLE_FIELDS, OPTION_SETS, capability
from dsr.crm_integration.normalize import deal_panel, merge_pages, normalise_response
from dsr.crm_integration.query import READ_ONLY_ENDPOINTS, ReadQuery, build_query
from dsr.crm_integration.sources import RECORDS, normalise_record, run_query
from dsr.crm_integration.vocabulary import (
    CRM_OBJECTS,
    CRM_SYSTEMS,
    DATAVERSE_DISPLAY_ANNOTATION,
    DATAVERSE_DISPLAY_PREFERENCE,
    DISPLAY_LABEL_CAPABILITY,
    IDENTITY_SOURCE,
    IDENTITY_SOURCES,
    PAGING_MODES,
    clamp_ttl,
    object_name,
    page_limit,
    supports_display_labels,
)

__all__ = [
    "AmbiguousIdentity",
    "CRM_OBJECTS",
    "CRM_SYSTEMS",
    "DATAVERSE_DISPLAY_ANNOTATION",
    "DATAVERSE_DISPLAY_PREFERENCE",
    "DEFAULT_FIELD_MAP",
    "DISPLAY_LABEL_CAPABILITY",
    "IDENTITIES",
    "IDENTITY_SOURCES",
    "IDENTITY_SOURCE",
    "INFERENCES",
    "LABELLABLE_FIELDS",
    "OPTION_SETS",
    "OWNED_COLLECTIONS",
    "PAGING_MODES",
    "QUERIES",
    "READ_ONLY_ENDPOINTS",
    "RECORDS",
    "SNAPSHOTS",
    "BatchTooLarge",
    "CrmIntegrationError",
    "CrmReadEngine",
    "DuplicateIdentity",
    "DuplicateOptionSet",
    "EmptyReadSet",
    "FieldMapError",
    "FieldNotMapped",
    "IdentityError",
    "LabelError",
    "MissingBuyerEmail",
    "NoCrmIdentity",
    "PageTooLarge",
    "QueryTooLong",
    "ReadQuery",
    "TooManyConditions",
    "UnknownCursor",
    "UnknownIdentity",
    "UnknownObject",
    "UnknownOptionSet",
    "UnknownSystem",
    "VendorLimitError",
    "build_query",
    "cache",
    "capability",
    "cache_state",
    "clamp_ttl",
    "deal_panel",
    "default_field_map",
    "describe_cache",
    "describe_inferences",
    "errors",
    "fieldmap",
    "identity",
    "inferences",
    "labels",
    "merge_pages",
    "normalise_field_map",
    "normalise_identity",
    "normalise_record",
    "normalise_response",
    "normalize",
    "object_name",
    "page_limit",
    "panel_fields",
    "query",
    "read_set",
    "require_mapped",
    "resolve_identity",
    "resolve_identity_or_refuse",
    "run_query",
    "sources",
    "supports_display_labels",
    "vocabulary",
]
