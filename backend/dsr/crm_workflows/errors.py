"""One error hierarchy for the CRM-workflow package.

Every refusal this package makes is something the caller sent, so the types share
a base and the feature module registers a single handler for it. Anything that is
*not* a :class:`WorkflowError` is a bug in this package and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler. A missing ``name`` and a definition that is already published are both
this package's errors, but one is a malformed request and the other conflicts with
state that already exists, and a handler that answered 400 for both would be lying
about the second. FastAPI only accepts exception handlers on the app object, so
the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two features may
not map the same type, which is why the whole hierarchy hangs off one base.

The split that matters most is **refusal** against **report**. Most of what could
go wrong with a workflow is a fact about the workflow rather than a mistake by the
caller, and those are returned in a ``findings`` list instead of raised:

* an action kind outside the four the research names is *reported unresolved*,
  because the source says "and more!";
* a contact already enrolled in a published workflow is *reported already
  enrolled*, because a repeated match is a fact about the contact's history;
* a ``lifecyclestage`` action naming a stage behind the contact's current one is
  *reported refused for that action*, because the rest of the workflow's actions
  are still valid.

Raising there would turn a normal run of a valid workflow into an HTTP error, and a
caller who cannot tell "your request was malformed" from "this action did not
apply this time" will eventually retry the malformed one forever.
"""

from __future__ import annotations


class WorkflowError(ValueError):
    """A workflow cannot be created, changed, or published as written."""

    code = "crm_workflow_error"
    status = 400


# --------------------------------------------------------------------------- #
# The definition
# --------------------------------------------------------------------------- #


class UnknownFilterFamily(WorkflowError):
    """The trigger's criteria named a family the integration does not publish.

    "When you select Dock, you'll see five different options for your filter." The
    five are enumerated, and this is a refusal rather than a warning for the same
    reason an unknown *event* is refused elsewhere in this product: a filter on a
    family the integration cannot evaluate would never fire, and a rule that can
    never fire is worse than a rule that will not save.

    The action vocabulary is deliberately *not* treated this way. See
    :mod:`dsr.crm_workflows.vocabulary` for why the two lists differ.
    """

    code = "unknown_filter_family"


class UnsupportedRefinement(WorkflowError):
    """A refinement the family does not publish.

    The source is explicit per family - "**Views:** filter by date",
    "**Downloads:** filter by date and/or file name", "Clicks/Interactions by date
    or link URL" - so refining Views by file name is a category error rather than a
    preference, and it would otherwise sit in a stored definition looking armed.
    """

    code = "unsupported_refinement"


class NotContactBased(WorkflowError):
    """A workflow was asked to be anything other than contact based.

    The constraint is quoted, not paraphrased: *Dock only supports Contact based
    workflows since the activities are tied to the contact record.*
    """

    code = "workflow_must_be_contact_based"


class UnknownTriggerMode(WorkflowError):
    """The trigger is not "When filter criteria is met"."""

    code = "unknown_trigger_mode"


class NoActions(WorkflowError):
    """A workflow with no actions.

    "Once you setup your filters, you can use HubSpot's Workflows to send emails,
    slack notifications, update fields, change stages" - the filter is the trigger
    and the actions are what the trigger is for. A definition with a filter and
    nothing to do would enroll contacts and do nothing, which is the one outcome
    worse than not having the workflow at all.
    """

    code = "workflow_has_no_actions"


class MalformedActivity(WorkflowError):
    """A DSR activity event cannot be read as activity.

    Refused rather than stored-and-ignored: an event with no contact cannot be
    evaluated against a contact-based workflow at all, and storing it would make
    the room's activity count disagree with the number of events that can enrol
    anybody.
    """

    code = "malformed_activity"


# --------------------------------------------------------------------------- #
# Prerequisites and state conflicts
# --------------------------------------------------------------------------- #


class UnknownIntegration(WorkflowError):
    """The trigger names an integration that is not registered.

    Step 1 of the researched flow is "Verify the HubSpot integration is on", so a
    workflow whose trigger names an integration nobody registered is missing a
    prerequisite rather than being malformed. 409 rather than 400, and the message
    names what to register.
    """

    code = "integration_not_registered"
    status = 409


class IntegrationDisabled(WorkflowError):
    """The integration is registered but switched off.

    The same step 1: "Verify the HubSpot integration is **on**". Publishing a
    workflow against a disabled integration would create a rule that silently
    never fires, which is the failure mode a seller cannot diagnose from a
    workflow list.
    """

    code = "integration_disabled"
    status = 409


class PublishedWorkflowIsImmutable(WorkflowError):
    """An amendment to a workflow that is already published.

    "Publish the workflow; DSR activity then drives it with no further setup" -
    once published, the definition is the thing that has already been firing, and
    a contact may already have been enrolled by the version being edited. Changing
    it in place would rewrite the history of every enrollment taken under it. The
    research does not claim HubSpot refuses this; it simply does not describe
    editing a published workflow, and the safe reading is that you unpublish,
    change, and republish. Recorded as the ``amend-published-workflow`` inference.
    """

    code = "published_workflow_is_immutable"
    status = 409


class AlreadyPublished(WorkflowError):
    """Publishing a workflow that is already published."""

    code = "workflow_already_published"
    status = 409


class AlreadyWithdrawn(WorkflowError):
    """A change to a withdrawn workflow.

    A withdrawn workflow has a history - enrollments taken under it still name it
    - so it is soft-deleted rather than destroyed, and it is not re-published or
    amended in place. Register a new one instead.
    """

    code = "workflow_withdrawn"
    status = 409


class IntegrationInUse(WorkflowError):
    """An amendment to an integration that a published workflow depends on.

    Turning the integration off, or unlinking the room a published workflow's
    criteria run against, would silently stop a live rule from firing. That is a
    conflict with state that exists, so it is refused with 409 and the names of the
    workflows that would stop, rather than accepted and reported afterwards.
    """

    code = "integration_in_use"
    status = 409
