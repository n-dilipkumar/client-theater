"""WF-109: every judgement call this workflow rests on, and the alternative rejected.

The specification fixes the two triggers, the four SLA targets, the lead-time rule, the
three cancellation conditions and the Data Connector execution shape. It leaves the
mechanism, the lead-time enforcement point, what a missed pre-warning window means, what
"escalates in priority" means, the already-fired rule and the plan/object-kind gate open.
Those gaps are decisions.

They are collected here, each with the alternative this build rejected and what would have
gone wrong, so a reviewer reads the list instead of reconstructing it from a diff. Every
entry that had to be derived without a source says so rather than papering over it.

Jev status
----------

The load-bearing architecture decision for this workflow -- where the escalation domain
lives and how the roughly-once-a-minute SLA check is performed with no worker process -- was
put to Jev as a ``choose_approach`` gate over three legitimate options, exactly as WF-108's
mechanism question was. It selected the new package plus an explicit evaluate route at
confidence 1.00 (audit ``jev-20261013T202514-3192-14675``), which is what this build
shipped. The complete decision, with the alternatives it rejected, is in the branch audit
file ``orchestration/decisions/wf-109-jev-audit.jsonl``, and the release bar that gated the
landing is the same file's second record.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-109"

#: The audits that settled the architecture questions.
JEV_DESIGN_AUDITS: dict[str, str] = {
    "domain_package_and_sweep_mechanism": "jev-20261013T202514-3192-14675",
}

#: A one-line statement of what the map above names, so a reader can tell a settled
#: decision from a pending one.
JEV_STATUS = (
    "The architecture question -- a new backend/dsr/sla_escalation/ package and an explicit "
    "evaluate route rather than a worker thread -- was put to Jev and selected "
    "new_package_explicit_route at confidence 1.00 (audit jev-20261013T202514-3192-14675). "
    "The pinned release bar that gated the landing is recorded in the same branch audit file."
)


def _decision(
    decision_id: str,
    question: str,
    *,
    evidence: str,
    chosen: str,
    rejected: str,
    consequence: str,
    unsourced: bool = False,
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "question": question,
        "evidence": evidence,
        "chosen": chosen,
        "rejected": rejected,
        "consequence": consequence,
        "unsourced": unsourced,
    }


#: Every decision, in the order a reader should meet them.
DECISIONS: list[dict[str, Any]] = [
    _decision(
        "domain-placement",
        "Where does WF-109's domain logic live, given WF-108 owns the SLA timer and WF-107 "
        "owns the conversation?",
        evidence=(
            "backend/dsr/sla_timers/ (WF-108, merged) owns the four SLA metrics, the live "
            "timer projection and the office-hours-aware deadline. "
            "backend/dsr/conversation_chase/ (WF-107, merged) owns the conversation states, "
            "the conversation and message-part records and the office-hours calendar. The "
            "brief instructs: read both and reuse their policy, timer and conversation "
            "vocabulary instead of re-declaring it. Nothing owns a workflow that watches a "
            "timer."
        ),
        chosen=(
            "A new package backend/dsr/sla_escalation/ holding vocabulary.py, rules.py, "
            "inferences.py and engine.py. It imports dsr.sla_timers for the metrics and the "
            "timer projection and dsr.conversation_chase for the conversation vocabulary, "
            "and adds only the escalation-specific terms. Put to Jev as audit "
            "jev-20261013T202514-3192-14675, which selected it (new_package_explicit_route) "
            "at confidence 1.00."
        ),
        rejected=(
            "Declaring a second copy of the four metrics and the conversation states inside "
            "sla_escalation, which would give the product two answers to what 'frt' or "
            "'snoozed' means. Also rejected: adding the escalation modules to sla_timers, "
            "which couples two tickets in one folder and makes the escalation domain "
            "unimportable without the timer workflow."
        ),
        consequence=(
            "One metric vocabulary, one conversation vocabulary, and a new package that "
            "cannot conflict with a branch working another domain."
        ),
    ),
    _decision(
        "timer-mechanism",
        "How is a minute-resolution SLA check performed in a product with no worker process?",
        evidence=(
            "'Intercom checks SLAs about once a minute, so a Workflow runs within roughly a "
            "minute of the target time, rather than to the exact second.' AGENTS.md requires "
            "all state to live in records.data and the product has no worker, scheduler or "
            "thread pool. WF-108 settled the same question for its timer with an explicit "
            "route (POST /rooms/{id}/timers/evaluate) at confidence 1.00 (audit "
            "jev-20261013T171356-7564-36737), because a thread needs a clock no test can "
            "move, writes audit rows naming no route, and races under pytest-xdist."
        ),
        chosen=(
            "POST /api/wf-109/rooms/{room_id}/escalations/evaluate is the sweep. It reads "
            "WF-108's live timer projection at an instant the caller passes, decides each "
            "live workflow, and writes the runs. The decision is also a pure function a test "
            "can call with a frozen clock. Put to Jev as audit jev-20261013T202514-3192-14675, "
            "which selected it together with the new package at confidence 1.00."
        ),
        rejected=(
            "A background thread or asyncio task ticking every minute. It matches the "
            "one-minute sentence most literally, but it needs a wall clock no test can "
            "freeze, writes audit rows naming no route the app serves, and races across the "
            "pytest-xdist workers CI uses."
        ),
        consequence=(
            "The check is testable and its audit rows name the evaluate route. Nothing is due "
            "until somebody calls evaluate; the response carries the counts and the per-target "
            "decisions so the gap is visible rather than silent."
        ),
    ),
    _decision(
        "lead-time-enforcement",
        "The spec says the lead time must be shorter than the target, but does not say whether "
        "the room refuses the workflow or only warns. Which, and where is it checked?",
        evidence=(
            "'For the Before SLA breach trigger, the lead time must be shorter than the SLA "
            "target's duration, or the Workflow won't run.' The flow's step 3 chooses a target "
            "name (FRT/NRT/TTC/TTR), not a policy: one workflow watches a metric across many "
            "conversations, whose policies may carry different target durations."
        ),
        chosen=(
            "Refuse at save where the target is knowable, and record a skip where it is only "
            "knowable per conversation. A before-breach workflow that names its policy_id is "
            "checked against that policy's target for the watched metric and refused with 422 "
            "if the lead is not shorter. A workflow that names no policy is checked per "
            "conversation at fire time and skips with the published code lead_time_not_shorter."
        ),
        rejected=(
            "A warning only, which would let a workflow that can never run be saved live. Also "
            "rejected: requiring every before-breach workflow to name a policy, which the flow "
            "does not ask for and which would stop one workflow covering a metric across a "
            "room's policies."
        ),
        consequence=(
            "A misconfigured workflow is refused where the target is known and cannot fire "
            "silently where it is not; the skip is a published code the page shows."
        ),
        unsourced=True,
    ),
    _decision(
        "cancellation-model",
        "'If a teammate replies, snoozes, or closes the conversation before the trigger point, "
        "the Workflow doesn't run.' How are the three implemented?",
        evidence=(
            "The three conditions are named. WF-107 records the conversation state (open, "
            "snoozed, closed) and the message parts, each with an author_kind and an instant. "
            "The watched target's anchor and the trigger instant are both known to the rules."
        ),
        chosen=(
            "cancel_reason() names all three. A closed conversation is conversation_closed, a "
            "snoozed one is conversation_snoozed, and a teammate message at or after the "
            "watched target's anchor and at or before the trigger point is teammate_replied. "
            "The window opens at the anchor, so a reply that predates the target does not "
            "suppress a warning about it."
        ),
        rejected=(
            "Pausing the workflow instead of cancelling it, which the sentence does not say. "
            "Also rejected: treating a teammate reply as a cancellation for any target and any "
            "window, which would let a reply from before the target suppress a warning the "
            "seller still needs."
        ),
        consequence=(
            "Every suppressed run carries one of the three named reasons, so a reviewer can "
            "check the rule the spec states rather than infer it from a missing run."
        ),
    ),
    _decision(
        "head-start-missed",
        "The check is roughly a minute, so a sweep can arrive after the target has already "
        "breached. What does the before-breach workflow do then?",
        evidence=(
            "One-minute tolerance: 'a Workflow runs within roughly a minute of the target time, "
            "rather than to the exact second.' The before-breach trigger 'runs a chosen amount "
            "of time before an SLA target is due to breach', and the on-breach trigger 'runs the "
            "moment an SLA target is missed'."
        ),
        chosen=(
            "A before-breach window the sweep missed is recorded as head_start_missed and does "
            "not fire. A pre-warning after the breach is not a pre-warning. The breach itself is "
            "the on-breach trigger's job, and a room that wants both configures both."
        ),
        rejected=(
            "Firing the before-breach workflow late, once the target had already breached, which "
            "would send a 'due in 10 minutes' warning about a target already missed. Also "
            "rejected: silently dropping the missed window, which is exactly the suppression the "
            "published skip code exists to make visible."
        ),
        consequence=(
            "The pre-warning is honest about what it observed, and the on-breach trigger remains "
            "the one path that fires at a breach."
        ),
        unsourced=True,
    ),
    _decision(
        "escalation-meaning",
        "'the inbox conversation is escalated in priority.' What does escalating mean here?",
        evidence=(
            "The data flow ends 'the inbox conversation is escalated in priority'. The flow's "
            "step 5 names the actions that mutate the conversation: assign, notify, tag, note. "
            "WF-107 stores a priority flag and its inbox already sorts by one."
        ),
        chosen=(
            "Every firing sets the conversation's priority flag, records an escalation level "
            "(pre_warning for before-breach, breach for on-breach) and a rank, and stamps "
            "escalated_at. The escalated inbox sorts by level rank first, then by the nearest "
            "SLA deadline, so a breached conversation outranks a warned one."
        ),
        rejected=(
            "Escalating by tag colour alone, which the design system forbids (no status by "
            "colour alone) and which no sort can read. Also rejected: a single priority flag "
            "with no level, which would make a pre-warning indistinguishable from a breach in "
            "the inbox."
        ),
        consequence=(
            "A pre-warning and a breach are two visible ranks, and the page shows each with its "
            "words rather than its colour."
        ),
        unsourced=True,
    ),
    _decision(
        "alert-as-record",
        "The Slack alert is an outbound HTTP call. How is it modelled in a product that makes "
        "no outbound call?",
        evidence=(
            "The spec routes the alert through a Data Connector and reports the outcome on "
            "data_connector.execution.completed with source_type 'workflow', success, "
            "http_status and error_type; the quoted failure is success false, http_status 400, "
            "error_type request_configuration_error. This product holds no connector credential, "
            "as WF-107 recorded for its message blocks."
        ),
        chosen=(
            "The alert is a record in wf109_alert_execution, carrying exactly those fields. Its "
            "outcome is derived from the action's own configuration: an alert with no "
            "connector_id or url is the quoted request_configuration_error at 400, a fully "
            "configured alert succeeds at 200, and a caller may state simulate_http_status to "
            "exercise a failure. Every execution carries sent_by_this_product: False."
        ),
        rejected=(
            "A log line, which the issue explicitly rejects and which the page could not show as "
            "a failed alert. Also rejected: claiming the alert was delivered, which this product "
            "cannot know without a credential."
        ),
        consequence=(
            "A failed alert is a row the page renders, the failure shape matches the researched "
            "webhook, and the honesty flag says the product did not send it."
        ),
        unsourced=False,
    ),
    _decision(
        "once-per-target",
        "A sweep may run many times. What stops one workflow firing repeatedly for one target on "
        "one conversation?",
        evidence=(
            "The flow sets a workflow live and the sweep runs on a clock, so the same fired "
            "workflow would be reconsidered on every sweep. WF-107 solves the analogous problem "
            "for its triggers with a per-message token and the rule 'can only trigger once per "
            "customer message'."
        ),
        chosen=(
            "A run is written for each firing, and the engine treats a live run for the same "
            "(workflow, conversation, metric) as already fired. The sweep returns "
            "already_fired_for_metric rather than firing again. The key is the metric, so a "
            "workflow watching first response time and one watching next response time are "
            "independent."
        ),
        rejected=(
            "A per-conversation key with no metric, which would stop the next-reply warning "
            "after the first-reply warning had fired. Also rejected: no guard at all, which "
            "would escalate the same conversation on every sweep."
        ),
        consequence=(
            "One escalation per watched target per conversation, enforced by the same sweep that "
            "writes the run."
        ),
    ),
    _decision(
        "scope-and-plan",
        "The research gates the triggers by plan and by object kind. How is that recorded?",
        evidence=(
            "'SLA Workflow triggers are available on the Expert plan and are currently in beta.' "
            "And: 'The SLA-based Workflow trigger is not supported for Back-office Tickets or "
            "Tracker Tickets.' This product's conversations carry one object kind, and the SLA "
            "timers WF-108 owns are conversation or ticket shaped."
        ),
        chosen=(
            "The workflow operates on the conversations WF-108's timer belongs to. The two "
            "gates are quoted in the vocabulary and shown on the page rather than hidden, and no "
            "back-office or tracker object kind is invented to be refused."
        ),
        rejected=(
            "Inventing a back-office ticket kind so it could be refused, which adds a domain "
            "object no other workflow has and no page can show. Also rejected: omitting the plan "
            "and object-kind notes, which would present a beta, gated trigger as generally "
            "available."
        ),
        consequence=(
            "The beta and object-kind limits are visible to a reviewer, and no unused object "
            "kind is added to the product."
        ),
        unsourced=False,
    ),
]


def inferences_report() -> dict[str, Any]:
    """The whole register, as the page renders it."""

    return {
        "ticket": TICKET,
        "decisions": list(DECISIONS),
        "jev_audits": dict(JEV_DESIGN_AUDITS),
        "jev_status": JEV_STATUS,
        "unsourced": [entry["id"] for entry in DECISIONS if entry["unsourced"]],
        "count": len(DECISIONS),
    }


def decision_by_id(decision_id: str) -> dict[str, Any] | None:
    for entry in DECISIONS:
        if entry["id"] == decision_id:
            return entry
    return None
