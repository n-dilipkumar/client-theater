"""One error hierarchy for the in-market company intent package.

Every refusal this package makes is the caller's to fix, so the types share a
base and the feature module registers a single handler for it. Anything that is
*not* a :class:`MarketIntentError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside
the handler, because a timeframe the research caps at 90 days and a view name
that collides with an existing one are both this package's errors, and only one
of them is a conflict with state that already exists. A handler that answered
400 for both would be lying about the second. FastAPI only accepts exception
handlers on the app object, so the feature module exports this mapping as
``EXCEPTION_HANDLERS``; two features may not map the same type, which is why
the whole hierarchy hangs off one base class.

Each class names the sentence in
``docs/research/digital-sales-room-workflows/wf/WF-033.md`` it implements, so a
reviewer can check the behaviour against the specification without reading the
handler.
"""

from __future__ import annotations


class MarketIntentError(ValueError):
    """An in-market company request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "market_intent_error"
    status = 400


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class InvalidConfiguration(MarketIntentError):
    """An intent criterion, research topic, or target market is not usable.

    The research lists "intent criteria per page" as a prerequisite, so a
    criterion that declares no page is not a broad criterion, it is an
    unfinished one, and it is refused rather than stored.
    """

    code = "invalid_configuration"
    status = 422


class InvalidPathFilter(InvalidConfiguration):
    """A specific-page-views filter names an operator the grammar does not have.

    The researched grammar is exactly five operators - "Path is equal to /
    Path is not equal to / Path contains / Path does not contain / Path starts
    with" - plus Domain. A sixth is not a more flexible grammar, it is a filter
    nobody can predict.
    """

    code = "invalid_path_filter"


class UnknownVocabularyValue(InvalidConfiguration):
    """A value is outside a published vocabulary, such as a traffic source.

    The UI offers a fixed set for "Traffic source" and "Visitor country", so a
    value outside it is a client that has drifted from the server rather than a
    new fact about the world.
    """

    code = "unknown_vocabulary_value"


# --------------------------------------------------------------------------- #
# Time frame
# --------------------------------------------------------------------------- #


class TimeframeTooLong(MarketIntentError):
    """"You can only set timeframes within the last 90 days."

    422 rather than 400: the request is well formed and the refusal is a bound
    the research states as a product rule, not a syntax problem. The message
    names the earliest permitted boundary so the caller can see that the bound
    is midnight-UTC based and not a rolling ninety times twenty-four hours.
    """

    code = "timeframe_too_long"
    status = 422


class InvalidTimeframe(MarketIntentError):
    """A time frame cannot be read.

    Raised for a window that ends before it starts, a negative or non-integer
    day count, and a timestamp with no timezone. The last one matters more than
    it looks: "This timeframe is based on midnight UTC", so a naive timestamp
    has no defensible place on the boundary and guessing one would put a company
    in or out of a view on a difference the caller cannot see.
    """

    code = "invalid_timeframe"
    status = 422


# --------------------------------------------------------------------------- #
# Views and automations
# --------------------------------------------------------------------------- #


class DuplicateViewName(MarketIntentError):
    """A saved view of that name already exists.

    "Click Save view to persist the filter set as a named view." Refused rather
    than overwritten, because an automation is attached to a view by name in the
    left panel: two views under one name would make which one an automation
    watches a matter of load order.
    """

    code = "view_name_taken"
    status = 409


class InvalidSort(InvalidConfiguration):
    """A sort key is not one of the three the table offers.

    "Sort by Page views, Unique visitors, or Last visit", ascending or
    descending. A fourth key would be a sort the researched table cannot show
    a column for.
    """

    code = "invalid_sort"


class InvalidAutomation(InvalidConfiguration):
    """An automation names a saved view that does not exist, or nothing at all.

    Automations are per saved view ("click Add new companies or Track intent
    signals to open the editing panel"), so an automation for a view nobody
    saved has nothing to fire on.
    """

    code = "invalid_automation"


class UnknownCategory(MarketIntentError):
    """Not one of the four stock auto-add categories.

    The research enumerates them exactly - net-new with visitor intent, net-new
    with research intent, in-CRM with visitor intent, net-new with both - and
    they are stock, so a caller inventing a fifth is asking for a predicate
    nobody has agreed what it means.
    """

    code = "unknown_auto_add_category"
    status = 422


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #


class CreditsRequired(MarketIntentError):
    """"To access buyer intent features like filtering by segments and
    excluding companies, you need HubSpot Credits."

    402 rather than 403, because the remedy is buying credits rather than being
    granted a permission, and a client that branches on the status can offer the
    right one. ``detail`` says which capability was refused so a page can point
    at the specific control.
    """

    code = "credits_required"
    status = 402

    def __init__(self, message: str, *, capability: str = "") -> None:
        super().__init__(message)
        self.capability = capability


class EnrichmentPermissionRequired(MarketIntentError):
    """"To add and enrich companies from buyer intent, Super Admin must assign
    users with Data enrichment permissions."

    Raised for the add, the enrich, and the manual enrol, because all three
    create or modify a CRM record on the strength of a signal rather than on
    something the seller typed.
    """

    code = "data_enrichment_permission_required"
    status = 403

    def __init__(self, message: str, *, actor: str = "") -> None:
        super().__init__(message)
        self.actor = actor


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


class LifecycleStageRegression(MarketIntentError):
    """``lifecyclestage`` is forward-only.

    Sourced from the CRM API primitives the research cites rather than from the
    buyer-intent page: "PATCH /crm/v3/objects/contacts/{contactId}
    (``lifecyclestage`` is forward-only)". A company whose stage moved backwards
    would drop out of every view that filters on it, silently, which is exactly
    the failure the note in the brief calls a bug someone hits in production.
    """

    code = "lifecycle_stage_regression"
    status = 409


class DomainExcluded(MarketIntentError):
    """This company is on the exclusions list.

    Excluded domains are invisible to the table and to every automation, so the
    one place the refusal surfaces is an operation that names the company
    directly. The message repeats the reason rather than saying "not allowed",
    because a company that vanished from a table with no explanation is the thing
    a seller would ask about.
    """

    code = "domain_excluded"
    status = 409


class AlreadyExcluded(MarketIntentError):
    """That domain is already excluded.

    Two rows for one domain would make "is it excluded?" have two answers and
    the second delete remove only one of them.
    """

    code = "domain_already_excluded"
    status = 409


class InvalidObservation(MarketIntentError):
    """A tracked page view or research observation cannot be read.

    A page view with no host, a session with no id, or a naive timestamp. All
    three are refused rather than stored, because the table's own columns are
    "website visits, unique visitors, last visit, and top page views" and a row
    missing the inputs to those is a row that will read as zero.
    """

    code = "invalid_observation"
    status = 422
