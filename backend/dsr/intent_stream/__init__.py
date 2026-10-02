"""WF-032: stream identified company/contact intent to your own systems.

Implemented from ``docs/research/digital-sales-room-workflows/wf/WF-032.md``,
whose research source is section 17 of ``docs/research/raw/analytics-intent.md``.

The public surface is :class:`~dsr.intent_stream.stream.IntentStream`. Everything
else in the package is an implementation detail of it. The HTTP surface is not
here: the feature host mounts
``dsr/features/wf032_stream_identified_company_contact_inte.py``, which owns the
routes under ``/api/wf-032`` and the mapping of
:class:`~dsr.intent_stream.errors.IntentStreamError` to a response.

What is researched and what is not
----------------------------------
Sourced, and implemented as researched: the **Webhooks** workflow type chosen from
Workflows -> New Workflow; a name and "the URL you want to send data to"; the
conditions a workflow applies "based on **saved Segments** from your account";
"only send a lead **once**" versus "**send updates as well**", with the promise
that an update carries "the same lead with updated activity data if that lead
visits your webpage again"; the payload output "only **Company** ... or **Company +
Contacts**", with contacts "filter[ed] ... on '**Keywords**' and required fields";
a token that is "**automatically generated**" and "**optional** to specify"; and
the destination being "a public API for a third party tool or a custom solution",
with the **Microsoft Teams** and **Google Sheets** recipes the research names.

Not researched, and therefore this build's own choices - each named, bounded, and
served at ``GET /api/wf-032/inferences`` so a reviewer can disagree with one by
name instead of finding it in a diff: the HTTP routes, the collection names, the
token's header, the Segment rule grammar, the delivery states, the timeout, the
payload envelope's own field names, and the whole of the failure handling. This
research describes **no** retry ladder, **no** token rotation, and **no** inbound
surface, so none of the three was built; see
:mod:`dsr.intent_stream.inferences` for why each omission is a decision rather
than a gap.

The two decisions most worth arguing with
-----------------------------------------
``required-fields-filter-contacts-not-fields``: "filtering the contact details on
'Keywords' and required fields" reads two ways, and this build reads it as two
filters on *which contacts are sent* rather than a projection of *which fields
are sent*. And ``conditions-require-at-least-one-segment``: the researched flow
makes conditions a step rather than an option, so a workflow with no Segment is
refused rather than quietly pointed at every company in the account.

What the flow looks like in code
--------------------------------
``record_visit`` is the whole automation in one method: write the visit, refresh
the lead's activity data, evaluate every workflow, and record one delivery row
per workflow per outcome - including the outcomes where nothing was sent, because
a rule that does not fall through is a bug somebody hits in production.
"""

from __future__ import annotations

from dsr.intent_stream import payloads, segments, tokens  # noqa: F401 - re-exported for callers
from dsr.intent_stream.delivery import (
    DeliveryResult,
    Transport,
    UrllibTransport,
    classify,
    encode,
    is_retryable,
)
from dsr.intent_stream.errors import (
    DeliveryError,
    IntentStreamError,
    LeadError,
    SegmentError,
    TargetError,
    WorkflowError,
)
from dsr.intent_stream.inferences import INFERENCES, describe as describe_inferences
from dsr.intent_stream.payloads import build_payload, filter_contacts
from dsr.intent_stream.registry import (
    Book,
    Books,
    lead_state_id,
    summarise_contact,
    summarise_delivery,
    summarise_lead,
    summarise_segment,
    summarise_visit,
    summarise_workflow,
)
from dsr.intent_stream.segments import (
    OPERATORS as SEGMENT_OPERATORS,
    evaluate_conditions,
    evaluate_segment,
)
from dsr.intent_stream.stream import IntentStream, url_warnings, validate_target_url
from dsr.intent_stream.tokens import (
    generate as generate_token,
    mask as mask_token,
    matches as token_matches,
)
from dsr.intent_stream.vocabulary import (
    ALL_COLLECTIONS,
    CONTACT_COLLECTION,
    DEFAULT_SEGMENT_MATCH,
    DEFAULT_SEND_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    DELIVERY_COLLECTION,
    DELIVERY_STATES,
    DESTINATION_RECIPES,
    EVIDENCE,
    FLOW,
    KEYWORD_FIELDS,
    LEAD_COLLECTION,
    MATCH_ALL,
    MATCH_ANY,
    PAYLOAD_COMPANY,
    PAYLOAD_COMPANY_CONTACTS,
    PAYLOAD_MODES,
    PAYLOAD_TYPE,
    SEGMENT_COLLECTION,
    SEND_MODES,
    SEND_ONCE,
    SEND_UPDATES,
    SKIP_ALREADY_SENT,
    SKIP_INACTIVE,
    SKIP_NOT_MATCHED,
    SKIP_REASONS,
    STATE_COLLECTION,
    STATE_DELIVERED,
    STATE_FAILED,
    STATE_SKIPPED,
    TOKEN_BODY_FIELD,
    TOKEN_HEADER,
    VISIT_COLLECTION,
    WORKFLOW_COLLECTION,
    WORKFLOW_TYPE,
    vocabulary,
)

__all__ = [
    "IntentStream",
    "IntentStreamError",
    "TargetError",
    "SegmentError",
    "WorkflowError",
    "LeadError",
    "DeliveryError",
    "validate_target_url",
    "url_warnings",
    "Books",
    "Book",
    "lead_state_id",
    "evaluate_conditions",
    "evaluate_segment",
    "build_payload",
    "filter_contacts",
    "generate_token",
    "mask_token",
    "token_matches",
    "TOKEN_HEADER",
    "TOKEN_BODY_FIELD",
    "DeliveryResult",
    "Transport",
    "UrllibTransport",
    "encode",
    "classify",
    "is_retryable",
    "WORKFLOW_TYPE",
    "SEND_ONCE",
    "SEND_UPDATES",
    "SEND_MODES",
    "DEFAULT_SEND_MODE",
    "PAYLOAD_COMPANY",
    "PAYLOAD_COMPANY_CONTACTS",
    "PAYLOAD_MODES",
    "PAYLOAD_TYPE",
    "MATCH_ANY",
    "MATCH_ALL",
    "DEFAULT_SEGMENT_MATCH",
    "SEGMENT_OPERATORS",
    "KEYWORD_FIELDS",
    "STATE_DELIVERED",
    "STATE_FAILED",
    "STATE_SKIPPED",
    "DELIVERY_STATES",
    "SKIP_ALREADY_SENT",
    "SKIP_INACTIVE",
    "SKIP_NOT_MATCHED",
    "SKIP_REASONS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DESTINATION_RECIPES",
    "EVIDENCE",
    "FLOW",
    "SEGMENT_COLLECTION",
    "LEAD_COLLECTION",
    "CONTACT_COLLECTION",
    "VISIT_COLLECTION",
    "WORKFLOW_COLLECTION",
    "DELIVERY_COLLECTION",
    "STATE_COLLECTION",
    "ALL_COLLECTIONS",
    "INFERENCES",
    "describe_inferences",
    "vocabulary",
    "summarise_workflow",
    "summarise_segment",
    "summarise_lead",
    "summarise_contact",
    "summarise_visit",
    "summarise_delivery",
    "payloads",
    "segments",
    "tokens",
]
