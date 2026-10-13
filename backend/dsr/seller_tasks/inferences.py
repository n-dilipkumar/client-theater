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
        "id": "contact_less_is_required",
        "question": "Is the contact-less task a fallback or a first-class path?",
        "options": ["main path", "fallback"],
        "choice": "The contact-less path is a first-class path.",
        "basis": "sourced",
        "quote": (
            "[sourced] Add Task: ... creates a contact-less task if the workflow is "
            "running without a contact."
        ),
        "consequence": "A create with no contact_id succeeds and records a standalone task.",
    },
    {
        "id": "timestamp_unit",
        "question": "What unit does the due time use?",
        "options": ["unix_ms", "unix_seconds", "iso8601"],
        "choice": "unix_ms",
        "basis": "inferred",
        "quote": ("[sourced] hs_task_reminders: ... You must use Unix timestamp in milliseconds."),
        "consequence": (
            "The due time and the reminder time are both stored as epoch "
            "milliseconds. ISO 8601 copies are stored beside them for reading."
        ),
    },
    {
        "id": "reminder_not_after_due",
        "question": "May a reminder fall after the due time?",
        "options": ["refuse", "allow"],
        "choice": "refuse",
        "basis": "inferred",
        "quote": (
            "[sourced] hs_task_reminders: The timestamp for when to send a reminder "
            "for the due date of the task."
        ),
        "consequence": "A reminder after the due time fails validation with one field error.",
    },
    {
        "id": "date_only_means_midnight_utc",
        "question": "What time does a date-only due string name?",
        "options": ["midnight_utc", "end_of_day_utc", "reject"],
        "choice": "midnight_utc",
        "basis": "inferred",
        "quote": "",
        "consequence": "A due_at of 2026-06-01 becomes 2026-06-01T00:00:00Z.",
    },
    {
        "id": "recycling_bin_retention",
        "question": "What retention rule does the recycling bin use?",
        "options": ["30_days", "indefinite", "7_days"],
        "choice": "30_days",
        "basis": "inferred",
        "quote": (
            "[sourced] DELETE /crm/v3/objects/tasks/{taskId} will add the task to the "
            "recycling bin."
        ),
        "consequence": ("A delete is soft. A purge hard-deletes a binned row only after 30 days."),
    },
    {
        "id": "workflow_model",
        "question": "Is the task fed by free variables or by a fixed step?",
        "options": ["fixed_step", "free_variables"],
        "choice": "fixed_step",
        "basis": "inferred",
        "quote": (
            "[sourced] For certain actions, such as the Create task action, you can "
            "also use the data panel to insert data variables from different object "
            "sources."
        ),
        "consequence": (
            "One signal maps to one deterministic task rule. The mapping is served "
            "from /vocabulary so the choice is visible."
        ),
    },
    {
        "id": "pin_writes_to_the_task",
        "question": "Which object receives the pin intent?",
        "options": ["the_task_row", "the_foreign_object"],
        "choice": "the_task_row",
        "basis": "inferred",
        "quote": (
            "[sourced] Tasks can be pinned to the record timeline by writing the task "
            "id into hs_pinned_engagement_id on the object APIs."
        ),
        "consequence": (
            "This build records the pin on the task row. It does not write to a "
            "foreign object it does not own."
        ),
    },
    {
        "id": "inbound_webhook_route",
        "question": "Does this build add an inbound webhook route?",
        "options": ["accept_the_signal_only", "add_a_route"],
        "choice": "accept_the_signal_only",
        "basis": "inferred",
        "quote": "[sourced] ... engagement score rule met, opportunity stage changed, inbound webhook",
        "consequence": (
            "The signal is accepted by name through /signals. No webhook endpoint "
            "is added, because the research names none."
        ),
    },
)

_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in INFERENCES}


def by_id(inference_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(inference_id)


def describe_inferences() -> list[dict[str, Any]]:
    return [dict(entry) for entry in INFERENCES]


__all__ = ["INFERENCES", "by_id", "describe_inferences"]
