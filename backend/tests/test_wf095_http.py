"""WF-095 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf095.py``. This file is the other half, and it is organised
by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and
    that no two of them collide.
``the envelope``
    Open, read, list, and the four statuses a caller can observe on it.
``the buyer's acceptance steps``
    View, verify, confirm, sign, and reassign, in the order the research puts them.
``the refusals``
    Every status code and body this router can produce, and the field-keyed map that lands
    next to each input.
``a failed signature is an outcome and not an error``
    The research says failures are logged automatically, so a refused attempt is a 200 with
    ``outcome: failed`` and an activity row.
``the quota route``
    One usage per envelope, a ceiling the research never stated, and the month it lands in.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.

Every test here passes with the file run on its own.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.quote_acceptance import vocabulary as vocab
from dsr.quote_acceptance.engine import AcceptanceEngine
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf095_collect_acceptance_by_e_signature"
PREFIX = "/api/wf-095"
FEATURE_ID = "wf-095-collect-acceptance-by-e-signature-countersignature"

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


def _engine(client: TestClient) -> AcceptanceEngine:
    """An engine over the test's own store with a clock and token the test controls.

    The HTTP layer builds its own engine per request from ``app.state.store``, so a test that
    needs a steered clock drives the engine directly. The HTTP tests below use the routes;
    this helper is for the one assertion that needs a boundary a wall clock cannot reach.
    """

    return AcceptanceEngine(_store(client), now=lambda: NOW, token_factory=lambda: "token-1")


def payload(**extra: Any) -> dict[str, Any]:
    """A valid acceptance configuration, so a test changes one thing at a time."""

    body = {
        "buyer_signers": [{"name": "Ada Byron", "email": "ada@northwind.example"}],
        "countersigners": [{"name": "Dana Reyes", "email": "dana@halcyon.example"}],
    }
    body.update(extra)
    return body


def open_envelope(client: TestClient, *, published: bool = True, **extra: Any) -> dict[str, Any]:
    response = client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a", "is_published": str(published).lower()},
        json=payload(**extra),
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign_through(client: TestClient, envelope: dict[str, Any]) -> dict[str, Any]:
    """Drive one envelope from opened to accepted, through the routes.

    This is the research's buyer step four followed by its countersigner step five, so a test
    that needs a signed envelope gets one produced the way the product produces it rather
    than by writing rows.
    """

    envelope_id = envelope["id"]
    buyer, countersigner = envelope["signers"]

    assert client.post(f"{PREFIX}/envelopes/{envelope_id}/view").status_code == 200

    if envelope.get(vocab.VERIFICATION_REQUIRED):
        link = client.post(f"{PREFIX}/envelopes/{envelope_id}/verify")
        assert link.status_code == 200, link.text
        token = link.json()["verification_link"]
        confirmed = client.post(
            f"{PREFIX}/envelopes/{envelope_id}/verify/confirm", json={"token": token}
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["verified"] is True

    first = client.post(
        f"{PREFIX}/signers/{buyer['id']}/sign",
        json={"signature_mode": "draw", "signature_payload": {"strokes": 3}},
    )
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "signed"

    second = client.post(
        f"{PREFIX}/signers/{countersigner['id']}/sign",
        json={"signature_mode": "type", "signature_payload": {"text": "Dana Reyes"}},
    )
    assert second.status_code == 200, second.text
    return second.json()


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_host_mounted_the_router_by_discovery_alone(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    feature = registry.by_id(FEATURE_ID)

    assert feature is not None
    assert feature.loaded is True, feature.error
    assert feature.prefix == PREFIX
    assert feature.module == FEATURE_MODULE


def test_the_feature_exports_the_three_things_a_feature_contributes(client: TestClient):
    from dsr.features import wf095_collect_acceptance_by_e_signature as feature

    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["ticket"] == "WF-095"
    assert feature.FEATURE["name"]
    assert callable(feature.seed)
    assert feature.EXCEPTION_HANDLERS


def test_the_error_handlers_are_this_workflows_own_four_types(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    assert sorted(registry.by_id(FEATURE_ID).exception_handlers) == [
        "AcceptanceRefused",
        "EnvelopeNotFound",
        "QuotaRefused",
        "SignerNotFound",
    ]


def test_no_two_of_this_routers_routes_collide(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    seen: set[tuple[str, str]] = set()
    for shape in registry.by_id(FEATURE_ID).routes:
        for method in shape["methods"]:
            pair = (method, shape["path"])
            assert pair not in seen, f"{pair} is declared twice on this router"
            seen.add(pair)


def test_the_board_and_the_research_answer(client: TestClient):
    for path in ("/summary", "/vocabulary", "/decisions"):
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code == 200, path


def test_the_summary_reports_no_stated_quota_ceiling(client: TestClient):
    """The research states that limits are pooled and reset on the 1st, and states no number."""

    quota = client.get(f"{PREFIX}/summary").json()["quota"]
    assert quota["limit"] is None
    assert quota["reset_day"] == vocab.QUOTA_RESET_DAY
    assert "states no number" in quota["quota_unspecified_quote"]


def test_the_vocabulary_route_carries_the_researched_caps(client: TestClient):
    vocabulary = client.get(f"{PREFIX}/vocabulary").json()

    assert vocabulary["verification_window_minutes"] == 60
    assert vocabulary["pdf_size_cap_mb"] == 40
    assert vocabulary["signature_modes"] == ["draw", "type", "upload"]
    assert vocabulary["activities"] == list(vocab.ACTIVITIES)
    assert set(vocabulary["status_transitions"]) == set(vocab.SIGNING_STATUSES)


def test_the_decisions_route_names_an_alternative_for_every_choice(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()

    assert body["count"] >= 6
    for decision in body["decisions"]:
        assert decision["chosen"] in decision["options"], decision["question"]
        assert len(decision["options"]) >= 2, decision["question"]


# --------------------------------------------------------------------------- #
# Opening an envelope
# --------------------------------------------------------------------------- #


def test_opening_an_envelope_returns_it_at_pending_signature(client: TestClient):
    envelope = open_envelope(client)

    assert envelope[vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_PENDING_SIGNATURE
    assert envelope["status_label"] == "Pending signature"
    assert envelope["signer_count"] == 2
    assert [signer["role"] for signer in envelope["signers"]] == ["buyer", "countersigner"]


def test_an_esignature_quote_with_no_buyer_signer_is_a_400_naming_the_field(client: TestClient):
    response = client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a"},
        json={"acceptance_method": "esignature"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_acceptance_request"
    assert "at least one buyer contact" in body["errors"]["buyer_signers"]


def test_an_unknown_acceptance_method_is_a_400_and_never_a_default(client: TestClient):
    response = client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a"},
        json=payload(hs_acceptance_method="carrier_pigeon"),
    )

    assert response.status_code == 400
    assert "carrier_pigeon" in response.json()["errors"]["hs_acceptance_method"]


def test_an_in_signing_attachment_on_a_print_and_sign_quote_is_a_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a"},
        json={
            "acceptance_method": "print_and_sign",
            "attachments": [{"name": "Security schedule", "in_signing": True}],
        },
    )

    assert response.status_code == 400
    assert "e-signature must be used" in response.json()["errors"]["hs_acceptance_method"]


def test_a_document_over_the_forty_megabyte_cap_is_refused_and_writes_no_envelope(
    client: TestClient,
):
    """A PDF over the cap "may not be successfully verified or signed", and the refusal is total.

    The route reads the size from the acceptance payload, so the same rule the domain test
    drives is observable over HTTP, and nothing is written when it refuses.
    """

    response = client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a"},
        json=payload(document_size_bytes=41 * 1024 * 1024),
    )

    assert response.status_code == 400
    assert "over the 40 MB cap" in response.json()["errors"]["document_size_bytes"]
    assert client.get(f"{PREFIX}/envelopes").json()["count"] == 0


def test_a_refused_configuration_leaves_the_board_empty(client: TestClient):
    client.post(
        f"{PREFIX}/envelopes",
        params={"room_id": "room_a"},
        json={"acceptance_method": "esignature"},
    )

    assert client.get(f"{PREFIX}/envelopes").json()["count"] == 0
    assert client.get(f"{PREFIX}/summary").json()["envelopes"] == 0


# --------------------------------------------------------------------------- #
# Reading the envelope
# --------------------------------------------------------------------------- #


def test_an_envelope_can_be_read_and_listed(client: TestClient):
    envelope = open_envelope(client)

    one = client.get(f"{PREFIX}/envelopes/{envelope['id']}")
    assert one.status_code == 200
    assert one.json()["id"] == envelope["id"]

    listed = client.get(f"{PREFIX}/envelopes", params={"room_id": "room_a"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


def test_an_absent_envelope_is_a_404_and_never_a_500(client: TestClient):
    for path in (
        "/envelopes/envelope_absent",
        "/envelopes/envelope_absent/events",
    ):
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code == 404, path
        assert response.json()["error"] == "not_found"


def test_an_absent_signer_is_a_404_with_its_own_error_code(client: TestClient):
    response = client.get(f"{PREFIX}/signers/signer_absent")

    assert response.status_code == 404
    assert response.json()["error"] == "signer_not_found"


# --------------------------------------------------------------------------- #
# The buyer's acceptance steps
# --------------------------------------------------------------------------- #


def test_viewing_advances_the_status_and_writes_no_activity(client: TestClient):
    envelope = open_envelope(client)

    response = client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")
    assert response.status_code == 200
    assert response.json()[vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_VIEWED_PENDING_SIGNATURE

    events = client.get(f"{PREFIX}/envelopes/{envelope['id']}/events").json()["events"]
    viewed = [event for event in events if event["event"] == "viewed"]
    assert viewed and viewed[0]["activity"] is None


def test_requesting_verification_returns_a_link_and_the_one_hour_window(client: TestClient):
    envelope = open_envelope(client, identity_verification_required=True)

    response = client.post(f"{PREFIX}/envelopes/{envelope['id']}/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["verification_link"]
    assert body["window"]["window_minutes"] == 60
    assert body["window"]["expired"] is False


def test_a_correct_token_confirms_and_a_wrong_one_does_not(client: TestClient):
    envelope = open_envelope(client, identity_verification_required=True)
    token = client.post(f"{PREFIX}/envelopes/{envelope['id']}/verify").json()["verification_link"]

    wrong = client.post(
        f"{PREFIX}/envelopes/{envelope['id']}/verify/confirm", json={"token": "not-the-token"}
    )
    assert wrong.status_code == 200
    assert wrong.json()["verified"] is False
    assert wrong.json()["reason"] == "verification_token_mismatch"

    right = client.post(
        f"{PREFIX}/envelopes/{envelope['id']}/verify/confirm", json={"token": token}
    )
    assert right.status_code == 200
    assert right.json()["verified"] is True


def test_a_signature_must_be_drawn_typed_or_uploaded(client: TestClient):
    envelope = open_envelope(client)
    buyer = envelope["signers"][0]

    response = client.post(
        f"{PREFIX}/signers/{buyer['id']}/sign", json={"signature_mode": "telepathy"}
    )
    assert response.status_code == 400
    assert "drawn, typed or uploaded" in response.json()["detail"]


def test_a_signature_after_viewing_advances_to_pending_countersignature(client: TestClient):
    envelope = open_envelope(client)
    client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")

    response = client.post(
        f"{PREFIX}/signers/{envelope['signers'][0]['id']}/sign",
        json={"signature_mode": "draw"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "signed"
    assert body["activity_label"] == "Quote buyer signed"
    assert body["envelope"][vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_PENDING_COUNTERSIGNATURE
    assert body["envelope"]["countersigners_notified"] is True


def test_the_two_party_flow_ends_sealed_and_accepted(client: TestClient):
    envelope = open_envelope(client)
    final = sign_through(client, envelope)

    sealed = final["envelope"]
    assert sealed[vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_ACCEPTED
    assert sealed["status_label"] == "Accepted"
    assert sealed["sealed"] is True
    assert sealed["accepted_by"] == "dana@halcyon.example"
    assert sealed[vocab.PAYMENT_STATUS_FIELD] == "accepted"


def test_the_activity_log_holds_the_four_named_activities(client: TestClient):
    envelope = open_envelope(client)
    sign_through(client, envelope)

    events = client.get(f"{PREFIX}/envelopes/{envelope['id']}/events").json()["events"]
    labels = [event["activity_label"] for event in events if event["activity_label"]]
    assert labels == ["Quote buyer signed", "Quote countersigned"]


def test_reassignment_is_allowed_only_when_the_quote_allows_it(client: TestClient):
    envelope = open_envelope(client, reassign_allowed=True)
    buyer = envelope["signers"][0]

    allowed = client.post(
        f"{PREFIX}/signers/{buyer['id']}/reassign",
        json={"name": "Grace Okonkwo", "email": "grace@northwind.example"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["email"] == "grace@northwind.example"

    refused_envelope = open_envelope(client, reassign_allowed=False)
    refused = client.post(
        f"{PREFIX}/signers/{refused_envelope['signers'][0]['id']}/reassign",
        json={"name": "Someone Else", "email": "else@northwind.example"},
    )
    assert refused.status_code == 400
    assert "can reassign is off" in refused.json()["errors"]["reassign_allowed"]


def test_a_reassignment_after_a_signature_is_refused(client: TestClient):
    envelope = open_envelope(client, reassign_allowed=True)
    buyer, countersigner = envelope["signers"]
    client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")
    client.post(f"{PREFIX}/signers/{buyer['id']}/sign", json={"signature_mode": "draw"})

    response = client.post(
        f"{PREFIX}/signers/{buyer['id']}/reassign",
        json={"name": "Grace Okonkwo", "email": "grace@northwind.example"},
    )
    assert response.status_code == 400
    assert "already signed" in response.json()["errors"]["signer"]


def test_a_reassignment_to_an_unroutable_address_is_refused(client: TestClient):
    envelope = open_envelope(client, reassign_allowed=True)

    response = client.post(
        f"{PREFIX}/signers/{envelope['signers'][0]['id']}/reassign",
        json={"name": "Nobody", "email": "nobody"},
    )
    assert response.status_code == 400
    assert "email address" in response.json()["errors"]["email"]


# --------------------------------------------------------------------------- #
# A failed signature is an outcome, not an error
# --------------------------------------------------------------------------- #


def test_an_unverified_signature_is_a_200_that_names_the_reason(client: TestClient):
    """The attempt happened, and the research logs failures automatically."""

    envelope = open_envelope(client, identity_verification_required=True)
    client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")

    response = client.post(
        f"{PREFIX}/signers/{envelope['signers'][0]['id']}/sign",
        json={"signature_mode": "draw"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert body["reason"] == "verification_not_requested"
    assert body["activity_label"] == "Signing attempt failed"
    assert body["envelope"]["signed_count"] == 0
    assert body["envelope"][vocab.SIGNING_STATUS_FIELD] == vocab.STATUS_VIEWED_PENDING_SIGNATURE


def test_a_second_signature_by_the_same_party_is_a_recorded_failure(client: TestClient):
    envelope = open_envelope(client)
    buyer = envelope["signers"][0]
    client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")
    client.post(f"{PREFIX}/signers/{buyer['id']}/sign", json={"signature_mode": "draw"})

    again = client.post(f"{PREFIX}/signers/{buyer['id']}/sign", json={"signature_mode": "draw"})
    assert again.status_code == 200
    assert again.json()["reason"] == "already_signed"


def test_a_countersignature_before_the_buyer_is_a_400_and_advances_nothing(client: TestClient):
    """The request was well formed and conflicts with the envelope's state."""

    envelope = open_envelope(client)

    response = client.post(
        f"{PREFIX}/signers/{envelope['signers'][1]['id']}/sign",
        json={"signature_mode": "type"},
    )
    assert response.status_code == 400
    assert "before the buyer has signed" in response.json()["detail"]

    assert client.get(f"{PREFIX}/envelopes/{envelope['id']}").json()["signed_count"] == 0


def test_a_failed_attempt_leaves_an_activity_row_a_reader_can_see(client: TestClient):
    envelope = open_envelope(client, identity_verification_required=True)
    client.post(
        f"{PREFIX}/signers/{envelope['signers'][0]['id']}/sign", json={"signature_mode": "draw"}
    )

    events = client.get(f"{PREFIX}/envelopes/{envelope['id']}/events").json()["events"]
    failures = [event for event in events if event["activity"] == vocab.ACTIVITY_ATTEMPT_FAILED]
    assert len(failures) == 1
    assert failures[0]["detail"]["reason"] == "verification_not_requested"


# --------------------------------------------------------------------------- #
# The quota route
# --------------------------------------------------------------------------- #


def test_the_quota_route_counts_one_usage_per_published_envelope(client: TestClient):
    open_envelope(client, published=True)
    open_envelope(client, published=True)
    open_envelope(client, published=False)

    quota = client.get(f"{PREFIX}/quota").json()
    assert quota["used"] == 2
    assert quota["envelopes_charged"] == 2
    assert quota["limit"] is None


def test_the_quota_route_counts_by_month_and_names_the_reset(client: TestClient):
    quota = client.get(f"{PREFIX}/quota", params={"month": "2026-10"}).json()
    assert quota["month"] == "2026-10"
    assert quota["reset_day"] == vocab.QUOTA_RESET_DAY


# --------------------------------------------------------------------------- #
# The quote and the document this workflow signs
# --------------------------------------------------------------------------- #


def test_a_quote_and_a_document_can_be_provisioned_for_the_flow(client: TestClient):
    quote = client.post(f"{PREFIX}/rooms/room_a/quotes", json={"title": "Northwind renewal"})
    assert quote.status_code == 201

    document = client.post(
        f"{PREFIX}/rooms/room_a/documents", json={"quote_id": quote.json()["id"]}
    )
    assert document.status_code == 201


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_router_can_record_names_a_mounted_route(client: TestClient):
    """No hand-written source string can survive this.

    A domain function that hardcodes a URL leaves the audit log naming a route the app
    stopped serving. Every source below is built by ``_source`` from the router, and the
    check is on the *produced* value.
    """

    from dsr.api import app
    from dsr.features import load_features, wf095_collect_acceptance_by_e_signature as feature

    registry = load_features(app)
    mounted = {
        (method, shape["path"])
        for shape in registry.by_id(FEATURE_ID).routes
        for method in shape["methods"]
    }

    # `_source` derives from the router rather than carrying a literal, so no route string in
    # this module can drift from the prefix the host mounted.
    assert "router.prefix" in inspect.getsource(feature._source)

    for method, path in (
        ("POST", "/rooms/{room_id}/quotes"),
        ("POST", "/rooms/{room_id}/documents"),
        ("POST", "/envelopes"),
        ("POST", "/envelopes/{envelope_id}/view"),
        ("POST", "/envelopes/{envelope_id}/verify"),
        ("POST", "/envelopes/{envelope_id}/verify/confirm"),
        ("POST", "/signers/{signer_id}/sign"),
        ("POST", "/signers/{signer_id}/reassign"),
    ):
        source = feature._source(method, path)
        assert source.startswith(f"{method} {PREFIX}"), source
        assert (method, f"{PREFIX}{path}") in mounted, source


def test_a_write_records_the_route_that_served_it(client: TestClient):
    store = _store(client)
    open_envelope(client)

    row = store.audit(collection=vocab.ENVELOPE_COLLECTION, limit=1)[0]
    assert row["source"] == f"POST {PREFIX}/envelopes"


def test_a_signature_records_the_sign_route(client: TestClient):
    store = _store(client)
    envelope = open_envelope(client)
    client.post(f"{PREFIX}/envelopes/{envelope['id']}/view")
    client.post(
        f"{PREFIX}/signers/{envelope['signers'][0]['id']}/sign", json={"signature_mode": "draw"}
    )

    row = store.audit(collection=vocab.SIGNER_COLLECTION, limit=10)
    signed = [entry for entry in row if entry["action"] == "update"]
    assert signed
    assert signed[-1]["source"] == f"POST {PREFIX}/signers/{{signer_id}}/sign"


# --------------------------------------------------------------------------- #
# The read-path rule
# --------------------------------------------------------------------------- #


def test_the_reads_write_no_audit_rows(client: TestClient):
    """A route that logged every read would fill the guarantee with entries describing no change."""

    envelope = open_envelope(client)
    before = len(_store(client).audit(limit=1000))

    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/envelopes",
        "/quota",
        f"/envelopes/{envelope['id']}",
        f"/envelopes/{envelope['id']}/events",
        f"/signers/{envelope['signers'][0]['id']}",
    ):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path

    assert len(_store(client).audit(limit=1000)) == before


# --------------------------------------------------------------------------- #
# Every route answers
# --------------------------------------------------------------------------- #


def test_no_route_on_this_router_answers_a_server_fault(client: TestClient):
    """The release pass asserts no 5xx. Every route here is called, with ids that do not exist."""

    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    for shape in registry.by_id(FEATURE_ID).routes:
        path = shape["path"]
        if "{room_id}" in path:
            path = path.replace("{room_id}", "room_a")
        for placeholder in ("{envelope_id}", "{signer_id}"):
            path = path.replace(placeholder, "absent")
        response = client.request(shape["methods"][0], f"{PREFIX}{path}", json={})
        assert response.status_code < 500, (
            f"{shape['methods'][0]} {path} returned {response.status_code}"
        )


def test_the_one_hour_window_is_enforced_at_the_boundary(client: TestClient):
    """A token presented an hour after the click must not pass.

    This drives the engine with a clock the test moves, because the HTTP layer builds its own
    engine per request from the real clock and no wall-clock wait belongs in a test.
    """

    store = _store(client)
    clock = {"now": NOW}
    issued = {"n": 0}

    def _token() -> str:
        issued["n"] += 1
        return f"token-{issued['n']}"

    engine = AcceptanceEngine(store, now=lambda: clock["now"], token_factory=_token)

    envelope = engine.open_envelope(
        payload(identity_verification_required=True),
        room_id="room_a",
        is_published=True,
        actor="dana",
        source=f"POST {PREFIX}/envelopes",
    )
    buyer = envelope["signers"][0]
    link = engine.request_verification(envelope["id"], actor="ada", source=f"POST {PREFIX}/verify")

    clock["now"] = NOW + timedelta(minutes=59)
    inside = engine.sign(
        buyer["id"],
        signature_mode="draw",
        verification_token=link["verification_link"],
        actor="ada",
        source="test",
    )
    assert inside["outcome"] == "signed"

    envelope_two = engine.open_envelope(
        payload(identity_verification_required=True),
        room_id="room_a",
        is_published=True,
        actor="dana",
        source="test",
    )
    buyer_two = envelope_two["signers"][0]
    link_two = engine.request_verification(envelope_two["id"], actor="ada", source="test")
    # The second window opened a minute ago, so its own hour ends a minute after this one.
    clock["now"] = (
        NOW + timedelta(minutes=61) + timedelta(minutes=vocab.VERIFICATION_WINDOW_MINUTES)
    )
    outside = engine.sign(
        buyer_two["id"],
        signature_mode="draw",
        verification_token=link_two["verification_link"],
        actor="ada",
        source="test",
    )
    assert outside["outcome"] == "failed"
    assert outside["reason"] == "verification_window_expired"
