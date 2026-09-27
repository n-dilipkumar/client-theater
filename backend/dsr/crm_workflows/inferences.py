"""Every judgement call this package makes, in one inspectable place.

The research for WF-030 is unusually specific about the *surface* of this
workflow: it enumerates the five filter families, it says which refinements each
family takes, it says the workflow is contact-based only, it says the trigger is
"When filter criteria is met", it names four actions, and it says "Nothing happens
on the seller's screen". What it does not do is say how a *sender* or a *run* of
this build behaves on the edges of that description, and the edges are where a
build has to decide something.

Those decisions are collected here rather than left as comments in function
bodies, because a judgement call in a comment is one nobody re-reads and a wrong
one becomes product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-030/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

One entry is not an inference but a boundary, and is listed for that reason:
``not-built`` records what this build deliberately does not do, because a feature
whose page does not show its own edges overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_workflows.vocabulary import (
    ACTION_FAMILIES,
    DELIVERY_PATHS,
    ENROLLMENT_TYPES,
    FILTER_FAMILIES,
    LIFECYCLE_STAGES,
    REFINEMENTS,
    TRIGGER_MODES,
)

#: The sentence from the research that governs most of the surface below.
SOURCED_QUOTE = (
    "When you select Dock, you'll see five different options for your filter. You have "
    "the option to select from Analytics events (Views, Clicks, Downloads, or "
    "Interactions), or MAP activity (movement related to project plans in the "
    "workspace)."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "unverifiable-refinement-does-not-match",
        "topic": "what happens when a filter refines on a field the event does not carry",
        "topic_note": "the largest inference in the package",
        "basis": (
            "The research says the five families can be refined by date, file name, link "
            "URL, or activity text, and that the filter 'evaluates criteria per contact'. "
            "It does not say what to do with an event that carries none of the fields the "
            "criteria refines on, which is a real case because activity payloads are "
            "arbitrary JSON in this product."
        ),
        "value": {
            "verdict": "does not match",
            "reason": "refinement_unverifiable",
            "reported_separately_from": "a plain miss",
        },
        "why": (
            "A match needs positive evidence. Firing on a field nobody supplied would let a "
            "'change stages based on onboarding tasks' rule enrol a contact because the "
            "event happened to omit a field. Failing silently would be the other bad "
            "outcome: a team that has not yet wired link_url onto its activity rows would "
            "see a workflow that quietly does nothing, with no way to find out why. "
            "Reporting the reason separately from a miss is what makes the edge debuggable."
        ),
        "change_it": (
            "The first branch of matches() in dsr/crm_workflows/criteria.py, and the "
            "'refinement_unverifiable' entry in REASONS next to it."
        ),
        "blast_radius": "Whether a workflow enrols a contact, and the reason shown for it.",
    },
    {
        "id": "amend-published-workflow",
        "topic": "whether a published workflow's definition can be edited in place",
        "basis": (
            "'Publish the workflow; DSR activity then drives it with no further setup.' The "
            "research says nothing about editing after publishing."
        ),
        "value": {"amendable": False, "path": "unpublish, amend, publish"},
        "why": (
            "A published definition is the thing already firing. Contacts may have been "
            "enrolled by the version being edited, and their enrollments name this "
            "workflow, so editing in place would rewrite what those rows claim happened."
        ),
        "change_it": (
            "The status check at the top of engine.amend(); PublishedWorkflowIsImmutable in "
            "dsr/crm_workflows/errors.py carries the 409."
        ),
        "blast_radius": "Whether a PATCH to a published workflow is accepted.",
    },
    {
        "id": "first-enrollment-wins",
        "topic": "what happens when a second matching event arrives for an enrolled contact",
        "basis": (
            "'The workflow is the automation; it fires continuously on matching activity.' The "
            "research does not describe a re-enrolment policy, and HubSpot's is a setting "
            "this research never mentions."
        ),
        "value": {
            "re_enrolment": False,
            "instead": "the existing enrollment's match_count and last_match_at move",
        },
        "why": (
            "Enrolment is a function of the (contact, workflow) pair - the same reason the "
            "workflow is contact-based at all. Counting the further matches on the existing "
            "enrollment keeps 'fires continuously' true without inventing a re-enrolment "
            "policy the sources do not state, and a second enrollment row would let the same "
            "contact appear to have been enrolled twice by one rule."
        ),
        "change_it": "The already-enrolled branch in engine.evaluate().",
        "blast_radius": "The number of enrollment rows, and match_count on each.",
    },
    {
        "id": "occurred-window-is-a-whole-utc-day",
        "topic": "what a bare date in the 'occurred' refinement means",
        "basis": (
            "'**Views:** filter by date.' A date filter in a CRM is a date *range* control; "
            "the research does not say which end of the day is included."
        ),
        "value": {
            "bare_date": "the whole UTC day, both ends included",
            "from_to": "inclusive; a date-only 'to' includes the whole day",
            "timezone": "UTC",
        },
        "why": (
            "A single instant of midnight would silently discard every event on the day "
            "being asked about, and a naive local reading would move a buyer's event across "
            "a day boundary and enrol a contact on a filter that did not match. This product "
            "stores UTC throughout, so the criterion is read in UTC too."
        ),
        "change_it": "occurred_window() in dsr/crm_workflows/activity.py.",
        "blast_radius": "Which events a date-refined filter matches.",
    },
    {
        "id": "activity-text-matches-the-quoted-task-name",
        "topic": "what an activity_text criterion is compared against",
        "basis": (
            "The evidence gives two worked examples verbatim: 'completed task \"Sign up for "
            "free account\"' and 'completed task \"Intro call\"'. Both are the same shape - a "
            "sentence with the task name quoted."
        ),
        "value": {
            "extracts": "the quoted task name, compared case-insensitively and exactly",
            "fallback": "any other text is compared whole",
        },
        "why": (
            "It makes \"completed task 'Intro call'\" and \"Intro call\" name the same "
            "criterion, which is what a person means when they type either. The whole-text "
            "fallback means a literal string this product did not document still works "
            "rather than being refused for being unfamiliar."
        ),
        "change_it": "activity_text_task_name() in dsr/crm_workflows/activity.py.",
        "blast_radius": "Which MAP-activity events a criteria matches.",
    },
    {
        "id": "link-url-is-exact-file-name-is-not",
        "topic": "case sensitivity and exactness of the two text refinements",
        "basis": (
            "The research says Clicks and Interactions are refined 'by date or link URL' and "
            "Downloads 'by date and/or file name'. It publishes no operator, no case rule, "
            "and no partial-match option."
        ),
        "value": {
            "link_url": "exact and case-sensitive, after trimming",
            "file_name": "exact, case-insensitive, whitespace-collapsed",
        },
        "why": (
            "A URL is an identifier and its path is case-sensitive, so comparing it loosely "
            "would match a different resource. A file name and a task name are "
            "human-authored labels that are spelled inconsistently - the research's own two "
            "examples differ in capitalisation - so case-insensitive exact is the "
            "comparison that behaves the way a person expects. Neither is a substring match, "
            "because the sources publish no such option and inventing one would make a "
            "criterion match more than the thing it names."
        ),
        "change_it": "The link_url and file_name branches of _check_refinement() in criteria.py.",
        "blast_radius": "Which click, interaction, and download events a criteria matches.",
    },
    {
        "id": "filters-are-anded-and-uncapped",
        "topic": "how several filters on one workflow combine, and how many there may be",
        "basis": (
            "'Trigger: When filter criteria is met' is singular, and the flow says 'Pick from "
            "the five filter families'. The five is a count of the options the integration "
            "offers, not a cap on a workflow's filters."
        ),
        "value": {"combination": "AND", "cap": None},
        "why": (
            "ANDing is what 'filter criteria is met' means and what the CRM this was "
            "researched on does. Putting a cap of five on the number of *criteria* would "
            "confuse the count of published families with a limit, and would refuse "
            "something the research never says is impossible."
        ),
        "change_it": "evaluate_contact() in criteria.py, and parse_criteria_list() for the cap.",
        "blast_radius": "Which events satisfy a multi-filter workflow.",
    },
    {
        "id": "criteria-are-evaluated-per-contact",
        "topic": "whether one event must satisfy every filter, or each filter some event",
        "topic_note": "the second-largest inference, and the one that decides whether a two-filter workflow can ever fire",
        "basis": (
            "The data flow says the integration's filter 'evaluates criteria per contact'. "
            "The sources do not say whether a workflow with two filters is ANDed per event "
            "or per contact, and the two readings differ in a way a user can feel."
        ),
        "value": {
            "granularity": "per contact",
            "each_filter": "must be satisfied by at least one event in the contact's activity",
            "per_event_and": "still available, as the single-event helper",
        },
        "why": (
            "A per-event AND says one event must satisfy every filter, which makes a "
            "workflow filtering both downloads and views unfireable - a single event is "
            "never both - and the person who built it would see a rule that silently does "
            "nothing. A per-contact AND is what a reader of 'when filter criteria is met' "
            "understands and what a CRM this was researched on does. The per-event reading "
            "is the more literal one, so it is kept as a helper rather than discarded."
        ),
        "change_it": (
            "evaluate_contact() in criteria.py, called from engine.evaluate(). Swap it for "
            "evaluate() over single events to get the per-event behaviour back."
        ),
        "blast_radius": "Whether a workflow with more than one filter family can ever fire.",
    },
    {
        "id": "action-list-is-open-filter-list-is-closed",
        "topic": "why an unknown action kind is stored but an unknown family is refused",
        "topic_note": "the asymmetry most likely to look like an inconsistency",
        "basis": (
            "Filters: 'you'll see **five different options** for your filter' - an "
            "enumeration. Actions: 'send emails, slack notifications, update fields, change "
            "stages **and more!**' - explicitly open, and the extensibility line says "
            "'Vendors add new trigger families by extending the integration's filterable "
            "properties'."
        ),
        "value": {
            "filter_families": "refused when unrecognised",
            "action_kinds": "stored with resolved=false and reported",
        },
        "why": (
            "A filter on a sixth family names something the integration cannot evaluate, so "
            "it would never fire - a rule that can never fire is worse than one that will "
            "not save. An action kind outside the four is a thing a team added, and this "
            "product's contract is that a field nobody coordinated with us still gets "
            "stored and reported. Silently dropping it is the one failure this codebase is "
            "built to avoid."
        ),
        "change_it": (
            "require_family() in vocabulary.py raises; normalise_actions() in definition.py "
            "sets resolved=False."
        ),
        "blast_radius": "Which definitions can be created, and what an enrollment reports.",
    },
    {
        "id": "lookback-defaults-to-unlimited",
        "topic": "how far back a filter looks",
        "basis": (
            "'it fires continuously on matching activity' - no window is mentioned anywhere in "
            "this workflow's sources."
        ),
        "value": {"default": None, "meaning": "every matching event in the room, of any age"},
        "why": (
            "A default window would be an invented requirement: it would quietly stop a "
            "workflow from enrolling a contact whose event predates the workflow's "
            "publication, with no sourced reason. A definition may set lookback_days, which "
            "is then a choice somebody made and can see."
        ),
        "change_it": "The default in normalise_workflow(), applied in engine.evaluate().",
        "blast_radius": "Which older events a workflow enrols a contact on.",
    },
    {
        "id": "activity-idempotency-key",
        "topic": "what a repeated activity event does",
        "basis": (
            "This workflow's research states no idempotency rule for activity. It does name "
            "the webhook source ('Dock workspace.* / workspace.plan.task.* webhooks'), and a "
            "retried webhook is a real case. The sibling WF-027 research states the rule "
            "explicitly for its own emission, and both cite the same Dock webhook reference."
        ),
        "value": {
            "optional": True,
            "rule": "first one wins; a repeat increments duplicate_attempts on the kept event",
        },
        "why": (
            "Without it, a retried webhook inflates an enrollment's match_count and a person "
            "reading the history sees activity that did not happen. It is optional, so a "
            "caller that sends no key is unaffected, and it is recorded here because this "
            "workflow's own sources do not state it."
        ),
        "change_it": "The idempotency branch in engine.record_activity().",
        "blast_radius": "The activity count and any match_count a duplicate inflated.",
    },
    {
        "id": "lookback-is-measured-from-now",
        "topic": "what 'look back N days' is measured from",
        "basis": (
            "The research mentions no window at all, so both the setting and its reference "
            "point are this build's."
        ),
        "value": {"measured_from": "the evaluation's clock", "alternative": "the contact's most recent event"},
        "why": (
            "Measured from the latest event, a single old event always satisfies any window, "
            "so the setting would appear to do nothing exactly when somebody was using it to "
            "stop old activity enrolling a contact. Measured from now, lookback_days: 1 "
            "means the last day, which is what the words say."
        ),
        "change_it": "engine._within_lookback().",
        "blast_radius": "Which events a workflow with a lookback considers.",
    },
    {
        "id": "unlinking-is-a-null-connection",
        "topic": "how a room's deal connection is removed",
        "basis": (
            "Step 1 checks that 'the workspace is connected to a deal/account', so the "
            "connection has to be able to become untrue. The sources say nothing about how."
        ),
        "value": {"unlink": "a null entry in the connections patch removes that room"},
        "why": (
            "The whole connections map is one JSON value in data, and 'remove this key' is "
            "what a merge patch means everywhere else in this product. A separate unlink route "
            "would be a second way to express the same change, and the map could then only "
            "ever grow."
        ),
        "change_it": "_amend_connections() in engine.py.",
        "blast_radius": "Whether a room can be unlinked at all, and what room_not_connected reports.",
    },
    {
        "id": "lifecycle-stage-constraint-refuses-one-action",
        "topic": "what a stage that cannot move forward does to the enrollment",
        "basis": (
            "Sourced: 'When you include the lifecyclestage property, you can only set the "
            "value *forward* in the stage order' - from the lead-scoring article this "
            "workflow also cites. Not sourced: what the workflow as a whole should do when "
            "one of its actions cannot apply."
        ),
        "value": {
            "granularity": "the action",
            "refused": "that action's status becomes 'refused' with the reason",
            "enrollment": "still created; its other actions still apply",
        },
        "why": (
            "Refusing the whole enrollment would mean a contact who completed a task out of "
            "order also failed to receive the email the same workflow sends. The constraint "
            "is about the property, not about the rule."
        ),
        "change_it": "The lifecycle branch of resolve_action() in actions.py.",
        "blast_radius": "One action's status on an enrollment, and the enrollment's counts.",
    },
    {
        "id": "lifecycle-stages-are-not-deal-stages",
        "topic": "which stage vocabulary 'change stages' means",
        "basis": (
            "'Change stages in HubSpot based on onboarding or mutual action plan tasks' is a "
            "pipeline statement; the forward-only constraint is about the lifecyclestage "
            "property, which is a different property."
        ),
        "value": {
            "stage_kind": "required, one of lifecyclestage or deal_stage",
            "default": "deal_stage",
            "forward_only": "lifecyclestage only",
        },
        "why": (
            "A pipeline moves backwards routinely, so applying a forward-only rule to a deal "
            "stage would refuse correct behaviour. Requiring the two to be named apart means "
            "the constraint is applied exactly where the source states it."
        ),
        "change_it": "normalise_actions() requires stage_kind; vocabulary.STAGE_KINDS.",
        "blast_radius": "Which change_stage actions are subject to the forward-only check.",
    },
    {
        "id": "integration-disabled-refuses-publish",
        "topic": "whether a workflow can be published against an integration that is off",
        "basis": (
            "Step 1: 'Verify the HubSpot integration is on and the workspace is connected to "
            "a deal/account.' The research makes the check a step; it does not say what "
            "happens if it fails."
        ),
        "value": {"publish": "refused with 409", "register": "refused with 404-equivalent", "lint": "still reports it"},
        "why": (
            "Publishing would create a rule that silently never fires, and a seller cannot "
            "diagnose that from a workflow list. The connection to a deal is per-room and so "
            "is reported per-evaluation rather than refused, because one unconnected room "
            "does not invalidate a workflow for every other room."
        ),
        "change_it": "engine.publish() raises; lint_workflow() reports without raising.",
        "blast_radius": "Whether a definition can reach 'published'.",
    },
    {
        "id": "unconnected-room-enrols-nobody",
        "topic": "what a room with no deal link in the integration does",
        "basis": (
            "Step 1 pairs the two conditions: the integration is on *and* the workspace is "
            "connected to a deal/account. The research does not say which of them a "
            "workflow can do without."
        ),
        "value": {"evaluation": "reports room_not_connected for every workflow", "enrollment": "none"},
        "why": (
            "The activities are tied to the contact record, and a contact with no deal has "
            "no stage to change and no pipeline to notify - so the researched action set "
            "cannot run. Reporting the reason per workflow is what makes the gap findable "
            "from the evaluation response rather than from a support ticket."
        ),
        "change_it": "The room-connection check in engine.evaluate().",
        "blast_radius": "Whether an evaluation reports decisions or enrollments.",
    },
    {
        "id": "contact-is-the-activity-owner",
        "topic": "whose activity a filter evaluates",
        "basis": (
            "'Dock only supports Contact based workflows since the activities are tied to the "
            "contact record.' The activities are per-contact; the room is the workspace."
        ),
        "value": {"evaluated_against": "one contact", "scoped_to": "one room"},
        "why": (
            "The constraint is not a preference - it is why the workflow type is what it is. "
            "An evaluation therefore takes a contact and a room and never a bare room, so a "
            "'change stages on task completion' rule cannot fire on an account-wide event "
            "with nobody attached to it."
        ),
        "change_it": "The route shape under /rooms/{room_id}/evaluate.",
        "blast_radius": "Which events are eligible for a workflow at all.",
    },
    {
        "id": "not-built",
        "topic": "what this build deliberately does not do",
        "basis": (
            "The research names the write side ('HubSpot's CRM API ... and HubSpot workflow "
            "APIs') and the webhook alternative ('Dock Webhooks + HubSpot Workflows'), and "
            "its extensibility line names PATCH /v1/workspaces/{id}. None of them is a step "
            "of this workflow's flow, and none of them can be exercised in a test."
        ),
        "value": {
            "outbound_crm_calls": "not built; each action records what it would write",
            "outbound_webhook_delivery": "not built; the alternative path is recorded as `via`",
            "lead_scoring": "a different workflow in the same research file (section 14), not built",
            "dynamic_workspace_sections": "a vendor capability named in the extensibility line, not a step here",
            "unknown_payload_keys": "kept, not dropped - the schema-flexibility rule applied to this package's own validator",
        },
        "why": (
            "A function that opens a socket to an API it has no credentials for is not a "
            "feature; it is a function that would fail in production and pass review. The "
            "action plan is the verifiable half, and it is what a team with a real client "
            "needs to add an executor against."
        ),
        "change_it": "Nothing to change: this entry is the record of the boundary.",
        "blast_radius": "What an enrollment says it did, which is `executed: false` everywhere.",
    },
)


def by_id(identifier: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == identifier:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole register, served.

    The sourced half comes back beside the inferred half, because the point of the
    endpoint is to see where the line falls.
    """
    return {
        "sourced_quote": SOURCED_QUOTE,
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "sourced": {
            "filter_families": list(FILTER_FAMILIES),
            "refinement_matrix": {family: list(names) for family, names in REFINEMENTS.items()},
            "enrollment_types": list(ENROLLMENT_TYPES),
            "trigger_modes": list(TRIGGER_MODES),
            "delivery_paths": list(DELIVERY_PATHS),
            "lifecycle_stages": list(LIFECYCLE_STAGES),
            "lifecycle_constraint": (
                "When you include the `lifecyclestage` property, you can only set the value "
                "*forward* in the stage order."
            ),
            "action_families_for_this_product": dict(ACTION_FAMILIES),
        },
    }
