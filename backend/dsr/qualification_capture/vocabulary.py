"""WF-123: every researched term an in-room qualifying form enforces.

This module is the researched specification for WF-123 made executable, and the
only place a constant named after the specification lives. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-123.md``, quoted in full in
issue 160.

What the research fixes, and what it does not
---------------------------------------------

The research names four things and leaves everything else open:

* The four things a seller asks about: budget, timeline, authority and the
  procurement path. Those four are quoted from the user flow, so they are
  sourced, and this module names no fifth.
* One visibility rule: "qualification summary visible to seller only". That is
  sourced, and :data:`SELLER_ONLY_FIELDS` is what makes it a rule rather than a
  sentence.
* Four automations, without saying how many this plugin owns. This build owns
  two in-room ones - attaching a submission to the deal, and auto-routing on the
  band - and delegates the other two. See
  :mod:`dsr.qualification_capture.inferences`.
* The witness to a submission: the buyer. The research points at WF-015's
  identity requirement ("WF-015 carries the identity requirement it would
  serve"), so this package reuses that workflow's visitor vocabulary rather than
  inventing a second one.

What is not sourced is the answer options, the weights and the band thresholds.
The research names the four dimensions and no scale, so every scale here is an
inference, recorded with its basis in
:mod:`dsr.qualification_capture.inferences`. A reviewer can read the four options
below beside the source sentence for the dimension and see the derivation.

Reuse, not re-declaration
-------------------------

Two vocabularies this workflow depends on already exist and are imported, not
copied:

* The **room** vocabulary lives in :mod:`dsr.room_lifecycle`: the two lifecycle
  states and :func:`~dsr.room_lifecycle.status_of`. A form may only be published
  on an active room, and this module does not restate what "active" means.
* The **visitor** vocabulary lives in :mod:`dsr.access`, which is WF-015's
  identity gate: the identity modes and the two session states a submission may
  carry. A submission that names a visitor session must resolve to that store
  and to one of those states; this module does not mint a second session type.

Nothing here imports the framework. See :mod:`dsr.qualification_capture.engine`
for the only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

# The visitor vocabulary, from WF-015's identity gate. A submission references an
# existing `access_session` and one of its two accepted states; nothing here
# declares a second session type.
from dsr.access import (
    MODE_IDENTIFY,
    MODE_OPEN,
    MODE_VERIFY_EMAIL,
    MODES as IDENTITY_MODES,
    SESSION_COLLECTION as VISITOR_SESSION_COLLECTION,
    STATUS_IDENTIFIED,
    STATUS_VERIFIED,
)

# The room vocabulary. Re-exported as this package's own so a caller of the
# package does not have to know which file the vocabulary lives in, and so a
# second workflow cannot quietly redefine "archived".
from dsr.room_lifecycle import (
    ACTIVE,
    ARCHIVED,
    STATUSES as ROOM_STATUSES,
    status_of,
)
from dsr.room_lifecycle.engine import ROOMS as ROOM_COLLECTION

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-123"
FEATURE_ID = "wf-123-qualification-capture"
NAME = "In-room qualification capture"
PREFIX = "/api/wf-123"

#: The contract at the top of the ticket. There is one route prefix and the
#: feature module derives every audit source from it.
ROUTE_PREFIX = PREFIX

# --------------------------------------------------------------------------- #
# The four researched dimensions
# --------------------------------------------------------------------------- #
#
# Quoted from the user flow: "Seller embeds a short form inside the room (budget,
# timeline, authority, procurement path)". Four dimensions, in that order.

DIMENSION_BUDGET = "budget"
DIMENSION_TIMELINE = "timeline"
DIMENSION_AUTHORITY = "authority"
DIMENSION_PROCUREMENT_PATH = "procurement_path"

DIMENSIONS: tuple[str, ...] = (
    DIMENSION_BUDGET,
    DIMENSION_TIMELINE,
    DIMENSION_AUTHORITY,
    DIMENSION_PROCUREMENT_PATH,
)

DIMENSION_LABELS: dict[str, str] = {
    DIMENSION_BUDGET: "Budget",
    DIMENSION_TIMELINE: "Timeline",
    DIMENSION_AUTHORITY: "Authority",
    DIMENSION_PROCUREMENT_PATH: "Procurement path",
}

#: The prompt the buyer reads for each dimension. Written as a question so the
#: form is a conversation rather than an interrogation.
DIMENSION_QUESTIONS: dict[str, str] = {
    DIMENSION_BUDGET: "Is there a budget for this, and has it been approved?",
    DIMENSION_TIMELINE: "When do you need this in place?",
    DIMENSION_AUTHORITY: "Who makes the final decision?",
    DIMENSION_PROCUREMENT_PATH: "How does your team buy software like this?",
}

# --------------------------------------------------------------------------- #
# The answer scale
# --------------------------------------------------------------------------- #
#
# Not sourced. The research names the four dimensions and no scale, so every
# option and every weight below is an inference. The shape is a small ordered
# ladder per dimension, from a strong buying signal to none, so the sum is a
# BANT-style score. `inferences.py` records the choice and the alternatives it
# would have cost.

#: The weight every answer carries, from strongest to none.
WEIGHT_STRONG = 3
WEIGHT_GOOD = 2
WEIGHT_WEAK = 1
WEIGHT_NONE = 0

#: One closed option set per dimension. Order runs strongest first.
ANSWER_OPTIONS: dict[str, tuple[str, ...]] = {
    DIMENSION_BUDGET: ("funded", "approved", "needs_approval", "no_budget"),
    DIMENSION_TIMELINE: ("now", "this_quarter", "this_year", "later"),
    DIMENSION_AUTHORITY: ("decision_maker", "champion", "influencer", "unknown"),
    DIMENSION_PROCUREMENT_PATH: ("known_process", "partly_known", "unknown"),
}

ANSWER_WEIGHTS: dict[str, dict[str, int]] = {
    DIMENSION_BUDGET: {
        "funded": WEIGHT_STRONG,
        "approved": WEIGHT_GOOD,
        "needs_approval": WEIGHT_WEAK,
        "no_budget": WEIGHT_NONE,
    },
    DIMENSION_TIMELINE: {
        "now": WEIGHT_STRONG,
        "this_quarter": WEIGHT_GOOD,
        "this_year": WEIGHT_WEAK,
        "later": WEIGHT_NONE,
    },
    DIMENSION_AUTHORITY: {
        "decision_maker": WEIGHT_STRONG,
        "champion": WEIGHT_GOOD,
        "influencer": WEIGHT_WEAK,
        "unknown": WEIGHT_NONE,
    },
    DIMENSION_PROCUREMENT_PATH: {
        "known_process": WEIGHT_STRONG,
        "partly_known": WEIGHT_WEAK,
        "unknown": WEIGHT_NONE,
    },
}

ANSWER_LABELS: dict[str, dict[str, str]] = {
    DIMENSION_BUDGET: {
        "funded": "Funded and approved",
        "approved": "Budget exists, not yet approved",
        "needs_approval": "We will need to ask for it",
        "no_budget": "No budget yet",
    },
    DIMENSION_TIMELINE: {
        "now": "As soon as possible",
        "this_quarter": "This quarter",
        "this_year": "This year",
        "later": "No date yet",
    },
    DIMENSION_AUTHORITY: {
        "decision_maker": "I decide",
        "champion": "My recommendation, someone else signs",
        "influencer": "I can influence the choice",
        "unknown": "Not sure yet",
    },
    DIMENSION_PROCUREMENT_PATH: {
        "known_process": "A defined process I can describe",
        "partly_known": "Roughly, but the detail is elsewhere",
        "unknown": "No idea yet",
    },
}

#: The highest score one dimension can contribute. Derived rather than typed, so
#: a change to a weight cannot leave the ceiling behind.
DIMENSION_CEILING = max(max(weights.values()) for weights in ANSWER_WEIGHTS.values())

# --------------------------------------------------------------------------- #
# The qualification band
# --------------------------------------------------------------------------- #

BAND_QUALIFIED = "qualified"
BAND_DEVELOPING = "developing"
BAND_UNQUALIFIED = "unqualified"

BANDS: tuple[str, ...] = (BAND_QUALIFIED, BAND_DEVELOPING, BAND_UNQUALIFIED)

BAND_LABELS: dict[str, str] = {
    BAND_QUALIFIED: "Qualified",
    BAND_DEVELOPING: "Developing",
    BAND_UNQUALIFIED: "Unqualified",
}

#: Where each band ends and the next begins, as a share of the reachable maximum
#: rather than a fixed integer. A form may enable only some of the four
#: dimensions, so a fixed threshold would be unreachable on a two-dimension form
#: and would mark a perfect answer unqualified. Ratio thresholds travel with the
#: form. Not sourced; the research names a "qualification band" and no cut.
BAND_QUALIFIED_RATIO = 0.75
BAND_DEVELOPING_RATIO = 0.40

# --------------------------------------------------------------------------- #
# Routing on the band
# --------------------------------------------------------------------------- #
#
# The research automations say "Auto-route on qualification band", so a band
# routes somewhere. The three targets are not sourced; the choice is recorded in
# `inferences.py`.

TARGET_ACCOUNT_EXECUTIVE = "account_executive"
TARGET_SALES_DEVELOPMENT = "sales_development"
TARGET_NURTURE = "nurture"

ROUTE_TARGET_BY_BAND: dict[str, str] = {
    BAND_QUALIFIED: TARGET_ACCOUNT_EXECUTIVE,
    BAND_DEVELOPING: TARGET_SALES_DEVELOPMENT,
    BAND_UNQUALIFIED: TARGET_NURTURE,
}

#: The two automations this plugin owns in-room, and the two it hands on. The
#: research lists four and does not say how many belong here, so the split is
#: recorded and served.
OWNED_AUTOMATIONS: tuple[str, ...] = (
    "attach_submission_to_deal",
    "auto_route_on_qualification_band",
)

DELEGATED_AUTOMATIONS: tuple[dict[str, str], ...] = (
    {
        "automation": "auto_create_or_update_a_lead_or_opportunity",
        "owner": "crm_upsert",
        "note": "Writing a CRM opportunity is another workflow's job; this plugin records the deal reference the CRM writer reads.",
    },
    {
        "automation": "auto_trigger_a_follow_up_task",
        "owner": "wf-116-seller-followup-task",
        "note": "A follow-up task is WF-116's. This plugin names the assignee on the routing decision; it does not open the task.",
    },
)

# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #
#
# Quoted: "qualification summary visible to seller only". The rule is a list of
# fields a buyer response must never carry, so it is enforced in one place.

VISIBILITY_SELLER_ONLY = "seller_only"
VISIBILITY_BUYER = "buyer"

#: Every field a buyer view strips. A name here is the difference between a
#: summary the seller reads and a score the buyer must not see.
SELLER_ONLY_FIELDS: tuple[str, ...] = (
    "score",
    "max_score",
    "band",
    "band_label",
    "routing",
    "route_target",
    "routed",
)

BUYER_VISIBLE_NOTE = (
    "The buyer sees the form and their own answers. The score, the band and the "
    "routing decision are visible to the seller only."
)

# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #

FORM_STATUS_DRAFT = "draft"
FORM_STATUS_LIVE = "live"

FORM_STATUSES: tuple[str, ...] = (FORM_STATUS_DRAFT, FORM_STATUS_LIVE)

FORM_STATUS_LABELS: dict[str, str] = {
    FORM_STATUS_DRAFT: "Draft",
    FORM_STATUS_LIVE: "Live",
}

SUBMISSION_STATUS_DRAFT = "draft"
SUBMISSION_STATUS_SUBMITTED = "submitted"

SUBMISSION_STATUSES: tuple[str, ...] = (
    SUBMISSION_STATUS_DRAFT,
    SUBMISSION_STATUS_SUBMITTED,
)

SUBMISSION_STATUS_LABELS: dict[str, str] = {
    SUBMISSION_STATUS_DRAFT: "In progress",
    SUBMISSION_STATUS_SUBMITTED: "Submitted",
}

#: The lifecycle events a form or a submission fires. The research names
#: "auto-save of answers", which is the reason a capture event exists at all.
EVENT_AUTOSAVED = "answers_autosaved"
EVENT_SUBMITTED = "answers_submitted"
EVENT_QUALIFIED = "qualification_scored"
EVENT_ROUTED = "routed_on_band"

EVENT_TYPES: tuple[str, ...] = (
    EVENT_AUTOSAVED,
    EVENT_SUBMITTED,
    EVENT_QUALIFIED,
    EVENT_ROUTED,
)

# --------------------------------------------------------------------------- #
# Collections and payload keys
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one `records` table
# and `find()` matches on collection before it matches on anything else.

FORM_COLLECTION = "wf123_qualification_form"
SUBMISSION_COLLECTION = "wf123_qualification_submission"
ROUTE_COLLECTION = "wf123_qualification_route"
EVENT_COLLECTION = "wf123_qualification_event"

COLLECTIONS: dict[str, str] = {
    "form": FORM_COLLECTION,
    "submission": SUBMISSION_COLLECTION,
    "route": ROUTE_COLLECTION,
    "event": EVENT_COLLECTION,
}

#: The payload-side twin of the envelope's ``room_id``.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of
#: the record *envelope*, so the store strips it out of ``data`` before the
#: dynamic index is built. A record that stored its room there would be
#: unfilterable by ``find()``. The envelope still carries ``room_id``; every
#: response projects this key back to it.
ROOM_REF = "room_ref"

#: The deal a submission is attached to. The research's data flow ends at
#: "deal/contact record in CRM", so a submission carries the deal it belongs to.
DEAL_REF = "deal_ref"
ACCOUNT_REF = "account_ref"
CONTACT_REF = "contact_ref"

#: The visitor session a submission was captured against, from WF-015.
VISITOR_SESSION_REF = "visitor_session_ref"

#: The identity states a submission may be captured against. Both are WF-015's.
CAPTURABLE_VISITOR_STATES: tuple[str, ...] = (STATUS_IDENTIFIED, STATUS_VERIFIED)

# --------------------------------------------------------------------------- #
# The researched user flow and the quotes behind it
# --------------------------------------------------------------------------- #

USER_FLOW: tuple[str, ...] = (
    "The seller embeds a short form inside the room.",
    "The buyer completes it without leaving the room.",
    "The answers are attached to the deal record.",
    "The seller reads the qualification status next to the content analytics.",
    "The band routes the deal to the right owner.",
)

SOURCED_QUOTES: dict[str, str] = {
    "dimensions": (
        "[sourced] Seller embeds a short form inside the room (budget, timeline, "
        "authority, procurement path)"
    ),
    "capture": ("[sourced] Buyer completes it without leaving the room"),
    "attach": ("[sourced] The responses are attached to the deal record"),
    "visibility": (
        "[sourced] Customisable form fields inside the room; buyer-facing "
        "simplicity; auto-save of answers; qualification summary visible to "
        "seller only."
    ),
    "route": ("[sourced] Auto-route on qualification band"),
    "dealhub_insights": (
        "[sourced] Buyer Qualification Insights - Collect key buyer details with "
        "customizable DealRoom forms to ensure smooth deal transitions"
    ),
    "data_flow": (
        "[sourced] Form schema -> room-rendered widget -> submission -> "
        "deal/contact record in CRM -> qualification score/flag -> routing rule."
    ),
}

#: What this build could not source. A reader must treat each line as a
#: hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "The research names the four dimensions and no answer scale, so the options "
    "and weights in this module are inferred, not sourced.",
    "The research names a qualification band and no cut point, so the band "
    "ratios here are inferred.",
    "The research names three routing targets in no source, so the band-to-owner map is inferred.",
    "The evidence marks the in-room form widget as documented by one vendor "
    "only: 'The specific in-room form widget is documented only by DealHub'. "
    "This build implements the DealHub-shaped widget and records the limit.",
    "The research names four automations and not how many this plugin owns; this "
    "build owns the two that are in-room and delegates the other two by name.",
)

SOURCES: tuple[str, ...] = (
    "https://dealhub.io/platform/dealroom/",
    "https://dealfront.com/",
    "https://www.ebsta.com/",
    "https://github.com/formbricks/formbricks",
    "https://www.mindtickle.com/blog/best-digital-sales-room-platforms-comparison-and-review/",
)


def dimension_payload() -> list[dict[str, Any]]:
    """The four dimensions, their options and their weights, as data."""

    return [
        {
            "id": dimension,
            "label": DIMENSION_LABELS[dimension],
            "question": DIMENSION_QUESTIONS[dimension],
            "options": [
                {
                    "value": option,
                    "label": ANSWER_LABELS[dimension][option],
                    "weight": ANSWER_WEIGHTS[dimension][option],
                }
                for option in ANSWER_OPTIONS[dimension]
            ],
        }
        for dimension in DIMENSIONS
    ]


def published_vocabulary() -> dict[str, Any]:
    """Every closed word and every sourced rule, in one payload."""

    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "name": NAME,
        "prefix": PREFIX,
        "dimensions": list(DIMENSIONS),
        "dimension_payload": dimension_payload(),
        "dimension_ceiling": DIMENSION_CEILING,
        "bands": list(BANDS),
        "band_labels": dict(BAND_LABELS),
        "band_ratios": {
            "qualified": BAND_QUALIFIED_RATIO,
            "developing": BAND_DEVELOPING_RATIO,
        },
        "form_statuses": list(FORM_STATUSES),
        "submission_statuses": list(SUBMISSION_STATUSES),
        "event_types": list(EVENT_TYPES),
        "route_target_by_band": dict(ROUTE_TARGET_BY_BAND),
        "owned_automations": list(OWNED_AUTOMATIONS),
        "delegated_automations": [dict(entry) for entry in DELEGATED_AUTOMATIONS],
        "visibility": {
            "seller_only": VISIBILITY_SELLER_ONLY,
            "buyer": VISIBILITY_BUYER,
            "seller_only_fields": list(SELLER_ONLY_FIELDS),
            "note": BUYER_VISIBLE_NOTE,
        },
        "room_vocabulary": {
            "collection": ROOM_COLLECTION,
            "statuses": list(ROOM_STATUSES),
            "note": "Reused from dsr.room_lifecycle; a form may only go live on an active room.",
        },
        "visitor_vocabulary": {
            "collection": VISITOR_SESSION_COLLECTION,
            "modes": list(IDENTITY_MODES),
            "capturable_states": list(CAPTURABLE_VISITOR_STATES),
            "note": "Reused from dsr.access (WF-015); a submission may name a visitor session from that store.",
        },
        "collections": dict(COLLECTIONS),
        "user_flow": list(USER_FLOW),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ACCOUNT_REF",
    "ACTIVE",
    "ANSWER_LABELS",
    "ANSWER_OPTIONS",
    "ANSWER_WEIGHTS",
    "ARCHIVED",
    "BAND_DEVELOPING",
    "BAND_DEVELOPING_RATIO",
    "BAND_LABELS",
    "BAND_QUALIFIED",
    "BAND_QUALIFIED_RATIO",
    "BAND_UNQUALIFIED",
    "BANDS",
    "BUYER_VISIBLE_NOTE",
    "CAPTURABLE_VISITOR_STATES",
    "COLLECTIONS",
    "CONTACT_REF",
    "DEAL_REF",
    "DELEGATED_AUTOMATIONS",
    "DIMENSION_BUDGET",
    "DIMENSION_CEILING",
    "DIMENSION_LABELS",
    "DIMENSION_PROCUREMENT_PATH",
    "DIMENSION_QUESTIONS",
    "DIMENSION_TIMELINE",
    "DIMENSIONS",
    "EVENT_AUTOSAVED",
    "EVENT_COLLECTION",
    "EVENT_QUALIFIED",
    "EVENT_ROUTED",
    "EVENT_SUBMITTED",
    "EVENT_TYPES",
    "FEATURE_ID",
    "FORM_COLLECTION",
    "FORM_STATUSES",
    "FORM_STATUS_DRAFT",
    "FORM_STATUS_LABELS",
    "FORM_STATUS_LIVE",
    "MODE_IDENTIFY",
    "MODE_OPEN",
    "MODE_VERIFY_EMAIL",
    "NAME",
    "OWNED_AUTOMATIONS",
    "PREFIX",
    "ROOM_COLLECTION",
    "ROOM_REF",
    "ROOM_STATUSES",
    "ROUTE_COLLECTION",
    "ROUTE_PREFIX",
    "ROUTE_TARGET_BY_BAND",
    "SELLER_ONLY_FIELDS",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "SUBMISSION_COLLECTION",
    "SUBMISSION_STATUSES",
    "SUBMISSION_STATUS_DRAFT",
    "SUBMISSION_STATUS_LABELS",
    "SUBMISSION_STATUS_SUBMITTED",
    "TARGET_ACCOUNT_EXECUTIVE",
    "TARGET_NURTURE",
    "TARGET_SALES_DEVELOPMENT",
    "TICKET",
    "USER_FLOW",
    "VISIBILITY_BUYER",
    "VISIBILITY_SELLER_ONLY",
    "VISITOR_SESSION_COLLECTION",
    "VISITOR_SESSION_REF",
    "dimension_payload",
    "published_vocabulary",
    "status_of",
]
