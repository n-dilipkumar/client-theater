"""The values WF-030's research fixes by name, and the ones it leaves open.

Everything in this module is either quoted from the research for WF-030 (section
15 of ``docs/research/raw/analytics-intent.md``) or a pure function over the
schema-flexible payloads the audited store holds. No database, no network, no
clock: the whole module is cheap to test and safe to import from anywhere.

The five filter families
------------------------

"When you select Dock, you'll see five different options for your filter. You have
the option to select from Analytics events (Views, Clicks, Downloads, or
Interactions), or MAP activity (movement related to project plans in the
workspace)."

That is four analytics families plus MAP activity, and the count is five. Both
halves are load-bearing: the four analytics families come from the first clause
and MAP activity from the second, so a reader who counted only the analytics
events would publish a four-family integration.

The per-family refinements
--------------------------

Two sentences in the sources pin the matrix, and they disagree about which keys
exist rather than about what they mean, so both are honoured:

* "**Downloads:** filter by date and/or file name. **Views:** filter by date."
* "Clicks/Interactions by date or link URL" (user flow, step 6)

MAP activity is "Filter by activity text - e.g. 'completed task \"Sign up for free
account\"'", and the lead-scoring article this workflow also cites adds "For MAP
activity, you can also refine by 'Occurred', but then also refine by task name to
give certain tasks more weight than others" - which is why ``map_activity`` has two
refinements rather than one.

So the matrix is not a preference; it is the researched surface, and
:func:`describe` publishes it so a client builds its filter editor from the same
table the validator checks against.

Why the filter list is closed and the action list is not
----------------------------------------------------------

This is the asymmetry this package turns on, and it is worth stating because a
reviewer will notice that one list refuses and the other warns.

*Filters.* "you'll see **five different options** for your filter." That is an
enumeration of what the integration offers, and it is closed. A criteria naming a
sixth family names something the integration cannot evaluate, so
:class:`~dsr.crm_workflows.errors.UnknownFilterFamily` refuses it.

*Actions.* "you can use HubSpot's Workflows to send emails, slack notifications,
update fields, change stages **and more!**" That is explicitly open, and the
extensibility line says the same thing from the other direction: "Vendors add new
trigger families by extending the integration's filterable properties." So an
action kind outside the four is **stored and reported unresolved**, never dropped -
exactly what the schema-flexibility rule requires of a field a team added without
coordinating with anyone. A silently dropped action is the failure this product
is built to avoid; a stored action with a visible ``unresolved`` marker is the
honest version of the same fact.

The ``family`` table for *this* product's activity
-------------------------------------------------

The research names Dock's analytics-event properties. This product's own activity
stream uses its own words (``viewed``, ``downloaded``, ``shared``, ``commented``,
``opened_link``, ``completed_section``), so the mapping from those words onto the
five families is this build's, not the source's. It is published in
:data:`ACTION_FAMILIES` rather than compiled into the matcher, for two reasons: a
client renders its picker from it, and a team whose activity rows use a word this
table does not know can assert the family directly on the event
(``action_family``) instead of needing a code change.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.crm_workflows.errors import (
    UnknownFilterFamily,
    UnknownTriggerMode,
    UnsupportedRefinement,
    WorkflowError,
)

# --------------------------------------------------------------------------- #
# The integration
# --------------------------------------------------------------------------- #

#: The CRM whose workflow surface the research describes. It is a *default*, not a
#: constant: "Vendors add new trigger families by extending the integration's
#: filterable properties" is a statement about more than one integration existing,
#: and the trigger names its integration as data. Nothing in this package refuses a
#: workflow for naming a different one - an unregistered name is a missing
#: prerequisite, reported by name, not a category error.
DEFAULT_INTEGRATION = "hubspot"

# --------------------------------------------------------------------------- #
# The five filter families
# --------------------------------------------------------------------------- #

#: The complete published family list. Exactly five, and asserting the count is
#: load-bearing: the first clause of the quoted sentence names four analytics
#: events and the second adds MAP activity.
FILTER_FAMILIES: tuple[str, ...] = (
    "views",
    "clicks",
    "downloads",
    "interactions",
    "map_activity",
)

#: Which half of the quoted sentence each family comes from. Served so a client
#: can group the picker the way the source groups it.
FAMILY_GROUP: dict[str, str] = {
    "views": "analytics_events",
    "clicks": "analytics_events",
    "downloads": "analytics_events",
    "interactions": "analytics_events",
    "map_activity": "map_activity",
}

#: The one-line gloss for each family, in the source's own words where it has them.
FAMILY_LABEL: dict[str, str] = {
    "views": "Views - filter by date.",
    "clicks": "Clicks - filter by date or link URL.",
    "downloads": "Downloads - filter by date and/or file name.",
    "interactions": "Interactions - filter by date or link URL.",
    "map_activity": "MAP activity - filter by date and/or activity text.",
}

# --------------------------------------------------------------------------- #
# The refinement matrix
# --------------------------------------------------------------------------- #

#: Which refinements each family publishes. This table is the researched surface;
#: a refinement outside a family's row is refused, not warned about.
REFINEMENTS: dict[str, tuple[str, ...]] = {
    # "**Views:** filter by date." - date only, which is why refining Views by a
    # file name is a category error rather than a preference.
    "views": ("occurred",),
    # "Clicks/Interactions by date or link URL" (user flow step 6).
    "clicks": ("occurred", "link_url"),
    # "**Downloads:** filter by date and/or file name."
    "downloads": ("occurred", "file_name"),
    "interactions": ("occurred", "link_url"),
    # "Filter by activity text", plus the cited lead-scoring article: "For MAP
    # activity, you can also refine by 'Occurred', but then also refine by task
    # name to give certain tasks more weight than others."
    "map_activity": ("occurred", "activity_text"),
}

#: The complete refinement vocabulary, so a client can build one control list.
ALL_REFINEMENTS: tuple[str, ...] = tuple(
    sorted({name for names in REFINEMENTS.values() for name in names})
)

#: What each refinement is matched against, for a client that has to label a field.
REFINEMENT_LABEL: dict[str, str] = {
    "occurred": "Occurred - a UTC date, or a {from, to} window.",
    "link_url": "Link URL - the URL the contact clicked or interacted with.",
    "file_name": "File name - the name of the file the contact downloaded.",
    "activity_text": "Activity text - MAP movement text, e.g. completed task 'Intro call'.",
}

# --------------------------------------------------------------------------- #
# The trigger
# --------------------------------------------------------------------------- #

#: "Trigger: **When filter criteria is met**". The research documents this one mode
#: and no other, so any other mode is refused rather than stored.
TRIGGER_MODES: tuple[str, ...] = ("filter_criteria_met",)

#: ""Dock only supports Contact based workflows since the activities are tied to the
#: contact record." One value, and it is the only value.
ENROLLMENT_TYPES: tuple[str, ...] = ("contact",)

#: Both researched paths for getting the same events in. "Configuration is in the
#: HubSpot UI" is the filter path; "Dock's alternative path is to send the same
#: events as webhooks into a HubSpot workflow webhook endpoint" is the webhook
#: path, and Dock documents it as its own pattern ("Dock Webhooks + HubSpot
#: Workflows"). An enrollment records which one delivered it.
DELIVERY_PATHS: tuple[str, ...] = ("filter", "webhook")

# --------------------------------------------------------------------------- #
# The actions
# --------------------------------------------------------------------------- #

#: "send emails, send Slack notifications, update HubSpot fields, change stages" /
#: "send emails, slack notifications, update fields, change stages and more!".
#: Closed for *resolution*, open for *storage* - see this module's docstring.
ACTION_KINDS: tuple[str, ...] = (
    "send_email",
    "slack_notification",
    "update_field",
    "change_stage",
)

ACTION_LABEL: dict[str, str] = {
    "send_email": "Send an email to the contact.",
    "slack_notification": "Notify the deal's Slack channel.",
    "update_field": "Write a value onto a HubSpot contact property.",
    "change_stage": "Change the contact's stage.",
}

#: The three examples the source gives for what a workflow is *for*, kept beside
#: the action list because a reviewer checking whether a feature is doing this
#: workflow's job should be able to see the three cases side by side.
RESEARCHED_EXAMPLES: tuple[str, ...] = (
    "To trigger emails and/or slack notifications based on Dock activity.",
    "Change stages in HubSpot based on onboarding or mutual action plan tasks.",
    "Update HubSpot fields based on Dock activity.",
)

# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #

#: HubSpot's lifecycle stages, in stage order. The order is the whole point: "When
#: you include the `lifecyclestage` property, you can only set the value *forward*
#: in the stage order" (the lead-scoring article this workflow cites). That is a
#: hard constraint on one of the four action kinds, and it is why this is a tuple
#: and not a set.
LIFECYCLE_STAGES: tuple[str, ...] = (
    "subscriber",
    "lead",
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
    "evangelist",
    "other",
)

#: A deal/pipeline stage is not a lifecycle stage and has no forward-only rule, so
#: "Change stages in HubSpot based on onboarding or mutual action plan tasks" can
#: move one in either direction. A ``change_stage`` action says which it is.
STAGE_KINDS: tuple[str, ...] = ("lifecyclestage", "deal_stage")

# --------------------------------------------------------------------------- #
# This product's activity words -> the five families
# --------------------------------------------------------------------------- #

#: How this product's own ``activity`` rows map onto the five published families.
#:
#: The research names Dock's analytics-event properties; this product's activity
#: stream uses its own vocabulary, so this mapping is a build decision and is
#: published as data rather than compiled into the matcher. An action word that is
#: not in this table is **not** dropped - it is reported ``unclassified`` with the
#: word named, and the event can carry ``action_family`` to assert the family
#: directly.
ACTION_FAMILIES: dict[str, str] = {
    "viewed": "views",
    "view": "views",
    "downloaded": "downloads",
    "download": "downloads",
    "opened_link": "clicks",
    "clicked": "clicks",
    "click": "clicks",
    "commented": "interactions",
    "shared": "interactions",
    "interaction": "interactions",
    # "movement related to project plans in the workspace" - a completed section or
    # task in a mutual action plan.
    "completed_section": "map_activity",
    "completed_task": "map_activity",
    "plan_task_completed": "map_activity",
}

#: Where each part of an event is read from, in priority order.
#:
#: Record payloads are arbitrary JSON, so a read that hard-codes one spelling is a
#: read that returns ``None`` for every team but the one that wrote the first row.
#: These are candidate keys, tried in order, exactly as the rest of this product
#: treats its schema-flexible records.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "contact": ("contact", "person", "user", "buyer", "email", "contact_email", "user.email"),
    "action": ("action", "event", "activity", "type", "kind"),
    "target": ("target", "document", "document_name", "title", "item", "file", "filename"),
    "link_url": ("link_url", "url", "link", "href", "target_url"),
    "file_name": ("file_name", "filename", "file", "document_name", "target", "document"),
    "activity_text": ("activity_text", "text", "task_name", "task", "plan_task", "movement"),
    "occurred_at": ("occurred_at", "at", "timestamp", "time", "created", "happened_at"),
    "account": ("account", "company", "organisation", "organization"),
    "room_id": ("room_id", "workspace_id", "workspace", "deal_id"),
}

# --------------------------------------------------------------------------- #
# What the workflow does *not* do
# --------------------------------------------------------------------------- #

#: "The workflow is the automation; it fires continuously on matching activity.
#: **Nothing happens on the seller's screen.**"
#:
#: Repeated beside every enrollment, every summary, and every page header, because
#: a seller who believes an enrollment is a task will stop acting on the ones that
#: are. This is the same commitment WF-027 makes about intent signals, and here it
#: is the research's own sentence rather than a reading of one.
ACTIONABILITY_NOTE = (
    "Nothing happens on the seller's screen. A CRM workflow fires continuously on "
    "matching activity, so an enrollment here is a record of what the workflow did, "
    "not a task for anyone."
)

#: The write side of the researched integration, recorded rather than performed.
#:
#: "The write side is HubSpot's CRM API (PATCH /crm/v3/objects/contacts/{contactId},
#: POST /crm/v3/objects/contacts/batch/upsert) and HubSpot workflow APIs." This build
#: records what would be written and says so, rather than opening a socket. The
#: endpoint is carried in the *response body* as provenance and is never used as an
#: audit ``source`` - the audit row names the route that served the request, which
#: is this app's own route.
CRM_WRITE_ENDPOINTS: dict[str, str] = {
    "send_email": "HubSpot workflow action: send email",
    "slack_notification": "HubSpot workflow action: Slack notification",
    "update_field": "PATCH /crm/v3/objects/contacts/{contactId}",
    "change_stage": "PATCH /crm/v3/objects/contacts/{contactId}",
}

EXECUTION_NOTE = (
    "Recorded, not executed. The research names HubSpot's CRM API as the write "
    "side; this build resolves each action against the contact and the room and "
    "records what would be written, so a team with a real HubSpot client can add an "
    "executor without a migration."
)


# --------------------------------------------------------------------------- #
# Require / normalise
# --------------------------------------------------------------------------- #


def is_family(name: Any) -> bool:
    return isinstance(name, str) and name.strip().lower() in FILTER_FAMILIES


def require_family(name: Any) -> str:
    """Resolve a family name, or refuse it naming the five."""
    if not isinstance(name, str) or not name.strip():
        raise UnknownFilterFamily(
            f"a filter must name one of the five published families: "
            f"{', '.join(FILTER_FAMILIES)}"
        )
    candidate = name.strip().lower()
    if candidate not in FILTER_FAMILIES:
        raise UnknownFilterFamily(
            f"unknown filter family {name!r}. The research says the integration "
            f"offers five options - {', '.join(FILTER_FAMILIES)} - so a filter on "
            f"{name!r} could never be evaluated."
        )
    return candidate


def refinements_for(family: str) -> tuple[str, ...]:
    return REFINEMENTS.get(family, ())


def require_refinement(family: str, name: Any) -> str:
    """Resolve a refinement against the family's published row, or refuse it."""
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("a refinement needs a name")
    candidate = name.strip().lower()
    allowed = REFINEMENTS.get(family, ())
    if candidate not in allowed:
        published = ", ".join(allowed) or "none"
        raise UnsupportedRefinement(
            f"{family} is not refined by {name!r}. The research publishes these "
            f"refinements for {family}: {published}."
        )
    return candidate


def require_trigger_mode(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        return TRIGGER_MODES[0]
    candidate = name.strip().lower()
    if candidate not in TRIGGER_MODES:
        raise UnknownTriggerMode(
            f"unknown trigger mode {name!r}. The documented trigger is "
            f"'When filter criteria is met' ({TRIGGER_MODES[0]})."
        )
    return candidate


def lifecycle_rank(stage: Any) -> int | None:
    """Where a lifecycle stage sits in stage order, or ``None`` if it is not one."""
    if not isinstance(stage, str):
        return None
    candidate = stage.strip().lower()
    try:
        return LIFECYCLE_STAGES.index(candidate)
    except ValueError:
        return None


def family_for_action(action: Any) -> str | None:
    """The family an action word belongs to, or ``None`` if the table has none."""
    if not isinstance(action, str):
        return None
    return ACTION_FAMILIES.get(action.strip().lower())


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of an arbitrary JSON payload.

    A numeric segment indexes a list, which is how the dynamic index stores array
    elements, so ``actions.0.kind`` reads back what was written.
    """
    current = payload
    for part in str(path).split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            if -len(current) <= index < len(current):
                current = current[index]
            else:
                return default
        else:
            return default
    return current


def first_present(data: Mapping[str, Any] | None, key: str) -> Any:
    """The first alias that resolves to something usable, or ``None``.

    ``FIELD_ALIASES`` is the contract between an arbitrary activity payload and
    this package. ``None`` means the payload does not carry the fact at all, which
    the matcher reports as unverifiable rather than treating as a value - a
    refinement that cannot be checked must not pass quietly.

    A non-scalar counts as absent, deliberately. A Dock webhook carries
    ``"user": {"id": ..., "email": ...}``, so the alias ``user`` resolves to an
    object rather than to the buyer; the list continues and ``user.email`` is tried
    next, which is the answer. Returning the object would make every text read
    downstream compare a dict against a string and quietly never match.
    """
    if not isinstance(data, Mapping):
        return None
    for path in FIELD_ALIASES.get(key, ()):
        found = dig(data, path)
        if found is None:
            continue
        if isinstance(found, (Mapping, list, tuple, set)):
            continue
        if isinstance(found, str) and not found.strip():
            continue
        return found
    return None


def pick(data: Mapping[str, Any] | None, key: str, default: Any = "") -> Any:
    found = first_present(data, key)
    return default if found is None else found


def describe() -> dict[str, Any]:
    """Every published vocabulary, served as data.

    A client builds its filter editor, its action editor and its family picker
    from this rather than from a list compiled into the page, so a family or a
    refinement added server-side reaches every client at once - and the validator
    and the editor can never disagree about what is legal.
    """
    return {
        "integration": {
            "default": DEFAULT_INTEGRATION,
            "note": (
                "The trigger names its integration as data. Nothing here refuses a "
                "workflow for naming a different one; an unregistered name is a "
                "missing prerequisite and is reported by name."
            ),
        },
        "filter_families": [
            {
                "name": name,
                "group": FAMILY_GROUP[name],
                "label": FAMILY_LABEL[name],
                "refinements": list(REFINEMENTS[name]),
            }
            for name in FILTER_FAMILIES
        ],
        "filter_family_count": len(FILTER_FAMILIES),
        "refinements": [
            {"name": name, "label": REFINEMENT_LABEL[name]} for name in ALL_REFINEMENTS
        ],
        "refinement_matrix": {family: list(names) for family, names in REFINEMENTS.items()},
        "trigger_modes": list(TRIGGER_MODES),
        "enrollment_types": list(ENROLLMENT_TYPES),
        "delivery_paths": list(DELIVERY_PATHS),
        "action_kinds": [
            {"kind": kind, "label": ACTION_LABEL[kind], "resolvable": True}
            for kind in ACTION_KINDS
        ],
        "action_kind_note": (
            "The four kinds above are the ones this build resolves. Any other kind is "
            "stored and reported unresolved rather than refused, because the source "
            "says 'and more!' - the action list is open where the filter list is "
            "closed."
        ),
        "researched_examples": list(RESEARCHED_EXAMPLES),
        "stage_kinds": list(STAGE_KINDS),
        "lifecycle_stages": list(LIFECYCLE_STAGES),
        "lifecycle_constraint": (
            "When you include the `lifecyclestage` property, you can only set the value "
            "*forward* in the stage order. A change_stage action of kind lifecyclestage "
            "naming a stage at or behind the contact's current one is reported refused "
            "for that action; the rest of the workflow still applies."
        ),
        "action_families": dict(ACTION_FAMILIES),
        "action_family_note": (
            "How this product's activity words map onto the five families. An action "
            "word that is not in this table is reported unclassified and the word is "
            "named; the event may assert action_family directly instead."
        ),
        "field_aliases": {key: list(paths) for key, paths in FIELD_ALIASES.items()},
        "actionability_note": ACTIONABILITY_NOTE,
        "crm_write_endpoints": dict(CRM_WRITE_ENDPOINTS),
        "execution_note": EXECUTION_NOTE,
    }


def published_family_names(families: Iterable[str]) -> list[str]:
    """Canonical family names in published order, de-duplicated."""
    seen = {name for name in families if name in FILTER_FAMILIES}
    return [name for name in FILTER_FAMILIES if name in seen]
