"""Every judgement call WF-110 makes, named and served so a reviewer can disagree.

The research is precise about the two triggers, the four actions, the two
documented limits, the tracker exclusion and the two events. It is silent about
several things, and the issue names four decisions it leaves open. Those gaps are
product behaviour rather than comments, so they are collected here and served at
``GET /api/wf-110/inferences``.

Each entry carries ``decision``, ``why`` and ``change_if``. A reviewer who
disagrees has a named thing to disagree with and a stated consequence.

The first four entries are the four decisions the issue itself names. The rest
are this build's own.
"""

from __future__ import annotations

from typing import Any

#: The four decisions the issue names.
DERIVED_ACTIONS_ARE_BACKGROUND_ONLY = "inference_actions_are_background_only"
DERIVED_SECOND_WORKFLOW_IS_SHIPPED = "inference_second_workflow_is_shipped"
DERIVED_CONVERSION_GATE_IS_A_PRECONDITION = "inference_conversion_gate_is_a_precondition"
DERIVED_TRACKER_EXCLUSION_IS_A_SKIP = "inference_tracker_exclusion_is_a_skip"
DERIVED_RESOLVED_AND_CLOSED_ARE_TWO_EVENTS = "inference_resolved_and_closed_are_two_events"
#: This build's own.
DERIVED_CLOSED_PAYLOAD_IS_NESTED = "inference_closed_payload_is_nested"
DERIVED_VERSION_BOUNDARY_IS_215 = "inference_version_boundary_is_215"
DERIVED_SET_CDA_NOT_MODELLED = "inference_set_cda_not_modelled"
DERIVED_NOTIFICATION_IS_RECORDED_NOT_DELIVERED = "inference_notification_recorded_not_delivered"
DERIVED_SLA_TIMER_IS_LOCAL = "inference_sla_timer_is_local"
DERIVED_ACTIONS_RUN_IN_DECLARED_ORDER = "inference_actions_run_in_declared_order"

#: The Jev decision that chose the model this build implements.
JEV_MODEL_DECISION = "jev-20261013T171439-13884-79783"

#: The recorded value of that decision.
JEV_MODEL_VERDICT = "accept"
JEV_MODEL_CONFIDENCE = 0.80


def _entry(
    entry_id: str,
    topic: str,
    question: str,
    decision: str,
    why: str,
    change_if: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "topic": topic,
        "question": question,
        "decision": decision,
        "why": why,
        "change_if": change_if,
        **extra,
    }


INFERENCES: tuple[dict[str, Any], ...] = (
    _entry(
        DERIVED_ACTIONS_ARE_BACKGROUND_ONLY,
        "the action set",
        "The research states 'Only background actions are available under this trigger'. "
        "What is the synchronous action set?",
        "There is no synchronous action set. Every action this build models - notify the "
        "owning team, assign the team, apply an SLA, tag the linked conversation - runs in "
        "the background and asks a seller to do nothing. BACKGROUND_ACTIONS is the whole "
        "action set, and the module publishes that reading on GET /api/wf-110/vocabulary.",
        "The researched sentence is a limit, not a grant: it names what is unavailable, so "
        "the safe reading is that nothing is available synchronously. Inventing a "
        "synchronous action would contradict the evidence, and a workflow that fires "
        "without a seller present is the entire point of an automation trigger.",
        "Add a synchronous action kind and a route a seller calls by hand, then record "
        "which actions are synchronous in vocabulary.BACKGROUND_ACTIONS.",
    ),
    _entry(
        DERIVED_SECOND_WORKFLOW_IS_SHIPPED,
        "the second trigger",
        "Step 4 says 'Optionally add a second Workflow on \"Teammate changes the state of a "
        "ticket\"'. Is the second workflow built?",
        "Yes. TRIGGER_STATE_CHANGED is a first-class trigger kind. A teammate changing a "
        "ticket's state fires it, and its actions run exactly as the ticket-created "
        "trigger's do. Selected by Jev at decision " + JEV_MODEL_DECISION + ".",
        "The workflow's own summary in the issue requires the owning team to be notified "
        "when the ticket is created and when its state changes. Without the second trigger "
        "the state change is silent, and the requirement is unmet. The research calls it "
        "optional; the requirement does not.",
        "Delete the ticket_state_changed trigger kind and stop evaluating it in "
        "engine.change_state. The state webhooks still fire.",
    ),
    _entry(
        DERIVED_CONVERSION_GATE_IS_A_PRECONDITION,
        "the conversion gate",
        "The research states 'Tag conversation, Apply SLA, and Set CDA actions will only "
        "work if the ticket acted upon was converted from a conversation'. Is the gate an "
        "error or a skip?",
        "A skip. A matched trigger runs every action it can. An action the gate blocks "
        "records the outcome skipped_not_converted and the evaluation continues. The "
        "converted and the unconverted case are tested separately.",
        "The gate names three actions that 'will only work if', which is a precondition on "
        "those actions, not on the trigger. Failing the whole evaluation would suppress the "
        "notify and assign actions that are not gated, so a converted-only feature would "
        "silently stop escalating unconverted tickets.",
        "Move the gate check into rules.trigger_matches so an unconverted ticket skips the "
        "whole trigger rather than one action.",
    ),
    _entry(
        DERIVED_TRACKER_EXCLUSION_IS_A_SKIP,
        "the tracker exclusion",
        "The research states 'the workflow will not trigger from tracker ticket creation'. "
        "Where is the exclusion enforced?",
        "In rules.trigger_matches, and only for the ticket_created trigger. The exclusion is "
        "checked before the conditions, so a tracker ticket that matches the type is still "
        "skipped, and the evaluation records the published code tracker_created_ticket. The "
        "ticket is created; only the trigger is excluded.",
        "The sentence is about the event rather than the ticket's fields. Checking it after "
        "the conditions would let a condition ordering decide the exclusion, and a tracker "
        "ticket that happened to match would fire - which is exactly what the sentence "
        "forbids.",
        "Remove the created_via == tracker branch from rules.trigger_matches and the "
        "tracker_created_ticket skip code from the vocabulary.",
    ),
    _entry(
        DERIVED_RESOLVED_AND_CLOSED_ARE_TWO_EVENTS,
        "the two events",
        "The research distinguishes ticket.resolved, 'a state-based event', from "
        "ticket.closed, which 'is separate from state transitions'. Are they one state or "
        "two?",
        "Two. 'resolved' and 'closed' are two states in TICKET_STATES. A transition into "
        "'resolved' emits ticket.resolved. 'ticket.closed' is emitted by a separate route, "
        "POST /tickets/{id}/close_conversation, which closes the linked conversation and "
        "does not change the ticket's state.",
        "Merging them would lose the distinction the research draws explicitly and would "
        "make a closed conversation and a resolved ticket indistinguishable to a downstream "
        "subscriber. The two payload shapes differ as well, which is the topic of the next "
        "entry.",
        "Collapse 'closed' into 'resolved' in vocabulary.TICKET_STATES and emit "
        "ticket.closed from engine.change_state.",
    ),
    _entry(
        DERIVED_CLOSED_PAYLOAD_IS_NESTED,
        "the payload shape",
        "The research states 'the Ticket is nested under a ticket field on data.item' for "
        "ticket.closed, and 'Note the ticket.closed vs ticket.resolved payload-shape "
        "difference in v2.15+'. How does one reader handle both?",
        "One builder, two shapes, one reader. rules.webhook_payload nests the ticket under "
        "'ticket' for ticket.closed and places the fields directly on data.item for every "
        "other topic. rules.read_webhook_ticket reads both and is tested against both.",
        "A reader written for one shape returns an empty dict for the other, which looks "
        "like a webhook with no ticket rather than a parsing bug. The research names the "
        "difference, so ignoring it would ship the bug the research warns about.",
        "Return one shape for every topic in rules.webhook_payload and delete the "
        "NESTED_TICKET_TOPICS set.",
    ),
    _entry(
        DERIVED_VERSION_BOUNDARY_IS_215,
        "the version boundary",
        "The research names a payload-shape difference 'in v2.15+' but dates no boundary. "
        "Which version does this build use?",
        "vocabulary.API_VERSION is '2.15', and rules.uses_nested_shape compares a parsed "
        "version against it. The boundary is named and not dated, so the default is the "
        "named version and every payload records the api_version it was built for.",
        "Silently picking a version would make the shape difference untestable and confuse "
        "a subscriber on either side of the boundary. Recording the version on the row "
        "means a later date for the boundary is a data change rather than a code change.",
        "Change vocabulary.API_VERSION and the comparison in rules.uses_nested_shape.",
    ),
    _entry(
        DERIVED_SET_CDA_NOT_MODELLED,
        "the set-CDA action",
        "The conversion gate names 'Set CDA' beside 'Tag conversation' and 'Apply SLA'. Is "
        "set-CDA modelled?",
        "No. 'Tag conversation and Set CDA are not available on ticket triggers, but can be "
        "accessed through reusable bots.' This build models the two actions a ticket "
        "trigger does support and omits set-CDA, which the research says needs a reusable "
        "bot. SET_CDA_ACTION names the omission in the vocabulary.",
        "Modelling set-CDA without a reusable-bot surface would put an action in the "
        "configuration that can never run, and the researched sentence says why. Naming the "
        "omission is better than an action that silently does nothing.",
        "Add a set_cda action kind and a reusable-bot surface; the gate in "
        "CONVERSION_REQUIRED_ACTIONS already lists where it belongs.",
    ),
    _entry(
        DERIVED_NOTIFICATION_IS_RECORDED_NOT_DELIVERED,
        "the Slack notification",
        "The research names 'the Slack app in Intercom' as the delivery path. Does this "
        "build send the Slack message?",
        "No. An action that notifies the owning team writes a wf110_notification row in the "
        "queued state, with delivered_by_this_product False. WF-112 owns Slack delivery and "
        "may not be present, so a fake delivery would be worse than an honest recording.",
        "A delivery needs a Slack credential this product does not hold, and a notification "
        "the page claims was sent but was not is a worse failure than one visibly queued. A "
        "ticket created with no notification row at all is the non-success state the seed "
        "prints.",
        "Add a delivery step that reads the queued notification and posts to Slack, then "
        "record the delivered state on the row.",
    ),
    _entry(
        DERIVED_SLA_TIMER_IS_LOCAL,
        "the SLA",
        "WF-108 owns SLA timers and may merge later. Does this workflow depend on it?",
        "No. An SLA policy here is a name, a metric and a target in hours. The apply action "
        "computes a due instant from the engine's own clock and stores it on the ticket. "
        "Nothing in this feature imports WF-108 and nothing waits for it.",
        "The issue says WF-108 may merge while this build runs and the workflow must never "
        "depend on it being present. A local due instant answers 'when does this ticket "
        "breach', which is the only thing the apply action needs to record.",
        "Read the due instant from WF-108's timer surface once it exists, and keep the "
        "stored policy name as the join key.",
    ),
    _entry(
        DERIVED_ACTIONS_RUN_IN_DECLARED_ORDER,
        "action order",
        "The research lists the actions as 'notify the responsible team in Slack, change the "
        "assigned team, apply an SLA, tag the linked conversation'. In what order do they "
        "run?",
        "In the order the trigger declares them. A trigger that wants the new team notified "
        "declares assign_team before notify_team; the seed does exactly that. A trigger that "
        "declares notify_team alone notifies the ticket's current team, or skips when it has "
        "none.",
        "The research lists the actions in prose rather than as a sequence, and the list "
        "puts notify before the team change. Running a fixed order would decide for the "
        "seller which team is notified, which is the one thing the configuration is for.",
        "Sort actions into a fixed order inside engine._fire instead of using the declared list.",
    ),
)

INFERENCE_IDS: tuple[str, ...] = tuple(str(entry["id"]) for entry in INFERENCES)


def describe() -> dict[str, Any]:
    """The whole registry, as served."""

    return {
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "ids": list(INFERENCE_IDS),
        "jev": {
            "audit_id": JEV_MODEL_DECISION,
            "verdict": JEV_MODEL_VERDICT,
            "confidence": JEV_MODEL_CONFIDENCE,
        },
        "note": (
            "Each entry names a judgement call this build made rather than a behaviour the "
            "research stated. A reviewer who disagrees with one has the change to make named."
        ),
    }


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One entry, or ``None``."""

    for entry in INFERENCES:
        if entry["id"] == entry_id:
            return dict(entry)
    return None
