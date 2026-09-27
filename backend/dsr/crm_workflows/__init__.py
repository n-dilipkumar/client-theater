"""WF-030: fire CRM workflows off DSR activity.

A CRM workflow here is the researched one: **contact based**, triggered by
**"When filter criteria is met"** on one of **five filter families**, with
**actions** attached, and **published** so DSR activity drives it with no further
setup. "Dock only supports Contact based workflows since the activities are tied to
the contact record", and that sentence is why almost every refusal in this package
is what it is.

Kept in its own package so no two features claim one path. The module layout, and
why each piece is separate:

``vocabulary``      the values the research fixes by name, served as data
``errors``          one hierarchy, so the feature module registers one handler
``activity``        reading a DSR event as one of the five families
``criteria``        the refinement matrix, its lint, and whether an event matches
``definition``      validating a workflow definition, and its lifecycle
``actions``         resolving the four action kinds into what would be written
``inferences``      every judgement call, named and served
``engine``          the façade the HTTP layer calls; owns the three collections

The two facts a reviewer most wants to check without reading the diff are served:
:func:`dsr.crm_workflows.vocabulary.describe` at the feature's ``/vocabulary``
route, which is the researched surface the validator enforces, and
:func:`dsr.crm_workflows.inferences.describe` at ``/inferences``, which says which
parts are sourced and which are this build's judgement.
"""

from __future__ import annotations

from dsr.crm_workflows import criteria, inferences
from dsr.crm_workflows.actions import resolve_action, resolve_actions
from dsr.crm_workflows.activity import normalise_activity
from dsr.crm_workflows.criteria import (
    evaluate,
    evaluate_contact,
    lint_criteria,
    matches,
    parse_criteria,
)
from dsr.crm_workflows.definition import apply_patch, lint_workflow, normalise_workflow
from dsr.crm_workflows.engine import (
    ACTIVITY,
    ENROLLMENTS,
    INTEGRATIONS,
    WORKFLOWS,
    WorkflowEngine,
)
from dsr.crm_workflows.errors import (
    AlreadyPublished,
    AlreadyWithdrawn,
    IntegrationDisabled,
    IntegrationInUse,
    MalformedActivity,
    NoActions,
    NotContactBased,
    PublishedWorkflowIsImmutable,
    UnknownFilterFamily,
    UnknownIntegration,
    UnknownTriggerMode,
    UnsupportedRefinement,
    WorkflowError,
)
from dsr.crm_workflows.vocabulary import (
    ACTION_KINDS,
    ACTIONABILITY_NOTE,
    DELIVERY_PATHS,
    ENROLLMENT_TYPES,
    FILTER_FAMILIES,
    LIFECYCLE_STAGES,
    REFINEMENTS,
    TRIGGER_MODES,
    describe as describe_vocabulary,
    require_family,
    require_refinement,
)

__all__ = [
    "ACTIONABILITY_NOTE",
    "ACTION_KINDS",
    "ACTIVITY",
    "AlreadyPublished",
    "AlreadyWithdrawn",
    "DELIVERY_PATHS",
    "ENROLLMENTS",
    "ENROLLMENT_TYPES",
    "FILTER_FAMILIES",
    "INTEGRATIONS",
    "IntegrationDisabled",
    "IntegrationInUse",
    "LIFECYCLE_STAGES",
    "MalformedActivity",
    "NoActions",
    "NotContactBased",
    "PublishedWorkflowIsImmutable",
    "REFINEMENTS",
    "TRIGGER_MODES",
    "UnknownFilterFamily",
    "UnknownIntegration",
    "UnknownTriggerMode",
    "UnsupportedRefinement",
    "WORKFLOWS",
    "WorkflowEngine",
    "WorkflowError",
    "apply_patch",
    "criteria",
    "describe_vocabulary",
    "evaluate",
    "evaluate_contact",
    "inferences",
    "lint_criteria",
    "lint_workflow",
    "matches",
    "normalise_activity",
    "normalise_workflow",
    "parse_criteria",
    "require_family",
    "require_refinement",
    "resolve_action",
    "resolve_actions",
]
