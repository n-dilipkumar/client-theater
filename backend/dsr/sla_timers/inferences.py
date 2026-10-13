"""WF-108: every judgement call this workflow rests on, and the alternative rejected.

The specification fixes the four metrics, the badge thresholds, the office-hours worked
example and the one-active-SLA invariant. It leaves the mechanism, the pause model, the
default numbers, the "fixed" rate and the terminal-metric choice open. Those gaps are
decisions.

They are collected here, each with the alternative this build rejected and what would
have gone wrong, so a reviewer reads the list instead of reconstructing it from a diff.
Every entry that was put to Jev names the audit that settled it; every entry that had to
be derived without a source says so rather than papering over it.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-108"

#: The architecture question settled with Jev before implementation.
JEV_DESIGN_AUDITS = {
    "domain_package_and_timer_mechanism": "jev-20261013T171356-7564-36737",
}


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
        "domain-package-placement",
        "Where does WF-108's domain logic live, given WF-107 already owns conversations "
        "and office hours?",
        evidence=(
            "backend/dsr/conversation_chase/ (WF-107, merged) owns the conversation "
            "vocabulary (states open, snoozed, closed), the conversation and part records, "
            "and the office-hours arithmetic. The brief instructs: read it and reuse its "
            "conversation vocabulary and statuses instead of re-declaring them. Nothing in "
            "backend/dsr/ owns an SLA policy, an SLA timer or a report."
        ),
        chosen=(
            "A new package backend/dsr/sla_timers/ holding vocabulary.py, rules.py, "
            "inferences.py and engine.py, which imports dsr.conversation_chase for the "
            "conversation vocabulary and the office-hours rules and adds only the "
            "SLA-specific terms."
        ),
        rejected=(
            "A second copy of the conversation states and the office-hours walk inside "
            "sla_timers, which would give the product two answers to what 'snoozed' means "
            "and two implementations of the office-hours walk that could drift. Also "
            "rejected: adding the SLA modules to conversation_chase, which couples two "
            "tickets in one folder and makes the SLA domain unimportable without the chase "
            "workflow."
        ),
        consequence=(
            "One conversation vocabulary, one office-hours walk, and a new package that "
            "cannot conflict with a branch working another domain. Put to Jev as audit "
            "jev-20261013T171356-7564-36737, which selected it at confidence 1.00."
        ),
    ),
    _decision(
        "timer-mechanism",
        "How is a 'continuous background timer' evaluated in a product with no worker process?",
        evidence=(
            "The spec says: 'The SLA timer is a continuous background timer, not a discrete "
            "job.' AGENTS.md requires all state to live in records.data. There is no worker, "
            "scheduler or thread pool. WF-107 settled the same question for its time-based "
            "triggers with an explicit route (POST /rooms/{id}/evaluate) at confidence 1.00 "
            "(audit jev-20261005T125557-19056-57169), because a thread needs a clock no test "
            "can move, writes audit rows naming no route, and races under pytest-xdist."
        ),
        chosen=(
            "POST /api/wf-108/rooms/{room_id}/timers/evaluate recomputes every active timer, "
            "and the timer state is also a pure projection readable at any time through GET. "
            "A SlaEngine holds a clock a test can move."
        ),
        rejected=(
            "A background thread or asyncio task ticking every minute. It matches the "
            "sentence most literally, but it needs a wall clock no test can freeze, it "
            "writes audit rows naming no route the app serves, and it races across the "
            "pytest-xdist workers CI uses."
        ),
        consequence=(
            "The timer is testable and its audit rows name the evaluate route. Nothing is "
            "due until somebody calls evaluate; the response carries the counts so the gap "
            "is visible rather than silent."
        ),
    ),
    _decision(
        "snooze-versus-auto-unsnooze",
        "The pause rule and the auto-unsnooze rule contradict each other. How are they reconciled?",
        evidence=(
            "Two sentences from the same spec. The pause rule: 'pause the timer when the "
            "conversation is snoozed or waiting on customer.' The auto-unsnooze rule: 'If an "
            "SLA is missed while a conversation is snoozed, the conversation will be "
            "automatically unsnoozed and re-opened.' If the clock is paused while snoozed it "
            "cannot newly miss inside a pause, so the two as written cannot both fire."
        ),
        chosen=(
            "The pause applies to a timer that is not already overdue. A timer that is "
            "already breached at the moment its conversation is snoozed is reopened instead "
            "of paused: evaluate() unsnoozes the conversation and records the reopen."
        ),
        rejected=(
            "Reading the auto-unsnooze sentence as unreachable and dropping it, which loses "
            "the researched behaviour that a stale breach must not be hidden behind a "
            "snooze. Also rejected: letting the clock run while snoozed, which contradicts "
            "the pause rule the flow's step 4 tells the seller to set."
        ),
        consequence=(
            "Both statements hold: a paused timer cannot newly breach, and an already-broken "
            "SLA is surfaced by reopening the conversation. is_reopening_breach() is the one "
            "place this is decided."
        ),
    ),
    _decision(
        "default-targets",
        "The spec names no target numbers. What are the defaults?",
        evidence=(
            "The flow's step 3 names FRT, NRT, TTC and TTR without a number. The only number "
            "in the specification is the office-hours worked example's 'SLA first response "
            "time is 15 minutes'."
        ),
        chosen=(
            "FRT defaults to 15 minutes, the number the spec's own example uses. NRT is one "
            "hour, TTC eight hours (a working day on the derived Mon-Fri 09:00-18:00 "
            "schedule), and TTR two days. Every one is editable per policy and published in "
            "the vocabulary."
        ),
        rejected=(
            "A single flat default for all four metrics, which would tell a reader nothing "
            "about which metric is expected to be fast. Also rejected: leaving a target "
            "unset so the policy refuses to save, which makes the demo empty."
        ),
        consequence=(
            "The shipped default FRT is the number the spec itself uses; the other three are "
            "a stated derivation, and that is recorded here rather than presented as researched."
        ),
        unsourced=True,
    ),
    _decision(
        "fixed-rate",
        "The report aggregates 'hit/miss/fixed rates' but the spec never defines 'fixed'. "
        "What is a fixed outcome?",
        evidence=(
            "The spec names the three rates and no definition: 'aggregate hit/miss/fixed "
            "rates flow into the SLAs report'. A hit and a miss are clear from their names. "
            "'fixed' is only readable next to the auto-unsnooze rule, the one place the spec "
            "describes a breach being repaired."
        ),
        chosen=(
            "A hit met its target. A miss breached and stayed breached. A fixed breached, "
            "was reopened by the auto-unsnooze rule, and then completed. classify_outcome() "
            "is the only place that decides, and a metric still pending or paused has no "
            "outcome and is not counted."
        ),
        rejected=(
            "Treating every breach as a miss and leaving 'fixed' unused, which would make "
            "the report's third rate unreadable and would lose the distinction the reopen "
            "rule exists to create. Also rejected: counting a reopen itself as fixed without "
            "requiring completion, which would score a breach as repaired by merely being "
            "noticed."
        ),
        consequence=(
            "The report's three rates are readable and the reopen rule has an observable "
            "effect. A metric still running has no outcome, so the rates never divide by a "
            "partial set."
        ),
        unsourced=True,
    ),
    _decision(
        "terminal-metric-per-kind",
        "The spec gives both TTC and TTR and says TTR is 'for tickets'. This product has "
        "conversations only. Which applies?",
        evidence=(
            "The flow: 'set First Reply Time (FRT), Next Reply Time (NRT), Time to Close "
            "(TTC), and - for tickets - Time to Resolution (TTR).' WF-107 conversations "
            "carry one terminal state, closed."
        ),
        chosen=(
            "A policy declares its object kind. A conversation policy earns FRT, NRT and "
            "TTC; a ticket policy earns FRT, NRT and TTR. Both terminal metrics measure from "
            "the moment the SLA was applied to the conversation's closed_at, and the metric "
            "that does not apply is reported not_applicable rather than hidden."
        ),
        rejected=(
            "Computing both TTC and TTR for every conversation, which reports a metric the "
            "object kind does not earn and makes the report count four metrics where the flow "
            "names three."
        ),
        consequence=(
            "A conversation policy shows FRT, NRT and TTC; a ticket policy shows FRT, NRT and "
            "TTR. The metric that does not apply is present as not_applicable, so the rule is "
            "visible rather than an omission."
        ),
        unsourced=True,
    ),
    _decision(
        "pause-model",
        "How is 'waiting on customer' represented, given the conversation record has no "
        "such state?",
        evidence=(
            "WF-107's conversation states are open, snoozed and closed; there is no "
            "'waiting' state. The flow says to pause the timer 'when the conversation is "
            "snoozed or waiting on customer', and the conversation parts record who wrote "
            "each message."
        ),
        chosen=(
            "A waiting-on-customer window opens at a teammate message and closes at the next "
            "customer message. waiting_intervals() derives the windows from the parts, and "
            "the window that is still open stops the clock now. Snapping is read from the "
            "conversation's snoozed_at / unsnoozed_at fields."
        ),
        rejected=(
            "Adding a 'waiting' state to WF-107's conversation vocabulary, which edits "
            "another ticket's domain and is exactly the re-declaration the brief forbids. "
            "Also rejected: pausing on every conversation with a teammate as the last author, "
            "which is the same rule expressed as a moment rather than as a window and cannot "
            "subtract time already spent."
        ),
        consequence=(
            "The pause is a window, so time already spent waiting is subtracted and the timer "
            "resumes when the buyer answers. No other ticket's vocabulary is edited."
        ),
        unsourced=True,
    ),
    _decision(
        "one-active-sla",
        "Only one active SLA per conversation is a stated invariant. How is it enforced?",
        evidence=(
            "'Intercom supports only one active SLA per conversation, so when a Workflow "
            "applies a new SLA later, the existing SLA is automatically removed.'"
        ),
        chosen=(
            "apply() soft-deletes any existing active timer on the conversation inside one "
            "transaction with the new timer's insert, so a conversation can never be observed "
            "with two active timers. The removed timer is kept as history."
        ),
        rejected=(
            "Refusing to apply a second SLA with a 409, which is a strictly weaker reading: "
            "the sentence says the existing SLA is automatically removed, not that the new "
            "one is refused. Also rejected: leaving both timers active and letting a reader "
            "pick, which makes the badge ambiguous."
        ),
        consequence=(
            "A conversation always has at most one active timer, enforced in one transaction, "
            "and the replaced timer survives as history a report can still read."
        ),
        unsourced=False,
    ),
    _decision(
        "inbound-webhooks",
        "The spec's extensibility names inbound webhook topics. Does this feature serve one?",
        evidence=(
            "The extensibility names ticket.created, ticket.state.updated, ticket.resolved, "
            "ticket.closed, ticket.admin.snoozed, ticket.admin.unsnoozed and "
            "conversation.admin.snoozed. The product's inbound-webhook surface is WF-082, "
            "which verifies provider signatures; this feature owns none."
        ),
        chosen=(
            "The topic names are published in the vocabulary as the events the timer "
            "observes, and no inbound webhook route is built. A driver advances the timer "
            "through this feature's own evaluate route."
        ),
        rejected=(
            "Building a second inbound webhook receiver inside this feature, which "
            "re-implements WF-082's signature verification and gives the product two "
            "inbound surfaces with two ideas of what a verified webhook is."
        ),
        consequence=(
            "The topic names are discoverable and one inbound surface, WF-082's, stays the "
            "only one that verifies a provider signature."
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
        "unsourced": [entry["id"] for entry in DECISIONS if entry["unsourced"]],
        "count": len(DECISIONS),
    }


def decision_by_id(decision_id: str) -> dict[str, Any] | None:
    for entry in DECISIONS:
        if entry["id"] == decision_id:
            return entry
    return None
