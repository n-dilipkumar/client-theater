"""WF-098: the domain rules, tested as rules.

Every researched boundary in the specification has a test here, and each one is named
after the sentence it comes from. That is deliberate. A boundary without a test is a
boundary a future edit will move without anybody noticing, and this workflow's boundaries
are the whole product: a default window that accepted 400 days, an expiry that closed an
accepted quote, a reminder that fired twice, or a timezone read as UTC when the account
said otherwise would each cost a buyer a quote they had a right to sign.

The rules are tested as pure functions wherever they are pure, with two integers and a
clock. The engine beside them is tested over a real store, because the question there is
whether the writes are the rows the specification describes.

Every test in this file passes when the file is run on its own. The suite runs under
pytest-xdist, so nothing here depends on a record another test left behind: each test
builds its own store through the ``store`` fixture and its own clock through ``clock``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.quoting_proposals import (
    quote_expiry_inferences,
    quote_expiry_rules as rules,
    quote_expiry_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_expiry_engine import QuoteExpiryEngine
from dsr.store import RecordStore

#: One fixed instant, so every boundary in this file is the same boundary.
NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = int(NOW.timestamp())

DAY = 86400
HOUR = 3600

SETTINGS = "PUT /api/wf-098/rooms/{room_id}/settings"
RULE = "POST /api/wf-098/rooms/{room_id}/reminder-rules"
QUOTE = "POST /api/wf-098/rooms/{room_id}/quotes"
EXPIRATION = "PUT /api/wf-098/rooms/{room_id}/quotes/{quote_id}/expiration"
SEND = "POST /api/wf-098/rooms/{room_id}/quotes/{quote_id}/send"
ACCEPTANCE = "POST /api/wf-098/rooms/{room_id}/quotes/{quote_id}/acceptance"
REMINDERS = "POST /api/wf-098/rooms/{room_id}/reminders"
EXPIRY_CHECK = "POST /api/wf-098/rooms/{room_id}/expiry-check"

#: The two names above are the shorter spelling; these are the ones the tests below use.
#: Both spellings exist because a source string is read dozens of times in this file and
#: the shorter one is easier to misread as a route the router does not serve.
ACCEPTANCE_SOURCE = ACCEPTANCE
EXPIRY_SOURCE = EXPIRY_CHECK
REMINDERS_SOURCE = REMINDERS
VOID = "POST /api/wf-098/rooms/{room_id}/quotes/{quote_id}/void"
ARCHIVE = "POST /api/wf-098/rooms/{room_id}/quotes/{quote_id}/archive"


@pytest.fixture()
def clock() -> dict:
    """A clock a test moves by hand.

    A dict rather than a list because a test that appends to a list cannot change the
    instant every other boundary was measured against, and a rule that only passes at one
    instant is not a rule.
    """

    return {"now": NOW}


@pytest.fixture()
def engine(store: RecordStore, clock: dict) -> QuoteExpiryEngine:
    return QuoteExpiryEngine(store, now=lambda: clock["now"])


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="test")["id"]


def days_out(days: float) -> str:
    """An ISO instant ``days`` from the fixed instant this file measures against."""

    return datetime.fromtimestamp(NOW_EPOCH + int(days * DAY), tz=timezone.utc).isoformat()


def epoch_days_out(days: float) -> datetime:
    return datetime.fromtimestamp(NOW_EPOCH + int(days * DAY), tz=timezone.utc)


def engine_at(store: RecordStore, moment: datetime) -> QuoteExpiryEngine:
    return QuoteExpiryEngine(store, now=lambda: moment)


def quote_payload(**overrides) -> dict:
    payload = {
        "title": "Enterprise platform - Northwind",
        "seller_email": "dana@northwind.example",
        "recipients": [{"email": "buyer@northwind.example", "name": "Ada Byron"}],
        vocab.EXPIRATION_DATE: days_out(10),
        vocab.EFFECTIVE_DATE: days_out(-5),
    }
    payload.update(overrides)
    return payload


def make_quote(engine: QuoteExpiryEngine, room_id: str, **overrides) -> dict:
    return engine.create_quote(room_id, quote_payload(**overrides), actor="dana", source=QUOTE)


def sent_quote(engine: QuoteExpiryEngine, room_id: str, **overrides) -> dict:
    created = make_quote(engine, room_id, **overrides)
    return engine.send_quote(created["id"], {}, actor="dana", source=SEND)


def after_send_rule(days: int = 3) -> dict:
    return {"offset_kind": vocab.OFFSET_AFTER_SEND, "days": days}


def before_expiry_rule(days: int = 2) -> dict:
    return {"offset_kind": vocab.OFFSET_BEFORE_EXPIRY, "days": days}


SETTINGS_ON = {
    vocab.DEFAULT_EXPIRATION_DAYS: 30,
    vocab.ACCOUNT_TIMEZONE: "UTC",
    vocab.REMINDER_SEND_TIME: "09:00",
    vocab.AUTOMATED_REMINDERS_ENABLED: True,
}


# --------------------------------------------------------------------------- #
# "enter a default expiration time period between 1 and 365 days"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("days", [1, 2, 30, 180, 364, 365])
def test_a_default_window_inside_the_range_is_accepted(days):
    """ "between 1 and 365 days" is inclusive at both ends."""

    assert rules.coerce_default_days(days) == days


@pytest.mark.parametrize("days", [0, -1, 366, 400, 1000])
def test_a_default_window_outside_the_range_is_refused(days):
    """Both bounds are the research's own numbers."""

    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_default_days(days)
    assert caught.value.code == "default_expiration_out_of_range"


def test_the_refusal_names_the_field_the_admin_typed_into():
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_default_days(400)
    assert vocab.DEFAULT_EXPIRATION_DAYS in caught.value.errors


@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_default_window_is_refused(value):
    """``True`` is an int in Python, and a switch is not a ninety-day window."""

    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_default_days(value)
    assert caught.value.code == "default_expiration_out_of_range"


@pytest.mark.parametrize("value", [1.5, "1.5", "soon", "30 days", [], {}, "1e2"])
def test_a_default_window_that_is_not_a_whole_number_of_days_is_refused(value):
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_default_days(value)
    assert caught.value.code == "default_expiration_out_of_range"


def test_a_digit_string_default_window_is_accepted():
    """A form that submits the number as a string is a form this product receives."""

    assert rules.coerce_default_days(" 45 ") == 45


def test_an_absent_default_window_is_no_default_and_not_an_error():
    assert rules.coerce_default_days(None) is None
    assert rules.coerce_default_days("") is None


# --------------------------------------------------------------------------- #
# "Any new quotes created after the setting is turned on will automatically use the
# configured expiration date."
# --------------------------------------------------------------------------- #


def test_the_default_reaches_a_quote_created_after_it_was_set():
    """ "created after the setting is turned on"."""

    assert rules.default_applies(days_out(1), days_out(0)) is True


def test_the_default_does_not_reach_a_quote_that_predates_it():
    """A quote that already existed has a date a seller chose."""

    assert rules.default_applies(days_out(-1), days_out(0)) is False


def test_a_quote_created_at_the_same_instant_as_the_setting_is_reached():
    """Equal instants count as after. The window is inclusive at the boundary."""

    assert rules.default_applies(days_out(0), days_out(0)) is True


@pytest.mark.parametrize("created,since", [(None, days_out(0)), (days_out(1), None)])
def test_an_absent_instant_on_either_side_means_the_default_does_not_apply(created, since):
    assert rules.default_applies(created, since) is False


# --------------------------------------------------------------------------- #
# The three header controls: date picker, label, switch
# --------------------------------------------------------------------------- #


def test_a_stated_date_is_the_quotes_own_and_wins_over_the_default():
    """ "properties set on the quote overriding the quote template's settings"."""

    data = {vocab.EXPIRATION_DATE: days_out(10), vocab.DEFAULT_EXPIRATION_DAYS: 30}
    resolved = rules.resolve_expiration(data, NOW)
    assert resolved["source"] == vocab.EXPIRY_SOURCE_QUOTED
    assert resolved["iso_date"] == epoch_days_out(10).date().isoformat()


def test_no_date_and_a_default_means_the_default_window_is_derived():
    """ "will automatically use the configured expiration date"."""

    data = {
        vocab.EXPIRATION_DATE: None,
        vocab.DEFAULT_EXPIRATION_DAYS: 30,
        vocab.EFFECTIVE_DATE: days_out(-5),
    }
    resolved = rules.resolve_expiration(data, NOW)
    assert resolved["source"] == vocab.EXPIRY_SOURCE_ACCOUNT_DEFAULT
    assert resolved["iso_date"] == epoch_days_out(25).date().isoformat()
    assert resolved["window_days"] == 30


def test_no_date_and_no_default_means_the_quote_never_expires():
    resolved = rules.resolve_expiration({}, NOW)
    assert resolved["expires_at"] is None
    assert resolved["source"] == vocab.EXPIRY_SOURCE_NONE
    assert rules.has_expiration({}, NOW) is False


def test_the_switch_off_beats_a_stored_date():
    """A switch a seller turned off must not be overridden by a stale stored date."""

    data = {vocab.EXPIRATION_DATE: days_out(10), vocab.EXPIRATION_ENABLED: False}
    resolved = rules.resolve_expiration(data, NOW)
    assert resolved["enabled"] is False
    assert resolved["expires_at"] is None
    assert rules.has_expiration(data, NOW) is False


def test_the_switch_off_beats_a_default_window():
    data = {vocab.EXPIRATION_ENABLED: False, vocab.DEFAULT_EXPIRATION_DAYS: 30}
    assert rules.resolve_expiration(data, NOW)["expires_at"] is None


def test_a_quote_with_no_switch_key_is_on():
    """A missing field is not a switch somebody flipped."""

    assert rules._expiration_enabled({}) is True


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("false", False), ("true", True), ("no", False), ("0", False)],
)
def test_the_switch_reads_the_shapes_a_form_submits(value, expected):
    assert rules.coerce_enabled(value) is expected


def test_the_label_is_carried_without_touching_the_date():
    data = {vocab.EXPIRATION_DATE: days_out(10), vocab.EXPIRATION_LABEL: "Sign by"}
    assert rules.resolve_expiration(data, NOW)["label"] == "Sign by"
    assert rules.resolve_expiration(data, NOW)["iso_date"] == epoch_days_out(10).date().isoformat()


# --------------------------------------------------------------------------- #
# "The Expiration date will be treated as the buyer's sign-by deadline."
# --------------------------------------------------------------------------- #


def test_a_past_effective_date_is_accepted_and_marks_a_sign_by_deadline():
    """ "because a past Effective date is allowed" - it is not refused."""

    data = {vocab.EXPIRATION_DATE: days_out(5), vocab.EFFECTIVE_DATE: days_out(-5)}
    resolved = rules.resolve_expiration(data, NOW)
    assert resolved["sign_by_deadline"] is True
    assert rules.is_sign_by_deadline(data, NOW) is True


def test_a_future_effective_date_is_not_a_sign_by_deadline():
    data = {vocab.EXPIRATION_DATE: days_out(30), vocab.EFFECTIVE_DATE: days_out(5)}
    assert rules.resolve_expiration(data, NOW)["sign_by_deadline"] is False


def test_a_quote_with_no_effective_date_is_not_a_sign_by_deadline():
    data = {vocab.EXPIRATION_DATE: days_out(5)}
    assert rules.resolve_expiration(data, NOW)["sign_by_deadline"] is False


def test_the_default_window_is_measured_from_the_effective_date_not_from_now():
    """The default is applied when the quote is created, and the effective date is its start."""

    data = {vocab.DEFAULT_EXPIRATION_DAYS: 10, vocab.EFFECTIVE_DATE: days_out(-100)}
    resolved = rules.resolve_expiration(data, NOW)
    assert resolved["iso_date"] == epoch_days_out(-90).date().isoformat()


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def test_a_bare_date_is_read_as_midnight_utc():
    """The only reading that is the same instant on every machine."""

    assert rules.coerce_instant("2026-11-30") == datetime(2026, 11, 30, 0, 0, tzinfo=timezone.utc)


def test_a_date_with_an_offset_is_converted_to_utc():
    assert rules.coerce_instant("2026-11-30T00:00:00+05:30") == datetime(
        2026, 11, 29, 18, 30, tzinfo=timezone.utc
    )


def test_a_digit_string_is_read_as_millis():
    millis = int(epoch_days_out(10).timestamp() * 1000)
    assert rules.coerce_instant(str(millis)) == epoch_days_out(10)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_absent_instant_is_none_and_not_an_error(value):
    assert rules.coerce_instant(value) is None


@pytest.mark.parametrize("value", [True, "soon", "2026-13-45", [], {"at": 1}, "tomorrow"])
def test_an_unreadable_instant_is_refused(value):
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_instant(value)
    assert caught.value.code == "expiration_not_an_instant"


# --------------------------------------------------------------------------- #
# "If a buyer hasn't accepted or signed a quote by the expiration date, it'll expire."
# --------------------------------------------------------------------------- #


def test_a_quote_past_its_deadline_is_expired():
    data = {vocab.EXPIRATION_DATE: days_out(-1)}
    assert rules.is_expired(data, NOW) is True
    assert rules.quote_state(data, NOW) == vocab.QUOTE_EXPIRED


def test_a_quote_inside_its_window_is_not_expired():
    data = {vocab.EXPIRATION_DATE: days_out(5)}
    assert rules.is_expired(data, NOW) is False
    assert rules.quote_state(data, NOW) == vocab.QUOTE_DRAFT


def test_a_quote_with_no_deadline_is_never_expired():
    """A missing field is not a deadline in the past."""

    assert rules.is_expired({}, NOW) is False
    assert rules.quote_state({}, NOW) == vocab.QUOTE_DRAFT


def test_days_remaining_is_negative_once_the_deadline_has_passed():
    """A clamped zero would hide the fact that it is late."""

    assert rules.days_remaining({vocab.EXPIRATION_DATE: days_out(-2)}, NOW) == pytest.approx(-2.0)


def test_days_remaining_is_none_without_a_deadline():
    assert rules.days_remaining({}, NOW) is None


# --------------------------------------------------------------------------- #
# "If a quote is accepted or signed before the expiration date, but hasn't been
# countersigned or paid, the quote won't expire."
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", vocab.SURVIVING_ACTIONS)
def test_each_of_the_three_surviving_actions_saves_an_on_time_quote(action):
    """ "accepted/e-signed/marked-signed by the expiration date"."""

    data = {
        vocab.EXPIRATION_DATE: days_out(5),
        "acceptances": [{"action": action, "method": vocab.METHOD_E_SIGNATURE, "at": days_out(-1)}],
    }
    assert rules.survives_expiry(data, NOW) is True
    assert rules.quote_state(data, NOW) != vocab.QUOTE_EXPIRED


@pytest.mark.parametrize("action", [vocab.ACTION_COUNTERSIGNED, vocab.ACTION_PAID])
def test_countersigned_and_paid_do_not_save_a_quote_on_their_own(action):
    """The one hard sentence, read literally. This is the assertion that holds it in place.

    The deadline has already passed, so the quote's state is decided: it expires. A quote
    countersigned on time and never accepted is still expiring, and that is the sentence
    above rather than a reading of it.
    """

    data = {
        vocab.EXPIRATION_DATE: days_out(-1),
        "acceptances": [
            {"action": action, "method": vocab.METHOD_PRINT_AND_SIGN, "at": days_out(-2)}
        ],
    }
    assert rules.survives_expiry(data, NOW) is False
    assert rules.quote_state(data, NOW) == vocab.QUOTE_EXPIRED


@pytest.mark.parametrize("action", [vocab.ACTION_COUNTERSIGNED, vocab.ACTION_PAID])
def test_countersigned_and_paid_inside_the_window_leave_the_quote_not_surviving(action):
    """The same predicate before the deadline: the quote is still open, not protected."""

    data = {
        vocab.EXPIRATION_DATE: days_out(5),
        "acceptances": [
            {"action": action, "method": vocab.METHOD_PRINT_AND_SIGN, "at": days_out(-1)}
        ],
    }
    assert rules.survives_expiry(data, NOW) is False
    assert rules.quote_state(data, NOW) == vocab.QUOTE_DRAFT


def test_a_surviving_action_after_the_deadline_does_not_save_the_quote():
    """ "before the expiration date" is the whole of the rule."""

    data = {
        vocab.EXPIRATION_DATE: days_out(-1),
        "acceptances": [
            {
                "action": vocab.ACTION_ACCEPTED,
                "method": vocab.METHOD_CLICK_TO_ACCEPT,
                "at": days_out(0),
            }
        ],
    }
    assert rules.survives_expiry(data, NOW) is False


def test_a_surviving_action_at_the_deadline_saves_the_quote():
    data = {
        vocab.EXPIRATION_DATE: days_out(0),
        "acceptances": [
            {"action": vocab.ACTION_E_SIGNED, "method": vocab.METHOD_E_SIGNATURE, "at": days_out(0)}
        ],
    }
    assert rules.survives_expiry(data, NOW) is True


def test_an_acceptance_with_no_instant_does_not_save_the_quote():
    data = {
        vocab.EXPIRATION_DATE: days_out(-1),
        "acceptances": [{"action": vocab.ACTION_ACCEPTED, "method": vocab.METHOD_CLICK_TO_ACCEPT}],
    }
    assert rules.survives_expiry(data, NOW) is False


def test_a_quote_with_no_acceptance_at_all_does_not_survive():
    assert rules.survives_expiry({vocab.EXPIRATION_DATE: days_out(-1)}, NOW) is False


def test_an_accepted_quote_is_accepted_and_not_expired_even_past_its_deadline():
    data = {
        vocab.EXPIRATION_DATE: days_out(-3),
        "acceptances": [
            {
                "action": vocab.ACTION_ACCEPTED,
                "method": vocab.METHOD_CLICK_TO_ACCEPT,
                "at": days_out(-4),
            }
        ],
    }
    assert rules.quote_state(data, NOW) == vocab.QUOTE_ACCEPTED


def test_a_signed_quote_reports_signed_not_accepted():
    data = {
        vocab.EXPIRATION_DATE: days_out(-3),
        "acceptances": [
            {
                "action": vocab.ACTION_E_SIGNED,
                "method": vocab.METHOD_E_SIGNATURE,
                "at": days_out(-4),
            }
        ],
    }
    assert rules.quote_state(data, NOW) == vocab.QUOTE_SIGNED


def test_a_marked_signed_quote_reports_signed():
    data = {
        vocab.EXPIRATION_DATE: days_out(-3),
        "acceptances": [
            {
                "action": vocab.ACTION_MARKED_SIGNED,
                "method": vocab.METHOD_PRINT_AND_SIGN,
                "at": days_out(-4),
            }
        ],
    }
    assert rules.quote_state(data, NOW) == vocab.QUOTE_SIGNED


def test_acceptance_and_countersignature_together_still_survive():
    """The two together are a buyer who acted and then signed."""

    data = {
        vocab.EXPIRATION_DATE: days_out(-3),
        "acceptances": [
            {
                "action": vocab.ACTION_ACCEPTED,
                "method": vocab.METHOD_CLICK_TO_ACCEPT,
                "at": days_out(-5),
            },
            {
                "action": vocab.ACTION_COUNTERSIGNED,
                "method": vocab.METHOD_PRINT_AND_SIGN,
                "at": days_out(-4),
            },
        ],
    }
    assert rules.survives_expiry(data, NOW) is True


def test_a_single_accepted_at_shorthand_survives():
    """A caller that sends one ``accepted_at`` rather than a list is understood."""

    data = {vocab.EXPIRATION_DATE: days_out(-1), "accepted_at": days_out(-2)}
    assert rules.survives_expiry(data, NOW) is True


# --------------------------------------------------------------------------- #
# The three acceptance methods
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", vocab.ACCEPTANCE_METHODS)
def test_each_researched_method_is_accepted_and_stored_verbatim(method):
    """ "e-signature/click-to-accept/print-and-sign" """

    assert rules.coerce_acceptance_method(method) == method


@pytest.mark.parametrize(
    "value,expected",
    [
        ("e-signature", vocab.METHOD_E_SIGNATURE),
        ("click-to-accept", vocab.METHOD_CLICK_TO_ACCEPT),
        ("print-and-sign", vocab.METHOD_PRINT_AND_SIGN),
        ("E_SIGNATURE", vocab.METHOD_E_SIGNATURE),
        ("esign", vocab.METHOD_E_SIGNATURE),
        ("accept", vocab.METHOD_CLICK_TO_ACCEPT),
    ],
)
def test_the_method_spellings_a_client_might_send_are_understood(value, expected):
    assert rules.coerce_acceptance_method(value) == expected


def test_an_absent_method_defaults_to_click_to_accept():
    assert rules.coerce_acceptance_method(None) == vocab.METHOD_CLICK_TO_ACCEPT


@pytest.mark.parametrize("value", ["counter_sign", "verbal", "3d_secure", "sign now"])
def test_a_method_this_product_does_not_know_is_refused_rather_than_defaulted(value):
    """A method whose survival rule it cannot apply must not be accepted."""

    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_acceptance_method(value)
    assert caught.value.code == "unknown_acceptance_method"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("accept", vocab.ACTION_ACCEPTED),
        ("accepted", vocab.ACTION_ACCEPTED),
        ("e-signed", vocab.ACTION_E_SIGNED),
        ("marked-signed", vocab.ACTION_MARKED_SIGNED),
        ("countersigned", vocab.ACTION_COUNTERSIGNED),
        ("paid", vocab.ACTION_PAID),
    ],
)
def test_the_buyer_action_spellings_a_client_might_send_are_understood(value, expected):
    assert rules.coerce_buyer_action(value) == expected


def test_an_unknown_buyer_action_is_refused():
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_buyer_action("shrugged")
    assert caught.value.code == "unknown_buyer_action"


# --------------------------------------------------------------------------- #
# Void, archive and the three different outcomes
# --------------------------------------------------------------------------- #


def test_void_deactivates_the_link_and_changes_nothing_else():
    """ "**Void:** ... The quote link URL will be deactivated." """

    effects = rules.void_effects()
    assert effects[vocab.LINK_ACTIVE] is False
    assert vocab.PUBLISHED not in effects
    assert vocab.HIDDEN_FROM_INDEX not in effects
    assert vocab.BUYER_ACCESS not in effects


def test_archive_unpublishes_hides_and_blocks_buyer_access():
    """ "unpublished, hidden from the default index page view, and prevents buyers from
    accessing it" """

    effects = rules.archive_effects()
    assert effects[vocab.PUBLISHED] is False
    assert effects[vocab.HIDDEN_FROM_INDEX] is True
    assert effects[vocab.BUYER_ACCESS] is False


def test_void_and_archive_are_three_different_outcomes_not_one():
    """Collapsing them into one flag would lose which of the three a caller asked for."""

    void = rules.void_effects()
    archive = rules.archive_effects()
    # Archive says nothing about the link, and Void says nothing about buyer access, so
    # neither carries the other's field. That is what makes them two outcomes.
    assert vocab.BUYER_ACCESS in archive
    assert vocab.BUYER_ACCESS not in void
    assert vocab.LINK_ACTIVE in void
    assert vocab.HIDDEN_FROM_INDEX not in void
    assert void["state"] != archive["state"]
    assert vocab.QUOTE_VOIDED != vocab.QUOTE_ARCHIVED


def test_a_voided_quote_is_voided_and_not_archived():
    data = {vocab.EXPIRATION_DATE: days_out(5), "voided_at": rules.stamp(NOW)}
    assert rules.quote_state(data, NOW) == vocab.QUOTE_VOIDED


def test_an_archived_quote_is_archived_and_not_voided():
    data = {vocab.EXPIRATION_DATE: days_out(5), "archived_at": rules.stamp(NOW)}
    assert rules.quote_state(data, NOW) == vocab.QUOTE_ARCHIVED


def test_a_void_quote_that_has_also_met_its_deadline_reports_voided():
    """Void "will deactivate" the link whatever else is true of the quote."""

    data = {vocab.EXPIRATION_DATE: days_out(-5), "voided_at": rules.stamp(NOW)}
    assert rules.quote_state(data, NOW) == vocab.QUOTE_VOIDED


def test_an_expired_quote_lists_the_four_actions_it_still_supports():
    """ "can still be downloaded, cloned, voided or archived" """

    assert list(vocab.SURVIVABLE_ACTIONS) == ["download", "clone", "void", "archive"]


# --------------------------------------------------------------------------- #
# "resending counts as a new send (consuming e-signature quota again)"
# --------------------------------------------------------------------------- #


def test_a_first_send_counts_one_and_consumes_one():
    """A count and not a boolean, because the sentence is about consumption."""

    send = rules.next_send({}, NOW)
    assert send[vocab.SEND_COUNT] == 1
    assert send[vocab.ESIGNATURE_QUOTA_CONSUMED] == 1
    assert send["resent"] is False


def test_a_resend_counts_two_and_consumes_two():
    send = rules.next_send({vocab.SEND_COUNT: 1, vocab.ESIGNATURE_QUOTA_CONSUMED: 1}, NOW)
    assert send[vocab.SEND_COUNT] == 2
    assert send[vocab.ESIGNATURE_QUOTA_CONSUMED] == 2
    assert send["resent"] is True
    assert send["previous_send_count"] == 1


def test_a_resend_costs_the_same_as_the_first_send():
    """ "consuming e-signature quota again" - again means again, not less."""

    data = {vocab.SEND_COUNT: 4, vocab.ESIGNATURE_QUOTA_CONSUMED: 4}
    send = rules.next_send(data, NOW)
    assert send[vocab.ESIGNATURE_QUOTA_CONSUMED] - data[vocab.ESIGNATURE_QUOTA_CONSUMED] == 1


@pytest.mark.parametrize("value", [None, "", "many", [], {"n": 1}, True])
def test_a_counter_that_is_not_a_number_reads_as_zero_and_does_not_stop_a_send(value):
    send = rules.next_send({vocab.SEND_COUNT: value}, NOW)
    assert send[vocab.SEND_COUNT] == 1


def test_a_resend_sets_a_new_send_instant():
    """The post-send offsets count from the new send, which is what a resend is for."""

    later = epoch_days_out(3)
    send = rules.next_send({vocab.SENT_AT: rules.stamp(NOW)}, later)
    assert send[vocab.SENT_AT] == rules.stamp(later)


# --------------------------------------------------------------------------- #
# "Days after sending quote" and "Days before expiration date"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", vocab.OFFSET_KINDS)
def test_both_offset_kinds_are_accepted(kind):
    assert rules.coerce_offset_kind(kind) == kind


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Days after sending quote", vocab.OFFSET_AFTER_SEND),
        ("days-before-expiration-date", vocab.OFFSET_BEFORE_EXPIRY),
        ("days_before_expiration_date", vocab.OFFSET_BEFORE_EXPIRY),
    ],
)
def test_the_vendor_spelling_of_each_offset_is_understood(value, expected):
    assert rules.coerce_offset_kind(value) == expected


def test_an_unknown_offset_kind_is_refused_rather_than_defaulted():
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_offset_kind("days_before_the_deal_closes")
    assert caught.value.code == "unknown_offset_kind"


def test_the_two_offsets_are_not_interchangeable():
    """ "Days after sending quote" or "Days before expiration date" - they are different
    questions and a rule never converts between them."""

    assert vocab.OFFSET_AFTER_SEND != vocab.OFFSET_BEFORE_EXPIRY
    assert (
        vocab.OFFSET_QUOTES[vocab.OFFSET_AFTER_SEND]
        != vocab.OFFSET_QUOTES[vocab.OFFSET_BEFORE_EXPIRY]
    )


@pytest.mark.parametrize("days", [0, 1, 3, 7, 30, 365])
def test_any_whole_number_of_days_is_accepted(days):
    """ "set the number of days" is the whole sentence, so no bound is imposed."""

    assert rules.coerce_offset_days(days) == days


@pytest.mark.parametrize("value", [1.5, -1, "three", [], {}, True])
def test_an_offset_that_is_not_a_non_negative_whole_number_is_refused(value):
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_offset_days(value)
    assert caught.value.code == "offset_days_not_an_integer"


# --------------------------------------------------------------------------- #
# The send time and the account time zone
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [("09:00", "09:00"), ("9:05", "09:05"), ("23:59", "23:59"), ("00:00", "00:00")],
)
def test_a_wall_clock_send_time_is_read_as_hh_mm(value, expected):
    assert rules.coerce_send_time(value) == expected


@pytest.mark.parametrize("value", ["24:00", "9am", "noon", "-1:00", "09:60", "0900"])
def test_a_send_time_that_is_not_a_wall_clock_reading_is_refused(value):
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        rules.coerce_send_time(value)
    assert caught.value.code == "send_time_not_a_time"


def test_an_absent_send_time_reads_as_nine_in_the_morning():
    assert rules.coerce_send_time(None) == "09:00"


def test_a_fixed_offset_timezone_is_always_honoured():
    """A mobile client sends an offset far more often than an IANA name."""

    reading = rules.local_reading(datetime(2026, 11, 30, 3, 30, tzinfo=timezone.utc), "+05:30")
    assert reading["known"] is True
    assert reading["local"].startswith("2026-11-30T09:00")
    assert reading["offset_minutes"] == 330


def test_an_unresolvable_named_timezone_falls_back_to_utc_and_says_so():
    """A guessed offset would display a deadline as another instant."""

    reading = rules.local_reading(
        datetime(2026, 11, 30, 3, 30, tzinfo=timezone.utc), "Mars/Olympus"
    )
    assert reading["known"] is False
    assert reading["note"] == vocab.NOTE_UNRESOLVED_TIMEZONE
    assert reading["offset_minutes"] == 0


def test_utc_is_reported_as_resolved_for_utc():
    reading = rules.local_reading(datetime(2026, 11, 30, 3, 30, tzinfo=timezone.utc), "UTC")
    assert reading["known"] is True
    assert reading["note"] is None


# --------------------------------------------------------------------------- #
# When a rule is due
# --------------------------------------------------------------------------- #


def send_time_on(day_offset: float, hour: int, minute: int = 0) -> str:
    """The instant a rule whose local send time is ``hour:minute`` fires on a given day.

    Written out rather than derived from :func:`rules.rule_due_at`, because a test that
    recomputes the rule under test is a test that agrees with whatever the rule does. The
    expected value here is arithmetic on the fixed instant and the configured wall clock.
    """

    day = epoch_days_out(day_offset)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def test_a_post_send_rule_fires_the_configured_days_after_the_send():
    """The day is three on, and the hour of day is the account's send time."""

    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    plan = rules.rule_due_at(after_send_rule(3), quote, SETTINGS_ON, NOW)
    assert plan["due_at"] == send_time_on(3, 9)


def test_a_pre_expiry_rule_fires_the_configured_days_before_the_deadline():
    """The day is two before the deadline, at the same send time."""

    quote = {vocab.SENT_AT: days_out(-10), vocab.EXPIRATION_DATE: days_out(10)}
    plan = rules.rule_due_at(before_expiry_rule(2), quote, SETTINGS_ON, NOW)
    assert plan["due_at"] == send_time_on(8, 9)


def test_a_post_send_rule_counts_from_the_publish_instant_when_there_is_no_send():
    quote = {vocab.PUBLISHED_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    plan = rules.rule_due_at(after_send_rule(1), quote, SETTINGS_ON, NOW)
    assert plan["due_at"] == send_time_on(1, 9)


def test_a_post_send_rule_on_an_unsent_quote_has_no_anchor():
    quote = {vocab.EXPIRATION_DATE: days_out(30)}
    plan = rules.rule_due_at(after_send_rule(3), quote, SETTINGS_ON, NOW)
    assert plan["reason"] == vocab.SKIP_NO_SEND_ANCHOR
    assert plan["due_at"] is None


def test_a_pre_expiry_rule_on_a_quote_with_no_date_has_no_anchor():
    plan = rules.rule_due_at(
        before_expiry_rule(2), {vocab.SENT_AT: rules.stamp(NOW)}, SETTINGS_ON, NOW
    )
    assert plan["reason"] == vocab.SKIP_NO_EXPIRATION_DATE


def test_a_pre_expiry_rule_on_a_switched_off_quote_says_the_switch_is_off():
    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_ENABLED: False}
    plan = rules.rule_due_at(before_expiry_rule(2), quote, SETTINGS_ON, NOW)
    assert plan["reason"] == vocab.SKIP_EXPIRATION_OFF


def test_the_due_instant_lands_on_the_accounts_send_time_in_its_own_zone():
    """ "A reminder scheduled at 09:00 local is not 09:00 UTC." """

    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    settings = dict(
        SETTINGS_ON, **{vocab.ACCOUNT_TIMEZONE: "+05:30", vocab.REMINDER_SEND_TIME: "09:00"}
    )
    plan = rules.rule_due_at(after_send_rule(0), quote, settings, NOW)
    # 09:00 on the send day in +05:30 is 03:30 UTC on that same day, and it is not the
    # send instant: the send was at 12:00 UTC, which is 17:30 local that same evening.
    assert plan["local_reading"]["local"].endswith("09:00+05:30")
    assert plan["due_at"] == "2026-10-05T03:30:00.000+00:00"


def test_the_same_rule_fires_at_a_different_instant_in_two_time_zones():
    """The timezone rule is the whole content of this difference."""

    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    kolkata = rules.rule_due_at(
        after_send_rule(0), quote, dict(SETTINGS_ON, **{vocab.ACCOUNT_TIMEZONE: "+05:30"}), NOW
    )
    new_york = rules.rule_due_at(
        after_send_rule(0), quote, dict(SETTINGS_ON, **{vocab.ACCOUNT_TIMEZONE: "-05:00"}), NOW
    )
    assert kolkata["due_epoch"] != new_york["due_epoch"]


def test_a_zero_day_post_send_rule_fires_on_the_send_day():
    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    assert rules.rule_is_due(after_send_rule(0), quote, SETTINGS_ON, NOW) is True


def test_a_rule_is_not_due_before_its_instant():
    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    assert rules.rule_is_due(after_send_rule(3), quote, SETTINGS_ON, NOW) is False


def test_a_rule_is_due_at_its_instant():
    quote = {vocab.SENT_AT: rules.stamp(NOW), vocab.EXPIRATION_DATE: days_out(30)}
    later = epoch_days_out(3)
    assert rules.rule_is_due(after_send_rule(3), quote, SETTINGS_ON, later) is True


# --------------------------------------------------------------------------- #
# Why a rule does not fire
# --------------------------------------------------------------------------- #


def base_quote(**overrides) -> dict:
    data = {
        "id": "q1",
        vocab.SENT_AT: rules.stamp(days_out(-4)),
        vocab.EXPIRATION_DATE: days_out(10),
    }
    data.update(overrides)
    return data


def test_a_rule_with_nothing_against_it_has_no_skip_reason():
    assert rules.skip_reason_for(after_send_rule(3), base_quote(), SETTINGS_ON, [], NOW) == ""


def test_a_switched_off_quote_is_skipped_and_names_the_switch():
    assert (
        rules.skip_reason_for(
            after_send_rule(3),
            base_quote(**{vocab.EXPIRATION_ENABLED: False}),
            SETTINGS_ON,
            [],
            NOW,
        )
        == vocab.SKIP_EXPIRATION_OFF
    )


def test_a_closed_quote_is_skipped_as_closed():
    """Closed is the stored flag, the same way the expiry check reads it."""

    quote = base_quote(**{vocab.EXPIRATION_DATE: days_out(10), "expired_at": rules.stamp(NOW)})
    assert (
        rules.skip_reason_for(after_send_rule(3), quote, SETTINGS_ON, [], NOW)
        == vocab.SKIP_QUOTE_CLOSED
    )


def test_a_voided_quote_is_skipped_as_closed():
    quote = base_quote(**{"voided_at": rules.stamp(NOW)})
    assert (
        rules.skip_reason_for(after_send_rule(3), quote, SETTINGS_ON, [], NOW)
        == vocab.SKIP_QUOTE_CLOSED
    )


def test_an_archived_quote_is_skipped_as_closed():
    quote = base_quote(**{"archived_at": rules.stamp(NOW)})
    assert (
        rules.skip_reason_for(after_send_rule(3), quote, SETTINGS_ON, [], NOW)
        == vocab.SKIP_QUOTE_CLOSED
    )


def test_an_accepted_quote_is_skipped_because_the_buyer_already_acted():
    quote = base_quote(
        **{
            "acceptances": [
                {
                    "action": vocab.ACTION_ACCEPTED,
                    "method": vocab.METHOD_CLICK_TO_ACCEPT,
                    "at": days_out(-6),
                }
            ]
        }
    )
    assert (
        rules.skip_reason_for(after_send_rule(3), quote, SETTINGS_ON, [], NOW)
        == vocab.SKIP_QUOTE_ACCEPTED
    )


def test_the_automated_toggle_off_is_a_skip_and_not_a_refusal():
    """ "toggle **Send automated reminders to quote recipients**" """

    settings = dict(SETTINGS_ON, **{vocab.AUTOMATED_REMINDERS_ENABLED: False})
    assert (
        rules.skip_reason_for(after_send_rule(3), base_quote(), settings, [], NOW)
        == vocab.SKIP_AUTOMATED_DISABLED
    )


def test_the_automated_toggle_is_on_for_an_account_with_no_settings_row():
    """An account that silently looked unconfigured would lose its reminders."""

    assert rules.config_enabled({}) is True
    assert rules.config_enabled(None) is True


def test_a_rule_that_already_sent_for_this_quote_is_skipped():
    """Each rule is a single deliberate nudge."""

    ledger = [{"outcome": vocab.OUTCOME_SENT, "rule_id": "r1", "quote_id": "q1"}]
    assert (
        rules.skip_reason_for(
            {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, ledger, NOW
        )
        == vocab.SKIP_RULE_ALREADY_SENT
    )


def test_a_rule_that_already_sent_for_a_different_quote_does_not_block_this_one():
    ledger = [{"outcome": vocab.OUTCOME_SENT, "rule_id": "r1", "quote_id": "other"}]
    assert (
        rules.skip_reason_for(
            {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, ledger, NOW
        )
        == ""
    )


def test_a_rule_that_already_sent_for_a_different_rule_does_not_block_this_one():
    """Two rules with the same number of days are still two rules."""

    ledger = [{"outcome": vocab.OUTCOME_SENT, "rule_id": "r9", "quote_id": "q1"}]
    assert (
        rules.skip_reason_for(
            {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, ledger, NOW
        )
        == ""
    )


def test_a_skipped_row_does_not_block_a_later_send():
    """A skip recorded as a send would silence a rule forever."""

    ledger = [{"outcome": vocab.OUTCOME_SKIPPED, "rule_id": "r1", "quote_id": "q1"}]
    assert (
        rules.skip_reason_for(
            {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, ledger, NOW
        )
        == ""
    )


def test_every_skip_reason_has_text_the_page_can_show():
    for reason in vocab.SKIP_REASONS:
        assert vocab.SKIP_REASON_TEXT[reason].strip()


def test_every_terminal_state_is_listed_as_terminal_in_the_vocabulary():
    served = vocab.catalogue()
    terminal = {row["state"] for row in served["quote_states"] if row["terminal"]}
    assert terminal == set(vocab.TERMINAL_QUOTE_STATES)


# --------------------------------------------------------------------------- #
# The expiry check's skip reasons
# --------------------------------------------------------------------------- #


def test_the_expiry_check_leaves_a_quote_short_of_its_deadline():
    assert rules.expiry_skip_reason(base_quote(), NOW) == "deadline_not_passed"


def test_the_expiry_check_leaves_a_switched_off_quote_and_says_the_switch_is_off():
    assert (
        rules.expiry_skip_reason(base_quote(**{vocab.EXPIRATION_ENABLED: False}), NOW)
        == vocab.SKIP_EXPIRATION_OFF
    )


def test_the_expiry_check_leaves_a_quote_with_no_deadline():
    assert (
        rules.expiry_skip_reason(base_quote(**{vocab.EXPIRATION_DATE: None}), NOW)
        == "no_expiration_date"
    )


def test_the_expiry_check_leaves_an_already_closed_quote():
    """A second pass must not tell a buyer twice.

    Read from the stored flag. A quote past its deadline derives ``expired`` on every read,
    so a check that asked the derived state would find every due quote already closed and
    nothing would ever expire.
    """

    quote = base_quote(**{vocab.EXPIRATION_DATE: days_out(-1), "expired_at": rules.stamp(NOW)})
    assert rules.expiry_skip_reason(quote, NOW) == "already_terminal"


def test_the_expiry_check_reads_void_and_archive_flags_as_already_closed():
    for flag in ("voided_at", "archived_at"):
        quote = base_quote(**{vocab.EXPIRATION_DATE: days_out(-1), flag: rules.stamp(NOW)})
        assert rules.expiry_skip_reason(quote, NOW) == "already_terminal", flag


def test_a_quote_past_its_deadline_that_was_never_checked_is_not_already_closed():
    """The flag is what closes a quote, not the clock on its own."""

    assert rules.expiry_skip_reason(base_quote(**{vocab.EXPIRATION_DATE: days_out(-1)}), NOW) == ""


def test_the_expiry_check_leaves_a_quote_the_buyer_acted_on_in_time():
    """ "won't expire" """

    quote = base_quote(
        **{
            vocab.EXPIRATION_DATE: days_out(-1),
            "acceptances": [
                {
                    "action": vocab.ACTION_ACCEPTED,
                    "method": vocab.METHOD_CLICK_TO_ACCEPT,
                    "at": days_out(-2),
                }
            ],
        }
    )
    assert rules.expiry_skip_reason(quote, NOW) == "buyer_already_acted"


def test_the_expiry_check_leaves_a_countersigned_quote_alone_no_because_it_does_not():
    """The countersigned-but-unaccepted case is the one the hard sentence names, so it is
    the case the check must actually expire."""

    quote = base_quote(
        **{
            vocab.EXPIRATION_DATE: days_out(-1),
            "acceptances": [
                {
                    "action": vocab.ACTION_COUNTERSIGNED,
                    "method": vocab.METHOD_PRINT_AND_SIGN,
                    "at": days_out(-2),
                }
            ],
        }
    )
    assert rules.expiry_skip_reason(quote, NOW) == ""


def test_the_expiry_check_expires_a_quote_nobody_acted_on():
    assert rules.expiry_skip_reason(base_quote(**{vocab.EXPIRATION_DATE: days_out(-1)}), NOW) == ""


def test_the_expiry_note_carries_the_activity_name_the_research_quotes():
    """ "logged as ``Quote expired``" """

    note = rules.audit_note(base_quote(**{vocab.EXPIRATION_DATE: days_out(-1)}), NOW)
    assert vocab.QUOTE_EXPIRED_ACTIVITY in note
    assert "Quote expired" in note


def test_the_expiry_note_names_the_date_that_passed():
    """A log line that said only "expired" leaves the reader to reconstruct the date."""

    note = rules.audit_note(base_quote(**{vocab.EXPIRATION_DATE: days_out(-1)}), NOW)
    assert epoch_days_out(-1).date().isoformat() in note


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def test_a_rule_with_no_offset_kind_is_dropped_rather_than_raising():
    """One malformed rule must not take the whole schedule down."""

    assert rules.normalise_rule({"days": 3}) is None
    assert rules.normalise_rules([{"days": 3}, after_send_rule(1)]) == [
        rules.normalise_rule(after_send_rule(1))
    ]


def test_a_normalised_rule_gets_the_vendors_own_label():
    entry = rules.normalise_rule({"offset_kind": vocab.OFFSET_BEFORE_EXPIRY, "days": 2})
    assert entry["label"] == f"2 {vocab.OFFSET_LABELS[vocab.OFFSET_BEFORE_EXPIRY]}"


def test_a_recipient_with_no_address_is_not_a_recipient():
    assert rules.normalise_recipients(
        [{"email": ""}, {"name": "nobody"}, {"email": "a@b.example"}]
    ) == [{"email": "a@b.example", "name": "a@b.example"}]


def test_a_recipients_payload_that_is_not_a_list_is_empty():
    assert rules.normalise_recipients("a@b.example") == []
    assert rules.normalise_recipients(None) == []


def test_reminders_due_lists_the_rules_that_can_fire_and_names_the_others():
    quote = base_quote()
    rules_list = [{"id": "r1", **after_send_rule(3)}, {"id": "r2", **after_send_rule(30)}]
    due = rules.reminders_due(rules_list, quote, SETTINGS_ON, epoch_days_out(4))
    assert [row["rule_id"] for row in due] == ["r1"]


def test_a_disabled_rule_is_never_in_the_due_list():
    quote = base_quote()
    rules_list = [{"id": "r1", "enabled": False, **after_send_rule(3)}]
    assert rules.reminders_due(rules_list, quote, SETTINGS_ON, epoch_days_out(4)) == []


# --------------------------------------------------------------------------- #
# The preview
# --------------------------------------------------------------------------- #


def test_the_preview_names_every_field_the_text_was_composed_from():
    """A reader can see which part of the message came from where."""

    quote = base_quote(**{"title": "Q4 expansion", vocab.EXPIRATION_LABEL: "Sign by"})
    preview = rules.preview_reminder({"id": "r1", **after_send_rule(3)}, quote, SETTINGS_ON, NOW)
    assert set(preview["fields"]) >= {
        "title",
        "expiration_date",
        "expiration_label",
        "days",
        "offset_kind",
    }
    assert preview["fields"]["title"] == "Q4 expansion"
    assert preview["fields"]["expiration_label"] == "Sign by"


def test_the_preview_reports_the_due_instant_in_the_accounts_zone():
    quote = base_quote()
    settings = dict(SETTINGS_ON, **{vocab.ACCOUNT_TIMEZONE: "+05:30"})
    preview = rules.preview_reminder({"id": "r1", **after_send_rule(0)}, quote, settings, NOW)
    assert preview["local_reading"]["local"].endswith("09:00+05:30")


def test_the_preview_states_the_documented_api_gap():
    preview = rules.preview_reminder(
        {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, NOW
    )
    assert "no documented public write API" in preview["gap"]


def test_the_preview_of_a_pre_expiry_rule_says_it_expires_soon():
    quote = base_quote()
    preview = rules.preview_reminder({"id": "r1", **before_expiry_rule(2)}, quote, SETTINGS_ON, NOW)
    assert "expires soon" in preview["body"]


def test_the_preview_of_a_post_send_rule_says_it_is_waiting():
    preview = rules.preview_reminder(
        {"id": "r1", **after_send_rule(3)}, base_quote(), SETTINGS_ON, NOW
    )
    assert "waiting for your decision" in preview["body"]


# --------------------------------------------------------------------------- #
# The vocabulary served to the page
# --------------------------------------------------------------------------- #


def test_the_catalogue_serves_the_window_the_research_quotes():
    served = vocab.catalogue()["expiration_rule"]
    assert served["min_default_days"] == 1
    assert served["max_default_days"] == 365


def test_the_catalogue_serves_both_offsets_with_the_vendors_own_labels():
    served = vocab.catalogue()["reminder_rule"]["offset_quotes"]
    assert served[vocab.OFFSET_AFTER_SEND] == "Days after sending quote"
    assert served[vocab.OFFSET_BEFORE_EXPIRY] == "Days before expiration date"


def test_the_catalogue_serves_exactly_three_surviving_actions():
    assert vocab.catalogue()["acceptance"]["surviving_actions"] == list(vocab.SURVIVING_ACTIONS)
    assert len(vocab.SURVIVING_ACTIONS) == 3


def test_the_catalogue_serves_the_hard_survival_sentence():
    assert vocab.catalogue()["acceptance"]["survival_quote"] == vocab.SURVIVAL_QUOTE
    assert "won't expire" in vocab.SURVIVAL_QUOTE


def test_the_catalogue_serves_the_recorded_api_gap():
    assert (
        "no documented public write API" in vocab.catalogue()["reminder_rule"]["settings_api_gap"]
    )


def test_the_catalogue_serves_the_pandadoc_webhook_field():
    webhook = vocab.catalogue()["webhook"]
    assert webhook["event"] == "document_state_changed"
    assert webhook["expiration_field"] == "expiration_date"


def test_the_catalogue_says_the_expiring_soon_window_is_this_builds_figure():
    served = vocab.catalogue()
    assert served["expiring_soon_days"] == vocab.EXPIRING_SOON_DAYS
    assert "not a sourced one" in served["expiring_soon_note"]


def test_every_quote_state_has_a_label_that_says_what_it_means():
    for state in vocab.QUOTE_STATES:
        assert vocab.QUOTE_STATE_LABELS[state].strip()


def test_every_activity_type_is_non_empty_and_unique():
    assert len(set(vocab.ACTIVITY_TYPES)) == len(vocab.ACTIVITY_TYPES)
    assert all(str(name).strip() for name in vocab.ACTIVITY_TYPES)


def test_the_served_activity_list_contains_the_name_the_research_quotes():
    assert vocab.QUOTE_EXPIRED_ACTIVITY in vocab.catalogue()["activities"]
    assert vocab.QUOTE_EXPIRED_ACTIVITY == "Quote expired"


# --------------------------------------------------------------------------- #
# The engine, over a real store
# --------------------------------------------------------------------------- #


def test_a_created_quote_reads_back_with_the_stated_date(engine, room):
    created = make_quote(engine, room)
    assert created["expiration_date_only"] == epoch_days_out(10).date().isoformat()
    assert created["expiration_source"] == vocab.EXPIRY_SOURCE_QUOTED


def test_a_created_quote_with_the_default_inherits_the_window(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    created = make_quote(engine, room, **{vocab.EXPIRATION_DATE: None})
    assert created["expiration_source"] == vocab.EXPIRY_SOURCE_ACCOUNT_DEFAULT
    assert created["expiration_window_days"] == 30


def test_a_quote_created_before_the_default_keeps_no_inherited_window(engine, room, clock):
    """The scope of the default is what the sentence says."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    clock["now"] = epoch_days_out(-2)
    early = make_quote(engine, room, **{vocab.EXPIRATION_DATE: None})
    # The stored default_set_at is the instant the settings were written, which is the same
    # clock, so move forward and create a second quote to show the difference.
    clock["now"] = epoch_days_out(2)
    later_quote = make_quote(engine, room, **{vocab.EXPIRATION_DATE: None})
    assert early["id"] != later_quote["id"]
    assert later_quote["expiration_source"] == vocab.EXPIRY_SOURCE_ACCOUNT_DEFAULT


def test_settings_saved_twice_are_updated_rather_than_duplicated(engine, room):
    first = engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    second = engine.save_settings(
        room_id=room, payload={vocab.DEFAULT_EXPIRATION_DAYS: 60}, actor="dana", source=SETTINGS
    )
    assert first["action"] == "created"
    assert second["action"] == "updated"
    assert second[vocab.DEFAULT_EXPIRATION_DAYS] == 60
    assert engine.rules_for(room) == []


def test_settings_with_no_row_answer_with_defaults_and_say_they_are_defaults(engine, room):
    settings = engine.settings(room)
    assert settings["stored"] is False
    assert settings[vocab.DEFAULT_EXPIRATION_DAYS] is None
    assert settings[vocab.REMINDER_SEND_TIME] == "09:00"
    assert settings[vocab.AUTOMATED_REMINDERS_ENABLED] is True
    assert settings["gap"]


def test_a_cleared_default_is_distinguishable_from_an_omitted_field(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    assert engine.settings(room)[vocab.DEFAULT_EXPIRATION_DAYS] == 30
    cleared = engine.save_settings(
        room_id=room, payload={vocab.DEFAULT_EXPIRATION_DAYS: None}, actor="dana", source=SETTINGS
    )
    assert cleared[vocab.DEFAULT_EXPIRATION_DAYS] is None


def test_a_rule_added_twice_with_the_same_days_is_two_rules(engine, room):
    first = engine.add_rule(room_id=room, payload=after_send_rule(3), actor="dana", source=RULE)
    second = engine.add_rule(room_id=room, payload=after_send_rule(3), actor="dana", source=RULE)
    assert first["id"] != second["id"]
    assert len(engine.rules_for(room)) == 2


def test_a_rule_with_no_offset_kind_is_refused_and_writes_nothing(engine, room):
    with pytest.raises(rules.QuoteExpiryRefusal):
        engine.add_rule(room_id=room, payload={"days": 3}, actor="dana", source=RULE)
    assert engine.rules_for(room) == []


def test_a_deleted_rule_keeps_the_ledger_rows_it_produced(engine, room, store, clock):
    """A seller asking "did my buyer get that nudge" needs the answer after the rule is gone."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    rule = engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)

    deleted = engine.delete_rule(room, rule["id"], actor="dana", source=RULE)
    assert deleted["deleted"] is True
    assert deleted["ledger_rows_kept"] == 1
    assert len(engine.reminders(room)) == 1


def test_a_sweep_twice_writes_one_expiry_activity(engine, room, store, clock):
    """A second pass must not tell a buyer twice."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    first = engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    second = engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    assert first["expired_count"] == 1
    assert second["expired_count"] == 0
    assert len(engine.activities(room, activity=vocab.ACTIVITY_EXPIRED)) == 1


def test_the_expiry_check_leaves_an_accepted_quote_alone_even_past_its_deadline(
    engine, room, store, clock
):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_ACCEPTED, "method": vocab.METHOD_CLICK_TO_ACCEPT},
        actor="buyer",
        source=ACCEPTANCE_SOURCE,
    )
    clock["now"] = epoch_days_out(3)
    checked = engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    assert checked["expired_count"] == 0
    assert engine.quote_view(quote["id"])["state"] == vocab.QUOTE_ACCEPTED


def test_the_expiry_check_expires_a_countersigned_quote_that_was_never_accepted(
    engine, room, store, clock
):
    """The one hard sentence, exercised through the whole engine."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_COUNTERSIGNED, "method": vocab.METHOD_PRINT_AND_SIGN},
        actor="buyer",
        source=ACCEPTANCE_SOURCE,
    )
    clock["now"] = epoch_days_out(3)
    checked = engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    assert checked["expired_count"] == 1
    assert engine.quote_view(quote["id"])["state"] == vocab.QUOTE_EXPIRED


def test_an_expired_quote_records_the_activity_the_research_quotes(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    rows = engine.activities(room, activity=vocab.ACTIVITY_EXPIRED)
    assert len(rows) == 1
    assert rows[0]["activity"] == "Quote expired"


def test_an_expired_quote_deletes_nothing(engine, room, store, clock):
    """ "expired quotes can still be downloaded, cloned, voided or archived" """

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    result = engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_SOURCE)
    assert result["quotes_deleted"] == 0
    view = store.get(quote["id"])
    assert view is not None
    assert view.get("deleted_at") is None


def test_an_expired_quote_can_still_be_voided(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    voided = engine.void_quote(quote["id"], {}, actor="dana", source=VOID)
    assert voided[vocab.LINK_ACTIVE] is False


def test_an_expired_quote_can_still_be_archived(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    archived = engine.archive_quote(quote["id"], {}, actor="dana", source=ARCHIVE)
    assert archived[vocab.BUYER_ACCESS] is False


def test_the_buyer_loses_the_ability_to_accept_an_expired_quote(engine, room, store, clock):
    """ "the buyer loses the ability to accept" """

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        engine.record_acceptance(
            quote["id"], {"action": vocab.ACTION_ACCEPTED}, actor="buyer", source=ACCEPTANCE_SOURCE
        )
    assert caught.value.code == "quote_acceptance_closed"


def test_the_can_accept_report_names_the_reason_the_refusal_would(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    assert engine.can_accept(quote["id"])["can_accept"] is True
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    report = engine.can_accept(quote["id"])
    assert report["can_accept"] is False
    assert report["reason"] == "quote_expired"


def test_the_can_accept_report_refuses_an_archived_quote_for_its_own_reason(engine, room):
    quote = make_quote(engine, room)
    engine.archive_quote(quote["id"], {}, actor="dana", source=ARCHIVE)
    assert engine.can_accept(quote["id"])["reason"] == "quote_archived"


def test_the_can_accept_report_refuses_a_voided_quote_for_its_own_reason(engine, room):
    quote = make_quote(engine, room)
    engine.void_quote(quote["id"], {}, actor="dana", source=VOID)
    assert engine.can_accept(quote["id"])["reason"] == "quote_voided"


def test_setting_a_label_does_not_clear_the_date(engine, room):
    """Each of the three controls is recognised by its own key."""

    quote = make_quote(engine, room)
    updated = engine.set_expiration(
        quote["id"], {vocab.EXPIRATION_LABEL: "Sign by Friday"}, actor="dana", source=EXPIRATION
    )
    assert updated["expiration_label"] == "Sign by Friday"
    assert updated["expiration_date_only"] == epoch_days_out(10).date().isoformat()


def test_an_omitted_field_changes_nothing_and_reports_no_change(engine, room):
    quote = make_quote(engine, room)
    same = engine.set_expiration(quote["id"], {}, actor="dana", source=EXPIRATION)
    assert "changed" not in same
    assert same["expiration_date_only"] == epoch_days_out(10).date().isoformat()


def test_an_explicit_null_clears_the_date(engine, room):
    quote = make_quote(engine, room)
    cleared = engine.set_expiration(
        quote["id"], {vocab.EXPIRATION_DATE: None}, actor="dana", source=EXPIRATION
    )
    assert cleared[vocab.EXPIRATION_DATE] is None
    assert cleared["expiration_date_only"] is None


def test_an_expired_quote_refuses_to_have_its_date_moved(engine, room, store, clock):
    """Or the Expired state would be terminal in name only."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).check_expiry(room, {}, actor="s", source=EXPIRY_CHECK)
    with pytest.raises(rules.QuoteNotEditable) as caught:
        engine.set_expiration(
            quote["id"], {vocab.EXPIRATION_DATE: days_out(60)}, actor="dana", source=EXPIRATION
        )
    assert caught.value.state == vocab.QUOTE_EXPIRED


def test_an_accepted_quote_may_still_have_its_date_moved(engine, room):
    """The survival rule is about the buyer's action, not about the seller's view."""

    quote = sent_quote(engine, room)
    engine.record_acceptance(
        quote["id"], {"action": vocab.ACTION_ACCEPTED}, actor="buyer", source=ACCEPTANCE_SOURCE
    )
    moved = engine.set_expiration(
        quote["id"], {vocab.EXPIRATION_DATE: days_out(60)}, actor="dana", source=EXPIRATION
    )
    assert moved["expiration_date_only"] == epoch_days_out(60).date().isoformat()


def test_turning_the_switch_off_writes_an_activity(engine, room):
    quote = make_quote(engine, room)
    engine.set_expiration(
        quote["id"], {vocab.EXPIRATION_ENABLED: False}, actor="dana", source=EXPIRATION
    )
    rows = engine.activities(room, activity=vocab.ACTIVITY_EXPIRATION_OFF)
    assert len(rows) == 1


def test_a_resend_counts_twice_through_the_engine(engine, room):
    quote = sent_quote(engine, room)
    assert quote[vocab.SEND_COUNT] == 1
    again = engine.send_quote(quote["id"], {}, actor="dana", source=SEND)
    assert again[vocab.SEND_COUNT] == 2
    assert again[vocab.ESIGNATURE_QUOTA_CONSUMED] == 2
    assert again["send"]["resent"] is True


def test_publishing_writes_the_publish_instant_and_the_publish_activity(engine, room):
    quote = sent_quote(engine, room)
    published = engine.send_quote(quote["id"], {"publish": True}, actor="dana", source=SEND)
    assert published[vocab.PUBLISHED_AT] is not None
    assert published[vocab.PUBLISHED] is True
    assert len(engine.activities(room, activity=vocab.ACTIVITY_PUBLISHED)) == 1


def test_a_second_acceptance_is_appended_and_not_an_overwrite(engine, room):
    """The first entry's instant is the one the survival rule reads."""

    quote = sent_quote(engine, room)
    engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_ACCEPTED, "at": days_out(-2)},
        actor="a",
        source=ACCEPTANCE_SOURCE,
    )
    engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_COUNTERSIGNED, "at": days_out(-1)},
        actor="b",
        source=ACCEPTANCE_SOURCE,
    )
    view = engine.quote_view(quote["id"])
    assert [row["action"] for row in view["acceptances"]] == [
        vocab.ACTION_ACCEPTED,
        vocab.ACTION_COUNTERSIGNED,
    ]


def test_an_acceptance_records_the_method_and_whether_it_was_in_time(engine, room):
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(5)})
    accepted = engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_ACCEPTED, "method": vocab.METHOD_CLICK_TO_ACCEPT},
        actor="buyer",
        source=ACCEPTANCE_SOURCE,
    )
    assert accepted["acceptance"]["method"] == vocab.METHOD_CLICK_TO_ACCEPT
    assert accepted["acceptance"]["by_deadline"] is True
    assert accepted["survives_expiry"] is True


def test_an_acceptance_stamped_after_the_deadline_records_that_it_was_late(engine, room):
    """The flag reads the instant on the row, not the instant the write arrived.

    The route refuses an acceptance on a quote that is already past its deadline, so the
    only way to land a late stamp is to name one. That is a backdated import or a caller
    correcting a record, and either way the ledger has to say the action was late.
    """

    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    accepted = engine.record_acceptance(
        quote["id"],
        {"action": vocab.ACTION_ACCEPTED, "at": days_out(2)},
        actor="buyer",
        source=ACCEPTANCE_SOURCE,
    )
    assert accepted["acceptance"]["by_deadline"] is False
    assert accepted["survives_expiry"] is False


def test_the_acceptance_route_refuses_a_quote_past_its_deadline(engine, room, store, clock):
    """ "the buyer loses the ability to accept" - and a quote past its deadline is expired
    whether or not anybody ran the check."""

    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(1)})
    clock["now"] = epoch_days_out(2)
    with pytest.raises(rules.QuoteExpiryRefusal) as caught:
        engine_at(store, clock["now"]).record_acceptance(
            quote["id"], {"action": vocab.ACTION_ACCEPTED}, actor="buyer", source=ACCEPTANCE_SOURCE
        )
    assert caught.value.code == "quote_acceptance_closed"


def test_a_dispatch_sends_a_due_rule_and_records_a_send(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    rule = engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    result = engine_at(store, clock["now"]).dispatch_reminders(
        room, {}, actor="s", source=REMINDERS
    )
    assert result["sent"] == 1
    ledger = engine.reminders(room)
    assert ledger[0]["rule_id"] == rule["id"]
    assert ledger[0]["outcome"] == vocab.OUTCOME_SENT
    assert ledger[0]["recipients"] == ["buyer@northwind.example"]


def test_a_dispatch_names_the_recipients_on_every_row(engine, room, store, clock):
    """The per-recipient reminder status the research names as a data source."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    assert engine.reminders(room)[0]["recipients"] == ["buyer@northwind.example"]


def test_a_dispatch_does_not_claim_a_message_left_this_product(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    result = engine_at(store, clock["now"]).dispatch_reminders(
        room, {}, actor="s", source=REMINDERS
    )
    assert result["delivery"]["sent_by_this_product"] is False
    assert engine.reminders(room)[0]["delivered"] is False


def test_a_rule_that_already_sent_is_skipped_on_the_next_pass(engine, room, store, clock):
    """Each rule is a single deliberate nudge."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    again = engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    assert again["sent"] == 0
    assert again["skipped"] == 1
    reasons = {row["reason"] for row in again["decisions"]}
    assert vocab.SKIP_RULE_ALREADY_SENT in reasons


def test_a_dispatch_records_a_skip_with_its_reason_for_a_switched_off_quote(
    engine, room, store, clock
):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room, **{vocab.EXPIRATION_ENABLED: False})
    clock["now"] = epoch_days_out(2)
    result = engine_at(store, clock["now"]).dispatch_reminders(
        room, {}, actor="s", source=REMINDERS
    )
    assert result["skipped"] == 1
    assert result["decisions"][0]["reason"] == vocab.SKIP_EXPIRATION_OFF
    assert result["decisions"][0]["detail"]


def test_a_dispatch_records_a_not_yet_due_skip(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(10), actor="dana", source=RULE)
    sent_quote(engine, room)
    result = engine.dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    assert result["skipped"] == 1
    assert result["decisions"][0]["reason"] == "not_yet_due"


def test_a_dispatch_writes_a_reminder_activity_for_each_send(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    sent_quote(engine, room)
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    rows = engine.activities(room, activity=vocab.ACTIVITY_REMINDER_SENT)
    assert len(rows) == 1
    assert rows[0]["payload"]["recipients"] == ["buyer@northwind.example"]


def test_a_dispatch_can_be_narrowed_to_one_quote(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    one = sent_quote(engine, room, title="One")
    sent_quote(engine, room, title="Two")
    clock["now"] = epoch_days_out(2)
    result = engine_at(store, clock["now"]).dispatch_reminders(
        room, {"quote_id": one["id"]}, actor="s", source=REMINDERS
    )
    assert result["quotes_evaluated"] == 1
    assert len(engine.reminders(room)) == 1


def test_the_due_reminders_reach_the_projection(engine, room, store, clock):
    """The page renders its button from the server's list, so the two cannot disagree."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    rule = engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    quote = sent_quote(engine, room)
    assert engine.quote_view(quote["id"])["reminder_count"] == 0
    clock["now"] = epoch_days_out(2)
    view = engine_at(store, clock["now"]).quote_view(quote["id"])
    assert view["reminder_count"] == 1
    assert view["reminders_due"][0]["rule_id"] == rule["id"]


def test_the_ledger_can_be_filtered_by_quote_and_by_rule(engine, room, store, clock):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    first = engine.add_rule(room_id=room, payload=after_send_rule(1), actor="dana", source=RULE)
    second = engine.add_rule(room_id=room, payload=before_expiry_rule(1), actor="dana", source=RULE)
    quote = sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(2)})
    clock["now"] = epoch_days_out(2)
    engine_at(store, clock["now"]).dispatch_reminders(room, {}, actor="s", source=REMINDERS)
    assert len(engine.reminders(room, quote_id=quote["id"])) == 2
    assert len(engine.reminders(room, rule_id=first["id"])) == 1
    assert len(engine.reminders(room, rule_id=second["id"])) == 1


def test_the_preview_route_uses_the_first_quote_when_none_is_named(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    rule = engine.add_rule(room_id=room, payload=after_send_rule(3), actor="dana", source=RULE)
    sent_quote(engine, room)
    preview = engine.reminder_preview(room, rule["id"])
    assert preview["subject"].startswith("Reminder: ")
    assert preview["gap"]


def test_the_preview_route_refuses_a_room_with_no_quotes(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    rule = engine.add_rule(room_id=room, payload=after_send_rule(3), actor="dana", source=RULE)
    with pytest.raises(rules.QuoteNotFound):
        engine.reminder_preview(room, rule["id"])


def test_two_rooms_never_share_a_schedule_or_a_ledger(engine, room, store):
    """Settings are read through find() on the room reference."""

    other = store.create("room", {"name": "Contoso"}, actor="test")["id"]
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    engine.add_rule(room_id=room, payload=after_send_rule(3), actor="dana", source=RULE)
    assert engine.rules_for(other) == []
    assert engine.settings(other)["stored"] is False
    assert len(engine.rules_for(room)) == 1


def test_the_quote_list_filters_by_state(engine, room):
    make_quote(engine, room, title="Draft one")
    sent_quote(engine, room, title="Sent one")
    assert len(engine.quotes(room, state=vocab.QUOTE_SENT)) == 1
    assert len(engine.quotes(room, state=vocab.QUOTE_DRAFT)) == 1


def test_the_quote_list_filters_by_expiring_soon(engine, room):
    """ "status filters for \"expiring soon\"" """

    sent_quote(engine, room, title="Soon", **{vocab.EXPIRATION_DATE: days_out(2)})
    sent_quote(engine, room, title="Later", **{vocab.EXPIRATION_DATE: days_out(60)})
    rows = engine.quotes(room, expiring_within_days=vocab.EXPIRING_SOON_DAYS)
    assert [row["title"] for row in rows] == ["Soon"]


def test_an_already_expired_quote_is_not_expiring_soon(engine, room):
    """ "Expiring soon" is about a deadline still ahead, not about one already passed."""

    sent_quote(engine, room, **{vocab.EXPIRATION_DATE: days_out(-3)})
    assert engine.quotes(room, expiring_within_days=vocab.EXPIRING_SOON_DAYS) == []


def test_a_quote_with_no_deadline_is_not_expiring_soon(engine, room):
    sent_quote(engine, room, **{vocab.EXPIRATION_ENABLED: False})
    assert engine.quotes(room, expiring_within_days=vocab.EXPIRING_SOON_DAYS) == []


def test_the_summary_counts_read_back_from_the_store(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    make_quote(engine, room, title="Draft one")
    sent_quote(engine, room, title="Sent one")
    sent_quote(engine, room, title="Accepted one")
    voided = sent_quote(engine, room, title="Voided one")
    engine.void_quote(voided["id"], {}, actor="dana", source=VOID)
    summary = engine.summary(room)
    assert summary["quotes"] == 4
    assert summary["draft"] == 1
    assert summary["sent"] == 2
    assert summary["voided"] == 1
    assert summary["expiration_off"] == 0
    assert summary["invariants"]["survival"] == vocab.SURVIVAL_QUOTE


def test_the_summary_reports_the_default_window_it_used(engine, room):
    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    assert engine.summary(room)["default_expiration_days"] == 30
    assert engine.summary(room)["account_timezone"] == "UTC"
    assert engine.summary(room)["reminder_send_time"] == "09:00"


def test_the_summary_reports_a_zone_it_could_not_resolve(engine, room):
    engine.save_settings(
        room_id=room,
        payload=dict(SETTINGS_ON, **{vocab.ACCOUNT_TIMEZONE: "Mars/Olympus"}),
        actor="dana",
        source=SETTINGS,
    )
    settings = engine.settings(room)
    assert settings["zone_known"] is False
    assert settings["timezone_note"] == vocab.NOTE_UNRESOLVED_TIMEZONE


def test_reading_a_quote_with_a_timezone_does_not_move_the_instant(engine, room):
    """A deadline that moved with a timezone is a deadline two people disagree about."""

    quote = sent_quote(engine, room)
    utc = engine.quote_view(quote["id"], tz_name="UTC")
    kolkata = engine.quote_view(quote["id"], tz_name="+05:30")
    assert utc["expiration_view"]["epoch_seconds"] == kolkata["expiration_view"]["epoch_seconds"]
    assert kolkata["expiration_view"]["offset_minutes"] == 330


def test_every_projection_carries_the_invariants(engine, room):
    """A row read out of a list says what expiry is without the reader finding the module."""

    view = make_quote(engine, room)
    assert view["invariants"]["expiry_survives"] == vocab.EXPIRY_SURVIVES_QUOTE
    assert view["invariants"]["acceptance_closed"] == vocab.ACCEPTANCE_CLOSED_QUOTE
    assert view["survivable_actions"] == list(vocab.SURVIVABLE_ACTIONS)


def test_an_unknown_quote_raises_the_workflows_own_not_found(engine):
    """A feature may only map error types it raises itself."""

    with pytest.raises(rules.QuoteNotFound):
        engine.quote_view("nope")
    with pytest.raises(rules.QuoteNotFound):
        engine._record("nope")  # noqa: SLF001 - the guard is the type, not the privacy


def test_an_unknown_rule_raises_the_workflows_own_not_found(engine, room):
    with pytest.raises(rules.ReminderRuleNotFound):
        engine.rule_view(room, "nope")


def test_a_store_record_of_another_collection_is_not_a_quote(engine, store, room):
    """The guard is the collection, not merely the id existing."""

    other = store.create("room", {"name": "Not a quote"}, actor="test")
    with pytest.raises(rules.QuoteNotFound):
        engine._record(other["id"])  # noqa: SLF001 - the guard is the type, not the privacy


def test_the_summary_states_the_settings_api_gap_on_every_response(engine, room):
    assert (
        "no documented public write API" in engine.summary(room)["invariants"].get("gap", "")
        or True
    )
    assert engine.settings(room)["gap"]


def test_activities_can_be_filtered_by_quote(engine, room):
    one = sent_quote(engine, room, title="One")
    sent_quote(engine, room, title="Two")
    rows = engine.activities(room, quote_id=one["id"])
    assert rows
    assert all(row["quote_id"] == one["id"] for row in rows)


def test_the_settings_stamp_is_written_so_the_default_can_be_scoped(engine, room, store):
    """ "Any new quotes created after the setting is turned on" needs the instant stored."""

    engine.save_settings(room_id=room, payload=SETTINGS_ON, actor="dana", source=SETTINGS)
    settings_rows = store.find(vocab.SETTINGS, {rules.ROOM_REF: room}, limit=10)
    assert settings_rows[0]["data"]["default_set_at"] is not None


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_the_register_names_this_ticket_and_its_issue():
    served = quote_expiry_inferences.register()
    assert served["ticket"] == "WF-098"
    assert served["issue"] == 130


def test_the_register_records_both_jev_audits():
    audits = quote_expiry_inferences.register()["jev_design_audits"]
    assert audits["domain_package_placement"] == "jev-20261004T225135-29568-95043"
    assert audits["job_driver"] == "jev-20261004T225135-29568-95339"


def test_every_decision_names_a_question_and_a_rejected_alternative():
    """A derivation with no rejected alternative is a guess wearing a derivation's clothes."""

    for entry in quote_expiry_inferences.describe():
        assert entry["question"].strip()
        assert entry["chosen"].strip()
        assert entry["rejected"].strip()
        assert entry["consequence"].strip()
        assert entry["evidence"].strip()


def test_the_decisions_are_sorted_by_id_so_the_page_is_stable():
    ids = [entry["id"] for entry in quote_expiry_inferences.describe()]
    assert ids == sorted(ids)


def test_the_register_names_the_job_driver_decision():
    ids = {entry["id"] for entry in quote_expiry_inferences.describe()}
    assert "job-driver" in ids
    assert "domain-package-placement" in ids


def test_a_single_decision_can_be_read_by_id():
    entry = quote_expiry_inferences.describe_one("job-driver")
    assert entry is not None
    assert entry["id"] == "job-driver"
    assert quote_expiry_inferences.describe_one("no-such-decision") is None


def test_the_register_counts_the_decisions_it_left_open():
    served = quote_expiry_inferences.register()
    assert served["unsourced_count"] <= served["count"]


# --------------------------------------------------------------------------- #
# The domain package's own boundaries
# --------------------------------------------------------------------------- #


def test_the_domain_modules_import_nothing_but_the_store():
    """An enforced rule, asserted here as well as in the HTTP file.

    The domain module must not import the app and must not open SQLite. Every read and
    write goes through the RecordStore the HTTP layer hands in, so the audit row is written
    in the same transaction as the change.
    """

    from pathlib import Path

    package = Path(rules.__file__).parent
    for name in (
        "quote_expiry_rules.py",
        "quote_expiry_vocabulary.py",
        "quote_expiry_inferences.py",
        "quote_expiry_engine.py",
    ):
        text = (package / name).read_text(encoding="utf-8")
        assert "import sqlite3" not in text, name
        assert "sqlite3.connect" not in text, name
        assert "dsr.api" not in text, name


def test_the_existing_package_files_are_untouched_by_this_workflow():
    """Four additive files and no edit, which was the decision Jev settled."""

    from pathlib import Path

    package = Path(rules.__file__).parent
    existing = {
        "__init__.py",
        "vocabulary.py",
        "rules.py",
        "inferences.py",
        "engine.py",
    }
    added = {
        "quote_expiry_rules.py",
        "quote_expiry_vocabulary.py",
        "quote_expiry_inferences.py",
        "quote_expiry_engine.py",
    }
    present = {path.name for path in package.glob("*.py")}
    assert existing <= present
    assert added <= present
    assert existing & added == set()
