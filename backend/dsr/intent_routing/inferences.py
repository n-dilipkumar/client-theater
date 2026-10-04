"""Every judgement call this workflow rests on, named, and served.

The research for WF-133 is a vendor feature list. It is exact about two things:
the alert context, quoted as "which pages, how long, which stakeholder", and the
worked example, a stakeholder who "reads the pricing section for 90 seconds". It
names five threshold inputs, four automations, two product surfaces and eleven
open-source tools without defining the rules any of them run on.

Those edges are where a build goes wrong quietly, so each one is written down here
with the reading taken, the reason, and what would change it. They are served over
HTTP by ``GET /api/wf-133/inferences``, because the point of the endpoint is to
show where the line falls rather than to have a reader reconstruct it from a diff.

One of them is sourced. The rest are inferred, and ``inference: True`` on all of
them is the point.
"""

from __future__ import annotations

from typing import Any

#: Each entry: the decision, the reading taken, why, what would reverse it, and
#: the risk of having got it wrong.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "the_four_derived_thresholds",
        "question": (
            "The research names five threshold inputs, 'pages, dwell, revisit, download, demo "
            "interaction', and gives one number. What are the other four?"
        ),
        "reading": (
            "Pages 4, revisit 3, download 1, demo interaction 1. Dwell stays at the quoted 90 "
            "seconds and is the only sourced value in this workflow."
        ),
        "why": (
            "Four distinct pages is the smallest count a reader cannot reach by scrolling within "
            "the one section the worked example describes, so it measures breadth rather than "
            "depth. Three returns is the count at which a return reads as interest rather than as "
            "a page failing to load, because two returns are equally consistent with a reload. One "
            "download is enough because taking material away from the room is a deliberate act "
            "rather than a passive read, and one demo interaction is enough on the same grounds."
        ),
        "change": (
            "Edit DWELL_SECONDS, DISTINCT_PAGES, REVISITS, DOWNLOADS or DEMO_INTERACTIONS in "
            "dsr/intent_routing/vocabulary.py. The vocabulary route serves the new numbers."
        ),
        "risk": (
            "Four pages is a judgement about how long a sales room is. On a two-page room it "
            "never fires and only the sourced dwell rule alerts. That is a room that alerts less, "
            "not a workflow that alerts wrongly."
        ),
        "sourced": False,
    },
    {
        "id": "one_crossing_is_enough",
        "question": "How many thresholds must be met before a rep hears about it?",
        "reading": "One. The research describes a single qualifying action raising a signal.",
        "why": (
            "The worked example is one stakeholder reading one section for 90 seconds. A rule that "
            "required several bars at once would miss that moment entirely, and the moment is the "
            "thing the workflow exists to catch. It is also the only setting a vendor would ever "
            "quote, because a vendor sells the alert firing rather than the arithmetic behind it."
        ),
        "change": "Set THRESHOLDS_TO_ALERT in dsr/intent_routing/vocabulary.py.",
        "risk": "One crossing is a noisy setting on a busy room. The suppression window below is "
        "what absorbs the noise rather than a higher bar.",
        "sourced": False,
    },
    {
        "id": "suppression_window",
        "question": "'auto-suppress contacted accounts' is named as an automation. For how long?",
        "reading": (
            "Twenty-four hours, per account, per room, per recipient. A second alert about the "
            "same account to the same person inside that window is recorded with its dispatch state "
            "set to suppressed rather than queued."
        ),
        "why": (
            "The research names the automation and states no window. Twenty-four hours is one "
            "working day, which is the shortest span in which a second alert about the same account "
            "is noise rather than news. The unit is per recipient rather than per account so a "
            "second owner who has not been told is still told, and the window is measured from the "
            "moment the alert was dispatched rather than the moment it was written, because a rep "
            "told an hour ago is still the person who was told."
        ),
        "change": "Set SUPPRESSION_HOURS in dsr/intent_routing/vocabulary.py.",
        "risk": (
            "Twenty-four hours is long enough to swallow a genuine second wave of interest, and "
            "short enough to let a buyer who returns on day three through. The signal record is "
            "written either way, so nothing is lost when the alert is suppressed."
        ),
        "sourced": False,
    },
    {
        "id": "email_is_queued_and_slack_is_held",
        "question": (
            "The flow names two recipients, email and a Slack DM. What does this build do about "
            "each?"
        ),
        "reading": (
            "Email is queued as a real outbox row with a recipient, a subject and a body. Slack is "
            "recorded with the dispatch state held_for_integration and a reason saying there is "
            "no Slack surface and no Slack credential."
        ),
        "why": (
            "Slack is named as an API in the research and quoted by three of its nine sources, so "
            "it is not an error to have it on the list. This product has no confirmed Slack "
            "surface, no Slack credential and no outbound transport of any kind, so a state called "
            "sent would be a claim nothing in the codebase can support. Leaving Slack out of the "
            "record entirely is worse: a rep reading the record would conclude it went somewhere."
        ),
        "change": (
            "Give the feature a Slack transport behind routing.dispatch_for and change the Slack "
            "branch to return the queued state. Nothing else moves."
        ),
        "risk": (
            "An alert a seller believes reached Slack and did not. The record says held rather "
            "than queued precisely so the gap is visible from the alert itself."
        ),
        "sourced": False,
    },
    {
        "id": "an_alert_naming_a_stakeholder_cannot_address_them",
        "question": (
            "The alert context names the stakeholder, and the flow sends the alert to them "
            "effectively. Can this workflow address one?"
        ),
        "reading": (
            "No, and it does not pretend otherwise. The stakeholder entry always carries "
            "addressable: false. The email recipient is the account owner, never the buyer."
        ),
        "why": (
            "WF-031 keeps a company contact as a name and a role by design and refuses an email "
            "address on one, so an anonymous stakeholder has no address anywhere in the product. "
            "The buyer's address does exist on a CRM contact row, and sending the rep a message "
            "addressed to the buyer would be both useless and a disclosure."
        ),
        "change": (
            "A stakeholder identity source that carries an address. Nothing in this workflow "
            "changes; the flag already says which way the answer goes."
        ),
        "risk": (
            "The payload field a rep most wants is the one the privacy stance of a dependency "
            "makes unavailable. That is the correct trade and it is recorded rather than worked "
            "around."
        ),
        "sourced": False,
    },
    {
        "id": "no_paid_reverse_ip_provider",
        "question": (
            "The research says reverse-IP company resolution is the one piece with no clean "
            "open-source equivalent. Should this build buy one?"
        ),
        "reading": (
            "No. This workflow calls no provider. It reads the company WF-031 already identified, "
            "and refuses an alert with 404 for an unknown company and 409 for an account it "
            "cannot attach an opportunity to."
        ),
        "why": (
            "Buying one puts a per-event cost, a licence and a third party's error rate inside the "
            "one step that has to be answerable from this product's own audit log. The refusal is "
            "also more honest than a guess: an alert routed to whoever a provider thought an "
            "address belonged to is worse than no alert, because a rep will act on it."
        ),
        "change": (
            "Put a resolved account on the company record, or add a resolver step to "
            "dsr/intent_routing/resolution.py that calls a provider and records its answer."
        ),
        "risk": (
            "A buyer from an address the vendor has never seen produces no alert at all. WF-031 "
            "creates a company record for every unseen address, so the account is on the watchlist "
            "and appears in the engagement view as not engaged, which is where a seller finds it."
        ),
        "sourced": True,
    },
    {
        "id": "the_account_join_is_a_string_match",
        "question": "How is a WF-031 company attached to a WF-042 opportunity?",
        "reading": (
            "By website host first, then by company name with legal-form words removed. The match "
            "method is named on every resolution: website_domain, company_name, or unmatched."
        ),
        "why": (
            "Neither workflow carries the other's key. WF-031 keys a company by a network and "
            "WF-042 keys records by the vendor's record id, and no field on either side holds the "
            "other. A domain is a single canonical identifier and a name is a string a human typed "
            "twice, possibly differently, so the host is tried first."
        ),
        "change": (
            "Write the CRM's account id onto the company record, or onto the account record, and "
            "match on that in dsr/intent_routing/resolution.py."
        ),
        "risk": (
            "Two subsidiaries sharing a website domain match each other, and a company that writes "
            "its website differently from its CRM record does not match at all. Both cases end in "
            "a refusal rather than a wrong routing, which is the safe direction for this one."
        ),
        "sourced": False,
    },
    {
        "id": "engagement_view_reads_the_watchlist",
        "question": "What does the researched 'who is engaged and who is not' view show?",
        "reading": (
            "Every account on the room's watchlists, each marked engaged or not_engaged. Engaged "
            "means the account has an alert that no rep has dismissed."
        ),
        "why": (
            "The research names the surface and describes none of its behaviour. Building it from "
            "the alerts can only ever show who is engaged, so the half of the screen the research "
            "asks for by name would be empty. An account nobody has engaged is the row a seller "
            "most needs and the one an alert-driven view can never give them."
        ),
        "change": (
            "The view is a projection over watchlists and alerts, so changing what counts as "
            "engaged is a change to the projection in IntentRouter.engagement."
        ),
        "risk": (
            "A large watchlist makes the view long. It is a read with the same limit as every other "
            "list in this product, and it is not paginated beyond that."
        ),
        "sourced": False,
    },
    {
        "id": "only_target_accounts_alert",
        "question": "What is the rule set behind the researched target-account watchlists?",
        "reading": (
            "A watchlist is a named set of company keys with a tier and a list of addresses. Only an "
            "account on a watchlist in the room raises an alert. Below the threshold, off the "
            "watchlist, or with no opportunity attached, nothing is written and the reason is said."
        ),
        "why": (
            "The research lists 'the target-account list' as a data source and names watchlists as "
            "a product surface, but states no rule. Without a membership test every visitor to "
            "every room would alert, and an alerting workflow that alerts on everyone is one a rep "
            "turns off in a week."
        ),
        "change": "A watchlist is an ordinary record; adding or removing an account needs no code.",
        "risk": (
            "An account nobody remembered to watch generates nothing. The engagement view is the "
            "place that surfaces it, which is why that view is built over the watchlist."
        ),
        "sourced": False,
    },
    {
        "id": "four_routing_kinds",
        "question": "What is the rule set behind the researched alert routing rules?",
        "reading": (
            "Four kinds, read in position order: crm_owner, team, watchlist, fallback. The CRM "
            "owner is first because the flow names that recipient explicitly."
        ),
        "why": (
            "The research names 'alert routing rules' as a surface and 'CRM ownership and "
            "territory rules' as a data source, and states no rules. A team is read from the paths "
            "an importing team may have written onto a CRM record, because WF-042 defines no "
            "territory field of its own and copies unknown keys through untouched."
        ),
        "change": (
            "Add a kind to RULE_KINDS and a branch to routing.recipients_for. A rule that matches "
            "on an unimplemented key is refused rather than ignored, so it cannot silently never "
            "fire."
        ),
        "risk": (
            "A rule set configured for one room does not apply to another. Rules are room-scoped, "
            "which is correct and is also something a seller has to set up twice."
        ),
        "sourced": False,
    },
    {
        "id": "the_rep_records_and_the_workflow_does_not_decide",
        "question": "How does a rep close a signal, given that 'rep action logged' ends the flow?",
        "reading": (
            "The rep records an action: acknowledged, dismissed, contacted or noted. A dismissal "
            "needs a note. The alert's state is derived from the log rather than set directly, and "
            "a dismissal is terminal."
        ),
        "why": (
            "The research ends its data flow at 'rep action logged' and nothing follows it, so there "
            "is no next step for this workflow to own and inventing one would be building against "
            "the specification. Deriving the state rather than accepting it means the alert can "
            "never hold a state its own log does not support."
        ),
        "change": "Add a kind to ACTION_KINDS and a branch to engine._next_state.",
        "risk": (
            "A dismissal is terminal, so a rep who dismisses by mistake cannot undo it from this "
            "screen. That is the cost of a monotonic log, and a new action kind is the way out "
            "rather than an edit to an existing alert."
        ),
        "sourced": False,
    },
    {
        "id": "auto_enrich_is_the_measurement",
        "question": "'auto-enrich with buying signals' is named as an automation. What enrichment?",
        "reading": (
            "The CRM task carries the alert payload: which pages, how long, which stakeholder, and "
            "the threshold arithmetic. No vendor is called and no field is inferred."
        ),
        "why": (
            "Enrichment is named with no definition. Enrichment in this workflow's sense is the "
            "task being useful without a rep having to open the room, and the measurements that "
            "fired the alert are already in hand. Calling a vendor to add a firmographic field would "
            "add a per-task cost and an error rate for data the rep did not ask for."
        ),
        "change": (
            "Add a resolver step to the task creation path. The payload already travels on the "
            "task, so an added field has somewhere to go."
        ),
        "risk": (
            "The task carries no firmographics. A rep looking at a task in a CRM that normally "
            "shows them will not find them there."
        ),
        "sourced": False,
    },
    {
        "id": "declined_open_source_tools",
        "question": (
            "The research lists eleven open-source tools. Which of them does this build decline, "
            "and why?"
        ),
        "reading": (
            "All eleven. PostHog, Umami, Matomo and OpenReplay are analytics and session-replay "
            "products, so adopting one means running a second product beside this one and reading "
            "the room through its pipeline. Snowplow is a behavioural event pipeline, which is what "
            "WF-027 already is. Node-RED, n8n and Kestra are workflow orchestrators, and this "
            "workflow's chain is five steps in one process. Superset and Metabase are BI tools over "
            "a warehouse, and this product's data is in SQLite behind an audit log."
        ),
        "why": (
            "Every one of them would be a dependency, a deployment and a second source of truth for "
            "the same buyer behaviour. The feature contract exists so a hundred features merge "
            "without touching a shared file, and each of these would be a shared file."
        ),
        "change": (
            "Adopting one is platform work, not feature work: it needs a deployment, an operator and "
            "a shared-file change with the platform-change label on the pull request."
        ),
        "risk": (
            "The engagement data this workflow reasons over is whatever the room recorded, and it "
            "has no session replay behind it. That is a real limit and it is the limit the product "
            "already accepted when it chose company-level records only."
        ),
        "sourced": True,
    },
    {
        "id": "overlap_with_wf134_and_wf031",
        "question": (
            "WF-134 covers engagement-driven deal-health scoring and coaching triggers, and WF-031 "
            "also drives auto-engagement. What does this workflow not duplicate?"
        ),
        "reading": (
            "This workflow owns the alert and the follow-up task and nothing about scoring. The "
            "payload carries raw measurements and the threshold arithmetic, never a health score or "
            "a coaching trigger."
        ),
        "why": (
            "The research for this ticket is about routing an alert while the interest is live. A "
            "health score on the same payload would make two features answer the same question with "
            "two sets of numbers, and the rep would see whichever one loaded last."
        ),
        "change": "N/a. This is a boundary, and both sides have to hold it.",
        "risk": (
            "A rep comparing an alert against a deal-health score has to reconcile two readings. "
            "Keeping the measurements raw is what lets them do that rather than trusting either."
        ),
        "sourced": False,
    },
)

__all__ = ["INFERENCES"]
