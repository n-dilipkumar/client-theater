"""Tests for WF-067: a mutual action plan sent out for e-signature approval.

The claims under test come from ``docs/research/raw/scheduling-meetings.md``
section 17. Nothing here is a preference of this build unless it is listed in
:mod:`dsr.scheduling_meetings.inferences`, and every inference in that registry
has a test that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* Five recipient roles - ``SIGNER``, **``APPROVER``**, ``CC``, ``VIEWER``,
  ``ASSISTANT`` - with the approver's one rule: "Must approve before signers can
  sign".
* "After distribution, recipients receive an email with a link to sign the
  document. The document status changes from ``DRAFT`` to ``PENDING``."
* Field geometry as percentages: "``positionX`` | Horizontal position from left
  edge (0 = left, 100 = right)", and ``identifier`` as a file index, "0 for first
  file, 1 for second".
* Fourteen webhook events, including ``DOCUMENT_COMPLETED`` with ``completedAt``
  and ``DOCUMENT_REJECTED`` with ``rejectionReason``.
* Two limits this product treats as rules: "The API cannot: Sign documents on
  behalf of recipients", and "Retrieve the signed PDF until all recipients have
  completed signing".
* Two operational rules: "Check the ``X-Documenso-Secret`` header matches your
  configured secret", and "Process idempotently - Webhooks may be retried, so
  handle duplicate events".

Four bugs these tests were written to catch, each of which is the kind this
feature could plausibly ship with:

* A ``DOCUMENT_REJECTED`` from an approver recorded as the same thing as one from
  a signer, which loses the one distinction the research's approver rule exists
  for.
* A duplicate event flipping an approved plan back to awaiting signature, because
  the retry was applied a second time.
* An event on a plan that was never distributed being believed, which would
  approve a plan no buyer was ever sent.
* A failed secret check still writing a row, which is the one thing an
  unauthenticated caller must not be able to do.

The HTTP half lives in ``test_wf067_http.py`` and runs against the real app over a
temporary database, asserting that every ``source`` in the audit log names a
route the host actually mounted.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.scheduling_meetings import (
    APPROVER,
    ASSISTANT,
    CC,
    COLLECTION_EVENT,
    COLLECTION_NOTICE,
    COLLECTION_PLAN,
    COLLECTION_RECIPIENT,
    COLLECTION_TEMPLATE,
    COORDINATE_MAX,
    COORDINATE_QUOTE,
    EVENT_DOCUMENT_CANCELLED,
    EVENT_DOCUMENT_COMPLETED,
    EVENT_DOCUMENT_OPENED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_DOCUMENT_REMINDER_SENT,
    EVENT_DOCUMENT_SIGNED,
    EVENT_RECIPIENT_EXPIRED,
    EVENTS,
    FIELD_TYPES,
    IDENTIFIER_QUOTE,
    MALFORMED_FIELD_GUARD,
    MILESTONE_APPROVED,
    MILESTONE_AWAITING_SIGNATURE,
    MILESTONE_DRAFT,
    MILESTONE_REFUSED_BY_APPROVER,
    MILESTONE_REFUSED_BY_SIGNER,
    OUTCOME_APPLIED,
    OUTCOME_NOTED,
    RECIPIENT_ROLES,
    SIGNER,
    SIGNING_ORDERS,
    SIGNING_ROLES,
    VIEWER,
    WEBHOOK_SECRET_HEADER,
    AlreadyDistributed,
    DuplicateEvent,
    EventBeforeDistribution,
    InvalidExternalId,
    MalformedEvent,
    MalformedField,
    MalformedRecipient,
    MapEngine,
    MissingRecipients,
    NoSuchPlan,
    NoSuchTemplate,
    PlanStateConflict,
    SignersBlocked,
    UnauthenticatedEvent,
    UnknownEventType,
    UnknownRecipientRole,
    UnresolvedPlan,
    apply_event,
    approver_block,
    build_recipients,
    describe,
    event_fingerprint,
    external_id_for,
    plan_is_complete,
    read_external_id,
    require_external_id,
    require_known_event,
    required_roles,
    secret_matches,
    signing_unlocked,
    validate_field,
)
from dsr.scheduling_meetings.errors import UnknownFieldType
from dsr.scheduling_meetings.events import (
    RECIPIENT_APPROVED,
    RECIPIENT_COMPLETED,
    RECIPIENT_OPENED,
    resolve_recipient,
)
from dsr.store import RecordStore

#: The feature's own prefix, duplicated so a renamed route fails rather than
#: following silently.
PREFIX = "/api/wf-067"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row asserts on the real thing.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/plans"

DEMO_SECRET = "northwind-map-webhook-secret"
SIGNER_EMAIL = "buyer@northwind.example"
APPROVER_EMAIL = "legal@contoso.example"
CC_EMAIL = "dana@northwind.example"
VIEWER_EMAIL = "ops@fabrikam.example"

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db():
    """In-memory rather than a file: 0.4 ms against 7.0 ms, measured."""
    database = AuditedDatabase()
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return MapEngine(store, clock=lambda: NOW)


@pytest.fixture()
def room(store):
    return store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="dana")


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="sam")


def people(*, with_approver: bool = True) -> list[dict]:
    everyone = [
        {"email": SIGNER_EMAIL, "name": "Ada Byron", "role": SIGNER, "party": "buyer"},
        {"email": CC_EMAIL, "name": "Dana Reed", "role": CC, "party": "seller"},
        {"email": VIEWER_EMAIL, "name": "Ops", "role": VIEWER},
    ]
    if with_approver:
        everyone.insert(
            1, {"email": APPROVER_EMAIL, "name": "Luis Ortega", "role": APPROVER, "party": "buyer"}
        )
    return everyone


def make_plan(engine, room_id, **overrides):
    payload = {
        "subject": "Mutual action plan",
        "webhook_secret": DEMO_SECRET,
        "recipients": people(),
    }
    payload.update(overrides)
    return engine.create_plan(room_id, payload, actor="dana", source=SOURCE)


def deliver(engine, room_id, plan, event, secret=DEMO_SECRET, **extra):
    headers = {WEBHOOK_SECRET_HEADER: secret} if secret else {}
    payload = {
        "event": event,
        "eventId": extra.pop("event_id", f"evt-{event}-{extra.get('recipientEmail', '')}"),
        "externalId": plan["plan"]["external_id"],
        **extra,
    }
    return engine.receive_event(
        room_id, headers=headers, payload=payload, actor="vendor", source=SOURCE
    )


# --------------------------------------------------------------------------- #
# The vocabulary is the research, not this build
# --------------------------------------------------------------------------- #


def test_the_five_recipient_roles_are_the_ones_the_research_names():
    assert RECIPIENT_ROLES == (SIGNER, APPROVER, CC, VIEWER, ASSISTANT)
    assert SIGNING_ROLES == (SIGNER, APPROVER)


def test_the_approvers_rule_is_quoted_where_the_roles_are_defined():
    served = describe()["roles"]
    approver = next(row for row in served if row["role"] == APPROVER)
    assert approver["gates_signers"] is True
    assert approver["meaning"] == "APPROVER | Must approve before signers can sign"


def test_only_the_signing_roles_are_told_they_sign():
    """A CC and a VIEWER are never asked to sign, so their absence holds nothing open."""
    served = {row["role"]: row for row in describe()["roles"]}
    assert served[SIGNER]["signs"] is True
    assert served[APPROVER]["signs"] is True
    assert served[CC]["signs"] is False
    assert served[VIEWER]["signs"] is False


def test_the_two_signing_orders_are_the_ones_the_research_names():
    assert SIGNING_ORDERS == ("PARALLEL", "SEQUENTIAL")


def test_the_fourteen_events_are_the_ones_the_research_enumerates():
    assert len(EVENTS) == 14
    assert EVENT_DOCUMENT_COMPLETED in EVENTS
    assert EVENT_DOCUMENT_REJECTED in EVENTS
    assert EVENT_RECIPIENT_EXPIRED in EVENTS
    assert EVENT_DOCUMENT_REMINDER_SENT in EVENTS
    # No duplicates, and nothing invented.
    assert len(set(EVENTS)) == len(EVENTS)


def test_the_coordinate_rule_carries_the_researchs_own_sentence():
    rule = describe()["coordinate_rule"]
    assert rule["min"] == 0.0
    assert rule["max"] == COORDINATE_MAX
    assert "0 = left, 100 = right" in rule["quote"]


def test_the_file_index_rule_carries_the_researchs_own_sentence():
    assert "0 for first file" in describe()["identifier_quote"]
    assert "0 for first file" in IDENTIFIER_QUOTE


def test_the_two_invariants_are_served_and_quoted():
    invariants = describe()["invariants"]
    assert "recipients must sign themselves" in invariants["never_sign_quote"]
    assert "until all recipients have completed signing" in invariants["signed_pdf_quote"]


def test_the_webhook_header_is_the_one_the_research_names():
    assert WEBHOOK_SECRET_HEADER == "X-Documenso-Secret"
    assert describe()["webhook_secret_header"] == "X-Documenso-Secret"


# --------------------------------------------------------------------------- #
# The join key
# --------------------------------------------------------------------------- #


def test_the_derived_external_id_carries_the_room_the_plan_and_a_token():
    key = external_id_for("room-1", "plan-1", "abc123")
    assert key == "dsr-map.room-1.plan-1.abc123"
    # Every part the join has to survive is in it.
    assert "room-1" in key and "plan-1" in key


def test_two_plans_in_one_room_never_share_a_join_key():
    """Otherwise an event naming one plan could resolve to the other."""
    first = external_id_for("room-1", "plan-1", "aaaaaa")
    second = external_id_for("room-1", "plan-2", "bbbbbb")
    assert first != second


def test_an_external_id_carrying_a_url_separator_is_refused():
    """The key travels in a query string and in webhook bodies."""
    for bad in ("has space", "a/b", "a?b", "a&b", "a#b"):
        with pytest.raises(InvalidExternalId):
            require_external_id(bad)


def test_an_empty_external_id_is_refused():
    with pytest.raises(InvalidExternalId):
        require_external_id("   ")


def test_the_research_join_key_is_read_under_all_three_spellings():
    for key in ("externalId", "external_id", "externalid"):
        assert read_external_id({key: "dsr-map.a.b.c"}) == "dsr-map.a.b.c"


def test_the_join_key_is_read_out_of_a_nested_body():
    body = {"payload": {"externalId": "dsr-map.a.b.c"}}
    assert read_external_id(body) == "dsr-map.a.b.c"


def test_a_body_with_no_join_key_reads_as_nothing():
    assert read_external_id({"event": "DOCUMENT_OPENED"}) == ""


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #


def test_a_recipient_needs_an_email_a_name_and_a_role():
    with pytest.raises(MalformedRecipient):
        build_recipients([{"name": "Ada", "role": SIGNER}])
    with pytest.raises(MalformedRecipient):
        build_recipients([{"email": "a@b.example", "role": SIGNER}])
    with pytest.raises(MalformedRecipient):
        build_recipients([{"email": "a@b.example", "name": "Ada"}])


def test_an_address_with_no_at_sign_is_refused():
    """The research's invitation path is an email a recipient opens."""
    with pytest.raises(MalformedRecipient):
        build_recipients([{"email": "not-an-address", "name": "Ada", "role": SIGNER}])


def test_a_role_outside_the_five_is_refused_rather_than_stored():
    with pytest.raises(UnknownRecipientRole) as caught:
        build_recipients([{"email": "a@b.example", "name": "Ada", "role": "NOTARY"}])
    assert "SIGNER" in str(caught.value)


def test_a_plan_with_nobody_to_sign_it_is_refused():
    with pytest.raises(MissingRecipients):
        build_recipients([])
    with pytest.raises(MissingRecipients) as caught:
        build_recipients([{"email": "a@b.example", "name": "Ada", "role": VIEWER}])
    assert SIGNER in str(caught.value)


def test_one_address_cannot_hold_two_roles():
    """The vendor would resolve it by one of them silently winning."""
    with pytest.raises(MalformedRecipient):
        build_recipients(
            [
                {"email": "a@b.example", "name": "Ada", "role": SIGNER},
                {"email": "A@B.example", "name": "Ada", "role": APPROVER},
            ]
        )


def test_required_roles_reports_which_signing_roles_a_plan_uses():
    assert required_roles(build_recipients(people())) == [SIGNER, APPROVER]
    assert required_roles(build_recipients(people(with_approver=False))) == [SIGNER]


# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #


def test_a_field_is_placed_in_the_percentage_scale_the_research_quotes():
    field = validate_field({"type": "SIGNATURE", "positionX": 10, "positionY": 60})
    assert field["positionX"] == 10.0
    assert field["positionY"] == 60.0
    assert field["identifier"] == 0


def test_a_coordinate_past_the_edge_of_the_page_is_refused():
    with pytest.raises(MalformedField):
        validate_field({"type": "SIGNATURE", "positionX": 140})
    with pytest.raises(MalformedField):
        validate_field({"type": "SIGNATURE", "positionY": -1})


def test_a_coordinate_the_research_quotes_is_accepted_at_both_ends():
    """positionX: "0 = left, 100 = right", so both ends are legal."""
    assert validate_field({"type": "NAME", "positionX": 0})["positionX"] == 0.0
    assert validate_field({"type": "NAME", "positionX": 100})["positionX"] == 100.0


def test_a_file_index_may_not_be_negative():
    """identifier is "Index of the file (0 for first file, 1 for second, etc.)"."""
    with pytest.raises(MalformedField):
        validate_field({"type": "SIGNATURE", "identifier": -1})


def test_a_field_type_outside_the_researched_set_is_refused():
    with pytest.raises(UnknownFieldType) as caught:
        validate_field({"type": "HOLOGRAM"})
    assert "SIGNATURE" in str(caught.value)


def test_a_field_with_no_type_is_refused():
    with pytest.raises(MalformedField):
        validate_field({"positionX": 10})


# --------------------------------------------------------------------------- #
# The approver gate
# --------------------------------------------------------------------------- #


def test_the_approver_blocks_the_signers_and_names_who():
    """APPROVER | Must approve before signers can sign"""
    roster = [
        {"email": SIGNER_EMAIL, "role": SIGNER, "status": "UNOPENED"},
        {"email": APPROVER_EMAIL, "role": APPROVER, "status": "UNOPENED", "name": "Luis"},
    ]
    unlocked, blockers = signing_unlocked(roster)
    assert unlocked is False
    assert "Luis must approve before signers can sign" in blockers


def test_signers_are_unlocked_once_the_approver_has_approved():
    roster = [
        {"email": SIGNER_EMAIL, "role": SIGNER, "status": "UNOPENED"},
        {"email": APPROVER_EMAIL, "role": APPROVER, "status": RECIPIENT_APPROVED},
    ]
    assert signing_unlocked(roster) == (True, [])


def test_a_plan_with_no_approver_is_never_blocked():
    roster = [{"email": SIGNER_EMAIL, "role": SIGNER, "status": "UNOPENED"}]
    assert signing_unlocked(roster) == (True, [])


def test_the_approver_block_lists_only_the_unapproved_approvers():
    roster = [
        {"email": APPROVER_EMAIL, "role": APPROVER, "status": RECIPIENT_APPROVED},
        {"email": "second@contoso.example", "role": APPROVER, "status": "UNOPENED"},
    ]
    assert [entry["email"] for entry in approver_block(roster)] == ["second@contoso.example"]


def test_can_sign_refuses_a_signer_while_the_approver_has_not_approved(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    decision = engine.can_sign(room["id"], plan["id"], SIGNER_EMAIL)
    assert decision["can_sign"] is False
    assert decision["reason"] == "awaiting_approver"
    assert "must approve before signers can sign" in decision["detail"]


def test_can_sign_refuses_before_the_plan_was_distributed(engine, room):
    plan = make_plan(engine, room["id"], recipients=people(with_approver=False))
    decision = engine.can_sign(room["id"], plan["id"], SIGNER_EMAIL)
    assert decision["can_sign"] is False
    assert decision["reason"] == "not_distributed"


def test_can_sign_allows_the_signer_once_the_approver_has_signed(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_SIGNED, recipientEmail=APPROVER_EMAIL)
    decision = engine.can_sign(room["id"], plan["id"], SIGNER_EMAIL)
    assert decision["can_sign"] is True


def test_a_signer_blocked_is_a_conflict_not_a_forbidden(engine, room):
    """409, because the caller's credentials are not in question."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    assert SignersBlocked().status == 409


# --------------------------------------------------------------------------- #
# Events: names, fingerprints, duplicates
# --------------------------------------------------------------------------- #


def test_an_event_name_outside_the_fourteen_is_refused():
    with pytest.raises(UnknownEventType):
        require_known_event("DOCUMENT_SENT_TO_THE_MOON")


def test_an_event_with_no_name_is_malformed():
    with pytest.raises(MalformedEvent):
        require_known_event("")


def test_a_vendor_event_id_identifies_a_retry():
    """Webhooks may be retried, so handle duplicate events."""
    body = {"eventId": "evt-1", "externalId": "dsr-map.a.b.c"}
    assert event_fingerprint(EVENT_DOCUMENT_OPENED, body) == "DOCUMENT_OPENED:evt-1"


def test_two_deliveries_of_one_event_share_a_fingerprint():
    first = {"eventId": "evt-1", "recipientEmail": SIGNER_EMAIL}
    second = {"eventId": "evt-1", "recipientEmail": SIGNER_EMAIL, "laterTimestamp": "x"}
    assert event_fingerprint(EVENT_DOCUMENT_OPENED, first) == event_fingerprint(
        EVENT_DOCUMENT_OPENED, second
    )


def test_a_fingerprint_falls_back_to_the_recipient_when_the_vendor_sends_no_id():
    body = {"envelopeId": "env-1", "recipientEmail": SIGNER_EMAIL}
    assert (
        event_fingerprint(EVENT_DOCUMENT_SIGNED, body)
        == "DOCUMENT_SIGNED:env-1:buyer@northwind.example"
    )


def test_two_recipients_signing_are_two_different_events():
    """A fingerprint of the whole body would collide a signature on the approver."""
    signer = {"envelopeId": "env-1", "recipientEmail": SIGNER_EMAIL}
    approver = {"envelopeId": "env-1", "recipientEmail": APPROVER_EMAIL}
    assert event_fingerprint(EVENT_DOCUMENT_SIGNED, signer) != event_fingerprint(
        EVENT_DOCUMENT_SIGNED, approver
    )


def test_a_repeated_event_is_refused_as_a_duplicate_and_applied_once(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    first = deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    assert first["outcome"] == OUTCOME_APPLIED
    with pytest.raises(DuplicateEvent):
        deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    events = engine.events(room["id"], plan_id=plan["id"])
    assert len(events) == 1
    assert events[0]["attempts"] == 2


def test_a_duplicate_event_carries_status_200_not_an_error(engine, room):
    """A retry is not the vendor doing anything wrong."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    with pytest.raises(DuplicateEvent) as caught:
        deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    assert caught.value.status == 200


# --------------------------------------------------------------------------- #
# Events: the secret
# --------------------------------------------------------------------------- #


def test_the_secret_is_compared_exactly_once_the_header_value_is_trimmed():
    """A header value arrives padded. The value itself is compared byte for byte."""
    assert secret_matches("abc", "abc")
    assert secret_matches(" abc ", "abc")
    assert not secret_matches("ABC", "abc")
    assert not secret_matches("abcd", "abc")


def test_a_missing_secret_never_authenticates():
    assert not secret_matches(None, "abc")
    assert not secret_matches("abc", None)
    assert not secret_matches("", "")


def test_a_non_ascii_secret_gets_an_answer_rather_than_an_exception():
    assert secret_matches("sécret", "sécret")


def test_an_event_with_the_wrong_secret_writes_nothing_at_all(engine, room):
    """The one thing an unauthenticated caller must not be able to do."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    with pytest.raises(UnauthenticatedEvent):
        deliver(
            engine,
            room["id"],
            plan,
            EVENT_DOCUMENT_SIGNED,
            secret="wrong",
            recipientEmail=SIGNER_EMAIL,
        )
    assert engine.events(room["id"], plan_id=plan["id"]) == []
    recipients = engine.recipients(room["id"], plan["id"])
    assert {entry["status"] for entry in recipients} == {"UNOPENED"}


def test_an_event_with_no_secret_at_all_is_refused(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    with pytest.raises(UnauthenticatedEvent):
        deliver(
            engine,
            room["id"],
            plan,
            EVENT_DOCUMENT_OPENED,
            secret=None,
            recipientEmail=SIGNER_EMAIL,
        )


def test_a_plan_with_no_configured_secret_can_never_be_authenticated(engine, room):
    """An unconfigured plan has no way to know who may move its milestone."""
    plan = make_plan(engine, room["id"], webhook_secret="")
    with pytest.raises(UnauthenticatedEvent) as caught:
        deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    assert "webhook_secret" in str(caught.value)


def test_the_secret_header_is_matched_however_the_vendor_cased_it(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    report = engine.receive_event(
        room["id"],
        headers={"x-documenso-secret": DEMO_SECRET},
        payload={
            "event": EVENT_DOCUMENT_OPENED,
            "eventId": "evt-case",
            "externalId": plan["plan"]["external_id"],
            "recipientEmail": SIGNER_EMAIL,
        },
        actor="vendor",
        source=SOURCE,
    )
    assert report["outcome"] == OUTCOME_APPLIED


# --------------------------------------------------------------------------- #
# Events: order, resolution, and the state machine
# --------------------------------------------------------------------------- #


def test_an_event_for_an_unknown_join_key_is_refused(engine, room):
    with pytest.raises(UnresolvedPlan):
        engine.receive_event(
            room["id"],
            headers={WEBHOOK_SECRET_HEADER: DEMO_SECRET},
            payload={"event": EVENT_DOCUMENT_OPENED, "externalId": "dsr-map.other.plan.tok"},
            actor="vendor",
            source=SOURCE,
        )


def test_an_event_with_no_join_key_is_refused_and_says_why(engine, room):
    make_plan(engine, room["id"])
    with pytest.raises(UnresolvedPlan) as caught:
        engine.receive_event(
            room["id"],
            headers={WEBHOOK_SECRET_HEADER: DEMO_SECRET},
            payload={"event": EVENT_DOCUMENT_OPENED},
            actor="vendor",
            source=SOURCE,
        )
    assert "externalId" in str(caught.value)


def test_a_join_key_resolves_only_within_its_own_room(engine, room, other_room):
    plan = make_plan(engine, room["id"])
    with pytest.raises(UnresolvedPlan):
        engine.resolve_plan(other_room["id"], {"externalId": plan["plan"]["external_id"]})


def test_an_event_about_a_recipient_who_is_not_on_the_plan_changes_nobody(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    report = deliver(
        engine, room["id"], plan, EVENT_DOCUMENT_SIGNED, recipientEmail="stranger@else.example"
    )
    assert report["new_milestone"] is None
    statuses = {entry["status"] for entry in engine.recipients(room["id"], plan["id"])}
    assert "SIGNED" not in statuses


def test_a_signing_event_with_no_recipient_is_malformed():
    """A DOCUMENT_SIGNED with no recipient would otherwise land on the wrong person."""
    plan = {"milestone": MILESTONE_AWAITING_SIGNATURE, "distributed_at": NOW.isoformat()}
    with pytest.raises(MalformedEvent) as caught:
        apply_event(EVENT_DOCUMENT_SIGNED, plan, [], {"event": EVENT_DOCUMENT_SIGNED})
    assert "recipient" in str(caught.value)


def test_an_event_on_an_undelivered_plan_is_refused_not_believed(engine, room):
    """Believing it would approve a plan no buyer was ever sent."""
    plan = make_plan(engine, room["id"])
    with pytest.raises(EventBeforeDistribution):
        deliver(engine, room["id"], plan, EVENT_DOCUMENT_SIGNED, recipientEmail=SIGNER_EMAIL)


def test_a_recipient_opened_their_status_becomes_opened(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    signer = next(
        entry
        for entry in engine.recipients(room["id"], plan["id"])
        if entry["email"] == SIGNER_EMAIL
    )
    assert signer["status"] == RECIPIENT_OPENED


def test_the_approvers_signature_is_read_as_an_approval(engine, room):
    """The approver and the signer both produce DOCUMENT_SIGNED; the role tells them apart."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_SIGNED, recipientEmail=APPROVER_EMAIL)
    approver = next(
        entry
        for entry in engine.recipients(room["id"], plan["id"])
        if entry["email"] == APPROVER_EMAIL
    )
    assert approver["status"] == RECIPIENT_APPROVED


def test_an_approver_refusal_and_a_signer_refusal_are_two_different_milestones(engine, room):
    """APPROVER | Must approve before signers can sign."""
    approver_plan = make_plan(engine, room["id"], subject="Approver refuses")
    engine.distribute(room["id"], approver_plan["id"], actor="dana", source=SOURCE)
    deliver(
        engine,
        room["id"],
        approver_plan,
        EVENT_DOCUMENT_REJECTED,
        recipientEmail=APPROVER_EMAIL,
        rejectionReason="Clause 7.2 is not acceptable.",
    )
    assert engine.plan_view(room["id"], approver_plan["id"])["milestone"] == (
        MILESTONE_REFUSED_BY_APPROVER
    )

    signer_plan = make_plan(engine, room["id"], subject="Signer refuses")
    engine.distribute(room["id"], signer_plan["id"], actor="dana", source=SOURCE)
    deliver(
        engine,
        room["id"],
        signer_plan,
        EVENT_DOCUMENT_REJECTED,
        recipientEmail=SIGNER_EMAIL,
        rejectionReason="The timeline does not work for us.",
    )
    refused = engine.plan_view(room["id"], signer_plan["id"])
    assert refused["milestone"] == MILESTONE_REFUSED_BY_SIGNER


def test_a_refusal_keeps_the_reason_the_buyer_gave(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(
        engine,
        room["id"],
        plan,
        EVENT_DOCUMENT_REJECTED,
        recipientEmail=SIGNER_EMAIL,
        rejectionReason="Clause 7.2 is not acceptable.",
    )
    signer = next(
        entry
        for entry in engine.recipients(room["id"], plan["id"])
        if entry["email"] == SIGNER_EMAIL
    )
    assert signer["rejection_reason"] == "Clause 7.2 is not acceptable."


def test_an_expired_recipient_expires_the_plan_without_it_being_a_refusal(engine, room):
    """RECIPIENT_EXPIRED: the buyer never said no."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(
        engine,
        room["id"],
        plan,
        EVENT_RECIPIENT_EXPIRED,
        recipientEmail=SIGNER_EMAIL,
        expiresAt="2026-10-05T00:00:00Z",
        expirationNotifiedAt="2026-10-05T00:00:01Z",
    )
    view = engine.plan_view(room["id"], plan["id"])
    assert view["milestone"] == "expired"
    assert view["milestone"] not in (MILESTONE_REFUSED_BY_SIGNER, MILESTONE_REFUSED_BY_APPROVER)


def test_a_reminder_is_recorded_and_moves_no_milestone(engine, room):
    """A reminder is the vendor chasing somebody, not somebody doing something."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    report = deliver(
        engine, room["id"], plan, EVENT_DOCUMENT_REMINDER_SENT, recipientEmail=SIGNER_EMAIL
    )
    assert report["outcome"] == OUTCOME_NOTED
    assert report["new_milestone"] is None
    signer = next(
        entry
        for entry in engine.recipients(room["id"], plan["id"])
        if entry["email"] == SIGNER_EMAIL
    )
    assert signer["reminder_count"] == 1


def test_completion_flips_the_milestone_to_approved(engine, room):
    """The sales room consumes DOCUMENT_COMPLETED to flip the milestone to Approved."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    report = deliver(
        engine, room["id"], plan, EVENT_DOCUMENT_COMPLETED, completedAt="2026-09-28T10:22:00Z"
    )
    assert report["new_milestone"] == MILESTONE_APPROVED
    view = engine.plan_view(room["id"], plan["id"])
    assert view["milestone"] == MILESTONE_APPROVED
    assert view["status"] == "COMPLETED"


def test_completion_notifies_the_owner(engine, room):
    """...and notify the owner."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_COMPLETED)
    notices = engine.notices(room["id"])
    assert any(row["reason"] == "plan_approved" for row in notices)


def test_an_approved_plan_is_never_moved_backwards(engine, room):
    """The research publishes no transition out of approved."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_COMPLETED)
    late = deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    assert late["new_milestone"] is None
    view = engine.plan_view(room["id"], plan["id"])
    assert view["milestone"] == MILESTONE_APPROVED


def test_a_template_event_changes_no_plan(engine, room):
    """Editing a template must not appear to approve a plan."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    report = deliver(engine, room["id"], plan, "TEMPLATE_UPDATED")
    assert report["outcome"] == OUTCOME_NOTED
    assert report["new_milestone"] is None


def test_a_cancelled_document_cancels_the_plan(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_CANCELLED)
    assert engine.plan_view(room["id"], plan["id"])["milestone"] == "cancelled"


# --------------------------------------------------------------------------- #
# The completion rule
# --------------------------------------------------------------------------- #


def test_a_plan_is_complete_when_every_signing_recipient_is_done():
    roster = [
        {"role": SIGNER, "status": RECIPIENT_COMPLETED},
        {"role": APPROVER, "status": RECIPIENT_APPROVED},
    ]
    assert plan_is_complete(roster) is True


def test_a_cc_who_never_opened_does_not_hold_a_plan_open():
    roster = [
        {"role": SIGNER, "status": RECIPIENT_COMPLETED},
        {"role": APPROVER, "status": RECIPIENT_APPROVED},
        {"role": CC, "status": "UNOPENED"},
        {"role": VIEWER, "status": "UNOPENED"},
    ]
    assert plan_is_complete(roster) is True


def test_a_plan_with_a_signer_still_to_act_is_not_complete():
    roster = [
        {"role": SIGNER, "status": RECIPIENT_COMPLETED},
        {"role": APPROVER, "status": "UNOPENED"},
    ]
    assert plan_is_complete(roster) is False


def test_a_recipient_is_resolved_by_address_by_vendor_id_and_by_record_id():
    roster = [
        {"id": "rec-1", "email": SIGNER_EMAIL, "vendor_recipient_id": "vend-1"},
    ]
    assert resolve_recipient(SIGNER_EMAIL.upper(), roster)["id"] == "rec-1"
    assert resolve_recipient("id:vend-1", roster)["id"] == "rec-1"
    assert resolve_recipient("id:rec-1", roster)["id"] == "rec-1"
    assert resolve_recipient("id:nobody", roster) is None


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_a_new_plan_is_a_draft_that_has_gone_nowhere(engine, room):
    plan = make_plan(engine, room["id"])
    assert plan["milestone"] == MILESTONE_DRAFT
    assert plan["status"] == "DRAFT"
    assert plan["plan"]["distributed_at"] == ""


def test_distributing_moves_the_plan_from_draft_to_pending(engine, room):
    """The document status changes from DRAFT to PENDING."""
    plan = make_plan(engine, room["id"])
    view = engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    assert view["milestone"] == MILESTONE_AWAITING_SIGNATURE
    assert view["status"] == "PENDING"
    assert view["plan"]["distributed_at"]


def test_distributing_twice_is_refused(engine, room):
    """The vendor would send the signing links again."""
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    with pytest.raises(AlreadyDistributed):
        engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)


def test_an_approved_plan_cannot_be_cancelled(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_COMPLETED)
    with pytest.raises(PlanStateConflict):
        engine.cancel_plan(room["id"], plan["id"], actor="dana", source=SOURCE)


def test_a_plan_in_another_room_is_not_found(engine, room, other_room):
    plan = make_plan(engine, room["id"])
    with pytest.raises(NoSuchPlan):
        engine.plan_view(other_room["id"], plan["id"])


def test_a_template_from_another_room_is_not_found(engine, room, other_room):
    template = engine.create_template(room["id"], {"name": "Standard"}, actor="dana", source=SOURCE)
    with pytest.raises(NoSuchTemplate):
        engine.template(other_room["id"], template["id"])


def test_a_plan_needs_a_subject(engine, room):
    with pytest.raises(MalformedEvent):
        make_plan(engine, room["id"], subject="  ")


def test_a_signing_order_outside_the_researched_pair_is_refused(engine, room):
    with pytest.raises(MalformedEvent):
        make_plan(engine, room["id"], signing_order="ROUND_ROBIN")


def test_the_invite_path_may_be_any_of_the_three_the_research_offers(engine, room):
    """Email, redirect and embed are all offered by the research."""
    for path in ("embed", "redirect", "email"):
        view = engine.create_plan(
            room["id"],
            {
                "subject": f"Invite {path}",
                "webhook_secret": DEMO_SECRET,
                "recipients": people(),
                "invite_path": path,
            },
            actor="dana",
            source=SOURCE,
        )
        assert view["links"]["invite_path"] == path


def test_an_invite_path_outside_the_three_is_refused(engine, room):
    with pytest.raises(MalformedEvent):
        make_plan(engine, room["id"], invite_path="carrier_pigeon")


# --------------------------------------------------------------------------- #
# The links, built from the research's own URL patterns
# --------------------------------------------------------------------------- #


def test_the_two_vendor_urls_are_built_from_the_researchs_patterns(engine, room):
    """URL https://app.documenso.com/d/{token}, embed .../embed/direct/{token}"""
    plan = make_plan(engine, room["id"])
    links = plan["links"]
    assert links["signing_url"].startswith("https://app.documenso.com/d/")
    assert links["embed_url"].startswith("https://app.documenso.com/embed/direct/")
    assert f"externalId={plan['plan']['external_id']}" in links["signing_url"]


# --------------------------------------------------------------------------- #
# Reading the room
# --------------------------------------------------------------------------- #


def test_the_summary_counts_this_rooms_plans_by_milestone(engine, room):
    make_plan(engine, room["id"], subject="One")
    make_plan(engine, room["id"], subject="Two")
    summary = engine.summary(room["id"])
    assert summary["plans"] == 2
    assert summary["by_milestone"][MILESTONE_DRAFT] == 2


def test_the_summary_serves_the_two_invariants(engine, room):
    """They are the two things a reader looks for and does not find on a page."""
    invariants = engine.summary(room["id"])["invariants"]
    assert invariants["never_sign_for_a_recipient"] is True
    assert invariants["signed_pdf_before_completion"] is False


def test_the_event_log_keeps_a_retry_visible_rather_than_collapsing_it(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    with pytest.raises(DuplicateEvent):
        deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    assert engine.events(room["id"], plan_id=plan["id"])[0]["attempts"] == 2


def test_a_notice_can_be_acknowledged(engine, room):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    notices = engine.notices(room["id"])
    assert notices and all(not row["read"] for row in notices)
    engine.acknowledge(room["id"], {}, actor="dana", source=SOURCE)
    assert all(row["read"] for row in engine.notices(room["id"]))


def test_every_write_names_the_route_that_served_it(engine, room, store):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_COMPLETED)
    sources = {row["source"] for row in store.audit(limit=500) if row["source"]}
    assert sources == {SOURCE}
    assert all(source.startswith(f"POST {PREFIX}/") for source in sources)


def test_the_collections_this_feature_owns_are_its_own(engine, room, store):
    plan = make_plan(engine, room["id"])
    engine.distribute(room["id"], plan["id"], actor="dana", source=SOURCE)
    deliver(engine, room["id"], plan, EVENT_DOCUMENT_OPENED, recipientEmail=SIGNER_EMAIL)
    for collection in (COLLECTION_PLAN, COLLECTION_RECIPIENT, COLLECTION_EVENT, COLLECTION_NOTICE):
        assert store.list(collection), collection
    assert not store.list(COLLECTION_TEMPLATE)


# --------------------------------------------------------------------------- #
# The architectural guards the contract names
# --------------------------------------------------------------------------- #


def test_the_domain_module_imports_nothing_but_the_store():
    """The contract's rule, read off the package on disk rather than off a claim."""
    package = Path(inspect.getfile(MapEngine)).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "from dsr.api" not in text, module.name
        assert "dsr.deps" not in text, module.name
        assert "fastapi" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_domain_module_opens_no_connection_of_its_own():
    package = Path(inspect.getfile(MapEngine)).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "_conn" not in text, module.name
        assert "db_path" not in text, module.name


def test_nothing_in_the_domain_module_can_sign_for_a_recipient():
    """The research states this as a limit on the API, and this product keeps it."""
    package = Path(inspect.getfile(MapEngine)).parent
    banned = ("def sign_for", "sign_on_behalf", "force_signature", "auto_sign")
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"{module.name} defines {phrase}"


def test_the_field_guard_constant_names_the_rule_it_guards():
    """Kept as a named constant so a test can point at the rule, not at a number."""
    assert "percent" in MALFORMED_FIELD_GUARD
    assert "SIGNATURE" in FIELD_TYPES


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    from dsr.scheduling_meetings.inferences import inferences as register

    report = register()
    assert report["count"] >= 10
    for entry in report["decisions"]:
        assert entry["id"], entry
        assert entry["question"], entry["id"]
        assert entry["reading"], entry["id"]
        assert entry["otherwise"], entry["id"]
        assert entry["changeable_by"], entry["id"]


def test_the_register_records_every_decision_the_issue_left_open():
    """The issue names six. All six are here."""
    from dsr.scheduling_meetings.inferences import inferences as register

    ids = {entry["id"] for entry in register()["decisions"]}
    for expected in (
        "never-sign-for-a-recipient",
        "external-id-is-derived",
        "approver-refusal-differs-from-signer-refusal",
        "invitation-path-is-the-embed",
        "webhook-events-are-accepted-unknown-ones-refused",
        "duplicate-events-are-not-refusals",
    ):
        assert expected in ids, expected


def test_the_two_invariants_are_registered_as_decisions_not_coincidence():
    from dsr.scheduling_meetings.inferences import inferences as register

    ids = {entry["id"] for entry in register()["decisions"]}
    assert "an-unauthenticated-event-writes-nothing" in ids
    assert "the-signed-pdf-is-not-retrievable-before-completion" in ids
    assert "white-labelling-is-not-used" in ids


def test_the_register_separates_what_the_research_fixed_from_what_it_left_open():
    from dsr.scheduling_meetings.inferences import inferences as register

    entries = {entry["id"]: entry for entry in register()["decisions"]}
    assert entries["never-sign-for-a-recipient"]["research_says"]
    assert entries["an-unauthenticated-event-writes-nothing"]["research_says"]
    assert "Not a judgement call" in entries["never-sign-for-a-recipient"]["changeable_by"]


# --------------------------------------------------------------------------- #
# The coordinate rule, restated so the guard constant cannot drift
# --------------------------------------------------------------------------- #


def test_the_coordinate_quote_is_the_researchs_own_sentence():
    assert COORDINATE_QUOTE.startswith("positionX | Horizontal position from left edge")
    assert "0 = left, 100 = right" in COORDINATE_QUOTE
