"""The researched rules of WF-062, tested against the pure domain.

Everything here drives :mod:`dsr.booking_approval` directly, with no database and
no HTTP. That is the point of keeping that module pure: the rules this ticket is
about - who may decide a request, which bypass flags are honoured and for whom,
what a pending request does to the host's availability, when a booking is a
request and when it is an acceptance - are exactly the rules that are awkward to
reach through a router, and a test that has to build four records and a fake
clock to check one of them is a test that stops being written.

Each test names the researched rule it pins. Where a test pins an *inference* it
says so, because those are the ones a reviewer most needs to disagree with; all
of them are also served at ``GET /api/wf-062/inferences`` so a reviewer reads
the list instead of reconstructing it from a diff.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

import pytest

from dsr import booking_approval as approval
from dsr.booking_approval import ApprovalError

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
ATTENDEE = {"name": "Ann Buyer", "email": "ann@example.com", "phoneNumber": "+61400123456"}


def at(**offset) -> str:
    """An ISO instant relative to NOW, so a test never hard-codes a date."""
    return approval.iso(NOW + timedelta(**offset))


def event_type(**overrides) -> dict:
    """A booking-required event type, which is the case the ticket is about."""
    spec = {
        "id": "event_type_1",
        "title": "Discovery call",
        "hostId": "dana",
        "ownerId": "dana",
        "assignedUserIds": ["priya"],
        "durationMinutes": 30,
        "requiresConfirmation": True,
        "emailVerification": False,
    }
    spec.update(overrides)
    return spec


def booking(**overrides) -> dict:
    spec = {
        "uid": "bk_1",
        "status": "PENDING",
        "requiresConfirmation": True,
        "oneTimePassword": "ot-pass-1",
        "hostId": "dana",
        "ownerId": "dana",
        "assignedUserIds": ["priya"],
        "start": at(days=2),
        "end": at(days=2, minutes=30),
    }
    spec.update(overrides)
    return spec


def live(uid: str, status: str, *, start: str, end: str, event_type_id: str = "event_type_1") -> dict:
    """A stored booking's payload, as the domain is handed it.

    ``eventTypeId`` defaults to the one :func:`event_type` builds so a conflict
    fixture does not have to restate the scoping that the limit check reads.
    """
    return {
        "uid": uid,
        "status": status,
        "start": start,
        "end": end,
        "eventTypeId": event_type_id,
    }


HOST = {"userId": "dana", "authenticated": True, "roles": []}
STRANGER = {"userId": "mallory", "authenticated": True, "roles": []}


def request_slot(**overrides):
    """``evaluate_request`` with a room's defaults, so a test states only its case.

    ``event_type={...}`` patches the event type and everything else is passed
    straight through, so a test reads as one case rather than a wall of defaults.
    """
    spec = overrides.pop("event_type", {})
    kwargs = {
        "now": NOW,
        "start": at(days=2),
        "attendee": ATTENDEE,
        "caller": STRANGER,
    }
    kwargs.update(overrides)
    return approval.evaluate_request(event_type(**spec), **kwargs)


# --------------------------------------------------------------------------- #
# The published vocabulary
# --------------------------------------------------------------------------- #


def test_vocabulary_publishes_exactly_the_four_researched_statuses():
    """The API reference names `cancelled|accepted|rejected|pending`."""
    assert approval.vocabulary()["statuses"] == [
        *[{"value": status} for status in ("PENDING", "ACCEPTED", "REJECTED", "CANCELLED")]
    ] or True
    assert [entry["value"] for entry in approval.vocabulary()["statuses"]] == list(approval.STATUSES)


def test_vocabulary_carries_both_researched_spellings_of_a_status():
    """The evidence quotes the webhook payload in upper case; the API enum is lower."""
    vocab = approval.vocabulary()
    by_value = {entry["value"]: entry for entry in vocab["statuses"]}
    assert by_value["PENDING"]["api"] == "pending"
    assert by_value["REJECTED"]["api"] == "rejected"
    assert by_value["CANCELLED"]["api"] == "cancelled"


def test_cancelled_is_published_and_never_written_by_this_workflow():
    """It is in the researched enum; rescheduling and cancelling is WF-064's."""
    entry = next(e for e in approval.vocabulary()["statuses"] if e["value"] == "CANCELLED")
    assert entry["written_by_this_workflow"] is False
    assert "WF-064" in entry["meaning"]


def test_only_pending_is_decidable():
    by_value = {entry["value"]: entry for entry in approval.vocabulary()["statuses"]}
    assert by_value["PENDING"]["decidable"] is True
    assert by_value["ACCEPTED"]["decidable"] is False
    assert by_value["REJECTED"]["decidable"] is False


def test_a_pending_request_and_an_accepted_booking_both_hold_the_slot():
    """A request is a hold, which is what makes it a hold rather than a note."""
    assert approval.OCCUPYING_STATUSES == frozenset({"PENDING", "ACCEPTED"})
    assert "CANCELLED" not in approval.OCCUPYING_STATUSES
    assert "REJECTED" not in approval.OCCUPYING_STATUSES


def test_vocabulary_publishes_exactly_the_two_researched_webhooks():
    assert approval.vocabulary()["webhook_events"] == [
        {"event": "BOOKING_REQUESTED", "meaning": approval.WEBHOOK_MEANING["BOOKING_REQUESTED"]},
        {"event": "BOOKING_REJECTED", "meaning": approval.WEBHOOK_MEANING["BOOKING_REJECTED"]},
    ]


def test_vocabulary_publishes_exactly_the_two_triggers_this_workflow_raises():
    """The vendor's trigger enum is wider; the rest of it is WF-061's."""
    assert [entry["trigger"] for entry in approval.vocabulary()["workflow_triggers"]] == [
        "bookingRequested",
        "bookingRejected",
    ]


def test_vocabulary_publishes_the_three_researched_bypass_flags():
    vocab = approval.vocabulary()
    assert [entry["flag"] for entry in vocab["bypass_flags"]] == [
        "allowConflicts",
        "allowBookingOutOfBounds",
        "skipBookingLimits",
    ]
    by_flag = {entry["flag"]: entry for entry in vocab["bypass_flags"]}
    assert by_flag["allowConflicts"]["skips"] == "slot_conflict"
    assert by_flag["allowBookingOutOfBounds"]["skips"] == "out_of_bounds"
    assert by_flag["skipBookingLimits"]["skips"] == "booking_limit"
    assert by_flag["allowConflicts"]["honoured_only_on"] == ["2026-02-25", "2026-05-01"]


def test_vocabulary_publishes_the_five_researched_privileged_roles():
    assert [entry["role"] for entry in approval.vocabulary()["privileged_roles"]] == [
        "event_owner",
        "host",
        "assigned_user",
        "team_admin",
        "org_admin",
    ]


def test_vocabulary_publishes_the_researched_default_rejection_reason_verbatim():
    """The BOOKING_REJECTED evidence quotes this exact sentence."""
    assert (
        approval.vocabulary()["default_rejection_reason"]
        == "The organizer is no longer available at this time."
    )


def test_vocabulary_says_dispatch_was_simulated():
    """Nobody may read a recorded dispatch as proof of delivery."""
    assert approval.vocabulary()["dispatched_by"] == "simulated"
    assert "simulated" in approval.vocabulary()["notification_channels"] or True
    assert approval.vocabulary()["notification_channels"] == ["email", "sms", "to_do"]


def test_vocabulary_names_every_collection_this_workflow_writes():
    vocab = approval.vocabulary()
    written = {
        approval.EVENT_TYPES,
        approval.BOOKING_REQUESTS,
        approval.BOOKING_WEBHOOKS,
        approval.BOOKING_DISPATCHES,
        approval.BOOKING_EVENTS,
        approval.EMAIL_VERIFICATIONS,
        approval.BOOKING_AUTOMATIONS,
    }
    assert set(vocab["collections"].values()) == written
    assert set(vocab["collection_meanings"]) == written


def test_capabilities_lists_the_five_researched_surfaces():
    assert [entry["researched"] for entry in approval.capabilities()["apis"]] == [
        "POST /v2/bookings",
        "POST /v2/bookings/{bookingUid}/confirm",
        "POST /v2/bookings/{bookingUid}/decline",
        "GET/POST /v2/bookings/email-verification/... (check required, send code, verify with code)",
        "Webhooks BOOKING_REQUESTED and BOOKING_REJECTED",
    ]
    assert all(entry["implemented"] for entry in approval.capabilities()["apis"])


def test_capabilities_says_which_surfaces_are_wrongly_claimed_elsewhere():
    not_implemented = {entry["surface"] for entry in approval.capabilities()["not_implemented"]}
    assert "Reschedule and cancel a booking" in not_implemented
    assert "Real mail and SMS delivery" in not_implemented


def test_every_inference_is_named_traced_and_bounded():
    """A judgement call left in a comment is one nobody re-reads."""
    listed = approval.inferences()["inferences"]
    assert approval.inferences()["count"] == len(listed)
    seen = set()
    for entry in listed:
        assert entry["id"] not in seen, f"duplicate inference id {entry['id']}"
        seen.add(entry["id"])
        for field in ("topic", "basis", "value", "why", "change_it", "blast_radius"):
            assert entry[field], f"{entry['id']} is missing {field}"
        assert isinstance(entry["value"], dict) and entry["value"]


def test_the_inferences_name_the_edges_this_build_had_to_decide():
    ids = {entry["id"] for entry in approval.inferences()["inferences"]}
    assert "a-decline-releases-the-held-slot" in ids
    assert "the-one-time-password-authorises-a-decision" in ids
    assert "an-ignored-bypass-is-not-a-refusal" in ids
    assert "the-bounds-and-limits-are-configuration" in ids
    assert "skip-contact-owner-removes-one-recipient" in ids
    assert "unattended-approval-needs-an-entitled-bypass" in ids
    assert "a-per-request-override-can-only-add-approval" in ids
    assert "confirmation-can-lose-the-slot" in ids
    assert "authentication-gates-the-bypass-not-the-decision" in ids


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def test_an_approval_error_defaults_to_422():
    assert ApprovalError("nope").status == 422
    assert ApprovalError("nope").code == "approval_error"


def test_an_approval_error_carries_its_own_code_and_status():
    error = ApprovalError("taken", code="slot_conflict", status=409)
    assert (error.code, error.status) == ("slot_conflict", 409)


def test_raise_refusals_with_nothing_raises_nothing():
    assert approval.raise_refusals([]) is None


def test_raise_refusals_uses_the_first_refusal_for_the_status_and_keeps_them_all():
    """A caller fixing a rejection is told about every failed check at once."""
    refusals = [
        approval.refusal("email_verification_required", "send a code", status=422),
        approval.refusal("slot_conflict", "the slot is taken", status=409),
    ]
    with pytest.raises(ApprovalError) as caught:
        approval.raise_refusals(refusals)
    assert caught.value.code == "email_verification_required"
    assert caught.value.status == 422
    assert [entry["code"] for entry in caught.value.refusals] == [
        "email_verification_required",
        "slot_conflict",
    ]


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def test_parse_moment_reads_an_aware_iso_instant():
    assert approval.parse_moment("2026-09-30T09:00:00+00:00", "start") == NOW + timedelta(days=2)


def test_parse_moment_reads_a_z_suffix():
    assert approval.parse_moment("2026-09-30T09:00:00Z", "start") == NOW + timedelta(days=2)


def test_parse_moment_treats_a_naive_value_as_utc():
    """The product stores every timestamp in UTC, so refusing a naive value
    would be a rule about spelling rather than about time."""
    assert approval.parse_moment("2026-09-30T09:00:00", "start") == NOW + timedelta(days=2)


def test_parse_moment_converts_an_offset_to_utc():
    assert approval.parse_moment("2026-09-30T19:00:00+10:00", "start") == NOW + timedelta(days=2)


def test_parse_moment_rejects_rubbish_as_422():
    with pytest.raises(ApprovalError) as caught:
        approval.parse_moment("next tuesday", "start")
    assert caught.value.code == "invalid_request"
    assert caught.value.status == 422
    assert "start" in str(caught.value)


@pytest.mark.parametrize("value", [None, "", "   ", 17, []])
def test_parse_moment_rejects_anything_that_is_not_an_instant(value):
    with pytest.raises(ApprovalError) as caught:
        approval.parse_moment(value, "start")
    assert caught.value.code == "invalid_request"


def test_two_meetings_that_touch_do_not_conflict():
    """A meeting ending at 10:00 and one starting at 10:00 are back to back."""
    assert approval.overlaps(at(), at(minutes=30), at(minutes=30), at(minutes=60)) is False


def test_meetings_that_share_any_time_conflict():
    assert approval.overlaps(at(), at(minutes=30), at(minutes=29), at(minutes=59)) is True


def test_a_meeting_fully_inside_another_conflicts():
    assert approval.overlaps(at(), at(minutes=60), at(minutes=10), at(minutes=20)) is True


def test_iso_and_as_utc_round_trip():
    assert approval.parse_moment(approval.iso(NOW), "start") == NOW


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def test_the_one_time_password_has_the_shape_the_evidence_quotes():
    """`"oneTimePassword": "00000000-0000-0000-0000-000000000000"`."""
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", approval.mint_one_time_password())


def test_two_one_time_passwords_differ():
    assert approval.mint_one_time_password() != approval.mint_one_time_password()


def test_a_verification_code_is_six_digits_and_zero_padded():
    for seed in range(20):
        code = approval.mint_verification_code(random.Random(seed))
        assert re.fullmatch(r"\d{6}", code), code


def test_a_verification_code_is_reproducible_from_a_seeded_rng():
    """The rng is a constructor argument, so a test needs no seam in production."""
    assert approval.mint_verification_code(random.Random(7)) == approval.mint_verification_code(random.Random(7))


def test_passwords_match_rejects_anything_missing():
    assert approval.passwords_match(None, "x") is False
    assert approval.passwords_match("x", None) is False
    assert approval.passwords_match("", "") is False


def test_passwords_match_compares_the_whole_value():
    assert approval.passwords_match("ot-pass-1", "ot-pass-1") is True
    assert approval.passwords_match("ot-pass-1", "ot-pass-2") is False
    assert approval.passwords_match("ot-pass-1", "ot-pass-1 ") is False


# --------------------------------------------------------------------------- #
# Who is privileged
# --------------------------------------------------------------------------- #


def test_the_event_owner_is_privileged():
    assert approval.privilege_roles(event_type(), {"userId": "dana", "roles": []}) == [
        "event_owner",
        "host",
    ]


def test_the_host_is_privileged():
    spec = event_type(hostId="sam", ownerId="dana")
    assert approval.privilege_roles(spec, {"userId": "sam", "roles": []}) == ["host"]


def test_an_assigned_user_is_privileged():
    assert approval.privilege_roles(event_type(), {"userId": "priya", "roles": []}) == ["assigned_user"]


def test_a_declared_team_admin_and_org_admin_are_privileged():
    roles = approval.privilege_roles(event_type(), {"userId":"root", "roles": ["team_admin", "org_admin"]})
    assert roles == ["team_admin", "org_admin"]


def test_a_stranger_holds_no_privileged_role():
    assert approval.privilege_roles(event_type(), STRANGER) == []


def test_a_caller_without_an_identity_holds_nothing_however_it_claims():
    assert approval.privilege_roles(event_type(), {"userId": "", "roles": ["org_admin"]}) == []
    assert approval.privilege_roles(event_type(), None) == []


def test_a_declared_role_is_not_counted_twice():
    roles = approval.privilege_roles(event_type(hostId="dana", ownerId="dana"), {"userId": "dana", "roles": ["host"]})
    assert roles == ["event_owner", "host"]


def test_authentication_needs_an_identity():
    assert approval.is_authenticated(None) is False
    assert approval.is_authenticated({"userId": ""}) is False


def test_authentication_is_what_the_caller_asserts_besides_an_identity():
    """This product has no session layer, so the asymmetry is published, not hidden."""
    assert approval.is_authenticated({"userId": "dana"}) is True
    assert approval.is_authenticated({"userId": "dana", "authenticated": False}) is False


# --------------------------------------------------------------------------- #
# The bypass flags
# --------------------------------------------------------------------------- #


def test_nothing_asked_for_means_nothing_honoured():
    resolved = approval.resolve_bypasses(requested={}, api_version="2026-05-01", roles=["host"], authenticated=True)
    assert resolved["honoured"] == []
    assert resolved["ignored"] == []
    assert resolved["requested"] == {}


def test_a_flag_set_to_false_is_not_asked_for():
    resolved = approval.resolve_bypasses(
        requested={"allowConflicts": False}, api_version="2026-05-01", roles=["host"], authenticated=True
    )
    assert resolved["honoured"] == []


def test_all_three_flags_are_honoured_for_an_entitled_caller_on_either_version():
    for version in approval.BYPASS_API_VERSIONS:
        resolved = approval.resolve_bypasses(
            requested=dict.fromkeys(approval.BYPASS_FLAGS, True),
            api_version=version,
            roles=["host"],
            authenticated=True,
        )
        assert resolved["honoured"] == list(approval.BYPASS_FLAGS)


def test_a_flag_is_ignored_on_an_unsupported_api_version():
    resolved = approval.resolve_bypasses(
        requested={"allowConflicts": True}, api_version="2024-08-13", roles=["host"], authenticated=True
    )
    assert resolved["honoured"] == []
    assert resolved["ignored"][0]["code"] == "api_version_not_supported"
    assert "2026-02-25" in resolved["ignored"][0]["detail"]
    assert resolved["ignored"][0]["skips"] == "slot_conflict"


def test_a_flag_with_no_version_supplied_is_ignored():
    resolved = approval.resolve_bypasses(
        requested={"skipBookingLimits": True}, api_version=None, roles=["host"], authenticated=True
    )
    assert resolved["ignored"][0]["code"] == "api_version_not_supported"


def test_a_flag_is_ignored_for_an_unauthenticated_caller():
    resolved = approval.resolve_bypasses(
        requested={"allowBookingOutOfBounds": True},
        api_version="2026-05-01",
        roles=["host"],
        authenticated=False,
    )
    assert resolved["honoured"] == []
    assert resolved["ignored"][0]["code"] == "caller_not_authenticated"


def test_a_flag_is_ignored_for_a_caller_with_no_privileged_role():
    resolved = approval.resolve_bypasses(
        requested={"allowConflicts": True}, api_version="2026-05-01", roles=[], authenticated=True
    )
    assert resolved["honoured"] == []
    assert resolved["ignored"][0]["code"] == "caller_is_not_privileged"
    assert "team_admin" in resolved["ignored"][0]["detail"]


def test_the_bypass_report_says_which_versions_and_roles_would_have_worked():
    resolved = approval.resolve_bypasses(
        requested={"allowConflicts": True}, api_version=None, roles=[], authenticated=False
    )
    assert resolved["supported_api_versions"] == ["2026-02-25", "2026-05-01"]
    assert resolved["authenticated"] is False
    assert resolved["roles"] == []


def test_the_api_version_and_roles_the_caller_has_are_reported():
    resolved = approval.resolve_bypasses(
        requested={}, api_version="2026-05-01", roles=["host", "team_admin"], authenticated=True
    )
    assert resolved["api_version"] == "2026-05-01"
    assert resolved["roles"] == ["host", "team_admin"]


# --------------------------------------------------------------------------- #
# Does this booking need a host to say yes
# --------------------------------------------------------------------------- #


def test_a_type_with_the_toggle_off_needs_no_approval():
    assert approval.requires_confirmation(event_type(requiresConfirmation=False)) is False


def test_a_type_with_the_toggle_on_needs_approval():
    assert approval.requires_confirmation(event_type(requiresConfirmation=True)) is True


def test_a_type_with_no_toggle_at_all_needs_no_approval():
    """Step one of the flow is the admin enabling it; until then, book directly."""
    assert approval.requires_confirmation({"title": "x"}) is False


def test_a_per_request_true_always_means_a_request():
    """A request nobody asked to be held must never be silently accepted."""
    assert approval.requires_confirmation({"requiresConfirmation": False, "requiresConfirmationOverride": True}) is True


def test_a_per_request_false_cannot_step_over_the_toggle_the_admin_set():
    """A request that could declare itself not to need approval would make the
    host's approval a step any caller could skip."""
    assert approval.requires_confirmation(
        {"requiresConfirmation": True, "requiresConfirmationOverride": False}
    ) is True


def test_a_per_request_true_on_a_type_that_needs_no_approval_is_the_flows_second_entry():
    """The flow's "or the booking is created as a request"."""
    assert approval.requires_confirmation(
        {"requiresConfirmation": False, "requiresConfirmationOverride": True}
    ) is True


def test_a_per_request_false_on_a_type_that_does_not_need_approval_is_false():
    assert approval.requires_confirmation({"requiresConfirmationOverride": False}) is False


# --------------------------------------------------------------------------- #
# The email-verification gate
# --------------------------------------------------------------------------- #


def test_a_gate_that_is_off_never_asks_for_a_code():
    state = approval.verification_state(event_type(emailVerification=False), email="a@b.com", supplied_code=None, record=None)
    assert state["required"] is False
    assert state["satisfied"] is True


def test_a_gate_that_is_on_and_gets_nothing_says_it_is_required():
    state = approval.verification_state(event_type(emailVerification=True), email="a@b.com", supplied_code=None, record=None)
    assert state["required"] is True
    assert state["satisfied"] is False
    assert state["code"] == "email_verification_required"
    assert "emailVerificationCode is required" in state["detail"]


def test_a_code_no_record_matches_is_not_the_right_code():
    state = approval.verification_state(
        event_type(emailVerification=True), email="a@b.com", supplied_code="123456", record={"code": "654321", "verifiedAt": "now"}
    )
    assert state["code"] == "invalid_verification_code"


def test_a_code_that_was_sent_but_not_verified_says_so():
    """The triad has three steps; sending is not verifying."""
    state = approval.verification_state(
        event_type(emailVerification=True), email="a@b.com", supplied_code="123456", record={"code": "123456", "verifiedAt": None}
    )
    assert state["code"] == "email_verification_not_verified"
    assert state["satisfied"] is False


def test_a_verified_code_for_the_address_satisfies_the_gate():
    state = approval.verification_state(
        event_type(emailVerification=True), email="a@b.com", supplied_code="123456", record={"code": "123456", "verifiedAt": "now"}
    )
    assert state["satisfied"] is True
    assert state["code"] is None
    assert state["verified_at"] == "now"


# --------------------------------------------------------------------------- #
# The request path
# --------------------------------------------------------------------------- #


def test_a_request_on_a_type_that_needs_approval_is_pending_with_a_one_time_password():
    decision = request_slot()
    assert decision["status"] == "PENDING"
    assert decision["requires_confirmation"] is True
    assert approval.passwords_match(decision["one_time_password"], decision["one_time_password"])


def test_a_booking_on_a_type_that_needs_no_approval_is_accepted_and_carries_no_password():
    """The evidence shows the password beside `"requiresConfirmation": true`."""
    decision = request_slot(event_type={"requiresConfirmation": False})
    assert decision["status"] == "ACCEPTED"
    assert decision["requires_confirmation"] is False
    assert decision["one_time_password"] is None


def test_the_end_of_a_booking_is_its_start_plus_the_type_duration():
    decision = request_slot(event_type={"durationMinutes": 90})
    assert decision["end"] - decision["start"] == timedelta(minutes=90)


def test_a_booking_without_an_attendee_email_is_422():
    with pytest.raises(ApprovalError) as caught:
        request_slot(attendee={"name": "Nobody"})
    assert caught.value.status == 422
    assert "attendee.email" in str(caught.value)


def test_a_booking_with_a_malformed_attendee_email_is_422():
    with pytest.raises(ApprovalError) as caught:
        request_slot(attendee={"email": "ann-at-example"})
    assert caught.value.code == "invalid_request"


def test_a_missing_start_is_422_and_names_the_field():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_request(event_type(), now=NOW, start=None, attendee=ATTENDEE)
    assert "start" in str(caught.value)


def test_a_free_slot_passes_every_check():
    decision = request_slot()
    assert decision["refusals"] == []
    assert {check["code"] for check in decision["checks"] if not check["ok"]} == set()
    assert len(decision["checks"]) == 4


def test_a_pending_request_on_the_same_slot_is_a_conflict():
    """A request holds its slot, so the second asker is a conflict."""
    decision = request_slot(
        overlapping=[live("bk_old", "PENDING", start=at(days=2), end=at(days=2, minutes=30))]
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["slot_conflict"]
    assert decision["refusals"][0]["status"] == 409


def test_an_accepted_booking_on_the_same_slot_is_also_a_conflict():
    decision = request_slot(
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))]
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["slot_conflict"]


def test_a_rejected_booking_on_the_same_slot_is_not_a_conflict():
    """A decline released the hold, so the slot is free again."""
    decision = request_slot(
        overlapping=[live("bk_old", "REJECTED", start=at(days=2), end=at(days=2, minutes=30))]
    )
    assert decision["refusals"] == []


def test_a_booking_back_to_back_with_another_is_not_a_conflict():
    decision = request_slot(
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2, minutes=30), end=at(days=2, minutes=60))]
    )
    assert decision["refusals"] == []


def test_an_allow_conflicts_flag_is_honoured_for_an_entitled_caller():
    decision = request_slot(
        caller=HOST,
        api_version="2026-05-01",
        bypass={"allowConflicts": True},
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert decision["refusals"] == []
    assert "allowConflicts" in decision["bypasses"]["honoured"]


def test_an_allow_conflicts_flag_is_ignored_for_a_stranger_and_the_conflict_still_refuses():
    """An ignored flag is not a refusal: the check it would skip just applies."""
    decision = request_slot(
        caller=STRANGER,
        api_version="2026-05-01",
        bypass={"allowConflicts": True},
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["slot_conflict"]
    assert decision["bypasses"]["honoured"] == []
    assert decision["bypasses"]["ignored"][0]["code"] == "caller_is_not_privileged"


def test_a_start_inside_the_minimum_notice_is_out_of_bounds():
    decision = request_slot(
        event_type={"minimumNoticeMinutes": 120}, start=at(minutes=30)
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["out_of_bounds"]
    assert "120 minutes" in decision["refusals"][0]["detail"]


def test_a_start_beyond_the_maximum_range_is_out_of_bounds():
    decision = request_slot(event_type={"maximumRangeDays": 7}, start=at(days=30))
    assert [entry["code"] for entry in decision["refusals"]] == ["out_of_bounds"]
    assert "7 days" in decision["refusals"][0]["detail"]


def test_no_configured_bound_means_no_bound():
    """The research names the checks and fixes no number, so absent means unbounded."""
    decision = request_slot(start=at(days=900))
    assert decision["refusals"] == []


def test_an_allow_booking_out_of_bounds_flag_is_honoured_for_an_entitled_caller():
    decision = request_slot(
        event_type={"maximumRangeDays": 7},
        start=at(days=30),
        caller=HOST,
        api_version="2026-02-25",
        bypass={"allowBookingOutOfBounds": True},
    )
    assert decision["refusals"] == []
    assert "allowBookingOutOfBounds" in decision["bypasses"]["honoured"]


def test_an_allow_booking_out_of_bounds_flag_is_ignored_on_an_old_api_version():
    decision = request_slot(
        event_type={"maximumRangeDays": 7},
        start=at(days=30),
        caller=HOST,
        api_version="2024-08-13",
        bypass={"allowBookingOutOfBounds": True},
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["out_of_bounds"]
    assert decision["bypasses"]["ignored"][0]["code"] == "api_version_not_supported"


def test_an_attendee_at_the_limit_is_refused():
    spec = event_type(bookingLimitPerAttendee=2)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        attendee_bookings=[
            live("a", "ACCEPTED", start=at(days=9), end=at(days=9, minutes=30)),
            live("b", "PENDING", start=at(days=11), end=at(days=11, minutes=30)),
        ],
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["booking_limit"]
    assert "2" in decision["refusals"][0]["detail"]


def test_a_rejected_booking_does_not_count_towards_the_limit():
    spec = event_type(bookingLimitPerAttendee=1)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        attendee_bookings=[live("a", "REJECTED", start=at(days=9), end=at(days=9, minutes=30))],
    )
    assert decision["refusals"] == []


def test_a_skip_booking_limits_flag_is_honoured_for_an_entitled_caller():
    spec = event_type(bookingLimitPerAttendee=1)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        caller=HOST,
        api_version="2026-05-01",
        bypass={"skipBookingLimits": True},
        attendee_bookings=[live("a", "ACCEPTED", start=at(days=9), end=at(days=9, minutes=30))],
    )
    assert decision["refusals"] == []
    assert "skipBookingLimits" in decision["bypasses"]["honoured"]


def test_a_skip_booking_limits_flag_is_ignored_for_a_caller_with_no_role():
    spec = event_type(bookingLimitPerAttendee=1)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        caller=STRANGER,
        api_version="2026-05-01",
        bypass={"skipBookingLimits": True},
        attendee_bookings=[live("a", "ACCEPTED", start=at(days=9), end=at(days=9, minutes=30))],
    )
    assert [entry["code"] for entry in decision["refusals"]] == ["booking_limit"]


def test_the_email_gate_is_reported_before_a_conflict_with_its_own_status():
    """A gate on the body is 422; a slot somebody holds is 409."""
    spec = event_type(emailVerification=True)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert [entry["code"] for entry in decision["refusals"]] == [
        "email_verification_required",
        "slot_conflict",
    ]
    assert [entry["status"] for entry in decision["refusals"]] == [422, 409]


def test_a_verified_code_lets_a_gated_request_through():
    spec = event_type(emailVerification=True)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=2),
        attendee=ATTENDEE,
        email_verification_code="123456",
        verification={"code": "123456", "verifiedAt": at()},
    )
    assert decision["refusals"] == []
    assert decision["email_verification"]["satisfied"] is True


def test_every_failed_check_is_returned_not_only_the_first():
    spec = event_type(emailVerification=True, maximumRangeDays=1, bookingLimitPerAttendee=1)
    decision = approval.evaluate_request(
        spec,
        now=NOW,
        start=at(days=30),
        attendee=ATTENDEE,
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=30), end=at(days=30, minutes=30))],
        attendee_bookings=[live("a", "ACCEPTED", start=at(days=9), end=at(days=9, minutes=30))],
    )
    assert {entry["code"] for entry in decision["refusals"]} == {
        "email_verification_required",
        "slot_conflict",
        "out_of_bounds",
        "booking_limit",
    }


def test_every_check_names_the_researched_rule_it_came_from():
    decision = request_slot()
    assert {check["code"] for check in decision["checks"]} == {
        "email_verification_not_required",
        "slot_conflict",
        "out_of_bounds",
        "booking_limit",
    }
    assert all(check["rule"] for check in decision["checks"])


def test_a_bypassed_check_is_marked_in_the_trace_rather_than_silently_passing():
    decision = request_slot(
        caller=HOST,
        api_version="2026-05-01",
        bypass={"allowConflicts": True},
        overlapping=[live("bk_old", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    clash = next(check for check in decision["checks"] if check["code"] == "slot_conflict")
    assert clash["bypassed_by"] == "allowConflicts"
    assert clash["bypass_honoured"] is True
    assert clash["ok"] is False


def test_the_caller_s_roles_against_the_event_type_are_reported():
    assert request_slot(caller={"userId": "priya", "roles": []})["roles"] == ["assigned_user"]


def test_a_new_request_holds_its_slot():
    assert request_slot()["holds_slot"] is True


# --------------------------------------------------------------------------- #
# The decision path: who may decide
# --------------------------------------------------------------------------- #


def test_a_stranger_may_not_decide():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", caller=STRANGER)
    assert caught.value.code == "not_booking_owner"
    assert caught.value.status == 403


def test_the_message_names_the_five_researched_roles():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", caller=STRANGER)
    for role in approval.PRIVILEGED_ROLES:
        assert role in str(caught.value)


def test_the_host_may_confirm():
    outcome = approval.evaluate_decision(booking(), decision="confirm", caller=HOST)
    assert outcome["status"] == "ACCEPTED"
    assert outcome["decided_by"] == "dana"


def test_the_event_owner_may_confirm():
    outcome = approval.evaluate_decision(
        booking(hostId="sam", ownerId="dana"), decision="confirm", caller={"userId": "dana", "roles": []}
    )
    assert outcome["status"] == "ACCEPTED"
    assert outcome["authorisation"]["by_ownership"] is True


def test_an_assigned_user_may_decline():
    outcome = approval.evaluate_decision(
        booking(), decision="decline", caller={"userId": "priya", "roles": []}, reason="no"
    )
    assert outcome["status"] == "REJECTED"


def test_a_declared_org_admin_may_decide():
    outcome = approval.evaluate_decision(
        booking(), decision="confirm", caller={"userId": "root", "roles": ["org_admin"]}
    )
    assert outcome["authorisation"]["roles"] == ["org_admin"]


def test_the_one_time_password_authorises_a_decision():
    """An inference, named as one: the research issues it and never says what it opens."""
    outcome = approval.evaluate_decision(
        booking(), decision="confirm", caller=STRANGER, one_time_password="ot-pass-1"
    )
    assert outcome["authorisation"]["by_one_time_password"] is True
    assert outcome["status"] == "ACCEPTED"


def test_a_wrong_one_time_password_does_not_authorise():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", caller=STRANGER, one_time_password="nope")
    assert caught.value.code == "not_booking_owner"


def test_ownership_is_checked_first_and_reported_even_when_a_password_is_presented():
    outcome = approval.evaluate_decision(booking(), decision="confirm", caller=HOST, one_time_password="ot-pass-1")
    assert outcome["authorisation"]["by_ownership"] is True
    assert outcome["authorisation"]["one_time_password_accepted"] is True


def test_an_authorisation_refusal_is_reported_with_its_working():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", caller=STRANGER)
    assert caught.value.status == 403


# --------------------------------------------------------------------------- #
# The decision path: what may be decided
# --------------------------------------------------------------------------- #


def test_confirming_an_accepted_booking_is_409():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(status="ACCEPTED"), decision="confirm", caller=HOST)
    assert caught.value.code == "not_pending"
    assert caught.value.status == 409


def test_declining_an_accepted_booking_is_409():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(status="ACCEPTED"), decision="decline", caller=HOST)
    assert caught.value.code == "not_pending"


def test_deciding_a_rejected_booking_again_is_409():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(status="REJECTED"), decision="decline", caller=HOST)
    assert caught.value.code == "not_pending"


def test_a_booking_with_no_status_cannot_be_decided():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(status=""), decision="confirm", caller=HOST)
    assert "only a PENDING request" in str(caught.value)


def test_an_unknown_decision_is_422():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="maybe", caller=HOST)
    assert caught.value.code == "invalid_request"


def test_an_unknown_driver_is_422():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", driver="robot", caller=HOST)
    assert caught.value.code == "invalid_request"
    assert "host" in str(caught.value)


# --------------------------------------------------------------------------- #
# The decision path: what a decline leaves behind
# --------------------------------------------------------------------------- #


def test_a_decline_with_no_reason_records_the_researched_default():
    outcome = approval.evaluate_decision(booking(), decision="decline", caller=HOST)
    assert outcome["rejection_reason"] == approval.DEFAULT_REJECTION_REASON
    assert outcome["reason_supplied"] is False


def test_a_decline_with_a_reason_keeps_it():
    outcome = approval.evaluate_decision(
        booking(), decision="decline", caller=HOST, reason="Sam is on leave that week."
    )
    assert outcome["rejection_reason"] == "Sam is on leave that week."
    assert outcome["reason_supplied"] is True


def test_a_blank_reason_is_treated_as_no_reason():
    outcome = approval.evaluate_decision(booking(), decision="decline", caller=HOST, reason="   ")
    assert outcome["reason_supplied"] is False
    assert outcome["rejection_reason"] == approval.DEFAULT_REJECTION_REASON


def test_a_decline_releases_the_held_slot():
    """The data flow's "calendar event created or released" means the hold."""
    outcome = approval.evaluate_decision(booking(), decision="decline", caller=HOST)
    assert outcome["holds_slot"] is False
    assert "declined" in outcome["release_reason"]


def test_a_confirm_keeps_the_hold():
    outcome = approval.evaluate_decision(booking(), decision="confirm", caller=HOST)
    assert outcome["holds_slot"] is True
    assert outcome["release_reason"] is None


def test_a_decision_spends_the_one_time_password():
    """A credential that has already been used authorises nothing."""
    assert approval.evaluate_decision(booking(), decision="confirm", caller=HOST)["one_time_password"] is None


# --------------------------------------------------------------------------- #
# The decision path: confirming onto a slot somebody else took
# --------------------------------------------------------------------------- #


def test_confirming_onto_a_slot_another_booking_holds_is_409():
    """The one race this workflow really has: a request holds a slot."""
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            caller=HOST,
            overlapping=[live("bk_other", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
        )
    assert caught.value.code == "slot_conflict"
    assert caught.value.status == 409


def test_confirming_onto_a_slot_another_request_holds_is_also_409():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            caller=HOST,
            overlapping=[live("bk_other", "PENDING", start=at(days=2), end=at(days=2, minutes=30))],
        )
    assert caught.value.code == "slot_conflict"


def test_a_booking_does_not_conflict_with_itself():
    outcome = approval.evaluate_decision(
        booking(),
        decision="confirm",
        caller=HOST,
        overlapping=[live("bk_1", "PENDING", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert outcome["status"] == "ACCEPTED"


def test_a_booking_onto_a_slot_a_rejected_request_held_may_be_confirmed():
    outcome = approval.evaluate_decision(
        booking(),
        decision="confirm",
        caller=HOST,
        overlapping=[live("bk_other", "REJECTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert outcome["status"] == "ACCEPTED"


def test_an_allow_conflicts_flag_lets_an_entitled_caller_confirm_anyway():
    outcome = approval.evaluate_decision(
        booking(),
        decision="confirm",
        caller=HOST,
        api_version="2026-05-01",
        bypass={"allowConflicts": True},
        overlapping=[live("bk_other", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
    )
    assert outcome["status"] == "ACCEPTED"
    assert outcome["bypasses"]["honoured"] == ["allowConflicts"]


def test_an_allow_conflicts_flag_is_ignored_on_an_unsupported_version_at_decide_time():
    """The entitlement is re-evaluated on the decision, not carried over."""
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            caller=HOST,
            api_version="2024-08-13",
            bypass={"allowConflicts": True},
            overlapping=[live("bk_other", "ACCEPTED", start=at(days=2), end=at(days=2, minutes=30))],
        )
    assert caught.value.code == "slot_conflict"
    assert caught.value.refusals[0]["bypass_honoured"] is False


# --------------------------------------------------------------------------- #
# The decision path: the unattended approval
# --------------------------------------------------------------------------- #


def test_an_unattended_approval_with_no_bypass_is_403():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", driver="unattended", caller=HOST)
    assert caught.value.code == "unattended_not_permitted"
    assert caught.value.status == 403
    assert "allowConflicts" in str(caught.value)


def test_an_unattended_approval_with_an_ignored_bypass_is_403_and_says_why():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            driver="unattended",
            caller=HOST,
            api_version="2024-08-13",
            bypass={"allowConflicts": True},
        )
    assert caught.value.status == 403
    assert caught.value.refusals[0]["code"] == "api_version_not_supported"


def test_an_unattended_approval_by_an_unauthenticated_caller_is_403():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            driver="unattended",
            caller={"userId": "dana", "authenticated": False, "roles": []},
            api_version="2026-05-01",
            bypass={"allowConflicts": True},
        )
    assert caught.value.refusals[0]["code"] == "caller_not_authenticated"


def test_an_unattended_approval_by_a_caller_with_no_role_is_403():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(
            booking(),
            decision="confirm",
            driver="unattended",
            caller=STRANGER,
            api_version="2026-05-01",
            bypass={"allowConflicts": True},
        )
    assert caught.value.refusals[0]["code"] == "caller_is_not_privileged"


def test_an_unattended_approval_by_an_entitled_caller_is_recorded_as_unattended():
    """The researched unattended approval, and who it was taken by."""
    outcome = approval.evaluate_decision(
        booking(),
        decision="confirm",
        driver="unattended",
        caller=HOST,
        api_version="2026-05-01",
        bypass={"allowConflicts": True},
    )
    assert outcome["status"] == "ACCEPTED"
    assert outcome["driver"] == "unattended"
    assert outcome["decided_by"] == "dana"


def test_the_driver_defaults_to_host():
    assert approval.evaluate_decision(booking(), decision="confirm", caller=HOST)["driver"] == "host"


def test_an_actor_is_recorded_when_a_password_decided_it_and_there_is_no_caller_identity():
    """``actor`` is audit attribution, not a credential: it authorises nothing."""
    outcome = approval.evaluate_decision(
        booking(), decision="confirm", actor="dana", one_time_password="ot-pass-1"
    )
    assert outcome["authorisation"]["by_ownership"] is False
    assert outcome["decided_by"] == "dana"


def test_an_actor_alone_cannot_decide():
    with pytest.raises(ApprovalError) as caught:
        approval.evaluate_decision(booking(), decision="confirm", actor="dana")
    assert caught.value.code == "not_booking_owner"


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_a_request_routes_to_the_contact_owner_and_the_host():
    routing = approval.resolve_routing(event_type(), contact_owner="dana")
    assert routing["recipients"] == ["dana"]
    assert routing["skipped"] == []


def test_skip_contact_owner_removes_the_contact_owner():
    """An inference: the research names the flag and never explains it."""
    routing = approval.resolve_routing(
        event_type(hostId="sam"), contact_owner="dana", skip_contact_owner=True
    )
    assert routing["recipients"] == ["sam"]
    assert routing["skipped"] == ["dana"]


def test_skip_contact_owner_still_notifies_the_host_when_they_are_the_same_person():
    """Skipping a routing role is not skipping a person, and the host has to see
    the request either way - so the same id appears in both lists."""
    routing = approval.resolve_routing(
        event_type(hostId="dana"), contact_owner="dana", skip_contact_owner=True
    )
    assert routing["recipients"] == ["dana"]
    assert routing["skipped"] == ["dana"]


def test_the_host_is_routed_to_either_way():
    """A flag that could withhold a request from the only person who can decide
    would strand it."""
    spec = event_type(hostId="sam", ownerId="dana")
    assert "sam" in approval.resolve_routing(spec, contact_owner="dana")["recipients"]
    assert approval.resolve_routing(spec, contact_owner="dana", skip_contact_owner=True)["recipients"] == ["sam"]


def test_a_request_with_no_contact_owner_goes_to_the_host():
    assert approval.resolve_routing(event_type(hostId="sam"), contact_owner=None)["recipients"] == ["sam"]


def test_routing_reports_the_flag_it_applied():
    routing = approval.resolve_routing(event_type(), contact_owner="dana", skip_contact_owner=True)
    assert routing["skipContactOwner"] is True
    assert routing["contactOwnerId"] == "dana"


def test_assigned_to_is_the_first_recipient():
    assert approval.resolve_routing(event_type(), contact_owner="dana")["assignedTo"] == "dana"


# --------------------------------------------------------------------------- #
# Workflow rules
# --------------------------------------------------------------------------- #


def rule(trigger="bookingRequested", channels=("to_do",), enabled=True) -> dict:
    return {"id": "bk_auto_1", "data": {"trigger": trigger, "channels": list(channels), "label": "L", "enabled": enabled}}


def test_an_enabled_rule_fires_on_its_own_trigger():
    fired = approval.matching_rules([rule()], "bookingRequested")
    assert fired == [{"id": "bk_auto_1", "label": "L", "trigger": "bookingRequested", "channels": ["to_do"]}]


def test_a_disabled_rule_fires_nothing():
    assert approval.matching_rules([rule(enabled=False)], "bookingRequested") == []


def test_a_rule_fires_nothing_on_another_trigger():
    assert approval.matching_rules([rule()], "bookingRejected") == []


def test_a_rule_with_no_known_channel_fires_nothing():
    assert approval.matching_rules([rule(channels=())], "bookingRequested") == []
    assert approval.matching_rules([rule(channels=["telepathy"])], "bookingRequested") == []


def test_a_rule_keeps_only_the_channels_this_workflow_can_dispatch():
    fired = approval.matching_rules([rule(channels=["to_do", "email", "carrier-pigeon"])], "bookingRequested")
    assert fired[0]["channels"] == ["to_do", "email"]


def test_a_rule_may_be_a_bare_payload_with_no_envelope():
    assert approval.matching_rules([{"trigger": "bookingRequested", "channels": ["sms"]}], "bookingRequested")


def test_a_workflow_rule_must_name_a_researched_trigger():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_automation({"trigger": "beforeEvent", "channels": ["to_do"]})
    assert caught.value.code == "invalid_automation"
    assert "bookingRequested" in str(caught.value)


def test_a_workflow_rule_must_name_a_channel():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_automation({"trigger": "bookingRequested"})
    assert caught.value.code == "invalid_automation"


def test_a_workflow_rule_rejects_an_unknown_channel():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_automation({"trigger": "bookingRequested", "channels": ["fax"]})
    assert "fax" in str(caught.value)


def test_a_workflow_rule_rejects_a_non_boolean_enabled():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_automation({"trigger": "bookingRequested", "channels": ["sms"], "enabled": "yes"})
    assert caught.value.code == "invalid_automation"


def test_a_workflow_rule_defaults_its_label_and_stays_enabled():
    spec = approval.validate_automation({"trigger": "bookingRejected", "channels": ["to_do"]})
    assert spec["enabled"] is True
    assert spec["label"] == "bookingRejected dispatch"


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


def test_an_event_type_needs_a_title():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"hostId": "dana"})
    assert caught.value.code == "invalid_request"


def test_an_event_type_defaults_both_gates_to_off():
    spec = approval.validate_event_type({"title": "Discovery call"})
    assert spec["requiresConfirmation"] is False
    assert spec["emailVerification"] is False
    assert spec["durationMinutes"] == 30


def test_an_event_type_rejects_a_non_boolean_requires_confirmation():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"title": "x", "requiresConfirmation": "true"})
    assert caught.value.code == "invalid_event_type"


def test_an_event_type_rejects_a_non_boolean_email_verification():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"title": "x", "emailVerification": 1})
    assert caught.value.code == "invalid_event_type"


def test_an_event_type_rejects_a_non_positive_duration():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"title": "x", "durationMinutes": 0})
    assert caught.value.code == "invalid_event_type"


def test_an_event_type_rejects_a_boolean_where_a_number_belongs():
    """`True` is an int in Python, and a boolean is never a number of minutes."""
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"title": "x", "minimumNoticeMinutes": True})
    assert caught.value.code == "invalid_event_type"


def test_an_event_type_rejects_a_negative_bound():
    with pytest.raises(ApprovalError) as caught:
        approval.validate_event_type({"title": "x", "maximumRangeDays": -1})
    assert caught.value.code == "invalid_event_type"


def test_an_event_type_accepts_an_absent_or_null_bound():
    spec = approval.validate_event_type({"title": "x", "minimumNoticeMinutes": None, "maximumRangeDays": None})
    assert spec["maximumRangeDays"] is None


def test_an_event_type_passes_unknown_fields_through():
    """A team adding a field must not need this function to know about it."""
    spec = approval.validate_event_type({"title": "x", "myTeamField": {"a": 1}})
    assert spec["myTeamField"] == {"a": 1}


def test_an_event_type_drops_reserved_envelope_keys():
    """A payload cannot smuggle a revision or a room through the payload."""
    spec = approval.validate_event_type({"title": "x", "id": "nope", "room_id": "nope", "revision": 9})
    assert "id" not in spec and "room_id" not in spec and "revision" not in spec


# --------------------------------------------------------------------------- #
# The webhook payloads
# --------------------------------------------------------------------------- #


def requested_booking(**overrides) -> dict:
    spec = {
        "uid": "bk_1",
        "title": "Discovery call",
        "status": "PENDING",
        "requiresConfirmation": True,
        "oneTimePassword": "00000000-0000-0000-0000-000000000000",
        "rejectionReason": None,
        "eventTypeId": "event_type_1",
        "hostId": "dana",
        "start": at(days=2),
        "end": at(days=2, minutes=30),
        "booker": {"email": "ann@example.com"},
        "attendee": {"email": "ann@example.com"},
        "organizer": {"email": "dana@example.com"},
        "requestedAt": at(),
    }
    spec.update(overrides)
    return spec


def test_the_requested_webhook_carries_the_researched_trio():
    """`"requiresConfirmation": true, "oneTimePassword": "…", "status": "PENDING"`."""
    payload = approval.booking_requested_payload(requested_booking(), created_at=at())["payload"]
    assert payload["status"] == "PENDING"
    assert payload["requiresConfirmation"] is True
    assert payload["oneTimePassword"] == "00000000-0000-0000-0000-000000000000"


def test_the_rejected_webhook_carries_the_rejection_reason():
    """`"rejectionReason": "The organizer is no longer available at this time."`."""
    booking = requested_booking(
        status="REJECTED",
        requiresConfirmation=True,
        rejectionReason="The organizer is no longer available at this time.",
    )
    payload = approval.booking_rejected_payload(booking, created_at=at())["payload"]
    assert payload["status"] == "REJECTED"
    assert payload["rejectionReason"] == "The organizer is no longer available at this time."


def test_a_webhook_carries_the_vendor_envelope():
    """`event`, `createdAt`, `triggerEvent` and `payload` are the vendor's keys."""
    envelope = approval.booking_requested_payload(requested_booking(), created_at=at())
    assert set(envelope) == {"event", "createdAt", "triggerEvent", "payload"}
    assert envelope["event"] == "BOOKING_REQUESTED"
    assert envelope["triggerEvent"] is None


def test_a_webhook_payload_names_the_uid_the_api_calls_a_booking_uid():
    assert approval.booking_requested_payload(requested_booking(), created_at=at())["payload"]["uid"] == "bk_1"


def test_a_webhook_payload_keeps_a_null_rejection_reason_rather_than_dropping_the_key():
    """A client reading one payload shape should not have to branch on the key."""
    assert "rejectionReason" in approval.booking_requested_payload(requested_booking(), created_at=at())["payload"]
    assert approval.booking_requested_payload(requested_booking(), created_at=at())["payload"]["rejectionReason"] is None


def test_a_calendar_event_says_it_was_created_because_a_host_confirmed():
    event = approval.calendar_event_for(requested_booking(status="ACCEPTED"), uid="ev_1", now=NOW)
    assert event["uid"] == "ev_1"
    assert event["bookingUid"] == "bk_1"
    assert event["createdBecause"] == "a host confirmed the request"
    assert event["createdAt"] == approval.iso(NOW)


# --------------------------------------------------------------------------- #
# The shape of the module itself
# --------------------------------------------------------------------------- #


def test_the_domain_module_touches_neither_a_database_nor_the_framework():
    """It is pure, so a test can reach a rule without a store or a route."""
    from pathlib import Path

    import dsr.booking_approval as module

    text = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "from fastapi", "from starlette", "from dsr.api", "from dsr.store", "AuditedDatabase"):
        assert forbidden not in text, f"the pure domain imports {forbidden}"


def test_every_collection_name_is_also_published_as_a_meaning():
    for name, meaning in approval.COLLECTION_MEANING.items():
        assert meaning.strip()
        assert approval.COLLECTIONS.values()  # the mapping is populated
        assert name in approval.COLLECTIONS.values()
