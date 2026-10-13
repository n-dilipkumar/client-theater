"""Every judgement call this workflow made, with what it rejected.

The specification says of itself: "This spec does not state the flow as separate
fields. The evidence below is the specification. An implementer who needs a flow
the evidence does not contain must derive it and record the derivation, not assume
it."

So this module is the record. Each entry carries the evidence it rests on, the
reading it rejected, what that reading would have cost, and the residual risk. The
feature module serves them at ``GET /api/wf-068/decisions``, because a record
nobody can read is not a record.

Fifteen entries. The issues the ticket raises explicitly are all here, each with
the line of the issue it answers.
"""

from __future__ import annotations

from typing import Any

#: One decision. Keys are fixed so a client can render every entry the same way.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "wf068-own-crm-collections",
        "topic": "where the account, contact, deal and lead records come from",
        "question": (
            "No shipped collection holds a CRM Account, Contact, Deal or Lead. "
            "Should this workflow own its own records, read another feature's "
            "collections, or take the entity payload from the caller?"
        ),
        "decision": (
            "This workflow owns wf068_crm_account, wf068_crm_contact, "
            "wf068_crm_deal and wf068_crm_lead."
        ),
        "evidence": (
            "The issue requires the source to be confirmed before a dependency is "
            "taken. A survey found no collection named account, contact, deal, "
            "lead or crm_* holding any of them; backend/dsr/crm holds "
            "crm_automation, crm_subscription, crm_event, crm_activity and "
            "crm_field, and backend/dsr/crm_upsert holds capability, connection, "
            "engagement and run rows."
        ),
        "rejected": (
            "Reading another feature's collections, or taking the collection name from the caller."
        ),
        "cost_of_the_rejected_reading": (
            "The researched parent-account gate becomes unenforceable. The issue "
            "says both gates must be refusals rather than warnings, and a gate over "
            "a collection the caller names is a gate over whatever the caller "
            "passed."
        ),
        "residual_risk": (
            "A second feature may later provision CRM entities into the shared "
            "records API, and the two sets would need reconciling. Jev chose this "
            "option at confidence 1.00, audit jev-20261013T111405-12092-45997."
        ),
        "surface": "crm.normalise_record, crm.collection_for",
    },
    {
        "id": "wf068-three-statuses-only",
        "topic": "the flow instance state machine",
        "question": "Which statuses does a flow instance have?",
        "decision": "Running, Pending and Paused, and nothing else.",
        "evidence": (
            "Quoted: flowInstanceStatus enum: Running, Pending, Paused. The issue "
            "adds: Model those three and no others."
        ),
        "rejected": "Adding a Completed or Failed status for a finished sequence.",
        "cost_of_the_rejected_reading": (
            "A fourth value would break the one rule the issue states exactly, and "
            "the research defines no completion step to hang it on."
        ),
        "residual_risk": (
            "The transition graph is derived, because the research names the enum "
            "and not its edges. Every one of the three statuses is reachable from "
            "every other, and each is served so a page can render the control."
        ),
        "surface": "instances.advance, instances.allowed_transitions",
    },
    {
        "id": "wf068-no-terminal-status",
        "topic": "what a finished follow-up sequence looks like",
        "question": "What status does a sequence carry once every touch is sent?",
        "decision": (
            "It stays in whichever of the three statuses it was in. Every instance "
            "view carries terminal: false and a note saying why."
        ),
        "evidence": (
            "The researched enum has three values and none of them describes a finished sequence."
        ),
        "rejected": "Treating all touches delivered as a fourth, terminal status.",
        "cost_of_the_rejected_reading": (
            "A caller reading flowInstanceStatus would see a value the vendor has "
            "never documented and would compare it against a Gong response that "
            "can never contain it."
        ),
        "residual_risk": (
            "Progress is visible only through touches_delivered, which is this "
            "build's field rather than the vendor's."
        ),
        "surface": "instances.status_view",
    },
    {
        "id": "wf068-a-new-instance-starts-pending",
        "topic": "the status a newly enrolled prospect carries",
        "question": "Does a prospect who was enrolled an hour ago read as Running or Pending?",
        "decision": (
            "Pending until the first touch is delivered. A flow whose first touch is "
            "due today or earlier starts Running, because a sequence whose first "
            "email was due this morning has begun."
        ),
        "evidence": (
            "The researched enum has three values. Pending is the only one of the "
            "three that describes a sequence which has not begun, and the research "
            "gives no separate value for one that has."
        ),
        "rejected": "Starting every new instance as Running, which is how the research lists them.",
        "cost_of_the_rejected_reading": (
            "A five-touch sequence enrolled an hour ago would read as Running, "
            "which claims the first touch had been sent. It had not, and the "
            "assignee's queue would show it as owed rather than as due."
        ),
        "residual_risk": (
            "The vendor's assignment response carries Running on the entry the "
            "caller sees, so the two values are reported side by side: "
            "vendor_status for the researched response, status for what this record "
            "is doing."
        ),
        "surface": "instances.initial_status, engine._starting_status",
    },
    {
        "id": "wf068-date-triggers-wait-for-morning",
        "topic": "when a date-based trigger fires",
        "question": "Does creating or editing a date-triggered automation run it?",
        "decision": (
            "No. trigger_matches returns False for a date trigger, and only "
            "due_on_morning_run can enrol a deal. A date automation that has never "
            "been evaluated reports last_evaluated_at as null."
        ),
        "evidence": (
            "Quoted: Date-based triggers run on a daily schedule and are evaluated "
            "each morning. They do not run immediately when an automation is "
            "created or edited."
        ),
        "rejected": (
            "Evaluating a date trigger from the ordinary event path, and treating "
            "a create as an event."
        ),
        "cost_of_the_rejected_reading": (
            "A renewal flow would enrol a buyer the moment an administrator saved "
            "the rule, which is exactly what the research says does not happen."
        ),
        "residual_risk": (
            "The hour of the morning run is derived, because the research says each "
            "morning and names no time. It is served as "
            "date_trigger_evaluation_hour so a page can show it."
        ),
        "surface": "automations.due_on_morning_run, automations.trigger_matches",
    },
    {
        "id": "wf068-per-prospect-failure",
        "topic": "a failure inside a 200-prospect batch",
        "question": "Does one bad prospect fail the whole batch?",
        "decision": (
            "No. One unknown id or one contact with no parent account becomes an "
            "entry in prospectsNotAssigned and the rest of the batch is assigned."
        ),
        "evidence": (
            "Quoted: Failures are returned per-prospect with an error code rather "
            "than failing the batch. The response splits prospectsAssigned[] from "
            "prospectsNotAssigned[]."
        ),
        "rejected": "Raising on the first bad prospect, which is the usual shape.",
        "cost_of_the_rejected_reading": (
            "A caller would have to rebuild a batch of two hundred because of one "
            "bad id, and could not tell which one was bad without bisecting the "
            "request."
        ),
        "residual_risk": (
            "Two request-level faults, an unlicensed assignee and a personal flow, "
            "refuse the whole batch. Both are properties of the request rather "
            "than of a prospect, and the vendor's documentation does not say which "
            "way each is reported."
        ),
        "surface": "flows.assign",
    },
    {
        "id": "wf068-gates-are-refusals",
        "topic": "the two researched permission gates",
        "question": "Should an unlicensed assignee or a parentless contact warn or refuse?",
        "decision": "Both refuse. The assignee is 403 and the contact is 409.",
        "evidence": (
            "Quoted: The selected user must have an Engage license. If they don't, "
            "the automation won't run. And: If the automation selects a contact "
            "without a parent account, the automation will fail to run. The issue "
            "says: Implement both as refusals, not as warnings."
        ),
        "rejected": "Recording the problem on the automation and letting it run.",
        "cost_of_the_rejected_reading": (
            "The failure is silent. A seller would find out when a prospect heard "
            "nothing, which is the outcome the research is warning about."
        ),
        "residual_risk": (
            "The parent-account check reads this workflow's own contact records, so "
            "a contact created by another route is not checked here."
        ),
        "surface": "crm.assert_engaged, crm.assert_parent_account",
    },
    {
        "id": "wf068-personal-flows-refused",
        "topic": "personal flows",
        "question": "May a personal flow be used with an automation?",
        "decision": "No. Creating the automation or running the assignment is refused with 409.",
        "evidence": "Quoted: Personal flows cannot be used with automations.",
        "rejected": "Accepting the pairing and marking the automation as ineligible.",
        "cost_of_the_rejected_reading": (
            "A saved automation that can never enrol anybody reads on the page as a working rule."
        ),
        "residual_risk": "None identified.",
        "surface": "crm.assert_not_personal",
    },
    {
        "id": "wf068-cadence-and-modification-date",
        "topic": "the CRM sync cadence",
        "question": "How often may a sync run, and what must an update carry?",
        "decision": (
            "A cadence outside one to five minutes is refused. modificationDate is "
            "required on an update and refused on a create."
        ),
        "evidence": (
            "Quoted: we recommend updating between every 1-5 minutes, and "
            "modificationDate is mandatory on updates."
        ),
        "rejected": "Recording an out-of-range cadence as a warning on the run.",
        "cost_of_the_rejected_reading": (
            "A run at a cadence nobody recommended would read as a sync this "
            "workflow performed under the vendor's guidance."
        ),
        "residual_risk": (
            "The research says recommend, so refusing outside the range is stricter "
            "than the sentence requires. A team that needs a different cadence has "
            "to change the rule rather than bypass it."
        ),
        "surface": "crm.validate_sync_run",
    },
    {
        "id": "wf068-summary-assembled-not-generated",
        "topic": "the pre-meeting summary",
        "question": "What produces the objectives, key topics and suggested questions?",
        "decision": (
            "The summary is assembled from records already in the store, and every "
            "prep page records summary_source as assembled_from_store."
        ),
        "evidence": (
            "The issue states: No summarisation model is named. Derive the source of "
            "the summary and record the derivation."
        ),
        "rejected": (
            "Calling a model to generate the summary, or inventing text to fill the six sections."
        ),
        "cost_of_the_rejected_reading": (
            "Generated text would be a claim about a buyer's intentions with no "
            "source behind it, and a rep would act on it in the room."
        ),
        "residual_risk": (
            "A page assembled from records is only as good as what the seller "
            "recorded. The six researched sections stay empty until it is."
        ),
        "surface": "prep.normalise_page, prep.page_view",
    },
    {
        "id": "wf068-dry-run-is-derived",
        "topic": "the dry run",
        "question": "What does a Dry run actually do, given no provider is named?",
        "decision": (
            "It builds an AI participant from the stored meeting context, records "
            "the chosen voice against three accepted labels, and returns a "
            "checklist of feedback over the prep sections. Nothing is synthesised."
        ),
        "evidence": (
            "Quoted: rehearse with an AI participant built from the meeting context, "
            "chooses a voice, gets feedback, and iterates with Go again. The issue "
            "states: No voice or model provider is named. Record what you build and "
            "what you leave out."
        ),
        "rejected": "Naming a speech vendor and calling it.",
        "cost_of_the_rejected_reading": (
            "A demo would show a plausible spoken rehearsal that never happened, "
            "which is worse than one that says it is derived."
        ),
        "residual_risk": (
            "The three voice labels are this build's, not the vendor's. A team with "
            "a real catalogue adds them to DRY_RUN_VOICES."
        ),
        "surface": "prep.dry_run_participant, prep.dry_run_feedback",
    },
    {
        "id": "wf068-ask-anything-answers-from-context",
        "topic": "Ask anything",
        "question": "The research gives Ask anything a name and no behaviour. What does it do here?",
        "decision": (
            "It records the question and answers from the prep sections, naming "
            "every section it used. A question no section bears on returns the list "
            "of sections rather than an invented answer."
        ),
        "evidence": "The issue states: Ask anything ... has no documented behaviour. Derive it or leave it out, and say which.",
        "rejected": "Leaving the feature out, and building a retrieval system over documents the evidence never mentions.",
        "cost_of_the_rejected_reading": (
            "Building it would invent a corpus. Leaving it out would drop a named "
            "capability from the prep page for no stated reason."
        ),
        "residual_risk": (
            "The answer is keyword matching, not comprehension. A page must say so, "
            "and the answer object does."
        ),
        "surface": "prep.answer",
    },
    {
        "id": "wf068-mobile-omits-the-feed",
        "topic": "the recent-activity feed",
        "question": "Does the prep page differ between the desktop and the mobile app?",
        "decision": "Yes. page_view drops recent_activity when mobile is true and says why.",
        "evidence": (
            "Quoted: The meeting prep page appears slightly differently in the "
            "mobile app and does not display recent activities."
        ),
        "rejected": "Serving an empty feed to a mobile client.",
        "cost_of_the_rejected_reading": (
            "An empty feed reads as a rep with no recent activity. The absence is "
            "a product decision, not a fact about the account."
        ),
        "residual_risk": "The flag is chosen by the caller, so a client that does not set it gets the desktop page.",
        "surface": "prep.page_view",
    },
    {
        "id": "wf068-vendor-attribution",
        "topic": "which vendor supplies what",
        "question": "The evidence mixes two vendors. How is each capability attributed?",
        "decision": (
            "Gong supplies the prep page, the dry run, the Engage flows, the CRM "
            "upload and the digital interactions. Cal supplies the follow-up "
            "webhooks, the recording downloads and the afterEvent workflows. The "
            "to-do queue, Ask anything and the Forecast reminders belong to neither."
        ),
        "evidence": (
            "The issue states: Record which vendor each capability comes from. Gong "
            "supplies the prep page, the flows and the CRM upload. Cal supplies the "
            "follow-up webhooks and recording download links."
        ),
        "rejected": "One undifferentiated vendor label on every request.",
        "cost_of_the_rejected_reading": (
            "A reader comparing our refusal with a vendor's would not know which "
            "codebook the code came from."
        ),
        "residual_risk": (
            "The Gong and Cal web APIs are cited in full and this product is a "
            "client of neither, so both are recorded as request shapes and sent."
        ),
        "surface": "vocabulary.VENDORS, crm.entities_request, cal.after_event_workflow",
    },
    {
        "id": "wf068-slack-left-out",
        "topic": "the Gong Forecast reminders",
        "question": "Are the Forecast reminder timings implemented?",
        "decision": (
            "The in-app 24-hour reminder is modelled. The two Slack rows are "
            "refused at validation, because the product has no confirmed Slack "
            "surface."
        ),
        "evidence": (
            "Quoted: in-app 24h before due, Slack 1h before to AEs, Slack summary 2h "
            "after to managers. The issue states: The product has no confirmed "
            "Slack surface. Record what you left out."
        ),
        "rejected": "Storing a Slack reminder that posts nowhere.",
        "cost_of_the_rejected_reading": (
            "A reminder that looks armed and delivers nothing is a rule an operator "
            "will trust in and cannot verify."
        ),
        "residual_risk": (
            "A seller who wants the Slack rows has no way to configure them until "
            "the product has a Slack surface."
        ),
        "surface": "reminders.validate_reminder, vocabulary.FORECAST_CHANNELS",
    },
    {
        "id": "wf068-tracker-quote-as-written",
        "topic": "the tracker trigger quote",
        "question": "Is the duplicated AI in the trigger quote corrected?",
        "decision": "No. It is reproduced exactly as written, beside a note that says so.",
        "evidence": (
            "The issue states: One evidence line contains a typo in the source. "
            "Quote it as written if you cite it."
        ),
        "rejected": "Silently correcting it to one AI.",
        "cost_of_the_rejected_reading": (
            "A reviewer matching this build against the evidence would not find the "
            "line they were quoted."
        ),
        "residual_risk": (
            "A reader who does not read the note may take the duplication for a "
            "mistake in this build. That is the cost the issue asked for."
        ),
        "surface": "vocabulary.TRACKER_QUOTE_WITH_TYPO",
    },
)


def describe() -> list[dict[str, Any]]:
    """Every decision, as a list a route can serve."""
    return [dict(entry) for entry in INFERENCES]


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One decision by its id, or ``None``."""
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe_one(inference_id: str) -> dict[str, Any]:
    """One decision by id, as a route answers it.

    Raises :class:`~dsr.meeting_followup.errors.AutomationInvalid` for an unknown
    id rather than returning a 404 body, so the caller sees the same shape for a
    bad decision id as for a bad automation id.
    """
    from dsr.meeting_followup.errors import AutomationInvalid

    found = by_id(inference_id)
    if found is None:
        raise AutomationInvalid(
            f"{inference_id!r} is not a recorded decision",
            {"decision_id": f"one of: {', '.join(e['id'] for e in INFERENCES)}"},
        )
    return found
