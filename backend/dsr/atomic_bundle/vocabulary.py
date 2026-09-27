"""The researched contract for WF-039, as data.

The research for this workflow is unusually specific about the *wire* and
silent about everything around it. Four vendor endpoints, two reference
syntaxes, one rollback flag, one ordering flag, and six sets of documented
limits. It says nothing about what a bundle record is called in this product,
how a room names the buyer, or what a rep sees when a bundle fails.

Everything in this module is therefore one of two things, and the difference is
kept visible:

* **sourced** - carried in a ``[sourced]``-marked constant, quoted from
  ``docs/research/digital-sales-room-workflows/wf/WF-039.md``, and traceable to
  the primary documentation the research cites;
* **designed** - this build's own vocabulary, always named in
  :mod:`dsr.atomic_bundle.inferences` with the reasoning.

The reason for the split is not tidiness. The sourced numbers are limits a
deployment will hit in production, and a reader has to be able to tell "Salesforce
documents 25" from "this build decided 25 is also a good warning threshold for
us". :func:`describe` serves both groups separately so a reviewer can disagree
with one without arguing with the other.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Dialects
# --------------------------------------------------------------------------- #

#: [sourced] ``POST /services/data/vXX.X/composite`` with ``allOrNone`` and
#: ``collateSubrequests``, subrequests carrying ``method``, ``url``,
#: ``referenceId``, ``body``.
SALESFORCE_COMPOSITE = "salesforce_composite"

#: [sourced] ``POST /services/data/vXX.X/composite/tree/{sObjectName}``.
SALESFORCE_TREE = "salesforce_sobject_tree"

#: [sourced] ``POST [Organization URI]/api/data/v9.2/$batch`` with
#: ``Content-Type: multipart/mixed`` and a ``changeset_*`` boundary.
DATAVERSE_BATCH = "dataverse_batch"

#: [sourced] ``POST /crm/v3/objects/contacts/batch/create`` with an
#: ``associations`` array, or the association ``PUT`` the research quotes.
HUBSPOT_ASSOCIATIONS = "hubspot_associations"

#: The four dialects, in the order the research lists them.
DIALECTS: tuple[str, ...] = (
    SALESFORCE_COMPOSITE,
    SALESFORCE_TREE,
    DATAVERSE_BATCH,
    HUBSPOT_ASSOCIATIONS,
)

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

#: [sourced] The composite path, with the version left as the documented
#: placeholder because the research quotes it that way and a deployment pins its
#: own version on the connector.
SF_COMPOSITE_PATH = "/services/data/vXX.X/composite"

#: [sourced] The sObject Tree path, templated on the root type.
SF_TREE_PATH = "/services/data/vXX.X/composite/tree/{sobject_name}"

#: [sourced] The Dataverse batch path. The organization URI is a deployment fact
#: and lives on the connector.
DV_BATCH_PATH = "/api/data/v9.2/$batch"

#: [sourced] HubSpot's contacts batch create.
HS_CONTACTS_BATCH_PATH = "/crm/v3/objects/contacts/batch/create"

#: [sourced] "To associate a record with other records or an activity, make a
#: ``PUT`` request to
#: ``/crm/objects/2026-09/{objectTypeId}/{fromRecordId}/associations/{toObjectTypeId}/{toRecordId}``."
HS_ASSOCIATION_PATH = (
    "/crm/objects/2026-09/{from_type}/{from_id}/associations/{to_type}/{to_id}"
)

#: [sourced] ``Content-Type: multipart/mixed`` on the Dataverse batch.
DV_CONTENT_TYPE = "multipart/mixed"

# --------------------------------------------------------------------------- #
# Rollback policy
# --------------------------------------------------------------------------- #

#: [sourced] "``allOrNone`` | Boolean | Specifies what to do when an error occurs
#: while processing a subrequest. If the value is ``true``, the entire composite
#: request is rolled back."
POLICY_STRICT = "strict"

#: [sourced] "… If the value is ``false``, the remaining subrequests that don't
#: depend on the failed subrequest are executed. Dependent subrequests aren't
#: executed."
POLICY_PARTIAL = "partial"

POLICIES: tuple[str, ...] = (POLICY_STRICT, POLICY_PARTIAL)

#: The research's own words for the two policies, so a client renders the
#: documented behaviour and not a paraphrase of it.
POLICY_MEANING: dict[str, str] = {
    POLICY_STRICT: (
        "[sourced] allOrNone: true. The entire composite request is rolled back, "
        "including the subrequests that already succeeded."
    ),
    POLICY_PARTIAL: (
        "[sourced] allOrNone: false. The remaining subrequests that don't depend on "
        "the failed subrequest are executed. Dependent subrequests aren't executed."
    ),
}

#: The ordered-subrequest controls. [sourced] "If you have relationships like this
#: where you need to control the order of execution, set ``collateSubrequests`` to
#: ``false``."
COLLATE_MEANING: dict[str, str] = {
    "true": (
        "[sourced] collateSubrequests: true. Subrequests of the same type may be "
        "grouped together for throughput, so an implicit dependency - a trigger "
        "that reads an earlier record - is not ordered against it."
    ),
    "false": (
        "[sourced] collateSubrequests: false. Subrequests execute in the order "
        "submitted, which is what a declared implicit dependency needs."
    ),
}

# --------------------------------------------------------------------------- #
# Documented limits
# --------------------------------------------------------------------------- #

#: [sourced] "You can have up to 25 subrequests in a single call."
SF_COMPOSITE_MAX_SUBREQUESTS = 25

#: [sourced] "Up to 5 of these subrequests can be sObject Collections or query
#: operations, including Query and QueryAll requests."
SF_COMPOSITE_MAX_COLLECTIONS = 5

#: [sourced] "Up to a total of 200 records across all trees"
SF_TREE_MAX_RECORDS = 200

#: [sourced] "Up to five records of different types"
SF_TREE_MAX_TYPES = 5

#: [sourced] "sObject trees up to five levels deep"
SF_TREE_MAX_DEPTH = 5

#: [sourced] "Batch requests can contain up to 1,000 individual requests".
DV_BATCH_MAX_REQUESTS = 1000

#: Every documented limit, with the sentence it comes from. Served at
#: ``/vocabulary`` so a deployment reads the numbers from the build rather than
#: from a comment.
LIMITS: dict[str, dict[str, Any]] = {
    "salesforce_composite.subrequests": {
        "max": SF_COMPOSITE_MAX_SUBREQUESTS,
        "quote": "You can have up to 25 subrequests in a single call.",
    },
    "salesforce_composite.collections": {
        "max": SF_COMPOSITE_MAX_COLLECTIONS,
        "quote": (
            "Up to 5 of these subrequests can be sObject Collections or query "
            "operations, including Query and QueryAll requests."
        ),
    },
    "salesforce_sobject_tree.records": {
        "max": SF_TREE_MAX_RECORDS,
        "quote": "Up to a total of 200 records across all trees",
    },
    "salesforce_sobject_tree.types": {
        "max": SF_TREE_MAX_TYPES,
        "quote": "Up to five records of different types",
    },
    "salesforce_sobject_tree.depth": {
        "max": SF_TREE_MAX_DEPTH,
        "quote": "sObject trees up to five levels deep",
    },
    "dataverse_batch.requests": {
        "max": DV_BATCH_MAX_REQUESTS,
        "quote": "Batch requests can contain up to 1,000 individual requests",
    },
}

# --------------------------------------------------------------------------- #
# Reference syntax
# --------------------------------------------------------------------------- #

#: [sourced] "reference syntax ``@{referenceId.FieldName}``", and the research
#: quotes two shapes it has to accept: ``@{NewAccount.BillingAddress.city}`` and
#: ``@{AccountInfo.recentItems[0].Id}``. The pattern below is deliberately
#: permissive about the *path* - dots and ``[n]`` index steps are part of it - and
#: strict about the *reference name*, which has to be an identifier.
SF_REFERENCE_PATTERN = re.compile(
    r"@\{(?P<reference>[A-Za-z][A-Za-z0-9_]*)"
    r"\.(?P<path>[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+|\[[0-9]+\])*)\}"
)

#: [sourced] "Within changesets, you can use ``$parameter`` such as ``$1``,
#: ``$2`` … to reference URIs returned for new entities created earlier in the
#: same changeset."
DV_REFERENCE_PATTERN = re.compile(r"\$(?P<index>[1-9][0-9]*)")

#: [sourced] The example body the research quotes for a changeset reference:
#: ``"originatingleadid@odata.bind":"$1"``.
DV_BIND_EXAMPLE = {"originatingleadid@odata.bind": "$1"}

#: The human-readable form of each syntax, for the preview panel.
REFERENCE_SYNTAX: dict[str, str] = {
    SALESFORCE_COMPOSITE: "@{referenceId.FieldName}",
    SALESFORCE_TREE: "@{referenceId.FieldName}",
    DATAVERSE_BATCH: "$1, $2 … (Content-ID within the changeset)",
    HUBSPOT_ASSOCIATIONS: "no reference syntax; a typed-id association names the record",
}

# --------------------------------------------------------------------------- #
# Atomicity, per dialect
# --------------------------------------------------------------------------- #

#: [sourced] The Dataverse guarantee, quoted: "When multiple operations are
#: contained in a change set, all the operations are considered *atomic*. An
#: atomic operation means that if any one of the operations fails, the batch
#: request rolls back any completed operations."
DATAVERSE_ATOMICITY_QUOTE = (
    "When multiple operations are contained in a change set, all the operations "
    "are considered *atomic*. An atomic operation means that if any one of the "
    "operations fails, the batch request rolls back any completed operations."
)

#: [sourced] The sObject Tree guarantee, quoted: "If an error occurs while creating
#: a record, the entire request fails. … The entire request counts as a single
#: call toward your API limits."
SALESFORCE_TREE_ATOMICITY_QUOTE = (
    "If an error occurs while creating a record, the entire request fails. … The "
    "entire request counts as a single call toward your API limits."
)

#: What atomicity each dialect actually gets, and where that comes from.
#:
#: The distinction matters and is the reason the preview states it in words: three
#: of the four dialects are a single atomic request, and the fourth is not one at
#: all in any documented form the research cites.
ATOMICITY: dict[str, dict[str, Any]] = {
    SALESFORCE_COMPOSITE: {
        "unit": "single_request",
        "atomic": True,
        "honours_partial": True,
        "basis": "[sourced] allOrNone on POST /composite.",
        "quote": POLICY_MEANING[POLICY_STRICT],
    },
    SALESFORCE_TREE: {
        "unit": "single_request",
        "atomic": True,
        "honours_partial": False,
        "basis": (
            "[sourced] the sObject Tree endpoint documents one behaviour only, so "
            "a partial policy has nothing to ask for and is refused rather than "
            "silently downgraded."
        ),
        "quote": SALESFORCE_TREE_ATOMICITY_QUOTE,
    },
    DATAVERSE_BATCH: {
        "unit": "changeset",
        "atomic": True,
        "honours_partial": False,
        "basis": (
            "[sourced] a changeset is atomic as a whole. The research documents no "
            "per-operation escape from that, so partial is refused here too - the "
            "non-atomic multi-write direction is Dataverse's "
            "Prefer: odata.continue-on-error, which belongs to WF-040 and is not "
            "built here."
        ),
        "quote": DATAVERSE_ATOMICITY_QUOTE,
    },
    HUBSPOT_ASSOCIATIONS: {
        "unit": "sequence",
        "atomic": False,
        "honours_partial": True,
        "basis": (
            "[sourced] The research cites a contacts batch create and an "
            "association PUT, and documents no cross-object transaction. This "
            "dialect therefore renders a *sequence* of requests, says so in its "
            "preview, and - under a strict policy - compensates by deleting what it "
            "created. A compensation is not a transaction, and the run record says "
            "`atomic: false` so nobody has to guess."
        ),
        "quote": (
            "POST /crm/v3/objects/contacts/batch/create with an associations array, "
            "or PUT /crm/v3/objects/{objectTypeId}/{recordId}/associations/"
            "{toObjectType}/{toObjectId}/{associationTypeId}."
        ),
    },
}

# --------------------------------------------------------------------------- #
# Subrequest outcomes
# --------------------------------------------------------------------------- #

#: [sourced] "Dependent subrequests aren't executed." A step whose declared
#: dependency did not succeed is ``skipped``, and skipped is not failed: nothing
#: went wrong with the step, its input never arrived.
OUTCOME_CREATED = "created"
OUTCOME_ROLLED_BACK = "rolled_back"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

OUTCOMES: tuple[str, ...] = (
    OUTCOME_CREATED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SKIPPED,
    OUTCOME_FAILED,
)

#: [sourced] "If the value is ``false``, the remaining subrequests that don't
#: depend on the failed subrequest are executed."
SKIP_DEPENDENCY_FAILED = "dependency_failed"

#: This build's own name for a failure the ordering flag caused. [sourced] "If you
#: have relationships like this where you need to control the order of execution,
#: set ``collateSubrequests`` to ``false``."
FAIL_COLLATION_VIOLATION = "collation_violation"

# --------------------------------------------------------------------------- #
# The quoted evidence
# --------------------------------------------------------------------------- #

#: The lines of ``WF-039.md`` that this package implements. Each is traceable to
#: the primary documentation the research cites. The two atomicity sentences also
#: exist as named constants above, because they are quoted in more than one place
#: and a second, shorter copy of a guarantee is how a guarantee drifts.
SOURCED_QUOTES: tuple[str, ...] = (
    "allOrNone | Boolean | Specifies what to do when an error occurs while "
    "processing a subrequest. If the value is true, the entire composite request "
    "is rolled back. … If the value is false, the remaining subrequests that don't "
    "depend on the failed subrequest are executed. Dependent subrequests aren't "
    "executed.",
    "Collation can cause issues if there are implicit but not explicit dependencies "
    "between items. For example, consider a request that creates an Account, a "
    "Contact related to the Account, and a custom object that has a trigger "
    "dependent on the account name. … If you have relationships like this where you "
    "need to control the order of execution, set collateSubrequests to false.",
    "You can have up to 25 subrequests in a single call. Up to 5 of these "
    "subrequests can be sObject Collections or query operations, including Query "
    "and QueryAll requests.",
    "The request can contain the following: Up to a total of 200 records across all "
    "trees / Up to five records of different types / sObject trees up to five "
    "levels deep … " + SALESFORCE_TREE_ATOMICITY_QUOTE,
    "You can group requests for operations together so that they're included as a "
    "single transaction by using Change sets. … " + DATAVERSE_ATOMICITY_QUOTE,
    "Batch requests can contain up to 1,000 individual requests",
    "Within changesets, you can use $parameter such as $1, $2 … to reference URIs "
    "returned for new entities created earlier in the same changeset.",
    "To associate a record with other records or an activity, make a PUT request to "
    "/crm/objects/2026-09/{objectTypeId}/{fromRecordId}/associations/"
    "{toObjectTypeId}/{toRecordId}.",
    "The connector builds a single request containing Account → Contact → "
    "Opportunity subrequests in dependency order.",
    "On any failure the whole bundle rolls back (strict mode) and the room shows a "
    "single actionable error.",
)

#: The research's own stated gap, kept next to the facts it qualifies so a reader
#: does not have to go back to the document to find it.
SOURCED_GAPS: tuple[str, ...] = (
    "Salesforce allOrNone interaction between the outer Composite flag and inner "
    "sObject Collections flags has four documented cases; the doc's own note is "
    "that the outer true overrides the inner value. The response-body example for "
    "Case 4 was image-only in the fetched markdown, so the exact rollback body is "
    "not quoted.",
    "The research does not quote an sObject Tree request body, only its endpoint, "
    "its four limits and its atomicity sentence. The nested body this build renders "
    "is therefore a named design inference, not a reproduction.",
    "The research does not quote a Dataverse multipart body, only the content type, "
    "the changeset boundary and the $n reference form.",
    "The research documents no cross-object transaction for HubSpot. The two "
    "endpoints it cites do not compose into one.",
)

#: The research's user flow, verbatim, as the order the preview walks a rep
#: through. Served so the page can label its steps from the specification rather
#: than from a paraphrase.
USER_FLOW: tuple[str, ...] = (
    "Admin clicks Sync → Create opportunity bundle for a buyer whose CRM account "
    "may not exist yet.",
    "The connector builds a single request containing Account → Contact → "
    "Opportunity subrequests in dependency order.",
    "The connector sets the rollback policy (strict / partial) for that request.",
    "The CRM executes subrequests in order, capturing each created record id.",
    "Later subrequests reference earlier ones by id, so the Opportunity is created "
    "against the just-created Account rather than a stale one.",
    "On any failure the whole bundle rolls back (strict mode) and the room shows a "
    "single actionable error.",
)

#: The researched surfaces this build deliberately does not implement, named so
#: that a reader is not left guessing whether an omission is an oversight.
ADJACENT_SURFACES: tuple[dict[str, str], ...] = (
    {
        "surface": "Salesforce sObject Collections as a query source",
        "why_not": (
            "The research names the 5-of-25 collections limit but the workflow's "
            "purpose is writing a record set. A collection is supported as a write "
            "step; it is not a query step."
        ),
    },
    {
        "surface": "Dataverse Prefer: odata.continue-on-error",
        "why_not": (
            "The research's section 7 is a different workflow (WF-040, 'Surface "
            "partial failures and reject invalid writes before commit'). Listing a "
            "per-operation outcome mode here would implement that ticket here."
        ),
    },
    {
        "surface": "HubSpot batch upsert and custom-object batch endpoints",
        "why_not": (
            "The research cites a contacts batch create and an association PUT. It "
            "does not cite a cross-object batch, which is what this workflow needs."
        ),
    },
    {
        "surface": "A scheduled job or an event trigger for the commit",
        "why_not": (
            "[sourced] 'None vendor-side. Room-side this can be triggered by an event "
            "… or a scheduled job, but it is a single explicit request either way.' "
            "The explicit request is the workflow; the scheduler is the host's."
        ),
    },
    {
        "surface": "Field-level error detail and 'retry failed rows only'",
        "why_not": (
            "That is section 7 of the research (WF-040). A run record here carries "
            "one actionable error, as section 6 requires, and keeps every per-step "
            "outcome so that workflow has something to read."
        ),
    },
)


def describe_user_flow() -> list[dict[str, str]]:
    """The researched user flow, numbered, as a page renders it.

    Numbered here rather than at each call site so the vocabulary endpoint and
    the bundle preview cannot disagree about which step is which - the preview's
    whole job is to line the subrequest order up against the flow.
    """
    return [
        {"step": str(index), "text": text} for index, text in enumerate(USER_FLOW, start=1)
    ]


def describe() -> dict[str, Any]:
    """The whole researched contract, as a client can render it.

    Read with no side effect, so it needs no store. The sourced group and the
    designed group are returned under separate keys: a reviewer who disagrees
    with a design decision can leave the sourced numbers alone.
    """
    return {
        "dialects": [
            {
                "id": dialect,
                "atomicity": ATOMICITY[dialect],
                "reference_syntax": REFERENCE_SYNTAX[dialect],
                "limits": sorted(
                    key for key in LIMITS if key.split(".", 1)[0] == dialect
                ),
            }
            for dialect in DIALECTS
        ],
        "policies": [
            {"id": policy, "meaning": POLICY_MEANING[policy]} for policy in POLICIES
        ],
        "collation": COLLATE_MEANING,
        "endpoints": {
            "salesforce_composite": SF_COMPOSITE_PATH,
            "salesforce_sobject_tree": SF_TREE_PATH,
            "dataverse_batch": DV_BATCH_PATH,
            "hubspot_associations": {
                "batch_create": HS_CONTACTS_BATCH_PATH,
                "association": HS_ASSOCIATION_PATH,
            },
        },
        "limits": LIMITS,
        "reference_syntax": {
            "salesforce": {
                "template": "@{referenceId.FieldName}",
                "pattern": SF_REFERENCE_PATTERN.pattern,
                "documented_examples": [
                    "@{NewAccount.BillingAddress.city}",
                    "@{AccountInfo.recentItems[0].Id}",
                ],
            },
            "dataverse": {
                "template": "$1, $2 …",
                "pattern": DV_REFERENCE_PATTERN.pattern,
                "documented_example": DV_BIND_EXAMPLE,
            },
        },
        "outcomes": list(OUTCOMES),
        "skip_reasons": {SKIP_DEPENDENCY_FAILED: "[sourced] Dependent subrequests aren't executed."},
        "fail_reasons": {
            FAIL_COLLATION_VIOLATION: (
                "[sourced] An implicit dependency executed before the record it "
                "depends on, because collateSubrequests grouped them. Set "
                "collateSubrequests to false."
            )
        },
        "user_flow": describe_user_flow(),
        "sourced_quotes": list(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "adjacent_surfaces": [dict(entry) for entry in ADJACENT_SURFACES],
    }
