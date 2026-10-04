"""The values WF-029's research fixes by name, and the ones it leaves open.

Everything in this module is either quoted from the research for WF-029 (section 14
of ``docs/research/raw/analytics-intent.md``) or a pure function over the
schema-flexible payloads the audited store holds. No database, no network, no
clock: the whole module is cheap to test and safe to import from anywhere.

The score property
------------------

"In HubSpot, lead scoring is automatically created as a contact property as
'HubSpot Score'." One property, named by the research, and it is a *contact*
property rather than a company or deal property, which is what makes the whole
workflow per-buyer rather than per-account.

The two buckets
---------------

"Click Add criteria for either positive or negative scores." Two buckets, and the
bucket is chosen *before* the value: "Setup filters and assign score." So a score is
a magnitude and the bucket carries the sign. That is why
:func:`require_bucket` refuses anything but the two names and why
:class:`~dsr.lead_score.errors.InvalidScoreValue` refuses a negative number rather
than reading it as a bucket.

The five Dock options
--------------------

Step 4 says "Scroll down until you see the Dock options in the lead score system.
Choose the Dock property you want to score against." The options are the four
analytics events -- Views, Clicks, Downloads, Interactions -- plus MAP activity.
That is the same closed list WF-030 publishes for its filter families, because both
workflows read the same integration's activity properties, and this package states
its own copy rather than importing WF-030's: the boundary between two features is
a contract, and Jev chose ``self_contained`` over ``reuse_crm_workflows`` at audit
``jev-20261004T024139-28028-99762``.

The per-family refinements
--------------------------

The lead-scoring article is more specific than the workflow article about which
filters are worth setting, and the specificity is the useful part:

* "For activities like Clicks, Downloads, Interactions and Views, we recommend using
  the 'Occurred' filter as a baseline. From there, you can add more refinement
  around the link name or file name."
* "For MAP activity, you can also refine by 'Occurred', but then also refine by task
  name to give certain tasks more weight than others."

So ``occurred`` is a *recommended baseline* on all five families -- a warning when it
is missing, never a refusal, because the research says "recommend" and not
"require". The second refinement is per family and named in the source's own words:
the link name for Clicks and Interactions, the file name for Downloads, the task
name for MAP activity. Views has no second refinement named anywhere, so its row
holds ``occurred`` alone.

The CRM write side
------------------

The research names four endpoints and two scopes. They are recorded here and
attached to every run as a plan; nothing in this package opens a socket. See
:data:`CRM_PLAN` and the ``write_scope`` inference.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.lead_score.errors import (
    UnknownScoreBucket,
    UnknownScoreFamily,
    UnknownScoreProperty,
    UnsupportedRefinement,
)

# --------------------------------------------------------------------------- #
# The integration
# --------------------------------------------------------------------------- #

#: The CRM whose lead-scoring surface the research describes. A *default*, not a
#: constant: the criterion records its organisation as data, and nothing here refuses
#: a criterion for naming a different one. An unregistered name is a missing
#: prerequisite, reported by name, not a category error.
DEFAULT_INTEGRATION = "hubspot"

#: "In HubSpot, lead scoring is automatically created as a contact property as
#: 'HubSpot Score'." Quoted exactly, including the capital S, because a reader
#: searching their CRM for the property is searching for this string.
SCORE_PROPERTY = "HubSpot Score"

#: The two scopes the research names. Both are needed before a criterion can be
#: armed: read to look the contact up, write to move the score.
REQUIRED_SCOPES: tuple[str, ...] = (
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
)

# --------------------------------------------------------------------------- #
# The buckets
# --------------------------------------------------------------------------- #

#: "Click Add criteria for either positive or negative scores." Two, closed.
BUCKETS: tuple[str, ...] = ("positive", "negative")

#: The sign each bucket applies. The score value stays a magnitude; the sign lives
#: here, in one place, so "added/subtracted from the HubSpot Score contact
#: property" has exactly one implementation.
BUCKET_SIGN: dict[str, int] = {"positive": 1, "negative": -1}

BUCKET_LABEL: dict[str, str] = {
    "positive": "Positive score - matching activity adds to the contact's score.",
    "negative": "Negative score - matching activity subtracts from the contact's score.",
}

# --------------------------------------------------------------------------- #
# The five Dock activity properties
# --------------------------------------------------------------------------- #

#: The complete published list. Exactly five, and asserting the count is
#: load-bearing: the analytics events are four and MAP activity is the fifth, so a
#: reader who counted only the analytics events would publish a four-option
#: integration.
FILTER_FAMILIES: tuple[str, ...] = (
    "views",
    "clicks",
    "downloads",
    "interactions",
    "map_activity",
)

#: Which half of the sentence each property comes from. Served so a client can group
#: the picker the way the source groups it: four analytics events, then MAP activity.
FAMILY_GROUP: dict[str, str] = {
    "views": "analytics_events",
    "clicks": "analytics_events",
    "downloads": "analytics_events",
    "interactions": "analytics_events",
    "map_activity": "map_activity",
}

#: The one-line gloss for each property, in the source's own words where it has them.
FAMILY_LABEL: dict[str, str] = {
    "views": "Views - analytics event. Recommended filter: Occurred.",
    "clicks": "Clicks - analytics event. Recommended filters: Occurred, then the link name.",
    "downloads": "Downloads - analytics event. Recommended filters: Occurred, then the file name.",
    "interactions": "Interactions - analytics event. Recommended filters: Occurred, then the "
    "link name.",
    "map_activity": "MAP activity - movement on a plan task. Recommended filters: Occurred, "
    "then the task name.",
}

#: Which refinements each property publishes. This table is the researched surface;
#: a refinement outside a family's row is refused, not warned about, because the
#: source names the second filter per family and nothing else.
REFINEMENTS: dict[str, tuple[str, ...]] = {
    # "For activities like Clicks, Downloads, Interactions and Views, we recommend using
    # the 'Occurred' filter as a baseline. From there, you can add more refinement
    # around the link name or file name." Views is named in that sentence but has no
    # second refinement attributed to it anywhere, so its row holds the baseline only.
    "views": ("occurred",),
    "clicks": ("occurred", "link_name"),
    "downloads": ("occurred", "file_name"),
    "interactions": ("occurred", "link_name"),
    # "For MAP activity, you can also refine by 'Occurred', but then also refine by task
    # name to give certain tasks more weight than others."
    "map_activity": ("occurred", "task_name"),
}

#: The complete refinement vocabulary, so a client can build one control list.
ALL_REFINEMENTS: tuple[str, ...] = tuple(
    sorted({name for names in REFINEMENTS.values() for name in names})
)

#: What each refinement is matched against, for a client that has to label a field.
REFINEMENT_LABEL: dict[str, str] = {
    "occurred": "Occurred - a UTC date, or a {from, to} window. The recommended baseline.",
    "link_name": "Link name - the URL the contact clicked or interacted with.",
    "file_name": "File name - the name of the file the contact downloaded.",
    "task_name": "Task name - the plan task the contact completed, e.g. 'Intro call'.",
}

#: Spellings a caller may use for a refinement, resolved to the published name.
#:
#: Record payloads are arbitrary JSON and a client building a form will produce one
#: spelling or the other, so both are accepted rather than one being refused for
#: being a synonym. The published name is always the canonical one, which is what a
#: stored criterion and a response carry.
REFINEMENT_ALIASES: dict[str, str] = {
    "occurred_at": "occurred",
    "date": "occurred",
    "occurred_window": "occurred",
    "link": "link_name",
    "link_url": "link_name",
    "url": "link_name",
    "file": "file_name",
    "filename": "file_name",
    "document_name": "file_name",
    "activity_text": "task_name",
    "task": "task_name",
    "plan_task": "task_name",
}

#: The refinement the research *recommends* as a baseline. A recommendation, so a
#: criterion without one is warned about rather than refused -- see the
#: ``occurred-is-a-recommendation-not-a-requirement`` inference.
BASELINE_REFINEMENT = "occurred"

BASELINE_QUOTE = (
    "For activities like Clicks, Downloads, Interactions and Views, we recommend using the "
    "'Occurred' filter as a baseline. From there, you can add more refinement around the link "
    "name or file name."
)

MAP_TASK_QUOTE = (
    "For MAP activity, you can also refine by 'Occurred', but then also refine by task name to "
    "give certain tasks more weight than others."
)

# --------------------------------------------------------------------------- #
# This product's activity words -> the five properties
# --------------------------------------------------------------------------- #

#: How this product's own activity words map onto the five published properties.
#:
#: The research names the integration's analytics-event properties; this product's
#: activity stream uses its own words (``viewed``, ``downloaded``, ``shared``,
#: ``commented``, ``opened_link``, ``completed_section``), so the mapping is this
#: build's. It is published as data rather than compiled into the matcher, for two
#: reasons: a client renders its picker from it, and a team whose activity rows use a
#: word this table does not know can assert the property directly on the event
#: (``action_family``) instead of needing a code change.
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
    "completed_section": "map_activity",
    "completed_task": "map_activity",
    "plan_task_completed": "map_activity",
}

#: Where each part of an event is read from, in priority order.
#:
#: Record payloads are arbitrary JSON, so a read that hard-codes one spelling is a
#: read that returns ``None`` for every team but the one that wrote the first row.
#: These are candidate keys, tried in order.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "contact": ("contact", "person", "user", "buyer", "email", "contact_email", "user.email"),
    "action": ("action", "event", "activity", "type", "kind"),
    "target": ("target", "document", "document_name", "title", "item"),
    "link_name": ("link_name", "link_url", "url", "link", "href", "target_url"),
    "file_name": ("file_name", "filename", "file", "document_name", "target", "document"),
    "task_name": ("task_name", "activity_text", "text", "task", "plan_task", "movement"),
    "occurred_at": ("occurred_at", "at", "timestamp", "time", "created", "happened_at"),
    "account": ("account", "company", "organisation", "organization"),
    "room_id": ("room_id", "workspace_id", "workspace", "deal_id"),
}

# --------------------------------------------------------------------------- #
# The lifecycle-stage constraint
# --------------------------------------------------------------------------- #

#: HubSpot's lifecycle stages, in stage order. The order is the whole point: "When
#: you include the `lifecyclestage` property, you can only set the value *forward* in
#: the stage order." A tuple and not a set, because a set cannot answer "is this
#: move backwards".
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

LIFECYCLE_CONSTRAINT = (
    "When you include the `lifecyclestage` property, you can only set the value *forward* in the "
    "stage order."
)

# --------------------------------------------------------------------------- #
# The CRM write side
# --------------------------------------------------------------------------- #

#: The endpoints the research names, recorded rather than called.
#:
#: "The write side of the CRM primitives is HubSpot's: PATCH
#: /crm/v3/objects/contacts/{contactId} to write a score-like contact property, POST
#: /crm/v3/objects/contacts/batch/upsert, and POST
#: /crm/v4/associations/{fromObjectType}/{toObjectType}/labels to read association
#: type IDs." Plus the extensibility path, "writing custom contact properties via POST
#: /crm/v3/properties".
#:
#: Every run carries the request bodies these endpoints would receive, and every one
#: of them is marked ``executed: false``. The endpoint is carried in the *response
#: body* as provenance and is never used as an audit ``source`` -- the audit row
#: names the route that served the request, which is this app's own route.
CRM_PLAN: dict[str, str] = {
    "write_contact": "PATCH /crm/v3/objects/contacts/{contactId}",
    "batch_upsert": "POST /crm/v3/objects/contacts/batch/upsert",
    "association_labels": "POST /crm/v4/associations/{fromObjectType}/{toObjectType}/labels",
    "create_property": "POST /crm/v3/properties",
}

EXECUTION_NOTE = (
    "Recorded, not executed. The research names HubSpot's CRM API as the write side and this "
    "build holds no CRM credential, so each run resolves exactly what would be written and says "
    "so. A team with a real HubSpot client adds an executor against the recorded plan without a "
    "migration: the request bodies are already on the run."
)

#: The property name the batch upsert would create for a contact that the CRM does
#: not hold yet. Recorded as the plan's key field, because the research's batch
#: endpoint is an *upsert* and therefore needs a key.
BATCH_KEY_FIELD = "email"

# --------------------------------------------------------------------------- #
# What this workflow does *not* do
# --------------------------------------------------------------------------- #

#: "The scoring rule is continuous - every matching Dock activity event re-evaluates
#: the score without user action." Nothing happens on the seller's screen, and the
#: sentence is repeated in every summary and next to every score for the same reason
#: WF-030 repeats it: a seller who believes a score change is a task will stop acting
#: on the ones that are.
ACTIONABILITY_NOTE = (
    "Nothing happens on the seller's screen. The scoring rule is continuous, so a score that "
    "moved is a record of what the buyer's activity did, not a task for anyone."
)

#: The research offers this only as an analogy: "'auto-add will only add companies
#: that enter your saved views after enabling the auto-add' is the analogous HubSpot
#: behaviour for buyer intent." It describes a different workflow over companies, not
#: this one over contacts, so it is recorded as out of scope rather than implemented.
AUTO_ADD_NOTE = (
    "Out of scope. 'auto-add will only add companies that enter your saved views after enabling "
    "the auto-add' is described in this research as the analogous HubSpot behaviour for buyer "
    "intent. It governs adding companies to a saved view, not scoring contacts, so this workflow "
    "does not reproduce it."
)


# --------------------------------------------------------------------------- #
# Require / normalise
# --------------------------------------------------------------------------- #


def require_family(name: Any) -> str:
    """Resolve a Dock activity property name, or refuse it naming the five."""
    if not isinstance(name, str) or not name.strip():
        raise UnknownScoreFamily(
            f"a criterion must name one of the five published Dock properties: "
            f"{', '.join(FILTER_FAMILIES)}"
        )
    candidate = name.strip().lower()
    if candidate not in FILTER_FAMILIES:
        raise UnknownScoreFamily(
            f"unknown Dock property {name!r}. Step 4 of the researched flow says to choose from "
            f"the Dock options in the lead-score system, and those are "
            f"{', '.join(FILTER_FAMILIES)} - so a criterion on {name!r} would never match an "
            f"event."
        )
    return candidate


def refinements_for(family: str) -> tuple[str, ...]:
    return REFINEMENTS.get(family, ())


def require_refinement(family: str, name: Any) -> str:
    """Resolve a filter name against the family's published row, or refuse it."""
    if not isinstance(name, str) or not name.strip():
        raise UnsupportedRefinement("a filter needs a name")
    candidate = name.strip().lower()
    canonical = REFINEMENT_ALIASES.get(candidate, candidate)
    allowed = REFINEMENTS.get(family, ())
    if canonical not in allowed:
        published = ", ".join(allowed) or "none"
        raise UnsupportedRefinement(
            f"{family} is not filtered by {name!r}. The research recommends "
            f"{published} for {family}, and names no other filter for it."
        )
    return canonical


def require_bucket(name: Any) -> str:
    """Resolve a bucket name, or refuse it naming the two."""
    if not isinstance(name, str) or not name.strip():
        raise UnknownScoreBucket(
            f"a criterion must name one of the two score buckets: {', '.join(BUCKETS)}. "
            f"'Click Add criteria for either positive or negative scores.'"
        )
    candidate = name.strip().lower()
    if candidate not in BUCKETS:
        raise UnknownScoreBucket(
            f"unknown score bucket {name!r}. The researched flow offers two: {', '.join(BUCKETS)}."
        )
    return candidate


def require_score_property(name: Any) -> str:
    """Resolve the contact property a criterion writes.

    The default is the researched property. Any other name is *accepted and
    reported unresolved*, because the extensibility note says third parties write
    their own contact properties, and this product's contract is that a field nobody
    coordinated with us is still stored and visible. Only a value that is not a name
    is refused, because then there is nothing to report.
    """
    if name is None or (isinstance(name, str) and not name.strip()):
        return SCORE_PROPERTY
    if not isinstance(name, str):
        raise UnknownScoreProperty(
            f"a score property must be a contact property name; got {name!r}"
        )
    candidate = " ".join(name.split()).strip()
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
    """The Dock property an action word belongs to, or ``None`` if the table has none."""
    if not isinstance(action, str):
        return None
    return ACTION_FAMILIES.get(action.strip().lower())


def dig(payload: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path out of an arbitrary JSON payload.

    A numeric segment indexes a list, which is how the dynamic index stores array
    elements, so ``contributions.0.score`` reads back what was written.
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

    ``FIELD_ALIASES`` is the contract between an arbitrary activity payload and this
    package. ``None`` means the payload does not carry the fact at all, which the
    matcher reports as unverifiable rather than treating as a value.

    A non-scalar counts as absent, deliberately. A webhook carries
    ``"user": {"id": ..., "email": ...}``, so the alias ``user`` resolves to an object
    rather than to the buyer; the list continues and ``user.email`` is tried next,
    which is the answer. Returning the object would make every text read downstream
    compare a dict against a string and quietly never match.
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


def published_family_names(families: Any) -> list[str]:
    """Canonical Dock property names in published order, de-duplicated."""
    seen = {
        name.strip().lower() for name in (families or []) if isinstance(name, str) and name.strip()
    }
    return [name for name in FILTER_FAMILIES if name in seen]


def describe() -> dict[str, Any]:
    """Every published vocabulary, served as data.

    A client builds its criterion editor, its family picker and its bucket picker
    from this rather than from a list compiled into the page, so a property or a
    filter added server-side reaches every client at once, and the validator and the
    editor can never disagree about what is legal.
    """
    return {
        "integration": {
            "default": DEFAULT_INTEGRATION,
            "required_scopes": list(REQUIRED_SCOPES),
            "note": (
                "The criterion records its organisation as data. Nothing here refuses a "
                "criterion for naming a different one; an unregistered name is a missing "
                "prerequisite and is reported by name."
            ),
        },
        "score_property": SCORE_PROPERTY,
        "buckets": [
            {
                "name": name,
                "sign": BUCKET_SIGN[name],
                "label": BUCKET_LABEL[name],
            }
            for name in BUCKETS
        ],
        "families": [
            {
                "name": name,
                "group": FAMILY_GROUP[name],
                "label": FAMILY_LABEL[name],
                "refinements": list(REFINEMENTS[name]),
            }
            for name in FILTER_FAMILIES
        ],
        "family_count": len(FILTER_FAMILIES),
        "refinements": [
            {"name": name, "label": REFINEMENT_LABEL[name]} for name in ALL_REFINEMENTS
        ],
        "refinement_matrix": {family: list(names) for family, names in REFINEMENTS.items()},
        "refinement_aliases": dict(REFINEMENT_ALIASES),
        "baseline_refinement": BASELINE_REFINEMENT,
        "baseline_quote": BASELINE_QUOTE,
        "map_task_quote": MAP_TASK_QUOTE,
        "action_families": dict(ACTION_FAMILIES),
        "field_aliases": {key: list(paths) for key, paths in FIELD_ALIASES.items()},
        "lifecycle_stages": list(LIFECYCLE_STAGES),
        "lifecycle_constraint": LIFECYCLE_CONSTRAINT,
        "crm_plan": dict(CRM_PLAN),
        "batch_key_field": BATCH_KEY_FIELD,
        "execution_note": EXECUTION_NOTE,
        "actionability_note": ACTIONABILITY_NOTE,
        "auto_add_note": AUTO_ADD_NOTE,
    }
