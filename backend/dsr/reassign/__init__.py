"""WF-063: reassign a booked meeting to a different host.

The researched workflow, in the order the research states it. A meeting was
booked and has a host. An administrator opens Meetings Activity, opens the
meeting, and either picks a known and free person and hits Reassign, or chooses
Edit Meeting to reopen the scheduler for the *same* Distribution and pick a new
slot. The Meeting Type and the Workspace cannot change. The invite takes the new
assignee's name, links and details. A ``Meeting Update`` webhook fires, an
Events History row records who, to whom, when and the source, and the round-robin
credit moves with the host.

Module map, in dependency order:

``vocabulary``
    The researched terms, the normalisation, and the invite fields.
``distribution``
    Distributions, hosts, availability, the bounds, and the credit movement.
``errors``
    The two domain error types, and why the split between them is a 400 and a
    409 rather than one code for both.
``webhooks``
    The two webhook payloads and the scope rule that decides which fires.
``rules``
    The state machine: one pure function that answers for both the preview and
    the write, so the two cannot disagree.
``engine``
    The flow over the audited store, in one transaction.
``inferences``
    Every judgement call, named and served over HTTP so a reviewer can disagree
    with a *named* entry instead of hunting through a diff.
"""

from __future__ import annotations

from dsr.reassign import distribution, inferences, rules, vocabulary, webhooks
from dsr.reassign.distribution import (
    CREDIT_ALREADY_RETURNED,
    CREDIT_MOVED,
    CREDIT_SAME_HOST,
    DEFAULT_DURATION_MINUTES,
    HALF_OPEN_INTERVALS,
    INELIGIBLE_REASONS,
    NAIVE_IS_UTC,
    auto_select,
    bounds_summary,
    candidates,
    conflicts_with,
    credit_patch,
    evaluate_bounds,
    format_instant,
    in_distribution_scope,
    is_free,
    move_credit,
    normalise_distribution,
    normalise_host,
    normalise_meeting,
    parse_instant,
)
from dsr.reassign.engine import (
    DISTRIBUTION_COLLECTION,
    HISTORY_COLLECTION,
    HISTORY_LIMIT,
    HOST_COLLECTION,
    MEETING_COLLECTION,
    PAST,
    REASSIGNMENT_COLLECTION,
    UPCOMING,
    ReassignEngine,
)
from dsr.reassign.errors import MeetingStateError, ReassignError
from dsr.reassign.rules import (
    OUTCOMES,
    REASSIGNED,
    Decision,
    decide,
    is_refused,
    outcome_table,
)
from dsr.reassign.vocabulary import (
    ASSIGNED,
    ASSIGNMENT_KINDS,
    ASSIGNMENT_MODES,
    ACTIVITY_TABS,
    AUTO_IS_ROUND_ROBIN_ONLY,
    AUTO_MODE,
    BOUNDS_ARE_IGNORED,
    DEFAULT_SURFACE,
    DEFAULT_TAB,
    EDITABLE_AND_LOCKED,
    EDITABLE_FIELDS,
    EVENTS_HISTORY_ROW,
    EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN,
    GROUP_ASSIGNMENT_KINDS,
    INVITE_FIELDS,
    LOCKED_FIELDS,
    MEETING_STATUSES,
    MEETING_UPDATE_WEBHOOK,
    REASSIGN_MODES,
    REASSIGNED_PAYLOAD_KEYS,
    REASSIGNABLE_STATUSES,
    SPECIFIC_HOST,
    SURFACE_LABELS,
    SURFACES,
    SURFACES_REQUIRING_ADDON,
    SPECIFIC_MODE,
    changed_invite_fields,
    invite_for,
    mode_for_kind,
    normalise_key,
    published_vocabulary,
    require_assignment_kind,
    require_locked,
    require_status,
    require_surface,
    require_tab,
)
from dsr.reassign.webhooks import (
    BOOKING_REASSIGNED,
    MEETING_UPDATE,
    PAYLOAD_VERSION,
    REASSIGNED_ONLY_KEYS,
    events_for,
    webhook_catalogue,
)

__all__ = [
    "ASSIGNED",
    "ASSIGNMENT_KINDS",
    "ASSIGNMENT_MODES",
    "ACTIVITY_TABS",
    "AUTO_IS_ROUND_ROBIN_ONLY",
    "AUTO_MODE",
    "BOOKING_REASSIGNED",
    "BOUNDS_ARE_IGNORED",
    "CREDIT_ALREADY_RETURNED",
    "CREDIT_MOVED",
    "CREDIT_SAME_HOST",
    "DEFAULT_DURATION_MINUTES",
    "DEFAULT_SURFACE",
    "DEFAULT_TAB",
    "DISTRIBUTION_COLLECTION",
    "Decision",
    "EDITABLE_AND_LOCKED",
    "EDITABLE_FIELDS",
    "EVENTS_HISTORY_ROW",
    "EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN",
    "GROUP_ASSIGNMENT_KINDS",
    "HALF_OPEN_INTERVALS",
    "HISTORY_COLLECTION",
    "HISTORY_LIMIT",
    "HOST_COLLECTION",
    "INELIGIBLE_REASONS",
    "INVITE_FIELDS",
    "LOCKED_FIELDS",
    "MEETING_COLLECTION",
    "MEETING_STATUSES",
    "MEETING_UPDATE",
    "MEETING_UPDATE_WEBHOOK",
    "MeetingStateError",
    "NAIVE_IS_UTC",
    "OUTCOMES",
    "PAST",
    "PAYLOAD_VERSION",
    "REASSIGNED",
    "REASSIGNED_ONLY_KEYS",
    "REASSIGNED_PAYLOAD_KEYS",
    "REASSIGNMENT_COLLECTION",
    "REASSIGNABLE_STATUSES",
    "REASSIGN_MODES",
    "ReassignEngine",
    "ReassignError",
    "SPECIFIC_HOST",
    "SPECIFIC_MODE",
    "SURFACES",
    "SURFACE_LABELS",
    "SURFACES_REQUIRING_ADDON",
    "UPCOMING",
    "auto_select",
    "bounds_summary",
    "candidates",
    "changed_invite_fields",
    "conflicts_with",
    "credit_patch",
    "decide",
    "distribution",
    "evaluate_bounds",
    "events_for",
    "format_instant",
    "in_distribution_scope",
    "inferences",
    "invite_for",
    "is_free",
    "is_refused",
    "mode_for_kind",
    "move_credit",
    "normalise_distribution",
    "normalise_host",
    "normalise_key",
    "normalise_meeting",
    "outcome_table",
    "parse_instant",
    "published_vocabulary",
    "require_assignment_kind",
    "require_locked",
    "require_status",
    "require_surface",
    "require_tab",
    "rules",
    "vocabulary",
    "webhook_catalogue",
    "webhooks",
]
