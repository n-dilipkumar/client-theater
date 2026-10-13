"""Every judgement call the research left open, named and published over HTTP.

The research is the specification, and it is silent or ambiguous in six places.
Each entry records the question, the choices, the choice this build makes, and
whether a source backs it. A reader can disagree with a choice and see exactly
what it costs, because the choice and the reason sit together.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "default_backfill_enrols_existing_matches",
        "question": "Which publish-panel choice is the default?",
        "options": ["existing_and_future", "future_only"],
        "choice": "existing_and_future",
        "basis": "inferred",
        "quote": (
            "[sourced] Yes, enroll existing objects which meet the trigger criteria as of "
            "now; or No, only enroll objects which meet the trigger criteria after turning "
            "the workflow on."
        ),
        "consequence": (
            "Publishing without stating a choice backfills the records that already match. "
            "A team that wants the future-only branch states it at publish time, and the "
            "same records are still enrolled by the next scheduled run because deferral is "
            "not exclusion. Jev returned uncertain on this default at confidence 0.40 "
            "(audit jev-20261013T221927-15252-67007), selecting future_only at p=0.70, so "
            "the value is a recorded choice and a caller can always state the other one."
        ),
        "jev": "jev-20261013T221927-15252-67007",
    },
    {
        "id": "reenrollment_is_after_completion_only",
        "question": "What does the Re-enroll toggle turn on?",
        "options": ["single", "after_completion", "each_match"],
        "choice": "after_completion",
        "basis": "sourced",
        "quote": (
            "[sourced] Turning on re-enrollment does not automatically enroll records into "
            "the workflow multiple times. A record can only re-enroll if it has completed "
            "the workflow, and is enrolled again."
        ),
        "consequence": (
            "Re-enroll on means a completed record may enrol again; it does not mean a "
            "record re-enrols on every match. A record that is currently enrolled is "
            "refused in both modes, which is the sentence the source states directly."
        ),
    },
    {
        "id": "an_unenrolled_record_may_re_enroll",
        "question": "May a record removed by the unenrollment criteria re-enroll?",
        "options": ["may_re_enroll", "never_again"],
        "choice": "may_re_enroll",
        "basis": "inferred",
        "quote": (
            "[sourced] Optionally set unenrollment criteria. ... the record's re-enrollment "
            "and unenrollment state and enrollment history are written to the workflow log."
        ),
        "consequence": (
            "Unenrollment removes a record from the workflow; it does not complete it. With "
            "Re-enroll on, a record that leaves by the unenrollment criteria and later "
            "matches again is enrolled again. With Re-enroll off it is not, because the "
            "single-enrollment rule still holds."
        ),
    },
    {
        "id": "the_delay_is_a_per_schedule_setting",
        "question": "How long is an enrolled record held in the queue, and in what unit?",
        "options": ["per_schedule_minutes", "fixed", "no_queue"],
        "choice": "per_schedule_minutes",
        "basis": "inferred",
        "quote": (
            "[sourced] Each enrolled record is held in an enrollment queue for the "
            "configured delay, then the actions fire in order."
        ),
        "consequence": (
            "A schedule carries a delay in whole minutes, defaulting to zero. A queued "
            "enrollment is released by the scheduled run whose instant has passed the "
            "delay, so the actions fire on a run rather than on a timer nobody can see."
        ),
    },
    {
        "id": "deferral_is_not_exclusion",
        "question": "Are the records that match at publish time excluded when the panel says no?",
        "options": ["deferred_then_eligible", "excluded_permanently"],
        "choice": "deferred_then_eligible",
        "basis": "sourced",
        "quote": (
            "[sourced] For scheduled workflows, if you choose not to enroll existing records "
            "at activation, those records are not enrolled immediately, but they can still be "
            "enrolled the next time the workflow runs on its schedule if they still meet the "
            "enrollment criteria."
        ),
        "consequence": (
            "A future-only publish enrols nobody and records the number of records it "
            "deferred. The next scheduled run enrols every record that matches, which is "
            "what 'deferred, not excluded' means in state."
        ),
    },
    {
        "id": "enrollment_is_a_sequence_of_audited_writes",
        "question": "Is one enrollment one transaction?",
        "options": ["single_transaction", "sequence_of_audited_writes"],
        "choice": "sequence_of_audited_writes",
        "basis": "inferred",
        "quote": (
            "[sourced] the record's re-enrollment / unenrollment state and enrollment history "
            "are written to the workflow log"
        ),
        "consequence": (
            "The enrollment row, the marker on the CRM record, the workflow log line, the "
            "task (written through WF-116's engine, which opens its own transaction) and the "
            "internal notification commit as audited steps. A step that fails is recorded on "
            "the run as failed, the enrollment stays queued, and the next run retries it "
            "rather than reporting a false completion."
        ),
    },
)

_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in INFERENCES}


def by_id(inference_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(inference_id)


def describe_inferences() -> list[dict[str, Any]]:
    return [dict(entry) for entry in INFERENCES]


__all__ = ["INFERENCES", "by_id", "describe_inferences"]
