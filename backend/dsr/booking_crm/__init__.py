"""WF-065: write the booking back into the CRM.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-065.md``, which is
the specification. The researched decisions are the product: the eight router
node names and the vendor each belongs to, the six branch labels, the two
selection rules - *most recently created Open Case*, *nearest Close Date
Opportunity* - the ``Booked`` CampaignMember status, the three owner identities,
the org-wide Sync Meeting Type toggle, the per-additional-guest child Events, and
the two sentences most implementations lose: the node that must precede the rest,
and the Contact that gates the extra relation.

Seven modules, each with one job:

``errors``
    The refusals this workflow makes, under one domain base type.
``vocabulary``
    The researched contract as data: the nodes, the branches, the related objects,
    the selection rules, the quotes, and the research's own two stated gaps.
``flow``
    The declaration - a meeting type, a path, an ordered node list - and the
    ordering check that refuses a flow that could never run.
``local_crm``
    The CRM the write goes to: matching by email, the related-object selection
    rules executed for real, and fault injection so a failed Event is reachable.
``engine``
    The run: every node in declared order, one run record, one Events History row
    per Event attempted.
``inferences``
    Every decision the research does not make, named and served at ``/inferences``.

Schema flexibility
------------------
No migration, no typed column, no new required field. A flow's node list is
arbitrary JSON, a booking is whatever the router sent, and a Data Field maps to
any CRM property a team names - which is the researched extensibility claim
made concrete, and the reason no team adding a field has to coordinate with
anyone.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the
routes pass the route that served the request, built from ``router.prefix``.
Hardcoding a source string in a domain function puts a path in the audit log that
the app might have stopped serving, and that class of bug has shipped in this
codebase before.
"""

from __future__ import annotations

from dsr.booking_crm import flow, inferences, local_crm, vocabulary
from dsr.booking_crm.engine import (
    COLLECTIONS,
    CONNECTOR_COLLECTION,
    FLOW_COLLECTION,
    HISTORY_COLLECTION,
    MEETING_TYPE_COLLECTION,
    NO_RECORD_MESSAGE,
    OUTCOMES,
    OUTCOME_APPLIED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    RUN_COLLECTION,
    REASON_MEETING_TYPE_SYNC_OFF,
    REASON_NO_CREATE,
    REASON_NO_RECORD,
    REASON_RETRY_NOT_FAILED,
    BookingWriteback,
    NodeResult,
    identity_for,
    normalise_booking,
)
from dsr.booking_crm.errors import (
    InvalidConfig,
    InvalidNode,
    NodeOrderError,
    NotConfigured,
    NotFound,
    WritebackError,
)
from dsr.booking_crm.flow import (
    PATH_MEANING,
    describe_plan,
    normalise_nodes,
    normalise_path,
    plan_order,
    sync_enabled,
    validate_nodes,
)
from dsr.booking_crm.inferences import INFERENCES
from dsr.booking_crm.inferences import by_id as inference_by_id
from dsr.booking_crm.inferences import describe as describe_inferences
from dsr.booking_crm.inferences import node_vocabulary
from dsr.booking_crm.local_crm import (
    CRM_RECORD_COLLECTION,
    CRM_TYPES,
    OPEN_STATUS,
    RESPONSE_NOT_PARSED_NOTE,
    CrmRefused,
    LocalCrm,
    describe_record_keys,
    parse_date,
)
from dsr.booking_crm.vocabulary import (
    ACTIVITY_ASSIGNED_TO,
    ALL_NODES,
    ANCHOR_NODES,
    CAL_SYNC_ERRORS_DESCRIPTION,
    CAL_SYNC_ERRORS_PATH,
    CAMPAIGN_MEMBER_STATUS,
    CREATE_BRANCHES,
    DELETE_EVENT_MODES,
    DEPENDENT_NODES,
    HISTORY_RETRY_QUOTE,
    HISTORY_SHOWS_WHEN_QUOTE,
    ORDERING_QUOTE,
    OWNER_FALLBACK_MODES,
    OWNER_IDENTITIES,
    PATHS,
    RELATED_REQUIRES_CONTACT_QUOTE,
    RELATED_SELECTION_QUOTE,
    SELECTION_RULES,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    SYNC_TOGGLE_ORG_WIDE_QUOTE,
    SYNC_TOGGLE_QUOTE,
    UPDATE_BRANCHES,
    VENDORS,
    describe as describe_vocabulary,
)
from dsr.store import RecordStore

__all__ = [
    "ACTIVITY_ASSIGNED_TO",
    "ALL_NODES",
    "ANCHOR_NODES",
    "CAL_SYNC_ERRORS_DESCRIPTION",
    "CAL_SYNC_ERRORS_PATH",
    "CAMPAIGN_MEMBER_STATUS",
    "COLLECTIONS",
    "CONNECTOR_COLLECTION",
    "CREATE_BRANCHES",
    "CRM_RECORD_COLLECTION",
    "CRM_TYPES",
    "DELETE_EVENT_MODES",
    "DEPENDENT_NODES",
    "FLOW_COLLECTION",
    "HISTORY_COLLECTION",
    "HISTORY_RETRY_QUOTE",
    "HISTORY_SHOWS_WHEN_QUOTE",
    "INFERENCES",
    "MEETING_TYPE_COLLECTION",
    "NO_RECORD_MESSAGE",
    "OPEN_STATUS",
    "ORDERING_QUOTE",
    "OUTCOMES",
    "OUTCOME_APPLIED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "OWNER_FALLBACK_MODES",
    "OWNER_IDENTITIES",
    "PATH_MEANING",
    "PATHS",
    "REASON_MEETING_TYPE_SYNC_OFF",
    "REASON_NO_RECORD",
    "REASON_RETRY_NOT_FAILED",
    "RELATED_REQUIRES_CONTACT_QUOTE",
    "RELATED_SELECTION_QUOTE",
    "RESPONSE_NOT_PARSED_NOTE",
    "RUN_COLLECTION",
    "SELECTION_RULES",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SYNC_TOGGLE_ORG_WIDE_QUOTE",
    "SYNC_TOGGLE_QUOTE",
    "UPDATE_BRANCHES",
    "VENDORS",
    "BookingWriteback",
    "CrmRefused",
    "InvalidConfig",
    "InvalidNode",
    "LocalCrm",
    "NodeOrderError",
    "NodeResult",
    "NotConfigured",
    "NotFound",
    "RecordStore",
    "WritebackError",
    "describe_inferences",
    "describe_plan",
    "describe_record_keys",
    "describe_vocabulary",
    "flow",
    "identity_for",
    "inference_by_id",
    "inferences",
    "local_crm",
    "node_vocabulary",
    "normalise_booking",
    "normalise_nodes",
    "normalise_path",
    "parse_date",
    "plan_order",
    "sync_enabled",
    "validate_nodes",
    "vocabulary",
]
