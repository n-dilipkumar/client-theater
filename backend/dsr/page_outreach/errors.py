"""One error hierarchy for the page-outreach workflow.

Every refusal this package makes is the caller's to fix, so the types share a base
and the feature module registers a single handler for the base. Anything that is
not a :class:`PageOutreachError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because "this page view matches no rule" and "this workflow already has
that name" are both this package's errors and only one of them conflicts with state
that already exists. A handler that answered 400 for both would be lying about the
second. FastAPI only accepts exception handlers on the app object, so the feature
module exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map one
type, which is why the whole hierarchy hangs off one base.

``RecordNotFound`` is deliberately not claimed here. The core app already maps it to
404, and a second handler for one type is a collision the host refuses.
"""

from __future__ import annotations


class PageOutreachError(ValueError):
    """A page-outreach request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller sent.
    Nothing in this package raises for a fault of its own.
    """

    code = "page_outreach_error"
    status = 400


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


class InvalidWorkflow(PageOutreachError):
    """A workflow cannot be saved.

    Raised for a missing or blank name, a frequency mode outside the three published
    ones, a channel this build cannot honour, a state outside ``draft`` and ``live``,
    a repeat window that is not a whole number of days between 1 and 90, a targeting
    rule with a kind or match mode nobody reads, a duplicate rule value, a block kind
    outside the published set, and a block that carries no text.
    """

    code = "invalid_workflow"
    status = 422


class DuplicateWorkflow(PageOutreachError):
    """A workflow with this name already exists in this room.

    Two live workflows with the same name on the same page-URL rules would show the
    same buyer the same block twice, and the receipts would be indistinguishable.
    """

    code = "workflow_already_exists"
    status = 409


class UnknownWorkflow(PageOutreachError):
    """No workflow exists under the id the caller named."""

    code = "unknown_workflow"
    status = 404


# --------------------------------------------------------------------------- #
# Page views
# --------------------------------------------------------------------------- #


class InvalidPageView(PageOutreachError):
    """A page view cannot be read.

    Raised for a missing workflow, a missing visitor key, a missing path, a dwell time
    that is not a whole number of seconds at or above zero, and a visited_at moment
    with no timezone offset. The workflow and the visitor key are required together
    because a trigger decision is meaningless without both: the rules say which page
    and the damper says whose session it is.
    """

    code = "invalid_page_view"
    status = 422


class UnknownPageViewField(PageOutreachError):
    """The page view carries a field this workflow does not name.

    The five signals the evidence names are a closed list, and so is the shape of a
    page view. An open list is not a more flexible page view, it is one that stores
    whatever a caller is configured to send, and the measured value of a trigger
    decision is the whole of this workflow's output. A field no rule reads is a field
    a reader will later mistake for one that was.
    """

    code = "unknown_page_view_field"
    status = 422


# --------------------------------------------------------------------------- #
# Interactions and receipts
# --------------------------------------------------------------------------- #


class InvalidInteraction(PageOutreachError):
    """A buyer interaction cannot be recorded.

    Raised for a kind outside the five published ones, and an interaction against a
    delivery that has not been shown. A receipt for a block nobody saw is a number
    this workflow would then use to decide the buyer engaged, so it is refused rather
    than stored.
    """

    code = "invalid_interaction"
    status = 422


class UnknownDelivery(PageOutreachError):
    """No delivery exists under the id the caller named."""

    code = "unknown_delivery"
    status = 404


class UnknownPath(PageOutreachError):
    """The workflow has no path under the key the caller named.

    The flow's step five branches on the buyer's answer, so a path selection names a
    branch that has to exist. A selection of a branch nobody declared would leave the
    receipt pointing at nothing.
    """

    code = "unknown_path"
    status = 422


__all__ = [
    "DuplicateWorkflow",
    "InvalidInteraction",
    "InvalidPageView",
    "InvalidWorkflow",
    "PageOutreachError",
    "UnknownDelivery",
    "UnknownPageViewField",
    "UnknownPath",
    "UnknownWorkflow",
]
