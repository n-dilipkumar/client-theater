"""The vocabulary of WF-022, as data.

Everything a client needs to render a column picker, a filter builder and a sort
control is here rather than compiled into the frontend, so a field added in one
place reaches every client at once and the validator enforces against the same
list the picker draws from.

Three groups of column, and the split is the researched one
-----------------------------------------------------------
The research lists the available fields verbatim, and the list is the reason the
keys are namespaced::

    "Engagement analytics: Views, Actions, Last Client View"
    "Salesforce data: Opportunity Stage, Opportunity Created Date, Opp Amount,
     Opportunity Type"
    "Hubspot data: Deal Stage, Deal Type, Deal Closed Date, Deal Amount"
    "Order forms: Status and Deal Type"

Note that ``Deal Type`` appears **twice**, once under HubSpot and once under
Order forms. A flat vocabulary would have to invent a tie-break; the vendor
resolves it with the group heading, so this build keys a field as
``<group>.<field>`` and the collision disappears rather than being arbitrated::

    hubspot.deal_type        Deal Type
    order_form.deal_type     Deal Type

The keys are dotted on purpose. They are the same dotted-path shape the dynamic
record index resolves, so a team that wants to find views by
``?where={"visibility": "public"}`` uses the same notation as a field key.

The default views
-----------------
"Click **Add view** and start from a default view: **All Workspaces**, **My
Workspaces**, **Active Pipeline**, **Deal Desk**, or **Implementations**." Two of
the five have a definition the research quotes, and they are transcribed exactly:

    Active Pipeline
        "Any workspace that has a 'Sales' workspace type, or an opportunity or
        deal connected from your CRM"
    Deal Desk
        "Any workspaces that uses Dock's order forms"

Active Pipeline is a **disjunction with two independent arms**, and that is the
single most important rule in this module. A workspace qualifies on either arm,
so both have to be implemented, and neither may be quietly folded into the
other. See :data:`DEFAULT_VIEWS` and the ``active-pipeline-two-arms`` inference.

The other three are not defined by the research beyond their names. What this
build chose for each is recorded in :mod:`dsr.triage.inferences` and marked
``inferred`` on the entry itself.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Column vocabulary
# --------------------------------------------------------------------------- #

#: The five documented field groups. ``engagement`` and ``dock`` are the two a
#: filter may read from a workspace record itself; the other three only exist
#: because a joined resource supplied them, which is the distinction the
#: researched ``workspaceFilters`` / ``workspaceDomainFilters`` parameters draw.
DOCK_GROUP = "dock"
ENGAGEMENT_GROUP = "engagement"
SALESFORCE_GROUP = "salesforce"
HUBSPOT_GROUP = "hubspot"
ORDER_FORM_GROUP = "order_form"

WORKSPACE_GROUPS = (DOCK_GROUP, ENGAGEMENT_GROUP)
DOMAIN_GROUPS = (SALESFORCE_GROUP, HUBSPOT_GROUP, ORDER_FORM_GROUP)

#: Value types drive comparison semantics in :mod:`dsr.triage.filters`: two dates
#: compare as instants, two numbers numerically, and two text values as text. A
#: value that arrives as the wrong type is coerced where that is safe and
#: reported where it is not, never crashed on.
TEXT = "text"
NUMBER = "number"
DATE = "date"

#: ``(key, label, type, sourced)`` per group. ``sourced`` marks whether the
#: research names the field; an unsourced Dock field such as ``dock.team`` is
#: named in the user flow ("add **Views**, **Actions**, **Last Client View**,
#: `Stage`, `Team`") but its label and semantics are this build's reading.
COLUMNS: tuple[dict[str, Any], ...] = (
    # Dock's own workspace properties.
    {"key": "dock.name", "label": "Workspace", "group": DOCK_GROUP, "type": TEXT, "sourced": True},
    {
        "key": "dock.account",
        "label": "Account",
        "group": DOCK_GROUP,
        "type": TEXT,
        "sourced": False,
    },
    {"key": "dock.owner", "label": "Owner", "group": DOCK_GROUP, "type": TEXT, "sourced": True},
    {"key": "dock.team", "label": "Team", "group": DOCK_GROUP, "type": TEXT, "sourced": True},
    {"key": "dock.stage", "label": "Stage", "group": DOCK_GROUP, "type": TEXT, "sourced": True},
    {
        "key": "dock.type",
        "label": "Workspace type",
        "group": DOCK_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    {
        "key": "dock.created_at",
        "label": "Created",
        "group": DOCK_GROUP,
        "type": DATE,
        "sourced": True,
    },
    # "Engagement analytics: Views, Actions, Last Client View"
    {
        "key": "engagement.views",
        "label": "Views",
        "group": ENGAGEMENT_GROUP,
        "type": NUMBER,
        "sourced": True,
        "note": "Buyer activity records whose action is a view kind.",
    },
    {
        "key": "engagement.actions",
        "label": "Actions",
        "group": ENGAGEMENT_GROUP,
        "type": NUMBER,
        "sourced": True,
        "note": "Every buyer activity record for the workspace.",
    },
    {
        "key": "engagement.last_client_view",
        "label": "Last Client View",
        "group": ENGAGEMENT_GROUP,
        "type": DATE,
        "sourced": True,
        "note": "When a buyer last viewed anything in this workspace. Null if never.",
    },
    # "Salesforce data: Opportunity Stage, Opportunity Created Date, Opp Amount,
    # Opportunity Type"
    {
        "key": "salesforce.opportunity_stage",
        "label": "Opportunity Stage",
        "group": SALESFORCE_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    {
        "key": "salesforce.opportunity_created_date",
        "label": "Opportunity Created Date",
        "group": SALESFORCE_GROUP,
        "type": DATE,
        "sourced": True,
    },
    {
        "key": "salesforce.opp_amount",
        "label": "Opp Amount",
        "group": SALESFORCE_GROUP,
        "type": NUMBER,
        "sourced": True,
    },
    {
        "key": "salesforce.opportunity_type",
        "label": "Opportunity Type",
        "group": SALESFORCE_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    # "Hubspot data: Deal Stage, Deal Type, Deal Closed Date, Deal Amount"
    {
        "key": "hubspot.deal_stage",
        "label": "Deal Stage",
        "group": HUBSPOT_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    {
        "key": "hubspot.deal_type",
        "label": "Deal Type",
        "group": HUBSPOT_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    {
        "key": "hubspot.deal_closed_date",
        "label": "Deal Closed Date",
        "group": HUBSPOT_GROUP,
        "type": DATE,
        "sourced": True,
    },
    {
        "key": "hubspot.deal_amount",
        "label": "Deal Amount",
        "group": HUBSPOT_GROUP,
        "type": NUMBER,
        "sourced": True,
    },
    # "Order forms: Status and Deal Type"
    {
        "key": "order_form.status",
        "label": "Status",
        "group": ORDER_FORM_GROUP,
        "type": TEXT,
        "sourced": True,
    },
    {
        "key": "order_form.deal_type",
        "label": "Deal Type",
        "group": ORDER_FORM_GROUP,
        "type": TEXT,
        "sourced": True,
        "note": "A second 'Deal Type', from the order form rather than the CRM.",
    },
)

COLUMNS_BY_KEY: dict[str, dict[str, Any]] = {column["key"]: column for column in COLUMNS}

#: Every key, in catalog order. The default column set of a view is this, minus
#: whatever the view's own definition narrows it to.
ALL_COLUMN_KEYS: tuple[str, ...] = tuple(column["key"] for column in COLUMNS)


def column(key: str) -> dict[str, Any] | None:
    """The catalog entry for ``key``, or ``None`` if it is not a known field."""
    return COLUMNS_BY_KEY.get(key)


def group_of(key: str) -> str:
    """The group prefix of a field key, or ``""`` for a key we do not publish."""
    prefix, _, _rest = key.partition(".")
    return prefix


# --------------------------------------------------------------------------- #
# Filter operators
# --------------------------------------------------------------------------- #

#: ``(op, label, arity, applies_to)``. ``arity`` is how many values the operator
#: consumes: 0 for the unary ones, 1 for a scalar, and -1 for a list.
#:
#: ``in_the_last`` is the operator behind the researched "recent client
#: activity" filter: it takes a number of days and asks whether a timestamp falls
#: inside that window. It is date-only.
OPERATORS: tuple[dict[str, Any], ...] = (
    {"op": "is", "label": "is", "arity": 1, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "is_not", "label": "is not", "arity": 1, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "contains", "label": "contains", "arity": 1, "applies_to": [TEXT]},
    {"op": "in", "label": "is any of", "arity": -1, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "not_in", "label": "is none of", "arity": -1, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "gt", "label": "greater than", "arity": 1, "applies_to": [NUMBER, DATE]},
    {"op": "gte", "label": "at least", "arity": 1, "applies_to": [NUMBER, DATE]},
    {"op": "lt", "label": "less than", "arity": 1, "applies_to": [NUMBER, DATE]},
    {"op": "lte", "label": "at most", "arity": 1, "applies_to": [NUMBER, DATE]},
    {"op": "is_empty", "label": "is empty", "arity": 0, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "is_not_empty", "label": "is not empty", "arity": 0, "applies_to": [TEXT, NUMBER, DATE]},
    {"op": "in_the_last", "label": "in the last (days)", "arity": 1, "applies_to": [DATE]},
)

OPERATORS_BY_OP: dict[str, dict[str, Any]] = {entry["op"]: entry for entry in OPERATORS}

#: How an unknown operator is handled, and why the two halves differ.
#:
#: A *filter* naming an operator this module does not implement is dropped from
#: the compiled predicate and reported in ``problems``. Silently ignoring it
#: would widen the result set past what the view says, and a triage table that
#: shows rows its own filter excludes is worse than one that says "I could not
#: read this filter". Dropping a condition can only ever *add* rows back, so the
#: failure is visible in the row count rather than silent.
#:
#: A *sort* naming an unknown field is refused with a :class:`TriageError`. A sort
#: has no safe fallback: the caller asked for an order, and answering with a
#: different one is a wrong answer rather than a partial one. See
#: ``unknown-filter-is-dropped-unknown-sort-is-refused`` in
#: :mod:`dsr.triage.inferences`.
UNKNOWN_OPERATOR = "dropped"

# --------------------------------------------------------------------------- #
# Default views
# --------------------------------------------------------------------------- #

#: ``all`` ANDs the two filter groups together; ``any`` ORs them, so a row
#: matching either group is in the view. Active Pipeline needs ``any``, because
#: its two arms are alternatives.
MATCH_ALL = "all"
MATCH_ANY = "any"
MATCH_MODES = (MATCH_ALL, MATCH_ANY)

#: A group is ``{"join": "and"|"or", "conditions": [...]}``; its conditions are
#: ``{"field": <column key>, "op": <operator>, "value": <scalar>}``.
#:
#: ``$me`` in a condition value is the "the requesting user" sentinel. It appears
#: only in a *published default definition* - My Workspaces - and is resolved to
#: the actor when a view is created from that default, so the sentinel is never
#: stored. See ``my-workspaces-sentinel`` in :mod:`dsr.triage.inferences`.
ME = "$me"

#: The type value the research quotes. "Any workspace that has a 'Sales'
#: workspace type" is the only workspace-type string in the source, so this one
#: is transcribed and the others are not.
SOURCED_TYPE = "Sales"
#: Not sourced. The dashboard "combines all the analytics from your Dock sales
#: deal rooms and customer onboarding plans with data from your CRM", so an
#: Implementations view selects workspaces of an onboarding kind, but the research
#: never names the type string. Recorded as ``inferred`` below.
INFERRED_IMPLEMENTATION_TYPE = "Implementation"


def _condition(field: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def _columns(*keys: str) -> list[str]:
    return list(keys)


#: The five default views a rep starts from. ``definition`` is transcribed from
#: the research where the research defines the view, and is this build's reading
#: where it does not. ``inferred`` says which, per view.
DEFAULT_VIEWS: tuple[dict[str, Any], ...] = (
    {
        "id": "all",
        "label": "All Workspaces",
        "inferred": False,
        "definition": "The whole set. The catch-all every other view is a narrowing of, and the reason a view with no filters can never come back empty by accident.",
        "workspace_filters": {"join": "and", "conditions": []},
        "workspace_domain_filters": {"join": "and", "conditions": []},
        "match": MATCH_ALL,
        "columns": _columns(
            "dock.name",
            "dock.account",
            "dock.owner",
            "dock.type",
            "dock.stage",
            "dock.team",
            "engagement.views",
            "engagement.actions",
            "engagement.last_client_view",
            "dock.created_at",
        ),
        "sort": {"field": "dock.name", "direction": "asc"},
    },
    {
        "id": "my",
        "label": "My Workspaces",
        "inferred": True,
        "definition": "Workspaces this user owns. The research names the view and the filter dimension ('filter and sort views by owner') but not the view's own rule; owner == the requesting user is the reading.",
        "workspace_filters": {
            "join": "and",
            "conditions": [_condition("dock.owner", "is", ME)],
        },
        "workspace_domain_filters": {"join": "and", "conditions": []},
        "match": MATCH_ALL,
        "columns": _columns(
            "dock.name",
            "dock.account",
            "dock.owner",
            "dock.stage",
            "engagement.last_client_view",
        ),
        "sort": {"field": "engagement.last_client_view", "direction": "desc"},
    },
    {
        "id": "active-pipeline",
        "label": "Active Pipeline",
        "inferred": False,
        "quote": (
            "Any workspace that has a 'Sales' workspace type, or an opportunity or "
            "deal connected from your CRM"
        ),
        "definition": (
            "Two independent arms joined by OR. Arm one is the workspace's type; arm two "
            "is a joined CRM opportunity or deal, itself an OR because the research "
            "says 'an opportunity or deal'. A workspace with neither is out."
        ),
        # Arm one: a workspace property.
        "workspace_filters": {
            "join": "and",
            "conditions": [_condition("dock.type", "is", SOURCED_TYPE)],
        },
        # Arm two: joined CRM state, and the two joined objects are alternatives.
        "workspace_domain_filters": {
            "join": "or",
            "conditions": [
                _condition("salesforce.opportunity_stage", "is_not_empty", None),
                _condition("hubspot.deal_stage", "is_not_empty", None),
            ],
        },
        # The two arms are alternatives, so the groups are ORed.
        "match": MATCH_ANY,
        "columns": _columns(
            "dock.name",
            "dock.account",
            "dock.owner",
            "dock.type",
            "dock.stage",
            "salesforce.opportunity_stage",
            "salesforce.opp_amount",
            "hubspot.deal_stage",
            "hubspot.deal_amount",
            "engagement.last_client_view",
        ),
        "sort": {"field": "engagement.last_client_view", "direction": "desc"},
    },
    {
        "id": "deal-desk",
        "label": "Deal Desk",
        "inferred": False,
        "quote": "Any workspaces that uses Dock's order forms",
        "definition": "Any workspace with an order form attached. Presence, not status: a voided order form still put the workspace on the deal desk, and the Status column is there so a rep can see which ones need chasing.",
        "workspace_filters": {"join": "and", "conditions": []},
        "workspace_domain_filters": {
            "join": "and",
            "conditions": [_condition("order_form.status", "is_not_empty", None)],
        },
        "match": MATCH_ALL,
        "columns": _columns(
            "dock.name",
            "dock.account",
            "dock.owner",
            "order_form.status",
            "order_form.deal_type",
            "salesforce.opp_amount",
            "engagement.last_client_view",
        ),
        "sort": {"field": "order_form.status", "direction": "asc"},
    },
    {
        "id": "implementations",
        "label": "Implementations",
        "inferred": True,
        "definition": (
            "Workspaces of an onboarding kind. The dashboard covers 'customer onboarding "
            "plans' as well as sales rooms, and this is the view that separates them; "
            "the type string is this build's choice because the research does not name it."
        ),
        "workspace_filters": {
            "join": "and",
            "conditions": [_condition("dock.type", "is", INFERRED_IMPLEMENTATION_TYPE)],
        },
        "workspace_domain_filters": {"join": "and", "conditions": []},
        "match": MATCH_ALL,
        "columns": _columns(
            "dock.name",
            "dock.account",
            "dock.owner",
            "dock.type",
            "dock.stage",
            "engagement.last_client_view",
            "dock.created_at",
        ),
        "sort": {"field": "dock.created_at", "direction": "desc"},
    },
)

DEFAULT_VIEWS_BY_ID: dict[str, dict[str, Any]] = {view["id"]: view for view in DEFAULT_VIEWS}


def default_view(view_id: str) -> dict[str, Any] | None:
    """One default view definition, or ``None`` if the id is not one of the five."""
    return DEFAULT_VIEWS_BY_ID.get(view_id)


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #

#: "You can create **private views** for yourself or **public views** for your
#: entire team." Two values, no third, and the difference is who may read the
#: view - see :mod:`dsr.triage.inferences` for what that means on the write side.
PRIVATE = "private"
PUBLIC = "public"
VISIBILITIES = (PRIVATE, PUBLIC)


# --------------------------------------------------------------------------- #
# The served vocabulary
# --------------------------------------------------------------------------- #


def catalog() -> dict[str, Any]:
    """Everything a client needs to build the view editor, in one payload.

    One call rather than four, because a client that fetches the column list and
    the operator list separately can render a picker offering a combination the
    server refuses.
    """
    return {
        "columns": [dict(column) for column in COLUMNS],
        "column_keys": list(ALL_COLUMN_KEYS),
        "groups": {
            "workspace": list(WORKSPACE_GROUPS),
            "domain": list(DOMAIN_GROUPS),
        },
        "operators": [dict(entry) for entry in OPERATORS],
        "default_views": [default_view_summary(view) for view in DEFAULT_VIEWS],
        "match_modes": list(MATCH_MODES),
        "visibilities": list(VISIBILITIES),
        "joins": ["and", "or"],
        "unknown_operator": UNKNOWN_OPERATOR,
        "me_sentinel": ME,
    }


def default_view_summary(view: dict[str, Any]) -> dict[str, Any]:
    """A default view definition, ready to be materialised by ``Add view``."""
    return {
        "id": view["id"],
        "label": view["label"],
        "inferred": view["inferred"],
        "definition": view["definition"],
        "quote": view.get("quote"),
        "workspace_filters": _copy_group(view["workspace_filters"]),
        "workspace_domain_filters": _copy_group(view["workspace_domain_filters"]),
        "match": view["match"],
        "columns": list(view["columns"]),
        "sort": dict(view["sort"]),
    }


def _copy_group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "join": group["join"],
        "conditions": [dict(condition) for condition in group["conditions"]],
    }
