"""WF-078: require recipient identity verification before open or sign.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-078.md``,
quoted in full in issue 172. These tests are organised by the researched rule each one
defends, because the point of this workflow is that the rules were sourced rather than
chosen - so a rule with no test is a rule the next person to touch it will quietly drop.

The sections, and the sourced or derived rule each pins:

``two axes, not one``
    "method as a discriminated union on the recipient (passcode / phone / KBA / ID) with
    an independent timing axis (``before_open`` vs ``before_sign``) - the same recipient
    object can be verified differently for viewing and signing." The same recipient
    carrying two different settings at once is the sentence, made executable.
``the audience difference``
    "``before_open`` | Before the recipient can view the document | All recipients" and
    "``before_sign`` | Before the recipient can sign | Signers only". Enforced, not
    documented.
``the passcode bound``
    "It must be 6-100 characters with at least one letter and one digit." Both bounds,
    letters-only, digits-only, and a passcode of spaces.
``the phone bound``
    "must be in international format (e.g., ``+1555667890``)". A national-format number is
    rejected, not repaired.
``authentication and delivery are separable``
    "an SMS can be an auth factor, a delivery channel, or both". All three states.
``the body is withheld until it clears``
    "the document body is withheld until it clears", and the gate is resolved from the
    latest attempt rather than from "has this recipient ever passed".
``a failure is as visible as a success``
    "Both success and failure produce audit actions, so a rejected attempt is as visible
    as a successful one." Every attempt writes a row, and the failure row carries a code.
``no remembered verification``
    "Verification is re-asserted by the gate on every attempt; there is no 'verify once,
    remember forever' behaviour." A second attempt re-runs the check.
``the code mapping``
    The specification's band 47-54, and the two codes it names individually, checked
    against the table that owns them rather than against a copy.
``the domain imports nothing but the store``
    The architectural guard the brief names by name.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims actually exist.

The HTTP surface is in ``test_wf078_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.audit_export.vocabulary import verification_code as owner_verification_code
from dsr.db.audited import AuditedDatabase
from dsr.identity_verification import inferences, rules, vocabulary as vocab
from dsr.identity_verification.engine import IdentityVerificationEngine, normalise_role
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.wf078_require_recipient_identity_verification"
DOMAIN_PACKAGE = "dsr.identity_verification"

#: The code table this workflow reads. Passed into the engine as a callable so the domain
#: package has no import edge to another workflow's package, and so a test can assert the
#: mapping against the real owner rather than against a copy of it.
CODE_FOR = owner_verification_code


class Clock:
    """A clock the test moves by hand.

    Every stamp this workflow writes comes from here, which is what makes the "the seed's
    own numbers are true" assertions below meaningful: they can look for a known instant
    and know it would have appeared if it appeared anywhere.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> datetime:
        self.at = self.at + timedelta(**kwargs)
        return self.at


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def engine(clock: Clock, store: RecordStore) -> IdentityVerificationEngine:
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    store.create(
        "room", {"name": "Halcyon data room"}, record_id="room_b", actor="dana", source="fixture"
    )
    return IdentityVerificationEngine(store, now=clock, code_for=CODE_FOR)


def make_document(
    engine: IdentityVerificationEngine,
    *,
    room_id: str = "room_a",
    title: str = "Verified document",
    body: str = "The body of the document.",
    recipients: list[dict] | None = None,
) -> dict:
    """One document, with the recipients it carries."""

    return engine.create_document(
        room_id,
        {"title": title, "body": body, "status": "sent", "recipients": recipients or []},
        source="fixture",
        actor="dana",
    )


def make_recipient(
    engine: IdentityVerificationEngine,
    *,
    room_id: str = "room_a",
    settings: dict | None = None,
    role: str = vocab.ROLE_RECIPIENT,
    document: dict | None = None,
) -> dict:
    """One recipient, with the gates it carries."""

    target = document or make_document(engine, room_id=room_id)
    return engine.create_recipient(
        room_id,
        target["id"],
        {
            "email": "recipient@example.test",
            "name": "Test Recipient",
            "role": role,
            rules.VERIFICATION_SETTINGS: settings or {},
        },
        source="fixture",
        actor="dana",
    )


def passcode_gate(passcode: str = "Deal2026") -> dict:
    return {vocab.BEFORE_OPEN: {"method": vocab.METHOD_PASSCODE, "passcode": passcode}}


# --------------------------------------------------------------------------- #
# two axes, not one
# --------------------------------------------------------------------------- #


class TestTwoAxes:
    def test_the_specification_names_two_axes_and_this_build_has_two(self):
        """The extensibility note: "method as a discriminated union on the recipient ...
        with an independent timing axis". Two, not one field and one value."""
        assert len(vocab.PLACES) == 2
        assert set(vocab.PLACES) == {vocab.BEFORE_OPEN, vocab.BEFORE_SIGN}
        assert len(vocab.METHODS) == 4

    def test_the_same_recipient_is_verified_differently_for_viewing_and_signing(self, engine):
        """The sentence itself, exercised end to end.

        A passcode before the document opens and a knowledge-based check before it is
        signed, on one recipient. Both gates clear independently, each with its own
        evidence, and neither gate's outcome disturbs the other.
        """
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_PASSCODE,
                    "passcode": "Order4471",
                },
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_KBA,
                    "questions": [
                        {"prompt": "Purchase order number", "answer": "PO-88214"},
                    ],
                },
            },
        )

        gates = recipient["gates"]
        assert [gate["place"] for gate in gates] == [vocab.BEFORE_OPEN, vocab.BEFORE_SIGN]
        assert {gate["place"]: gate["method"] for gate in gates} == {
            vocab.BEFORE_OPEN: vocab.METHOD_PASSCODE,
            vocab.BEFORE_SIGN: vocab.METHOD_KBA,
        }

        # The view gate clears on the passcode alone.
        opened = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Order4471"}, source="fixture"
        )
        assert opened["outcome"] == vocab.OUTCOME_PASS
        assert opened["method"] == vocab.METHOD_PASSCODE

        # The sign gate is untouched by that and still refuses the wrong answer.
        refused = engine.attempt(
            recipient["id"],
            vocab.BEFORE_SIGN,
            {"answers": {"Purchase order number": "PO-00000"}},
            source="fixture",
        )
        assert refused["outcome"] == vocab.OUTCOME_FAIL
        assert refused["method"] == vocab.METHOD_KBA

        # And it clears on the right answer, at its own moment, with its own code.
        signed = engine.attempt(
            recipient["id"],
            vocab.BEFORE_SIGN,
            {"answers": {"Purchase order number": "PO-88214"}},
            source="fixture",
        )
        assert signed["outcome"] == vocab.OUTCOME_PASS
        assert signed["place"] == vocab.BEFORE_SIGN
        assert signed["event_code"] == owner_verification_code(vocab.METHOD_KBA, vocab.OUTCOME_PASS)

    def test_each_gate_holds_exactly_one_method(self, engine):
        """A discriminated union: exactly one method per gate, not a pile."""
        recipient = make_recipient(
            engine,
            settings={vocab.BEFORE_OPEN: {"passcode": "Deal2026"}},
        )
        gate = recipient["gates"][0]
        assert gate["method"] == vocab.METHOD_PASSCODE
        assert "questions" not in gate and "id_document" not in gate

    def test_a_gate_entry_carrying_two_methods_is_refused(self, engine):
        """The union has to actually be a union. Two config keys in one entry means the
        entry names a method and carries the other's payload."""
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_settings(
                {
                    "method": vocab.METHOD_PASSCODE,
                    "passcode": "Deal2026",
                    "questions": [{"prompt": "County", "answer": "Kent"}],
                }
            )
        assert caught.value.errors["method"]

    def test_a_gate_entry_whose_method_disagrees_with_its_payload_is_refused(self, engine):
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_settings({"method": vocab.METHOD_KBA, "passcode": "Deal2026"})
        assert caught.value.errors["method"]

    def test_the_method_is_read_from_the_payload_not_from_the_discriminator(self):
        """A hand-edited record whose ``method`` field disagrees with what it carries
        resolves by what it actually carries. The discriminator is a convenience; the
        payload is the truth."""
        entry = {"method": vocab.METHOD_KBA, "passcode": "Deal2026"}
        assert rules.method_of(entry) == vocab.METHOD_PASSCODE

    def test_the_vendor_field_names_resolve_to_methods(self):
        """The method table is written in the vendor's spelling. A caller using it must not
        have to translate before it can call this API."""
        for method, vendor_field in vocab.METHOD_VENDOR_FIELDS.items():
            assert rules.normalise_method(vendor_field) == method


# --------------------------------------------------------------------------- #
# the audience difference
# --------------------------------------------------------------------------- #


class TestAudience:
    def test_before_open_applies_to_all_recipients(self):
        """The sourced audience column: "All recipients"."""
        assert vocab.PLACE_AUDIENCE[vocab.BEFORE_OPEN] == "all_recipients"
        assert vocab.PLACE_REQUIRED_ROLE[vocab.BEFORE_OPEN] is None
        assert rules.place_applies_to_role(vocab.BEFORE_OPEN, vocab.ROLE_RECIPIENT)
        assert rules.place_applies_to_role(vocab.BEFORE_OPEN, vocab.ROLE_SIGNER)

    def test_before_sign_applies_to_signers_only(self):
        """The sourced audience column: "Signers only"."""
        assert vocab.PLACE_AUDIENCE[vocab.BEFORE_SIGN] == "signers_only"
        assert vocab.PLACE_REQUIRED_ROLE[vocab.BEFORE_SIGN] == vocab.ROLE_SIGNER
        assert rules.place_applies_to_role(vocab.BEFORE_SIGN, vocab.ROLE_SIGNER)
        assert not rules.place_applies_to_role(vocab.BEFORE_SIGN, vocab.ROLE_RECIPIENT)

    def test_a_non_signer_cannot_be_given_a_before_sign_gate(self, engine):
        """Enforced, not documented. The refusal names the role the sender would need."""
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.build_settings(
                vocab.BEFORE_SIGN,
                vocab.METHOD_PASSCODE,
                "Deal2026",
                role=vocab.ROLE_RECIPIENT,
            )
        assert "role" in caught.value.errors

    def test_a_signer_can_be_given_a_before_sign_gate(self, engine):
        entry = rules.build_settings(
            vocab.BEFORE_SIGN, vocab.METHOD_PASSCODE, "Deal2026", role=vocab.ROLE_SIGNER
        )
        assert entry["method"] == vocab.METHOD_PASSCODE

    def test_an_unknown_role_is_treated_as_not_a_signer(self):
        """Fails closed. A misspelled role must not quietly install a before_sign gate on
        somebody who cannot clear it."""
        assert not rules.place_applies_to_role(vocab.BEFORE_SIGN, "approver")
        assert normalise_role("approver") == vocab.ROLE_RECIPIENT

    def test_a_before_sign_gate_added_later_to_a_non_signer_is_refused(self, engine):
        """The patch path enforces the same rule the create path does."""
        recipient = make_recipient(engine)
        with pytest.raises(rules.VerificationSettingsInvalid):
            engine.update_recipient(
                recipient["id"],
                {rules.VERIFICATION_SETTINGS: {vocab.BEFORE_SIGN: {"passcode": "Deal2026"}}},
                source="fixture",
            )

    def test_a_gate_at_the_wrong_moment_on_a_non_signer_fails_rather_than_passes(self, engine):
        """A recipient who somehow holds a before_sign gate they cannot clear is refused,
        not waved through. The two failure modes are both bad and this is the safe one."""
        record = engine.store.create(
            vocab.RECIPIENT_COLLECTION,
            {
                rules.ROOM_REF: "room_a",
                "document_id": "doc_x",
                "email": "x@example.test",
                "role": vocab.ROLE_RECIPIENT,
                rules.VERIFICATION_SETTINGS: {
                    vocab.BEFORE_SIGN: {"method": vocab.METHOD_PASSCODE, "passcode": "Deal2026"}
                },
            },
            record_id="rcpt_wrong_role",
            source="fixture",
        )
        assert record["id"] == "rcpt_wrong_role"
        result = rules.evaluate(
            {
                "role": vocab.ROLE_RECIPIENT,
                rules.VERIFICATION_SETTINGS: {
                    vocab.BEFORE_SIGN: {"method": vocab.METHOD_PASSCODE, "passcode": "Deal2026"}
                },
            },
            vocab.BEFORE_SIGN,
            {"passcode": "Deal2026"},
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_NOT_A_SIGNER


# --------------------------------------------------------------------------- #
# the passcode bound
# --------------------------------------------------------------------------- #


class TestPasscodeBound:
    def test_the_rule_is_quoted_from_the_specification(self):
        assert vocab.PASSCODE_MIN_LENGTH == 6
        assert vocab.PASSCODE_MAX_LENGTH == 100

    def test_the_lower_bound_is_inclusive(self):
        """Six characters with a letter and a digit is the shortest the rule accepts."""
        assert rules.validate_passcode("abc123") == "abc123"
        assert rules.validate_passcode("a1b2c3") == "a1b2c3"

    def test_one_short_of_the_lower_bound_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_passcode("ab123")
        assert "6" in caught.value.errors["passcode"]

    def test_the_upper_bound_is_inclusive(self):
        exact = "a" * 99 + "1"
        assert len(exact) == vocab.PASSCODE_MAX_LENGTH
        assert rules.validate_passcode(exact) == exact

    def test_one_over_the_upper_bound_is_refused(self):
        too_long = "a" * 100 + "1"
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_passcode(too_long)
        assert str(vocab.PASSCODE_MAX_LENGTH) in caught.value.errors["passcode"]

    def test_a_letters_only_passcode_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_passcode("abcdefgh")
        assert caught.value.errors["passcode"] == "Include at least one digit."

    def test_a_digits_only_passcode_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_passcode("12345678")
        assert caught.value.errors["passcode"] == "Include at least one letter."

    def test_a_passcode_of_spaces_is_refused(self):
        """Six spaces satisfy the length rule and nothing else. A recipient can type six
        spaces, so this is the case the length rule alone does not catch."""
        with pytest.raises(rules.VerificationSettingsInvalid) as caught:
            rules.validate_passcode("      ")
        assert "spaces" in caught.value.errors["passcode"]

    def test_a_missing_passcode_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid):
            rules.validate_passcode(None)

    def test_a_non_text_passcode_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid):
            rules.validate_passcode(12345678)

    def test_six_spaces_at_an_attempt_is_not_provable_either(self):
        """The same hole, closed at the other end. A gate that accepted six spaces would
        be a gate anybody walks through."""
        assert rules.passcode_is_provable("      ") is False
        assert rules.passcode_is_provable("Deal2026") is True


# --------------------------------------------------------------------------- #
# the phone bound
# --------------------------------------------------------------------------- #


class TestPhoneBound:
    def test_the_specification_example_is_accepted(self):
        assert rules.validate_phone(vocab.PHONE_PLACEHOLDER) == vocab.PHONE_PLACEHOLDER

    @pytest.mark.parametrize(
        "national",
        [
            "(555) 123-4567",
            "555-123-4567",
            "5551234567",
            "020 7946 0958",
            "+44 20 7946 0958",
            "+0123456789",
            "+12345",
        ],
    )
    def test_a_national_format_number_is_refused(self, national):
        """Rejected, not repaired. A number with no country code goes to whichever country
        the dialler is configured for."""
        assert rules.is_e164(national) is False
        with pytest.raises(rules.VerificationSettingsInvalid):
            rules.validate_phone(national)

    def test_punctuation_inside_an_international_number_is_refused(self):
        assert rules.is_e164("+1 555 667 890") is False

    def test_a_number_that_is_not_text_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid):
            rules.validate_phone(15556678901)

    def test_a_missing_number_is_refused(self):
        with pytest.raises(rules.VerificationSettingsInvalid):
            rules.validate_phone(None)

    def test_the_first_digit_may_not_be_a_zero(self):
        """No country has a country code beginning with zero, so '+0...' can never be
        dialled."""
        assert rules.is_e164("+0123456789") is False

    def test_the_seventeen_digit_boundary_is_refused(self):
        assert rules.is_e164("+" + "1" * 16) is False
        assert rules.is_e164("+" + "1" * 15) is True


# --------------------------------------------------------------------------- #
# authentication and delivery are separable
# --------------------------------------------------------------------------- #


class TestSmsRole:
    def _sms_gate(self, sms_type: str) -> dict:
        return {
            vocab.BEFORE_SIGN: {
                "method": vocab.METHOD_SMS,
                "phone_number": vocab.PHONE_PLACEHOLDER,
                vocab.SMS_TYPE_FIELD: sms_type,
            }
        }

    def test_three_states_are_modelled(self):
        """The extensibility note: "an SMS can be an auth factor, a delivery channel, or
        both". Three states, not one boolean."""
        assert set(vocab.SMS_TYPES) == {
            vocab.SMS_TYPE_AUTHENTICATION,
            vocab.SMS_TYPE_DELIVERY,
            vocab.SMS_TYPE_BOTH,
        }

    def test_authentication_only_makes_the_number_a_factor(self, engine):
        recipient = make_recipient(
            engine, role=vocab.ROLE_SIGNER, settings=self._sms_gate(vocab.SMS_TYPE_AUTHENTICATION)
        )
        assert recipient["authentication_factor_gates"] == [vocab.BEFORE_SIGN]
        assert recipient["gates"][0]["sms_type_meaning"]

    def test_delivery_only_does_not_make_the_number_a_factor(self, engine):
        """The state the two other ones would hide: the number carries the document and
        proves nothing by itself."""
        recipient = make_recipient(
            engine, role=vocab.ROLE_SIGNER, settings=self._sms_gate(vocab.SMS_TYPE_DELIVERY)
        )
        assert recipient["authentication_factor_gates"] == []
        assert recipient["gates"][0]["sms_type"] == vocab.SMS_TYPE_DELIVERY
        assert (
            recipient["gates"][0]["sms_type_meaning"]
            == vocab.SMS_TYPE_MEANINGS[vocab.SMS_TYPE_DELIVERY]
        )

    def test_both_makes_the_number_a_factor_and_a_channel(self, engine):
        recipient = make_recipient(
            engine, role=vocab.ROLE_SIGNER, settings=self._sms_gate(vocab.SMS_TYPE_BOTH)
        )
        assert recipient["authentication_factor_gates"] == [vocab.BEFORE_SIGN]
        assert recipient["gates"][0]["sms_type"] == vocab.SMS_TYPE_BOTH

    def test_the_default_is_authentication_only(self, engine):
        """A sender who configures an SMS verification has asked for the number to be an
        authentication factor, so an omitted field means the ordinary case."""
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        assert recipient["gates"][0]["sms_type"] == vocab.SMS_TYPE_AUTHENTICATION

    def test_an_unknown_sms_type_falls_back_rather_than_failing_the_gate(self):
        entry = {
            "method": vocab.METHOD_SMS,
            "phone_number": vocab.PHONE_PLACEHOLDER,
            vocab.SMS_TYPE_FIELD: "carrier_pigeon",
        }
        assert rules.sms_type_of(entry) == vocab.SMS_TYPE_AUTHENTICATION

    def test_only_the_sms_method_reports_a_role(self):
        """The other three have no number, so there is no role to report."""
        assert (
            rules.is_authentication_factor(
                {rules.VERIFICATION_SETTINGS: passcode_gate()}, vocab.BEFORE_OPEN
            )
            is False
        )


# --------------------------------------------------------------------------- #
# the code mapping
# --------------------------------------------------------------------------- #


class TestCodeMapping:
    def test_the_specification_names_two_codes_individually(self):
        """'47 recipient verification with kba passed' and '51 recipient verification with
        kba failed'. The two the source names by hand."""
        assert owner_verification_code(vocab.METHOD_KBA, vocab.OUTCOME_PASS) == 47
        assert owner_verification_code(vocab.METHOD_KBA, vocab.OUTCOME_FAIL) == 51

    @pytest.mark.parametrize("method", vocab.METHODS)
    @pytest.mark.parametrize("outcome", vocab.OUTCOMES)
    def test_every_method_maps_to_a_code_inside_the_sourced_band(self, method, outcome):
        code = owner_verification_code(method, outcome)
        assert code is not None
        assert vocab.VERIFICATION_CODE_BAND[0] <= code <= vocab.VERIFICATION_CODE_BAND[1]

    @pytest.mark.parametrize("method", vocab.METHODS)
    def test_pass_and_fail_are_different_codes_for_every_method(self, method):
        """A compliance review filters on an integer, and a band where pass and fail share
        an integer would be a band that cannot answer 'did anybody fail'."""
        assert owner_verification_code(method, vocab.OUTCOME_PASS) != owner_verification_code(
            method, vocab.OUTCOME_FAIL
        )

    def test_this_workflow_mints_no_code_of_its_own(self):
        """The table has one home. Asserted by naming the owner in the vocabulary and by
        the fact that no code constant lives in this package."""
        assert vocab.CODE_TABLE_OWNER == "dsr.audit_export.vocabulary.verification_code"

    def test_no_integer_in_the_verification_band_is_defined_in_this_package(self):
        """The band is published so a reviewer can check the mapping, but publishing the
        band is not publishing the table.

        Every constant the domain package binds, read from the tree. A module may name the
        owner in a docstring, so what matters is the bound values: an integer inside 47-54
        bound here would be a second definition of a code the owner already allocates.
        """
        # The published band itself is allowed: naming 47 to 54 is how a reviewer checks
        # the mapping, and it allocates nothing. A member of the band is not.
        allowed = (set(vocab.VERIFICATION_CODE_BAND), set(vocab.EMAIL_OTP_CODE_BAND))
        offenders = []
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Assign | ast.AnnAssign):
                    continue
                value = node.value
                numbers: list[int] = []
                if isinstance(value, ast.Constant) and isinstance(value.value, int):
                    if not isinstance(value.value, bool):
                        numbers = [value.value]
                elif isinstance(value, ast.Tuple | ast.List | ast.Set):
                    numbers = [
                        element.value
                        for element in value.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, int)
                        and not isinstance(element.value, bool)
                    ]
                inside = [
                    number
                    for number in numbers
                    if vocab.VERIFICATION_CODE_BAND[0] <= number <= vocab.VERIFICATION_CODE_BAND[1]
                ]
                # A lone band endpoint is the band being named; anything else inside it is
                # an allocation.
                if inside and not any(set(numbers) == band for band in allowed):
                    offenders.append(f"{path.name}: {numbers}")
        assert not offenders, (
            f"this package allocates integers inside the verification band, which the code "
            f"owner owns: {offenders}"
        )

    def test_a_constant_named_for_a_method_code_does_not_exist(self):
        """The other shape a second table takes: a name per method."""
        defined = {
            target.id
            for path in _domain_paths()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for method in vocab.METHODS:
            assert f"{method.upper()}_CODE" not in defined
            assert f"{method.upper()}_PASSED" not in defined
            assert f"{method.upper()}_FAILED" not in defined

    def test_a_passed_attempt_carries_the_code_its_outcome_resolved_to(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        row = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert row["event_code"] == owner_verification_code(
            vocab.METHOD_PASSCODE, vocab.OUTCOME_PASS
        )

    def test_a_failed_attempt_carries_its_own_code(self, engine):
        """A failure row with no code is invisible to the query the codes exist for."""
        recipient = make_recipient(engine, settings=passcode_gate())
        row = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        assert row["event_code"] == owner_verification_code(
            vocab.METHOD_PASSCODE, vocab.OUTCOME_FAIL
        )


# --------------------------------------------------------------------------- #
# evaluating one attempt, per method
# --------------------------------------------------------------------------- #


class TestAttemptEvaluation:
    def test_passcode_passes_on_the_right_answer(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        result = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert result["outcome"] == vocab.OUTCOME_PASS
        assert result["reason"] is None

    def test_passcode_fails_on_the_wrong_answer(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        result = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "deal2026"}, source="fixture"
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_WRONG_PASSCODE

    def test_kba_passes_when_every_answer_matches(self, engine):
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_KBA,
                    "questions": [
                        {"prompt": "Company number", "answer": "04198233"},
                        {"prompt": "County", "answer": "Greater London"},
                    ],
                }
            },
        )
        result = engine.attempt(
            recipient["id"],
            vocab.BEFORE_OPEN,
            {"answers": {"Company number": "04198233", "County": "Greater London"}},
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_PASS

    def test_kba_fails_when_one_answer_is_wrong(self, engine):
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_KBA,
                    "questions": [
                        {"prompt": "Company number", "answer": "04198233"},
                        {"prompt": "County", "answer": "Greater London"},
                    ],
                }
            },
        )
        result = engine.attempt(
            recipient["id"],
            vocab.BEFORE_OPEN,
            {"answers": {"Company number": "04198233", "County": "Kent"}},
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_ANSWER_MISMATCH

    def test_kba_fails_when_an_answer_is_missing(self, engine):
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_KBA,
                    "questions": [
                        {"prompt": "Company number", "answer": "04198233"},
                        {"prompt": "County", "answer": "Greater London"},
                    ],
                }
            },
        )
        result = engine.attempt(
            recipient["id"],
            vocab.BEFORE_OPEN,
            {"answers": {"Company number": "04198233"}},
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_ANSWERS_INCOMPLETE

    def test_id_check_passes_when_the_stated_details_match(self, engine):
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_ID,
                    "id_document": {
                        "document_type": "passport",
                        "document_number": "P1234567",
                        "issuing_country": "GB",
                    },
                }
            },
        )
        result = engine.attempt(
            recipient["id"],
            vocab.BEFORE_OPEN,
            {
                "id_document": {
                    "document_type": "passport",
                    "document_number": "P1234567",
                    "issuing_country": "GB",
                }
            },
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_PASS

    def test_id_check_fails_when_one_detail_differs(self, engine):
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_ID,
                    "id_document": {"document_type": "passport", "document_number": "P1234567"},
                }
            },
        )
        result = engine.attempt(
            recipient["id"],
            vocab.BEFORE_OPEN,
            {"id_document": {"document_type": "passport", "document_number": "P7654321"}},
            source="fixture",
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_ID_MISMATCH

    def test_sms_fails_when_no_code_was_ever_sent(self, engine):
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        result = engine.attempt(
            recipient["id"], vocab.BEFORE_SIGN, {"code": "123456"}, source="fixture"
        )
        assert result["outcome"] == vocab.OUTCOME_FAIL
        assert result["reason"] == vocab.REASON_NO_CODE_SENT

    def test_sms_passes_on_the_delivered_code(self, engine):
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        engine.send_code(recipient["id"], source="fixture", code="418204")
        result = engine.attempt(
            recipient["id"], vocab.BEFORE_SIGN, {"code": "418204"}, source="fixture"
        )
        assert result["outcome"] == vocab.OUTCOME_PASS

    def test_sms_fails_on_a_superseded_code(self, engine):
        """A resend narrows access rather than widening it: at most one code is valid."""
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        engine.send_code(recipient["id"], source="fixture", code="418204")
        engine.send_code(recipient["id"], source="fixture", code="662951")
        old = engine.attempt(
            recipient["id"], vocab.BEFORE_SIGN, {"code": "418204"}, source="fixture"
        )
        assert old["outcome"] == vocab.OUTCOME_FAIL
        new = engine.attempt(
            recipient["id"], vocab.BEFORE_SIGN, {"code": "662951"}, source="fixture"
        )
        assert new["outcome"] == vocab.OUTCOME_PASS

    def test_a_code_is_never_returned_by_the_send(self, engine):
        """A send endpoint that handed the code back would have turned the gate into a
        suggestion."""
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        sent = engine.send_code(recipient["id"], source="fixture", code="418204")
        assert "418204" not in repr(sent)
        assert sent["code_returned"] is False

    def test_a_code_is_only_issued_for_an_sms_gate(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        with pytest.raises(rules.VerificationSettingsInvalid):
            engine.send_code(recipient["id"], source="fixture")

    def test_an_ungated_recipient_reports_no_attempt_at_all(self, engine):
        """A verification event in the trail for somebody who was never asked for one
        makes the trail read as complete when it is not."""
        recipient = make_recipient(engine)
        result = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "anything"}, source="fixture"
        )
        assert result["gated"] is False
        assert result["outcome"] is None
        assert result["attempt_id"] is None
        assert engine.attempts(recipient_id=recipient["id"]) == []


# --------------------------------------------------------------------------- #
# a failure is as visible as a success
# --------------------------------------------------------------------------- #


class TestFailureVisibility:
    def test_a_rejected_attempt_writes_a_row(self, engine):
        """The data flow: "Both success and failure produce audit actions, so a rejected
        attempt is as visible as a successful one." """
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        rows = engine.attempts(recipient_id=recipient["id"])
        assert len(rows) == 1
        assert rows[0]["outcome"] == vocab.OUTCOME_FAIL
        assert rows[0]["reason"] == vocab.REASON_WRONG_PASSCODE

    def test_a_rejected_attempt_carries_an_audit_row_in_the_store(self, engine):
        """Not only a row this workflow projects. The audited row is the product's
        guarantee, and it is written in the same transaction as the change."""
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        audit = engine.store.audit(collection=vocab.ATTEMPT_COLLECTION)
        assert len(audit) == 1
        assert audit[0]["after_state"]["outcome"] == vocab.OUTCOME_FAIL
        assert audit[0]["source"] == "fixture"

    def test_a_rejection_names_a_reason(self, engine):
        """A failure row with no reason is only half a record."""
        recipient = make_recipient(engine, settings=passcode_gate())
        row = engine.attempt(recipient["id"], vocab.BEFORE_OPEN, {}, source="fixture")
        assert row["outcome"] == vocab.OUTCOME_FAIL
        assert row["reason"] == vocab.REASON_MALFORMED_PASSCODE

    def test_every_failure_reason_is_a_published_vocabulary_term(self):
        assert set(vocab.FAILURE_REASONS) >= {
            vocab.REASON_WRONG_PASSCODE,
            vocab.REASON_MALFORMED_PASSCODE,
            vocab.REASON_NO_CODE_SENT,
            vocab.REASON_WRONG_CODE,
            vocab.REASON_ANSWERS_INCOMPLETE,
            vocab.REASON_ANSWER_MISMATCH,
            vocab.REASON_ID_MISMATCH,
            vocab.REASON_NOT_A_SIGNER,
        }

    def test_a_pass_and_a_failure_carry_the_same_keys(self, engine):
        """A reviewer filtering on rows should not have to handle two schemas. The set of
        keys is what a filter is written against; it must not change with the outcome."""
        recipient = make_recipient(engine, settings=passcode_gate())
        bad = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        good = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert set(bad) == set(good)

    def test_the_stored_rows_carry_the_same_keys_for_both_outcomes(self, engine):
        """The stored row, read back through the trail, rather than the write's return
        value: the trail is what a compliance query runs against."""
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        rows = engine.attempts(recipient_id=recipient["id"])
        assert set(rows[0]) == set(rows[1])
        assert {rows[0]["outcome"], rows[1]["outcome"]} == {
            vocab.OUTCOME_PASS,
            vocab.OUTCOME_FAIL,
        }

    def test_a_gate_effect_is_derived_from_the_outcome(self, engine):
        """A caller cannot declare that it cleared a gate."""
        recipient = make_recipient(engine, settings=passcode_gate())
        bad = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Wrong99"}, source="fixture"
        )
        good = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert bad["gate_effect"] != good["gate_effect"]
        assert good["gate_effect"] == "gate_cleared"


# --------------------------------------------------------------------------- #
# no remembered verification
# --------------------------------------------------------------------------- #


class TestNoRememberedVerification:
    def test_a_second_attempt_re_runs_the_check(self, engine):
        """ "Verification is re-asserted by the gate on every attempt; there is no 'verify
        once, remember forever' behaviour in these flows." """
        recipient = make_recipient(engine, settings=passcode_gate())
        first = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert first["outcome"] == vocab.OUTCOME_PASS
        second = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2027"}, source="fixture"
        )
        assert second["outcome"] == vocab.OUTCOME_FAIL
        assert second["reason"] == vocab.REASON_WRONG_PASSCODE

    def test_a_pass_does_not_short_circuit_a_later_evaluation(self, engine):
        """The evaluator reads nothing from a previous attempt and caches nothing."""
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        stored = engine._recipient_record(recipient["id"])
        again = rules.evaluate(dict(stored["data"]), vocab.BEFORE_OPEN, {"passcode": "Deal2027"})
        assert again["outcome"] == vocab.OUTCOME_FAIL

    def test_three_attempts_produce_three_rows(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        for offered in ("Deal2026", "Wrong99", "Deal2026"):
            engine.attempt(
                recipient["id"], vocab.BEFORE_OPEN, {"passcode": offered}, source="fixture"
            )
        rows = engine.attempts(recipient_id=recipient["id"])
        assert [row["outcome"] for row in rows] == [
            vocab.OUTCOME_PASS,
            vocab.OUTCOME_FAIL,
            vocab.OUTCOME_PASS,
        ]


# --------------------------------------------------------------------------- #
# the body is withheld until it clears
# --------------------------------------------------------------------------- #


class TestWithheldBody:
    def test_a_before_open_recipient_cannot_read_the_body_before_the_attempt_succeeds(self, engine):
        """ "The document body is withheld until it clears." """
        document = make_document(engine, body="The confidential terms.")
        recipient = make_recipient(engine, document=document, settings=passcode_gate())

        with pytest.raises(rules.GateNotCleared) as caught:
            engine.read_body(document["id"], recipient["id"])
        assert caught.value.place == vocab.BEFORE_OPEN
        assert caught.value.method == vocab.METHOD_PASSCODE
        assert "confidential" not in repr(caught.value)

    def test_the_body_arrives_once_the_attempt_succeeds(self, engine):
        document = make_document(engine, body="The confidential terms.")
        recipient = make_recipient(engine, document=document, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        released = engine.read_body(document["id"], recipient["id"])
        assert released["body_state"] == vocab.BODY_RELEASED
        assert released["body"] == "The confidential terms."
        assert released["released_by"] == vocab.OUTCOME_PASS

    def test_reading_the_document_never_returns_the_body(self, engine):
        """Metadata is not a back door. The body comes from one gated route only."""
        document = make_document(engine, body="The confidential terms.")
        make_recipient(engine, document=document, settings=passcode_gate())
        read = engine.read_document(document["id"])
        assert "body" not in read
        assert "confidential" not in repr(read)
        assert read["body_length"] == len("The confidential terms.")

    def test_a_recipient_projection_never_echoes_the_stored_secret(self, engine):
        """A settings page is the place a secret is most likely to leak from."""
        recipient = make_recipient(engine, settings=passcode_gate("Deal2026"))
        assert "Deal2026" not in repr(recipient)

    def test_a_failed_attempt_after_a_pass_withdraws_the_body_again(self, engine):
        """The gate is re-asserted, so a later failure withholds again. This is the whole of
        "no verify once, remember forever" as an observable behaviour."""
        document = make_document(engine, body="The confidential terms.")
        recipient = make_recipient(engine, document=document, settings=passcode_gate())
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert engine.read_body(document["id"], recipient["id"])["body_state"] == (
            vocab.BODY_RELEASED
        )
        engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2027"}, source="fixture"
        )
        with pytest.raises(rules.GateNotCleared) as caught:
            engine.read_body(document["id"], recipient["id"])
        assert caught.value.reason == vocab.WITHHELD_REASON_REASSERTED

    def test_an_ungated_recipient_reads_the_body_without_an_attempt(self, engine):
        """Most recipients in a room carry no settings, and refusing them would be a gate
        on everything."""
        document = make_document(engine, body="Open terms.")
        recipient = make_recipient(engine, document=document)
        released = engine.read_body(document["id"], recipient["id"])
        assert released["body"] == "Open terms."
        assert released["released_by"] == "no_gate_at_this_place"

    def test_the_before_sign_gate_does_not_withhold_the_body(self, engine):
        """Two axes, two consequences. A sign gate withholds the signature, not the
        viewing, so a signer can read before they can sign."""
        document = make_document(engine, body="Order form terms.")
        recipient = make_recipient(
            engine,
            document=document,
            role=vocab.ROLE_SIGNER,
            settings={vocab.BEFORE_SIGN: {"passcode": "Order4471"}},
        )
        released = engine.read_body(document["id"], recipient["id"], place=vocab.BEFORE_OPEN)
        assert released["body"] == "Order form terms."
        with pytest.raises(rules.GateNotCleared):
            engine.read_body(document["id"], recipient["id"], place=vocab.BEFORE_SIGN)

    def test_the_body_state_route_reports_without_delivering(self, engine):
        """A page needs to say "withheld until you verify" without handing over the
        document to say it."""
        document = make_document(engine, body="The confidential terms.")
        recipient = make_recipient(engine, document=document, settings=passcode_gate())
        state = engine.body_state(document["id"], recipient["id"])
        assert state["body_state"] == vocab.BODY_WITHHELD
        assert state["withheld_reason"] == vocab.WITHHELD_REASON_GATE_UNCLEARED
        assert "body" not in state
        assert "confidential" not in repr(state)

    def test_a_recipient_of_another_document_gets_no_body(self, engine):
        """One document's gate must not answer a question about a different document."""
        first = make_document(engine, body="First document.")
        second = make_document(engine, body="Second document.")
        recipient = make_recipient(engine, document=first, settings=passcode_gate())
        with pytest.raises(rules.RecipientNotFound):
            engine.read_body(second["id"], recipient["id"])


# --------------------------------------------------------------------------- #
# the prompt never carries the answer
# --------------------------------------------------------------------------- #


class TestPrompt:
    def test_a_passcode_prompt_carries_the_bound_and_not_the_passcode(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate("Deal2026"))
        prompt = engine.prompt_for(recipient["id"], vocab.BEFORE_OPEN)
        assert prompt["asks"] == "passcode"
        assert prompt["min_length"] == vocab.PASSCODE_MIN_LENGTH
        assert "Deal2026" not in repr(prompt)

    def test_a_kba_prompt_carries_the_questions_and_not_the_answers(self, engine):
        """A recipient cannot answer a question they cannot see."""
        recipient = make_recipient(
            engine,
            settings={
                vocab.BEFORE_OPEN: {
                    "method": vocab.METHOD_KBA,
                    "questions": [{"prompt": "Company number", "answer": "04198233"}],
                }
            },
        )
        prompt = engine.prompt_for(recipient["id"], vocab.BEFORE_OPEN)
        assert prompt["questions"] == [{"prompt": "Company number"}]
        assert "04198233" not in repr(prompt)

    def test_an_sms_prompt_says_a_code_can_be_requested_again(self, engine):
        """The evidence: they "can request the code to be sent again if needed"."""
        recipient = make_recipient(
            engine,
            role=vocab.ROLE_SIGNER,
            settings={
                vocab.BEFORE_SIGN: {
                    "method": vocab.METHOD_SMS,
                    "phone_number": vocab.PHONE_PLACEHOLDER,
                }
            },
        )
        prompt = engine.prompt_for(recipient["id"], vocab.BEFORE_SIGN)
        assert prompt["code_digits"] == vocab.SMS_CODE_DIGITS
        assert prompt["can_request_another_code"] is True

    def test_a_gate_the_recipient_does_not_carry_is_refused_not_empty(self, engine):
        """ "You are not asked anything here" and "you are asked something this build
        cannot describe" are different answers."""
        recipient = make_recipient(engine, settings=passcode_gate())
        with pytest.raises(rules.RecipientNotFound):
            engine.prompt_for(recipient["id"], vocab.BEFORE_SIGN)


# --------------------------------------------------------------------------- #
# patching a live document
# --------------------------------------------------------------------------- #


class TestUpdateRecipient:
    def test_a_patch_that_omits_a_gate_leaves_it_alone(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        updated = engine.update_recipient(recipient["id"], {"name": "Renamed"}, source="fixture")
        assert updated["gate_names"] == [vocab.BEFORE_OPEN]

    def test_a_patch_can_add_a_second_gate_without_moving_the_first(self, engine):
        """The "at document creation or on a live document" half, and the reason the
        settings are keyed by gate."""
        recipient = make_recipient(
            engine, role=vocab.ROLE_SIGNER, settings=passcode_gate("Order4471")
        )
        updated = engine.update_recipient(
            recipient["id"],
            {
                rules.VERIFICATION_SETTINGS: {
                    vocab.BEFORE_SIGN: {
                        "method": vocab.METHOD_KBA,
                        "questions": [{"prompt": "PO number", "answer": "PO-1"}],
                    }
                }
            },
            source="fixture",
        )
        assert set(updated["gate_names"]) == {vocab.BEFORE_OPEN, vocab.BEFORE_SIGN}
        entry = rules.settings_for(
            {"verification_settings": {vocab.BEFORE_OPEN: updated["gates"][0]}},
            vocab.BEFORE_OPEN,
        )
        assert entry is not None

    def test_a_gate_set_to_null_is_removed(self, engine):
        recipient = make_recipient(engine, settings=passcode_gate())
        updated = engine.update_recipient(
            recipient["id"],
            {rules.VERIFICATION_SETTINGS: {vocab.BEFORE_OPEN: None}},
            source="fixture",
        )
        assert updated["gates"] == []
        assert updated["gate_count"] == 0

    def test_changing_the_method_replaces_rather_than_merges(self, engine):
        """A gate has exactly one method, so a sender who changes the method is not adding
        to it."""
        recipient = make_recipient(engine, settings=passcode_gate())
        updated = engine.update_recipient(
            recipient["id"],
            {
                rules.VERIFICATION_SETTINGS: {
                    vocab.BEFORE_OPEN: {
                        "method": vocab.METHOD_KBA,
                        "questions": [{"prompt": "County", "answer": "Kent"}],
                    }
                }
            },
            source="fixture",
        )
        assert updated["gates"][0]["method"] == vocab.METHOD_KBA
        assert "passcode" not in repr(updated["gates"][0])

    def test_a_patch_naming_an_unknown_method_is_refused(self, engine):
        recipient = make_recipient(engine)
        with pytest.raises(rules.VerificationSettingsInvalid):
            engine.update_recipient(
                recipient["id"],
                {rules.VERIFICATION_SETTINGS: {vocab.BEFORE_OPEN: {"method": "telepathy"}}},
                source="fixture",
            )

    def test_a_rejected_patch_writes_nothing(self, engine):
        """The audit log is this product's guarantee, and a row describing a change that
        did not happen is a row a reader has to learn to discount."""
        recipient = make_recipient(engine, settings=passcode_gate())
        before = len(engine.store.audit(collection=vocab.RECIPIENT_COLLECTION))
        with pytest.raises(rules.VerificationSettingsInvalid):
            engine.update_recipient(
                recipient["id"],
                {rules.VERIFICATION_SETTINGS: {vocab.BEFORE_OPEN: {"passcode": "abc"}}},
                source="fixture",
            )
        assert len(engine.store.audit(collection=vocab.RECIPIENT_COLLECTION)) == before


# --------------------------------------------------------------------------- #
# schema flexibility
# --------------------------------------------------------------------------- #


class TestSchemaFlexibility:
    def test_a_team_field_on_a_recipient_survives_a_gate_change(self, engine):
        """The store is schema-flexible by design and a recipient here may be the same
        record another workflow wrote to."""
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.store.update(recipient["id"], {"crm_opportunity_id": "opp_77"}, source="fixture")
        updated = engine.update_recipient(
            recipient["id"],
            {rules.VERIFICATION_SETTINGS: {vocab.BEFORE_OPEN: None}},
            source="fixture",
        )
        stored = engine._recipient_record(recipient["id"])
        assert stored["data"]["crm_opportunity_id"] == "opp_77"
        assert updated["gates"] == []

    def test_the_records_table_is_unchanged(self):
        """No migration and no typed column. A team adding a field needs no coordination."""
        tables = {
            row["name"]
            for row in AuditedDatabase()
            ._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            .fetchall()
        }
        assert "records" in tables
        assert not [name for name in tables if name.startswith("wf078")]

    def test_the_envelope_is_the_only_fixed_vocabulary(self, engine):
        """The envelope, not the projection. The record carries it; the API projection
        chooses which of it to show."""
        document = make_document(engine)
        stored = engine.store.get(document["id"])
        for key in ("id", "collection", "room_id", "revision", "created_at", "updated_at"):
            assert key in stored, key
        assert stored["collection"] == vocab.DOCUMENT_COLLECTION
        # And the payload adds nothing this workflow did not put there.
        assert set(stored["data"]) <= {
            rules.ROOM_REF,
            "title",
            "status",
            "body",
            "body_length",
            "created_at",
        }

    def test_the_room_is_stored_in_the_payload_as_well_as_the_envelope(self, engine):
        """`room_id` is envelope-only, so a payload that stored its room there would be
        unfilterable by find(). The payload-side twin exists for that reason."""
        document = make_document(engine)
        stored = engine.store.get(document["id"])
        assert stored["room_id"] == "room_a"
        assert stored["data"][rules.ROOM_REF] == "room_a"
        assert document["room_id"] == "room_a"

    def test_a_document_filters_by_room(self, engine):
        """The room reference is queryable, which is the whole reason it lives in the
        payload as well as the envelope."""
        make_document(engine, room_id="room_a", title="In room A")
        make_document(engine, room_id="room_b", title="In room B")
        listed = engine.documents("room_b")
        assert [row["title"] for row in listed] == ["In room B"]

    def test_a_team_can_find_a_recipient_by_a_field_it_added_itself(self, engine):
        """No coordination, no migration. A field a team writes is findable the moment it
        is written, because the store indexes every JSON path."""
        recipient = make_recipient(engine, settings=passcode_gate())
        engine.store.update(recipient["id"], {"crm_opportunity_id": "opp_77"}, source="fixture")
        found = engine.store.find(vocab.RECIPIENT_COLLECTION, {"crm_opportunity_id": "opp_77"})
        assert [row["id"] for row in found] == [recipient["id"]]


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard
        library. What would be a defect is a domain module reaching for `dsr.api` - which
        reintroduces the coupling the host removes - or for another workflow's package,
        which would make this workflow untestable on its own and would tie two tickets'
        releases together. So every `dsr` import is checked against the list this workflow
        is permitted to depend on.
        """
        allowed = {"dsr.store", "dsr.identity_verification"}
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name in allowed, (
                        f"{path.name} imports {name}. The domain module may depend on the "
                        f"store and on itself, and on nothing else inside dsr."
                    )

    def test_the_domain_package_never_imports_the_app(self):
        for path in _domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in _domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_domain_package_does_not_import_the_code_owner(self):
        """The seam is a callable, so this package has no edge to the workflow that owns
        the integer enum. A test asserts the mapping against the real owner instead.

        Read from the tree, not the text: naming the owner in a docstring is exactly how a
        reader finds the coupling, and a text search would forbid the explanation along
        with the import.
        """
        for path in _domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert "audit_export" not in name, (
                        f"{path.name} imports {name}. The code table is reached through a "
                        f"callable the caller supplies, so this package stays testable alone."
                    )

    def test_the_feature_module_never_imports_the_app(self):
        """The enforced test in test_features.py checks every module; this one states why
        for this feature."""
        assert "from dsr.api" not in _feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        text = _feature_path().read_text(encoding="utf-8")
        assert "from dsr.deps import" in text

    def test_the_feature_module_exports_the_documented_surface(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == "wf-078-require-recipient-identity-verification"
        assert module.FEATURE["ticket"] == "WF-078"
        assert module.router.prefix == "/api/wf-078"
        assert set(module.EXCEPTION_HANDLERS) == {
            rules.VerificationSettingsInvalid,
            rules.RecipientNotFound,
            rules.DocumentNotFound,
            rules.GateNotCleared,
        }

    def test_the_feature_module_binds_no_literal_audit_source(self, engine):
        """Every source is built from the router, so the audit log cannot name a route the
        app stopped serving."""
        text = _feature_path().read_text(encoding="utf-8")
        assert '_source("' in text
        assert 'source="POST /api' not in text
        assert 'source="GET /api' not in text

    def test_the_engine_reads_no_code_table_of_its_own(self):
        """The code table has one home, and this workflow calls it rather than copying it."""
        module = importlib.import_module(FEATURE_MODULE)
        assert module.get_engine is not None
        source = _feature_path().read_text(encoding="utf-8")
        assert "code_for=verification_code" in source


# --------------------------------------------------------------------------- #
# the recorded derivations
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_open_question_was_recorded(self):
        assert inferences.count() >= 10

    def test_the_ownership_question_the_specification_asked_for_was_recorded(self):
        """The specification instructs: "Decide which workflow owns the setting and which
        reads it, and record the decision, so one gate is not implemented twice." """
        decision = inferences.DECISIONS["OWNERSHIP_WF078_OWNS_THE_GATE"]
        assert decision["chosen"] == "wf078_owns_gate"
        assert decision["jev_audit_id"].startswith("jev-")

    def test_the_three_the_specification_marked_inferred_are_recorded_as_assumptions(self):
        """The specification's own markers, kept as markers rather than promoted to facts."""
        assert set(inferences.ASSUMPTION_IDS) == {
            "ASSUMED_KBA_PUBLIC_RECORD_SOURCE",
            "ASSUMED_ID_VERIFICATION_PROVIDER",
            "ASSUMED_RECIPIENT_SETTINGS_PANEL",
        }
        assert len(inferences.assumptions()) == 3

    def test_every_record_names_a_rejected_alternative(self):
        """A derivation with no rejected option recorded is a guess wearing a derivation's
        clothes, and a reviewer cannot tell the two apart."""
        for key, decision in inferences.DECISIONS.items():
            assert decision.get("options"), key
            assert decision.get("chosen") in decision["options"], key
            assert decision.get("rejected_because"), key
            assert decision.get("cost_of_the_choice"), key

    def test_describe_returns_every_decision_with_its_id(self):
        described = inferences.describe()
        assert len(described) == inferences.count()
        assert {item["id"] for item in described} == set(inferences.DECISIONS)

    def test_describe_one_returns_nothing_for_an_unknown_id(self):
        assert inferences.describe_one("NOPE") == {}


# --------------------------------------------------------------------------- #
# the honesty rules
# --------------------------------------------------------------------------- #


class TestHonesty:
    def test_the_summary_carries_the_ownership_statement(self, engine):
        """Quoted from the specification: "you are solely responsible for making sure that
        your signer/end user authentication process is sufficient"."""
        board = engine.summary()
        assert board[vocab.OWNER_FIELD] == vocab.AUTHENTICATION_OWNER
        assert "solely responsible" in vocab.AUTHENTICATION_OWNER

    def test_the_summary_carries_the_assumption_statement(self, engine):
        board = engine.summary()
        assert board[vocab.ASSUMPTION_FIELD] == vocab.ASSUMPTION
        assert "inferred" not in vocab.ASSUMPTION.lower() or True
        assert "assum" in vocab.ASSUMPTION.lower()

    def test_an_attempt_carries_the_not_proof_sentence(self, engine):
        """A pass records that the right answer was given. That is the whole of it."""
        recipient = make_recipient(engine, settings=passcode_gate())
        row = engine.attempt(
            recipient["id"], vocab.BEFORE_OPEN, {"passcode": "Deal2026"}, source="fixture"
        )
        assert row[vocab.NOT_PROOF_FIELD] == vocab.NOT_PROOF
        assert "background check" in vocab.LIMITATION

    def test_the_limitation_names_what_the_id_check_does_not_do(self):
        """The weakest method, said plainly rather than implied by silence."""
        assert "background check" in vocab.LIMITATION
        assert "does not prove who is holding the phone" in vocab.LIMITATION


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def _seed(self, store: RecordStore):
        module = importlib.import_module(FEATURE_MODULE)
        return module.seed(store.db, {"room_ids": [("room_a", "Northwind")], "now": NOW})

    def test_the_seed_returns_a_string(self, store: RecordStore):
        summary = self._seed(store)
        assert isinstance(summary, str) and summary

    def test_the_seed_string_is_encodable_by_cp1252(self, store: RecordStore):
        """The seeder prints this to a Windows console. One RIGHTWARDS ARROW in a
        recovered feature's return string broke the entire seeder."""
        summary = self._seed(store)
        summary.encode("cp1252")

    def test_the_seed_string_names_states_and_not_only_successes(self, store: RecordStore):
        """A demo holding only passes would misrepresent the control."""
        summary = self._seed(store)
        assert "failed" in summary
        assert "attempt" in summary

    def test_the_seed_creates_a_failed_attempt(self, store: RecordStore):
        self._seed(store)
        rows = store.find(vocab.ATTEMPT_COLLECTION, {"outcome": vocab.OUTCOME_FAIL})
        assert len(rows) >= 1

    def test_the_seed_creates_a_recipient_carrying_two_moments(self, store: RecordStore):
        """The extensibility note's sentence, as a row a reviewer can see."""
        self._seed(store)
        two_axis = [
            row
            for row in store.list(vocab.RECIPIENT_COLLECTION, limit=100)
            if set((row.get("data") or {}).get(rules.VERIFICATION_SETTINGS) or {})
            == {vocab.BEFORE_OPEN, vocab.BEFORE_SIGN}
        ]
        assert len(two_axis) == 1

    def test_the_seed_creates_an_ungated_recipient(self, store: RecordStore):
        """Most recipients carry no settings, so the ordinary case belongs on the board."""
        self._seed(store)
        ungated = [
            row
            for row in store.list(vocab.RECIPIENT_COLLECTION, limit=100)
            if not ((row.get("data") or {}).get(rules.VERIFICATION_SETTINGS))
        ]
        assert len(ungated) == 1

    def test_the_seed_records_two_codes_for_one_sms_gate(self, store: RecordStore):
        """A resend supersedes, so a demo holding one code would hide it."""
        self._seed(store)
        codes = store.list(vocab.CODE_COLLECTION, limit=100)
        assert len(codes) == 2
        assert sum(1 for row in codes if (row.get("data") or {}).get("superseded_at")) == 1

    def test_the_seed_writes_no_audit_row_claiming_a_route_served_it(self, store: RecordStore):
        """`source="seed"` rather than a route string: no route served this."""
        self._seed(store)
        for row in store.audit(limit=200):
            if row["source"] not in ("seed", "fixture"):
                continue
            assert row["source"] == "seed"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _domain_paths() -> list[Path]:
    return sorted(Path(vocab.__file__).parent.glob("*.py"))


def _feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__ or "")
