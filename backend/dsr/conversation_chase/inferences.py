"""WF-107: every judgement call this workflow rests on, and the alternative rejected.

The specification fixes the two triggers, the duration bounds, the step blocks, the
tag, the inbox assignment and the precedence rule. It says almost nothing about the
mechanism, the origin flag, the office-hours model, or which timestamp each trigger
measures.

Those gaps are decisions. They are collected here, each with the alternative this build
rejected and what would have gone wrong, so a reviewer reads the list instead of
reconstructing it from a diff. Every entry names the evidence it was taken from, and
where the specification says nothing, that is stated rather than papered over.

All four architecture questions were put to Jev before implementation, and the audit ids
are recorded against the entries they settled.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-107"

#: The four architecture questions settled with Jev before implementation.
#:
#: Recorded here rather than only in the audit log so a reader of the inferences
#: register sees that the mechanism choices were gated rather than picked.
JEV_DESIGN_AUDITS = {
    "domain_package_placement": "jev-20261005T125556-19056-56859",
    "job_driver": "jev-20261005T125557-19056-57169",
    "api_created_conversation": "jev-20261005T125618-8304-78829",
    "trigger_anchor": "jev-20261005T125619-8304-79111",
    "design_doc": "jev-20261005T125934-9776-74784",
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
        "Where does WF-107's domain logic live, given no existing package owns "
        "conversations or inboxes?",
        evidence=(
            "docs/FEATURE-CONTRACT.md says a workflow ships as a plugin under a domain "
            "package. backend/dsr/reassign/ exists and is the nearest neighbour by name, "
            "but it is WF-063 meeting reassignment: its vocabulary is hosts, meetings, "
            "meeting types and workspaces, and none of those nouns appears in this "
            "workflow. backend/dsr/signals/ holds intent and engagement signals, which "
            "this workflow reads but does not produce. Nothing in backend/dsr/ mentions "
            "a conversation, an inbox, office hours or a teammate as a collection."
        ),
        chosen=(
            "A new package backend/dsr/conversation_chase/ holding vocabulary.py, "
            "rules.py, inferences.py and engine.py. Four additive files, and the "
            "package __init__.py is created with them rather than appended to."
        ),
        rejected=(
            "Adding four modules to backend/dsr/reassign/, which would put conversation "
            "vocabulary in a package named for meeting reassignment and make two "
            "modules both called rules.py hold two unrelated sets of nouns. Also "
            "rejected: editing an existing package's __init__.py, which is on main and "
            "is the first line a parallel branch working that package would touch, so it "
            "converts an additive diff into a one-line context conflict."
        ),
        consequence=(
            "One domain, one package, four files that cannot conflict with a branch "
            "working another domain. Python imports a submodule on demand, so the "
            "modules are reachable as dsr.conversation_chase.engine without the host "
            "knowing. Put to Jev as audit jev-20261005T125556-19056-56859, which selected "
            "it at confidence 0.99 with a margin of 1.00 over the runner-up."
        ),
    ),
    _decision(
        "job-driver",
        "What drives the two inactivity triggers and the Wait/Snooze timer, given the "
        "research calls them timers?",
        evidence=(
            "The automations section says: 'Two purely time-based, automatic triggers "
            "(no seller action)' and the data flow says the Wait/Snooze 'timer runs and "
            "can be interrupted by a new customer/teammate message'. This product runs "
            "no worker and no scheduler, and docs/FEATURE-CONTRACT.md requires that "
            "'every write's audit row names a route the app actually serves'."
        ),
        chosen=(
            "Three explicit POST routes. /evaluate sweeps the room's live triggers "
            "against its open conversations and writes runs; /runs/{id}/advance writes "
            "the next step or starts the wait; /runs/{id}/resolve finishes after the "
            "wait or closes it early. Time moves because a caller asks."
        ),
        rejected=(
            "A background thread started at import time. It matches the research's word "
            "'timer' most literally, and it fails three project rules at once: it needs "
            "a clock no test can move, it writes audit rows naming no route, and under "
            "pytest-xdist it would race across workers for the same rows. Also rejected: "
            "deriving the state on every read, which reports a state the store does not "
            "hold and cannot write the 'Conversation closed' activity a GET must not "
            "produce."
        ),
        consequence=(
            "Nothing fires until somebody calls /evaluate, and no wait resolves until "
            "somebody resolves it. The page reports a visible due count so the gap is "
            "legible rather than silent, and every boundary is reachable in a test by "
            "moving a clock. Put to Jev as audit jev-20261005T125557-19056-57169, which "
            "selected it at confidence 1.00 with a margin of 1.00."
        ),
    ),
    _decision(
        "api-created-conversation",
        "The research says 'This workflow won't trigger for conversations created via "
        "our REST API.' How is an API-created conversation represented under that rule?",
        evidence=(
            "That sentence is quoted in the spec's evidence list with nothing further. "
            "The same spec names POST /messages with "
            "create_conversation_without_contact_reply as the way an external service "
            "'replays the same behaviour', so this product does have API-created "
            "conversations and has to represent them somewhere."
        ),
        chosen=(
            "An explicit origin flag on every conversation, 'inbox' or 'api'. The sweep "
            "reads it and reports an api-origin conversation as skipped with the "
            "published code api_created_conversation. The skip is a row on the page and "
            "a case in a test."
        ),
        rejected=(
            "Never creating API-origin conversations in this workflow's data, which "
            "makes the rule unfalsifiable: there is nothing to trigger on, so a reviewer "
            "cannot see the rule held or violated. Also rejected: honouring API-origin "
            "conversations like any other, which drops a requirement the spec states."
        ),
        consequence=(
            "The exception is visible. The conversation is still fully readable and "
            "writable: the rule scopes triggers, not the record, and an exemption that "
            "hid the conversation would be a different rule from the one quoted. Put to "
            "Jev as audit jev-20261005T125618-8304-78829, which selected it at "
            "confidence 1.00."
        ),
    ),
    _decision(
        "trigger-anchor",
        "Which conversation timestamp does each trigger measure its inactivity from?",
        evidence=(
            "The research gives two anchors in two places. The customer trigger's data "
            "flow is 'Last-message timestamp on the Conversation object -> inactivity "
            "elapsed -> trigger fires', and its timer is '10 minutes after there's been "
            "no response from the customer'. The teammate trigger is different: 'is "
            "evaluated against customer's first message. This means if the customer "
            "sends 3 messages in a row, the timer will be set against their first "
            "message, not last.'"
        ),
        chosen=(
            "customer_idle anchors to the last message of any kind. teammate_idle "
            "anchors to the first customer message. rules.anchor_for is the single place "
            "that mapping lives, and the anchor that was used is recorded on every "
            "evaluation."
        ),
        rejected=(
            "Anchoring both triggers to the first message, for symmetry: it would reset "
            "the customer-idle clock on nothing, so a buyer could keep a conversation "
            "alive indefinitely by never letting the last message be theirs. Anchoring "
            "both to the last message: it would never fire for a buyer who sends three "
            "messages in a row, which is the exact case the quote describes."
        ),
        consequence=(
            "Three messages in a row keep the teammate timer on the first, and reset the "
            "customer timer to the third. Both behaviours are tests. Put to Jev as audit "
            "jev-20261005T125619-8304-79111, which selected it at confidence 1.00."
        ),
    ),
    _decision(
        "office-hours-model",
        "The spec names office-hours configuration as a data source and sources no "
        "office-hours model. What model is derived?",
        evidence=(
            "The data sources list says 'Office hours configuration'. Step 6 says the "
            "Show expected reply time step 'uses office hours'. Nothing in the four "
            "cited sources defines the shape. The research corpus's own worked example "
            "for the neighbouring workflow is 'if your office hours are set to 9am - 6pm, "
            "and your SLA first response time is 15 minutes, a message received at 5:50pm "
            "will have an expected response time of 9:05am on the next working day'."
        ),
        chosen=(
            "A weekly schedule: seven named days, each either closed or a pair of "
            "open/close minutes past midnight. The derived default is Monday to Friday "
            "09:00 to 18:00, taken from that worked example rather than from a "
            "convention, because nine-to-five would contradict the sentence. "
            "expected_reply_time adds the duration and walks forward until the result "
            "falls inside the schedule."
        ),
        rejected=(
            "A flat offset with no schedule, which would make 'uses office hours' "
            "meaningless and would read 18:05 for a 17:50 message. Also rejected: a "
            "per-timezone database, because this product has no timezone database, so an "
            "instant that is right in one zone would be silently wrong in another."
        ),
        consequence=(
            "This is the one place the build is explicitly unsourced as a shape, and it "
            "is labelled so in this register rather than presented as a researched "
            "requirement. A schedule is stored per room and is editable through the "
            "office-hours route, so a team whose hours differ replaces the default "
            "without a code change. No test carries a fixed date: every worked case "
            "derives its weekday from a clock the test controls.",
        ),
        unsourced=True,
    ),
    _decision(
        "default-create-without-contact-reply",
        "The spec says create_conversation_without_contact_reply 'Defaults to false if "
        "not provided.' Where is that default made explicit?",
        evidence=(
            "The messages API reference gives the parameter and its false default. "
            "AGENTS.md and docs/FEATURE-CONTRACT.md both require the build to state its "
            "defaults rather than inherit a caller's behaviour."
        ),
        chosen=(
            "vocabulary.CREATE_WITHOUT_CONTACT_REPLY_DEFAULT = False, published in "
            "/vocabulary, and every path that opens a conversation for a message without "
            "a contact reply passes the value explicitly."
        ),
        rejected=(
            "Reading the flag off the payload and defaulting at the point of use, which "
            "makes the default a property of whichever route happened to read it and "
            "lets the two differ silently."
        ),
        consequence=(
            "The default is a named constant a test can assert on and the page can show "
            "in the vocabulary section, not a behaviour that differs between two routes."
        ),
    ),
    _decision(
        "interruption-is-terminal",
        "Does a cancelled wait resume the run, or end it?",
        evidence=(
            "The research says the Wait block's 'interruption events cancel the wait' "
            "and the data flow says a wait 'can be interrupted by a new customer/teammate "
            "message'. Nowhere does it say the run continues afterwards."
        ),
        chosen=(
            "An interrupted run is finished. There is no resume step kind and no route "
            "that moves a run out of the interrupted state."
        ),
        rejected=(
            "Treating an interruption as a pause that the run continues from after, "
            "which would mean the workflow closes a conversation the buyer had just "
            "answered. A buyer who replies during the window has ended the need for a "
            "closing message and a Close action."
        ),
        consequence=(
            "vocabulary.INTERRUPTED_IS_TERMINAL names the choice and RUN_INTERRUPTED is "
            "in CLOSED_RUN_STATES, so advancing an interrupted run is a 409 rather than "
            "a silent no-op."
        ),
    ),
    _decision(
        "system-parts-do-not-interrupt",
        "Can the workflow's own message block cancel the wait it is waiting on?",
        evidence=(
            "Step 3 adds a message block and step 4 adds a Wait. The message is written "
            "before the wait starts, so under a naive reading the wait is always "
            "interrupted by the workflow's own first act."
        ),
        chosen=(
            "Only a customer or teammate part can cancel a wait. A part written by the "
            "workflow is author_kind 'system' and never interrupts, whatever the step "
            "listed. Separately, a part that arrived before the wait started does not "
            "interrupt it either."
        ),
        rejected=(
            "Letting any part cancel the wait, which would make every chase workflow stop "
            "the moment it wrote its own message. Comparing only the author kind and "
            "ignoring the wait's start instant, which would let a message from hours "
            "earlier retroactively cancel a wait that has just begun."
        ),
        consequence=(
            "Two independent guards, each a test: rules.interruption_cancels refuses a "
            "system author, and rules.first_interruption skips parts older than the "
            "wait's own start instant."
        ),
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
