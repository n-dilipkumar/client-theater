"""WF-060: the consent and recording rules, tested without a request.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-060.md``.
These tests are organised by the decision they defend, because the point of this
workflow is that the decisions were *derived* - the research says so itself: "This
spec does not state the flow as separate fields. The evidence below is the
specification. An implementer who needs a flow the evidence does not contain must
derive it and record the derivation, not assume it."

So a derivation with no test is a derivation the next person will quietly change.
The sections below, and what each pins:

``vocabulary``
    The researched terms, and that they are the terms. A state machine is only
    checkable if the set of states is closed.
``profiles``
    Steps 1 to 5. The cross-field rules, the per-provider link kinds, and the
    unknown-variable refusal that stops a consent notice shipping with a token
    printed inside it.
``directory``
    Step 6, and the key the profile resolves on. This is the load-bearing decision:
    resolve on the room and one seller's consent settings apply to another seller's
    meeting.
``decisions``
    The derived state machine, including both answers the research leaves open.
``links``
    Step 7. The researched request fields, the two documented failures, and
    stale-link invalidation as a state rather than a delete.
``precall``
    Step 4. The 10-to-20-minute window as a range, the external audience, and the
    researched variables.
``inferences``
    That every judgement call was recorded, and that the four open points the issue
    names are among them.
``engine``
    The flow over the audited store, including that the seed string survives a
    Windows console.
``isolation``
    That this workflow reads and writes only its own collections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.recording_consent import (
    ConsentEngine,
    decisions,
    directory,
    inferences,
    links,
    precall,
    profiles,
    vocabulary as vocab,
)
from dsr.recording_consent.errors import (
    BookingNotFound,
    ConsentPageDisabled,
    IllegalTransition,
    JoinWithoutConsentRefused,
    LinkSuperseded,
    OrganizerUnmapped,
    ProfileInvalid,
    ProfileNotFound,
)
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def clock():
    """A clock the test moves by hand.

    The pre-call window, the supersede stamp and the open stamp are all statements
    about an instant, so they can only be tested against a clock the test owns.
    """

    class Clock:
        def __init__(self) -> None:
            self.at = NOW

        def __call__(self) -> datetime:
            return self.at

        def set(self, moment: datetime) -> datetime:
            self.at = moment
            return self.at

    return Clock()


@pytest.fixture()
def engine(store: RecordStore, clock) -> ConsentEngine:
    """An engine over a fresh, empty store and the test's clock."""
    return ConsentEngine(store, now=clock)


def enforcing_profile(**overrides) -> dict:
    """A profile with the consent page on and enforcement on."""
    payload = {
        "name": "Standard recording consent",
        "description": "This call will be recorded.",
        "consent_page_enabled": True,
        "enforce_consent_page": True,
        "allow_join_without_consent": True,
        "providers": {"zoom": "dynamic_link"},
        "default_provider": "zoom",
    }
    payload.update(overrides)
    return payload


def advisory_profile(**overrides) -> dict:
    """A profile with the consent page on and enforcement OFF.

    The researched open point: "Decide what the gate does when enforcement is off.
    The evidence does not say."
    """
    payload = {
        "name": "Advisory consent",
        "consent_page_enabled": True,
        "enforce_consent_page": False,
        "providers": {"zoom": "dynamic_link"},
        "default_provider": "zoom",
    }
    payload.update(overrides)
    return payload


INVITEES = [
    {"email": "buyer@northwind.example", "name": "Buyer"},
    {"email": "analyst@contoso.example", "name": "Analyst"},
]


def booking_payload(booking_id: str = "b-1", organizer: str = "dana@northwind.example") -> dict:
    return {
        "booking_id": booking_id,
        "organizer_email": organizer,
        "title": "Northwind walkthrough",
        "start_time": (NOW + timedelta(days=1)).isoformat(),
        "end_time": (NOW + timedelta(days=1, minutes=30)).isoformat(),
        "invitees": [dict(invitee) for invitee in INVITEES],
    }


@pytest.fixture()
def ready(engine: ConsentEngine):
    """An engine with one enforcing profile and one directory user assigned to it."""
    profile = engine.create_profile(enforcing_profile(), room_id="room-1", source="test")
    engine.add_user(
        {"email": "dana@northwind.example", "name": "Dana", "profile_id": profile["id"]},
        room_id="room-1",
        source="test",
    )
    return engine, profile


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #


def test_the_state_set_is_closed():
    """A machine is only checkable if the set of states is closed."""
    assert len(set(vocab.STATES)) == len(vocab.STATES)
    assert set(vocab.TERMINAL_STATES) <= set(vocab.STATES)
    assert set(vocab.CONSENT_STATES) <= set(vocab.CONSENT_STATES)
    assert set(vocab.RECORDING_STATES) == {
        "blocked",
        "armed",
        "in_progress",
        "complete",
        "cancelled",
    }


def test_the_four_researched_providers_and_three_link_kinds():
    """Step 3 names four providers and three link kinds. No more, no fewer."""
    assert set(vocab.PROVIDERS) == {"zoom", "google_meet", "microsoft_teams", "webex"}
    assert vocab.LINK_KINDS == ("dynamic_link", "static_link", "host_decides")


def test_the_precall_window_is_the_researched_range():
    """ "Automatically send pre-call emails to external invitees between 10 and 20 minutes"."""
    assert vocab.PRECALL_WINDOW_MINUTES == (10, 20)


def test_the_recording_bot_is_the_researched_example_address():
    """ "additionalInvitees ... should support adding email addresses such as coordinator@gong.io"."""
    assert vocab.RECORDING_BOT_EMAIL == "coordinator@gong.io"


def test_the_two_documented_failures_are_quoted_not_paraphrased():
    """A caller comparing our refusal to Gong's should not need two codebooks."""
    assert vocab.DOCUMENTED_ERRORS[409] == (
        "Conflict, e.g. consent page is not enabled in your company"
    )
    assert vocab.DOCUMENTED_ERRORS[404] == (
        "No Gong user found corresponding to the provided organizer email"
    )


def test_the_prompt_choice_is_recorded_and_the_other_mode_still_accepted():
    """The research offers both modes and chooses neither. This pins the choice.

    It also pins that ``every_guest`` remains a legal value, because a derivation
    recorded as a decision is different from a decision disguised as a restriction.
    """
    assert vocab.DEFAULT_PROMPT_MODE == "first_guest_with_audio"
    assert "every_guest" in vocab.PROMPT_MODES
    assert (
        profiles.normalise(
            enforcing_profile(audio_prompt_enabled=True, audio_prompt={"mode": "every_guest"})
        )["audio_prompt"]["mode"]
        == "every_guest"
    )


# --------------------------------------------------------------------------- #
# profiles: steps 1 to 5
# --------------------------------------------------------------------------- #


def test_a_valid_profile_normalises_every_switch():
    result = profiles.normalise(enforcing_profile(audio_prompt_enabled=True))
    assert result["consent_page_enabled"] is True
    assert result["enforce_consent_page"] is True
    assert result["allow_join_without_consent"] is True
    assert result["providers"] == {"zoom": "dynamic_link"}
    assert result["default_provider"] == "zoom"
    # A switch that was never sent is off, not absent. An administrator who never
    # opened the recording settings has not enabled anything.
    assert result["precall_email_enabled"] is False
    assert result["is_default"] is False


def test_a_profile_without_a_name_is_refused():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(enforcing_profile(name="  "))
    assert "name" in caught.value.errors


def test_a_non_object_payload_is_refused_with_a_body_error():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(["not", "an", "object"])
    assert caught.value.errors["body"] == "must be an object"


def test_an_unknown_provider_is_refused_and_the_research_named_set_is_offered():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(enforcing_profile(providers={"skype": "dynamic_link"}))
    message = caught.value.errors["providers.skype"]
    assert "unknown provider" in message
    for provider in vocab.PROVIDERS:
        assert provider in message


def test_an_unknown_link_kind_is_refused_and_the_research_named_set_is_offered():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(enforcing_profile(providers={"zoom": "magic_link"}))
    message = caught.value.errors["providers.zoom"]
    for kind in vocab.LINK_KINDS:
        assert kind in message


def test_a_provider_may_be_given_as_a_one_key_object():
    """A form posts a bare string and a config file carries an object. Both are real."""
    result = profiles.normalise(
        enforcing_profile(providers={"webex": {"link_kind": "static_link"}}, default_provider=None)
    )
    assert result["providers"] == {"webex": "static_link"}
    assert result["default_provider"] == "webex"


def test_a_default_provider_must_name_a_provider_the_profile_carries():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(
            enforcing_profile(providers={"zoom": "dynamic_link"}, default_provider="webex")
        )
    assert "must name a provider this profile carries" in caught.value.errors["default_provider"]


def test_a_default_provider_is_derived_when_only_one_provider_is_present():
    """Step 3's "Sets a default provider" is the common case of exactly one."""
    result = profiles.normalise(
        enforcing_profile(providers={"microsoft_teams": "host_decides"}, default_provider=None)
    )
    assert result["default_provider"] == "microsoft_teams"
    # The provider the default was derived from is still on the profile. A derivation
    # that consumed the value it chose would leave the profile carrying a default that
    # names a provider it does not have.
    assert result["providers"] == {"microsoft_teams": "host_decides"}


def test_an_unsupported_language_is_refused():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(enforcing_profile(locales=["en", "kl"]))
    assert "unsupported language" in caught.value.errors["locales"]


def test_every_bad_field_is_reported_in_one_pass():
    """An administrator filling in one form sees every problem with it at once."""
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise({"name": "", "providers": {"skype": "x"}, "locales": ["kl"]})
    assert {"name", "locales"} <= set(caught.value.errors)


# -- the pre-call email ----------------------------------------------------- #


def test_an_unknown_email_variable_is_refused_not_shipped():
    """A consent notice that reaches a buyer with a token printed inside it is worse than one
    that refuses to save. The only legal variables are the four the research names."""
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(
            enforcing_profile(
                precall_email_enabled=True,
                precall_email={"subject": "Hi", "body": "Call at {{unknown_token}}"},
            )
        )
    assert "{{unknown_token}}" in caught.value.errors["precall_email.tokens"]


def test_every_researched_variable_is_accepted():
    body = " ".join(vocab.PRECALL_EMAIL_VARIABLES)
    result = profiles.normalise(
        enforcing_profile(
            precall_email_enabled=True, precall_email={"subject": "Call", "body": body}
        )
    )
    assert len(result["precall_email"]["variables"]) == len(vocab.PRECALL_EMAIL_VARIABLES)


def test_an_unclosed_variable_is_refused():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(
            enforcing_profile(
                precall_email_enabled=True,
                precall_email={"subject": "Call", "body": "{{meeting_title is open"},
            )
        )
    assert "unclosed" in caught.value.errors["precall_email.body"]


def test_a_precall_email_without_a_subject_is_refused():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.normalise(
            enforcing_profile(precall_email_enabled=True, precall_email={"body": "Hello"})
        )
    assert "is required" in caught.value.errors["precall_email.subject"]


# -- patch ------------------------------------------------------------------ #


def test_a_patch_revalidates_the_whole_profile_not_only_the_fields_sent():
    """Turning the consent page on makes the profile invalid without a provider, and the
    request that turns it on is rarely the request that adds the provider. Validating only
    the fields that changed would let the profile through in a state no request asked for.

    Note that neither field in this patch is the switch. The profile is refused because the
    *combination* is impossible, which is the whole point of revalidating.

    Both values are ``null`` rather than empty containers: a merge would keep the stored
    provider, and the tri-state is the only way a PATCH can clear a value at all."""
    existing = profiles.normalise(enforcing_profile())
    with pytest.raises(ProfileInvalid) as caught:
        profiles.patch(existing, {"providers": None, "default_provider": None})
    assert "at least one provider is required" in caught.value.errors["providers"]


def test_a_patch_clears_a_value_with_null_and_merges_with_an_object():
    """Three states for one field: absent leaves it, an object merges, null replaces.
    A merge that could not clear would let a caller believe they had removed a
    provider and had not."""
    existing = profiles.normalise(
        enforcing_profile(providers={"zoom": "dynamic_link", "webex": "static_link"})
    )

    # An object merges: the stored providers survive and one is added.
    merged = profiles.patch(existing, {"providers": {"google_meet": "dynamic_link"}})
    assert merged["providers"] == {
        "zoom": "dynamic_link",
        "webex": "static_link",
        "google_meet": "dynamic_link",
    }

    # Null on a text field empties it.
    cleared_text = profiles.patch(existing, {"logo_url": None})
    assert cleared_text["logo_url"] == ""

    # Null on an object field replaces it outright, which is the only way to empty
    # `providers`. Both fields have to be cleared together: leaving the stored
    # `default_provider` pointing at a provider the profile no longer carries is a
    # second, separately-reported error, and that is the revalidation earning its keep.
    with pytest.raises(ProfileInvalid) as caught:
        profiles.patch(existing, {"consent_page_enabled": False, "providers": None})
    assert caught.value.errors["default_provider"] == "must name a provider this profile carries"

    replaced = profiles.patch(
        existing,
        {"consent_page_enabled": False, "providers": None, "default_provider": None},
    )
    assert replaced["providers"] == {}
    assert replaced["consent_page_enabled"] is False
    # The default provider is not silently invented either.
    assert replaced["default_provider"] is None


def test_a_patch_may_not_rewrite_a_field_that_is_not_patchable():
    with pytest.raises(ProfileInvalid) as caught:
        profiles.patch(profiles.normalise(enforcing_profile()), {"id": "somewhere-else"})
    assert caught.value.errors["id"] == "is not a patchable field"


def test_a_patch_merges_nested_objects_rather_than_replacing_them():
    existing = profiles.normalise(
        enforcing_profile(
            audio_prompt_enabled=True, audio_prompt={"mode": "first_guest_with_audio"}
        )
    )
    patched = profiles.patch(existing, {"audio_prompt": {"suppress_when_consent_page_used": False}})
    assert patched["audio_prompt"]["mode"] == "first_guest_with_audio"
    assert patched["audio_prompt"]["suppress_when_consent_page_used"] is False


# --------------------------------------------------------------------------- #
# directory: step 6, and the resolution key
# --------------------------------------------------------------------------- #


def test_emails_fold_case_and_space_but_nothing_else():
    """The address is an opaque key into a vendor directory. Rewriting a local part or
    stripping a +tag would resolve to a different user, or to no user."""
    assert directory.normalise_email("  Dana@Northwind.Example ") == "dana@northwind.example"
    assert directory.normalise_email("dana+tag@northwind.example") == "dana+tag@northwind.example"


def test_the_profile_resolves_on_the_organiser_email_not_the_room():
    """organizerEmail: 'the Gong consent page link will be used according to the settings of
    this user.' This test is the assertion the issue asks for: 'Record which key you resolve on'."""
    assert vocab.PROFILE_RESOLUTION_KEY == "organizer_email"

    users = [
        directory.normalise_user(
            {"email": "dana@northwind.example", "name": "Dana", "profile_id": "p-dana"}
        ),
        directory.normalise_user(
            {"email": "sam@northwind.example", "name": "Sam", "profile_id": "p-sam"}
        ),
    ]
    # No room id is passed and none is needed. Resolution cannot even be expressed in
    # terms of a room, which is the strongest form of the assertion available.
    assert directory.resolve(users, "sam@northwind.example")["profile_id"] == "p-sam"
    assert directory.resolve(users, "dana@northwind.example")["profile_id"] == "p-dana"


def test_an_unmapped_organiser_raises_the_documented_404():
    users = [directory.normalise_user({"email": "dana@northwind.example", "name": "Dana"})]
    with pytest.raises(OrganizerUnmapped) as caught:
        directory.resolve(users, "stranger@elsewhere.example")
    assert vocab.DOCUMENTED_ERRORS[404] in str(caught.value)


def test_resolution_records_which_of_the_three_sources_was_used():
    users = [
        directory.normalise_user({"email": "a@x.example", "name": "A", "profile_id": "p-assigned"}),
        directory.normalise_user(
            {
                "email": "b@x.example",
                "name": "B",
                "is_default_for_new_members": True,
            }
        ),
        directory.normalise_user({"email": "c@x.example", "name": "C"}),
    ]
    assert directory.resolve(users, "a@x.example", "p-default")["source"] == "assigned"
    assert (
        directory.resolve(users, "b@x.example", "p-default")["source"] == "default_for_new_members"
    )
    assert directory.resolve(users, "c@x.example", "p-default")["source"] == "organisation_default"
    # With no fallback at all, the resolution succeeds and says so rather than
    # inventing a profile.
    assert directory.resolve(users, "c@x.example")["source"] == "unprofiled"


def test_an_invitee_may_block_its_own_recording_and_the_addresses_come_back():
    """The researched third flag: 'if the invitation of this user to a web conference will
    prevent its recording'. Returns addresses, not a bool, so the caller can say which one."""
    users = [
        directory.normalise_user(
            {"email": "guest@blocked.example", "name": "Guest", "blocks_recording": True}
        )
    ]
    assert directory.recording_blocked_by_invitee(users, INVITEES) == []
    found = directory.recording_blocked_by_invitee(
        users, [*INVITEES, {"email": "guest@blocked.example", "name": "Guest"}]
    )
    assert found == ["guest@blocked.example"]


def test_a_blocking_invitee_is_found_in_either_invitee_shape():
    """The research's invitees are objects and a caller's list is often bare addresses.
    Stringifying a dict produces something that matches no directory entry, so the
    failure would look like 'no blockers' rather than like a bug."""
    users = [
        directory.normalise_user(
            {"email": "guest@blocked.example", "name": "Guest", "blocks_recording": True}
        )
    ]
    assert directory.recording_blocked_by_invitee(users, ["guest@blocked.example"]) == [
        "guest@blocked.example"
    ]
    assert directory.recording_blocked_by_invitee(users, [{"email": "guest@blocked.example"}]) == [
        "guest@blocked.example"
    ]


def test_the_organisers_own_record_by_gong_flag_is_read():
    recordable = [directory.normalise_user({"email": "d@x.example", "name": "D"})]
    blocked = [
        directory.normalise_user({"email": "d@x.example", "name": "D", "record_by_gong": False})
    ]
    assert directory.can_record(recordable, "d@x.example") == (True, "")
    ok, why = directory.can_record(blocked, "d@x.example")
    assert ok is False
    assert "record_by_gong" in why


def test_a_directory_entry_needs_an_email_and_a_name():
    with pytest.raises(ProfileInvalid) as caught:
        directory.normalise_user({"name": "No Email"})
    assert "resolves on the organiser email" in caught.value.errors["email"]
    with pytest.raises(ProfileInvalid) as caught:
        directory.normalise_user({"email": "dana@northwind.example"})
    assert caught.value.errors["name"] == "is required"


# --------------------------------------------------------------------------- #
# decisions: the derived state machine
# --------------------------------------------------------------------------- #


def test_an_enforcing_profile_starts_awaiting_consent_with_the_recording_blocked():
    """The gate made visible before any call happens. Without the blocked state, a booking
    that requires consent and one that does not look identical until someone answers."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    assert machine.state == "awaiting_consent"
    assert machine.consent_state == "pending"
    assert machine.recording_state == "blocked"
    assert machine.enforced is True


def test_consent_is_required_only_when_the_page_and_enforcement_are_both_on():
    """The derivation the research does not state. Both switches, and the conjunction."""
    assert decisions.consent_required({"consent_page_enabled": True, "enforce_consent_page": True})
    assert not decisions.consent_required(
        {"consent_page_enabled": True, "enforce_consent_page": False}
    )
    assert not decisions.consent_required(
        {"consent_page_enabled": False, "enforce_consent_page": True}
    )
    assert not decisions.consent_required({})


def test_a_grant_arms_the_recording():
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    moved = decisions.advance(machine, "grant_consent", {})
    assert moved.state == "consented"
    assert moved.recording_state == "armed"


def test_a_decline_under_enforcement_cancels_the_recording():
    """ "consent page enforcement cancels recording when declined"."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    moved = decisions.advance(machine, "decline_consent", {})
    assert moved.state == "declined"
    assert moved.consent_state == "declined"
    assert moved.recording_state == "cancelled"


def test_a_decline_with_enforcement_off_records_the_decline_and_keeps_recording():
    """The open point: 'Decide what the gate does when enforcement is off. The evidence
    does not say.' This is the answer, and it is the answer this workflow is judged on.

    Enforcement is what makes a decision binding, so with it clear the page is
    advisory: the decision is still stored as evidence and the recording proceeds.
    The record carries consent_state 'declined' with enforced false, so the situation
    is visible to a reviewer rather than buried."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": False})
    moved = decisions.advance(machine, "decline_consent", {})
    assert moved.consent_state == "declined"
    assert moved.recording_state == "armed"
    assert moved.enforced is False
    assert decisions.is_consistent(moved) == (True, "")


def test_joining_without_consent_is_refused_where_the_profile_does_not_allow_it():
    """With the switch off, the participant is not admitted. That refusal is the only
    reading under which the switch has an effect at all."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    with pytest.raises(JoinWithoutConsentRefused) as caught:
        decisions.advance(machine, "join_without_consent", {})
    assert vocab.JOIN_WITHOUT_CONSENT_SWITCH in str(caught.value)


def test_joining_without_consent_cancels_the_recording_where_the_profile_allows_it():
    """Step 2: 'Allow participants to join without giving consent (recording will be
    canceled)'. The parenthetical is the researched consequence."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    moved = decisions.advance(
        machine, "join_without_consent", {vocab.JOIN_WITHOUT_CONSENT_SWITCH: True}
    )
    assert moved.state == "joined_without_consent"
    assert moved.recording_state == "cancelled"


def test_the_join_switch_defaults_to_closed_when_no_profile_is_passed():
    """A caller that forgets to pass the profile must get the safe answer, not the
    permissive one."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    with pytest.raises(JoinWithoutConsentRefused):
        decisions.advance(machine, "join_without_consent", None)


def test_a_blocked_recording_cannot_be_started():
    """This is the property the whole workflow exists for. A booking whose profile requires
    consent cannot reach 'recorded' without a grant, because 'start_recording' is refused
    from every state whose recording is not armed."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    assert machine.recording_state == "blocked"
    with pytest.raises(IllegalTransition):
        decisions.advance(machine, "start_recording", {})
    assert "start_recording" not in decisions.allowed_steps("awaiting_consent")


def test_every_state_the_machine_can_reach_has_a_consistent_pair_of_axes():
    """The axes are stored separately so they can be queried, and separate storage is
    separate truth unless something checks it."""
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    for step in decisions.STEPS:
        if step in decisions.allowed_steps(machine.state):
            try:
                candidate = decisions.advance(machine, step, {})
            except (IllegalTransition, JoinWithoutConsentRefused):
                continue
            ok, why = decisions.is_consistent(candidate)
            assert ok, f"{step} produced an inconsistent state: {why}"
            assert candidate.state in vocab.STATES


def test_an_unknown_step_names_the_state_and_the_alternatives():
    machine = decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True})
    with pytest.raises(IllegalTransition) as caught:
        decisions.advance(machine, "teleport", {})
    assert caught.value.current == "awaiting_consent"
    assert caught.value.step == "teleport"
    assert "grant_consent" in caught.value.allowed


def test_an_unreadable_stored_state_is_refused_rather_than_defaulted():
    """A silent default here would let the machine move from a state nobody stored."""
    with pytest.raises(IllegalTransition):
        decisions.machine_from_data({"state": "invented"})


def test_a_stored_record_round_trips_through_the_machine():
    original = decisions.advance(
        decisions.initial({"consent_page_enabled": True, "enforce_consent_page": True}),
        "grant_consent",
        {},
    )
    assert decisions.machine_from_data(original.to_data()) == original


def test_contradictory_axes_are_detected():
    """A record whose state says 'recorded' while its recording axis says 'cancelled' is a
    compliance record that contradicts itself."""
    machine = decisions.machine_from_data(
        {
            "state": "recorded",
            "consent_state": "granted",
            "recording_state": "cancelled",
            "enforced": True,
        }
    )
    ok, why = decisions.is_consistent(machine)
    assert ok is False
    assert "recorded" in why


def test_an_enforced_decline_with_an_armed_recording_is_detected():
    """The contradiction that matters most: enforcement says this must not record, and the
    recording axis says it is armed."""
    machine = decisions.machine_from_data(
        {
            "state": "declined",
            "consent_state": "declined",
            "recording_state": "armed",
            "enforced": True,
        }
    )
    ok, why = decisions.is_consistent(machine)
    assert ok is False
    assert "enforcement cancels the recording" in why


# --------------------------------------------------------------------------- #
# links: step 7
# --------------------------------------------------------------------------- #


def test_the_meeting_request_carries_exactly_the_six_researched_fields():
    request = links.new_meeting_request(
        organizer_email="dana@northwind.example",
        start_time="2026-10-05T09:00:00+00:00",
        end_time="2026-10-05T09:30:00+00:00",
        title="Walkthrough",
        invitees=INVITEES,
        external_id="b-1",
        provider="zoom",
    )
    assert set(request["body"]) == set(vocab.NEW_MEETING_REQUEST_FIELDS)
    assert request["endpoint"] == vocab.MEETINGS_ENDPOINT
    assert request["scope"] == vocab.MEETING_CREATE_SCOPE


def test_every_missing_request_field_is_reported_by_name():
    with pytest.raises(ProfileInvalid) as caught:
        links.new_meeting_request(
            organizer_email="",
            start_time="",
            end_time="",
            title="",
            invitees=[],
            external_id="",
            provider="skype",
        )
    assert set(caught.value.errors) == {
        "organizer_email",
        "start_time",
        "end_time",
        "title",
        "invitees",
        "external_id",
        "provider",
    }


def test_a_409_response_becomes_the_consent_page_refusal():
    """Gong documents 409 for exactly this state. Letting it become a link would produce a
    booking that looks successful and records nothing."""
    with pytest.raises(ConsentPageDisabled) as caught:
        links.new_meeting_response({"status": 409}, {}, vocab.RECORDING_BOT_EMAIL)
    assert vocab.DOCUMENTED_ERRORS[409] in str(caught.value)


def test_a_404_response_becomes_the_unmapped_organiser_refusal():
    with pytest.raises(OrganizerUnmapped) as caught:
        links.new_meeting_response({"status": 404}, {}, vocab.RECORDING_BOT_EMAIL)
    assert vocab.DOCUMENTED_ERRORS[404] in str(caught.value)


def test_a_response_without_a_meeting_url_is_refused():
    with pytest.raises(ProfileInvalid) as caught:
        links.new_meeting_response({"meetingId": "m-1"}, {}, vocab.RECORDING_BOT_EMAIL)
    assert "meetingUrl" in caught.value.errors


def test_a_response_without_additional_invitees_falls_back_and_says_so():
    """ "additionalInvitees" is the research's source of truth for the bot. The fallback is
    recorded so a reviewer can tell which of the two produced the address."""
    result = links.new_meeting_response(
        {"meetingId": "m-1", "meetingUrl": "https://x.example/m"}, {}, vocab.RECORDING_BOT_EMAIL
    )
    assert result["additional_invitees"] == [vocab.RECORDING_BOT_EMAIL]
    assert result["additional_invitees_source"] == "fallback"

    given = links.new_meeting_response(
        {"meetingId": "m-1", "meetingUrl": "https://x.example/m", "additionalInvitees": ["a@x"]},
        {},
        vocab.RECORDING_BOT_EMAIL,
    )
    assert given["additional_invitees"] == ["a@x"]
    assert given["additional_invitees_source"] == "response"


def test_a_previous_link_is_disabled_as_a_state_and_never_deleted():
    """ "Each time you change this link, the previous link is disabled."

    Implemented as a state so the audit row for the link that was replaced survives.
    A delete would make "which link did the buyer actually join" unanswerable for
    exactly the bookings where it matters, which is a booking whose link changed."""
    old = links.link_record(
        {"meeting_id": "m-1", "meeting_url": "https://x/1", "provider": "zoom"},
        "b-1",
        "p-1",
        "2026-10-04T09:00:00.000+00:00",
    )
    new = links.link_record(
        {"meeting_id": "m-2", "meeting_url": "https://x/2", "provider": "zoom"},
        "b-1",
        "p-1",
        "2026-10-04T10:00:00.000+00:00",
    )
    retired = links.retire_previous(old, new, "2026-10-04T10:00:00.000+00:00")
    assert retired["link_state"] == "superseded"
    assert retired["superseded_by"] == "m-2"
    assert retired["superseded_at"] == "2026-10-04T10:00:00.000+00:00"
    # Every field the record had before is still on it. Nothing was dropped.
    assert retired["meeting_url"] == old["meeting_url"]
    assert vocab.LINK_STATES == ("active", "superseded")


def test_retiring_an_already_superseded_link_does_not_rewrite_its_history():
    """A second change must not stamp a second superseded_at over the first. That is the
    kind of history rewriting the audit guarantee exists to prevent."""
    old = links.link_record(
        {"meeting_id": "m-1", "meeting_url": "https://x/1"},
        "b-1",
        "p-1",
        "2026-10-04T09:00:00+00:00",
    )
    once = links.retire_previous(old, {"meeting_id": "m-2"}, "2026-10-04T10:00:00+00:00")
    twice = links.retire_previous(once, {"meeting_id": "m-3"}, "2026-10-04T11:00:00+00:00")
    assert twice["superseded_at"] == "2026-10-04T10:00:00+00:00"
    assert twice["superseded_by"] == "m-2"


def test_the_audio_prompt_is_suppressed_only_when_the_page_was_actually_used():
    """Step 5: 'Don't play the audio prompt if the consent page is used'. A profile with
    the page off has no page to make the prompt redundant."""
    page_on = {"audio_prompt_enabled": True, "consent_page_enabled": True}
    assert links.prompt_suppressed(page_on) is True
    assert links.prompt_suppressed({**page_on, "consent_page_enabled": False}) is False
    assert links.prompt_suppressed({"consent_page_enabled": True}) is False
    assert (
        links.prompt_suppressed({**page_on, "suppress_prompt_when_consent_page_used": False})
        is False
    )


# --------------------------------------------------------------------------- #
# precall: step 4
# --------------------------------------------------------------------------- #


def test_the_window_opens_twenty_minutes_before_and_closes_ten_minutes_before():
    start = NOW + timedelta(hours=2)
    opens, closes = precall.window(start)
    assert start - opens == timedelta(minutes=20)
    assert start - closes == timedelta(minutes=10)


def test_the_window_edges_are_inclusive_on_both_ends():
    """ "Between 10 and 20 minutes" reads as inclusive, and a planner polling once a minute
    would otherwise miss exactly the minute the research describes."""
    start = NOW + timedelta(hours=2)
    assert precall.should_send(start - timedelta(minutes=20), start)[0] is True
    assert precall.should_send(start - timedelta(minutes=10), start)[0] is True
    assert precall.should_send(start - timedelta(minutes=21), start) == (False, "before_window")
    assert precall.should_send(start - timedelta(minutes=9), start) == (False, "after_window")


def test_the_recorded_reason_states_the_measured_lead_time():
    """The reason names the minute the planner fired at. Naming a window *edge* instead
    would assert something the research does not claim: there is no preferred minute
    inside a 10-to-20-minute range."""
    start = NOW + timedelta(hours=2)
    early, reason_early = precall.should_send(start - timedelta(minutes=18), start)
    late, reason_late = precall.should_send(start - timedelta(minutes=12), start)
    assert early and late
    assert reason_early == "in_window_at_t_minus_18min"
    assert reason_late == "in_window_at_t_minus_12min"


def test_a_second_send_is_refused_with_a_reason():
    start = NOW + timedelta(hours=2)
    assert precall.should_send(start - timedelta(minutes=15), start, already_sent=True) == (
        False,
        "already_sent",
    )


def test_only_external_invitees_receive_it():
    """ "Automatically send pre-call emails to external invitees". Same domain means internal."""
    external, internal = precall.recipients(INVITEES, "dana@northwind.example")
    assert external == ["analyst@contoso.example"]
    assert internal == ["buyer@northwind.example"]


def test_a_domainless_address_is_not_external():
    """An address with no @ has no domain to compare. Splitting a bare string to itself
    would compare unequal to any real domain and make a malformed address the most
    external thing in the list."""
    assert precall.is_external("buyer", "dana@northwind.example") is False
    assert precall.is_external("buyer@northwind.example", "dana") is False
    assert precall.is_external("", "dana@northwind.example") is False


def test_the_email_renders_every_researched_variable_and_keeps_the_legal_footer():
    profile = profiles.normalise(
        enforcing_profile(
            precall_email_enabled=True,
            precall_email={
                "subject": "{{meeting_title}} at {{meeting_hour}}",
                "body": "{{sender_name}} of {{sender_company}} invites you.",
                "signature": "Regards",
                "legal_footer": "Consent: Standard recording consent.",
            },
        )
    )
    rendered = precall.render(
        profile,
        sender_name="Dana",
        sender_company="Northwind",
        meeting_title="Walkthrough",
        meeting_hour="09:00",
        to=["buyer@contoso.example"],
        now_iso="2026-10-04T09:00:00.000+00:00",
    )
    assert rendered["subject"] == "Walkthrough at 09:00"
    assert "Dana of Northwind invites you." in rendered["body"]
    assert "Consent: Standard recording consent." in rendered["body"]
    assert precall.RECORDING_DISCLOSURE in rendered["disclosure"]
    assert rendered["status"] == "sent"


def test_an_email_without_a_body_is_refused_at_render_time_too():
    """Defence in depth: the validator refuses it on save, and the renderer refuses it on
    use, because the profile could have been written before the rule existed."""
    with pytest.raises(ProfileInvalid):
        precall.render(
            {"precall_email": {"subject": "Hi", "body": ""}},
            sender_name="D",
            sender_company="C",
            meeting_title="T",
            meeting_hour="09:00",
            to=["a@b.example"],
            now_iso="2026-10-04T09:00:00+00:00",
        )


# --------------------------------------------------------------------------- #
# inferences: the derivation record
# --------------------------------------------------------------------------- #


def test_every_judgement_call_is_recorded_with_what_it_rejected():
    """The research requires it: 'must derive it and record the derivation, not assume it'.
    A record with no rejected reading is a record of what was decided, not of why."""
    required = {
        "id",
        "question",
        "decision",
        "evidence",
        "rejected",
        "cost_of_the_rejected_reading",
        "residual_risk",
        "surface",
    }
    for inference in inferences.describe():
        assert required <= set(inference), inference["id"]
        for field in required:
            assert str(inference[field]).strip(), f"{inference['id']}.{field} is empty"


def test_the_four_open_points_the_issue_names_are_all_answered():
    """The issue lists four questions the implementer must answer. Each must have an entry,
    or the workflow has silently guessed on one of them."""
    ids = {inference["id"] for inference in inferences.describe()}
    for required_id in (
        "wf060-enforcement-off-makes-the-page-advisory",
        "wf060-join-without-consent-cancels-only-under-enforcement",
        "wf060-audio-prompt-fires-once-per-call",
        "wf060-a-stale-link-is-disabled-not-deleted",
        "wf060-the-profile-resolves-by-organiser-email",
    ):
        assert required_id in ids


def test_an_inference_is_readable_by_id_and_an_unknown_one_refuses():
    found = inferences.by_id("wf060-audio-prompt-fires-once-per-call")
    assert found["decision"].startswith("On the first guest with audio on")
    assert inferences.by_id("nope") is None
    with pytest.raises(ProfileInvalid):
        inferences.describe_one("nope")


# --------------------------------------------------------------------------- #
# engine: the flow over the audited store
# --------------------------------------------------------------------------- #


def test_opening_a_booking_resolves_the_profile_and_blocks_the_recording(ready):
    engine, profile = ready
    record = engine.open(booking_payload(), room_id="room-1", source="test")
    data = record["data"]
    assert data["profile_id"] == profile["id"]
    assert data["profile_resolution"] == "assigned"
    assert data["profile_resolution_key"] == vocab.PROFILE_RESOLUTION_KEY
    assert data["state"] == "awaiting_consent"
    assert data["recording_state"] == "blocked"
    assert data["bot_invited"] == [vocab.RECORDING_BOT_EMAIL]
    assert data["consent_link"]["link_state"] == "active"


def test_the_stored_record_carries_the_exact_outbound_request(ready):
    """ "mint records the exact outbound request it would send, so a reviewer can read the
    researched call without a network call happening." """
    engine, _ = ready
    record = engine.open(booking_payload(), room_id="room-1", source="test")
    request = record["data"]["outbound_request"]
    assert request["body"]["organizerEmail"] == "dana@northwind.example"
    assert request["body"]["externalId"] == "b-1"
    assert set(request["body"]) == set(vocab.NEW_MEETING_REQUEST_FIELDS)


def test_opening_refuses_when_the_consent_page_is_off(engine):
    """Gong documents this as 409. Reading the switch first avoids spending a call to learn
    a state the product already knows."""
    profile = engine.create_profile(
        enforcing_profile(consent_page_enabled=False), room_id="room-1", source="test"
    )
    engine.add_user(
        {"email": "dana@northwind.example", "name": "Dana", "profile_id": profile["id"]},
        room_id="room-1",
        source="test",
    )
    with pytest.raises(ConsentPageDisabled) as caught:
        engine.open(booking_payload(), room_id="room-1", source="test")
    assert vocab.DOCUMENTED_ERRORS[409] in str(caught.value)


def test_opening_refuses_an_organiser_with_no_directory_entry(ready):
    engine, _ = ready
    with pytest.raises(OrganizerUnmapped):
        engine.open(booking_payload(organizer="stranger@elsewhere.example"), source="test")


def test_opening_refuses_an_invitee_who_prevents_recording(ready):
    """The researched third flag. Refused before the invite goes out, so the seller learns
    of it while there is still something to do."""
    engine, _ = ready
    engine.add_user(
        {
            "email": "guest@blocked.example",
            "name": "Guest",
            "blocks_recording": True,
        },
        room_id="room-1",
        source="test",
    )
    payload = booking_payload()
    payload["invitees"] = [*INVITEES, {"email": "guest@blocked.example", "name": "Guest"}]
    with pytest.raises(ProfileInvalid) as caught:
        engine.open(payload, room_id="room-1", source="test")
    assert "guest@blocked.example" in str(caught.value)


def test_opening_refuses_an_organiser_who_is_not_recordable(ready):
    engine, profile = ready
    engine.add_user(
        {
            "email": "sam@northwind.example",
            "name": "Sam",
            "profile_id": profile["id"],
            "record_by_gong": False,
        },
        room_id="room-1",
        source="test",
    )
    with pytest.raises(ProfileInvalid) as caught:
        engine.open(booking_payload(organizer="sam@northwind.example"), source="test")
    assert "record_by_gong" in str(caught.value)


def test_a_booking_without_an_organiser_email_is_refused(ready):
    engine, _ = ready
    payload = booking_payload()
    del payload["organizer_email"]
    with pytest.raises(ProfileInvalid) as caught:
        engine.open(payload, source="test")
    assert vocab.PROFILE_RESOLUTION_KEY in caught.value.errors


def test_the_full_happy_path_reaches_recorded_and_writes_a_run_per_step(ready):
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    engine.decide("b-1", "granted", source="test")
    engine.start_recording("b-1", source="test")
    finished = engine.finish_recording("b-1", source="test")
    assert finished["data"]["state"] == "recorded"
    assert finished["data"]["recording_state"] == "complete"
    assert finished["data"]["terminal"] is True

    events = [run["data"]["event"] for run in engine.runs("b-1")]
    assert events == ["opened", "granted", "start_recording", "finish_recording"]


def test_a_declined_booking_never_becomes_recorded(ready):
    """The seed's named non-success, tested as a rule rather than as demo data."""
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    declined = engine.decide("b-1", "declined", source="test")
    assert declined["data"]["recording_state"] == "cancelled"
    with pytest.raises(IllegalTransition):
        engine.start_recording("b-1", source="test")
    ended = engine.cancel_recording("b-1", source="test")
    assert ended["data"]["state"] == "cancelled"


def test_an_unknown_decision_is_refused_by_name(ready):
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    with pytest.raises(ProfileInvalid) as caught:
        engine.decide("b-1", "maybe", source="test")
    for decision in vocab.DECISIONS:
        assert decision in caught.value.errors["decision"]


def test_deciding_on_an_unknown_booking_is_refused(ready):
    engine, _ = ready
    with pytest.raises(BookingNotFound) as caught:
        engine.decide("no-such-booking", "granted", source="test")
    assert caught.value.errors["booking_id"] == "no consent record for this booking"


def test_reading_an_unknown_profile_is_a_404_shaped_refusal(engine):
    with pytest.raises(ProfileNotFound):
        engine.profile("no-such-profile")


def test_reissuing_a_link_supersedes_the_previous_one_and_keeps_it_readable(ready):
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    engine.decide("b-1", "granted", source="test")
    reissued = engine.reissue_link("b-1", source="test")
    data = reissued["data"]

    assert data["link_change_count"] == 1
    assert data["consent_link"]["link_state"] == "active"
    # The previous link is still on the record, disabled rather than deleted.
    assert data["superseded_link"]["link_state"] == "superseded"
    assert data["superseded_link"]["superseded_by"] == data["consent_link"]["meeting_id"]

    assert engine.current_link("b-1")["meeting_url"] == data["consent_link"]["meeting_url"]


def test_reading_a_superseded_link_is_a_409_shaped_refusal(ready):
    """The question a caller with an already-sent invite has: is this link still current?

    The booking's *current* link is never superseded after a reissue, because a reissue
    installs the replacement as the current one and keeps the old one under
    `superseded_link`. So the refused case is an older meeting_id being asked about,
    which is exactly what a calendar integration holds."""
    engine, _ = ready
    with pytest.raises(BookingNotFound):
        engine.current_link("b-1")

    record = engine.open(booking_payload(), room_id="room-1", source="test")
    first_meeting_id = record["data"]["consent_link"]["meeting_id"]
    engine.reissue_link("b-1", source="test")

    # The current link reads fine.
    assert engine.current_link("b-1")["meeting_id"] != first_meeting_id
    # The one in the invite that was already sent does not.
    with pytest.raises(LinkSuperseded) as caught:
        engine.current_link("b-1", first_meeting_id)
    assert first_meeting_id in str(caught.value)


def test_the_precall_email_fires_inside_the_window_and_records_the_lead_time(ready, clock):
    engine, profile = ready
    engine.update_profile(
        profile["id"],
        {
            "precall_email_enabled": True,
            "precall_email": {
                "subject": "{{meeting_title}}",
                "body": "{{sender_name}} invites you.",
            },
        },
        source="test",
    )
    record = engine.open(booking_payload(), room_id="room-1", source="test")
    # The window is measured from the booking's own start, so the test reads that rather
    # than assuming one.
    start = datetime.fromisoformat(record["data"]["start_time"])

    clock.set(start - timedelta(minutes=30))
    assert engine.plan_precall_email("b-1", source="test")["reason"] == "before_window"

    clock.set(start - timedelta(minutes=15))
    sent = engine.plan_precall_email("b-1", sender_name="Dana", source="test")
    assert sent["data"]["status"] == "sent"
    assert sent["data"]["window_reason"] == "in_window_at_t_minus_15min"
    assert sent["data"]["window_minutes"] == [10, 20]
    # Only the external invitee. The internal one already knows.
    assert sent["data"]["to"] == ["analyst@contoso.example"]


def test_a_precall_email_is_not_sent_twice(ready, clock):
    engine, profile = ready
    engine.update_profile(
        profile["id"],
        {
            "precall_email_enabled": True,
            "precall_email": {"subject": "Call", "body": "Body"},
        },
        source="test",
    )
    record = engine.open(booking_payload(), room_id="room-1", source="test")
    start = datetime.fromisoformat(record["data"]["start_time"])
    clock.set(start - timedelta(minutes=15))
    assert engine.plan_precall_email("b-1", source="test")["data"]["status"] == "sent"
    again = engine.plan_precall_email("b-1", source="test")
    assert again["status"] == "skipped"
    assert again["reason"] == "already_sent"


def test_a_skip_is_a_result_and_not_a_failure(ready):
    """Nothing was wrong with the request. An operator reading a run of refusals would see
    a product that is broken rather than one that is early."""
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    skipped = engine.plan_precall_email("b-1", source="test")
    assert skipped["sent"] is False
    assert skipped["reason"] == "precall_email_disabled"
    assert engine.emails("b-1") == []


def test_the_summary_counts_the_states_a_reviewer_reads(ready):
    engine, _ = ready
    engine.open(booking_payload("b-1"), room_id="room-1", source="test")
    engine.open(booking_payload("b-2"), room_id="room-1", source="test")
    engine.decide("b-2", "granted", source="test")
    engine.start_recording("b-2", source="test")
    engine.finish_recording("b-2", source="test")

    counts = engine.summary("room-1")
    assert counts["bookings"] == 2
    assert counts["by_state"]["awaiting_consent"] == 1
    assert counts["by_state"]["recorded"] == 1
    assert counts["recordings_blocked"] == 1
    assert counts["recordings_complete"] == 1
    assert counts["inconsistent"] == []
    assert counts["resolution_key"] == "organizer_email"
    assert counts["precall_window_minutes"] == [10, 20]


def test_every_write_goes_through_the_audited_store(store: RecordStore, clock):
    """The product guarantee is that the audit row is written in the same transaction as
    the change. This asserts the rows exist for the writes this workflow makes."""
    engine = ConsentEngine(store, now=clock)
    profile = engine.create_profile(enforcing_profile(), room_id="room-1", source="test")
    engine.add_user(
        {"email": "dana@northwind.example", "name": "Dana", "profile_id": profile["id"]},
        room_id="room-1",
        source="test",
    )
    engine.open(booking_payload(), room_id="room-1", source="test")
    engine.decide("b-1", "granted", source="test")

    actions = {entry["action"] for entry in store.audit(limit=100)}
    assert actions == {"insert", "update"}
    for collection in (vocab.PROFILE_COLLECTION, vocab.DIRECTORY_COLLECTION):
        assert store.audit(collection=collection, limit=10)


def test_a_duplicate_directory_entry_is_refused(ready):
    engine, profile = ready
    with pytest.raises(ProfileInvalid) as caught:
        engine.add_user(
            {"email": "DANA@Northwind.Example", "name": "Dana Again", "profile_id": profile["id"]},
            source="test",
        )
    assert "already in the directory" in caught.value.errors["email"]


def test_only_one_profile_is_the_default(ready):
    engine, first = ready
    second = engine.create_profile(advisory_profile(name="Second"), source="test")
    engine.set_default_profile(first["id"], source="test")
    engine.set_default_profile(second["id"], source="test")
    defaults = [row["id"] for row in engine.profiles() if row["data"].get("is_default")]
    assert defaults == [second["id"]]


def test_an_unassigned_user_with_no_default_has_no_profile_and_is_refused(engine):
    """Gong's documented 404 is about a missing user; a user with no profile has the same
    problem for this workflow, because there is no consent rule to apply."""
    engine.create_profile(enforcing_profile(), source="test")
    engine.add_user({"email": "dana@northwind.example", "name": "Dana"}, source="test")
    with pytest.raises(OrganizerUnmapped) as caught:
        engine.resolve_profile("dana@northwind.example")
    assert "no consent profile assigned" in str(caught.value)


# --------------------------------------------------------------------------- #
# isolation
# --------------------------------------------------------------------------- #


def test_this_workflow_touches_only_its_own_collections(ready, store: RecordStore):
    """Two features authored independently must not share a Python module or a collection.
    This is the assertion that keeps that true for the five this workflow owns."""
    engine, _ = ready
    engine.open(booking_payload(), room_id="room-1", source="test")
    engine.decide("b-1", "granted", source="test")

    written = {row["collection"] for row in store.collections()}
    assert written == set(vocab.ALL_COLLECTIONS) & written
    assert written <= set(vocab.ALL_COLLECTIONS)


def test_the_domain_package_opens_no_socket_and_imports_no_framework():
    """The architectural guard, stated the way the brief states it: no ``dsr.api`` and no
    bare ``sqlite3``. A domain module that imports the app reintroduces the coupling the
    feature host removes, and one that imports sqlite3 bypasses the audit log the product
    guarantee is built on. Both are asserted rather than reviewed."""
    import pathlib

    import dsr.recording_consent as package

    root = pathlib.Path(package.__file__).parent
    forbidden = ("dsr.api", "sqlite3", "fastapi", "starlette", "pydantic", "requests", "httpx")

    for module in sorted(root.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            for name in forbidden:
                assert not stripped.startswith(f"import {name}"), f"{module.name}: {stripped}"
                assert not stripped.startswith(f"from {name}"), f"{module.name}: {stripped}"

    # And the one `dsr.` dependency it is allowed: the store.
    dsr_imports = {
        token
        for module in sorted(root.glob("*.py"))
        for line in module.read_text(encoding="utf-8").splitlines()
        for token in _dsr_modules(line)
    }
    outside = {
        token
        for token in dsr_imports
        if token != "dsr.store" and not token.startswith("dsr.recording_consent")
    }
    assert not outside, f"the domain package imports {sorted(outside)}"


def _dsr_modules(line: str) -> set[str]:
    """The ``dsr.*`` module a single import line names, if any."""
    stripped = line.strip()
    if not stripped.startswith(("import ", "from ")):
        return set()
    if stripped.startswith("from dsr."):
        return {stripped.split()[1]}
    if stripped.startswith("import dsr."):
        return {stripped.split()[1]}
    return set()


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


def test_the_seed_string_survives_a_windows_console(db, clock):
    """One RIGHTWARDS ARROW in a recovered feature's return string broke the entire seeder
    on a Windows console. This prints the string, which is the test the brief asks for."""
    from dsr.features import load_feature

    module = load_feature("wf060_auto_join_and_record_with_consent")
    message = module.seed(db, {"now": clock.at, "room_ids": [("room-1", "Room one")]})
    assert isinstance(message, str) and message
    # The assertion the incident actually needed.
    message.encode("cp1252")
    print(message)
    assert not any(ord(ch) > 127 for ch in message)


def test_the_seed_contains_a_state_that_is_not_a_success(db, clock):
    """The brief names the state: 'A booking whose consent was declined and whose recording
    was cancelled is such a state.'"""
    from dsr.features import load_feature

    module = load_feature("wf060_auto_join_and_record_with_consent")
    module.seed(db, {"now": clock.at, "room_ids": [("room-1", "Room one")]})
    rows = RecordStore(db).find(
        vocab.CONSENT_RECORDING_COLLECTION, {"recording_state": "cancelled"}
    )
    assert rows
    assert {row["data"]["consent_state"] for row in rows} <= {
        "declined",
        "joined_without_consent",
    }


def test_the_seed_produces_a_booking_recorded_after_a_decline(db, clock):
    """The derived open point, made visible in demo data. A demo holding only the enforcing
    path would leave the most contestable decision this workflow made unreviewable."""
    from dsr.features import load_feature

    module = load_feature("wf060_auto_join_and_record_with_consent")
    module.seed(db, {"now": clock.at, "room_ids": [("room-1", "Room one")]})
    rows = RecordStore(db).find(
        vocab.CONSENT_RECORDING_COLLECTION,
        {"consent_state": "declined", "recording_state": "complete"},
    )
    assert rows, "no booking recorded a decline under an unenforced profile"
    assert rows[0]["data"]["enforced"] is False


def test_the_seed_returns_nothing_without_a_room(db, clock):
    from dsr.features import load_feature

    module = load_feature("wf060_auto_join_and_record_with_consent")
    assert module.seed(db, {"now": clock.at, "room_ids": []}) == ""


def test_every_seeded_record_is_self_consistent(db, clock):
    """The axes and the state must agree on every seeded row, not only on the ones a test
    happened to walk."""
    from dsr.features import load_feature

    module = load_feature("wf060_auto_join_and_record_with_consent")
    module.seed(db, {"now": clock.at, "room_ids": [("room-1", "Room one")]})
    for row in RecordStore(db).list(vocab.CONSENT_RECORDING_COLLECTION, limit=50):
        machine = decisions.machine_from_data(row["data"])
        ok, why = decisions.is_consistent(machine)
        assert ok, f"{row['data']['booking_id']}: {why}"
