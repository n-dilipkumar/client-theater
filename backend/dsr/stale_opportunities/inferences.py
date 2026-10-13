"""Every judgement call the research left open, named and published over HTTP.

The research is the specification, and it is silent or ambiguous in five places.
Each entry records the question, the choices, the choice this build makes, and
whether a source backs it. A reader can disagree with a choice and see exactly
what it costs, because the choice and the reason sit together.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "stale_reference_is_latest_of_both",
        "question": "What is inactivity measured against?",
        "options": ["last_activity", "stage_entry", "latest_of_both"],
        "choice": "latest_of_both",
        "basis": "inferred",
        "quote": (
            "[sourced] Opportunity record (stage, date entered stage, last activity "
            "date, value, owner) -> stale-rule evaluation finds records exceeding the "
            "inactivity threshold"
        ),
        "consequence": (
            "Only an opportunity idle in both senses is stale. An opportunity that "
            "moved stage or had a recent activity is not swept. The two fields the "
            "data flow names are the only evidence, so neither is ignored."
        ),
        "jev": "jev-20261013T192612-13876-72068",
    },
    {
        "id": "refire_control_is_a_marker_on_the_opportunity",
        "question": "How is the researched Remove from Workflow modelled?",
        "options": ["marker_on_opportunity", "separate_ledger_row", "no_control"],
        "choice": "marker_on_opportunity",
        "basis": "inferred",
        "quote": (
            "[sourced] Remove from Workflow ... Removes contacts from a workflow ... "
            "so the sweep does not re-fire on the same record."
        ),
        "consequence": (
            "The escalation writes an instant onto the opportunity row through the "
            "researched Create/Update Opportunity action. A later sweep skips the "
            "opportunity until its own reference instant moves past that marker, "
            "which is what 'does not re-fire on the same record' means when the "
            "record can change."
        ),
        "jev": "jev-20261013T192612-13876-72068",
    },
    {
        "id": "cadence_is_a_per_sweep_setting",
        "question": "Is the nudge/escalate cadence fixed or configurable?",
        "options": ["fixed_7_21", "per_sweep_setting"],
        "choice": "per_sweep_setting",
        "basis": "inferred",
        "quote": (
            "[sourced] add a Wait step to add a graduated cadence (e.g. nudge at 7 "
            "days, escalate at 21 days)."
        ),
        "consequence": (
            "A sweep carries an ordered cadence. The 7/21-day example is the "
            "default; a team that wants a different cadence sets it without a code "
            "change."
        ),
    },
    {
        "id": "unknown_value_fails_a_value_band",
        "question": "Does an opportunity with no value pass a value-band filter?",
        "options": ["pass", "fail"],
        "choice": "fail",
        "basis": "inferred",
        "quote": "[sourced] optional If/Else branch on stage/value",
        "consequence": (
            "An opportunity whose value is unknown cannot be shown to be inside a "
            "band, so it is reported as value_filtered rather than swept. The stage "
            "filter behaves the same way when the filter names stages and the "
            "opportunity has none."
        ),
    },
    {
        "id": "no_evidence_is_reported_not_swept",
        "question": "What happens to an opportunity with neither field set?",
        "options": ["treat_as_stale", "report_and_skip"],
        "choice": "report_and_skip",
        "basis": "inferred",
        "quote": "",
        "consequence": (
            "An opportunity with neither a last-activity date nor a stage-entry date "
            "is returned by an evaluation with reason no_activity_evidence and is "
            "never escalated. The sweep does not invent the evidence its rule reads."
        ),
    },
    {
        "id": "escalation_is_a_sequence_of_audited_writes",
        "question": "Is one escalation one transaction?",
        "options": ["single_transaction", "sequence_of_audited_writes"],
        "choice": "sequence_of_audited_writes",
        "basis": "inferred",
        "quote": (
            "[sourced] Opportunity field or pipeline-stage write + Task creation for "
            "the owner + internal notification to the rep's team"
        ),
        "consequence": (
            "The owner task (written through WF-116's engine, which opens its own "
            "transaction), the notification and the opportunity marker commit as "
            "audited steps, and the marker is written last. The audited wrapper's "
            "transaction cannot nest the task engine's own, so a step that fails is "
            "recorded on the run as failed, the opportunity stays eligible, and the "
            "next run retries it rather than reporting a false success."
        ),
    },
)

_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in INFERENCES}


def by_id(inference_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(inference_id)


def describe_inferences() -> list[dict[str, Any]]:
    return [dict(entry) for entry in INFERENCES]


__all__ = ["INFERENCES", "by_id", "describe_inferences"]
