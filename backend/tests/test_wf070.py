"""WF-070: the NDA gate, exercised as a domain module with no server.

The rules in ``dsr.link_gating.agreement`` are pure: they touch the
:class:`~dsr.store.RecordStore` and nothing else. So every rule below is
reachable without a ``TestClient``, and these tests are what makes the gate's
security properties checkable at the instant they are stated about.

What is under test, and why each one matters
--------------------------------------------

* The gate is a field on the link, and enabling it without an agreement is
  refused. A link that looks gated and releases everything is the worst state
  this workflow can be in.
* An acceptance is bound to a viewer session, so the second reader of a forwarded
  link does not inherit the first reader's acceptance.
* An acceptance is bound to a *digest of the text*, so editing the NDA re-opens
  the gate instead of grandfathering the buyer into the new terms.
* The gate fails closed when the agreement it names is gone.
* Expiry and revocation are evaluated on the request that releases content.

Every test names the property it protects, because a test named
``test_agreement_flow`` tells a reviewer nothing about which rule they can stop
worrying about.
"""

from __future__ import annotations

import ast
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.link_gating import agreement as nda, gate as link_gate, rules
from dsr.store import RecordStore

MODULE = "wf070_require_nda_acceptance_before_viewing"

NOW = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)

NDA_BODY = (
    "MUTUAL NON-DISCLOSURE AGREEMENT. Each party keeps the other party's "
    "confidential information confidential."
)

AMENDED_BODY = "MUTUAL NON-DISCLOSURE AGREEMENT, AMENDED. Retention is extended to five years."


@pytest.fixture()
def engine(store: RecordStore) -> nda.AgreementEngine:
    """The engine, with the clock pinned so every boundary is exact."""
    return nda.AgreementEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    record = store.create("room", {"name": "Northwind"}, actor="test", source="test")
    return record["id"]


@pytest.fixture()
def links(store: RecordStore) -> link_gate.GateEngine:
    """WF-069's engine, so the links under test are real gated links."""
    return link_gate.GateEngine(store, now=lambda: NOW)


@pytest.fixture()
def nda_row(engine: nda.AgreementEngine, room: str) -> dict:
    return engine.create_agreement(
        room,
        {"title": "Northwind mutual NDA", "body": NDA_BODY},
        source="test",
        actor="test",
    )


def make_link(
    links: link_gate.GateEngine, room: str, title: str = "Deal link", **extra: object
) -> dict:
    return links.create_link(
        room,
        {"title": title, "dataroom_id": room, **extra},
        source="test",
        actor="test",
    )


def gated(
    engine: nda.AgreementEngine, links: link_gate.GateEngine, room: str, agreement_id: str
) -> dict:
    """A link with the agreement gate on, made the way the CLI makes it."""
    link = make_link(links, room)
    engine.set_gate(link["id"], {nda.CONVENIENCE_FLAG: agreement_id}, source="test", actor="test")
    return link


def accept_now(engine: nda.AgreementEngine, link_id: str, email: str | None = None) -> dict:
    opened = engine.open_link(link_id, source="test")
    return engine.accept(
        link_id,
        opened["session_id"],
        {"accepted": True, **({"email": email} if email else {})},
        source="test",
    )


# --------------------------------------------------------------------------- #
# The digest. What an acceptance is actually bound to.
# --------------------------------------------------------------------------- #


def test_whitespace_is_not_a_change_to_the_text():
    """A re-wrap of the same paragraphs must not re-open a settled gate.

    Cosmetic re-wrapping is the edit a seller makes most often and means least.
    Treating it as a change to the terms would expire the acceptance of every
    buyer on the link, which trains a seller to stop trusting the gate.
    """
    assert nda.body_digest("one\n\ntwo   three") == nda.body_digest("one two three")


def test_any_real_edit_changes_the_digest():
    assert nda.body_digest(NDA_BODY) != nda.body_digest(AMENDED_BODY)


def test_acceptance_covers_needs_both_the_id_and_the_digest():
    """Matching on the id alone misses an edit; matching on the digest alone lets an
    unrelated agreement whose text happens to match release the content."""
    acceptance = {"agreement_id": "a1", "body_digest": nda.body_digest(NDA_BODY)}
    assert nda.acceptance_covers(acceptance, "a1", NDA_BODY)
    # Right text, wrong agreement.
    assert not nda.acceptance_covers(acceptance, "a2", NDA_BODY)
    # Right agreement, superseded text.
    assert not nda.acceptance_covers(acceptance, "a1", AMENDED_BODY)
    # Nothing at all.
    assert not nda.acceptance_covers({}, "a1", NDA_BODY)


# --------------------------------------------------------------------------- #
# Gate settings. Tri-state, and the researched requirement that they pair up.
# --------------------------------------------------------------------------- #


def test_enabling_the_gate_without_an_agreement_is_refused():
    """OpenAPI: `agreement_id` is "Required when `enable_agreement` is true`."""
    with pytest.raises(nda.AgreementError) as caught:
        nda.normalize_gate({nda.ENABLE_FIELD: True})
    assert "agreement_id" in caught.value.errors


def test_the_convenience_flag_enables_the_gate_and_sets_the_agreement():
    """CLI: "The single flag both enables the gate and sets the agreement id`."""
    gate = nda.normalize_gate({nda.CONVENIENCE_FLAG: " agr-1 "})
    assert gate == {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: "agr-1"}


def test_an_explicit_flag_still_wins_over_the_convenience_flag():
    gate = nda.normalize_gate({nda.CONVENIENCE_FLAG: "agr-1", nda.ENABLE_FIELD: False})
    assert gate[nda.ENABLE_FIELD] is False
    assert gate[nda.AGREEMENT_REF_FIELD] == "agr-1"


def test_the_update_is_tri_state():
    """Absent leaves it alone, a boolean sets it, and an explicit null clears it."""
    base = {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: "agr-1"}

    untouched = nda.normalize_gate({}, base=base)
    assert untouched == base

    switched_off = nda.normalize_gate({nda.ENABLE_FIELD: "off"}, base=base)
    assert switched_off[nda.ENABLE_FIELD] is False
    # The selection survives, so the gate can be paused mid-deal and resumed.
    assert switched_off[nda.AGREEMENT_REF_FIELD] == "agr-1"

    cleared = nda.normalize_gate(
        {nda.ENABLE_FIELD: False, nda.AGREEMENT_REF_FIELD: None}, base=base
    )
    assert cleared == {nda.ENABLE_FIELD: False, nda.AGREEMENT_REF_FIELD: None}


def test_clearing_the_agreement_while_the_gate_is_on_is_refused():
    """OpenAPI: `agreement_id` is "Required when `enable_agreement` is true".

    Clearing it and leaving the gate on would produce the one state this workflow
    exists to prevent: a link that looks gated and releases everything. The caller
    has to turn the gate off in the same request.
    """
    base = {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: "agr-1"}
    with pytest.raises(nda.AgreementError) as caught:
        nda.normalize_gate({nda.AGREEMENT_REF_FIELD: None}, base=base)
    assert "agreement_id" in caught.value.errors


def test_the_gate_defaults_off():
    """CLI flag table: `--agreement <id> | none`."""
    gate = nda.normalize_gate({})
    assert gate[nda.ENABLE_FIELD] is nda.DEFAULT_ENABLE_AGREEMENT is False


def test_an_empty_agreement_id_is_not_a_clear():
    """The empty string is the one value that could never have been an id.

    Reading it as "clear" would let a form that submits an empty box silently
    ungate a link, which is the failure the explicit null exists to prevent.
    """
    with pytest.raises(nda.AgreementError) as caught:
        nda.normalize_gate({nda.AGREEMENT_REF_FIELD: "  "}, base={nda.ENABLE_FIELD: False})
    assert "agreement_id" in caught.value.errors


def test_the_flag_accepts_the_documented_on_and_off():
    assert nda.normalize_gate({nda.ENABLE_FIELD: "on", nda.AGREEMENT_REF_FIELD: "a"})[
        nda.ENABLE_FIELD
    ]
    assert not nda.normalize_gate({nda.ENABLE_FIELD: "off"})[nda.ENABLE_FIELD]


def test_a_nonsense_flag_is_refused_rather_than_coerced():
    with pytest.raises(nda.AgreementError):
        nda.normalize_gate({nda.ENABLE_FIELD: "sometimes", nda.AGREEMENT_REF_FIELD: "a"})


def test_the_gate_is_off_even_when_an_agreement_is_still_selected():
    """`gate_required` looks at the flag only, which is what makes pausing work."""
    assert not nda.gate_required({nda.ENABLE_FIELD: False, nda.AGREEMENT_REF_FIELD: "a"})
    assert nda.gate_required({nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: "a"})


# --------------------------------------------------------------------------- #
# Agreements
# --------------------------------------------------------------------------- #


def test_an_agreement_needs_a_title_and_a_body(engine, room):
    """An agreement with no text is not something a viewer can accept."""
    with pytest.raises(nda.AgreementError) as caught:
        engine.create_agreement(room, {}, source="test", actor="test")
    assert set(caught.value.errors) == {"title", "body"}


def test_an_agreement_needs_a_room(engine):
    with pytest.raises(nda.AgreementError) as caught:
        engine.create_agreement("", {"title": "t", "body": "b"}, source="test", actor="test")
    assert "room_id" in caught.value.errors


def test_a_created_agreement_is_version_one_and_fingerprinted(engine, room):
    row = engine.create_agreement(
        room, {"title": "NDA", "body": NDA_BODY}, source="test", actor="test"
    )
    assert row["version"] == 1
    assert row["body_digest"] == nda.body_digest(NDA_BODY)
    assert row["body"] == NDA_BODY


def test_an_unknown_agreement_kind_is_refused(engine, room):
    with pytest.raises(nda.AgreementError) as caught:
        engine.create_agreement(
            room, {"title": "t", "body": "b", "kind": "e-thing"}, source="test", actor="test"
        )
    assert "kind" in caught.value.errors


def test_fixing_a_title_does_not_move_the_version(engine, room, nda_row):
    """A cosmetic edit must not re-open a gate for a buyer who already accepted."""
    edited = engine.update_agreement(
        nda_row["id"], {"title": "Northwind mutual NDA v2"}, source="test", actor="test"
    )
    assert edited["version"] == 1
    assert edited["title"] == "Northwind mutual NDA v2"


def test_changing_the_text_moves_the_version(engine, room, nda_row):
    edited = engine.update_agreement(
        nda_row["id"], {"body": AMENDED_BODY}, source="test", actor="test"
    )
    assert edited["version"] == 2
    assert edited["body_digest"] == nda.body_digest(AMENDED_BODY)


def test_an_update_that_changes_nothing_still_returns_the_current_state(engine, room, nda_row):
    same = engine.update_agreement(nda_row["id"], {}, source="test", actor="test")
    assert same["version"] == 1
    assert same["body_digest"] == nda_row["body_digest"]


def test_blanking_the_body_is_refused(engine, room, nda_row):
    """An edit must not be able to leave a gate showing an empty box."""
    with pytest.raises(nda.AgreementError) as caught:
        engine.update_agreement(nda_row["id"], {"body": "   "}, source="test", actor="test")
    assert "body" in caught.value.errors


def test_an_unknown_agreement_is_not_found(engine):
    with pytest.raises(nda.AgreementNotFound):
        engine.read_agreement("agr_absent")


def test_a_record_of_another_collection_is_not_an_agreement(engine, room, store):
    """The store is schema-flexible, so a wrong id must not resolve to something."""
    other = store.create("room", {"name": "Not an NDA"}, actor="test", source="test")
    with pytest.raises(nda.AgreementNotFound):
        engine.read_agreement(other["id"])


def test_the_list_omits_the_bodies_but_the_read_includes_them(engine, room, nda_row):
    """A board wants titles and versions. Shipping every NDA's full text to render
    a list is how legal text ends up in a browser cache nobody chose to put it in."""
    listed = engine.list_agreements(room)
    assert listed[0]["title"] == "Northwind mutual NDA"
    assert "body" not in listed[0]
    assert engine.read_agreement(nda_row["id"])["body"] == NDA_BODY


def test_retiring_an_agreement_keeps_its_acceptances_and_names_the_broken_gates(
    engine, links, room, nda_row
):
    """WF-069's rule, reused: revocation is expiry, not disappearance.

    The seller is told which gates now fail closed rather than having them repaired
    silently, because a gate that quietly opened itself is the one change nobody
    could audit.
    """
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"])

    retired = engine.delete_agreement(nda_row["id"], source="test", actor="test")

    assert retired["retired"] is True
    assert retired["gates_now_closed"] == [link["id"]]
    # The acceptance survives, so "who accepted what" stays answerable.
    assert len(engine.list_acceptances(room_id=room)) == 1


# --------------------------------------------------------------------------- #
# The gate on a link
# --------------------------------------------------------------------------- #


def test_the_gate_settings_land_on_the_link_row_itself(engine, store, links, room, nda_row):
    """The research: "The gate flag + agreement reference live on the Link row."

    Asserted against the stored record rather than against the engine's own
    projection, because a projection would agree with itself no matter where the
    write actually landed.
    """
    link = gated(engine, links, room, nda_row["id"])
    stored = store.get(link["id"])["data"]
    assert stored[nda.ENABLE_FIELD] is True
    assert stored[nda.AGREEMENT_REF_FIELD] == nda_row["id"]


def test_the_gate_cannot_point_at_an_agreement_that_does_not_exist(engine, links, room):
    """Caught on the way in, so the link never reaches the state where it looks
    gated and releases everything."""
    link = make_link(links, room)
    with pytest.raises(nda.AgreementNotFound):
        engine.set_gate(
            link["id"],
            {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: "agr_absent"},
            source="test",
            actor="test",
        )
    assert engine.link_gate(link["id"])["gate"]["enabled"] is False


def test_setting_the_gate_to_what_it_already_is_writes_nothing(engine, store, links, room, nda_row):
    """An update that re-stamps a row it did not change fills the audit log with
    no-ops and makes the log harder to read than it needs to be."""
    link = gated(engine, links, room, nda_row["id"])
    before = len(store.audit(collection=rules.LINK_COLLECTION))
    engine.set_gate(
        link["id"], {nda.ENABLE_FIELD: True, nda.AGREEMENT_REF_FIELD: nda_row["id"]}, source="test"
    )
    assert len(store.audit(collection=rules.LINK_COLLECTION)) == before


def test_a_revoked_link_still_reports_its_gate(engine, links, room, nda_row):
    """A seller needs to see which NDA a withdrawn link was pointing at."""
    link = gated(engine, links, room, nda_row["id"])
    links.revoke_link(link["id"], source="test", actor="test")
    reported = engine.link_gate(link["id"])
    assert reported["revoked"] is True
    assert reported["gate"]["agreement_id"] == nda_row["id"]


def test_a_gate_pointing_at_a_retired_agreement_reports_itself_broken(engine, links, room, nda_row):
    """A seller has to be able to see the fail-closed state. From the flag alone it
    is invisible."""
    link = gated(engine, links, room, nda_row["id"])
    engine.delete_agreement(nda_row["id"], source="test", actor="test")
    gate = engine.link_gate(link["id"])["gate"]
    assert gate["enabled"] is True
    assert gate["agreement_ok"] is False
    assert gate["agreement_title"] is None


def test_gating_a_link_that_is_not_one_is_not_found(engine, store):
    other = store.create("room", {"name": "Not a link"}, actor="test", source="test")
    with pytest.raises(nda.AgreementNotFound):
        engine.link_gate(other["id"])


# --------------------------------------------------------------------------- #
# The viewer. This is the part that has to be right.
# --------------------------------------------------------------------------- #


def test_an_ungated_link_needs_no_session_and_releases_content(engine, links, room, store):
    """The control case. A link with no gate must not accumulate rows."""
    link = make_link(links, room, email_protected=False)
    opened = engine.open_link(link["id"], source="test")
    assert opened["state"] == nda.STATE_OPEN
    assert opened["session_id"] is None
    assert store.count_where(nda.SESSION_COLLECTION, {}) == 0
    assert engine.content(link["id"], None, source="test")["released"] is True


def test_a_gated_link_serves_the_nda_and_asks_for_acceptance(engine, links, room, nda_row):
    """Step three of the flow: "before any document renders, is shown the NDA`."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    assert opened["state"] == nda.STATE_AGREEMENT
    assert opened["agreement"]["body"] == NDA_BODY
    assert opened["message"]


def test_content_is_refused_before_acceptance(engine, links, room, nda_row):
    """Step four is the whole point: "Only after acceptance does the room's
    content load`."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_NOT_ACCEPTED


def test_content_is_refused_with_no_session_at_all(engine, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], None, source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_SESSION_REQUIRED


def test_content_is_refused_for_a_session_from_another_link(engine, links, room, nda_row):
    """A session is bound to the link that minted it, so one link's acceptance
    cannot be presented to a different link's gate."""
    first = gated(engine, links, room, nda_row["id"])
    second = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(first["id"], source="test")
    engine.accept(first["id"], opened["session_id"], {"accepted": True}, source="test")

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(second["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_SESSION_UNKNOWN


def test_an_unknown_session_is_refused(engine, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], "sess_absent", source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_SESSION_UNKNOWN


def test_the_acceptance_must_be_explicit(engine, links, room, nda_row):
    """A request that merely arrives at the accept route has not accepted
    anything. Treating the arrival as consent is the failure this guards."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    with pytest.raises(nda.AgreementError) as caught:
        engine.accept(link["id"], opened["session_id"], {}, source="test")
    assert "accepted" in caught.value.errors


def test_accepting_releases_the_content(engine, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    accepted = accept_now(engine, link["id"])
    assert accepted["accepted"] is True
    assert accepted["content_released"] is True

    session_id = accepted["session_id"]
    released = engine.content(link["id"], session_id, source="test")
    assert released["released"] is True
    assert released["gate_satisfied"] is True

    # A *new* session has not accepted, so it is still refused. Re-opening the link
    # mints a fresh session and nothing carries forward.
    reopened = engine.open_link(link["id"], source="test")
    assert reopened["session_id"] != session_id
    with pytest.raises(nda.AgreementDenied):
        engine.content(link["id"], reopened["session_id"], source="test")


def test_a_second_reader_of_a_forwarded_link_does_not_inherit_the_acceptance(
    engine, links, room, nda_row
):
    """The reason an acceptance is recorded against a viewer session.

    Putting the acceptance on the link instead would make "accepted" a property of
    a link rather than of a person who opened it.
    """
    link = gated(engine, links, room, nda_row["id"])
    first = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], first["session_id"], {"accepted": True}, source="test")

    second = engine.open_link(link["id"], source="test")
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], second["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_NOT_ACCEPTED


def test_each_open_mints_a_fresh_session(engine, links, room, nda_row, store):
    """Nothing a viewer proved on an earlier pass carries into this one, including
    a pass they cleared a minute ago."""
    link = gated(engine, links, room, nda_row["id"])
    first = engine.open_link(link["id"], source="test")
    second = engine.open_link(link["id"], source="test")
    assert first["session_id"] != second["session_id"]
    assert store.count_where(nda.SESSION_COLLECTION, {"link_id": link["id"]}) == 2


def test_accepting_twice_does_not_write_a_second_row(engine, links, room, nda_row, store):
    """The research specifies no cap on acceptance, and a duplicate row would make
    "how many people accepted" a question with two answers."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    first = engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")
    second = engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")
    assert first["created"] is True
    assert second["created"] is False
    assert store.count_where(nda.ACCEPTANCE_COLLECTION, {}) == 1


def test_the_acceptance_records_the_version_and_the_email(engine, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"], email="Buyer@Northwind.Example")

    row = engine.list_acceptances(room_id=room)[0]
    assert row["agreement_version"] == 1
    assert row["body_digest"] == nda.body_digest(NDA_BODY)
    assert row["email"] == "buyer@northwind.example"


def test_an_email_can_be_added_at_acceptance_time(engine, store, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"], email="buyer@northwind.example")
    sessions = engine.list_sessions(room_id=room)
    assert sessions[0]["email"] == "buyer@northwind.example"
    assert sessions[0]["accepted"] is True
    # The id is there, or the list is not actionable.
    assert sessions[0]["id"] == store.find(nda.SESSION_COLLECTION, {}, limit=1)[0]["id"]
    # The token hash never leaves through a list.
    assert "token_hash" not in sessions[0]


def test_accepting_on_an_ungated_link_is_refused(engine, links, room):
    """There is nothing to accept, so the route must say so rather than record a
    consent to no agreement."""
    link = make_link(links, room, email_protected=False)
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.accept(link["id"], "sess_x", {"accepted": True}, source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_NOT_ACCEPTED


# --------------------------------------------------------------------------- #
# The rule that matters most: an edit re-opens the gate.
# --------------------------------------------------------------------------- #


def test_editing_the_nda_re_opens_the_gate_for_someone_who_already_accepted(
    engine, links, room, nda_row
):
    """A seller's edit to their own legal text is the one change that must not
    quietly weaken a gate already in force."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")

    engine.update_agreement(nda_row["id"], {"body": AMENDED_BODY}, source="test", actor="test")

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_SUPERSEDED


def test_the_superseded_reason_is_distinct_from_the_not_accepted_one():
    """The remedies differ: one means "read and click", the other means "your
    acceptance no longer covers what you would be reading`."""
    reasons = nda.AgreementDenied.MESSAGES
    assert (
        reasons[nda.AgreementDenied.REASON_SUPERSEDED]
        != reasons[nda.AgreementDenied.REASON_NOT_ACCEPTED]
    )


def test_accepting_the_current_text_re_opens_the_gate_after_an_edit(engine, links, room, nda_row):
    """The loop has to be escapable: re-accepting is what a viewer does."""
    link = gated(engine, links, room, nda_row["id"])
    first = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], first["session_id"], {"accepted": True}, source="test")
    engine.update_agreement(nda_row["id"], {"body": AMENDED_BODY}, source="test", actor="test")

    # The same viewer, the same session, accepting the amended text.
    engine.accept(link["id"], first["session_id"], {"accepted": True}, source="test")
    assert engine.content(link["id"], first["session_id"], source="test")["released"] is True
    assert engine.read_agreement(nda_row["id"])["version"] == 2


def test_pointing_a_link_at_a_different_agreement_does_not_release_it(engine, links, room, store):
    """Id-only matching would have released this. The gate is link-scoped, so
    changing the link's answer changes what has to be accepted."""
    first_nda = engine.create_agreement(
        room, {"title": "First", "body": NDA_BODY}, source="test", actor="test"
    )
    second_nda = engine.create_agreement(
        room, {"title": "Second", "body": AMENDED_BODY}, source="test", actor="test"
    )
    link = gated(engine, links, room, first_nda["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")

    engine.set_gate(
        link["id"], {nda.AGREEMENT_REF_FIELD: second_nda["id"]}, source="test", actor="test"
    )

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_NOT_ACCEPTED
    # And the acceptance record is untouched: the history is not rewritten to suit
    # the new gate.
    assert store.count_where(nda.ACCEPTANCE_COLLECTION, {}) == 1


def test_turning_the_gate_off_releases_content_again(engine, links, room, nda_row):
    """Step five: "Rep can later turn the gate on/off`."""
    link = gated(engine, links, room, nda_row["id"])
    engine.set_gate(link["id"], {nda.ENABLE_FIELD: False}, source="test", actor="test")
    assert engine.content(link["id"], None, source="test")["released"] is True


# --------------------------------------------------------------------------- #
# Fail closed.
# --------------------------------------------------------------------------- #


def test_a_gate_whose_agreement_is_retired_releases_nothing(engine, links, room, nda_row):
    """A gate on with nothing to accept must not fall through to open content."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")

    engine.delete_agreement(nda_row["id"], source="test", actor="test")

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.open_link(link["id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_UNAVAILABLE

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_UNAVAILABLE


def test_the_unavailable_reason_does_not_look_like_a_buyer_error():
    """The viewer cannot fix this one, so it must not read as "you did not click`."""
    reasons = nda.AgreementDenied.MESSAGES
    assert (
        reasons[nda.AgreementDenied.REASON_UNAVAILABLE]
        != reasons[nda.AgreementDenied.REASON_NOT_ACCEPTED]
    )


# --------------------------------------------------------------------------- #
# The link's own lifecycle, evaluated on the request that releases content.
# --------------------------------------------------------------------------- #


def test_an_expired_link_releases_nothing_even_to_someone_who_accepted(
    engine, links, room, store, nda_row
):
    """WF-069's boundary rule, reused so the two gates cannot disagree about the
    instant a link closes: at the instant named it is still open, strictly after
    it is closed."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")

    store.update(
        link["id"],
        {"expires_at": (NOW - timedelta(seconds=1)).isoformat(timespec="milliseconds")},
        actor="test",
        source="test",
    )

    with pytest.raises(nda.AgreementDenied) as caught:
        engine.content(link["id"], opened["session_id"], source="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_CLOSED


def test_a_link_is_still_open_at_the_instant_its_expiry_names(engine, links, room, store, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")

    store.update(
        link["id"],
        {"expires_at": NOW.isoformat(timespec="milliseconds")},
        actor="test",
        source="test",
    )

    assert engine.content(link["id"], opened["session_id"], source="test")["released"] is True


def test_a_revoked_link_releases_nothing_even_to_someone_who_accepted(engine, links, room, nda_row):
    """A revoked link and an expired one are worded identically, so a viewer
    holding a forwarded URL cannot tell the two apart."""
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")
    links.revoke_link(link["id"], source="test", actor="test")

    with pytest.raises(nda.AgreementDenied) as expired:
        engine.content(
            links.create_link(
                room,
                {
                    "title": "also expired",
                    "dataroom_id": room,
                    "expires_at": (NOW - timedelta(days=1)).isoformat(timespec="milliseconds"),
                },
                source="test",
                actor="test",
            )["id"],
            None,
            source="test",
        )
    with pytest.raises(nda.AgreementDenied) as revoked:
        engine.content(link["id"], opened["session_id"], source="test")

    assert str(expired.value) == str(revoked.value)


def test_a_revoked_link_cannot_have_its_gate_changed(engine, links, room, nda_row):
    link = gated(engine, links, room, nda_row["id"])
    links.revoke_link(link["id"], source="test", actor="test")
    with pytest.raises(nda.AgreementDenied) as caught:
        engine.set_gate(link["id"], {nda.ENABLE_FIELD: False}, source="test", actor="test")
    assert caught.value.reason == nda.AgreementDenied.REASON_CLOSED


# --------------------------------------------------------------------------- #
# Content release
# --------------------------------------------------------------------------- #


def test_the_released_target_carries_the_remaining_gates(engine, links, room, store, nda_row):
    """The most dangerous kind of wrong on this page is implying the room is fully
    available when another gate is still owed.

    Accepting an NDA is not the same as clearing a password, and the response says
    so instead of letting the buyer find out at the document.
    """
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"], email="buyer@northwind.example")
    acceptance = engine.list_acceptances(room_id=room)[0]

    released = engine.content(link["id"], acceptance["session_id"], source="test")
    assert released["remaining_gates"] == list(
        link_gate.rules.steps_required(store.get(link["id"])["data"])
    )
    assert released["note"]


def test_the_content_is_resolved_from_the_link_target(engine, store, links, room, nda_row):
    document = store.create(
        "document", {"title": "Pricing", "body": "the deck"}, actor="test", source="test"
    )
    link = links.create_link(
        room,
        {"title": "Deck link", "document_id": document["id"]},
        source="test",
        actor="test",
    )
    engine.set_gate(link["id"], {nda.CONVENIENCE_FLAG: nda_row["id"]}, source="test", actor="test")
    accept_now(engine, link["id"])
    acceptance = engine.list_acceptances(room_id=room)[0]

    released = engine.content(link["id"], acceptance["session_id"], source="test")
    assert released["resolved"] is True
    assert released["content"]["title"] == "Pricing"
    assert released["target"]["kind"] == "document"


def test_a_target_that_is_gone_is_reported_unresolved_rather_than_crashing(
    engine, store, links, room, nda_row
):
    """`resolved: false` with a 200 is the honest answer. A 500 here would be the
    route falling over on a link whose subject was removed."""
    link = links.create_link(
        room,
        {"title": "Dangling", "document_id": "doc_absent"},
        source="test",
        actor="test",
    )
    engine.set_gate(link["id"], {nda.CONVENIENCE_FLAG: nda_row["id"]}, source="test", actor="test")
    accept_now(engine, link["id"])
    acceptance = engine.list_acceptances(room_id=room)[0]

    released = engine.content(link["id"], acceptance["session_id"], source="test")
    assert released["resolved"] is False
    assert released["content"] is None


def test_no_response_carries_a_token_hash(engine, links, room, store, nda_row):
    """The negative, asserted directly rather than by reading the code.

    The session token is stored only as a hash. `redact` strips the hash on the way
    out, so a future field cannot leak one by accident.
    """
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"])
    responses = [
        engine.open_link(link["id"], source="test"),
        engine.link_gate(link["id"]),
        engine.list_gates(room),
        engine.list_acceptances(room_id=room),
        engine.list_sessions(room_id=room),
        engine.summary(room),
    ]
    for response in responses:
        assert link_gate.redacted(response) is not None
        assert "token_hash" not in str(link_gate.redacted(response))


def test_the_cleartext_session_token_appears_only_in_the_response_that_issued_it(
    engine, links, room, store, nda_row
):
    link = gated(engine, links, room, nda_row["id"])
    opened = engine.open_link(link["id"], source="test")
    token = opened["session_token"]

    # Nothing written to the store holds it.
    assert store.find(nda.SESSION_COLLECTION, {"token_hash": token}, limit=1) == []
    session = store.get(opened["session_id"])["data"]
    assert token not in str(session)

    engine.accept(link["id"], opened["session_id"], {"accepted": True}, source="test")
    assert token not in str(engine.link_gate(link["id"]))
    assert token not in str(engine.list_sessions(room_id=room))


# --------------------------------------------------------------------------- #
# Reads and the board
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_states_the_board_shows(engine, links, room, nda_row):
    ungated = make_link(links, room, email_protected=False)
    gated_link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, gated_link["id"])

    counted = engine.summary(room)
    assert counted["agreements"] == 1
    assert counted["links"] == 2
    assert counted["gated"] == 1
    assert counted["ungated"] == 1
    assert counted["broken_gates"] == 0
    assert counted["acceptances"] == 1
    assert counted["accepted_sessions"] == 1
    assert counted["sessions"] == 1
    assert ungated["id"] != gated_link["id"]


def test_the_summary_counts_a_broken_gate(engine, links, room, nda_row):
    gated(engine, links, room, nda_row["id"])
    engine.delete_agreement(nda_row["id"], source="test", actor="test")
    assert engine.summary(room)["broken_gates"] == 1


def test_gates_can_be_listed_per_room(engine, links, room, store, nda_row):
    other = store.create("room", {"name": "Second room"}, actor="test", source="test")
    here = gated(engine, links, room, nda_row["id"])
    there = gated(engine, links, other["id"], nda_row["id"])

    assert [g["id"] for g in engine.list_gates(room)] == [here["id"]]
    assert [g["id"] for g in engine.list_gates(other["id"])] == [there["id"]]


def test_acceptances_can_be_filtered_by_link(engine, links, room, nda_row):
    first = gated(engine, links, room, nda_row["id"])
    second = gated(engine, links, room, nda_row["id"])
    accept_now(engine, first["id"])

    assert len(engine.list_acceptances(room_id=room)) == 1
    assert len(engine.list_acceptances(link_id=first["id"])) == 1
    assert engine.list_acceptances(link_id=second["id"]) == []


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_names_the_fields_the_research_names():
    """The create form and the gate must not disagree about which fields exist."""
    served = nda.vocabulary()
    assert set(served["fields"]) == {"enable_agreement", "agreement_id"}
    assert served["fields"]["enable_agreement"]["default"] is False
    assert served["fields"]["agreement_id"]["default"] is None
    assert served["convenience_flag"]["field"] == "agreement"
    assert set(served["kinds"]) == {"nda", "agreement"}
    assert nda.STATE_CLOSED in served["states"]


def test_the_vocabulary_says_what_was_deliberately_not_built():
    """A reviewer who cannot tell "we decided not to" from "we forgot to" has to go
    and read the source, and that is a cost paid every time."""
    not_implemented = nda.vocabulary()["not_implemented"]
    assert len(not_implemented) >= 3
    joined = " ".join(not_implemented).lower()
    assert "e-signature" in joined
    assert "plan" in joined


def test_the_vocabulary_records_what_the_acceptance_binds_to():
    assert "digest" in nda.vocabulary()["acceptance_binds_to"]


# --------------------------------------------------------------------------- #
# Audit and storage shape
# --------------------------------------------------------------------------- #


def test_every_write_goes_through_the_audited_database(store, links, room, nda_row):
    """The guarantee the product is built on: the audit row is written in the same
    transaction as the change.

    Read from ``store.db`` rather than from a second ``db`` fixture. The ``store``
    fixture is backed by ``memory_db``, so asking for both hands this test two
    different databases and the audit log it then asserted on was always empty.
    """
    engine = nda.AgreementEngine(store, now=lambda: NOW)
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"])
    engine.set_gate(link["id"], {nda.ENABLE_FIELD: False}, source="test", actor="test")

    actions = {row["action"] for row in store.db.audit()}
    assert "insert" in actions
    assert "update" in actions


def test_every_write_demands_an_audit_source(engine, links, room):
    """Hard rule 4: an audit row must name the route that served the write.

    ``source`` is a required keyword on every write, so a new caller cannot reach
    the store without saying where it came from. A default of ``""`` would be
    exactly the defect this prevents: an audit log full of writes that name no
    route at all.

    The route string itself is asserted in ``test_wf070_http.py``, where a real
    request produces one. Here the property is that the argument cannot be omitted.
    """
    link = make_link(links, room)
    calls = [
        lambda **kw: engine.create_agreement(room, {"title": "t", "body": "b"}, **kw),
        lambda **kw: engine.update_agreement("agr_absent", {"title": "t"}, **kw),
        lambda **kw: engine.set_gate(link["id"], {}, **kw),
        lambda **kw: engine.delete_agreement("agr_absent", **kw),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()  # no source=


def test_the_domain_module_imports_no_framework_and_no_database_handle():
    """The enforced guards, restated here so a change that breaks them fails in
    this file too.

    Checked against the parsed import list rather than the raw text, because a
    docstring that *names* ``dsr.api`` in order to explain why it is not imported
    is documentation, and a substring check fails on it.
    """
    tree = ast.parse(Path(nda.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for banned in ("fastapi", "dsr.api", "sqlite3", "pydantic"):
        assert not any(name == banned or name.startswith(f"{banned}.") for name in imported), banned


def test_the_feature_module_does_not_import_the_app_or_open_the_database():
    """The two guards CI enforces, checked against the feature module itself."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "import sqlite3" not in source
    assert "sqlite3.connect" not in source
    assert "from dsr.deps import" in source


def test_the_domain_module_depends_on_the_store_and_its_own_package():
    """`dsr.store` for the store, `dsr.link_gating.rules` for the link collection
    name and the expiry boundary rule. Importing the link collection name rather
    than repeating the literal is what stops the two gates drifting apart."""
    imports = [
        line.strip()
        for line in Path(nda.__file__).read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    ]
    assert imports == [
        "import hashlib",
        "from collections.abc import Mapping",
        "from datetime import datetime, timezone",
        "from typing import Any, Callable",
        "from dsr.link_gating import rules, secrets as link_secrets",
        "from dsr.store import RecordStore",
    ], imports


def test_the_records_are_ordinary_json_with_no_typed_column(engine, store, links, room, nda_row):
    """A team adding a field must need no coordination with anyone."""
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"], email="buyer@northwind.example")

    acceptance = store.find(nda.ACCEPTANCE_COLLECTION, {}, limit=1)[0]
    assert acceptance["collection"] == nda.ACCEPTANCE_COLLECTION
    # Arbitrary extra fields ride along with no migration.
    store.update(
        acceptance["id"], {"our_team_field": {"nested": [1, 2]}}, actor="test", source="test"
    )
    assert store.get(acceptance["id"])["data"]["our_team_field"] == {"nested": [1, 2]}


def test_the_acceptance_is_filterable_by_dotted_json_paths(engine, store, links, room, nda_row):
    """`find()` resolving a dotted path is what makes the store schema-flexible in
    practice rather than only in principle."""
    link = gated(engine, links, room, nda_row["id"])
    accept_now(engine, link["id"], email="buyer@northwind.example")
    found = store.find(nda.ACCEPTANCE_COLLECTION, {"email": "buyer@northwind.example"}, limit=5)
    assert len(found) == 1


def test_rooms_are_filterable_even_though_room_id_is_part_of_the_envelope(
    engine, store, links, room, nda_row
):
    """`room_id` is stripped from `data` before the index is built, so the payload
    needs its own key. Without it "filter by room" silently returns nothing."""
    gated(engine, links, room, nda_row["id"])
    assert store.count_where(nda.AGREEMENT_COLLECTION, {link_gate.rules.ROOM_REF: room}) == 1
    assert store.count_where(nda.AGREEMENT_COLLECTION, {link_gate.rules.ROOM_REF: "r_other"}) == 0


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def seed_context(rooms: list[str]) -> dict:
    return {
        "room_ids": [(room, "Account") for room in rooms],
        "now": NOW,
        "rng": random.Random("wf070"),
    }


@pytest.fixture()
def seeded(db: AuditedDatabase) -> tuple[ModuleType, RecordStore, str, list[str]]:
    """Run ``seed(db, context)`` once and hand back everything it produced.

    One database, on purpose. The ``store`` fixture is backed by ``memory_db``, so
    asking for both this fixture and ``store`` gives two databases, and a seed
    writing into one while the assertions read the other produces a suite that
    passes a green build with nothing in it.
    """
    feature: ModuleType = load_feature(MODULE)
    store = RecordStore(db)
    rooms = [
        store.create("room", {"name": name}, actor="test", source="test")["id"]
        for name in ("First", "Second")
    ]
    return feature, store, feature.seed(db, seed_context(rooms)), rooms


def test_the_seed_reports_the_states_it_created(seeded):
    """A feature whose page is empty in the demo is a feature nobody can review."""
    _feature, store, reported, _rooms = seeded
    assert "agreements" in reported
    assert "gate(s) failing closed" in reported
    assert store.count_where(nda.AGREEMENT_COLLECTION, {}) >= 1


def test_the_seed_creates_every_state_the_page_shows(seeded):
    """Five states the board renders. A seed that only produces the happy path
    teaches a reviewer nothing."""
    _feature, store, _reported, _rooms = seeded
    engine = nda.AgreementEngine(store, now=lambda: NOW)
    gates = engine.list_gates()
    assert any(g["gate"]["enabled"] for g in gates), "no gated link"
    assert any(not g["gate"]["enabled"] for g in gates), "no ungated link"
    assert any(not g["gate"]["agreement_ok"] for g in gates), "no fail-closed gate"
    assert engine.list_acceptances(), "no acceptance recorded"

    # The revised state: an acceptance exists whose digest no longer matches the
    # text it was given under, which is the rule the whole workflow turns on.
    live = {
        row["id"]: row["data"]["body_digest"]
        for row in store.find(nda.AGREEMENT_COLLECTION, {}, limit=20)
    }
    superseded = [
        row
        for row in store.find(nda.ACCEPTANCE_COLLECTION, {}, limit=20)
        if row["data"]["body_digest"] != live.get(row["data"]["agreement_id"])
    ]
    assert superseded, "no acceptance left behind by an amended agreement"


def test_the_seed_runs_the_real_gate_rather_than_writing_rows_by_hand(seeded):
    """A demo row claiming an acceptance that was never recorded is the one thing
    this workflow exists to prevent, so the seed produces its states through the
    engine and the sessions it leaves behind are the proof."""
    _feature, store, _reported, _rooms = seeded
    accepted = store.find(nda.SESSION_COLLECTION, {"accepted": True}, limit=10)
    assert accepted, "the seed claims an acceptance but left no accepted session"
    for row in accepted:
        assert row["data"]["acceptance_id"]


def test_the_seed_writes_through_the_audited_database(seeded):
    _feature, store, _reported, _rooms = seeded
    sources = {row["source"] for row in store.db.audit()}
    # No route served the demo data, and claiming one would be the lie hard rule 4
    # exists to prevent.
    assert "seed" in sources
    assert not any(str(source).startswith(("GET ", "POST ")) for source in sources)


def test_the_seed_does_nothing_without_a_room(db: AuditedDatabase):
    feature: ModuleType = load_feature(MODULE)
    assert feature.seed(db, {"room_ids": [], "now": NOW, "rng": None}) == ""


def test_every_character_of_the_seed_string_is_encodable_by_cp1252(seeded):
    """A single U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire
    seeder on a Windows console. Tested by encoding it, not by reading it."""
    _feature, _store, reported, _rooms = seeded
    reported.encode("cp1252")
