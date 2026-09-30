"""Every judgement call this build makes where the research is silent.

The research for WF-063 is specific about its own vocabulary and about what
reassignment ignores. It is silent on several things a working implementation
has to decide. Those decisions are product behaviour, not comments, so they are
named here and served at ``/api/wf-063/inferences`` for a reviewer to disagree
with one entry at a time rather than by reading a diff.

The pattern follows the ones already in this codebase: each entry carries the
``basis`` it rests on, the ``value`` this build chose, a ``why``, the
``change_it`` that names the code to touch, and a ``blast_radius``. A wrong
inference should be findable by name.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "round-robin-limits-the-automatic-path-only",
        "topic": "what the round-robin limit applies to",
        "basis": (
            "Cal's reassign/auto endpoint is documented as 'Currently only supports reassigning host for "
            "round robin bookings', while reassign/{userId} is 'Reassign a booking to a specific host' and "
            "carries no such limit. The research does not say whether a named individual may be chosen for "
            "a booking that is not round robin."
        ),
        "value": {
            "specific_host_requires_round_robin": False,
            "auto_select_requires_round_robin": True,
            "read_as": "the limit is a property of the endpoint, not of the booking",
        },
        "why": (
            "The sentence is attached to the endpoint that picks the host for you, and the endpoint that "
            "takes a specific user id says nothing of the kind. Refusing to reassign a non-round-robin "
            "booking to a named person would also break the researched rescue case: a stale booking with "
            "the wrong host is exactly what reassignment exists to fix, and requiring the booking to have "
            "been round robin first would make the one booking most in need of help the hardest to move."
        ),
        "change_it": "The round-robin guard in rules.decide, and rules._resolve_named_host.",
        "blast_radius": "Every reassignment of a non-round-robin booking, by name or by group.",
    },
    {
        "id": "availability-is-checked-against-the-meetings-own-slot",
        "topic": "which availability the new host is checked against",
        "basis": (
            "The flow says 'pick a new slot' and the evidence says reassignment ignores the minimum "
            "scheduling notice and the maximum availability range. The research does not say whether a "
            "host's existing bookings elsewhere count as a conflict."
        ),
        "value": {
            "checks": "the new host's own calendar blocks overlapping the proposed window",
            "ignored": ["min_notice", "max_range"],
            "not_ignored": ["calendar_conflict", "host_inactive", "distribution_membership"],
        },
        "why": (
            "The researched exemption is written as a list of exactly two things, and it is introduced with "
            "'does not take into account' rather than 'ignores the host's schedule'. Reading it as a general "
            "exemption would let a reassignment double-book someone, and a double-booked host is the failure "
            "the whole workflow is meant to prevent. Enumerating what is still enforced keeps the exemption "
            "narrow and testable instead of implied."
        ),
        "change_it": "rules.decide's availability branch, and distribution.conflicts_with.",
        "blast_radius": "Any reassignment where the new host has a block over the slot.",
    },
    {
        "id": "one-transaction-for-the-whole-reassignment",
        "topic": "whether a partial reassignment may be written",
        "basis": (
            "The data flow reads 'old invite updated/retired -> Meeting Update webhook -> Events History "
            "audit row'. It does not say what happens if the middle step fails."
        ),
        "value": {
            "atomic": True,
            "records_written": ["meeting", "reassignment", "meeting_events_history", "host_credit"],
            "on_refusal": "nothing at all is written",
        },
        "why": (
            "An audit row claiming a reassignment happened while the meeting still names the old host is "
            "the exact drift the product's audit guarantee exists to prevent, so the whole flow is one "
            "transaction. A refused request writes nothing: there is no reassignment to record, and a row "
            "saying 'we tried and did not' would be indistinguishable from a row saying 'we tried and "
            "succeeded' to any reader who did not open the detail."
        ),
        "change_it": "ReassignEngine.reassign's transaction block.",
        "blast_radius": "Nothing on the happy path. Everything on a partial-failure path.",
    },
    {
        "id": "credit-moves-only-once",
        "topic": "what happens to round-robin credit when a no-show credit-back already ran",
        "basis": (
            "Automations list 'no-show credit-back interacts with reassignment' without saying how. The "
            "research does not describe the credit ledger at all."
        ),
        "value": {
            "on_reassignment": "one credit moves from the previous host to the new one",
            "after_no_show_credit_back": "no credit moves",
            "reason_recorded": True,
        },
        "why": (
            "A credit-back returns a booking's credit to the host who absorbed the no-show. If a later "
            "reassignment then moved a second credit, one booking would be worth two credits in the "
            "rotation, and the rotation would drift further from even every time a no-show was followed by "
            "a reassignment. Recording that the credit did not move, and why, is more useful to an "
            "administrator than a silently absent number."
        ),
        "change_it": "distribution.move_credit and distribution.credit_patch.",
        "blast_radius": "Reassignments of meetings that have already had a no-show credit-back.",
    },
    {
        "id": "auto-selects-the-fewest-credits",
        "topic": "which host an automatic reassignment picks",
        "basis": (
            "The research says the credit state 'moves with the host' and that the platform can "
            "'auto-select the replacement host'. It does not publish the selection rule."
        ),
        "value": {
            "order": "fewest round-robin credits first",
            "tie_break": "lowest host id",
            "excluded": ["inactive", "already_the_host", "out_of_scope", "busy"],
        },
        "why": (
            "Picking the host who has hosted least is what a round robin is for, and it is the only "
            "selection rule that the surrounding sentence about credit state supports. The tie-break is "
            "there because a rotation routinely has two hosts on the same count, and an arbitrary choice "
            "between them would make the same state reassign to a different person on a different run."
        ),
        "change_it": "distribution.auto_select.",
        "blast_radius": "Every automatic (group) reassignment.",
    },
    {
        "id": "invite-fields-do-not-survive-the-host-change",
        "topic": "what happens to an invite field the new host has not set",
        "basis": (
            "The evidence says the platform 'should update the invite accordingly with the new assignee's "
            "name, links, and other details that possibly changed from one assignee to another'."
        ),
        "value": {"strategy": "rebuild from the new host", "carried_over": False, "missing_becomes": None},
        "why": (
            "The fields are per-assignee by construction - a dial-in belongs to whoever holds the phone. "
            "Carrying an unset field over would leave the previous host's number on the new host's "
            "meeting, which is the exact defect the sentence is written to prevent. Nulling it makes the "
            "gap visible in the invite the buyer reads, where silently keeping a wrong number would not."
        ),
        "change_it": "vocabulary.invite_for.",
        "blast_radius": "Any reassignment where the new host has a field the old host did not, or the reverse.",
    },
    {
        "id": "no-outbound-calendar-or-crm-call",
        "topic": "whether this workflow calls Google, Chili Piper or a CRM",
        "basis": (
            "The research documents two outbound APIs and four entry points, and this product has no "
            "client for any of them. The data sources name a calendar provider and a CRM Event surface."
        ),
        "value": {
            "calls_outbound": False,
            "invite_recorded": "the reassignment's before/after invite, readable over HTTP",
            "webhooks_recorded": "both payload shapes, built but not delivered",
        },
        "why": (
            "The researchable behaviour here is which payload a reassignment produces and which fields "
            "change; both are answerable without a transport. Faking an HTTP client to a vendor the "
            "product cannot authenticate to would be a claim this build cannot back, and the decision "
            "logic is the part that is worth testing."
        ),
        "change_it": "webhooks.py. Nothing else in the package knows a delivery step is missing.",
        "blast_radius": "Nothing today. It is the seam a real connector would use.",
    },
    {
        "id": "the-distribution-is-the-meetings-own",
        "topic": "whether assign_to may name a different Distribution",
        "basis": (
            "The data flow says the scheduler 'reopens the *same* Distribution context' and the evidence "
            "says reassignment 'will take into account ... the Distribution settings of the meeting "
            "booked'. The user flow's 'change the Distribution, Team, or Individual' is the three "
            "granularities the reopened scheduler offers. The research does not say whether 'change the "
            "Distribution' means move between distributions or choose within the one already open."
        ),
        "value": {
            "reads_as": "choose who within the meeting's own Distribution",
            "naming_another_distribution": "refused",
            "granularity": "team and distribution mean eligible members of this one, not of another",
        },
        "why": (
            "Both researched sentences point at the meeting booked, and a reassignment that quietly "
            "switched Distribution would apply a different notice window, a different membership and "
            "different invite details - a different meeting wearing the same booking. Reading the flow's "
            "three words as the three granularities keeps the same-context reading and the editable-axis "
            "reading consistent with each other, where reading them as a cross-distribution move makes "
            "the second contradict the first. Naming the meeting's own distribution is accepted, so a "
            "client may send it and get the same answer either way."
        ),
        "change_it": "The 3a branch of rules.decide.",
        "blast_radius": "Only requests whose assign_to names a distribution id other than the meeting's.",
    },
    {
        "id": "surface-is-recorded-not-gated",
        "topic": "which entry points are enforced rather than described",
        "basis": (
            "Four entry points are documented. Only one carries a stated precondition: the calendar add-on "
            "requires the ChiliCal extension installed and logged in."
        ),
        "value": {
            "gated": ["chilical_home"],
            "recorded_only": ["meetings_activity", "myapp", "crm_event_button", "api"],
        },
        "why": (
            "Only the add-on precondition is quoted, and an invented gate on the other three would refuse "
            "requests the research never said were invalid. The surface is still recorded on every "
            "reassignment, because the Events History row must display it - that part is sourced."
        ),
        "change_it": "rules._add_on_state.",
        "blast_radius": "Requests claiming a surface other than chilical_home.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced."""
    from dsr.reassign import rules
    from dsr.reassign.distribution import HALF_OPEN_INTERVALS, INELIGIBLE_REASONS
    from dsr.reassign.vocabulary import published_vocabulary

    return {
        "count": len(INFERENCES),
        "sourced": published_vocabulary(),
        "inferences": [dict(entry) for entry in INFERENCES],
        "outcomes": rules.outcome_table(),
        "notes": {
            "half_open_intervals": HALF_OPEN_INTERVALS,
            "ineligible_reasons": list(INELIGIBLE_REASONS),
        },
    }
