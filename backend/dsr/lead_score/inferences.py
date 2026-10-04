"""Every judgement call this package makes, in one inspectable place.

The research for WF-029 is unusually specific about the *surface* of this workflow:
it names the score property, the two buckets, the five Dock activity properties, the
filters worth setting on each, the two CRM scopes, four CRM endpoints and the
lifecycle-stage constraint. What it does not do is say how a *sender* or a *run* of
this build behaves on the edges of that description, and the edges are where a build
has to decide something.

Those decisions are collected here rather than left as comments in function bodies,
because a judgement call in a comment is one nobody re-reads and a wrong one becomes
product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-029/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

Three entries are not inferences but **decisions**, and they are marked with a
``jev_audit`` field: they were put to Jev before the code was written, and the audit
id is recorded so the reasoning survives the merge. One entry is a boundary rather
than a decision, and is listed for that reason: ``not-built`` records what this build
deliberately does not do, because a feature whose page does not show its own edges
overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.lead_score.vocabulary import (
    BASELINE_QUOTE,
    BUCKETS,
    FAMILY_LABEL,
    FILTER_FAMILIES,
    LIFECYCLE_CONSTRAINT,
    MAP_TASK_QUOTE,
    REFINEMENTS,
    REQUIRED_SCOPES,
    SCORE_PROPERTY,
)

#: The sentence from the research that governs most of the surface below.
SOURCED_QUOTE = (
    "Within HubSpot, go to your settings. Go to the Contact property. Search for HubSpot "
    "score. Click Add criteria for either positive or negative scores; Scroll down until you "
    "see the Dock options in the lead score system. Choose the Dock property you want to "
    "score against. Setup filters and assign score."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "score-is-recomputed-from-the-whole-history",
        "topic": "whether a score accumulates per event or is recomputed from history",
        "topic_note": "the largest inference in the package, and the one a seller feels",
        "jev_audit": "jev-20261004T024258-6152-78859",
        "basis": (
            "Sourced, and pulling two ways: the data flow says a matching event is "
            "'added/subtracted from the HubSpot Score contact property', which reads as an "
            "increment, and the automation note says 'every matching Dock activity event "
            "re-evaluates the score without user action', which reads as a recomputation. The "
            "sources do not say which, and the product behaviour differs between them."
        ),
        "value": {
            "model": "recompute_from_history",
            "total": "the sum over every criterion of its score times the number of matching "
            "events in the contact's history, signed by the bucket",
            "idempotent": True,
            "alternative": "accumulate_per_event, with a ledger of applied movements",
        },
        "why": (
            "'Re-evaluates' is the operative word and it is the one the automation note uses. "
            "A recomputation is also the only reading under which two things a seller will "
            "eventually try both work: replaying a webhook does not inflate a score, and "
            "withdrawing a criterion takes its points off the contact at the next event instead "
            "of leaving a claim nothing backs. Accumulation was offered to Jev as the "
            "alternative and scored 0.02 against 0.98."
        ),
        "change_it": (
            "contributions_for() and recompute() in dsr/lead_score/scoring.py. Swap the body "
            "for a per-event ledger read and write, and the run's from_score becomes the "
            "ledger total rather than the stored score."
        ),
        "blast_radius": "Whether a replayed webhook moves a score, and whether a withdrawn criterion's points survive.",
    },
    {
        "id": "no-outbound-crm-call",
        "topic": "whether this build calls HubSpot or records what it would call",
        "jev_audit": "jev-20261004T024259-6152-79173",
        "basis": (
            "Sourced: the endpoints ('PATCH /crm/v3/objects/contacts/{contactId}', 'POST "
            "/crm/v3/objects/contacts/batch/upsert', 'POST "
            "/crm/v4/associations/{fromObjectType}/{toObjectType}/labels', 'POST "
            "/crm/v3/properties') and the scopes. Not available: a credential. The issue "
            "records WF-034 as the dependency that provisions the token, and this branch "
            "carries no token."
        ),
        "value": {
            "outbound_calls": "not built",
            "instead": "each run carries the request bodies the endpoints would receive, with executed=false",
            "reason": "an audit row must be deterministic, and a network call inside a route would make it depend on a third party",
        },
        "why": (
            "A function that opens a socket to an API it has no credentials for is not a "
            "feature; it is a function that would fail in production and pass review. The "
            "request plan is the verifiable half, and it is what a team with a real HubSpot "
            "client needs in order to add an executor without a migration."
        ),
        "change_it": (
            "crm_plan_for() and batch_plan_for() in scoring.py. Send their bodies through a "
            "transport and the shape of every run response stays the same."
        ),
        "blast_radius": "What a run says it did, which is `executed: false` everywhere.",
    },
    {
        "id": "self-contained-domain-package",
        "topic": "why this package does not import the sibling WF-030 package",
        "jev_audit": "jev-20261004T024139-28028-99762",
        "basis": (
            "WF-030 already ships dsr/crm_workflows with the same five Dock properties, the "
            "same filter matrix and a criterion matcher, because both workflows read the same "
            "integration's activity properties. WF-030's own inferences register states that "
            "lead scoring is 'a different workflow in the same research file (section 14), not "
            "built', so the two do not overlap in behaviour. The feature contract says a "
            "feature 'must not import another feature'."
        ),
        "value": {
            "reused_from_wf030": "nothing",
            "duplicated_on_purpose": "the five properties, the filter matrix, the activity reader",
            "cost": "roughly 300 lines of near-duplicate vocabulary and event reading",
        },
        "why": (
            "A criterion that scores a contact because WF-030's matcher changed is a defect "
            "nobody can find from either diff. Jev chose self_contained over reuse at 0.97 "
            "against 0.02. The duplication is the isolation, and it is paid for deliberately."
        ),
        "change_it": "Nothing: this entry is the record of the boundary.",
        "blast_radius": "Whether a change to one feature can alter the other's behaviour.",
    },
    {
        "id": "text-filters-are-exact-and-none-is-a-substring",
        "topic": "how the link name, file name and task name are compared",
        "basis": (
            "The research says the four analytics events can be refined 'around the link name "
            "or file name' and MAP activity 'by task name to give certain tasks more weight "
            "than others'. It publishes no operator, no case rule, and no partial-match "
            "option."
        ),
        "value": {
            "link_name": "exact and case-sensitive, after trimming",
            "file_name": "exact, case-insensitive, whitespace collapsed",
            "task_name": "the quoted name out of a 'completed task ...' sentence, then as above",
            "substring": "not supported",
            "fallback": "text outside the documented shape is compared whole",
        },
        "why": (
            "A URL is an identifier and its path is case-sensitive, so comparing it loosely "
            "would match a different resource. A file name and a task name are human-authored "
            "labels spelled inconsistently - the research's own MAP examples differ in "
            "capitalisation - so case-insensitive exact is the comparison that behaves the way "
            "a person expects. Substring matching is the tempting one and the wrong one: it "
            "would make a criterion on 'Pricing One-Pager' also match 'Pricing One-Pager 2024', "
            "which is a different file, and the sources publish no such option. The "
            "whole-text fallback means a literal this product never documented still works "
            "rather than being refused for being unfamiliar."
        ),
        "change_it": "The link_name, file_name and task_name branches of _check_refinement() in criteria.py.",
        "blast_radius": "Which click, download and MAP-activity events a criterion scores.",
    },
    {
        "id": "unverifiable-filter-does-not-match",
        "topic": "what happens when a criterion filters on a field the event does not carry",
        "basis": (
            "The research names the filters (date, link name, file name, task name) and says "
            "the rule is continuous. It does not say what to do with an event that carries none "
            "of the fields a criterion filters on, which is a real case because activity "
            "payloads are arbitrary JSON in this product."
        ),
        "value": {
            "verdict": "does not match",
            "reason": "refinement_unverifiable",
            "reported_separately_from": "a plain miss",
        },
        "why": (
            "A score change needs positive evidence. Scoring a contact because the event "
            "happened to omit a field would let a 'score a contact who downloads the pricing "
            "pack' rule fire on a click. Failing silently would be the other bad outcome: a "
            "team that has not yet wired link_name onto its activity rows would see a score "
            "that quietly never moves, with no way to find out why. Reporting the reason "
            "separately from a miss is what makes the edge debuggable."
        ),
        "change_it": (
            "The unverifiable branch of matches() in criteria.py, and the "
            "'refinement_unverifiable' entry in REASONS next to it."
        ),
        "blast_radius": "Whether a contact's score moves, and the reason shown for it.",
    },
    {
        "id": "occurred-is-a-recommendation-not-a-requirement",
        "topic": "whether a criterion without an Occurred filter is refused or warned about",
        "basis": (
            "Sourced, and the verb matters: 'we recommend using the Occurred filter as a "
            "baseline'. The research never says a criterion must carry one, and its examples "
            "of adding refinement are additions to a baseline rather than substitutes for it."
        ),
        "value": {
            "missing_baseline": "warning, criterion still saves",
            "missing_property": "refused",
        },
        "why": (
            "A refusal here would block something the sources permit, and the sources are the "
            "specification. The warning is on the response and on the stored criterion, so the "
            "person who saved it can see that the criterion scores activity of any age, "
            "including events from before the workspace was connected to the deal."
        ),
        "change_it": "The 'no_occurred_filter' branch of lint_criterion(), and parse_criterion().",
        "blast_radius": "Whether a criterion without a date filter can be saved.",
    },
    {
        "id": "score-value-is-a-magnitude-and-the-bucket-carries-the-sign",
        "topic": "why a negative score number is refused",
        "basis": (
            "The researched order is bucket first, then value: 'Click Add criteria for either "
            "positive or negative scores' and then 'Setup filters and assign score'. The "
            "sources never show a negative number."
        ),
        "value": {
            "score": "an integer above zero",
            "sign": "read from the bucket",
            "zero": "refused",
        },
        "why": (
            "Accepting a negative number as well would let one criterion be stored two ways, and "
            "a reader of the criterion list could not tell which bucket it would score into "
            "without opening it. Zero is refused because a criterion that moves no points is a "
            "row that looks armed and does nothing."
        ),
        "change_it": "parse_score_value() in criteria.py.",
        "blast_radius": "Which score values a criterion can carry.",
    },
    {
        "id": "there-is-no-score-floor-unless-one-is-set",
        "topic": "whether the score is clamped at zero",
        "basis": (
            "The sources describe adding and subtracting points and say nothing about a floor. "
            "The two buckets are the whole of the negative side, and a negative criterion on a "
            "contact already at zero is exactly the case a floor would silently swallow."
        ),
        "value": {
            "default_minimum": None,
            "meaning": "no floor; the score is the signed total",
            "setting": "minimum_score on the CRM organisation, unset by default",
        },
        "why": (
            "Inventing a floor would make every negative criterion a no-op for a cold contact "
            "and no response would say so. Leaving the setting unset keeps the behaviour the "
            "sources describe, and putting it on the organisation makes the floor a visible "
            "choice somebody made rather than a hidden default."
        ),
        "change_it": "The minimum_score argument of recompute() in scoring.py.",
        "blast_radius": "Whether a contact's score can go below zero.",
    },
    {
        "id": "custom-score-property-is-stored-not-refused",
        "topic": "what happens when a criterion writes to a contact property this build does not provision",
        "basis": (
            "Sourced: 'In HubSpot, lead scoring is automatically created as a contact property "
            "as HubSpot Score' and, from the extensibility note, 'Third parties can add their own "
            "criteria by writing custom contact properties via POST /crm/v3/properties'. The "
            "issue states the ownership question is open: 'Whether this plugin owns that write "
            "path or only consumes it is not stated.'"
        ),
        "value": {
            "default_property": SCORE_PROPERTY,
            "other_property": "stored, computed, and reported as property_resolved=false",
            "this_build_writes": "the researched property only",
            "third_party_write_path": "consumed, not owned",
        },
        "why": (
            "The answer is consume-and-report. Owning POST /crm/v3/properties would mean this "
            "feature creating contact properties in somebody's CRM, which is a platform "
            "capability and not a step in this workflow's flow. Refusing the name instead would "
            "break the extensibility the research promises. The points are still computed and "
            "still shown, and the criterion carries a warning saying no property of that name "
            "is written by this build - which is the honest state of it."
        ),
        "change_it": "require_score_property() in vocabulary.py and the lint warning.",
        "blast_radius": "Whether a criterion naming a third-party property is kept or refused.",
    },
    {
        "id": "missing-dock-property-matches-nothing",
        "topic": "what a criterion does when no Dock property has been provisioned",
        "basis": (
            "The issue records WF-036 as a dependency and states the consequence: 'Without it, "
            "no criterion in the user flow has a property to score against, so every rule "
            "matches nothing and the score never moves.'"
        ),
        "value": {
            "criterion_against_an_unprovisioned_property": "saved, and scores nothing",
            "report": "the run names the missing property as the reason",
            "not": "a refusal",
        },
        "why": (
            "Refusing to save would mean a seller could not prepare their criteria before "
            "provisioning arrives, which is the order the research describes anyway: step 1 is "
            "about the integration and the properties come from the engagement object. The "
            "consequence the issue describes - a score that never moves - is reported per run "
            "so it is findable from the score list rather than from a support ticket."
        ),
        "change_it": "The property lookup in engine._criteria(), and the run's findings list.",
        "blast_radius": "Whether a run says why a criterion matched nothing.",
    },
    {
        "id": "save-is-arm-there-is-no-publish-step",
        "topic": "whether a saved criterion needs publishing before it scores",
        "basis": (
            "The flow ends at step 6: 'Save; subsequent buyer activity in the DSR moves the "
            "contact's score automatically.' No publish step is described, and the automation "
            "note says the rule is continuous. The sibling workflow WF-030 does describe a "
            "publish step, which is why the absence here is a decision rather than an omission."
        ),
        "value": {"publish_step": "none", "armed_on": "save", "amendable_while_armed": True},
        "why": (
            "Adding a publish step would gate a rule the research says starts working the "
            "moment it is saved. Being amendable while armed follows from recomputation: the "
            "score is derived, so an edit takes effect at the next event and needs no "
            "republication step to make it true."
        ),
        "change_it": "engine.add_criterion() and engine.amend_criterion() carry no status check.",
        "blast_radius": "Whether a saved criterion is scoring.",
    },
    {
        "id": "disabled-crm-org-refuses-a-criterion",
        "topic": "what happens when a criterion is saved against a switched-off integration",
        "basis": (
            "Step 1: 'Confirm the Dock and HubSpot integration is enabled and that the "
            "workspace is connected to a deal/account'. The research makes the check a step; it "
            "does not say what happens if it fails."
        ),
        "value": {
            "save": "refused with 409",
            "read": "still reported, so a page can show what is wrong",
            "room_without_a_deal": "reported per run, not refused",
        },
        "why": (
            "Saving would create a criterion that silently never moves a score, and a seller "
            "cannot diagnose that from a criterion list. The connection to a deal is per-room, "
            "so it is reported per evaluation rather than refused: one unconnected room does "
            "not invalidate a criterion for every other room."
        ),
        "change_it": "engine._require_writable_org() raises; the settings route does not.",
        "blast_radius": "Whether a criterion can be saved at all.",
    },
    {
        "id": "a-room-with-no-deal-connection-scores-nobody",
        "topic": "what a room with no deal link does at scoring time",
        "basis": (
            "Step 1 pairs the two conditions: the integration is on *and* the workspace is "
            "connected to a deal/account. The research does not say which of them scoring can "
            "do without."
        ),
        "value": {
            "evaluation": "reports room_not_deal_connected and writes nothing",
            "criteria": "untouched",
            "other_rooms": "unaffected",
        },
        "why": (
            "The score lands on a contact property, and a contact with no deal has no account "
            "for the score to be attributed to - which is also why the research names the "
            "association-label read. Reporting the reason is what makes the gap findable from "
            "the run response."
        ),
        "change_it": "The room-connection check in engine.score().",
        "blast_radius": "Whether a run reports a decision or a refusal.",
    },
    {
        "id": "lifecyclestage-is-never-written-by-a-score-write",
        "topic": "how the forward-only lifecycle constraint is designed around",
        "basis": (
            "Sourced, quoted by the API guide the research cites: 'When you include the "
            "`lifecyclestage` property, you can only set the value *forward* in the stage "
            "order.' The issue lists this as the one constraint the research tells this build "
            "to design around."
        ),
        "value": {
            "written": "nothing",
            "on_request": "reported with the stage-order comparison and the quoted constraint",
            "stages_published": "the eight, in order",
        },
        "why": (
            "A score write has no business moving a lifecycle stage, so the property is never "
            "written. Refusing it with the comparison attached is the part that matters: a "
            "field dropped silently is the failure this codebase exists to avoid, and a caller "
            "who sent the property deserves to know why it did not land."
        ),
        "change_it": "lifecycle_write_verdict() in scoring.py.",
        "blast_radius": "What a caller is told when it sends lifecyclestage with a score.",
    },
    {
        "id": "a-repeated-event-is-ignored-by-key",
        "topic": "what a retried webhook does to a score",
        "basis": (
            "This workflow's research states no idempotency rule. It does name the webhook "
            "source, and a retried webhook is a real case. The sibling WF-030 research states "
            "the rule explicitly for its own emission, and both cite the same vendor reference."
        ),
        "value": {
            "optional": True,
            "rule": "the first event wins; a repeat increments duplicate_attempts on the kept "
            "event and moves nothing",
        },
        "why": (
            "Under recomputation a replay would not inflate the score anyway, which is the main "
            "hazard already closed by the model above. The key still earns its place: the "
            "activity list a reviewer reads should not show the same engagement twice."
        ),
        "change_it": "The idempotency branch in engine.record_activity().",
        "blast_radius": "The activity count, and nothing about the score.",
    },
    {
        "id": "activity-fields-are-read-through-aliases",
        "topic": "why one event may spell a field four ways",
        "basis": (
            "Record payloads are arbitrary JSON by design, and the researched source is a "
            "webhook. Neither fixes the spelling of 'who did this' or 'when'."
        ),
        "value": {
            "read_through": "dsr/lead_score/vocabulary.py FIELD_ALIASES",
            "missing_field": "reported absent, never defaulted to a plausible value",
        },
        "why": (
            "A read that hard-codes one spelling is a read that returns nothing for every team "
            "but the one that wrote the first row. And an absent field must stay absent, because "
            "defaulting it would turn an unanswered question into a score."
        ),
        "change_it": "FIELD_ALIASES in vocabulary.py.",
        "blast_radius": "Which events a criterion can match.",
    },
    {
        "id": "not-built",
        "topic": "what this build deliberately does not do",
        "basis": (
            "The research names the write side ('PATCH /crm/v3/objects/contacts/{contactId}', "
            "'POST /crm/v3/objects/contacts/batch/upsert', 'POST "
            "/crm/v4/associations/{fromObjectType}/{toObjectType}/labels') and the third-party "
            "path ('POST /crm/v3/properties'). It also offers the auto-add behaviour only as an "
            "analogy: 'auto-add will only add companies that enter your saved views after "
            "enabling the auto-add' is described as the analogous HubSpot behaviour for buyer "
            "intent."
        ),
        "value": {
            "outbound_crm_calls": "not built; each run records what it would write",
            "third_party_property_writes": "not owned; a custom property is consumed and reported",
            "auto_add_boundary": "not reproduced; it governs adding companies to a saved view",
            "company_scoring": "not built; the research scores contacts, not companies",
        },
        "why": (
            "The auto-add sentence is the trap here. It is quoted as an analogy, and it "
            "describes a different workflow over companies. Reproducing its watermark rule would "
            "be implementing a feature the research explicitly attributes to something else."
        ),
        "change_it": "Nothing to change: this entry is the record of the boundary.",
        "blast_radius": "What a run says it did, which is `executed: false` everywhere.",
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
        "jev_decisions": [
            {
                "id": entry["id"],
                "audit": entry["jev_audit"],
                "selected": entry["value"],
            }
            for entry in INFERENCES
            if entry.get("jev_audit")
        ],
        "sourced": {
            "score_property": SCORE_PROPERTY,
            "buckets": list(BUCKETS),
            "filter_families": list(FILTER_FAMILIES),
            "family_labels": dict(FAMILY_LABEL),
            "refinement_matrix": {family: list(names) for family, names in REFINEMENTS.items()},
            "required_scopes": list(REQUIRED_SCOPES),
            "baseline_quote": BASELINE_QUOTE,
            "map_task_quote": MAP_TASK_QUOTE,
            "lifecycle_constraint": LIFECYCLE_CONSTRAINT,
        },
    }
