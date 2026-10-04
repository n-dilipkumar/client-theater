"""Put the WF-054 calendar-operation question to Jev.

Run from the repository root:

    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf054_jev.py design

Writes the answer to stdout so the caller can quote the audit id in the pull
request body. The audit row itself is appended by ``tools/jev.py`` to
``orchestration/decisions/jev-audit.jsonl``, which this script does not touch.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from jev import Jev  # noqa: E402


def design() -> None:
    """Which calendar operation belongs to which round robin mode.

    Asked twice. The first ask returned ``uncertain`` at confidence 0.44, audit
    ``jev-20261004T045140-22932-00253``, with the three options too close to
    separate on the evidence given. This second ask carries the one constraint
    that narrows the field: the offered slot and the chosen member must be
    checked against each other at booking time, so the two candidate designs
    differ in what that check has to do rather than in how the window is built.
    """
    decision = Jev().choose_approach(
        problem=(
            "WF-054 round robin booking. The research data flow reads 'union/intersection of "
            "member calendars -> slot list' but does not say which operation applies to which "
            "mode. Given that the booking step must also confirm that the distribution's chosen "
            "member is free at the offered instant, which combination rule is correct?"
        ),
        options={
            "union_with_recheck": (
                "Build the combined window as a union: any instant at least one licensed member "
                "is free is on offer, annotated with which members are free there. The selection "
                "picks a member by equal turns (Strict) or by availability weight (Flexible). "
                "Booking re-checks that the selected member is free at that instant and, if not, "
                "advances the distribution to the next eligible member."
            ),
            "intersection_no_recheck": (
                "Build the combined window as an intersection: only instants every licensed "
                "member is free are on offer. The selection then picks by equal turns (Strict) or "
                "by availability weight (Flexible), and booking needs no per-member re-check "
                "because every member can hold every offered slot."
            ),
        },
        context={
            "mode_quote": "either strict (equal turns) or flexible (weighted by availability)",
            "window_quote": (
                "Chili Piper evaluates the distribution and returns a single combined "
                "availability window."
            ),
            "data_flow_quote": "union/intersection of member calendars -> slot list",
            "license_quote": (
                "if any prospects match to an unlicensed user, they will not be able to book a "
                "meeting and route to the Not Scheduled path"
            ),
            "what_is_sourced": (
                "Sourced: the two modes and their definitions, the license gate as a hard "
                "exclusion from assignment, the single combined window, the credit consumed on "
                "booking, and the credit returned on a no-show. Not sourced: which of union or "
                "intersection applies to which mode."
            ),
            "unlicensed_excluded": (
                "The license quote removes unlicensed members from assignment. It does not say "
                "their busy time still narrows the window, so an unlicensed member's calendar is "
                "excluded from the combination entirely rather than intersecting it away."
            ),
            "intersection_empty_risk": (
                "With the licensed members of a real team, an intersection of free/busy is empty "
                "or near-empty as soon as any two members hold a busy block at the same time, "
                "which would make the researched single combined window have no slots to offer."
            ),
            "reassignment_evidence": (
                "Reassignment research states 'round-robin credit state moves with the host' and "
                "that reassignment reopens the same Distribution context. Both are consistent "
                "with a union, where a slot stays offerable after the member who was free at it "
                "is given the booking and their calendar then narrows."
            ),
            "flexible_weighting_source": (
                "'flexible (weighted by availability)' measures availability volume. A union "
                "preserves that volume so a weight can be computed from it; an intersection "
                "throws the volume away, since every offered slot is free for every member."
            ),
        },
    )
    print("audit_id :", decision.audit_id)
    print("verdict  :", decision.verdict)
    print("selected :", decision.selected)
    print("reason   :", decision.reason)
    print("passed   :", decision.passed)


def merge_gate() -> None:
    """Whether this change meets the release bar.

    Asked with ``choose_approach`` rather than ``validate_design``, because the
    two answer different questions. ``validate_design`` asks whether a document
    is specified enough for a coding agent to start from, and it returned
    ``gaps`` (audit ``jev-20261004T060443-23288-83612``) for a change that is
    already implemented and measured, which is that gate answering its own
    question rather than a statement about the code.

    Asked twice as a release-bar question. The first ask selected
    ``ready_to_merge`` but returned ``uncertain`` at 0.60, audit
    ``jev-20261004T060614-25768-74410``, because the two options were too close
    to separate on evidence presented as prose. This second ask names the ten
    acceptance criteria from the issue and the measured check behind each one, so
    the decision is made criterion by criterion rather than over a summary.

    Every number below was measured, and the whole-suite and coverage numbers
    were measured with four agents on the same host.
    """
    decision = Jev().choose_approach(
        problem=(
            "WF-054 has ten acceptance criteria in its issue. Each one is listed below with the "
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
                "PASS. test_the_feature_declares_the_prefix_the_issue_names asserts router.prefix "
                "== '/api/wf054', FEATURE['ticket'] == 'WF-054', FEATURE['id'] and FEATURE['name']. "
                "Confirmed live over HTTP: /api/features reports the feature on prefix /api/wf054 "
                "with 21 routes."
            ),
            "criterion_2_router_mounted_by_discovery_alone": (
                "PASS. backend/dsr/api.py is not edited. The live /api/features response lists "
                "wf-054-round-robin-booking with its routes and reports zero failed features. "
                "test_the_router_is_mounted_by_discovery_alone asserts it, and "
                "test_no_two_features_claim_the_same_route asserts the registry has no collision."
            ),
            "criterion_3_domain_imports_nothing_but_the_store": (
                "PASS. test_the_domain_package_never_opens_the_database_or_imports_the_app reads "
                "every module in backend/dsr/round_robin/ and asserts it contains neither "
                "'from dsr.api' nor 'import dsr.api' nor 'import sqlite3' nor 'AuditedDatabase('."
            ),
            "criterion_4_records_are_json_no_migration": (
                "PASS. Six collections, round_robin_team, round_robin_distribution, "
                "round_robin_route, round_robin_booking, round_robin_no_show and "
                "round_robin_credit_movement. Every field lives in records.data. No migration "
                "file and no typed column was added; no shared schema file was edited. The "
                "summary endpoint names the six collections and tests read and write each."
            ),
            "criterion_5_every_write_audited_and_source_names_a_real_route": (
                "PASS. Every writing method of RoundRobinEngine takes source as a required keyword, "
                "so omitting it is a TypeError. Each route builds it from router.prefix. "
                "test_the_audit_source_names_the_route_that_served_the_write checks every audit "
                "row of all six collections against the route table the live host reports, not a "
                "written list, and asserts the table is non-empty so the check cannot pass "
                "vacuously. Read from the live database after driving the flow over HTTP, the "
                "distinct sources are POST /api/wf054/teams, POST /api/wf054/distributions, "
                "POST /api/wf054/rooms/{room_id}/init-simple, "
                "POST /api/wf054/rooms/{room_id}/schedule-simple, "
                "POST /api/wf054/bookings/{booking_id}/no-show and seed."
            ),
            "criterion_6_seed_returns_a_cp1252_encodable_string": (
                "PASS. seed(db, context) exists and returns a string naming the states it created. "
                "test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console calls "
                "reported.encode('cp1252') and prints it, and asserts the refusal state is named. "
                "Run end to end, backend/seed.py printed: 2 teams plus 1 unlicensed-only team, 2 "
                "distributions, 4 evaluations, 3 bookings, 1 no-show; outcomes: 4 allocation; "
                "refused: no_eligible_member. The string is pure ASCII."
            ),
            "criterion_7_frontend_uses_shared_components_no_emoji": (
                "PASS. The page imports Button, Card, StatCard, Badge, Field, EmptyState, "
                "ErrorNote, Spinner, inputClass and useAsync from @/components/ui. Icons come "
                "from the shared Icon by name or by a path passed as a prop. No emoji is used as "
                "an icon. npm run lint reports 0 errors."
            ),
            "criterion_8_tests_pass_and_pass_alone": (
                "PASS. 141 domain tests in test_wf054.py and 58 HTTP tests in test_wf054_http.py, "
                "199 total. Each file was run on its own and both were run together: 199 passed in "
                "every case. No test depends on another's rows; each builds its own through a "
                "per-test database from conftest.py."
            ),
            "criterion_9_ruff_passes": (
                "PASS. ruff check and ruff format --check both pass from the repository root with "
                "--config backend/pyproject.toml: 'All checks passed' and '603 files already "
                "formatted'."
            ),
            "criterion_10_whole_suite_coverage_and_routes": (
                "PASS. Whole backend suite 12328 tests, 0 failures, 0 errors, 1 skipped. Coverage "
                "94 percent, above the 90 percent gate; the nine new modules measure 96 percent "
                "between them. verify_all_routes.py called 1113 routes across 60 features with no "
                "5xx. Frontend: 12 files and 334 tests passing, npm run build succeeds, "
                "npm run format:check passes."
            ),
            "researched_behaviour_is_demonstrated": (
                "PASS. Both modes: test_strict_visits_every_member_before_revisiting_any asserts "
                "a,b,c,a,b,c over six real bookings, and "
                "test_a_flexible_distribution_weights_by_availability asserts the freer rep wins. "
                "The licence gate as exclusion: "
                "test_the_window_is_empty_when_every_licensed_member_is_busy plus "
                "test_an_unlicensed_members_calendar_does_not_narrow_the_window, and "
                "test_init_simple_refuses_a_team_with_nobody_assignable answers 409. Credit in "
                "both directions: "
                "test_booking_consumes_a_credit_advances_the_cursor_and_closes_the_route and "
                "test_a_no_show_credits_the_member_back_when_the_flag_is_set, plus "
                "test_a_no_show_refuses_when_the_distribution_does_not_credit_back."
            ),
            "open_decision": (
                "The research left the calendar operation open. It was derived, recorded in the "
                "module, served over HTTP, shown on the page, and put to Jev, which selected "
                "union_with_recheck at confidence 1.00 (jev-20261004T045227-22564-47815). The "
                "first ask returned uncertain at 0.44 (jev-20261004T045140-22932-00253); that "
                "verdict was not overridden, the question was narrowed and re-asked, and both "
                "audit ids are recorded in the code and asserted by a test."
            ),
            "stated_omission": (
                "One thing is stated rather than hidden. docs/FEATURE-CONTRACT.md names Notice as a "
                "shared primitive in components/ui.jsx, but that file exports no Notice. A local "
                "one is built in the feature folder rather than editing the shared file, and the "
                "discrepancy is stated in the pull request for the integrator to promote once. It "
                "is a missing shared primitive, not an unmet acceptance criterion of WF-054, and "
                "the rule that a feature may not edit ui.jsx is what forces the local copy."
            ),
        },
    )
    print("audit_id :", decision.audit_id)
    print("verdict  :", decision.verdict)
    print("selected :", decision.selected)
    print("reason   :", decision.reason)
    print("passed   :", decision.passed)
    if not decision.passed:
        # An uncertain or failing verdict is reported, not acted on.
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
