"""WF-029: score DSR activity as CRM lead-score criteria.

The domain logic for this workflow. A **criterion** here is one researched row:
a Dock activity property to score against, the filters that narrow it, and a
score value assigned to a **positive** or **negative** bucket. Every matching DSR
activity event re-evaluates a contact's score, and the total lands on the
``HubSpot Score`` contact property.

The module layout, and why each piece is separate:

``names``        the six collections this workflow owns, in one place
``errors``       one hierarchy, so the feature module registers one handler
``vocabulary``   the values the research fixes by name, served as data
``activity``     reading a DSR event as one of the five Dock properties
``criteria``     validating one criterion, linting it, and matching an event
``scoring``      the score arithmetic: contributions, total, floor, CRM plan
``inferences``   every judgement call, named and served
``engine``       the facade the HTTP layer calls; owns the six collections

Nothing here imports the framework. The store is reached through
:class:`dsr.store.RecordStore`, every write is audited in the same transaction as
the change, and no socket is opened: the CRM endpoints the research names are
recorded as a plan on each run rather than called, because this build holds no CRM
credential. That is the ``write_scope`` decision, recorded in
:mod:`dsr.lead_score.inferences` and taken by Jev at audit
``jev-20261004T024259-6152-79173``.

Two facts a reviewer most wants to check without reading the diff are served:
:func:`dsr.lead_score.vocabulary.describe` at the feature's ``/vocabulary`` route,
which is the researched surface the validator enforces, and
:func:`dsr.lead_score.inferences.describe` at ``/inferences``, which says which
parts are sourced and which are this build's judgement.
"""

from __future__ import annotations

from dsr.lead_score import criteria, inferences, names
from dsr.lead_score.activity import (
    MalformedActivity,
    normalise_activity,
    parse_timestamp,
)
from dsr.lead_score.criteria import (
    REASONS,
    describe_matcher,
    lint_criterion,
    matches,
    parse_criterion,
    parse_score_value,
    tally_reasons,
)
from dsr.lead_score.engine import LeadScoreEngine
from dsr.lead_score.errors import (
    CrmOrgDisabled,
    CrmOrgNotConnected,
    InvalidScoreValue,
    LeadScoreError,
    MissingCrmScope,
    UnknownCriterion,
    UnknownScoreBucket,
    UnknownScoreFamily,
    UnknownScoreProperty,
    UnsupportedRefinement,
)
from dsr.lead_score.names import (
    ACTIVITY,
    CONTACTS,
    CRITERIA,
    INTEGRATIONS,
    PROPERTIES,
    RUNS,
)
from dsr.lead_score.scoring import (
    batch_plan_for,
    contributions_for,
    crm_plan_for,
    lifecycle_write_verdict,
    recompute,
)
from dsr.lead_score.vocabulary import (
    ACTION_FAMILIES,
    ALL_REFINEMENTS,
    BASELINE_REFINEMENT,
    BUCKET_SIGN,
    BUCKETS,
    CRM_PLAN,
    DEFAULT_INTEGRATION,
    FAMILY_GROUP,
    FAMILY_LABEL,
    FILTER_FAMILIES,
    LIFECYCLE_STAGES,
    REFINEMENTS,
    REQUIRED_SCOPES,
    SCORE_PROPERTY,
    describe as describe_vocabulary,
    family_for_action,
    require_bucket,
    require_family,
    require_refinement,
)

__all__ = [
    "ACTION_FAMILIES",
    "ACTIVITY",
    "ALL_REFINEMENTS",
    "BASELINE_REFINEMENT",
    "BUCKETS",
    "BUCKET_SIGN",
    "CONTACTS",
    "CRM_PLAN",
    "CRITERIA",
    "CrmOrgDisabled",
    "CrmOrgNotConnected",
    "DEFAULT_INTEGRATION",
    "FAMILY_GROUP",
    "FAMILY_LABEL",
    "FILTER_FAMILIES",
    "INTEGRATIONS",
    "InvalidScoreValue",
    "LIFECYCLE_STAGES",
    "LeadScoreEngine",
    "LeadScoreError",
    "MalformedActivity",
    "MissingCrmScope",
    "PROPERTIES",
    "REASONS",
    "REFINEMENTS",
    "REQUIRED_SCOPES",
    "RUNS",
    "SCORE_PROPERTY",
    "UnknownCriterion",
    "UnknownScoreBucket",
    "UnknownScoreFamily",
    "UnknownScoreProperty",
    "UnsupportedRefinement",
    "batch_plan_for",
    "contributions_for",
    "criteria",
    "crm_plan_for",
    "describe_matcher",
    "describe_vocabulary",
    "family_for_action",
    "inferences",
    "lifecycle_write_verdict",
    "lint_criterion",
    "matches",
    "names",
    "normalise_activity",
    "parse_criterion",
    "parse_score_value",
    "parse_timestamp",
    "recompute",
    "require_bucket",
    "require_family",
    "require_refinement",
    "tally_reasons",
]
