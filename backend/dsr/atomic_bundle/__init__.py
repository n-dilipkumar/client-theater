"""Write account + contact + opportunity as one atomic transaction (WF-039).

The researched flow, in six steps, and the domain that carries them:

``dsr.atomic_bundle.vocabulary``
    The researched wire contract: the four endpoints, the two reference
    syntaxes, ``allOrNone`` and ``collateSubrequests``, the six sets of
    documented limits, the per-step outcomes, and the researched user flow.
``dsr.atomic_bundle.references``
    Reference resolution. ``@{refAccount.id}`` with the documented dotted and
    indexed paths, and ``$1`` against a Content-ID.
``dsr.atomic_bundle.planner``
    The dependency-ordered plan - the researched claim that the graph is
    declared as data - and the limit checks that refuse a bundle the CRM would
    reject.
``dsr.atomic_bundle.dialects``
    One plan rendered four ways: composite, sObject tree, Dataverse changeset,
    and HubSpot's sequence.
``dsr.atomic_bundle.transport``
    Where the request goes: the real HTTP transport, and the in-process CRM
    that executes a plan and writes what it creates into the audited store.
``dsr.atomic_bundle.engine``
    :class:`~dsr.atomic_bundle.engine.BundleCommitter` - connectors, bundles, the
    researched bundle preview, the commit, and the run log.
``dsr.atomic_bundle.inferences``
    Every judgement call this package makes, named and served over HTTP.
``dsr.atomic_bundle.errors``
    One error hierarchy, so the HTTP layer maps each answer to one status.

Nothing in this package imports ``dsr.api``; the HTTP surface lives in
:mod:`dsr.features.wf039_write_account_contact_opportunity_as_o`, and nothing here
imports another feature's package either. The two shared seams it *does* use -
:class:`~dsr.store.RecordStore` and :data:`~dsr.deps.StoreDep` - are the seams
the contract names.
"""

from dsr.atomic_bundle.dialects import (
    CHANGESET_DEFAULT,
    CHANGESET_PREFIX,
    DEFAULT_API_VERSION,
    DEFAULT_DATAVERSE_VERSION,
    RequestDocument,
    RequestPart,
    RenderOptions,
    object_type_of,
    render,
    tree_children,
)
from dsr.atomic_bundle.engine import (
    BUNDLE_COLLECTION,
    COLLECTIONS,
    CONNECTOR_COLLECTION,
    RUN_COLLECTION,
    TRANSPORTS,
    BundleCommitter,
)
from dsr.atomic_bundle.errors import (
    BundleError,
    BundleNotConfigured,
    BundleShapeError,
    LimitExceeded,
    NotFound,
    PolicyError,
    ReferenceError,
)
from dsr.atomic_bundle.inferences import (
    INFERENCES,
    by_id,
    describe as describe_inferences,
    warning_codes,
)
from dsr.atomic_bundle.planner import (
    WARNINGS,
    BundlePlan,
    Step,
    plan_bundle,
)
from dsr.atomic_bundle.references import (
    content_id,
    content_id_for,
    dv_references,
    resolve_dv,
    resolve_sf,
    sf_references,
    sf_references_in_text,
)
from dsr.atomic_bundle.transport import (
    TARGET_COLLECTION,
    CommitResult,
    LocalCrm,
    StepOutcome,
    Transport,
    UrllibTransport,
)
from dsr.atomic_bundle.vocabulary import (
    ATOMICITY,
    COLLATE_MEANING,
    DATAVERSE_BATCH,
    DIALECTS,
    FAIL_COLLATION_VIOLATION,
    HS_ASSOCIATION_PATH,
    HS_CONTACTS_BATCH_PATH,
    LIMITS,
    OUTCOMES,
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SKIPPED,
    POLICIES,
    POLICY_MEANING,
    POLICY_PARTIAL,
    POLICY_STRICT,
    REFERENCE_SYNTAX,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    USER_FLOW,
    describe as describe_vocabulary,
)

__all__ = [
    "ATOMICITY",
    "BUNDLE_COLLECTION",
    "CHANGESET_DEFAULT",
    "CHANGESET_PREFIX",
    "COLLATE_MEANING",
    "COLLECTIONS",
    "CONNECTOR_COLLECTION",
    "DATAVERSE_BATCH",
    "DEFAULT_API_VERSION",
    "DEFAULT_DATAVERSE_VERSION",
    "DIALECTS",
    "FAIL_COLLATION_VIOLATION",
    "HS_ASSOCIATION_PATH",
    "HS_CONTACTS_BATCH_PATH",
    "INFERENCES",
    "LIMITS",
    "OUTCOMES",
    "OUTCOME_CREATED",
    "OUTCOME_FAILED",
    "OUTCOME_ROLLED_BACK",
    "OUTCOME_SKIPPED",
    "POLICIES",
    "POLICY_MEANING",
    "POLICY_PARTIAL",
    "POLICY_STRICT",
    "REFERENCE_SYNTAX",
    "RUN_COLLECTION",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "TARGET_COLLECTION",
    "TRANSPORTS",
    "USER_FLOW",
    "WARNINGS",
    "BundleCommitter",
    "BundleError",
    "BundleNotConfigured",
    "BundlePlan",
    "BundleShapeError",
    "CommitResult",
    "LimitExceeded",
    "LocalCrm",
    "NotFound",
    "PolicyError",
    "ReferenceError",
    "RequestDocument",
    "RequestPart",
    "RenderOptions",
    "Step",
    "StepOutcome",
    "Transport",
    "UrllibTransport",
    "by_id",
    "content_id",
    "content_id_for",
    "describe_inferences",
    "describe_vocabulary",
    "dv_references",
    "object_type_of",
    "plan_bundle",
    "render",
    "resolve_dv",
    "resolve_sf",
    "sf_references",
    "sf_references_in_text",
    "tree_children",
    "warning_codes",
]
