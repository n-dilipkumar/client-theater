"""Every judgement call this build makes, in one inspectable place.

The research quotes the three frequency modes and the session damper sentence. It
names four further things and specifies none of them: the repeat count the ticket
title calls "repeatedly", the repeat window, the dwell threshold, and the contents of
the five trigger panes. Those are recorded here rather than left as comments in
function bodies, and ``GET /api/wf-106/inferences`` serves them.

Each entry carries four fields and the last three are the point:

``sourced``
    Whether the research states this. Eleven of the fifteen entries are inferences.

``reading``
    What this build does instead.

``change``
    The one line a reviewer edits to disagree. Most of them name a constant in
    :mod:`dsr.page_outreach.vocabulary` or a field in the workflow record.

``risk``
    What goes wrong if the reading is wrong. An entry without a risk is an entry
    nobody thought about.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "repeat_count",
        "question": "How many matching visits does a buyer have to make?",
        "reading": (
            "Three matching visits inside seven days. The ticket title says repeatedly and the "
            "spec states no count, so the number is this build's reading. Three is the point "
            "where a pattern stops being a page opened twice and starts being intent."
        ),
        "why": (
            "Two visits is what one browser tab refreshing looks like. Five is a campaign the "
            "seller would have to run deliberately. Neither describes the buyer who read the "
            "upgrade page on Monday, the pricing page on Wednesday and the upgrade page again "
            "on Friday."
        ),
        "change": "Set REPEAT_VISITS in dsr.page_outreach.vocabulary, or repeat_visits on a workflow.",
        "risk": (
            "A buyer who returns three times inside an hour sees nothing, and one who spreads "
            "three visits over a fortnight sees the block twice."
        ),
        "sourced": False,
    },
    {
        "id": "repeat_window",
        "question": "How far apart may those visits be?",
        "reading": (
            "Seven days, counted back from the current visit. Long enough to span a weekend "
            "and a return trip to the buyer's desk."
        ),
        "why": (
            "The research describes a de-facto repeated-visit damper and states no window. A "
            "window shorter than a weekend splits one buying cycle into two, and a window "
            "longer than a month stops being a damper at all."
        ),
        "change": "Set REPEAT_WINDOW_DAYS in the vocabulary, or window_days on a workflow.",
        "risk": "A slow cycle re-qualifies a buyer and the seller sees the block a second time.",
        "sourced": False,
    },
    {
        "id": "dwell_threshold",
        "question": "How long must a buyer sit on a matching page?",
        "reading": (
            "Sixty seconds on one page, or a per-rule number when the rule names one. The "
            "evidence says the trigger fires on time on page or visited URL and gives no "
            "number for either signal."
        ),
        "why": (
            "Sixty seconds is above the time anyone spends on a page they are not reading, and "
            "below the ninety seconds WF-133 uses for the same behaviour, so the two workflows "
            "do not disagree about one visitor on one page."
        ),
        "change": "Set DWELL_SECONDS in the vocabulary, or a number on a workflow's dwell rule.",
        "risk": (
            "A buyer who reads fast is treated the same as one who skimmed, because no dwell "
            "signal separates them."
        ),
        "sourced": False,
    },
    {
        "id": "rule_list_is_and",
        "question": "Do a workflow's targeting rules combine with AND or with OR?",
        "reading": (
            "AND across kinds, OR within one kind. A workflow with a URL rule and a dwell rule "
            "needs both. A workflow with two URL rules needs either."
        ),
        "why": (
            "A seller who writes a pricing-page rule and a ninety-second rule means both, and a "
            "seller who writes two URL rules means either of them. AND across kinds with OR "
            "within a kind is the only reading that serves both sentences."
        ),
        "change": "match_rules in dsr.page_outreach.rules; the engine passes the list through.",
        "risk": "A workflow with many rules is hard to qualify for and its owner will not know which one failed.",
        "sourced": False,
    },
    {
        "id": "workflow_with_no_rules",
        "question": "What does a workflow with no targeting rules mean?",
        "reading": (
            "It matches every page view, and only the repeat threshold, the frequency mode and "
            "the session damper stand between a visit and a block."
        ),
        "why": (
            "The alternative is refusing to save it. A seller who wants every returning visitor "
            "to see a block has said so by naming no page, and a refusal would make the "
            "workflow builder harder for the case it already handles."
        ),
        "change": "The rules list on the workflow record. An empty list is the reading.",
        "risk": (
            "It is the widest possible trigger, so a seller who creates a workflow before "
            "adding rules will show it to the whole audience."
        ),
        "sourced": False,
    },
    {
        "id": "seen_counts_shows",
        "question": "What does the Seen mode count?",
        "reading": (
            "Shows. A buyer is sent the block once and never again whether or not they interact "
            "or dismiss it, so the counter is of deliveries and not of interactions."
        ),
        "why": (
            "The quote is explicit. The alternative, counting only engagements, would re-show "
            "the block to every buyer who ignored it, which is the opposite of what the mode "
            "is for."
        ),
        "change": "frequency_decision in dsr.page_outreach.rules.",
        "risk": "A buyer who was shown the block on a page load that failed keeps it counted as seen.",
        "sourced": True,
    },
    {
        "id": "any_interaction_counts_dismissal",
        "question": "Does a dismissal count as an interaction?",
        "reading": (
            "Yes. Any interaction happens stops on a dismissal, a Messenger open, a click, a "
            "path selection or a goal."
        ),
        "why": (
            "The research lists the buyer's interaction as path selection, Messenger open or "
            "dismissal in one breath, so all three are interactions. Treating a dismissal as "
            "something else would re-show the block to the buyer who just closed it."
        ),
        "change": "The any_interaction entry in FREQUENCY_MODES, and frequency_decision.",
        "risk": "A buyer who closed it by accident never sees it again.",
        "sourced": True,
    },
    {
        "id": "engaged_with_needs_a_path",
        "question": "What counts as engaging for the Engaged with mode?",
        "reading": (
            "A path selection, or a goal reached. A Messenger open and a dismissal are "
            "recorded but are not engagement."
        ),
        "why": (
            "The quote says the buyer engages by selecting a Workflow path. Counting a Messenger "
            "open as engagement would end the mode for a buyer who merely looked, which is the "
            "one thing the mode exists to avoid."
        ),
        "change": "ENGAGEMENT_INTERACTIONS in the vocabulary.",
        "risk": "A buyer who books a meeting from an app without choosing a branch never engages.",
        "sourced": True,
    },
    {
        "id": "session_damper_expiry",
        "question": "How does the session damper know a session has ended?",
        "reading": (
            "It does not track time. The snippet's session id is the boundary: the damper reads "
            "only the interactions recorded against the current session id, so it stops "
            "applying the moment a new id arrives."
        ),
        "why": (
            "The research says the block is hidden for the remainder of the session and shown "
            "again in a new one, and it names no session length. A timer would have to invent "
            "a length, and the snippet already knows the boundary."
        ),
        "change": "session_decision, and the session_id field on the page view.",
        "risk": "A snippet that reuses one session id for a whole day makes the damper permanent.",
        "sourced": True,
    },
    {
        "id": "session_hides_from_dismissal",
        "question": "Which interactions hide the block for the rest of the session?",
        "reading": "A dismissal and a Messenger open. Nothing else does.",
        "why": (
            "The quote names exactly those two. A click on a booking app is not a reason to "
            "hide the block, because the buyer is further along, not finished."
        ),
        "change": "SESSION_HIDING_INTERACTIONS in the vocabulary.",
        "risk": "None recorded: this one follows a quoted sentence.",
        "sourced": True,
    },
    {
        "id": "workflow_state_is_scheduling",
        "question": "Where does the flow's set it live step live?",
        "reading": (
            "On the Scheduling pane, as a state of draft or live. A draft never fires, whatever "
            "the page views say."
        ),
        "why": (
            "The flow's last step is to build the workflow and set it live, and the pane list "
            "names Scheduling without saying what it holds. A state a seller cannot set is a "
            "builder that publishes by accident."
        ),
        "change": "The state field on the workflow record, and WORKFLOW_STATES.",
        "risk": (
            "A workflow saved as live fires on the next matching page view, so a seller who "
            "edits rules on a live workflow changes live behaviour immediately."
        ),
        "sourced": False,
    },
    {
        "id": "audience_pane_is_three_lists",
        "question": "What does the Audience pane hold?",
        "reading": (
            "Company keys, tags and segments, all optional, all read from the visitor keys the "
            "snippet reports."
        ),
        "why": (
            "The research names Segments and Contact plus Company attributes as data sources. It "
            "states no rule, and a three-list reading is the one that can be served from the "
            "room's own records without a vendor call."
        ),
        "change": "The audience object on the workflow record.",
        "risk": (
            "An audience the room has not identified is an empty list, and a view with no company "
            "key is not filtered on. A seller who targets a segment will see matches they did "
            "not expect until the room has identified those companies."
        ),
        "sourced": False,
    },
    {
        "id": "goal_pane_is_one_name",
        "question": "What does the Goal pane hold?",
        "reading": (
            "One goal name, and a goal_reached receipt is recorded against the workflow when a "
            "caller says the goal fired. This build does not evaluate the goal itself."
        ),
        "why": (
            "The research lists a Goal pane and a content_stat goal topic, and never says what "
            "the goal measures for this trigger. A name the seller chooses is the whole of what "
            "the evidence supports."
        ),
        "change": "The goal_name field on the workflow record, and goal_reached in INTERACTIONS.",
        "risk": "A goal nobody posts is a name on a record and never a receipt.",
        "sourced": False,
    },
    {
        "id": "where_to_send_is_one_channel",
        "question": "What does the Where to send pane offer?",
        "reading": "One channel, in_app. Anything else is refused at save time.",
        "why": (
            "The research names the Messenger as the surface and POST /messages with "
            "message_type in_app as its API equivalent. This product has no outbound transport, "
            "so a second channel would be a stored promise nothing could keep."
        ),
        "change": "CHANNELS in the vocabulary, and the channel field on the workflow record.",
        "risk": "A seller who wants email cannot build it, and the refusal says so rather than storing it.",
        "sourced": True,
    },
    {
        "id": "one_call_per_page_view",
        "question": "What contract does the ingest route have?",
        "reading": (
            "One page view per call. The caller posts one call per view the snippet sees, with "
            "the snippet's own session id. This workflow owns the rules and the receipts, not "
            "the snippet."
        ),
        "why": (
            "The research says the page view is collected by an external JavaScript snippet. A "
            "batch route would have to invent a batching key the vendor does not define, and a "
            "partial batch would make the repeat count untrustworthy."
        ),
        "change": "parse_page_view refuses a list. The route takes one object.",
        "risk": (
            "A high-traffic page makes one call per view. The route answers a decision and writes "
            "at most three records, so the cost is bounded, but a caller that posts a view per "
            "page load is doing more work than it needs to."
        ),
        "sourced": False,
    },
    {
        "id": "delivery_is_recorded_not_sent",
        "question": "Does showing the block mean a message was posted?",
        "reading": (
            "No. A delivery record says the block would be shown, in the channel this build "
            "names, to that visitor in that session. Nothing is sent to any vendor and no "
            "browser messenger is opened."
        ),
        "why": (
            "The research describes the vendor's messenger. This product is a sales room, so the "
            "honest build records the decision and the receipt and says plainly that it posts "
            "nothing."
        ),
        "change": "DELIVERY_LIMITS in the vocabulary, and the note at the foot of the page.",
        "risk": "A reader who takes a delivery row for a sent message will over-trust the workflow.",
        "sourced": True,
    },
    {
        "id": "audience_matches_the_visitor",
        "question": "Who does the audience list filter?",
        "reading": (
            "The visitor, through the company key the page view carries. A view with no company "
            "key is not filtered out; it is recorded and counted, because refusing it would lose "
            "the repeat count for every anonymous buyer."
        ),
        "why": (
            "The snippet identifies some visitors and not others, and the research treats "
            "Intercom Contact plus Company attributes as an enrichment rather than a "
            "precondition. Dropping unidentified visitors would make the workflow look for "
            "exactly the anonymous traffic it was built to catch."
        ),
        "change": "The engine's audience filter.",
        "risk": "An audience list is a soft filter on identified visitors and a hard one on the rest.",
        "sourced": False,
    },
)


def inferences() -> dict[str, Any]:
    """The decision record, served as data with the sourced half counted separately."""
    sourced = [entry for entry in INFERENCES if entry["sourced"]]
    return {
        "ticket": "WF-106",
        "count": len(INFERENCES),
        "sourced_count": len(sourced),
        "inferred_count": len(INFERENCES) - len(sourced),
        "inferences": [dict(entry) for entry in INFERENCES],
    }


__all__ = ["INFERENCES", "inferences"]
