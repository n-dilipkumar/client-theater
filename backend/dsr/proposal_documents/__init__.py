"""WF-103: generate a proposal from CRM data, gate it on internal approval, sync status back.

The package is four modules and one boundary, and the boundary is the point of it:

* :mod:`~dsr.proposal_documents.vocabulary` - every researched term, the state machine
  the data flow enumerates, the refusal codes, and the derived-stand-in markers. The
  one place any of those is named.
* :mod:`~dsr.proposal_documents.errors` - the two error types, each carrying its
  published code and the status that code maps to.
* :mod:`~dsr.proposal_documents.rules` - pure functions. Nothing here reads or writes.
* :mod:`~dsr.proposal_documents.inferences` - the judgement calls, served so a
  reviewer reads the list instead of reconstructing it from a diff.
* :mod:`~dsr.proposal_documents.engine` - the only module that touches the store.

What this workflow does not do
------------------------------

It calls no document API. The research documents ten sources and the full endpoint
list; it documents no credential this product holds, and this product has no HTTP
client for a document vendor. So the document, its id, its per-recipient shared link
and its status are *derived* from the CRM record and written to the audited store.

That is not a quiet shortcut. The discipline is the one
``headless_booking.assets.meeting_link`` established, and it is four separate
declarations so a reader who ignores the metadata still cannot mistake the artefact
for a real one:

1. every derived identifier is built on the reserved ``.invalid`` host, so it cannot
   resolve even in principle;
2. every document row carries ``document_url_is_derived: True``;
3. every response that carries a derived artefact carries a prose note saying so;
4. a named inference, ``no-outbound-document-api-call``, explains why.

"A demo that showed a plausible document without saying so is worse than one that says
it is derived."

The one half that is real
-------------------------

The writeback. "The integration writes status back to the CRM (opportunity stage/close
date, notes & attachments) and, via ``linked_objects``, makes the document discoverable
from the CRM record." That happens in this product, through the audited store, in the
same transaction as the change, and it is the part of the workflow the audit log can
actually vouch for.
"""

from __future__ import annotations

from dsr.proposal_documents import (
    engine as _engine,
    errors as _errors,
    inferences as _inferences,
    rules as _rules,
    vocabulary as _vocabulary,
)
from dsr.proposal_documents.engine import ACTOR, ProposalEngine
from dsr.proposal_documents.errors import ProposalNotFound, ProposalRefusal, refuse
from dsr.proposal_documents.rules import (
    after_send,
    apply_webhook,
    build_document_request,
    build_pricing_table,
    evaluate_send,
    is_duplicate,
    normalise_decision,
    normalise_role,
    quote_update_effect,
    record_approval,
    require_quote_update,
    require_sendable,
    shared_link_for,
    stage_for,
    stamp,
    sync_from_document,
    validate_recipients,
    verify_signature,
)
from dsr.proposal_documents.vocabulary import (
    ACTIVITY,
    APPROVALS,
    CRM_SYNC,
    DOCUMENTS,
    PRICING_TABLES,
    RECIPIENTS,
    SOURCE_DEALS,
    STATE_CHANGES,
    WEBHOOK_EVENTS,
    derived_document_url,
    derived_shared_link,
    describe,
    normalise_state,
    vendor_state,
)

__all__ = [
    # engine
    "ACTOR",
    "ProposalEngine",
    # errors
    "ProposalNotFound",
    "ProposalRefusal",
    "refuse",
    # rules
    "after_send",
    "apply_webhook",
    "build_document_request",
    "build_pricing_table",
    "evaluate_send",
    "is_duplicate",
    "normalise_decision",
    "normalise_role",
    "quote_update_effect",
    "record_approval",
    "require_quote_update",
    "require_sendable",
    "shared_link_for",
    "stage_for",
    "stamp",
    "sync_from_document",
    "validate_recipients",
    "verify_signature",
    # vocabulary
    "ACTIVITY",
    "APPROVALS",
    "CRM_SYNC",
    "DOCUMENTS",
    "PRICING_TABLES",
    "RECIPIENTS",
    "SOURCE_DEALS",
    "STATE_CHANGES",
    "WEBHOOK_EVENTS",
    "derived_document_url",
    "derived_shared_link",
    "describe",
    "normalise_state",
    "vendor_state",
    # module handles
    "_engine",
    "_errors",
    "_inferences",
    "_rules",
    "_vocabulary",
]
