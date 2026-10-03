"""The design inferences WF-048 rests on, and how to change each one.

The research is specific about the four assertions, the environment vocabulary,
the HubSpot version floors and the CI path, and silent about how most of that
becomes an API. The parts that are therefore judgement calls are collected
here, next to the sourced facts they are measured against, and served at
``GET /api/wf-048/inferences`` so a reviewer can see which reading the code
took without reconstructing the argument.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "test_environment_is_a_second_connection_row",
        "decision": (
            "a test environment is its own connection row cloned from the production one, "
            "not a flag on the production row"
        ),
        "basis": (
            "the research's extensibility line says a test environment is 'just another "
            "connection row' because the connector is parameterised by base URL + credentials, "
            "and the self-test command 'creates both connection rows'"
        ),
        "would_change_if": (
            "a connector someday needs the same credential in both environments rather than "
            "a separate OAuth client - then the rows would share a vault key and differ only "
            "in base URL"
        ),
    },
    {
        "id": "running_a_test_sync_against_production_is_refused",
        "decision": (
            "``run-test-sync`` against a connection whose environment is production answers 400 "
            "rather than sending synthetic buyers at a real org"
        ),
        "basis": (
            "the researched user flow runs the pipeline 'against the sandbox with synthetic "
            "buyers' and switches to production only 'after green'; a test sync in production "
            "would write fixture rows into real CRM records"
        ),
        "would_change_if": "the research ever documents an in-production dry-run mode",
    },
    {
        "id": "promotion_requires_the_latest_green_run",
        "decision": (
            "``promote`` refuses unless the newest run for the connection passed, and the "
            "promotion records which run the decision came from"
        ),
        "basis": (
            "the researched flow: 'Only after green does the admin switch the connection to "
            "production' - green is evidence, so the audit row names the run that carried it"
        ),
        "would_change_if": "a workflow ever needs a manual override with its own recorded approval",
    },
    {
        "id": "revert_rejects_the_sandbox_row_and_leaves_production_untouched",
        "decision": (
            "``revert`` marks the test-environment row reverted and changes nothing on the "
            "production row it was cloned from"
        ),
        "basis": (
            "the researched data flow ends 'promote or revert': the sandbox config is either "
            "promoted or abandoned, and the production connection was never touched by the test"
        ),
        "would_change_if": "the research documents a rollback that also rewinds production settings",
    },
    {
        "id": "assertions_are_evaluated_from_the_vendors_own_answers",
        "decision": (
            "every assertion is computed from what the sandbox actually answered - status, "
            "body record id, created flag, per-item results - never from a status the room "
            "writes onto a record by hand"
        ),
        "basis": (
            "the researched assertions are about the connector's behaviour ('object created, "
            "dedupe key honoured, rollback fired, quota headers behaved'), and a hand-written "
            "state would pass a demo and lie about a real vendor"
        ),
        "would_change_if": "a vendor certifies its own sandbox results and the room trusts them",
    },
    {
        "id": "dedupe_is_measured_through_the_upsert_path",
        "decision": (
            "the dedupe probe resends the fixture row through the same keyed upsert the "
            "production connector uses, and reads the vendor's ``created`` flag and record id"
        ),
        "basis": (
            "the researched data flow says 'same mapping + same code path as production', so "
            "the probe must exercise the upsert, not a side-channel read"
        ),
        "would_change_if": "a vendor documents a distinct dedupe-check endpoint",
    },
    {
        "id": "vendor_paths_are_generic_shapes",
        "decision": (
            "the probes build vendor-neutral paths (``/{object}/{key_field}/{key_value}``, "
            "``/{object}/composite?allOrNone=true``); the exact vendor URLs belong to the "
            "production connector, which this validator parameterises"
        ),
        "basis": (
            "the research cites environment/test-account surfaces (CLI, config file, "
            "environments admin), not upsert paths for this workflow, so pinning a vendor URL "
            "here would be an invented requirement"
        ),
        "would_change_if": "the research later sources per-vendor upsert paths",
    },
    {
        "id": "quota_behaviour_is_about_the_room_not_the_vendor",
        "decision": (
            "'quota headers behaved' is scored on the run's own conduct: it stops sending on "
            "the first 429 / exhausted signal, records the ``Retry-After`` (or the floor) and "
            "marks the remaining assertions skipped"
        ),
        "basis": (
            "the researched assertion names quota *headers behaving*, and a room that keeps "
            "hammering after a 429 is the defect the probe exists to catch - the vendor "
            "sending the header is already outside the room's control"
        ),
        "would_change_if": "the research ever requires verifying vendor-side quota accounting",
    },
    {
        "id": "no_salesforce_sandbox_semantics",
        "decision": (
            "no Salesforce-specific sandbox behaviour is implemented: a Salesforce connector "
            "validates through the generic sandbox rules, because the research could not "
            "source Salesforce sandbox types"
        ),
        "basis": (
            "the research gap: 'Salesforce sandbox types and scratch orgs could not be "
            "sourced' - a hypothesis is not a specification"
        ),
        "would_change_if": "the gap is closed with a sourced Salesforce sandbox reference",
    },
    {
        "id": "version_floors_are_refusals_not_warnings",
        "decision": (
            "a HubSpot test account below platform 2025.2 or CLI 8.3.0 is refused at create "
            "time, not accepted with a warning"
        ),
        "basis": (
            "the research states the floors as requirements for creating a configurable test "
            "account, so a create below them could never succeed against the real platform"
        ),
        "would_change_if": "HubSpot lowers the floors or documents a deprecation path",
    },
    {
        "id": "trial_limit_is_one_per_user",
        "decision": (
            "a second non-expired trial environment for the same owner is refused, and an "
            "expired trial frees the slot"
        ),
        "basis": (
            "'Trial environments … expire after 30 days and are limited to one per user' - "
            "enforced as a hard limit with the sourced lifetime"
        ),
        "would_change_if": "Microsoft changes the per-user limit",
    },
)


def describe() -> dict[str, Any]:
    """Every inference, as data, next to the facts it is measured against."""
    return {"count": len(INFERENCES), "inferences": list(INFERENCES)}
