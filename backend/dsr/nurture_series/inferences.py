"""Every judgement call WF-104 rests on, and the alternative each rejected.

The research is specific about the shape of a Series and silent about a number of things a
build has to decide anyway. This module collects those decisions so a reviewer reads them
rather than reconstructing them from a diff, and so a later change has something to argue
against. Each entry names the question, the choice, the rejected alternative, and the
consequence of the choice.

The decisions that were put to Jev carry the audit id that chose them.
"""

from __future__ import annotations

from typing import Any

#: The decisions made while building WF-104. The ``unsourced`` flag marks one the research
#: does not answer at all, which is every one of them here.
DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "audience-mechanism",
        "question": "How does the domain module obtain the contacts a rule block filters?",
        "chosen": (
            "The audience is caller-supplied: evaluate and preview accept the contact and "
            "company objects as input, read from the CRM the caller holds (WF-039). This "
            "feature stores no contact rows."
        ),
        "rejected": (
            "The feature keeps its own wf104_audience collection and duplicates the CRM "
            "contacts into it, or the domain module reads the WF-039 targets collection "
            "directly."
        ),
        "consequence": (
            "This feature is decoupled from the CRM's storage shape and re-stores nothing it "
            "does not own, at the cost of the caller having to fetch the audience before it "
            "calls evaluate."
        ),
        "jev_audit": "jev-20261013T150622-7908-82240",
        "confidence": 0.86,
        "unsourced": True,
    },
    {
        "id": "poll-interval",
        "question": "What is the evaluation clock?",
        "chosen": (
            "A 30-minute poll, named by the research: 'Series checks every 30 minutes whether a "
            "customer matches each rule, message, wait, or tag block.' The poll is an explicit "
            "POST route, run when a caller or a scheduler calls it."
        ),
        "rejected": (
            "A background thread that evaluates on a wall clock. It would need a clock no test "
            "can move, it would write audit rows naming no route, and under pytest-xdist it "
            "would race across workers."
        ),
        "consequence": (
            "Nothing fires until someone calls the sweep, so the response reports a ``due`` "
            "count: the number the next call would act on."
        ),
        "unsourced": False,
    },
    {
        "id": "engagement-event-canonical-list",
        "question": "Which engagement-event list is canonical?",
        "chosen": (
            "The ``content_stat.series`` webhook topic is canonical for a series' lifecycle "
            "events (receipt, goal_success, series_completion, series_disengagement, "
            "series_exit); the data flow is canonical for the per-buyer interaction events "
            "(open, click, reply). The union is the accepted set."
        ),
        "rejected": "Only the topic's five, or only the data flow's five.",
        "consequence": (
            "A rule block can wait on a lifecycle event or an interaction event, and the two "
            "lists are published separately so a client can tell them apart."
        ),
        "unsourced": False,
    },
    {
        "id": "send-window-is-retry",
        "question": "Is a content block's send window a delay or a retry window?",
        "chosen": (
            "A retry window. The block tries to deliver; a buyer who is offline does not delay "
            "the message, the block holds only while it retries, and the offline branch fires "
            "once the window has elapsed."
        ),
        "rejected": (
            "A sleep: hold the message until the window elapses, then send it once. That inverts "
            "the research's tooltip, which is about the buyer leaving the path while the block "
            "keeps trying."
        ),
        "consequence": (
            "A content block whose primary channel cannot deliver holds the run in a "
            "``send_window`` wait, and resolving it past the window fires the fallback."
        ),
        "unsourced": False,
    },
    {
        "id": "offline-branch-is-timeout",
        "question": "Is the offline branch a delivery failure or a timeout?",
        "chosen": (
            "A timeout: 'If the person still hasn't come online after 30d, they will leave this "
            "path'. The reason recorded is not_online_after_send_window."
        ),
        "rejected": "Treating the offline branch as a bounced delivery, which the research never says.",
        "consequence": (
            "An offline buyer is a state the run moves through, not an error; nothing raises."
        ),
        "unsourced": False,
    },
    {
        "id": "filter-fields-are-inferred",
        "question": "What are the contact and company filter fields?",
        "chosen": (
            "A published vocabulary of contact attributes (email, name, created_at, last_seen_at, "
            "plan, lead_score, owner_id, locale, tags, segment_ids, event_counts.*) and company "
            "attributes (name, industry, employees, country, plan, tags), each with an operator."
        ),
        "rejected": "Accepting arbitrary JSON paths, which would make a typo a silently-matchless rule.",
        "consequence": (
            "An unknown field is refused by name, and the vocabulary is exported so WF-105 reuses "
            "the same names."
        ),
        "unsourced": True,
    },
    {
        "id": "dispatch-is-recorded",
        "question": "Does this build send a message?",
        "chosen": (
            "No. Every dispatch row carries sent_by_this_product: false and "
            "channel_send_is_recorded: true, the same standard dsr.conversation_chase and "
            "dsr.automation_engagement follow."
        ),
        "rejected": "Calling a messaging provider, which needs a credential this product does not hold.",
        "consequence": (
            "The channel decision and the fallback are real, recorded state; the send is a stand-in."
        ),
        "unsourced": True,
    },
)

#: The audit ids referenced above, by decision id.
JEV_AUDITS: dict[str, str] = {
    decision["id"]: decision["jev_audit"] for decision in DECISIONS if decision.get("jev_audit")
}


def inferences_report() -> dict[str, Any]:
    """Every recorded decision, as data, for the ``/inferences`` route."""

    return {
        "ticket": "WF-104",
        "count": len(DECISIONS),
        "decisions": [dict(decision) for decision in DECISIONS],
        "jev_audits": dict(JEV_AUDITS),
        "unsourced": [d["id"] for d in DECISIONS if d.get("unsourced")],
    }


def decision_by_id(decision_id: str) -> dict[str, Any] | None:
    for decision in DECISIONS:
        if decision["id"] == decision_id:
            return dict(decision)
    return None
