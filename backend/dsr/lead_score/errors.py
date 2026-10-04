"""One error hierarchy for the lead-score package.

Every refusal this package makes is something the caller sent, or a state the
caller has to change first, so the types share a base and the feature module
registers a single handler for it. Anything that is *not* a
:class:`LeadScoreError` is a bug in this package and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside
the handler, because a criterion that names no family is a malformed request (400)
and a criterion saved against a disabled CRM organisation is a conflict with state
that already exists (409). One handler answering 400 for both would be lying
about the second. FastAPI only accepts exception handlers on the app object, so
the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two features
may not map the same type, which is why the whole hierarchy hangs off one base.

The split that matters most is **refusal** against **report**. Most of what could
go wrong with a scoring run is a fact about the run rather than a mistake by the
caller, and those are returned in ``findings`` and per-criterion ``decisions``
instead of raised:

* a criterion whose filters name a field the event does not carry is *reported
  unverifiable*, because the research recommends the ``Occurred`` filter as a
  baseline and never says a missing field is an error;
* a contact with no activity in a room is *reported as no events*, not refused;
* a room with no deal connected is *reported per run*, because one unconnected
  room does not stop a criterion scoring contacts in every other room.

Raising there would turn a normal run of a valid criterion into an HTTP error, and
a caller who cannot tell "your request was malformed" from "this criterion did not
apply to this contact" will retry the malformed one forever.
"""

from __future__ import annotations


class LeadScoreError(ValueError):
    """A criterion cannot be saved, or a prerequisite is not met.

    Base of every refusal in :mod:`dsr.lead_score`.
    """

    code = "lead_score_error"
    status = 400


# --------------------------------------------------------------------------- #
# The criterion
# --------------------------------------------------------------------------- #


class UnknownScoreFamily(LeadScoreError):
    """The criterion named a Dock activity property that does not exist.

    Step 4 of the researched flow is "Scroll down until you see the Dock options in
    the lead score system. Choose the Dock property you want to score against", and
    the options are a closed list: the four analytics events plus MAP activity. A
    criterion on a sixth property has nothing to score against, so it would never
    move a score, and a rule that can never fire is worse than one that will not
    save.
    """

    code = "unknown_score_family"


class UnsupportedRefinement(LeadScoreError):
    """A filter the family does not publish.

    The research is explicit per family. Views is recommended for the ``Occurred``
    baseline and nothing else, Clicks and Interactions are refined by "the link
    name", Downloads by the file name, and MAP activity by the task name. Refining
    Views by a file name is a category error rather than a preference, and it would
    otherwise sit in a saved criterion looking armed.
    """

    code = "unsupported_refinement"


class UnknownScoreBucket(LeadScoreError):
    """The criterion named neither the positive nor the negative bucket.

    "Click Add criteria for either positive or negative scores." Two buckets, and
    the bucket is chosen before the score value, which is why the score itself must
    be a magnitude. See :class:`InvalidScoreValue`.
    """

    code = "unknown_score_bucket"


class InvalidScoreValue(LeadScoreError):
    """The score value is not a positive whole number.

    "assign score value" is a magnitude, and the bucket carries the sign. Accepting
    a negative number as well would let one criterion be stored two ways, and a
    reader of the criterion list could not tell which bucket it would score into.
    """

    code = "invalid_score_value"


class UnknownScoreProperty(LeadScoreError):
    """The criterion named a contact property this build does not provision.

    "In HubSpot, lead scoring is automatically created as a contact property as
    'HubSpot Score'." That is the one property this build writes. A criterion may
    name a different one -- the extensibility note is explicit that third parties
    write their own contact properties -- so the name is *stored* and reported
    ``resolved: false``. What is refused is a criterion whose property is not a
    name at all, because there is nothing to report.
    """

    code = "unknown_score_property"


class UnknownCriterion(LeadScoreError):
    """No saved criterion answers to that id.

    404 rather than 400: the request named a row that does not exist, which is not a
    malformed request but a reference to state this portal does not hold.
    """

    code = "unknown_criterion"
    status = 404


# --------------------------------------------------------------------------- #
# Prerequisites
# --------------------------------------------------------------------------- #


class CrmOrgNotConnected(LeadScoreError):
    """No CRM organisation has been registered.

    Step 1 of the researched flow is "Confirm the Dock and HubSpot integration is
    enabled", and without an organisation there is no contact property to write and
    no token to write it with. 409 rather than 400: the request is well formed and
    the portal is not yet in the state the request assumes.
    """

    code = "crm_org_not_connected"
    status = 409


class CrmOrgDisabled(LeadScoreError):
    """The CRM organisation is registered but switched off.

    The same step 1, and the same word in the source: "Verify the HubSpot
    integration is on". Saving a criterion against a disabled organisation would
    create a rule that silently never moves a score, which is the failure mode a
    seller cannot diagnose from a criterion list.
    """

    code = "crm_org_disabled"
    status = 409


class MissingCrmScope(LeadScoreError):
    """The registered token does not carry the scopes the write needs.

    The research names both: ``crm.objects.contacts.read`` and
    ``crm.objects.contacts.write``. A token with only the read scope can look at a
    contact and cannot move its score, so a criterion armed on it would report a
    score that no one ever received. The missing scopes are named in the message.
    """

    code = "missing_crm_scope"
    status = 409
