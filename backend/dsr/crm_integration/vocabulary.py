"""The values the WF-042 research fixes by name, served to clients as data.

Nothing in this module reads or writes a record. It is the one place a value
that came out of the research is written down, so a client renders its pickers
from here rather than from a list compiled into a page, and a value added here
reaches every client at once.

Each constant carries the sentence from
``docs/research/raw/crm-integration.md`` section 9 that fixes it, because a
number without its source is a number a future agent will "correct".
"""

from __future__ import annotations

from dsr.crm_integration.errors import UnknownObject, UnknownSystem

# --------------------------------------------------------------------------- #
# The vendors
# --------------------------------------------------------------------------- #

#: The three CRM systems the research reads from. The room stores the vendor's
#: tables locally, so a room on a system outside this set has no read path here.
CRM_SYSTEMS: tuple[str, ...] = ("salesforce", "dataverse", "hubspot")

#: The three objects the room's deal panel reads. The user flow names the fields
#: it needs: "the room needs deal name, stage, amount, primary contact, account
#: industry", which is one field from each of these three objects.
CRM_OBJECTS: tuple[str, ...] = ("deal", "contact", "account")

#: How each vendor spells each object. Salesforce uses a singular entity name in
#: SOQL, Dataverse a plural entity set in the URL, and HubSpot a plural object
#: type in the path.
OBJECT_NAMES: dict[str, dict[str, str]] = {
    "salesforce": {"deal": "Opportunity", "contact": "Contact", "account": "Account"},
    "dataverse": {"deal": "opportunities", "contact": "contacts", "account": "accounts"},
    "hubspot": {"deal": "deals", "contact": "contacts", "account": "companies"},
}

#: The record id column each vendor names its rows by. Salesforce spells it ``Id``,
#: Dataverse ``<entity>id``, and HubSpot uses the path-level ``id`` of the object.
ID_FIELDS: dict[str, str] = {
    "salesforce": "Id",
    "dataverse": "id",
    "hubspot": "id",
}


def require_system(system: str) -> str:
    """Return the canonical system name, or refuse.

    Case-folded on the way in, because a caller that writes ``Salesforce`` and a
    caller that writes ``salesforce`` mean one vendor and the read set must not
    depend on which spelling arrived.
    """
    folded = str(system or "").strip().lower()
    if folded not in CRM_SYSTEMS:
        raise UnknownSystem(
            f"{system!r} is not a CRM system this workflow reads. "
            f"Choose one of {', '.join(CRM_SYSTEMS)}."
        )
    return folded


def require_object(object_name: str) -> str:
    """Return the canonical object name, or refuse."""
    folded = str(object_name or "").strip().lower()
    if folded not in CRM_OBJECTS:
        raise UnknownObject(
            f"{object_name!r} is not an object this workflow reads. "
            f"Choose one of {', '.join(CRM_OBJECTS)}."
        )
    return folded


def object_name(system: str, object_name_: str) -> str:
    """The vendor's own spelling of one object."""
    return OBJECT_NAMES[require_system(system)][require_object(object_name_)]


# --------------------------------------------------------------------------- #
# Display labels
# --------------------------------------------------------------------------- #

#: The annotation Dataverse adds when the room asks for formatted values:
#: ``"statecode@OData.Community.Display.V1.FormattedValue": "Active"``.
DATAVERSE_DISPLAY_ANNOTATION = "OData.Community.Display.V1.FormattedValue"

#: "Showing formatted values by using the request header:
#: ``Prefer: odata.include-annotations="OData.Community.Display.V1.FormattedValue"``"
DATAVERSE_DISPLAY_PREFERENCE = (
    'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
)

#: Which vendors can return a display label beside a stored option value. This is
#: the capability flag the research describes: "Vendors expose a capability flag
#: for 'display-label annotations' that the room uses when available and falls
#: back to its own option-set map when not."
DISPLAY_LABEL_CAPABILITY: dict[str, bool] = {
    "salesforce": False,
    "dataverse": True,
    "hubspot": False,
}


#: The suffix a Dataverse annotation puts on the column it labels.
def display_annotation_suffix(system: str) -> str:
    """The key a labelled column carries, or an empty string.

    Dataverse returns ``{"statecode": 0, "statecode@...FormattedValue":
    "Active"}``, so the labelled key is the stored key plus a fixed suffix.
    """
    return f"@{DATAVERSE_DISPLAY_ANNOTATION}" if DISPLAY_LABEL_CAPABILITY.get(system) else ""


# --------------------------------------------------------------------------- #
# Where a buyer's CRM identity comes from
# --------------------------------------------------------------------------- #

#: The research names the source without choosing it: "Room resolves the buyer's
#: CRM identity (from W1/W2 mapping or a signed token)."
IDENTITY_SOURCES: tuple[str, ...] = ("room_mapping", "signed_token")

#: The source this build implements. See
#: :data:`dsr.crm_integration.inferences.IDENTITY_SOURCE`.
IDENTITY_SOURCE = "room_mapping"

#: The columns a signed identity assertion must carry. Declared and unused, so
#: the room can say what the unbuilt source would have needed rather than
#: leaving the choice to be made twice.
SIGNED_TOKEN_CLAIMS: tuple[str, ...] = ("iss", "aud", "sub", "exp", "account_id", "contact_id")

# --------------------------------------------------------------------------- #
# Paging
# --------------------------------------------------------------------------- #

#: The three continuation shapes the data flow names: "paged results with
#: ``totalSize`` / ``done`` / ``nextRecordsUrl`` (or ``@odata.nextLink`` /
#: ``paging.next.after``)".
PAGING_MODES: dict[str, str] = {
    "salesforce": "query_locator",
    "dataverse": "odata_next_link",
    "hubspot": "paging_cursor",
}

#: The vendor response field each mode reads, and the one that says the last
#: page has been reached.
PAGING_FIELDS: dict[str, dict[str, str]] = {
    "query_locator": {"total": "totalSize", "done": "done", "next": "nextRecordsUrl"},
    "odata_next_link": {"total": "@odata.count", "done": "absent", "next": "@odata.nextLink"},
    "paging_cursor": {"total": "total", "done": "absent", "next": "paging.next.after"},
}

#: Salesforce returns records under ``records``; OData returns a ``value`` array;
#: HubSpot returns ``results``. A batch read has no next page at all.
RECORD_ARRAY_FIELDS: dict[str, str] = {
    "query_locator": "records",
    "odata_next_link": "value",
    "paging_cursor": "results",
}


def paging_mode(system: str) -> str:
    """Which continuation shape a vendor uses."""
    return PAGING_MODES[require_system(system)]


# --------------------------------------------------------------------------- #
# The vendor limits
# --------------------------------------------------------------------------- #

#: "When a SOQL query is executed, up to 2,000 records can be returned at a time
#: in a synchronous request."
SALESFORCE_SYNCHRONOUS_RECORD_LIMIT = 2000

#: "Without this limit, Dataverse returns up to 5,000 standard table rows and
#: 500 elastic table rows."
DATAVERSE_STANDARD_ROW_LIMIT = 5000
DATAVERSE_ELASTIC_ROW_LIMIT = 500

#: "You can include up to 500 total conditions in a query."
DATAVERSE_MAX_CONDITIONS = 500

#: "You can retrieve up to 100 contacts in one request."
HUBSPOT_BATCH_READ_LIMIT = 100

#: "The maximum number of supported objects per page is 200."
HUBSPOT_PAGE_LIMIT = 200

#: "A query can contain a maximum of 3,000 characters".
HUBSPOT_QUERY_CHARACTER_LIMIT = 3000

#: "The search endpoints are limited to 10,000 total results for any given query."
HUBSPOT_SEARCH_RESULT_LIMIT = 10000

#: "The search endpoints are rate limited to five requests per second per account."
HUBSPOT_SEARCH_REQUESTS_PER_SECOND = 5

#: The page ceiling per vendor, and the sentence that fixes each one.
PAGE_LIMITS: dict[str, int] = {
    "salesforce": SALESFORCE_SYNCHRONOUS_RECORD_LIMIT,
    "dataverse": DATAVERSE_STANDARD_ROW_LIMIT,
    "hubspot": HUBSPOT_PAGE_LIMIT,
}

#: The total-result ceiling per vendor. Only HubSpot publishes one.
RESULT_LIMITS: dict[str, int] = {
    "salesforce": SALESFORCE_SYNCHRONOUS_RECORD_LIMIT,
    "dataverse": DATAVERSE_STANDARD_ROW_LIMIT,
    "hubspot": HUBSPOT_SEARCH_RESULT_LIMIT,
}

#: The page a read asks for when the caller names none.
#:
#: The *ceiling* is the vendor's maximum, and a ceiling is not a sensible
#: default: a panel read that asks for 2,000 rows to show one deal is a read that
#: pays the vendor's largest-page cost for the smallest answer. 200 is the
#: research's own HubSpot page maximum, and it is inside every vendor's ceiling.
#: See :data:`dsr.crm_integration.inferences.DEFAULT_PAGE`.
DEFAULT_PAGE = 200

#: The condition ceiling per vendor. Only Dataverse publishes one, and its
#: message is quoted with the number so an operator can search for it.
CONDITION_LIMITS: dict[str, int] = {"dataverse": DATAVERSE_MAX_CONDITIONS}

MAX_CONDITIONS_MESSAGE = (
    "You can include up to 500 total conditions in a query. Otherwise, you see this "
    "error message: Number of conditions in query exceeded maximum limit."
)


def page_limit(system: str) -> int:
    """The largest page a vendor returns in one synchronous read."""
    return PAGE_LIMITS[require_system(system)]


def default_page(system: str) -> int:
    """The page a read asks for when the caller names none, never above the ceiling."""
    return min(DEFAULT_PAGE, page_limit(system))


def condition_limit(system: str) -> int | None:
    """The largest filter a vendor accepts, or ``None`` when it publishes none."""
    return CONDITION_LIMITS.get(require_system(system))


def supports_display_labels(system: str) -> bool:
    """Whether a vendor can annotate a stored option value with its label."""
    return DISPLAY_LABEL_CAPABILITY.get(require_system(system), False)


# --------------------------------------------------------------------------- #
# The read-through cache
# --------------------------------------------------------------------------- #

#: "Room caches the result per buyer with a short TTL and renders the deal
#: panel." The research fixes no number, so this one is this build's and is
#: reported wherever it is applied. See
#: :data:`dsr.crm_integration.inferences.CACHE_TTL`.
CACHE_TTL_SECONDS = 300
CACHE_TTL_MIN_SECONDS = 30
CACHE_TTL_MAX_SECONDS = 3600

#: How a cached panel reads at the moment it is served.
CACHE_STATES: tuple[str, ...] = ("fresh", "stale", "absent", "expired")

#: "Read-through cache refresh on a room scheduler; nothing pushes."
REFRESH_MODE = "pull"

#: The alternative the research records and this build does not take:
#: "Dataverse also supports a change-tracking mode for the same table (see W10)
#: if the room prefers push."
REFRESH_MODE_ALTERNATIVE = "change_tracking"


def clamp_ttl(seconds: int | None) -> int:
    """Bound a requested TTL to the range a "short TTL" can mean.

    Clamped rather than refused. A room asking for an hour has made a decision
    about its own data, and the deviation is reported on the snapshot rather than
    turned into an error the operator has to argue with.
    """
    if seconds is None:
        return CACHE_TTL_SECONDS
    try:
        wanted = int(seconds)
    except (TypeError, ValueError):
        return CACHE_TTL_SECONDS
    return max(CACHE_TTL_MIN_SECONDS, min(wanted, CACHE_TTL_MAX_SECONDS))


# --------------------------------------------------------------------------- #
# The read query record
# --------------------------------------------------------------------------- #

#: What the room did on a read. These are the words the query log is filtered
#: on, and they are the audit story of a read path: a read that is not recorded
#: is a read nobody can account for.
READ_OUTCOMES: tuple[str, ...] = (
    "complete",
    "paged",
    "empty",
    "truncated_at_vendor_limit",
    "capability_fallback",
)


__all__ = [
    "CACHE_STATES",
    "CACHE_TTL_MAX_SECONDS",
    "CACHE_TTL_MIN_SECONDS",
    "CACHE_TTL_SECONDS",
    "CONDITION_LIMITS",
    "CRM_OBJECTS",
    "CRM_SYSTEMS",
    "DATAVERSE_DISPLAY_ANNOTATION",
    "DATAVERSE_DISPLAY_PREFERENCE",
    "DATAVERSE_ELASTIC_ROW_LIMIT",
    "DATAVERSE_MAX_CONDITIONS",
    "DATAVERSE_STANDARD_ROW_LIMIT",
    "DEFAULT_PAGE",
    "DISPLAY_LABEL_CAPABILITY",
    "HUBSPOT_BATCH_READ_LIMIT",
    "HUBSPOT_PAGE_LIMIT",
    "HUBSPOT_QUERY_CHARACTER_LIMIT",
    "HUBSPOT_SEARCH_REQUESTS_PER_SECOND",
    "HUBSPOT_SEARCH_RESULT_LIMIT",
    "ID_FIELDS",
    "IDENTITY_SOURCES",
    "IDENTITY_SOURCE",
    "MAX_CONDITIONS_MESSAGE",
    "OBJECT_NAMES",
    "PAGE_LIMITS",
    "PAGING_FIELDS",
    "PAGING_MODES",
    "READ_OUTCOMES",
    "RECORD_ARRAY_FIELDS",
    "REFRESH_MODE",
    "REFRESH_MODE_ALTERNATIVE",
    "RESULT_LIMITS",
    "SALESFORCE_SYNCHRONOUS_RECORD_LIMIT",
    "SIGNED_TOKEN_CLAIMS",
    "clamp_ttl",
    "condition_limit",
    "default_page",
    "display_annotation_suffix",
    "object_name",
    "page_limit",
    "paging_mode",
    "require_object",
    "require_system",
    "supports_display_labels",
]
