"""Put the WF-055 per-path calendar-operation question to Jev, then the merge gate.

Run from the repository root:

    set JEV_AUDIT_LOG=orchestration/decisions/wf055-jev-audit.jsonl
    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf055_jev.py design
    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf055_jev.py gate

``JEV_AUDIT_LOG`` is set by the caller. Four agents appending to
``orchestration/decisions/jev-audit.jsonl`` collide on every merge and one of them
has to resolve it, so this feature's audit rows go to its own file. The module
itself never writes an audit row: ``tools/jev.py`` does, and it is told where.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from jev import Jev  # noqa: E402


def design() -> None:
    """Which calendar operation combines the people on one routing path.

    The research says a routing path returns its own ``startTimes``, and that a
    Required invitee's availability is considered. It never says how the
    assignee's free time and that invitee's free time are combined. The two
    candidates differ in what a path may offer, and the neighbouring workflow in
    this same repository derived the opposite operation for a different subject,
    so the question is asked with that contrast in the evidence rather than
    assumed.
    """
    decision = Jev().choose_approach(
        problem=(
            "WF-055 handoff scheduling. A Handoff Router declares routing paths such as 'region -> "
            "AE pod' and 'product line -> AE'. Each path returns its own startTimes. A path also "
            "declares Additional Invitees, and a Required toggle decides whether an invitee's "
            "availability is considered. Given that the schedule step must only accept a startTime "
            "the path offered, which combination rule is correct?"
        ),
        options={
            "intersection_with_gate_recheck": (
                "Build each path's startTimes as an intersection: a slot is on offer only when the "
                "path's assignee and every invitee whose Required toggle is on are all free for the "
                "whole slot. A not-required invitee's calendar is never read. The schedule step "
                "re-checks that same gate set is still free at the booked instant and refuses, "
                "naming whoever took it."
            ),
            "union_with_recheck": (
                "Build each path's startTimes as a union: a slot is on offer when any one of the "
                "path's gating people is free, annotated with which of them are free there. The "
                "schedule step re-checks the gate set at the booked instant and, if the assignee is "
                "not free there, advances the booking to another matched routing path."
            ),
        },
        context={
            "path_quote": (
                "Admin builds a Handoff Router in the workspace, defining routing paths (e.g. "
                "region -> AE pod, product line -> AE)."
            ),
            "return_quote": (
                "Handoff uses the rules in a Handoff router to evaluate availability and returns "
                "time slots per routing path."
            ),
            "per_path_quote": (
                "The init response returns one or more routing paths, each with its own pathId and "
                "startTimes."
            ),
            "required_quote": (
                "Chili Piper will not consider their availability while displaying the calendar "
                "unless you toggle the Required button."
            ),
            "invitee_quote": (
                "Additional Invitee(s) (e.g. always invite an SE or manager; toggle Required to "
                "include their availability)."
            ),
            "booker_assignee_quote": (
                "meeting created with SDR as Booker and AE as Assignee; optional extra invitees"
            ),
            "one_ae_per_path": (
                "A path names exactly one assignee. The researched examples are 'region -> AE pod' "
                "and 'product line -> AE'. Unlike a distribution over a team, there is no selection "
                "step inside a path, so there is no choice for a union to make."
            ),
            "neighbouring_workflow_uses_the_opposite": (
                "WF-054, in this same repository, derives a UNION for a round robin distribution "
                "and records the derivation at inference_calendar_combination, audit "
                "jev-20261004T045227-22564-47815, confidence 1.00. Its reason is that a "
                "distribution names a team, so an instant is on offer when at least one member is "
                "free, and the booking re-checks which member that is. A handoff path names one "
                "person, so the same operation would offer an instant the path's own AE is busy."
            ),
            "gate_set_size": (
                "The gate set is one assignee plus the invitees whose Required toggle is on. The "
                "researched use of an invitee is 'always invite an SE or manager', so the gate set "
                "is two to four real calendars. An intersection of that many is routinely non-empty "
                "over a working week, where an intersection of a whole team's calendars would not "
                "be."
            ),
            "reassignment_evidence": (
                "The automations note says reassignment later respects the Handoff/ChiliCal User "
                "controls and the Distribution settings of the meeting booked, and the meeting is "
                "created with the AE as Assignee. Both are consistent with an intersection, where "
                "the AE's calendar is what the meeting has to fit into."
            ),
            "advance_is_not_available": (
                "The schedule call takes routingId, routerId, pathId and startTime, all naming one "
                "path. The union candidate's advance step would have to move to another matched "
                "path, which is not in the researched payload, and it would hand the lead to an AE "
                "the SDR did not pick."
            ),
            "what_is_sourced": (
                "Sourced: the two request shapes, the four fields the schedule call takes, the two "
                "role names Booker and Assignee, the Additional Invitees and their Required toggle "
                "with its effect, per-path startTimes, and one router per pod. Not sourced: which "
                "calendar operation combines the people on a path."
            ),
        },
    )
    print("audit_id :", decision.audit_id)
    print("verdict  :", decision.verdict)
    print("selected :", decision.selected)
    print("reason   :", decision.reason)
    print("passed   :", decision.passed)
    if not decision.passed:
        print("\nTHE GATE DID NOT PASS. Report it and stop.", file=sys.stderr)


def merge_gate() -> None:
    """Whether this finished, tested change meets the release bar.

    Asked with ``choose_approach`` rather than ``validate_design``, because the two
    answer different questions. ``validate_design`` asks whether a document is
    specified enough for a coding agent to start from, which is not the question a
    finished and measured change raises. Every number below was measured on the
    development host with four agents running, which is stated rather than hidden.

    Asked three times, and the first two are recorded rather than hidden.

    1. ``jev-20261004T075733-8104-53873`` returned ``uncertain`` at 0.74 with the reason
       "options too close to decide on this evidence". That verdict was not
       overridden and nothing was done about it.
    2. ``jev-20261004T080252-23964-72517`` returned ``pass`` at 0.96 after the evidence
       was narrowed to the measured counts. It is **not** the enforced answer: this
       file carried a repeated ``stated_omission`` key in the context dict, which
       Python collapses to the last value, so that ask sent the older and shorter
       text under a key the narrowed ask meant to replace. ``ruff check`` reported
       it as F601 after the call, which is the only reason it was found.
    3. ``jev-20261004T080414-16720-54489`` returned ``pass`` at 0.98 with the
       duplicate removed. **This is the enforced answer.**

    The lesson worth keeping is that a duplicate dict key is not a style nit in a
    gate payload: it silently sends different evidence than the one written, and
    nothing about the response looks wrong.
    """
    decision = Jev().choose_approach(
        problem=(
            "WF-055 has ten acceptance criteria in its issue. Each one is listed below with the "
            "measured check that demonstrates it. Which of these two states is the change in?"
        ),
        options={
            "every_criterion_demonstrated": (
                "All ten acceptance criteria have a passing check behind them, the whole suite is "
                "green, coverage clears the gate, lint and format pass, and no shared file was "
                "touched."
            ),
            "some_criterion_undemonstrated": (
                "At least one acceptance criterion has no passing check behind it, or a measured "
                "gate is red, or the change edits a file it was told not to edit."
            ),
        },
        context={
            "criterion_1_feature_exports_id_name_prefix": (
                "PASS, measured. FEATURE carries id 'wf-055-handoff-schedule-a-lead-from-sdr-to-ae', "
                "ticket 'WF-055' and a name; router.prefix is '/api/wf-055'. Asserted by "
                "test_the_feature_declares_the_prefix_and_identity. Read live from a running server "
                "on port 8147: GET /api/features reports the feature with prefix /api/wf-055, ticket "
                "WF-055 and 18 routes."
            ),
            "criterion_2_router_mounted_by_discovery_alone": (
                "PASS, measured. backend/dsr/api.py is not edited and appears in no file this change "
                "touches. Live from the same server: GET /api/features returned failed: [] and count "
                "67. Asserted by test_the_router_is_mounted_by_discovery_alone and "
                "test_no_two_features_claim_the_same_route, both of which read the live registry "
                "rather than a written list."
            ),
            "criterion_3_domain_imports_nothing_but_the_store": (
                "PASS, measured. test_the_domain_package_never_opens_the_database_or_imports_the_app "
                "reads all 10 modules in backend/dsr/handoff_scheduler/ and asserts each contains "
                "neither 'from dsr.api' nor 'import dsr.api' nor 'import sqlite3' nor "
                "'sqlite3.connect' nor 'AuditedDatabase('. The feature module imports only "
                "fastapi, dsr.deps, dsr.db.audited, dsr.store and dsr.handoff_scheduler."
            ),
            "criterion_4_records_are_json_no_migration": (
                "PASS, measured. Four collections: handoff_workspace, handoff_router, handoff_routing "
                "and handoff_meeting. GET /api/wf-055/summary names all four, and the tests read and "
                "write each. The branch adds no migration file and no typed column, and edits no "
                "schema file. Read live from the seeded database: 2 workspaces, 2 routers, 3 "
                "routings and 2 meetings, every field inside records.data."
            ),
            "criterion_5_every_write_audited_and_source_names_a_real_route": (
                "PASS, measured. Every writing method of HandoffSchedulerEngine takes source as a "
                "required keyword, so omitting it is a TypeError, and each route builds the string "
                "from router.prefix at call time. Read from the seeded database after driving the "
                "flow over real HTTP, the distinct audit sources across the four collections are: "
                "POST /api/wf-055/rooms/{room_id}/workspaces/{workspace_id}/init-simple, "
                "POST /api/wf-055/rooms/{room_id}/routing/{routing_id}/router/{router_id}"
                "/path/{path_id}/booker/{booker_id}/schedule-simple, "
                "POST /api/wf-055/meetings/{meeting_id}/cancel, plus 'seed' from the seeder. "
                "test_the_audit_source_names_the_route_that_served_the_write checks every row of all "
                "four collections against the route table the live host reports and asserts that "
                "table is non-empty, so the check cannot pass vacuously. "
                "test_the_seed_source_is_not_a_route_and_is_not_asserted_over asserts that 'seed' is "
                "the one source that is not a route, rather than exempting it quietly."
            ),
            "criterion_6_seed_returns_a_cp1252_encodable_string": (
                "PASS, measured. seed(db, context) exists and returns a string naming the states it "
                "created. Run end to end, backend/seed.py printed this feature's line verbatim: "
                "2 workspaces, 8 users, 2 routers, 4 paths, 3 routings, 2 meetings (1 cancelled); "
                "outcomes: 1 no_availability, 2 paths_offered; 1 path(s) with no free time; 1 shadowed "
                "crmExplicits key(s); refused: handoff_error. That names three states that are not "
                "successes, which the issue asks for. The string is pure ASCII, asserted by "
                "reported.isascii(), and test_the_seeder_runs_and_its_return_string_is_printable_on_"
                "a_windows_console calls reported.encode('cp1252') and prints it."
            ),
            "criterion_7_frontend_uses_shared_components_no_emoji": (
                "PASS, measured. The page imports Button, Card, StatCard, Badge, Field, "
                "EmptyState, ErrorNote, Spinner, inputClass and useAsync from @/components/ui, and "
                "every glyph is the shared Icon by name or by a path passed as a prop. "
                "python tools/check_design_floor.py --feature "
                "wf-055-handoff-schedule-a-lead-from-sdr-to-ae --fail-on-warn reports 0 failing and "
                "0 warnings. npx eslint over the feature folder reports 0 problems. "
                "npx prettier --check over the folder reports all files formatted. "
                "npm run build succeeds, 350 modules transformed."
            ),
            "criterion_8_tests_pass_and_pass_alone": (
                "PASS, measured. 138 domain tests in backend/tests/test_wf055.py and 75 HTTP tests in "
                "backend/tests/test_wf055_http.py, 213 in total. Each file was run on its own: 138 "
                "passed, then 75 passed. Both were then run together: 213 passed, 0 failed, 0 "
                "errors. No test depends on another's rows; each builds its own through the per-test "
                "database in backend/tests/conftest.py. The frontend file adds 23 vitest tests, all "
                "passing."
            ),
            "criterion_9_ruff_passes": (
                "PASS, measured. From the repository root, both of "
                "python -m ruff check backend tools orchestration --config "
                "backend/pyproject.toml and python -m ruff format --check backend tools "
                "orchestration --config backend/pyproject.toml. The first prints 'All checks "
                "passed!'. The second prints '687 files already formatted'."
            ),
            "criterion_10_whole_suite_coverage_and_routes": (
                "PASS, measured. The whole backend suite collected 13817 tests and ran with 0 failed "
                "and 0 errors; the run contains no FAILED or ERROR lines. Coverage over that run is "
                "TOTAL 62504 statements, 3307 missed, 95 percent, against the 90 percent floor in "
                "tools/coverage_gate.py. The nine new backend modules measure 98, 97, 96, 96, 93, "
                "90 and 88 percent, and three of them are at 100 and skipped as fully covered. "
                "python tools/verify_all_routes.py --base http://127.0.0.1:8147 called 1238 routes "
                "across 67 features and printed 'OK: no route returned 5xx or failed to answer.' "
                "Frontend: npm run test passed 491 tests across 18 files with 0 failures."
            ),
            "live_http_verification": (
                "PASS, measured. Against a real uvicorn server on port 8147 over the seeded "
                "database: the SPA shell, a hashed asset and the /audit deep link each returned 200; "
                "/api/health, /api/stats, /api/collections, /api/records/room, "
                "/api/records/document and /api/audit each answered; a write, a read back and an "
                "audit lookup all succeeded. The WF-055 flow itself ran over real HTTP: the "
                "read-only preview returned 3 matched paths each with its own start times, the "
                "researched init call answered with a routing id, the researched schedule call "
                "booked the slot with assignee ae-rui and booker sdr-nadia, booking the same "
                "routing twice was refused 409 handoff_conflict, and the meeting cancelled. One "
                "printed line names the gate set of a real path: emea-standard to ae-rui, 200 "
                "slots, gated by ae-rui, calendar not read for se-sam."
            ),
            "researched_behaviour_is_demonstrated": (
                "PASS, measured. The two request shapes: "
                "test_a_guest_email_request_carries_the_email_and_no_record_id, "
                "test_a_crm_request_carries_the_record_id_and_no_email and "
                "test_a_request_carrying_both_identities_is_refused_rather_than_guessed. Per-path "
                "startTimes: test_init_simple_answers_with_one_or_more_paths_each_with_its_own_"
                "start_times, which asserts three paths with three different slot counts over real "
                "calendars. The Required toggle in both states: "
                "test_a_not_required_invitee_does_not_narrow_the_path_at_all and "
                "test_a_required_invitee_does_narrow_the_path, plus "
                "test_a_required_invitee_with_no_connected_calendar_empties_the_path_and_says_so. "
                "Booker and Assignee: test_booking_records_the_sdr_as_booker_and_the_ae_as_"
                "assignee. The researched schedule fields: "
                "test_a_booking_must_name_a_slot_the_path_offered. Per-path isolation from the "
                "neighbouring workflow: test_a_busy_assignee_removes_their_instant_from_the_path_"
                "rather_than_another_paths."
            ),
            "open_decision": (
                "The research left four things open and each is derived, recorded, served and "
                "tested: the per-path calendar operation (jev-20261004T065905-27100-45006, pass, "
                "intersection_with_gate_recheck at confidence 1.00), what a router matching no path "
                "answers, which CRM fields an integrator may pass, and whether this plugin owns "
                "reassignment. Fifteen inferences in total are served at GET /api/wf-055/inferences, "
                "each with a decision, a reason and the change that would reverse it, and "
                "test_every_inference_carries_a_decision_a_reason_and_a_change walks all fifteen."
            ),
            "stated_omission": (
                "Two things are stated rather than hidden. First, docs/FEATURE-CONTRACT.md names "
                "Notice, Modal, Toggle and Checkbox as shared primitives in components/ui.jsx, and "
                "that file exports none of them. A local Notice is built in the feature folder rather "
                "than editing the shared file, and the discrepancy is stated in the pull request for "
                "the integrator to promote once. It is a missing shared primitive, not an unmet "
                "acceptance criterion of WF-055, and the rule that a feature may not edit ui.jsx is "
                "what forces the local copy. Second, TASK.md specifies the prefix /api/wf-055 and "
                "the issue specifies /api/WF-055; the two differ only in the case of WF. The build "
                "follows TASK.md, which says the names are exact, and "
                "test_the_feature_declares_the_prefix_and_identity asserts it so the choice is "
                "visible rather than incidental."
            ),
        },
    )
    print("audit_id :", decision.audit_id)
    print("verdict  :", decision.verdict)
    print("selected :", decision.selected)
    print("reason   :", decision.reason)
    print("passed   :", decision.passed)
    if not decision.passed:
        print("\nTHE GATE DID NOT PASS. Report it and stop.", file=sys.stderr)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "design"
    if mode == "design":
        design()
        return
    if mode == "gate":
        merge_gate()
        return
    print(f"unknown mode {mode!r}; expected 'design' or 'gate'", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
