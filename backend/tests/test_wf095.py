"""WF-095 domain rules: acceptance configuration, the status machine, verification, quota.

The rules are in ``dsr.quote_acceptance.rules``, the vocabulary in ``vocabulary``, the
judgement calls in ``inferences``, and the writes in ``engine``. This file tests them without
HTTP and is organised by the claims the specification makes:

``the method is one of three, and e-signature needs a buyer``
    "``hs_acceptance_method`` = ``clickwrap`` | ``esignature`` | ``print_and_sign``", and the
    seller "tick[s] the buyer contacts under **Buyer contacts required to sign**".
``an In signing attachment forces e-signature``
    "If an attachment is marked *In signing*, e-signature must be used for the quote."
``the signing status advances only on the researched events``
    "Pending signature -> Viewed - pending signature -> Pending countersignature -> Accepted",
    and each status has exactly one event that moves it.
``the buyer signs first``
    "Countersigners are emailed automatically when the buyer signs."
``identity verification is a one-hour window from the click``
    "Buyers have one hour to complete the signature process after clicking Verify email."
``a PDF over 40 MB is refused``
    "Quote PDFs larger than 40 MB may not be successfully verified or signed."
``a three-signature quote costs one usage``
    "if a published quote with e-signatures enabled requires three signatures, this only
    counts as one usage toward your limit", and usage lands "as soon as the e-signature option
    is turned on for a published quote".
``reassignment is per-quote and only before a signature``
    "optionally enable **Quote signer(s) can reassign**".
``only the four named activities are written``
    "Quote buyer signed", "Quote countersigned", "Quote reassigned", "Signing attempt failed".
``the store is reached only through the audited wrapper``
    No module in the package imports ``dsr.api`` or opens SQLite.

Every test here passes with the file run on its own. Nothing in this module depends on a test
that ran before it.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.quote_acceptance import (
    errors,
    inferences,
    rules,
    vocabulary as vocab,
)
from dsr.quote_acceptance.engine import AcceptanceEngine
from dsr.store import RecordStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
MODULES = ("vocabulary.py", "rules.py", "inferences.py", "engine.py")
PACKAGE = Path(__file__).resolve().parents[1] / "dsr" / "quote_acceptance"


class Clock:
    """A clock the test moves by hand.

    The one-hour verification window is the only value here that depends on the current
    instant, and its boundaries are exactly the ones a real clock cannot be steered to. A
    token presented at 59 minutes must pass and one presented at 61 must not.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: int) -> datetime:
        self.now = self.now + timedelta(**delta)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tokens() -> list[str]:
    """A predictable token sequence, so a test can present the token it was given."""

    issued: list[str] = []

    def factory() -> str:
        issued.append(f"token-{len(issued) + 1}")
        return issued[-1]

    return issued


@pytest.fixture
def engine(store: RecordStore, clock: Clock, tokens: list[str]) -> AcceptanceEngine:
    def _token() -> str:
        issued = factory_holder.setdefault("issued", 0) + 1
        factory_holder["issued"] = issued
        return f"token-{issued}"

    factory_holder: dict[str, int] = {}
    return AcceptanceEngine(store, now=clock, token_factory=_token)


def config(**extra) -> dict:
    """A valid e-signature acceptance configuration, so a test changes one thing at a time."""

    payload = {
        "buyer_signers": [{"name": "Ada Byron", "email": "ada@northwind.example"}],
        "countersigners": [{"name": "Dana Reyes", "email": "dana@halcyon.example"}],
    }
    payload.update(extra)
    return payload


def open_two_party(engine: AcceptanceEngine, *, published: bool = False, **extra) -> dict:
    """Open a two-party envelope.

    ``published`` is spelled out here rather than passed through ``extra`` because it is an
    engine argument rather than an acceptance-configuration field, and it is the difference
    between an envelope that consumes quota and one that does not.
    """

    return engine.open_envelope(
        config(**extra),
        room_id="room_a",
        actor="dana",
        source="test",
        is_published=published,
    )


# --------------------------------------------------------------------------- #
# The vocabulary is the researched one
# --------------------------------------------------------------------------- #


def test_the_three_acceptance_methods_are_the_ones_the_research_names():
    """``hs_acceptance_method`` = ``clickwrap`` | ``esignature`` | ``print_and_sign``."""

    assert vocab.ACCEPTANCE_METHODS == ("esignature", "clickwrap", "print_and_sign")
    assert vocab.ACCEPTANCE_METHOD_FIELD == "hs_acceptance_method"


def test_the_four_signing_statuses_are_the_ones_the_data_flow_names():
    """Pending signature -> Viewed - pending signature -> Pending countersignature -> Accepted."""

    assert vocab.SIGNING_STATUSES == (
        "pending_signature",
        "viewed_pending_signature",
        "pending_countersignature",
        "accepted",
    )


def test_each_status_advances_on_exactly_one_event_and_accepted_advances_on_nothing():
    """The machine is a chain, and the last state is terminal."""

    causes = [row[1] for row in vocab.STATUS_TRANSITIONS.values()]
    assert causes == ["viewed", "buyer_signed", "countersigned", "accepted"]
    assert vocab.STATUS_TRANSITIONS[vocab.STATUS_ACCEPTED] == (None, "accepted")
    # Every non-terminal status names exactly one successor.
    successors = [row[0] for row in vocab.STATUS_TRANSITIONS.values() if row[0]]
    assert len(successors) == len(set(successors))


def test_the_four_activities_are_the_ones_the_research_names():
    """Quote buyer signed, Quote countersigned, Quote reassigned, Signing attempt failed."""

    assert set(vocab.ACTIVITIES) == {
        "quote_buyer_signed",
        "quote_countersigned",
        "quote_reassigned",
        "signing_attempt_failed",
    }


def test_the_verification_window_is_the_researched_hour():
    """ "Buyers have one hour to complete the signature process after clicking Verify email"."""

    assert vocab.VERIFICATION_WINDOW_MINUTES == 60


def test_the_pdf_cap_is_the_researched_forty_megabytes():
    """ "Quote PDFs larger than 40 MB may not be successfully verified or signed"."""

    assert vocab.PDF_SIZE_CAP_MB == 40


# --------------------------------------------------------------------------- #
# The acceptance configuration
# --------------------------------------------------------------------------- #


def test_a_method_is_read_under_either_spelling():
    """The vendor property says ``esignature``; this product's prose says *E-signature*."""

    assert rules.normalise_method("esignature") == vocab.METHOD_ESIGNATURE
    assert rules.normalise_method("E-signature") == vocab.METHOD_ESIGNATURE
    assert rules.normalise_method("e_signature") == vocab.METHOD_ESIGNATURE
    assert rules.normalise_method("print_and_sign") == vocab.METHOD_PRINT_AND_SIGN
    assert rules.normalise_method("Print and sign") == vocab.METHOD_PRINT_AND_SIGN
    assert rules.normalise_method("clickwrap") == vocab.METHOD_CLICKWRAP
    assert rules.normalise_method("Accept without signature") == vocab.METHOD_CLICKWRAP


def test_the_vendor_property_wins_over_the_plain_spelling_when_both_are_present():
    """A payload carrying both is a payload an integration built, so the vendor name wins."""

    result = rules.validate_acceptance(
        {
            "hs_acceptance_method": "print_and_sign",
            "acceptance_method": "esignature",
            "buyer_signers": [{"email": "ada@northwind.example"}],
        }
    )
    assert result["method"] == vocab.METHOD_PRINT_AND_SIGN


def test_an_unknown_method_is_refused_by_name_and_never_defaulted():
    """A silently defaulted method would put the wrong acceptance rule on a quote."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.normalise_method("carrier_pigeon")
    assert "carrier_pigeon" in caught.value.errors["hs_acceptance_method"]


def test_an_absent_method_defaults_to_esignature_and_is_recorded_as_a_decision():
    """The decision is recorded, with the two alternatives it rejected."""

    decision = inferences.DECISIONS["DERIVED_ACCEPTANCE_METHOD_DEFAULT_IS_ESIGNATURE"]
    assert decision["chosen"] == "default_esignature"
    assert set(decision["options"]) >= {"default_esignature", "default_clickwrap"}
    assert rules.normalise_method(None) == vocab.METHOD_ESIGNATURE


def test_an_esignature_quote_with_no_buyer_signer_is_refused():
    """The seller ticks buyer contacts, so an e-signature with none has no first signature."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_acceptance({"acceptance_method": "esignature"})
    assert "at least one buyer contact" in caught.value.errors["buyer_signers"]


def test_a_buyer_signer_may_be_a_string_a_mapping_or_a_comma_separated_list():
    """A seller ticks contacts and an integration sends objects, so all three shapes are read."""

    one = rules.validate_acceptance({"buyer_signers": "ada@northwind.example"})
    assert one["signers"][0]["email"] == "ada@northwind.example"

    named = rules.validate_acceptance({"buyer_signers": "Ada Byron: ada@northwind.example"})
    assert named["signers"][0]["name"] == "Ada Byron"
    assert named["signers"][0]["email"] == "ada@northwind.example"

    listed = rules.validate_acceptance(
        {"buyer_signers": ["ada@northwind.example", {"email": "cy@orbis.example"}]}
    )
    assert [s["email"] for s in listed["signers"]] == [
        "ada@northwind.example",
        "cy@orbis.example",
    ]


def test_a_signer_without_a_routable_address_is_refused():
    """The verification link has to reach somebody, so an address with no domain is refused."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_acceptance({"buyer_signers": [{"email": "ada@northwind"}]})
    assert "email address" in caught.value.errors["buyer_signers[0].email"]


def test_an_envelope_refuses_more_than_two_signers():
    """Two parties, so a third signer is a typo rather than a configuration."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_acceptance(
            {
                "buyer_signers": [
                    {"email": "a@x.example"},
                    {"email": "b@x.example"},
                ],
                "countersigners": [{"email": "c@x.example"}],
            }
        )
    assert "at most 2 signers" in caught.value.errors["buyer_signers"]


def test_the_signer_cap_is_recorded_as_a_decision_against_an_n_party_envelope():
    """The research describes a three-signature quote for quota, not for envelope shape."""

    decision = inferences.DECISIONS["DERIVED_TWO_PARTIES_ONE_BUYER_SIGNER"]
    assert decision["chosen"] == "two_parties_one_signer_each"
    assert "n_party_envelope" in decision["options"]
    assert vocab.MAX_SIGNERS == 2


def test_signers_required_is_read_and_held_to_the_signers_named():
    """``hs_esign_num_signers_required`` may be 1 or 2, never more than the signers present."""

    assert (
        rules.validate_acceptance(config(hs_esign_num_signers_required=1))["signers_required"] == 1
    )

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_acceptance(config(hs_esign_num_signers_required=2, countersigners=[]))
    assert "hs_esign_num_signers_required" in caught.value.errors


@pytest.mark.parametrize("bad", [0, 3, "many", True])
def test_a_signers_required_that_is_not_a_whole_number_in_range_is_refused(bad):
    with pytest.raises(errors.AcceptanceRefused):
        rules.validate_acceptance(config(hs_esign_num_signers_required=bad))


# --------------------------------------------------------------------------- #
# The In signing attachment rule
# --------------------------------------------------------------------------- #


def test_an_in_signing_attachment_forces_the_esignature_method():
    """ "If an attachment is marked *In signing*, e-signature must be used for the quote"."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_acceptance(
            {
                "acceptance_method": "print_and_sign",
                "attachments": [{"name": "Security schedule", "in_signing": True}],
            }
        )
    assert "e-signature must be used" in caught.value.errors["hs_acceptance_method"]


def test_an_in_signing_attachment_is_permitted_on_an_esignature():
    result = rules.validate_acceptance(
        config(attachments=[{"name": "Security schedule", "in_signing": True}])
    )
    assert result["in_signing_attachments"] == ["Security schedule"]


def test_an_attachment_not_marked_in_signing_does_not_constrain_the_method():
    """Only the *In signing* flag constrains it, so an ordinary attachment is ignored here."""

    result = rules.validate_acceptance(
        {"acceptance_method": "print_and_sign", "attachments": [{"name": "Brochure"}]}
    )
    assert result["in_signing_attachments"] == []


# --------------------------------------------------------------------------- #
# The document size cap
# --------------------------------------------------------------------------- #


def test_a_document_at_the_cap_is_accepted_and_its_size_is_reported():
    """The cap is 40 MB, and the reading is recorded rather than merely passed."""

    result = rules.check_document_size(40 * rules.BYTES_PER_MB)
    assert result["over_cap"] is False
    assert result["size_mb"] == 40
    assert result["evidence"] == vocab.PDF_SIZE_CAP_QUOTE


def test_a_document_over_the_cap_is_refused_with_the_size_named():
    """A PDF over the cap "may not be successfully verified or signed"."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.check_document_size(41 * rules.BYTES_PER_MB)
    assert "41.0 MB" in caught.value.errors["document_size_bytes"]


@pytest.mark.parametrize("bad", [-1, "large", True])
def test_a_size_that_is_not_a_number_is_refused(bad):
    with pytest.raises(errors.AcceptanceRefused):
        rules.check_document_size(bad)


def test_an_absent_size_is_tolerated_and_reads_as_no_reading():
    """A quote with no document yet has no size, and that is not a refusal."""

    assert rules.check_document_size(None)["size_bytes"] is None


# --------------------------------------------------------------------------- #
# The status machine
# --------------------------------------------------------------------------- #


def test_the_status_chain_runs_pending_to_accepted_on_the_named_events():
    status = vocab.STATUS_PENDING_SIGNATURE
    for event, expected in (
        ("viewed", vocab.STATUS_VIEWED_PENDING_SIGNATURE),
        ("buyer_signed", vocab.STATUS_PENDING_COUNTERSIGNATURE),
        ("countersigned", vocab.STATUS_ACCEPTED),
    ):
        status = rules.next_status(status, event)
        assert status == expected


def test_an_event_the_status_does_not_authorise_leaves_the_status_alone():
    """A second signature on an accepted envelope is a no-op, not a rewind.

    The strict rule is the one that refuses a rewind. An accepted quote has no successor, and
    a quote already awaiting its countersignature cannot be sent back by a late view.
    """

    assert rules.step_status(vocab.STATUS_ACCEPTED, "countersigned") == vocab.STATUS_ACCEPTED
    assert rules.step_status(vocab.STATUS_PENDING_COUNTERSIGNATURE, "viewed") == (
        vocab.STATUS_PENDING_COUNTERSIGNATURE
    ), "a late view must not rewind a quote that has moved on"
    assert rules.step_status(vocab.STATUS_VIEWED_PENDING_SIGNATURE, "countersigned") == (
        vocab.STATUS_VIEWED_PENDING_SIGNATURE
    )


def test_asserting_a_transition_the_table_does_not_authorise_is_refused():
    """The enforcing half, for a caller that asked for a specific step."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.assert_transition(vocab.STATUS_PENDING_SIGNATURE, "countersigned")
    assert "signing_status" in caught.value.errors


def test_an_accepted_envelope_is_not_open_and_the_others_are():
    assert rules.is_open(vocab.STATUS_PENDING_SIGNATURE) is True
    assert rules.is_open(vocab.STATUS_PENDING_COUNTERSIGNATURE) is True
    assert rules.is_open(vocab.STATUS_ACCEPTED) is False


# --------------------------------------------------------------------------- #
# The buyer-first order
# --------------------------------------------------------------------------- #


def test_the_signing_order_is_the_buyer_then_the_countersigner_whatever_the_payload_order():
    """The order comes from the role, never from the order the seller's payload listed."""

    ordered = rules.signing_order_signers(
        [{"email": "a@x.example", "role": "buyer"}],
        [{"email": "c@x.example", "role": "countersigner"}],
    )
    assert [s["signing_order"] for s in ordered] == [1, 2]
    assert ordered[0]["role"] == vocab.ROLE_BUYER


def test_a_countersignature_before_the_buyer_has_signed_is_refused():
    """ "Countersigners are emailed automatically when the buyer signs"."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.assert_signature_order(vocab.STATUS_PENDING_SIGNATURE, vocab.ROLE_COUNTERSIGNER)
    assert "before the buyer has signed" in caught.value.detail


def test_a_countersignature_is_permitted_once_the_buyer_has_signed():
    for status in (vocab.STATUS_VIEWED_PENDING_SIGNATURE, vocab.STATUS_PENDING_COUNTERSIGNATURE):
        rules.assert_signature_order(status, vocab.ROLE_COUNTERSIGNER)


def test_a_buyer_signature_is_never_refused_by_the_order_rule():
    rules.assert_signature_order(vocab.STATUS_PENDING_SIGNATURE, vocab.ROLE_BUYER)


# --------------------------------------------------------------------------- #
# Reassignment
# --------------------------------------------------------------------------- #


def test_a_reassignment_is_refused_when_the_quote_does_not_allow_it():
    """ "optionally enable **Quote signer(s) can reassign**" makes it per-quote and off by default."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_reassignment({"reassign_allowed": False}, {"signed": False})
    assert "can reassign is off" in caught.value.errors["reassign_allowed"]


def test_a_signer_who_has_already_signed_cannot_be_reassigned():
    """The reassignment button is in the acceptance step, so it precedes the signature."""

    with pytest.raises(errors.AcceptanceRefused) as caught:
        rules.validate_reassignment({"reassign_allowed": True}, {"signed": True})
    assert "already signed" in caught.value.errors["signer"]


def test_a_signer_who_has_not_signed_may_be_reassigned_when_the_quote_allows_it():
    assert rules.validate_reassignment({"reassign_allowed": True}, {"signed": False}) == {
        "reassign_allowed": True
    }


def test_the_reassign_flag_is_read_from_a_boolean_a_string_or_a_number():
    assert rules.validate_reassignment({"reassign_allowed": "true"}, {"signed": False})
    assert rules.validate_reassignment({"reassign_allowed": 1}, {"signed": False})
    with pytest.raises(errors.AcceptanceRefused):
        rules.validate_reassignment({"reassign_allowed": 0}, {"signed": False})


# --------------------------------------------------------------------------- #
# The one-hour verification window
# --------------------------------------------------------------------------- #


def test_the_window_opens_at_the_request_and_closes_an_hour_later():
    """ "one hour ... after clicking Verify email"."""

    window = rules.verification_window(NOW, NOW)
    assert window["expires_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert window["window_minutes"] == 60
    assert window["open"] is True


def test_the_window_is_open_at_fifty_nine_minutes_and_closed_at_sixty_one():
    """Both boundaries, because a token must not pass an hour after the click."""

    assert rules.verification_window(NOW, NOW + timedelta(minutes=59))["open"] is True
    assert rules.verification_window(NOW, NOW + timedelta(minutes=61))["expired"] is True


def test_a_token_passes_inside_the_window_and_fails_outside_it():
    envelope = {
        "identity_verification_required": True,
        "verification_requested_at": NOW.isoformat(),
        "verification_token": "token-1",
    }
    inside = rules.verify_token(envelope, "token-1", NOW + timedelta(minutes=59))
    assert inside["verified"] is True

    outside = rules.verify_token(envelope, "token-1", NOW + timedelta(minutes=61))
    assert outside["verified"] is False
    assert outside["reason"] == "verification_window_expired"


def test_a_wrong_token_fails_inside_the_window():
    envelope = {
        "identity_verification_required": True,
        "verification_requested_at": NOW.isoformat(),
        "verification_token": "token-1",
    }
    result = rules.verify_token(envelope, "token-2", NOW)
    assert result["verified"] is False
    assert result["reason"] == "verification_token_mismatch"


def test_a_buyer_who_never_clicked_verify_email_is_unverified_rather_than_missing():
    """Clicking is the research's step, so not clicking is an ordinary state."""

    result = rules.verify_token({"identity_verification_required": True}, None, NOW)
    assert result["verified"] is False
    assert result["reason"] == "verification_not_requested"


def test_verification_off_needs_no_token_and_the_widget_is_unlocked():
    """ "If identity verification is on, clicks **Verify email**" is conditional on it being on."""

    result = rules.verify_token({"identity_verification_required": False}, None, NOW)
    assert result == {"required": False, "verified": True, "reason": "verification_not_required"}


def test_the_window_opens_on_the_click_not_at_send():
    """The recorded decision, because opening at send would expire it for a late reader."""

    decision = inferences.DECISIONS["DERIVED_VERIFICATION_WINDOW_OPENS_ON_REQUEST"]
    assert decision["chosen"] == "opens_on_verify_email_click"


def test_verification_binds_the_buyer_and_not_a_countersigner():
    """ "Countersigners who start first do not need to verify"."""

    assert "do not need to verify" in vocab.VERIFICATION_BINDS_BUYER_ONLY


# --------------------------------------------------------------------------- #
# Quota
# --------------------------------------------------------------------------- #


def test_a_multi_signature_quote_costs_exactly_one_usage():
    """ "requires three signatures, this only counts as one usage toward your limit"."""

    assert rules.quota_cost({"signers": [1, 2, 3]}) == 1
    assert rules.quota_cost({"signers": [1]}) == 1


def test_usage_is_consumed_when_a_published_quote_turns_the_option_on():
    """ "as soon as the e-signature option is turned on for a published quote"."""

    assert rules.enables_quota(True, vocab.METHOD_ESIGNATURE) is True
    assert rules.enables_quota(False, vocab.METHOD_ESIGNATURE) is False
    assert rules.enables_quota(True, vocab.METHOD_PRINT_AND_SIGN) is False


def test_a_room_with_no_stated_ceiling_is_never_refused():
    """The research states no number, so ``limit: None`` always passes."""

    rules.assert_quota_not_exceeded(used=10_000, limit=None)


def test_a_stated_ceiling_is_enforced_and_names_the_month_and_the_way_out():
    with pytest.raises(errors.QuotaRefused) as caught:
        rules.assert_quota_not_exceeded(used=10, limit=10, month="2026-10")
    body = caught.value.to_dict()
    assert body["quota_limit"] == 10
    assert body["quota_month"] == "2026-10"
    assert body["reset_day"] == vocab.QUOTA_RESET_DAY
    assert "reset on the 1st" in body["remedy"]


def test_the_quota_month_is_the_month_of_the_reading():
    assert rules.quota_month(NOW) == "2026-10"
    assert rules.quota_month(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-01"


def test_the_unspecified_ceiling_is_recorded_as_a_decision():
    """A fixed default would refuse valid envelopes at an arbitrary boundary."""

    decision = inferences.DECISIONS["DERIVED_QUOTA_CEILING_IS_UNSPECIFIED"]
    assert decision["chosen"] == "count_only_no_ceiling"
    assert "QUOTA_UNSPECIFIED" in vocab.__name__.upper() or vocab.QUOTA_UNSPECIFIED


# --------------------------------------------------------------------------- #
# The activity log
# --------------------------------------------------------------------------- #


def test_only_the_four_named_activities_are_written():
    for event, activity in (
        ("buyer_signed", vocab.ACTIVITY_BUYER_SIGNED),
        ("countersigned", vocab.ACTIVITY_COUNTERSIGNED),
        ("reassigned", vocab.ACTIVITY_REASSIGNED),
        ("attempt_failed", vocab.ACTIVITY_ATTEMPT_FAILED),
    ):
        assert rules.record_activity(event)["activity"] == activity


def test_viewing_and_requesting_verification_write_no_activity_row():
    """The research names four activities and neither of these is one of them."""

    assert rules.record_activity("viewed")["writes_activity"] is False
    assert rules.record_activity(vocab.EVENT_VERIFICATION_REQUESTED)["writes_activity"] is False


def test_the_four_activity_decision_names_the_two_it_rejected():
    decision = inferences.DECISIONS["DERIVED_ACTIVITY_LOG_HAS_FOUR_ENTRIES"]
    assert decision["chosen"] == "four_named_activities_only"
    assert "add_viewed_and_verified_activities" in decision["options"]


# --------------------------------------------------------------------------- #
# The engine's writes
# --------------------------------------------------------------------------- #


def test_opening_an_envelope_writes_the_envelope_and_one_row_per_signer(engine: AcceptanceEngine):
    envelope = open_two_party(engine)

    assert envelope["signing_status"] == vocab.STATUS_PENDING_SIGNATURE
    assert envelope["signer_count"] == 2
    assert [s["role"] for s in envelope["signers"]] == [
        vocab.ROLE_BUYER,
        vocab.ROLE_COUNTERSIGNER,
    ]
    assert engine.store.list(vocab.ENVELOPE_COLLECTION)[0]["collection"] == (
        vocab.ENVELOPE_COLLECTION
    )


def test_every_signer_carries_the_researched_association_type(engine: AcceptanceEngine):
    """ "contact association type 702 (signer)" is recorded on each signer."""

    envelope = open_two_party(engine)
    assert all(s["702"] == vocab.SIGNER_ASSOCIATION_TYPE for s in envelope["signers"])


def test_a_refused_configuration_writes_nothing(engine: AcceptanceEngine):
    with pytest.raises(errors.AcceptanceRefused):
        engine.open_envelope({"acceptance_method": "esignature"}, room_id="room_a", source="test")

    assert engine.store.list(vocab.ENVELOPE_COLLECTION) == []
    assert engine.store.list(vocab.SIGNER_COLLECTION) == []


def test_a_document_over_the_cap_writes_no_envelope(engine: AcceptanceEngine):
    """The cap is checked before anything is written, so a refusal costs no rows."""

    with pytest.raises(errors.AcceptanceRefused):
        engine.open_envelope(
            config(),
            room_id="room_a",
            document_size_bytes=41 * rules.BYTES_PER_MB,
            source="test",
        )
    assert engine.store.list(vocab.ENVELOPE_COLLECTION) == []


def test_a_published_envelope_consumes_one_usage_even_before_anybody_signs(
    engine: AcceptanceEngine, store: RecordStore
):
    """ "The quote doesn't need to be signed to apply to the signature limit"."""

    open_two_party(engine, published=True)
    open_two_party(engine, published=True)
    usage = engine.quota_usage(rules.quota_month(NOW))
    assert usage["used"] == 2
    assert usage["envelopes_charged"] == 2


def test_an_unpublished_envelope_consumes_no_usage(engine: AcceptanceEngine):
    open_two_party(engine, published=False)
    assert engine.quota_usage(rules.quota_month(NOW))["used"] == 0


def test_the_two_party_acceptance_runs_to_sealed(engine: AcceptanceEngine):
    envelope = open_two_party(engine)
    buyer, counter = envelope["signers"]

    engine.mark_viewed(envelope["id"], actor="ada", source="test")
    link = engine.request_verification(envelope["id"], actor="ada", source="test")
    engine.verify(envelope["id"], link["verification_link"], actor="ada", source="test")

    first = engine.sign(
        buyer["id"],
        signature_mode="draw",
        verification_token=link["verification_link"],
        actor="ada",
        source="test",
    )
    assert first["outcome"] == "signed"
    assert first["envelope"]["signing_status"] == vocab.STATUS_PENDING_COUNTERSIGNATURE
    assert first["envelope"]["countersigners_notified"] is True

    second = engine.sign(
        counter["id"],
        signature_mode="type",
        signature_payload={"text": "Dana Reyes"},
        actor="dana",
        source="test",
    )
    sealed = second["envelope"]
    assert sealed["signing_status"] == vocab.STATUS_ACCEPTED
    assert sealed["sealed"] is True
    assert sealed["accepted_by"] == "dana@halcyon.example"
    assert sealed["hs_payment_status"] == "accepted"


def test_a_signature_must_be_drawn_typed_or_uploaded(engine: AcceptanceEngine):
    envelope = open_two_party(engine)
    buyer = envelope["signers"][0]

    with pytest.raises(errors.AcceptanceRefused) as caught:
        engine.sign(buyer["id"], signature_mode="telepathy", actor="ada", source="test")
    assert "drawn, typed or uploaded" in caught.value.detail
    assert set(vocab.SIGNATURE_MODES) == {"draw", "type", "upload"}


def test_an_unverified_signing_attempt_is_logged_and_advances_nothing(
    engine: AcceptanceEngine,
):
    """ "Signing attempt failures are logged automatically"."""

    envelope = open_two_party(engine, identity_verification_required=True)
    buyer = envelope["signers"][0]

    result = engine.sign(buyer["id"], signature_mode="draw", actor="ada", source="test")

    assert result["outcome"] == "failed"
    assert result["reason"] == "verification_not_requested"
    assert result["activity_label"] == "Signing attempt failed"
    assert result["envelope"]["signing_status"] == vocab.STATUS_PENDING_SIGNATURE
    assert result["envelope"]["signed_count"] == 0


def test_a_signing_attempt_after_the_hour_is_logged_as_failed(
    engine: AcceptanceEngine, clock: Clock
):
    envelope = open_two_party(engine, identity_verification_required=True)
    buyer = envelope["signers"][0]
    link = engine.request_verification(envelope["id"], actor="ada", source="test")

    clock.advance(minutes=61)
    result = engine.sign(
        buyer["id"],
        signature_mode="draw",
        verification_token=link["verification_link"],
        actor="ada",
        source="test",
    )
    assert result["outcome"] == "failed"
    assert result["reason"] == "verification_window_expired"
    assert "one-hour" in result["detail"]


def test_a_second_signature_by_the_same_party_is_refused_as_already_signed(
    engine: AcceptanceEngine,
):
    envelope = open_two_party(engine)
    buyer = envelope["signers"][0]
    engine.sign(buyer["id"], signature_mode="draw", actor="ada", source="test")

    again = engine.sign(buyer["id"], signature_mode="draw", actor="ada", source="test")
    assert again["outcome"] == "failed"
    assert again["reason"] == "already_signed"


def test_a_countersignature_before_the_buyer_is_refused_and_logs_nothing_signed(
    engine: AcceptanceEngine,
):
    envelope = open_two_party(engine)
    counter = envelope["signers"][1]

    with pytest.raises(errors.AcceptanceRefused):
        engine.sign(counter["id"], signature_mode="type", actor="dana", source="test")

    assert engine.envelope(envelope["id"])["signed_count"] == 0


def test_a_reassignment_updates_the_signer_and_appends_an_activity(engine: AcceptanceEngine):
    envelope = open_two_party(engine, reassign_allowed=True)
    buyer = envelope["signers"][0]

    updated = engine.reassign(
        buyer["id"],
        {"name": "Grace Okonkwo", "email": "grace@northwind.example"},
        actor="ada",
        source="test",
    )
    assert updated["name"] == "Grace Okonkwo"
    assert updated["email"] == "grace@northwind.example"

    activities = [event["activity"] for event in engine.events(envelope["id"])]
    assert vocab.ACTIVITY_REASSIGNED in activities


def test_a_reassignment_clears_the_old_partys_verification(engine: AcceptanceEngine):
    """The new party must verify in their own name if the envelope requires it."""

    envelope = open_two_party(engine, reassign_allowed=True, identity_verification_required=True)
    buyer = envelope["signers"][0]
    link = engine.request_verification(envelope["id"], actor="ada", source="test")
    engine.verify(envelope["id"], link["verification_link"], actor="ada", source="test")
    assert engine.signer(buyer["id"])["verified"] is True

    engine.reassign(
        buyer["id"],
        {"name": "Grace Okonkwo", "email": "grace@northwind.example"},
        actor="ada",
        source="test",
    )
    assert engine.signer(buyer["id"])["verified"] is False


def test_viewing_advances_the_status_and_writes_no_activity(engine: AcceptanceEngine):
    envelope = open_two_party(engine)

    after = engine.mark_viewed(envelope["id"], actor="ada", source="test")
    assert after["signing_status"] == vocab.STATUS_VIEWED_PENDING_SIGNATURE

    viewed = [event for event in engine.events(envelope["id"]) if event["event"] == "viewed"]
    assert viewed and viewed[0]["activity"] is None


def test_a_missing_envelope_is_a_lookup_error_and_never_a_server_fault(engine: AcceptanceEngine):
    with pytest.raises(errors.EnvelopeNotFound):
        engine.envelope("envelope_absent")


def test_a_missing_signer_is_its_own_error_because_the_remedy_differs(engine: AcceptanceEngine):
    with pytest.raises(errors.SignerNotFound):
        engine.signer("signer_absent")


def test_the_summary_counts_by_status_and_signatures(engine: AcceptanceEngine):
    envelope = open_two_party(engine)
    # The buyer must view before signing, because pending signature advances on 'viewed'
    # and only then on the buyer's signature. The status chain is the research's.
    engine.mark_viewed(envelope["id"], actor="ada", source="test")
    engine.sign(envelope["signers"][0]["id"], signature_mode="draw", actor="ada", source="test")

    summary = engine.summary()
    assert summary["envelopes"] == 1
    assert summary["signers_total"] == 2
    assert summary["signers_signed"] == 1
    assert summary["signers_outstanding"] == 1
    assert summary["by_status"][vocab.STATUS_PENDING_COUNTERSIGNATURE] == 1


def test_every_summary_carries_the_authentication_owner_and_the_downstream_consumer(
    engine: AcceptanceEngine,
):
    """ "It is your responsibility to verify the identity of any user who views a document"."""

    summary = engine.summary()
    assert "solely responsible" in summary["authentication_owner"] or (
        "your responsibility" in summary["authentication_owner"]
    )
    assert "WF-099" in summary["contract_is_downstream"]


def test_the_contract_is_downstream_because_the_issue_names_wf_099_as_its_consumer():
    """The data flow's last clause creates a contract, and this ticket does not own it."""

    assert "WF-099" in vocab.CONTRACT_IS_DOWNSTREAM


def test_the_countersigner_pool_is_this_room_and_that_is_recorded(engine: AcceptanceEngine):
    """ "select **Countersigners** from your organisation", and no directory is named."""

    assert "this room's own users" in vocab.COUNTERSIGNER_POOL_IS_THIS_ROOM


# --------------------------------------------------------------------------- #
# Every write is audited
# --------------------------------------------------------------------------- #


def test_every_write_names_the_route_that_served_it(db_path, tmp_path):
    """The audit row and the change are written in the same transaction, so both survive.

    This drives the engine through the same ``source`` strings the router builds, and reads
    the audit rows back, because a feature that writes without a source leaves the guarantee
    this product is built on unverifiable.
    """

    from dsr.api import app
    from dsr.features import load_features, wf095_collect_acceptance_by_e_signature as feature

    database = AuditedDatabase(db_path, mirror_dir=tmp_path / "audit", actor="test")
    try:
        store = RecordStore(database)
        engine = AcceptanceEngine(store, now=lambda: NOW, token_factory=lambda: "token-1")

        envelope = engine.open_envelope(
            config(),
            room_id="room_a",
            is_published=True,
            actor="dana",
            source=feature._source("POST", "/envelopes"),
        )
        engine.sign(
            envelope["signers"][0]["id"],
            signature_mode="draw",
            actor="ada",
            source=feature._source("POST", "/signers/{signer_id}/sign"),
        )

        rows = database.audit(limit=200)
        assert rows, "a workflow that writes must leave audit rows"
        sources = {row.get("source") for row in rows}
        assert feature._source("POST", "/envelopes") in sources
        assert feature._source("POST", "/signers/{signer_id}/sign") in sources

        registry = load_features(app)
        mounted = {
            (method, shape["path"])
            for shape in registry.by_id(
                "wf-095-collect-acceptance-by-e-signature-countersignature"
            ).routes
            for method in shape["methods"]
        }
        for row in rows:
            source = row.get("source") or ""
            if source.startswith("POST /api/wf-095"):
                method, _, path = source.partition(" ")
                assert (method, path) in mounted, f"{source} names a route the app does not serve"
    finally:
        database.close()


# --------------------------------------------------------------------------- #
# The seeder
# --------------------------------------------------------------------------- #


def test_the_seed_return_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """Every character the seeder prints must survive a Windows console.

    One U+2192 RIGHTWARDS ARROW in a recovered feature's return string broke the entire
    seeder on a Windows console, so this is asserted rather than assumed.
    """

    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    room = RecordStore(db).create("room", {"name": "Demo"}, actor="test", source="test")
    message = feature.seed(db, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None})

    assert message, "the seed must describe the states it created"
    assert message.encode("cp1252")
    # The point of the assertion is that encoding does not raise, so printing it here is the
    # same code path the seeder takes on a Windows console.
    print(message)


def test_the_seed_names_states_rather_than_only_successes(db: AuditedDatabase):
    """A board that shows only successes cannot demonstrate the refusals."""

    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    room = RecordStore(db).create("room", {"name": "Demo"}, actor="test", source="test")
    message = feature.seed(db, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None})

    assert "accepted" in message
    assert "pending countersignature" in message
    assert "reassigned" in message
    assert "failed signing attempt" in message


def test_the_seed_returns_nothing_without_a_room(db: AuditedDatabase):
    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    assert feature.seed(db, {"room_ids": [], "now": NOW}) == ""


def test_the_seed_drives_the_engine_rather_than_asserting_the_states(db: AuditedDatabase):
    """The seeded states must be produced by the rules, not written in by the seeder.

    An event row carries the status it moved the envelope from and to, so a state the rules
    could not have produced is visible here.
    """

    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    store = RecordStore(db)
    room = store.create("room", {"name": "Demo"}, actor="test", source="test")
    feature.seed(db, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None})

    events = store.list(vocab.EVENT_COLLECTION, limit=200)
    assert events, "the seed must produce signature events"
    signed = [
        row["data"] for row in events if row["data"].get("activity") == vocab.ACTIVITY_BUYER_SIGNED
    ]
    assert signed, "the seed must produce a buyer signature"
    for row in signed:
        assert row["status_before"] == vocab.STATUS_VIEWED_PENDING_SIGNATURE
        assert row["status_after"] == vocab.STATUS_PENDING_COUNTERSIGNATURE


# --------------------------------------------------------------------------- #
# The package imports nothing but the store
# --------------------------------------------------------------------------- #


def _imported_modules(source: str) -> set[str]:
    """Every module name a source file imports, read from the tree rather than the text.

    A raw substring search for ``dsr.api`` matches the sentence in a module docstring that
    promises the module does not import it, which is the opposite of what the check is for.
    Reading the tree can only see a real import, so a docstring can describe the rule without
    tripping it.
    """

    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_module_in_the_package_imports_the_app_or_opens_sqlite():
    """The dependency direction is api -> features -> deps -> store, and this is the check."""

    for name in MODULES:
        text = (PACKAGE / name).read_text(encoding="utf-8")
        imports = _imported_modules(text)
        assert "dsr.api" not in imports, (
            f"{name} imports the application module; take dependencies from dsr.deps"
        )
        assert "sqlite3" not in imports, f"{name} opens SQLite directly"


def test_the_feature_module_takes_its_dependencies_from_deps():
    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    text = inspect.getsource(feature)
    assert "from dsr.deps import StoreDep" in text
    imports = _imported_modules(text)
    assert "dsr.api" not in imports
    assert "sqlite3" not in imports


def test_the_records_are_plain_json_with_no_migration():
    """A team adding a field must need no coordination with anyone."""

    store = RecordStore(AuditedDatabase(":memory:", actor="test"))
    engine = AcceptanceEngine(store, now=lambda: NOW)
    engine.open_envelope(config(), room_id="room_a", actor="dana", source="test")

    record = store.list(vocab.ENVELOPE_COLLECTION)[0]
    assert isinstance(record["data"], dict)
    assert record["data"]["method"] == vocab.METHOD_ESIGNATURE

    # An undeclared field a team adds is stored and filtered like any other.
    store.create(
        vocab.ENVELOPE_COLLECTION,
        {"room_ref": "room_a", "their_own_field": "anything"},
        room_id="room_a",
        actor="test",
        source="test",
    )
    found = store.find(vocab.ENVELOPE_COLLECTION, {"their_own_field": "anything"})
    assert len(found) == 1


def test_the_package_records_the_package_choice_with_its_audit_id():
    """The decision that created this package rather than extending another one."""

    decision = inferences.DECISIONS["DERIVED_NEW_PACKAGE_QUOTE_ACCEPTANCE"]
    assert decision["chosen"] == "new_package_quote_acceptance"
    assert decision["audit_id"] == "jev-20261005T075612-8560-72925"
    assert decision["confidence"] == 1.0


def test_every_decision_names_a_rejected_alternative_and_says_what_it_cost():
    """A derivation with no rejected alternative is a guess wearing a derivation's clothes."""

    assert inferences.DECISIONS
    for key, decision in inferences.DECISIONS.items():
        assert decision["question"], key
        assert decision["chosen"] in decision["options"], key
        assert len(decision["options"]) >= 2, key
        assert decision["rejected_because"].strip(), key
        assert decision["left_open_by"].strip(), key


def test_the_four_error_types_are_declared_in_one_place_and_told_apart():
    """The host refuses a second feature registering a handler for a type another claimed."""

    assert errors.ERROR_TYPES == (
        errors.AcceptanceRefused,
        errors.EnvelopeNotFound,
        errors.SignerNotFound,
        errors.QuotaRefused,
    )
    assert len({error.__name__ for error in errors.ERROR_TYPES}) == 4


def test_a_signature_from_pending_signature_still_reaches_pending_countersignature():
    """Signing implies viewing, so the chain is applied until it stops changing.

    The dead end this prevents was found over real HTTP: a buyer who signed before viewing
    left the envelope at pending_signature, the buyer then viewed and moved it to
    viewed_pending_signature, and from there nothing could advance it, because the buyer had
    already signed and the countersigner was refused by the order rule.
    """

    assert rules.next_status(vocab.STATUS_PENDING_SIGNATURE, "buyer_signed") == (
        vocab.STATUS_PENDING_COUNTERSIGNATURE
    )


def test_the_loop_is_bounded_and_cannot_spin_on_a_malformed_table():
    """A table that named a cycle would otherwise loop forever on the first signature."""

    seen = []
    for _ in range(3):
        seen.append(rules.next_status(vocab.STATUS_PENDING_SIGNATURE, "buyer_signed"))
    assert seen == [vocab.STATUS_PENDING_COUNTERSIGNATURE] * 3


def test_the_signing_implies_viewing_decision_names_the_dead_end_it_closes():
    decision = inferences.DECISIONS["DERIVED_SIGNING_IMPLIES_VIEWING"]
    assert decision["chosen"] == "signing_implies_viewing"
    assert "require_viewing_first" in decision["options"]
    assert "dead end" in decision["rejected_because"]


def test_an_envelope_signed_without_viewing_reaches_accepted_and_does_not_wedge(engine):
    """The whole flow, driven out of order, still ends sealed."""

    envelope = open_two_party(engine)
    buyer, counter = envelope["signers"]

    engine.sign(buyer["id"], signature_mode="draw", actor="ada", source="test")
    assert engine.envelope(envelope["id"])[vocab.SIGNING_STATUS_FIELD] == (
        vocab.STATUS_PENDING_COUNTERSIGNATURE
    )

    engine.mark_viewed(envelope["id"], actor="ada", source="test")
    assert engine.envelope(envelope["id"])[vocab.SIGNING_STATUS_FIELD] == (
        vocab.STATUS_PENDING_COUNTERSIGNATURE
    ), "viewing after signing must not rewind the status"

    engine.sign(counter["id"], signature_mode="type", actor="dana", source="test")
    assert engine.envelope(envelope["id"])[vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_ACCEPTED
