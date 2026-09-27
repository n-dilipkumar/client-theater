"""The vocabulary of the Sales Impact report, and how a CRM stage becomes a class.

Sourced
-------
The research fixes three things about vocabulary and this module keeps all three:

* the workspace type that admits a room to the report - "The Sales Impact report pulls in
  any workspace designated as a 'Sales' type that has a CRM opportunity" - so
  :data:`SALES_TYPE` is the single constant every inclusion test reads;
* the two documented API constraints, the 429 rate limit and the ``properties``
  selection parameter, which are served as data by :func:`describe` so a client building
  against the real API knows what to design around;
* the two shapes a CRM calls a closed deal, which are in the sets below.

Inferred
--------
The arithmetic *behind* several tiles is not in the research and lives in
:mod:`dsr.salesimpact.rollup`. What is here is the part a caller has to agree on for
the arithmetic to mean anything: which strings count as a won deal and which as a lost
one. :data:`WON_STAGES` and :data:`LOST_STAGES` cover Salesforce (``Closed Won``,
``Closed Lost``) and HubSpot (``closedwon``, ``closedlost``) plus the separator variants
of the same words, and :func:`classify_stage` is the one place that decides.

**Lost is tried before won, across every match level.** ``"won/lost"`` is a label a CRM
really does use, and it has to land on lost: a prefix match for ``"won"`` would file it as
a win and put a lost deal into Revenue. :func:`classify_stage` is the only function that
applies the order, and the order is argued there.

Field locations, not field names
--------------------------------
A CRM is a third party: Salesforce calls a deal's value ``Amount``, HubSpot calls it
``amount``, and the research names ``Opp Amount`` and ``Deal Amount`` as the workspace
columns these sync into. :data:`FIELD_SYNONYMS` therefore lists candidate keys per
concept and :func:`pick` takes the first that is present and non-empty. A team whose CRM
uses a fourth spelling overrides the list in the ``sales_impact_config`` record; no code
change and no migration. This mirrors how ``dsr.analytics.py`` discovers its fields.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: The workspace type that admits a room to the report. Sourced: the report "pulls in
#: any workspace designated as a 'Sales' type that has a CRM opportunity".
SALES_TYPE = "sales"

#: The classes a stage can fall into. ``open`` is everything that is neither closed, and
#: ``unknown`` is everything that could not be classified at all - which is a subset of
#: open for the active-deal count but is reported separately so a reader can see it.
STAGE_CLASSES = ("won", "lost", "open", "unknown")

#: Stage strings that mean a deal was won. Salesforce spells it two words, HubSpot runs
#: it together, and both use it in lower, upper and title case. A team's own CRM may use
#: something else entirely, which is why this is a set and not an enum.
WON_STAGES: frozenset[str] = frozenset(
    {
        "closed won",
        "closedwon",
        "closed won deal",
        "won",
        "won deal",
        "success",
    }
)

#: Stage strings that mean a deal was lost. Same reasoning, and the same reason the
#: classifier tries these first.
LOST_STAGES: frozenset[str] = frozenset(
    {
        "closed lost",
        "closedlost",
        "closed lost deal",
        "lost",
        "lost deal",
        "closed dead",
        "dead",
        "no sale",
    }
)

#: The synonyms for "this action was a view". The report's ``buyer_views`` tile counts
#: events whose action falls here, and ``buyer_actions`` counts every event, so this set
#: is what makes the two tiles different numbers rather than the same one twice.
VIEW_ACTIONS: frozenset[str] = frozenset(
    {"view", "viewed", "page view", "pageview", "workspace viewed", "space viewed", "opened"}
)

#: Candidate keys per concept, in preference order. A team's CRM sends whichever it
#: sends; whichever arrives first that is present and non-empty wins.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "crm_deal_id": ("crm_deal_id", "deal_id", "opportunity_id", "opp_id", "external_id", "crmId"),
    "name": ("name", "title", "deal_name", "opportunity_name"),
    "account": ("account", "company", "organisation", "organization", "account_name"),
    "stage": ("stage", "status", "phase", "stage_name", "deal_stage"),
    "amount": ("amount", "value", "deal_amount", "opp_amount", "arr", "total"),
    "currency": ("currency", "currency_code", "currencyCode"),
    "owner": ("owner", "owner_name", "deal_owner", "assigned_to"),
    "team": ("team", "team_name", "group"),
    "created_at": (
        # `created_at` is absent on purpose. It is one of the store's reserved envelope
        # keys and is stripped from a payload on create, so listing it first would
        # advertise a spelling that silently disappears. See project_deal's docstring.
        "created_date",
        "open_date",
        "opened_at",
        "start_date",
        "opportunity_created_date",
        "created",
    ),
    "closed_at": (
        "closed_at",
        "closed_date",
        "close_date",
        "won_at",
        "lost_at",
        "decision_date",
    ),
    # The workspace's own field locations, read from a room record.
    "room_type": ("type", "workspace_type", "workspaceType", "room_type"),
    "room_owner": ("owner", "owner_name", "assigned_to", "sponsor"),
    "room_name": ("name", "title", "account", "company"),
    "room_account": ("account", "company", "organisation", "organization"),
    # The engagement event's field locations.
    "person": ("person", "user", "visitor", "buyer", "email", "actor", "by"),
    "action": ("action", "event", "activity", "kind", "type"),
    "occurred_at": ("occurred_at", "at", "happened_at", "timestamp", "event_time", "created_at"),
}

#: The documented API constraints, served at ``/vocabulary``. Both are named in the
#: research's ``extensibility`` line as "the documented API constraints to design
#: around", and neither is a behaviour this build implements: no socket is opened here.
API_CONSTRAINTS: dict[str, str] = {
    "rate_limit": (
        "429 Too many requests. The research names this response and no retry policy, "
        "so this build does not invent an attempt count or a backoff."
    ),
    "properties": (
        "A properties query parameter selects which fields a response carries. Omit it "
        "and the research states the response contains only the resource's id, object "
        "and url."
    ),
    "endpoints_named_but_undocumented": (
        "GET /v1/deals, GET /v1/deals/{id}, GET /v1/accounts, GET /v1/workspaces. The "
        "research names these and documents no request or response schema, so this "
        "build makes no claim about their shape and calls none of them."
    ),
}

#: The published properties selection this report would ask for, by the vocabulary the
#: research uses. Named so a client building a real extractor has a starting point, and
#: clearly not a call this build makes.
PROPERTIES_SELECTION: dict[str, list[str]] = {
    "deals": [
        "stage",
        "amount",
        "close date",
        "owner",
        "opportunity stage",
        "opp amount",
        "deal stage",
        "deal amount",
        "deal closed date",
    ],
    "workspaces": ["type", "stage", "team", "views", "actions", "last client view"],
}


# --------------------------------------------------------------------------- #
# Scalars
# --------------------------------------------------------------------------- #


def as_text(value: Any, default: str = "") -> str:
    """A trimmed string, or ``default`` for anything that is not usable text.

    Containers, booleans and ``None`` all read as absent: a team that puts a dict where
    a name belongs has not told us the name, and coercing it would put ``"{'a': 1}"`` in
    a report.
    """
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value).strip() or default


def as_number(value: Any, default: float | None = None) -> float | None:
    """A number, or ``None`` when the value is not one.

    ``None`` rather than ``0`` on failure, because "this deal has no amount" and "this
    deal is worth nothing" are different facts and the rollup counts them differently.
    """
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return default
        # A CRM will happily send "$48,000.00" and "45000 USD".
        for prefix in ("$", "€", "£", "¥"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        trailing = text
        for suffix in ("usd", "eur", "gbp", "aud", "cad", "sek", "nok", "dkk", "chf"):
            if trailing.lower().endswith(suffix):
                text = text[: -len(suffix)].strip()
                break
        try:
            return float(text)
        except ValueError:
            return default
    return default


def as_bool(value: Any) -> bool:
    """The usual spellings of yes, without inventing one."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on", "connected")
    return False


def pick(
    data: Mapping[str, Any] | None,
    keys: Sequence[str],
    default: Any = None,
    *,
    synonyms: Mapping[str, Sequence[str]] | None = None,
    concept: str | None = None,
) -> Any:
    """First non-empty value among ``keys``, or the concept's configured synonyms.

    ``keys`` is the caller's explicit order. When it is empty and ``concept`` is given,
    the list comes from ``synonyms`` - the config override - falling back to
    :data:`FIELD_SYNONYMS`. That is how a team replaces one concept's key list without
    touching code.
    """
    if not isinstance(data, Mapping):
        return default
    candidates = list(keys)
    if not candidates and concept:
        override = (synonyms or {}).get(concept)
        if isinstance(override, (list, tuple)) and override:
            candidates = [str(key) for key in override]
        else:
            candidates = list(FIELD_SYNONYMS.get(concept, ()))
    for key in candidates:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


# --------------------------------------------------------------------------- #
# Stage classification
# --------------------------------------------------------------------------- #


def normalise(value: Any) -> str:
    """Fold a stage, a team or a currency to a comparable string.

    Lowercase, and every run of ``_``, ``-``, ``.`` and whitespace collapsed to a single
    space. ``"Closed_Won"``, ``"closed-won"``, ``"CLOSED WON"`` and ``" closed  won "``
    all become ``"closed won"``. A team that stores a stage as an enum value gets the
    same treatment, so ``"CLOSED_WON"`` is not a fourth vocabulary.
    """
    text = as_text(value)
    if not text:
        return ""
    out: list[str] = []
    previous_separator = False
    for character in text.lower():
        if character in "_-. \t\n/":
            if out and not previous_separator:
                out.append(" ")
            previous_separator = True
        else:
            out.append(character)
            previous_separator = False
    return "".join(out).strip()


def is_sales_type(value: Any, *, sales_type: str | None = None) -> bool:
    """Whether a workspace's type admits it to the report.

    Compares normalised, so ``"Sales"``, ``"sales"``, ``"SALES"`` and ``"sales "`` are
    the one workspace type the research names, and ``"Sales Workspace"`` is not.

    ``sales_type=None`` means "the researched default", not "compare against nothing".
    That distinction is load-bearing: a ``None`` that normalised to the empty string made
    *every* workspace fail the type test, and the whole report came back empty with
    ``sales_typed_rooms: 0`` while the excluded rows still showed ``type: "Sales"``.
    """
    wanted = normalise(SALES_TYPE if sales_type is None else sales_type)
    return bool(wanted) and normalise(value) == wanted


def classify_stage(
    value: Any,
    *,
    won: frozenset[str] | set[str] | None = None,
    lost: frozenset[str] | set[str] | None = None,
) -> str:
    """Which of :data:`STAGE_CLASSES` a stage string falls into.

    Three levels - exact, ``startswith``, whole-word - and the order is **term-major**:
    every level is tried against the lost set before any level is tried against the won
    set. The levels catch, respectively, ``"Closed Won"``, ``"Closed Won (100%)"`` and
    ``"Salesforce Closed Won"`` / ``"Renewal - Lost"``.

    Term-major rather than level-major is not a preference. ``"won/lost"`` normalises to
    ``"won lost"``: a *prefix* match for ``"won"`` would file it as a win and put a lost
    deal into Revenue, while ``"lost"`` matches it only as a later word. Trying the whole
    lost set first lets the more specific signal beat the more general one, which is the
    only ordering that survives a stage name carrying both words. No string in the won set
    contains ``"lost"``, so the reordering cannot cost a real win.

    The third level is **whole-word**, which a plain ``in`` is not. A plain substring test
    files ``"Gewonnen"`` as won, because that German word contains ``"won"``; a team whose
    CRM is not English hits that the first time they read the report, and a misclassified
    deal in the revenue figure is the worst thing this function can do. Splitting on
    non-alphanumerics makes the level mean "the word appears", which is what it was always
    meant to mean.

    Anything that matches neither set at any level is ``"unknown"``. It is not a refusal:
    the research defines the close rate over closed won and closed lost, so an
    unclassifiable deal is outside both arms of the fraction, and it still counts as an
    active deal because nothing says it closed. Reporting it as a bucket is what keeps a
    team's own stage string from being rejected by a report that is supposed to read it.
    """
    text = normalise(value)
    if not text:
        return "unknown"
    won_set = {normalise(item) for item in (won if won is not None else WON_STAGES)}
    lost_set = {normalise(item) for item in (lost if lost is not None else LOST_STAGES)}
    words = _words(text)

    levels = (
        lambda term: term == text,
        lambda term: text.startswith(term),
        lambda term: term in words,
    )
    for terms in (lost_set, won_set):
        for matcher in levels:
            if any(matcher(term) for term in terms):
                return "lost" if terms is lost_set else "won"
    return "unknown"


def _words(text: str) -> set[str]:
    """The alphanumeric words of a normalised string.

    Separators were already folded to single spaces by :func:`normalise`, so splitting on
    whitespace and dropping anything that is not a letter or a digit is enough. ``"won"``
    is a word of ``"Salesforce Closed Won"`` and is not a word of ``"Gewonnen"``.
    """
    out: set[str] = set()
    current: list[str] = []
    for character in text:
        if character.isalnum():
            current.append(character)
        elif current:
            out.add("".join(current))
            current = []
    if current:
        out.add("".join(current))
    return out


def describe() -> dict[str, Any]:
    """The published vocabulary, served at ``GET /api/wf-023/vocabulary``.

    A client renders its stage and action pickers from this rather than from a list
    compiled into its own source, so a stage set changed here reaches every client at
    once. Both halves are in the payload on purpose: the sourced vocabulary *and* the
    documented constraints, so a reader can see which is which.
    """
    return {
        "workspace_type": {"value": SALES_TYPE, "admission_rule": "equal after normalisation"},
        "stage_classes": list(STAGE_CLASSES),
        "won_stages": sorted(WON_STAGES),
        "lost_stages": sorted(LOST_STAGES),
        "classification_order": (
            "term-major: every level is tried against the lost set before any level is "
            "tried against the won set"
        ),
        "classification_levels": [
            "exact match",
            "starts with",
            "contains the word (whole-word, so 'Gewonnen' is not 'won')",
        ],
        "unknown_stage": (
            "counted, shown in the funnel, treated as an active deal, and excluded from "
            "both arms of the close rate, because the researched fraction is closed won "
            "over closed won plus closed lost"
        ),
        "view_actions": sorted(VIEW_ACTIONS),
        "field_synonyms": {concept: list(keys) for concept, keys in FIELD_SYNONYMS.items()},
        "field_synonyms_override": (
            "sales_impact_config.fields.<concept> replaces a concept's key list; no code "
            "change and no migration"
        ),
        "filters": {
            "from": "ISO-8601 date, inclusive; ranges the deal's created date",
            "to": "ISO-8601 date, inclusive; ranges the deal's created date",
            "stage": "a raw stage string, or one of the classes, or a comma-separated list of either",
            "owner": "comma-separated list, matched against the resolved owner",
            "team": "comma-separated list, matched against the deal's team",
            "bucket": {
                "values": ["day", "week", "month"],
                "default": "day",
                "applies_to": ["deals_created_over_time", "buyer_views_over_time"],
            },
        },
        "api_constraints": dict(API_CONSTRAINTS),
        "properties_selection": {name: list(values) for name, values in PROPERTIES_SELECTION.items()},
        "note": "This build calls none of the above endpoints. It is a local mirror.",
    }
