"""Every judgement call this build makes, with the sentence it rests on.

The research for WF-052 is precise about one thing - the request carries no
``interval`` - and precise about the guarantee that follows from it. It is silent
about the rule language, the verdict vocabulary, what an unroutable lead answers
with, where the CRM values in a payload come from, and what the recorded path
writes. Those gaps are product behaviour rather than comments, so they are
collected here as data a reviewer can disagree with by name, and served at
``GET /api/wf-052/inferences`` so the disagreement does not need a code change.

Nothing here is a source. Each entry names what the research said and what this
build chose instead.
"""

from __future__ import annotations

from typing import Any

#: What the research states, and what this build does about each statement.
INFERRED: tuple[dict[str, Any], ...] = (
    {
        "decision": "derived_route_id",
        "question": "Where does the routeId come from, if no session may be consumed?",
        "chose": (
            "A digest of the router slug and the lead payload, shaped like the researched sample. "
            "The same lead through the same router always answers with the same id, and nothing is "
            "stored for it to be stored in."
        ),
        "because": (
            "The research says the routeId is returned but no session is consumed. A stored id "
            "would be the session, so the id is derived instead. Decided by Jev, audit "
            "jev-20261004T042850-28312-30910, over the three candidate representations."
        ),
        "researched": "The routeId is returned but no slot list is computed and no session is consumed.",
    },
    {
        "decision": "interval_refused",
        "question": "What happens when a caller sends an interval to this workflow?",
        "chose": "A 400 that names the researched sentence, rather than ignoring the field.",
        "because": (
            "The difference is whether you pass an interval. Answering half of the other workflow "
            "would hand back a routingId the caller could not use and a slot list this workflow "
            "never computed."
        ),
        "researched": "the difference is whether you pass an `interval`.",
    },
    {
        "decision": "catch_all_required",
        "question": "May a router be saved without a catch-all?",
        "chose": "No. A chain with no catch-all is refused at save, with the sentence in the error.",
        "because": (
            "An unroutable lead is the one outcome the research does not define, and the catch-all "
            "is the vendor's own answer to it. Refusing at save means the unroutable verdict is only "
            "reachable when an assignee is missing, which is a state an operator can fix."
        ),
        "researched": "Each router must end with a 'Catch All' path.",
    },
    {
        "decision": "not_scheduled_collapses_disqualified",
        "question": "The research names both a Not Scheduled and a Disqualified path. Are they one verdict?",
        "chose": "One verdict, not_scheduled, with the rule's own name carrying the difference.",
        "because": (
            "Both paths have the same consequence in the evidence: no scheduler, and a CRM writeback "
            "node available. No field distinguishes them, so a second verdict would be a distinction "
            "nobody could act on."
        ),
        "researched": "CRM writeback nodes available separately on Not Scheduled / Disqualified paths.",
    },
    {
        "decision": "unroutable_is_a_verdict",
        "question": "What does a lead that cannot be routed answer with?",
        "chose": "The verdict unroutable, assignment type unassigned, and a reason naming the gap.",
        "because": (
            "The research asks the workflow to confirm a lead is routable, which implies an answer "
            "for the case where it is not. It is a verdict rather than an error because the caller "
            "still needs the routeId: it is the lead's answer, not a failed request."
        ),
        "researched": "Confirm a lead is routable.",
    },
    {
        "decision": "crm_values_come_from_the_caller",
        "question": "Where does a rule's CRM value come from?",
        "chose": "The request body, under a crm key. This workflow reads no CRM of its own.",
        "because": (
            "The data_sources line is a back-reference to WF-051 and adds only that availability is "
            "not queried. WF-051 owns the CRM integration, and nothing here reads a surface WF-051 "
            "provisions. The caller is the one that already knows the CRM."
        ),
        "researched": "same as #1 (webform, Data Fields, CRM objects, calendars) - but availability is not queried.",
    },
    {
        "decision": "no_email_required",
        "question": "Must the form payload carry an email?",
        "chose": "No. Any non-empty form object qualifies.",
        "because": (
            "WF-053 requires guestEmail because resolving an owner needs it. Nothing in this "
            "workflow's evidence needs an address, so requiring one would refuse leads the research "
            "expects to qualify."
        ),
        "researched": "`form` data (no `interval`).",
    },
    {
        "decision": "recorded_path_stages_the_writeback",
        "question": "What does the CRM-write caller path actually write?",
        "chose": (
            "One verdict row with the CRM writeback staged inside it, marked applied false. Nothing "
            "is pushed anywhere."
        ),
        "because": (
            "The research says no automation fires beyond rule evaluation, deliberately, and lists "
            "the writeback nodes as available separately. Writing a verdict and staging what a node "
            "would write keeps the guarantee while giving the caller the record it needs."
        ),
        "researched": "none fire beyond rule evaluation - deliberately.",
    },
    {
        "decision": "operators_added",
        "question": "Which comparison operators does a rule carry?",
        "chose": (
            "equals, not_equals, contains, in, exists, gt, gte, lt, lte. The first four of those are "
            "this build's; the research only says rules read CRM values and Data Field values."
        ),
        "because": (
            "A screen needs a size comparison to be useful, and a company-size rule is the case the "
            "research's own example implies. The added operators are named here rather than smuggled in."
        ),
        "researched": "Routing rules read CRM values or Data Field values.",
    },
    {
        "decision": "publish_before_serving",
        "question": "May an unpublished router qualify a lead?",
        "chose": "No. A router with enabled false answers 409 rather than qualifying.",
        "because": (
            "The research's step five is that the router is published and deployed before a prospect "
            "meets it. Qualifying through an unpublished router would hide a draft from its own author."
        ),
        "researched": "The router is published and deployed (embedded/deployed to web form, in-app button, or a router link).",
    },
)


def describe() -> dict[str, Any]:
    """Every inference, the researched sentence it answers, and the guarantees."""
    from dsr.lead_qualification.vocabulary import GUARANTEES, published_vocabulary

    return {
        "workflow": "WF-052",
        "spec": "docs/research/digital-sales-room-workflows/wf/WF-052.md",
        "derived_from": "docs/research/raw/scheduling-meetings.md section 2",
        "inferred": [dict(entry) for entry in INFERRED],
        "decisions": [str(entry["decision"]) for entry in INFERRED],
        "sourced": {
            "no_session_consumed": GUARANTEES["consumes_no_session"],
            "availability_not_queried": GUARANTEES["queries_no_availability"],
            "no_slot_list": GUARANTEES["computes_no_slots"],
            "no_automation_fired": GUARANTEES["fires_no_automation"],
        },
        "not_derived_here": {
            "webform_trigger_and_field_mapping": "WF-051 owns the Trigger node and Data Field mapping.",
            "booking_the_slot": "The scheduling workflow owns the second call and the calendar.",
            "crm_integration": "No CRM is read. The caller passes CRM values in the body.",
            "round_robin_assignment": "Not this workflow: no session may be consumed, so no "
            "individual is selected from a team.",
        },
        "vocabulary": published_vocabulary(),
    }
