"""WF-055: hand a lead off from an SDR scheduler to an AE.

The researched flow, in its own order: an admin builds a **Handoff Router** in a
workspace, defining routing paths (e.g. region to an AE pod, product line to an
AE); an SDR opens the Handoff scheduler and enters the guest's email, or the CRM
record id; the router is evaluated and **one or more routing paths** come back,
each with its own ``pathId`` and ``startTimes``; and the SDR picks a path and a
slot, and the meeting is booked with the AE as Assignee and the SDR as Booker.

The package is laid out so the line between what the research published and what
this build chose is visible without reading the code:

``vocabulary``
    Every researched term, each beside the sentence that fixes it, and the
    constants this build named where the research published none.
``inferences``
    Every decision the research left open, with the reading chosen, the reason,
    and the reading that was rejected.
``errors``
    One hierarchy hanging off a single base, so the feature module registers one
    handler. Each refusal carries its own ``code`` and ``status``, because these
    refusals are not all the same kind of thing.
``timeutil``
    Every timestamp is an aware UTC datetime, and the slot grid is aligned to the
    interval's own start rather than to midnight.
``workspaces``
    One SDR/AE pod, its user records, their roles, and the calendar gate that
    decides who may book and who may be booked.
``paths``
    The routing path declaration, and the researched ``Required`` toggle that
    decides whether an Additional Invitee's availability narrows the path.
``rules``
    The request context a routing rule reads: the two researched request fields
    plus whatever the integrator passed as ``crmExplicits``.
``availability``
    Per-path ``startTimes``, and the derivation behind the operation used to
    combine the calendars on one path.
``engine``
    The two researched API calls, and every write.

This package imports nothing but the store. No framework, no ``dsr.api``, no
``sqlite3``, and no ``AuditedDatabase``.
"""

from dsr.handoff_scheduler.availability import (
    DERIVATION_ID,
    MAX_RANGE_DAYS,
    MAX_SLOTS,
    busy_at,
    explain_missing,
    find_slot,
    normalise_interval,
    path_window,
)
from dsr.handoff_scheduler.engine import (
    DEFAULT_DURATION_MINUTES,
    MEETING_COLLECTION,
    ROUTER_COLLECTION,
    ROUTING_COLLECTION,
    WORKSPACE_COLLECTION,
    HandoffSchedulerEngine,
)
from dsr.handoff_scheduler.errors import HandoffConflict, HandoffError, HandoffNotFound
from dsr.handoff_scheduler.inferences import INFERENCE_IDS, INFERENCES, by_id, describe
from dsr.handoff_scheduler.paths import (
    DEFAULT_REQUIRED,
    assignee_can_be_assigned,
    gating_user_ids,
    ignored_user_ids,
    path_id,
    required_of,
    validate_path,
    validate_paths,
)
from dsr.handoff_scheduler.rules import (
    CRM_RECORD_FIELD,
    CRM_REQUEST,
    GUEST_EMAIL_REQUEST,
    RESERVED_FIELDS,
    build_context,
    explain_miss,
    match_report,
    matches,
    read_request,
    value_matches,
)
from dsr.handoff_scheduler.timeutil import grid, is_free, iso, overlaps, parse, utcnow
from dsr.handoff_scheduler.vocabulary import (
    API_FIELD_NAMES,
    ASSIGNEE,
    BOOKED,
    BOOKER,
    CANCELLED,
    CONFIRMED,
    CRM_EXPLICITS,
    EVALUATION_OUTCOME_NAMES,
    EVALUATION_OUTCOMES,
    HANDOFF,
    LINK_TYPE_NAMES,
    LINK_TYPES,
    LIVE_MEETING_STATES,
    LIVE_ROUTING_STATES,
    MEETING_ROLE_NAMES,
    MEETING_ROLES,
    MEETING_STATES,
    NO_AVAILABILITY,
    OPEN,
    PATH_AVAILABILITY,
    PATHS_OFFERED,
    POD_PARTITION,
    REASSIGNMENT_HANDOFF,
    REQUEST_TYPE_NAMES,
    REQUIRED_TOGGLE,
    ROUTING_STATES,
    SOURCES,
    published_vocabulary,
    require_meeting_state,
    require_outcome,
    require_request_type,
    require_role,
    require_routing_state,
)
from dsr.handoff_scheduler.workspaces import (
    ROLE_NAMES,
    calendar_connected,
    exclusion_reason,
    find_user,
    has_role,
    index_users,
    require_user,
    role_article,
    roles_of,
    summarise,
    user_id,
    validate_workspace,
)

__all__ = [
    "API_FIELD_NAMES",
    "ASSIGNEE",
    "BOOKED",
    "BOOKER",
    "CANCELLED",
    "CONFIRMED",
    "CRM_EXPLICITS",
    "CRM_RECORD_FIELD",
    "CRM_REQUEST",
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_REQUIRED",
    "DERIVATION_ID",
    "EVALUATION_OUTCOMES",
    "EVALUATION_OUTCOME_NAMES",
    "GUEST_EMAIL_REQUEST",
    "HANDOFF",
    "LINK_TYPES",
    "LINK_TYPE_NAMES",
    "LIVE_MEETING_STATES",
    "LIVE_ROUTING_STATES",
    "MAX_RANGE_DAYS",
    "MAX_SLOTS",
    "MEETING_COLLECTION",
    "MEETING_ROLES",
    "MEETING_ROLE_NAMES",
    "MEETING_STATES",
    "NO_AVAILABILITY",
    "OPEN",
    "PATH_AVAILABILITY",
    "PATHS_OFFERED",
    "POD_PARTITION",
    "REASSIGNMENT_HANDOFF",
    "REQUEST_TYPE_NAMES",
    "REQUIRED_TOGGLE",
    "RESERVED_FIELDS",
    "ROLE_NAMES",
    "ROUTER_COLLECTION",
    "ROUTING_COLLECTION",
    "ROUTING_STATES",
    "SOURCES",
    "WORKSPACE_COLLECTION",
    "HandoffConflict",
    "HandoffError",
    "HandoffNotFound",
    "HandoffSchedulerEngine",
    "INFERENCES",
    "INFERENCE_IDS",
    "assignee_can_be_assigned",
    "build_context",
    "busy_at",
    "by_id",
    "calendar_connected",
    "describe",
    "exclusion_reason",
    "explain_miss",
    "explain_missing",
    "find_slot",
    "find_user",
    "gating_user_ids",
    "grid",
    "has_role",
    "ignored_user_ids",
    "index_users",
    "is_free",
    "iso",
    "match_report",
    "matches",
    "normalise_interval",
    "overlaps",
    "parse",
    "path_id",
    "path_window",
    "published_vocabulary",
    "read_request",
    "require_meeting_state",
    "require_outcome",
    "require_request_type",
    "require_role",
    "require_routing_state",
    "require_user",
    "required_of",
    "role_article",
    "roles_of",
    "summarise",
    "user_id",
    "utcnow",
    "validate_path",
    "validate_paths",
    "validate_workspace",
    "value_matches",
]
