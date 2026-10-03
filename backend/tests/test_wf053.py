"""Tests for WF-053: route a booking to the owner of the CRM record.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-053.md``. Nothing here is a
preference of this build unless it is listed in
:mod:`dsr.ownership_routing.inferences`, and every inference in that registry has
a test that checks it is still named, still bounded and still changeable.

The researched half
-------------------

* "**Ownership** - routes to the owner of the guest's CRM record (lead, contact, or
  account owner), resolved at booking time."
* "For **Ownership** links, also pass ``guestEmail`` in the init call - it is
  required so Chili Piper can resolve the owner from your CRM."
* "The rule example below checks whether one of the reps from Team A owns the
  Lead, Contact **or** Account object associated with the prospect."
* "you should not use this node in **Ownership** paths. If you have any Assign To
  nodes in Ownership-related paths, this could prevent your Router from being
  published."
* "For Lead-to-Account (L2A) Matching in **Salesforce**, we will use the existing
  settings defined in your workspace."
* "Available ``startTimes`` plus a ``routingId`` for the second call", then
  ``schedule-simple`` with ``{startTime, guestEmail}``.
* "routes may be pre-resolved from your own CRM ... rather than letting Chili Piper
  do the lookup."

Four bugs these tests were written to catch, three of which shipped during the
build and would not have been visible without a behavioural test:

* ``route`` and ``resolve`` read ``rules`` / ``resolution_order`` off the store's
  *envelope* rather than its payload. Every link then took the no-chain branch,
  which routes to the resolved owner - so it looked right while making the
  researched catch-all unreachable. The page would have shown every prospect
  reaching the deal desk, and nothing would have failed.
* The catch-all's ``outcome`` was computed after the catch-all itself had been
  appended to the considered list, so every fall-through reported as ``resolved``
  and the one distinction the page draws disappeared.
* The catch-all's ``owner_id`` was dropped when building the reported rule, so a
  chain that fell through returned an empty owner and the booking went nowhere.
* The summary counted ``links_with_chain`` off the envelope too, reporting zero
  links with a chain on a store where both had one.

The HTTP half runs against the real app over a temporary database, the way
``test_features.py`` does, and asserts that every ``source`` recorded in the audit
log names a route the host actually mounted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.ownership_routing import (
    BOOKING_COLLECTION,
    CALENDAR_PROVIDERS,
    CATCH_ALL,
    CRM_OBJECT_TYPES,
    DECISION_COLLECTION,
    DISCOVERY_OPERATION,
    EDGE_ENDPOINTS,
    FORBIDDEN_ON_OWNERSHIP_PATH,
    FORBIDDEN_QUOTE,
    INIT_REQUIRED_FIELDS,
    LINK_COLLECTION,
    LINK_HISTORY_LIMIT,
    LINK_TYPES,
    OWNERSHIP,
    RECORD_COLLECTION,
    REP_COLLECTION,
    RESOLUTION_ORDER_RATIONALE,
    RESOLUTION_OUTCOMES,
    RESOLUTION_SOURCES,
    ROUTE_COLLECTION,
    RULE_KINDS,
    SCHEDULE_REQUIRED_FIELDS,
    SUPPORTED_LINK_TYPES,
    AmbiguousOwner,
    BookingStateError,
    CalendarNotConnected,
    ForbiddenNodeOnOwnershipPath,
    GuestEmailRequired,
    GuestMismatch,
    IntervalError,
    LinkError,
    LinkTypeNotSupported,
    NoOwnerResolved,
    OwnershipEngine,
    OwnershipError,
    OwnerUnknown,
    RouteConsumed,
    RouteNotFound,
    RulesDoNotFallThrough,
    SlotNotOffered,
    calendars as cal,
    inferences as ownership_inferences,
    rules as ownership_rules,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to the
#: prefix has to be made deliberately in the test as well, which is the point of a
#: test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-053"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/init-simple"
BOOK_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/schedule-simple"
LINK_SOURCE = f"POST {PREFIX}/links"

MODULE = "wf053_route_a_booking_to_the_owner_of_the_cr"
FEATURE_ID = "wf-053-route-a-booking-to-the-owner-of-the-cr"

#: A fixed moment, so "the next Monday" is a fact rather than a race with the wall
#: clock. ``now`` is a Monday at 08:00 UTC, which leaves the whole working week in
#: front of every slot assertion below.
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "wf053.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return OwnershipEngine(store, clock=lambda: NOW)


@pytest.fixture()
def room(store):
    return store.create(
        "room", {"name": "Northwind — Enterprise Evaluation", "account": "Northwind"}, actor="dana"
    )


@pytest.fixture()
def other_room(store):
    return store.create(
        "room", {"name": "Contoso Health — Security Review", "account": "Contoso"}, actor="sam"
    )


@pytest.fixture()
def http(monkeypatch, tmp_path):
    """A client over a temporary database.

    ``get_engine`` is a FastAPI dependency, so the real routes already build the
    engine from ``StoreDep`` and the suite needs no override: the engine holds
    nothing beyond the store, so the production path and the test path are the same
    path.
    """
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", tmp_path / "absent-frontend")
    with TestClient(app) as client:
        yield client


def mounted_routes(client, feature_id=FEATURE_ID):
    """Every (method, path) the host mounted for one feature, templates intact."""
    entry = next(
        (f for f in client.get("/api/features").json()["features"] if f["id"] == feature_id), None
    )
    assert entry is not None, f"{feature_id} is not mounted"
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def all_served_routes(client):
    """Every (method, path) the running app serves, core routes included.

    The audit log is shared, so a check of what it records has to be allowed to see
    a core write as well as a feature one.
    """
    routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, getattr(route, "path", "")))
    for feature in client.get("/api/features").json()["features"]:
        for route in feature["routes"]:
            for method in route["methods"]:
                routes.add((method, route["path"]))
    return routes


def source_names_a_mounted_route(source, routes):
    """Does ``"POST /api/wf-053/rooms/abc/init-simple"`` name a route that exists?

    A recorded source carries concrete ids; a mounted path carries FastAPI's
    ``{param}`` placeholders. The pattern is built from the *template* and matched
    against the source, with each placeholder as one wildcard segment, and the
    literal parts escaped so a segment that happens to contain a regex
    metacharacter cannot make the pattern match something else.
    """
    method, _, path = source.partition(" ")
    for mounted_method, template in routes:
        if mounted_method != method:
            continue
        parts = re.split(r"(\{[^}]+\})", template)
        pattern = (
            "^"
            + "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
            + "$"
        )
        if re.match(pattern, path):
            return True
    return False


# --------------------------------------------------------------------------- #
# Payload builders
# --------------------------------------------------------------------------- #


def interval(**overrides):
    """A window starting tomorrow and running a week, 30-minute meetings.

    A function rather than a constant so a test that changes one field changes one
    field, and so nothing silently depends on the wall clock.
    """
    payload = {
        "start": (NOW + timedelta(days=1)).isoformat(),
        "end": (NOW + timedelta(days=8)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
    }
    payload.update(overrides)
    return payload


def rep_row(owner_id="005-nadia", **overrides):
    """A rep with a connected Google calendar - the shape a resolution can book."""
    row = {
        "owner_id": owner_id,
        "name": "Nadia A. Farouk",
        "email": f"{owner_id}@soluspring.example",
        "team": "Enterprise",
        "calendar": {"provider": "google", "connected": True},
    }
    calendar = {**row.get("calendar", {}), **overrides.pop("calendar", {})}
    row.update(overrides)
    row["calendar"] = calendar
    return row


def add_rep(store, owner_id="005-nadia", **overrides):
    return store.create(
        REP_COLLECTION, rep_row(owner_id, **overrides), actor="dana", source=LINK_SOURCE
    )


def set_calendar(store, engine, owner_id, calendar):
    """Replace one rep's calendar in place.

    A *patch* rather than another create, because a second row with the same owner
    id is the ambiguity this package refuses - so "this rep has no calendar now"
    has to be expressed as a change to their row, not as a second one.
    """
    record_id = engine.crm.rep(owner_id)["id"]
    return store.update(record_id, {"calendar": calendar}, actor="dana", source=LINK_SOURCE)


def add_record(store, *, email=None, owner_id="005-nadia", object_type="lead", **overrides):
    """A CRM row the lookup reads. ``email`` is indexed under its own field."""
    row = {
        "object_type": object_type,
        "email": email or "lead@example.com",
        "name": "Marguerite Oyelaran",
        "owner_id": owner_id,
    }
    row.update(overrides)
    return store.create(RECORD_COLLECTION, row, actor="dana", source=LINK_SOURCE)


def add_link(engine, **overrides):
    """A declared Ownership link. Defaults make it immediately bookable."""
    payload = {
        "type": OWNERSHIP,
        "linkId": "own_test",
        "interval": interval(),
        "rules": [{"kind": CATCH_ALL, "name": "Deal desk", "owner_id": "005-desk"}],
    }
    payload.update(overrides)
    return engine.create_link(payload, actor="dana", source=LINK_SOURCE)


def basic_workspace(store):
    """One desk, one Enterprise rep, one lead owned by them. Enough to route."""
    add_rep(store, "005-desk", name="Deal Desk", team="Commercial")
    add_rep(store, "005-nadia")
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    return store


def open_route(engine, room_id, email="lead@example.com", *, guest_fields=None, **link_overrides):
    """A link plus a routing session for one guest, which is step 2 to 4.

    ``guest_fields`` is kept out of ``**link_overrides`` on purpose: it belongs to
    the *guest's* request, so passing it as a link override would silently declare
    a link field called ``guest_fields`` and leave the rule with no Data Field to
    read - which is the same failure as a rule that never matches.
    """
    link = add_link(engine, **link_overrides)
    payload = {"guestEmail": email}
    if guest_fields is not None:
        payload["data_fields"] = guest_fields
    opened = engine.init_simple(link["id"], payload, room_id=room_id, actor="dana", source=SOURCE)
    return link, opened


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    routes = mounted_routes(http)
    assert ("GET", f"{PREFIX}/vocabulary") in routes
    assert ("POST", f"{PREFIX}/rooms/{{room_id}}/init-simple") in routes


def test_registry_reports_the_feature_with_its_routes(http):
    entry = http.get(f"/api/features/{FEATURE_ID}").json()
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-053"
    assert entry["loaded"] is True
    assert entry["exception_handlers"] == ["OwnershipError"]
    assert entry["routes"]


def test_no_feature_failed_to_load(http):
    """A feature that raised on import would be reported here rather than mounted."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_this_features_prefix_is_its_own(http):
    """Prefixes may legitimately be shared across features, so the claim is narrow:
    no *other* feature claims the prefix this one owns."""
    body = http.get("/api/features").json()
    claimants = [f["id"] for f in body["features"] if f["prefix"] == PREFIX]
    assert claimants == [FEATURE_ID]


def test_the_feature_module_does_not_import_the_app():
    """The dependency direction the contract fixes: features import from dsr.deps."""
    text = (Path(load_feature(MODULE).__file__)).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text


def test_nav_entry_is_declared(http):
    entry = http.get(f"/api/features/{FEATURE_ID}").json()
    assert entry["nav"] == [{"id": "ownership-routing", "label": "Ownership routing"}]


# --------------------------------------------------------------------------- #
# The researched vocabulary, served as data
# --------------------------------------------------------------------------- #


def test_the_five_researched_link_types_are_published():
    types = [entry["type"] for entry in LINK_TYPES]
    assert types == ["Personal", "Admin", "RoundRobin", "Group", OWNERSHIP]


def test_ownership_is_the_only_supported_link_type():
    assert SUPPORTED_LINK_TYPES == (OWNERSHIP,)


def test_the_three_crm_object_types_are_published_in_the_researched_order():
    assert CRM_OBJECT_TYPES == ("lead", "contact", "account")


def test_the_resolution_order_rationale_says_the_order_is_ours():
    """The evidence sentence is alphabetical, so it cannot fix an order.

    Asserted because the order *is* a build decision: if this text ever stops
    saying so, the reasoning and the behaviour have drifted apart.
    """
    assert "alphabetical" in RESOLUTION_ORDER_RATIONALE
    assert "not the order" in RESOLUTION_ORDER_RATIONALE


def test_the_calendar_providers_are_the_two_named():
    assert [entry["provider"] for entry in CALENDAR_PROVIDERS] == ["google", "outlook"]


def test_the_discovery_operation_is_named_exactly():
    """The research names an Edge API operation id, and a paraphrase is not it."""
    assert DISCOVERY_OPERATION == "scheduling-link-list-ownership"


def test_the_two_edge_endpoints_are_the_documented_paths():
    assert EDGE_ENDPOINTS["init"] == ("POST /api/fire-edge/v1/org/schedulingLinks/init-simple")
    assert "routing/{routeId}/schedule-simple" in EDGE_ENDPOINTS["schedule"]


def test_the_init_payload_requires_guest_email_and_interval():
    assert INIT_REQUIRED_FIELDS == ("link", "guestEmail", "interval")


def test_the_schedule_payload_requires_a_start_time_and_a_guest():
    assert SCHEDULE_REQUIRED_FIELDS == ("startTime", "guestEmail")


def test_the_two_researched_rule_kinds_are_published():
    kinds = [entry["kind"] for entry in RULE_KINDS]
    assert kinds == ["crm_ownership", "without_ownership"]
    assert all(entry["quote"] for entry in RULE_KINDS)


def test_both_resolution_sources_are_published_with_the_crm_as_default():
    sources = {entry["source"]: entry for entry in RESOLUTION_SOURCES}
    assert sources["crm"]["default"] is True
    assert sources["pre_resolved"]["default"] is False
    assert "pre-resolved from your own CRM" in sources["pre_resolved"]["quote"]


def test_the_resolution_outcomes_include_a_catch_all_and_an_unroutable():
    assert RESOLUTION_OUTCOMES == ("resolved", "catch_all", "unroutable")


def test_the_vocabulary_endpoint_carries_the_node_guardrail(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["node_guardrail"]["forbidden"] == list(FORBIDDEN_ON_OWNERSHIP_PATH)
    assert body["node_guardrail"]["quote"] == FORBIDDEN_QUOTE
    assert body["node_guardrail"]["preview_only"] is True


def test_the_vocabulary_endpoint_agrees_with_the_validator(http):
    """One source, so a picker cannot offer a value the validator refuses."""
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["supported_link_types"] == list(SUPPORTED_LINK_TYPES)
    for entry in body["link_types"]:
        accepted = entry["type"] in SUPPORTED_LINK_TYPES
        assert entry["supported"] is accepted


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    """A register entry without a `change_it` is a comment, and comments rot."""
    for entry in ownership_inferences.INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"] and entry["why"]
        assert entry["change_it"], f"{entry['id']} names no way to change it"
        assert entry["blast_radius"], f"{entry['id']} names no blast radius"
        assert isinstance(entry["value"], (dict, list))


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in ownership_inferences.INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inferences_endpoint_separates_sourced_from_chosen(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(ownership_inferences.INFERENCES)
    assert body["sourced"]["link_types"] == [OWNERSHIP]
    assert body["sourced"]["catch_all"] == CATCH_ALL
    assert "ownership" in body["sourced_quotes"]
    assert body["sourced_quotes"]["guest_email"]


def test_an_inference_can_be_looked_up_by_name():
    """The point of the register: disagree with one entry, not with a diff."""
    entry = ownership_inferences.by_id("resolution-order")
    assert entry is not None
    assert entry["value"]["order"] == ["lead", "contact", "account"]
    assert ownership_inferences.by_id("no-such-inference") is None


def test_the_resolution_order_inference_matches_the_constant():
    assert ownership_inferences.by_id("resolution-order")["value"]["order"] == list(
        CRM_OBJECT_TYPES
    )


def test_the_package_re_exports_the_inference_lookup():
    from dsr import ownership_routing

    assert ownership_routing.inference_by_id("resolution-order")["id"] == "resolution-order"


# --------------------------------------------------------------------------- #
# The researched link type
# --------------------------------------------------------------------------- #


def test_an_ownership_link_can_be_declared(engine):
    link = add_link(engine)
    assert link["data"]["type"] == OWNERSHIP
    assert link["data"]["enabled"] is True
    assert link["has_catch_all"] is True


def test_the_link_type_is_accepted_in_any_case(engine):
    """A caller who read the docs and sent `ownership` is not wrong."""
    assert add_link(engine, type="ownership")["data"]["type"] == OWNERSHIP


@pytest.mark.parametrize("declared", ["Personal", "Admin", "RoundRobin", "Group"])
def test_the_other_four_link_types_are_refused_with_the_reason(engine, declared):
    """They route by something this workflow does not consult."""
    with pytest.raises(LinkTypeNotSupported) as caught:
        add_link(engine, type=declared)
    assert declared in str(caught.value)
    assert caught.value.status == 400


def test_an_unknown_link_type_is_refused_by_name(engine):
    with pytest.raises(LinkTypeNotSupported) as caught:
        add_link(engine, type="Handoff")
    assert "Handoff" in str(caught.value)
    assert "Personal" in str(caught.value)


def test_a_refused_link_type_leaves_no_row_behind(engine, store):
    """Validation happens before the insert, so a bad type cannot half-configure."""
    with pytest.raises(LinkTypeNotSupported):
        add_link(engine, type="Group")
    assert store.list(LINK_COLLECTION) == []


def test_a_link_mints_an_edge_link_id_when_none_is_given(engine):
    link = add_link(engine, linkId="")
    assert link["data"]["linkId"].startswith("own_")


def test_a_disabled_link_cannot_open_a_session(engine, store, room):
    basic_workspace(store)
    link = add_link(engine, enabled=False)
    with pytest.raises(LinkError) as caught:
        engine.init_simple(
            link["id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "disabled" in str(caught.value)
    assert store.list(ROUTE_COLLECTION) == []


# --------------------------------------------------------------------------- #
# The researched required guestEmail
# --------------------------------------------------------------------------- #


def test_guest_email_is_required_on_an_ownership_init(engine, store, room):
    """ "it is required so Chili Piper can resolve the owner from your CRM"."""
    basic_workspace(store)
    link = add_link(engine)
    with pytest.raises(GuestEmailRequired) as caught:
        engine.init_simple(link["id"], {}, room_id=room["id"], actor="dana", source=SOURCE)
    assert "guestEmail" in str(caught.value)
    assert caught.value.code == "ownership_guest_email_required"


def test_a_blank_guest_email_is_treated_as_missing(engine, store, room):
    basic_workspace(store)
    link = add_link(engine)
    with pytest.raises(GuestEmailRequired):
        engine.init_simple(
            link["id"], {"guestEmail": "   "}, room_id=room["id"], actor="dana", source=SOURCE
        )


def test_a_crm_record_id_stands_in_for_the_guest_email(engine, store, room):
    """The researched data flow reads "guest email (or CRM record id)"."""
    basic_workspace(store)
    record = store.list(RECORD_COLLECTION)[0]
    link = add_link(engine)
    opened = engine.init_simple(
        link["id"], {"record_id": record["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    assert opened["owner_name"] == "Nadia A. Farouk"


def test_guest_email_is_normalised_before_the_lookup(engine, store, room):
    """Two spellings of one address are one prospect."""
    basic_workspace(store)
    link = add_link(engine)
    opened = engine.init_simple(
        link["id"],
        {"guestEmail": "  Lead@Example.COM "},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert opened["guest_email"] == "lead@example.com"


# --------------------------------------------------------------------------- #
# The interval
# --------------------------------------------------------------------------- #


def test_an_interval_must_end_after_it_starts(engine):
    with pytest.raises(IntervalError):
        add_link(
            engine,
            interval=interval(end=(NOW - timedelta(days=1)).isoformat()),
        )


def test_an_interval_needs_a_duration(engine):
    with pytest.raises(IntervalError):
        add_link(
            engine,
            interval={"start": NOW.isoformat(), "end": (NOW + timedelta(days=2)).isoformat()},
        )


def test_a_zero_duration_is_refused(engine):
    with pytest.raises(IntervalError):
        add_link(engine, interval=interval(duration_minutes=0))


def test_a_negative_minimum_notice_is_refused(engine):
    with pytest.raises(IntervalError):
        add_link(engine, interval=interval(min_notice_minutes=-1))


def test_an_interval_written_in_another_zone_compares_correctly(engine):
    """Ordering is checked on parsed values, so a start expressed in a different
    offset than the end is still understood - which a string compare would get
    wrong by the offset rather than by the range."""
    with pytest.raises(IntervalError):
        add_link(
            engine,
            interval={
                # 12:00+11:00 is 01:00Z, so this end is an hour *before* the start.
                "start": NOW.isoformat(),
                "end": (NOW - timedelta(hours=1))
                .astimezone(timezone(timedelta(hours=11)))
                .isoformat(),
                "duration_minutes": 30,
            },
        )


def test_a_trailing_z_is_accepted_as_a_timestamp(engine):
    """``datetime.fromisoformat`` only learned to read ``Z`` in 3.11, and the
    researched payloads use it."""
    link = add_link(
        engine,
        interval={
            "start": (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (NOW + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_minutes": 30,
        },
    )
    assert link["data"]["interval"]["duration_minutes"] == 30


def test_a_link_with_no_interval_refuses_to_open_a_session(engine, store, room):
    basic_workspace(store)
    payload = {
        "type": OWNERSHIP,
        "linkId": "own_no_interval",
        "rules": [{"kind": CATCH_ALL, "owner_id": "005-desk"}],
    }
    link = engine.create_link(payload, actor="dana", source=LINK_SOURCE)
    with pytest.raises(IntervalError) as caught:
        engine.init_simple(
            link["id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "interval" in str(caught.value)


# --------------------------------------------------------------------------- #
# Owner resolution: the researched CRM lookup
# --------------------------------------------------------------------------- #


def test_the_owner_is_the_lead_owner(engine, store, room):
    basic_workspace(store)
    link, opened = open_route(engine, room["id"])
    assert opened["owner_id"] == "005-nadia"
    assert opened["matched_object_type"] == "lead"


def test_a_contact_owner_is_used_when_there_is_no_lead(engine, store, room):
    basic_workspace(store)
    add_record(store, email="c@example.com", owner_id="005-rui", object_type="contact")
    add_rep(store, "005-rui")
    _, opened = open_route(engine, room["id"], email="c@example.com")
    assert opened["owner_id"] == "005-rui"
    assert opened["matched_object_type"] == "contact"


def test_an_account_owner_is_used_when_there_is_no_person_record(engine, store, room):
    basic_workspace(store)
    add_rep(store, "005-rui")
    add_record(
        store,
        email="x@fabrikam.example",
        owner_id="005-rui",
        object_type="account",
        domain="fabrikam.example",
        name="Fabrikam Logistics",
    )
    _, opened = open_route(engine, room["id"], email="ops@fabrikam.example")
    assert opened["owner_id"] == "005-rui"
    assert opened["matched_object_type"] == "account"


def test_a_lead_is_preferred_over_the_account_the_same_domain_shares(engine, store, room):
    """Lead first: it is about a person, the account is a coarser answer."""
    basic_workspace(store)
    add_rep(store, "005-rui")
    add_record(
        store,
        email="ops@fabrikam.example",
        owner_id="005-nadia",
        object_type="lead",
        domain="fabrikam.example",
    )
    add_record(
        store,
        email="x@fabrikam.example",
        owner_id="005-rui",
        object_type="account",
        domain="fabrikam.example",
    )
    _, opened = open_route(engine, room["id"], email="ops@fabrikam.example")
    assert opened["owner_id"] == "005-nadia"
    assert opened["matched_object_type"] == "lead"


def test_a_lead_with_no_owner_is_skipped_for_the_contact(engine, store, room):
    """A record naming nobody is not the answer; the lookup continues."""
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-rui", team="Commercial")
    add_record(store, email="d@example.com", owner_id="", object_type="lead")
    add_record(store, email="d@example.com", owner_id="005-rui", object_type="contact")
    _, opened = open_route(engine, room["id"], email="d@example.com")
    assert opened["owner_id"] == "005-rui"
    assert opened["matched_object_type"] == "contact"


def test_the_resolution_records_what_it_skipped_and_why(engine, store, room):
    """A rep reading a decision needs to see the lead was passed over."""
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-rui", team="Commercial")
    add_record(store, email="d@example.com", owner_id="", object_type="lead")
    add_record(store, email="d@example.com", owner_id="005-rui", object_type="contact")
    resolution = engine.resolve({"guestEmail": "d@example.com"})
    assert resolution["skipped"][0]["reason"] == "no_owner_id"
    assert resolution["skipped"][0]["object_type"] == "lead"


def test_the_resolution_order_is_recorded_on_the_decision(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    decision = engine.decisions()[0]["data"]
    assert decision["resolution_order"] == list(CRM_OBJECT_TYPES)


def test_a_link_may_declare_its_own_resolution_order(engine, store, room):
    basic_workspace(store)
    add_rep(store, "005-rui")
    add_record(store, email="e@example.com", owner_id="005-rui", object_type="contact")
    add_record(store, email="e@example.com", owner_id="005-nadia", object_type="lead")
    _, opened = open_route(
        engine, room["id"], email="e@example.com", resolution_order=["contact", "lead", "account"]
    )
    assert opened["matched_object_type"] == "contact"


def test_an_unknown_object_type_in_the_resolution_order_is_refused(engine):
    with pytest.raises(LinkError):
        add_link(engine, resolution_order=["lead", "opportunity"])


def test_a_resolution_with_no_owner_refuses_when_nothing_is_tolerating(engine, store):
    """ "Who owns this record" has None as its honest answer."""
    basic_workspace(store)
    with pytest.raises(NoOwnerResolved):
        engine.resolve({"guestEmail": "stranger@elsewhere.example"})


def test_an_owner_id_no_rep_answers_to_is_refused_with_the_remedy(engine, store):
    basic_workspace(store)
    add_record(store, email="j@example.com", owner_id="005-former-employee")
    with pytest.raises(OwnerUnknown) as caught:
        engine.resolve({"guestEmail": "j@example.com"})
    assert "005-former-employee" in str(caught.value)
    assert "sync the workspace's reps" in str(caught.value)
    assert caught.value.status == 409


def test_one_owner_id_may_not_answer_to_two_reps(engine, store):
    """The researched flow resolves one owner, with one calendar."""
    basic_workspace(store)
    add_rep(store, "005-nadia", name="Nadia (duplicate row)")
    with pytest.raises(AmbiguousOwner) as caught:
        engine.resolve({"guestEmail": "lead@example.com"})
    assert caught.value.status == 409


def test_a_resolution_reaches_a_record_held_by_another_room(engine, store, room, other_room):
    """The CRM is account-level, so a record is not hidden by the room asking.

    Found the hard way: with the lookup room-scoped, a prospect arriving from the
    wrong room found no owner and reached the catch-all, and nothing in the
    decision said why - the record existed the whole time.
    """
    basic_workspace(store)
    record = add_record(store, email="s@example.com", owner_id="005-nadia")
    store.update(record["id"], {"room_id": other_room["id"]}, actor="dana", source=LINK_SOURCE)
    _, opened = open_route(engine, room["id"], email="s@example.com")
    assert opened["outcome"] == "resolved"
    assert opened["owner_id"] == "005-nadia"


def test_the_lookup_records_what_it_consulted(engine, store):
    basic_workspace(store)
    engine.resolve({"guestEmail": "lead@example.com"})
    lookup = engine.crm.lookups[-1]
    assert lookup["order"] == list(CRM_OBJECT_TYPES)
    assert len(lookup["candidates"]) == 1


# --------------------------------------------------------------------------- #
# Lead-to-Account: read, not configured
# --------------------------------------------------------------------------- #


def test_lead_to_account_is_off_when_the_workspace_declares_nothing(engine, store):
    """Absent is not the same as yes."""
    assert engine.crm.lead_to_account_matching() is False


def test_lead_to_account_follows_the_declared_setting(engine, store, room):
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-rui", team="Commercial")
    add_record(store, email="f@example.com", owner_id="", object_type="lead", account_id="acct-1")
    store.create(
        "crm_owner_setting",
        {"key": "default", "lead_to_account_matching": True},
        actor="dana",
        source=LINK_SOURCE,
    )
    # No address on the account: reachable only by the Lead-to-Account walk.
    add_record(
        store,
        email="",
        owner_id="005-rui",
        object_type="account",
        account_id="acct-1",
        domain="f.example",
    )
    _, opened = open_route(engine, room["id"], email="f@example.com")
    assert opened["owner_id"] == "005-rui"
    assert opened["matched_object_type"] == "account"


def test_a_lead_is_not_followed_to_its_account_while_the_setting_is_off(engine, store, room):
    """The setting is read, not configured - so absent means off.

    The account here carries no address of its own, so it can only be reached by
    walking from the Lead. With the setting off there is no such walk, which is the
    whole point: a workspace that has not declared L2A does not get it anyway.
    """
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-rui", team="Commercial")
    add_record(store, email="g@example.com", owner_id="", object_type="lead", account_id="acct-2")
    add_record(
        store,
        email="",
        owner_id="005-rui",
        object_type="account",
        account_id="acct-2",
        domain="g.example",
    )
    assert engine.crm.lead_to_account_matching() is False
    with pytest.raises(NoOwnerResolved):
        engine.resolve({"guestEmail": "g@example.com"})


def test_the_l2a_setting_is_named_in_the_vocabulary(http):
    assert http.get(f"{PREFIX}/vocabulary").json()["l2a_setting"] == "lead_to_account_matching"


# --------------------------------------------------------------------------- #
# Availability from the owner's connected calendar
# --------------------------------------------------------------------------- #


def test_slots_come_from_the_resolved_owners_own_calendar(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    assert opened["slots_offered"] > 0
    assert opened["start_times"] == sorted(opened["start_times"])


def test_no_slots_at_all_is_an_answer_not_an_error(engine, store, room):
    """A real window with nothing free in it is an answer."""
    add_rep(store, "005-desk", team="Commercial")
    add_rep(
        store,
        "005-nadia",
        calendar={
            "busy": [{"start": NOW.isoformat(), "end": (NOW + timedelta(days=30)).isoformat()}]
        },
    )
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    _, opened = open_route(engine, room["id"])
    assert opened["slots_offered"] == 0
    assert opened["start_times"] == []


def test_a_rep_with_no_calendar_is_refused_with_the_remedy(engine, store, room):
    basic_workspace(store)
    set_calendar(store, engine, "005-nadia", {})
    link = add_link(engine)
    with pytest.raises(CalendarNotConnected) as caught:
        engine.init_simple(
            link["id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "calendar" in str(caught.value)
    assert caught.value.status == 409


def test_a_disconnected_calendar_is_refused(engine, store, room):
    basic_workspace(store)
    set_calendar(store, engine, "005-nadia", {"provider": "google", "connected": False})
    link = add_link(engine)
    with pytest.raises(CalendarNotConnected) as caught:
        engine.init_simple(
            link["id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "reconnect" in str(caught.value)


def test_a_calendar_with_no_provider_is_refused(engine):
    with pytest.raises(CalendarNotConnected):
        cal.calendar_for({"id": "x", "calendar": {"connected": True}})


def test_slots_never_overlap_each_other(engine, store, room):
    """The grid is the duration, so overlap is impossible by construction."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    starts = [datetime.fromisoformat(slot) for slot in opened["start_times"]]
    # `strict=False`, not `True`: `starts[1:]` is one shorter than `starts` on
    # purpose, because the assertion compares each slot with the one after it.
    # `strict=True` would raise StopIteration on the last pair instead.
    for earlier, later in zip(starts, starts[1:], strict=False):
        assert (later - earlier).total_seconds() >= 30 * 60


def test_a_busy_block_removes_the_slots_it_overlaps(engine, store, room):
    busy_start = NOW + timedelta(days=1, hours=9)
    add_rep(
        store,
        "005-desk",
        team="Commercial",
        calendar={
            "busy": [
                {
                    "start": busy_start.isoformat(),
                    "end": (busy_start + timedelta(hours=3)).isoformat(),
                }
            ]
        },
    )
    add_rep(store, "005-nadia")
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    _, opened = open_route(engine, room["id"])
    starts = [datetime.fromisoformat(slot) for slot in opened["start_times"]]
    assert all(
        not (slot < busy_start + timedelta(hours=3) and slot + timedelta(minutes=30) > busy_start)
        for slot in starts
    )


def test_a_buffer_widens_a_busy_block_onto_both_sides(engine, store):
    """A rep needs the gap before a meeting as much as the meeting."""
    busy_start = NOW + timedelta(days=1, hours=9)
    owner = rep_row(
        "005-x",
        calendar={
            "provider": "google",
            "connected": True,
            "buffer_minutes": 30,
            "busy": [
                {
                    "start": busy_start.isoformat(),
                    "end": (busy_start + timedelta(minutes=30)).isoformat(),
                }
            ],
        },
    )
    slots = cal.available_slots(
        owner,
        {
            "start": (NOW + timedelta(days=1)).isoformat(),
            "end": (NOW + timedelta(days=2)).isoformat(),
            "duration_minutes": 30,
        },
        now=NOW,
    )
    starts = [datetime.fromisoformat(slot) for slot in slots]
    assert all(
        slot < busy_start - timedelta(minutes=30) or slot >= busy_start + timedelta(hours=1)
        for slot in starts
    )


def test_minimum_notice_pushes_the_first_slot_out(engine, store, room):
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-nadia")
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    _, opened = open_route(engine, room["id"], interval=interval(min_notice_minutes=60 * 24 * 2))
    first = datetime.fromisoformat(opened["start_times"][0])
    assert first >= NOW + timedelta(days=2)


def test_a_window_entirely_inside_the_notice_period_offers_nothing(engine, store, room):
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-nadia")
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    _, opened = open_route(
        engine,
        room["id"],
        interval={
            "start": NOW.isoformat(),
            "end": (NOW + timedelta(minutes=30)).isoformat(),
            "duration_minutes": 30,
            "min_notice_minutes": 60,
        },
    )
    assert opened["start_times"] == []


def test_the_slot_count_is_bounded(engine, store, room):
    """An unbounded list over a long window is a response nobody can render."""
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-nadia")
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    _, opened = open_route(
        engine,
        room["id"],
        interval={
            "start": (NOW + timedelta(days=1)).isoformat(),
            "end": (NOW + timedelta(days=60)).isoformat(),
            "duration_minutes": 15,
        },
    )
    assert opened["slots_offered"] == cal.DEFAULT_MAX_SLOTS


def test_two_callers_get_the_same_lattice(engine, store, room):
    """Aligned from the epoch, so the answer cannot depend on when it was asked."""
    basic_workspace(store)
    _, first = open_route(engine, room["id"])
    _, second = open_route(engine, room["id"])
    assert first["start_times"] == second["start_times"]


def test_weekends_are_not_offered(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    starts = [datetime.fromisoformat(slot) for slot in opened["start_times"]]
    assert all(start.weekday() < 5 for start in starts)


def test_a_rep_who_declares_itself_unavailable_all_week_has_no_slots(engine):
    """Not the default week: "no availability" and "the default week" differ."""
    owner = rep_row("005-x", working_days=[])
    assert (
        cal.available_slots(
            owner,
            {
                "start": (NOW + timedelta(days=1)).isoformat(),
                "end": (NOW + timedelta(days=8)).isoformat(),
                "duration_minutes": 30,
            },
            now=NOW,
        )
        == []
    )


def test_a_malformed_busy_block_is_refused_rather_than_ignored(engine):
    """A block this build cannot read is a meeting somebody believes is protected."""
    owner = rep_row(
        "005-x",
        calendar={"provider": "google", "connected": True, "busy": [{"start": NOW.isoformat()}]},
    )
    with pytest.raises(ValueError):
        cal.available_slots(
            owner,
            {
                "start": (NOW + timedelta(days=1)).isoformat(),
                "end": (NOW + timedelta(days=2)).isoformat(),
                "duration_minutes": 30,
            },
            now=NOW,
        )


def test_a_busy_block_that_ends_before_it_starts_is_refused(engine):
    owner = rep_row(
        "005-x",
        calendar={
            "provider": "google",
            "connected": True,
            "busy": [{"start": (NOW + timedelta(days=2)).isoformat(), "end": NOW.isoformat()}],
        },
    )
    with pytest.raises(ValueError):
        cal.busy_blocks(owner)


def test_an_impossible_working_window_is_refused(engine):
    with pytest.raises(ValueError):
        cal.working_window({"calendar": {"work_start_minute": 600, "work_end_minute": 300}})


# --------------------------------------------------------------------------- #
# The two researched calls, and the routing session between them
# --------------------------------------------------------------------------- #


def test_init_answers_with_slots_and_a_routing_id(engine, store, room):
    """Available startTimes plus a routingId for the second call."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    assert opened["routing_id"]
    assert opened["start_times"]
    assert opened["state"] == "open"


def test_init_writes_the_route_the_decision_and_the_link_history_together(engine, store, room):
    basic_workspace(store)
    link, opened = open_route(engine, room["id"])
    assert len(store.list(ROUTE_COLLECTION)) == 1
    assert len(store.list(DECISION_COLLECTION)) == 1
    history = store.get(link["id"])["data"]["history"]
    assert history[0]["decision_id"] == opened["decision_id"]
    assert history[0]["outcome"] == "resolved"


def test_the_link_history_is_capped(engine, store, room):
    """A busy link must not grow an unbounded array on its own row."""
    basic_workspace(store)
    link = add_link(engine)
    for _ in range(LINK_HISTORY_LIMIT + 5):
        engine.init_simple(
            link["id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    history = store.get(link["id"])["data"]["history"]
    assert len(history) == LINK_HISTORY_LIMIT


def test_the_route_records_the_owner_and_the_object_it_came_from(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    route = engine.get_route(opened["routing_id"])
    assert route["data"]["owner_id"] == "005-nadia"
    assert route["data"]["matched_object_type"] == "lead"
    assert route["is_open"] is True


def test_schedule_simple_books_one_of_the_offered_slots(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    booked = engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    assert booked["data"]["state"] == "confirmed"
    assert booked["data"]["owner_id"] == "005-nadia"
    assert booked["data"]["room_id" if False else "start_time"] == opened["start_times"][0]


def test_the_booking_carries_the_meetings_end_time(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    booked = engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    start = datetime.fromisoformat(booked["data"]["start_time"])
    end = datetime.fromisoformat(booked["data"]["end_time"])
    assert (end - start).total_seconds() == 30 * 60


def test_a_session_is_consumed_by_one_booking(engine, store, room):
    """Two prospects in one slot on one owner's calendar is the bug to refuse."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    with pytest.raises(RouteConsumed) as caught:
        engine.book(
            opened["routing_id"],
            {"startTime": opened["start_times"][1], "guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )
    assert caught.value.status == 409


def test_a_slot_that_was_never_offered_is_refused(engine, store, room):
    """The owner's calendar was never shown to be free then."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    never_offered = (NOW + timedelta(days=3, hours=11, minutes=7)).isoformat()
    with pytest.raises(SlotNotOffered):
        engine.book(
            opened["routing_id"],
            {"startTime": never_offered, "guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )


def test_a_different_guest_cannot_book_the_slot(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    with pytest.raises(GuestMismatch) as caught:
        engine.book(
            opened["routing_id"],
            {"startTime": opened["start_times"][0], "guestEmail": "someone.else@example.com"},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )
    assert "lead@example.com" in str(caught.value)


def test_a_slot_echoed_back_in_another_notation_still_books(engine, store, room):
    """A caller writing ``Z`` where the offer used ``+00:00`` is correct."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    offered = opened["start_times"][0]
    as_z = offered.replace("+00:00", "Z")
    booked = engine.book(
        opened["routing_id"],
        {"startTime": as_z, "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    assert booked["data"]["state"] == "confirmed"


def test_an_unknown_routing_id_is_a_404_shaped_refusal(engine, store, room):
    basic_workspace(store)
    with pytest.raises(RouteNotFound) as caught:
        engine.book(
            "crm_owner_route_nope",
            {"startTime": NOW.isoformat(), "guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )
    assert caught.value.status == 404


def test_a_booking_needs_a_start_time(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    with pytest.raises(OwnershipError) as caught:
        engine.book(
            opened["routing_id"],
            {"guestEmail": "lead@example.com"},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )
    assert "startTime" in str(caught.value)


def test_a_booking_needs_a_guest_email(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    with pytest.raises(GuestEmailRequired):
        engine.book(
            opened["routing_id"],
            {"startTime": opened["start_times"][0]},
            room_id=room["id"],
            actor="dana",
            source=BOOK_SOURCE,
        )


def test_a_booked_slot_is_not_offered_to_the_next_prospect(engine, store, room):
    """Two prospects must not both be offered the slot that is about to go."""
    basic_workspace(store)
    _, first = open_route(engine, room["id"])
    engine.book(
        first["routing_id"],
        {"startTime": first["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    _, second = open_route(engine, room["id"])
    assert first["start_times"][0] not in second["start_times"]


def test_cancelling_a_booking_releases_its_slot(engine, store, room):
    basic_workspace(store)
    _, first = open_route(engine, room["id"])
    booked = engine.book(
        first["routing_id"],
        {"startTime": first["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    engine.cancel_booking(booked["id"], room_id=room["id"], actor="dana", source=BOOK_SOURCE)
    _, second = open_route(engine, room["id"])
    assert first["start_times"][0] in second["start_times"]


def test_a_booking_cannot_be_cancelled_twice(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    booked = engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    engine.cancel_booking(booked["id"], room_id=room["id"], actor="dana", source=BOOK_SOURCE)
    with pytest.raises(BookingStateError):
        engine.cancel_booking(booked["id"], room_id=room["id"], actor="dana", source=BOOK_SOURCE)


def test_check_writes_nothing_at_all(engine, store, room):
    """The read-only half of init-simple, and the guarantee that matters."""
    basic_workspace(store)
    link = add_link(engine)
    answer = engine.check(link["id"], {"guestEmail": "lead@example.com"}, room_id=room["id"])
    assert answer["wrote"] is False
    assert answer["would_route_to"] == "005-nadia"
    assert answer["slots_offered"] > 0
    assert store.list(ROUTE_COLLECTION) == []
    assert store.list(DECISION_COLLECTION) == []
    assert store.list(BOOKING_COLLECTION) == []


def test_check_agrees_with_what_init_simple_would_do(engine, store, room):
    basic_workspace(store)
    link = add_link(engine)
    checked = engine.check(link["id"], {"guestEmail": "lead@example.com"}, room_id=room["id"])
    opened = engine.init_simple(
        link["id"],
        {"guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=SOURCE,
    )
    assert checked["would_route_to"] == opened["owner_id"]
    assert checked["start_times"] == opened["start_times"]


# --------------------------------------------------------------------------- #
# The CRM Ownership routing rule, and the catch-all that has to be there
# --------------------------------------------------------------------------- #


def test_a_team_rule_matches_when_the_owner_is_on_that_team(engine, store, room):
    """ "checks whether one of the reps from Team A owns the Lead, Contact or Account"."""
    basic_workspace(store)
    add_rep(store, "005-ana", team="Commercial")
    _, opened = open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise owner", "team": "Enterprise"},
            {"kind": CATCH_ALL, "name": "Deal desk", "owner_id": "005-desk"},
        ],
    )
    assert opened["outcome"] == "resolved"
    assert opened["owner_id"] == "005-nadia"
    assert opened["rule"]["kind"] == "crm_ownership"


def test_a_team_rule_does_not_match_an_owner_off_the_team(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Commercial owner", "team": "Commercial"},
            {"kind": CATCH_ALL, "name": "Deal desk", "owner_id": "005-desk"},
        ],
    )
    assert opened["outcome"] == "catch_all"
    assert opened["owner_id"] == "005-desk"


def test_a_team_rule_needs_a_team(engine):
    with pytest.raises(OwnershipError):
        add_link(
            engine,
            rules=[
                {"kind": "crm_ownership", "name": "Nameless"},
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
            ],
        )


def test_a_value_rule_matches_a_crm_value(engine, store, room):
    basic_workspace(store)
    add_record(
        store,
        email="h@example.com",
        owner_id="005-rui",
        object_type="contact",
        segment="enterprise",
    )
    add_rep(store, "005-rui")
    add_rep(store, "005-ana", team="Commercial")
    assert (
        engine.crm.record(store.list(RECORD_COLLECTION, limit=1)[0]["id"])["segment"]
        == "enterprise"
    )
    _, opened = open_route(
        engine,
        room["id"],
        email="h@example.com",
        rules=[
            {
                "kind": "without_ownership",
                "name": "Enterprise segment",
                "field": "segment",
                "equals": "enterprise",
                "owner_id": "005-ana",
            },
            {"kind": CATCH_ALL, "name": "Deal desk", "owner_id": "005-desk"},
        ],
    )
    assert opened["outcome"] == "resolved"
    assert opened["owner_id"] == "005-ana"


def test_a_value_rule_reads_a_data_field_too(engine, store, room):
    basic_workspace(store)
    add_rep(store, "005-ana", team="Commercial")
    _, opened = open_route(
        engine,
        room["id"],
        rules=[
            {
                "kind": "without_ownership",
                "name": "Came from the field",
                "field": "channel",
                "equals": "partner",
                "source": "data_field",
                "owner_id": "005-ana",
            },
            {"kind": CATCH_ALL, "name": "Deal desk", "owner_id": "005-desk"},
        ],
        guest_fields={"channel": "partner"},
    )
    assert opened["owner_id"] == "005-ana"


def test_a_value_rule_needs_a_field(engine):
    with pytest.raises(OwnershipError):
        add_link(
            engine,
            rules=[
                {"kind": "without_ownership", "name": "No field"},
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
            ],
        )


def test_a_value_rule_may_only_read_a_crm_or_a_data_field_value(engine):
    with pytest.raises(OwnershipError) as caught:
        add_link(
            engine,
            rules=[
                {"kind": "without_ownership", "field": "x", "source": "vibes"},
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
            ],
        )
    assert "crm" in str(caught.value)


def test_a_chain_with_no_catch_all_is_undecliarable(engine, store):
    """A chain that can run out is a prospect who books nothing."""
    with pytest.raises(RulesDoNotFallThrough) as caught:
        add_link(
            engine, rules=[{"kind": "crm_ownership", "name": "Only rule", "team": "Enterprise"}]
        )
    assert "catch-all" in str(caught.value)


def test_a_catch_all_naming_nobody_is_refused(engine):
    with pytest.raises(RulesDoNotFallThrough) as caught:
        add_link(engine, rules=[{"kind": CATCH_ALL, "name": "Nowhere"}])
    assert "routes nowhere" in str(caught.value)


def test_an_empty_chain_is_refused(engine):
    with pytest.raises(RulesDoNotFallThrough):
        add_link(engine, rules=[])


def test_a_catch_all_in_the_middle_is_refused(engine):
    """Every rule after it would be dead, and dead rules look live in a payload."""
    with pytest.raises(RulesDoNotFallThrough):
        add_link(
            engine,
            rules=[
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
                {"kind": "crm_ownership", "name": "Too late", "team": "Enterprise"},
            ],
        )


def test_two_catch_alls_are_refused(engine):
    with pytest.raises(OwnershipError):
        add_link(
            engine,
            rules=[
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
                {"kind": CATCH_ALL, "owner_id": "005-ana"},
            ],
        )


def test_an_unknown_rule_kind_is_refused_by_name(engine):
    with pytest.raises(OwnershipError) as caught:
        add_link(
            engine,
            rules=[
                {"kind": "astrology", "name": "Nope"},
                {"kind": CATCH_ALL, "owner_id": "005-desk"},
            ],
        )
    assert "astrology" in str(caught.value)


def test_the_first_matching_rule_wins_and_later_rules_never_run(engine, store, room):
    """A chain is a chain: the first match ends it, and the rest are not consulted."""
    basic_workspace(store)
    add_rep(store, "005-ana", team="Commercial")
    _, opened = open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise", "team": "Enterprise"},
            {"kind": "crm_ownership", "name": "Commercial", "team": "Commercial"},
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    considered = engine.decisions()[0]["data"]["considered"]
    assert [entry["name"] for entry in considered] == ["Enterprise"]
    assert opened["rule"]["name"] == "Enterprise"


def test_a_crm_value_rule_reads_the_matched_record_not_the_first_candidate(engine, store, room):
    """The rule reads the record the *resolution* matched, not any record for the guest.

    Two records for one address is ordinary in a CRM - a lead and the contact it
    became - and reading the wrong one makes the rule's answer depend on insertion
    order rather than on which record actually decided the owner.
    """
    basic_workspace(store)
    add_record(
        store, email="o@example.com", owner_id="005-nadia", object_type="lead", segment="smb"
    )
    add_record(
        store,
        email="o@example.com",
        owner_id="005-nadia",
        object_type="contact",
        segment="strategic",
    )
    _, opened = open_route(
        engine,
        room["id"],
        email="o@example.com",
        rules=[
            {
                "kind": "without_ownership",
                "name": "Strategic",
                "field": "segment",
                "equals": "strategic",
                "owner_id": "005-nadia",
            },
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    # The lead wins the resolution, so the rule reads the lead's segment.
    assert opened["outcome"] == "catch_all"


def test_the_l2a_walk_fetches_the_account_even_with_no_address_on_it(engine, store, room):
    """An Account normally has no address, so the walk must not go via the email search."""
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-rui", team="Commercial")
    add_record(
        store, email="p@example.com", owner_id="", object_type="lead", account_id="acct-walk"
    )
    account = add_record(
        store,
        email="",
        owner_id="005-rui",
        object_type="account",
        account_id="acct-walk",
        domain="p.example",
    )
    store.create(
        "crm_owner_setting",
        {"key": "default", "lead_to_account_matching": True},
        actor="dana",
        source=LINK_SOURCE,
    )
    resolution = engine.crm.resolve_owner(guest_email="p@example.com")
    assert resolution["owner_id"] == "005-rui"
    assert resolution["via_lead"]
    assert resolution["matched_record_id"] == account["id"]


def test_the_l2a_walk_reaches_nothing_when_the_lead_names_no_account(engine, store):
    add_rep(store, "005-desk", team="Commercial")
    add_record(store, email="q@example.com", owner_id="", object_type="lead")
    store.create(
        "crm_owner_setting",
        {"key": "default", "lead_to_account_matching": True},
        actor="dana",
        source=LINK_SOURCE,
    )
    from dsr.ownership_routing.errors import NoOwnerResolved as _NoOwner

    with pytest.raises(_NoOwner):
        engine.crm.resolve_owner(guest_email="q@example.com")


def test_the_l2a_walk_reaches_nothing_when_the_named_record_is_not_an_account(engine, store):
    add_rep(store, "005-desk", team="Commercial")
    add_record(
        store,
        email="r@example.com",
        owner_id="",
        object_type="lead",
        account_id="crm_owner_record_x",
    )
    store.create(
        "crm_owner_setting",
        {"key": "default", "lead_to_account_matching": True},
        actor="dana",
        source=LINK_SOURCE,
    )
    assert engine.crm.account_by_reference("crm_owner_record_x") is None


def test_an_account_reference_matches_an_external_id_too(engine, store):
    """A deployment should not have to restate its CRM ids in this store's terms."""
    account = add_record(
        store, email="", owner_id="005-rui", object_type="account", external_id="001A000001"
    )
    assert engine.crm.account_by_reference("001A000001")["id"] == account["id"]


def test_an_account_reference_that_names_nothing_is_none(engine, store):
    assert engine.crm.account_by_reference("nothing-here") is None


def test_a_guest_nobody_owns_falls_through_to_the_catch_all(engine, store, room):
    """The researched catch-all exists for exactly this prospect."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"], email="stranger@elsewhere.example")
    assert opened["outcome"] == "catch_all"
    assert opened["owner_id"] == "005-desk"


def test_a_record_naming_an_absent_rep_falls_through_to_the_catch_all(engine, store, room):
    """A workspace that has not caught up with its CRM would otherwise refuse
    every prospect whose record it has."""
    basic_workspace(store)
    add_record(store, email="i@example.com", owner_id="005-former-employee")
    _, opened = open_route(engine, room["id"], email="i@example.com")
    assert opened["outcome"] == "catch_all"
    assert opened["owner_id"] == "005-desk"


def test_a_link_with_only_a_catch_all_still_routes_to_the_crm_owner(engine, store, room):
    """A fallback is not a decision: sending everyone to the desk would make the
    link's own name a lie."""
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    assert opened["outcome"] == "resolved"
    assert opened["owner_id"] == "005-nadia"


def test_the_catch_all_lands_on_the_rep_it_names(engine, store, room):
    basic_workspace(store)
    add_rep(store, "005-ana", team="Commercial")
    _, opened = open_route(
        engine,
        room["id"],
        email="stranger@elsewhere.example",
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise only", "team": "Enterprise"},
            {"kind": CATCH_ALL, "name": "Ana", "owner_id": "005-ana"},
        ],
    )
    assert opened["owner_id"] == "005-ana"
    assert opened["rule"]["owner_id"] == "005-ana"


def test_the_decision_records_why_the_catch_all_was_reached(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"], email="stranger@elsewhere.example")
    decision = engine.decisions()[0]["data"]
    assert decision["outcome"] == "catch_all"
    assert decision["rule"]["kind"] == CATCH_ALL


def test_a_catch_all_rep_with_no_calendar_is_refused_at_the_booking_step(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"], email="stranger@elsewhere.example")
    store.update(
        engine.crm.rep("005-desk")["id"],
        {"calendar": {"provider": "outlook", "connected": False}},
        actor="dana",
        source=LINK_SOURCE,
    )
    with pytest.raises(CalendarNotConnected):
        engine.init_simple(
            opened["link_id"],
            {"guestEmail": "another@elsewhere.example"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )


def test_a_team_rule_says_why_it_did_not_match(engine, store, room):
    """The decision has to be readable, not merely correct."""
    basic_workspace(store)
    open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Commercial only", "team": "Commercial"},
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    considered = engine.decisions()[0]["data"]["considered"][0]
    assert considered["matched"] is False
    assert considered["why"] == "the resolved owner 005-nadia is not on team 'Commercial'"


def test_a_team_rule_says_why_it_did_match(engine, store, room):
    basic_workspace(store)
    open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise", "team": "Enterprise"},
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    considered = engine.decisions()[0]["data"]["considered"][0]
    assert considered["matched"] is True
    assert "is on team 'Enterprise'" in considered["why"]


def test_a_team_rule_names_the_dead_owner_when_the_record_names_an_absent_rep(engine, store, room):
    """ "no rep in this workspace answers to it" is the useful half of that message."""
    basic_workspace(store)
    add_record(store, email="k@example.com", owner_id="005-former-employee")
    open_route(
        engine,
        room["id"],
        email="k@example.com",
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise", "team": "Enterprise"},
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    decision = engine.decisions()[0]["data"]
    assert decision["named_owner_id"] == "005-former-employee"
    assert decision["owner_unknown"] is True
    assert "005-former-employee" in decision["considered"][0]["why"]


def test_a_value_rule_keeps_the_resolved_owner_when_it_names_none(engine, store, room):
    """A value rule with no owner is there to *stop a fall-through*, not to reassign."""
    basic_workspace(store)
    add_record(
        store, email="m@example.com", owner_id="005-nadia", object_type="lead", segment="strategic"
    )
    _, opened = open_route(
        engine,
        room["id"],
        email="m@example.com",
        rules=[
            {
                "kind": "without_ownership",
                "name": "Strategic",
                "field": "segment",
                "equals": "strategic",
            },
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    assert opened["outcome"] == "resolved"
    assert opened["owner_id"] == "005-nadia"


def test_a_value_rule_that_does_not_match_falls_through(engine, store, room):
    basic_workspace(store)
    add_record(
        store, email="n@example.com", owner_id="005-nadia", object_type="lead", segment="smb"
    )
    _, opened = open_route(
        engine,
        room["id"],
        email="n@example.com",
        rules=[
            {
                "kind": "without_ownership",
                "name": "Strategic",
                "field": "segment",
                "equals": "strategic",
                "owner_id": "005-ana",
            },
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    assert opened["outcome"] == "catch_all"
    assert opened["owner_id"] == "005-desk"


def test_a_data_field_rule_says_so_when_the_field_was_never_collected(engine, store, room):
    basic_workspace(store)
    open_route(
        engine,
        room["id"],
        rules=[
            {
                "kind": "without_ownership",
                "name": "Partner channel",
                "field": "channel",
                "equals": "partner",
                "source": "data_field",
                "owner_id": "005-ana",
            },
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    considered = engine.decisions()[0]["data"]["considered"][0]
    assert considered["matched"] is False
    assert "no Data Field named 'channel'" in considered["why"]


# --------------------------------------------------------------------------- #
# The researched node guardrail
# --------------------------------------------------------------------------- #


def test_a_forbidden_node_is_named_before_a_missing_catch_all(engine):
    """A link with both problems is told about the one it was told to fix.

    The node guardrail says "you should not use this node in Ownership paths".
    Answering "your chain has no catch-all" instead would send somebody to write a
    chain rather than to remove the node - so the node check runs first.
    """
    with pytest.raises(ForbiddenNodeOnOwnershipPath):
        add_link(engine, nodes=["assign_to"], rules=[])


def test_the_guardrail_names_both_halves_of_the_researched_warning():
    assert "update_ownership" in FORBIDDEN_ON_OWNERSHIP_PATH
    assert "assign_to" in FORBIDDEN_ON_OWNERSHIP_PATH
    assert "assign_to_team" in FORBIDDEN_ON_OWNERSHIP_PATH


@pytest.mark.parametrize("node", sorted(FORBIDDEN_ON_OWNERSHIP_PATH))
def test_a_forbidden_node_is_refused_on_an_ownership_path(engine, node):
    """ "this could prevent your Router from being published" - so refuse it here."""
    with pytest.raises(ForbiddenNodeOnOwnershipPath) as caught:
        add_link(engine, nodes=["create_event", node])
    assert node in str(caught.value)
    assert caught.value.status == 400


def test_the_refusal_quotes_the_researched_warning(engine):
    with pytest.raises(ForbiddenNodeOnOwnershipPath) as caught:
        add_link(engine, nodes=["assign_to"])
    assert "published" in str(caught.value)


def test_a_forbidden_node_is_allowed_on_a_non_ownership_path(engine):
    """The research forbids these on *Ownership* paths specifically."""
    link = add_link(engine, nodes=["update_ownership"], ownership_path=False)
    assert "update_ownership" in link["data"]["nodes"]


def test_an_allowed_node_survives_a_declaration(engine):
    link = add_link(engine, nodes=["Create Event", "display_calendar"])
    assert link["data"]["nodes"] == ["create_event", "display_calendar"]


def test_the_advisory_check_reports_without_refusing():
    warnings = ownership_rules.nodes_warnings(["create_event", "assign_to"], ownership_path=True)
    assert [entry["node"] for entry in warnings] == ["assign_to"]
    assert warnings[0]["severity"] == "refused"


def test_the_advisory_check_is_quiet_on_another_path():
    assert ownership_rules.nodes_warnings(["update_ownership"], ownership_path=False) == []


def test_the_guardrail_endpoint_checks_a_list_without_refusing(http):
    body = http.get(
        f"{PREFIX}/nodes/guardrail", params={"nodes": "create_event,assign_to_team"}
    ).json()
    assert [entry["node"] for entry in body["warnings"]] == ["assign_to_team"]
    assert body["ownership_path"]["forbidden"] == list(FORBIDDEN_ON_OWNERSHIP_PATH)


def test_the_guardrail_endpoint_is_quiet_with_no_nodes(http):
    assert http.get(f"{PREFIX}/nodes/guardrail").json()["warnings"] == []


# --------------------------------------------------------------------------- #
# Pre-resolved owners: the researched extensibility note
# --------------------------------------------------------------------------- #


def test_a_pre_resolved_link_skips_the_crm_lookup(engine, store, room):
    basic_workspace(store)
    _, opened = (
        open_route(engine, room["id"], resolution_source="pre_resolved", owner_id="005-ana")
        if False
        else (
            None,
            engine.init_simple(
                add_link(engine, resolution_source="pre_resolved")["id"],
                {"owner_id": "005-nadia"},
                room_id=room["id"],
                actor="dana",
                source=SOURCE,
            ),
        )
    )
    assert opened["owner_source"] == "pre_resolved"
    assert opened["owner_id"] == "005-nadia"


def test_a_pre_resolved_owner_is_still_looked_up_as_a_rep(engine, store, room):
    """A rep with no calendar has no availability, so there is nothing to book."""
    basic_workspace(store)
    link = add_link(engine, resolution_source="pre_resolved")
    with pytest.raises(OwnerUnknown):
        engine.init_simple(
            link["id"],
            {"owner_id": "005-not-a-rep"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )


def test_a_pre_resolved_link_without_an_owner_id_is_refused(engine, store, room):
    basic_workspace(store)
    link = add_link(engine, resolution_source="pre_resolved")
    with pytest.raises(OwnershipError) as caught:
        engine.init_simple(link["id"], {}, room_id=room["id"], actor="dana", source=SOURCE)
    assert "already knows" in str(caught.value)


def test_an_unknown_resolution_source_is_refused(engine):
    with pytest.raises(LinkError):
        add_link(engine, resolution_source="telepathy")


# --------------------------------------------------------------------------- #
# Reads, and the summary the page reads
# --------------------------------------------------------------------------- #


def test_the_summary_counts_links_chains_routes_and_bookings(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(
        engine,
        room["id"],
        rules=[
            {"kind": "crm_ownership", "name": "Enterprise", "team": "Enterprise"},
            {"kind": CATCH_ALL, "owner_id": "005-desk"},
        ],
    )
    engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    totals = engine.summary()
    assert totals["links"] == 1
    assert totals["links_with_chain"] == 1
    assert totals["routes"] == 1
    assert totals["routes_booked"] == 1
    assert totals["bookings_confirmed"] == 1
    assert totals["decisions_by_outcome"] == {"resolved": 1}


def test_the_summary_is_room_scoped_over_the_same_rows(engine, store, room, other_room):
    basic_workspace(store)
    open_route(engine, room["id"])
    open_route(engine, other_room["id"])
    assert engine.summary()["routes"] == 2
    assert engine.summary(room_id=room["id"])["routes"] == 1


def test_the_summary_counts_only_connected_calendars(engine, store, room):
    add_rep(store, "005-desk", team="Commercial")
    add_rep(store, "005-nadia")
    add_rep(
        store, "005-priya", team="Commercial", calendar={"provider": "google", "connected": False}
    )
    add_record(store, email="lead@example.com", owner_id="005-nadia")
    totals = engine.summary()
    assert totals["reps"] == 3
    assert totals["reps_with_calendar"] == 2


def test_the_catalog_lists_records_reps_and_teams(engine, store, room):
    basic_workspace(store)
    catalog = engine.catalog()
    assert catalog["record_count"] == 1
    assert catalog["rep_count"] == 2
    assert catalog["teams"] == {"Commercial": ["005-desk"], "Enterprise": ["005-nadia"]}


def test_the_teams_map_comes_from_the_reps_themselves(store, engine):
    add_rep(store, "005-a", team="Alpha")
    add_rep(store, "005-b", team="Beta")
    assert engine.teams() == {"Alpha": ["005-a"], "Beta": ["005-b"]}


def test_a_rep_with_no_team_is_in_no_team(engine, store):
    add_rep(store, "005-solo")
    store.update(engine.crm.rep("005-solo")["id"], {"team": ""}, actor="dana", source=LINK_SOURCE)
    assert engine.teams() == {}


def test_the_decisions_can_be_filtered_by_outcome_and_object_type(engine, store, room):
    basic_workspace(store)
    open_route(engine, room["id"])
    open_route(engine, room["id"], email="stranger@elsewhere.example")
    assert len(engine.decisions(outcome="catch_all")) == 1
    assert len(engine.decisions(outcome="resolved")) == 1
    assert len(engine.decisions(owner_id="005-nadia")) == 1


def test_the_routes_can_be_filtered_by_state(engine, store, room):
    basic_workspace(store)
    _, opened = open_route(engine, room["id"])
    open_route(engine, room["id"])
    engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    assert len(engine.routes(state="booked")) == 1
    assert len(engine.routes(state="open")) == 1


def test_a_link_can_be_patched_and_revalidated_together(engine, store, room):
    basic_workspace(store)
    link = add_link(engine)
    patched = engine.update_link(link["id"], {"name": "Renamed"}, actor="dana", source=LINK_SOURCE)
    assert patched["data"]["name"] == "Renamed"
    assert patched["data"]["type"] == OWNERSHIP


def test_patching_a_link_into_a_round_robin_fails(engine, store):
    basic_workspace(store)
    link = add_link(engine)
    with pytest.raises(LinkTypeNotSupported):
        engine.update_link(link["id"], {"type": "RoundRobin"}, actor="dana", source=LINK_SOURCE)


def test_patching_a_link_into_a_chain_with_no_catch_all_fails(engine, store):
    """The link must not be left holding one."""
    basic_workspace(store)
    link = add_link(engine)
    with pytest.raises(RulesDoNotFallThrough):
        engine.update_link(
            link["id"],
            {"rules": [{"kind": "crm_ownership", "name": "Only", "team": "Enterprise"}]},
            actor="dana",
            source=LINK_SOURCE,
        )


def test_deleting_a_link_keeps_its_bookings_readable(engine, store, room):
    basic_workspace(store)
    link, opened = open_route(engine, room["id"])
    booked = engine.book(
        opened["routing_id"],
        {"startTime": opened["start_times"][0], "guestEmail": "lead@example.com"},
        room_id=room["id"],
        actor="dana",
        source=BOOK_SOURCE,
    )
    engine.delete_link(link["id"], actor="dana", source=f"DELETE {PREFIX}/links/{link['id']}")
    assert engine.get_link(link["id"]) is None
    assert engine.get_booking(booked["id"])["data"]["guest_email"] == "lead@example.com"


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def seed_http(client):
    """A room and enough workspace for one prospect to reach one rep."""
    room = client.post(
        "/api/records/room", json={"name": "Northwind", "account": "Northwind"}
    ).json()
    client.post(
        "/api/records/crm_owner_rep",
        json={
            "owner_id": "005-nadia",
            "name": "Nadia",
            "team": "Enterprise",
            "calendar": {"provider": "google", "connected": True},
        },
    )
    client.post(
        "/api/records/crm_owner_rep",
        json={
            "owner_id": "005-desk",
            "name": "Desk",
            "team": "Commercial",
            "calendar": {"provider": "outlook", "connected": True},
        },
    )
    client.post(
        "/api/records/crm_owner_record",
        json={
            "object_type": "lead",
            "email": "lead@example.com",
            "owner_id": "005-nadia",
        },
    )
    return room


def create_http_link(client, **overrides):
    payload = {
        "type": "Ownership",
        "linkId": "own_http",
        "interval": {
            "start": (NOW + timedelta(days=1)).isoformat(),
            "end": (NOW + timedelta(days=8)).isoformat(),
            "duration_minutes": 30,
        },
        "rules": [{"kind": "catch_all", "name": "Deal desk", "owner_id": "005-desk"}],
    }
    payload.update(overrides)
    return client.post(f"{PREFIX}/links", json=payload)


def test_the_vocabulary_and_inferences_are_served_without_a_store(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200
    assert http.get(f"{PREFIX}/inferences").status_code == 200
    assert http.get(f"{PREFIX}/catalog").status_code == 200
    assert http.get(f"{PREFIX}/summary").status_code == 200


def test_a_link_can_be_declared_over_http(http):
    seed_http(http)
    response = create_http_link(http)
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["type"] == OWNERSHIP
    assert body["has_catch_all"] is True


def test_a_refused_link_type_answers_400_with_its_code(http):
    seed_http(http)
    response = create_http_link(http, type="RoundRobin")
    assert response.status_code == 400
    assert response.json()["error"] == "ownership_link_type_not_supported"


def test_a_forbidden_node_answers_400_over_http(http):
    seed_http(http)
    response = create_http_link(http, nodes=["assign_to"])
    assert response.status_code == 400
    assert response.json()["error"] == "ownership_forbidden_node_on_path"


def test_a_chain_with_no_catch_all_answers_400_over_http(http):
    seed_http(http)
    response = create_http_link(
        http, rules=[{"kind": "crm_ownership", "name": "Only", "team": "Enterprise"}]
    )
    assert response.status_code == 400
    assert response.json()["error"] == "ownership_rules_no_catch_all"


def test_the_full_two_call_flow_works_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    )
    assert opened.status_code == 201
    body = opened.json()
    assert body["owner_id"] == "005-nadia"
    assert body["start_times"]

    booked = http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": body["routing_id"],
            "startTime": body["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    )
    assert booked.status_code == 201
    assert booked.json()["data"]["state"] == "confirmed"


def test_a_missing_guest_email_answers_400_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    response = http.post(f"{PREFIX}/rooms/{room['id']}/init-simple", json={"link_id": link["id"]})
    assert response.status_code == 400
    assert response.json()["error"] == "ownership_guest_email_required"


def test_a_consumed_session_answers_409_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    payload = {
        "routing_id": opened["routing_id"],
        "startTime": opened["start_times"][0],
        "guestEmail": "lead@example.com",
    }
    assert (
        http.post(f"{PREFIX}/rooms/{room['id']}/schedule-simple", json=payload).status_code == 201
    )
    second = http.post(f"{PREFIX}/rooms/{room['id']}/schedule-simple", json=payload)
    assert second.status_code == 409
    assert second.json()["error"] == "ownership_route_consumed"


def test_an_unoffered_slot_answers_409_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": (NOW + timedelta(days=3, hours=11, minutes=7)).isoformat(),
            "guestEmail": "lead@example.com",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "ownership_slot_not_offered"


def test_an_unknown_routing_id_answers_404_over_http(http):
    room = seed_http(http)
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": "crm_owner_route_nope",
            "startTime": NOW.isoformat(),
            "guestEmail": "lead@example.com",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "ownership_route_not_found"


def test_check_writes_nothing_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    before = http.get("/api/stats").json()["records"]
    response = http.post(
        f"{PREFIX}/rooms/{room['id']}/check",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["would_route_to"] == "005-nadia"
    assert response.json()["wrote"] is False
    assert http.get("/api/stats").json()["records"] == before


def test_a_room_that_does_not_exist_is_404_on_the_room_scoped_routes(http):
    assert (
        http.post(f"{PREFIX}/rooms/room_absent/init-simple", json={"link_id": "x"}).status_code
        == 404
    )
    assert http.post(f"{PREFIX}/rooms/room_absent/check", json={"link_id": "x"}).status_code == 404
    assert (
        http.post(
            f"{PREFIX}/rooms/room_absent/schedule-simple", json={"routing_id": "x"}
        ).status_code
        == 404
    )
    assert http.get(f"{PREFIX}/rooms/room_absent/bookings").status_code == 404


def test_a_missing_link_id_in_the_body_is_400(http):
    room = seed_http(http)
    response = http.post(f"{PREFIX}/rooms/{room['id']}/init-simple", json={})
    assert response.status_code == 400
    assert "link_id" in response.json()["detail"]


def test_links_can_be_listed_read_patched_and_deleted_over_http(http):
    seed_http(http)
    created = create_http_link(http).json()
    assert http.get(f"{PREFIX}/links").json()["count"] == 1
    assert http.get(f"{PREFIX}/links/{created['id']}").status_code == 200
    patched = http.patch(f"{PREFIX}/links/{created['id']}", json={"name": "Renamed"})
    assert patched.json()["data"]["name"] == "Renamed"
    assert http.delete(f"{PREFIX}/links/{created['id']}").status_code == 204
    assert http.get(f"{PREFIX}/links/{created['id']}").status_code == 404


def test_a_missing_link_is_404_over_http(http):
    assert http.get(f"{PREFIX}/links/crm_owner_link_nope").status_code == 404
    assert http.delete(f"{PREFIX}/links/crm_owner_link_nope").status_code == 404


def test_records_and_reps_are_readable_over_http(http):
    seed_http(http)
    records = http.get(f"{PREFIX}/records").json()
    assert records["count"] == 1
    assert records["collection"] == RECORD_COLLECTION
    reps = http.get(f"{PREFIX}/reps").json()
    assert reps["count"] == 2
    assert reps["collection"] == REP_COLLECTION


def test_records_can_be_filtered_by_object_type_and_owner(http):
    seed_http(http)
    http.post(
        "/api/records/crm_owner_record",
        json={
            "object_type": "contact",
            "email": "c@example.com",
            "owner_id": "005-desk",
        },
    )
    assert http.get(f"{PREFIX}/records", params={"object_type": "contact"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/records", params={"owner_id": "005-desk"}).json()["count"] == 1


def test_routes_bookings_and_decisions_are_readable_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    )
    assert http.get(f"{PREFIX}/routes").json()["count"] == 1
    assert http.get(f"{PREFIX}/routes", params={"state": "booked"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/routes/{opened['routing_id']}").status_code == 200
    bookings = http.get(f"{PREFIX}/rooms/{room['id']}/bookings").json()
    assert bookings["count"] == 1
    assert http.get(f"{PREFIX}/bookings/{bookings['bookings'][0]['id']}").status_code == 200
    assert http.get(f"{PREFIX}/decisions").json()["count"] == 1


def test_an_unknown_route_or_booking_is_404_over_http(http):
    assert http.get(f"{PREFIX}/routes/crm_owner_route_nope").status_code == 404
    assert http.get(f"{PREFIX}/bookings/crm_owner_booking_nope").status_code == 404


def test_a_booking_can_be_cancelled_over_http(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    booked = http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    ).json()
    cancelled = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booked['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["state"] == "cancelled"
    assert (
        http.post(f"{PREFIX}/rooms/{room['id']}/bookings/{booked['id']}/cancel").status_code == 409
    )


def test_cancelling_an_unknown_booking_is_404_over_http(http):
    room = seed_http(http)
    response = http.post(f"{PREFIX}/rooms/{room['id']}/bookings/crm_owner_booking_nope/cancel")
    assert response.status_code == 404


def test_the_summary_endpoint_reads_the_rooms_own_rows(http):
    room = seed_http(http)
    link = create_http_link(http, room_id=room["id"]).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    )
    scoped = http.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert scoped["routes"] == 1
    assert scoped["links"] == 1
    assert scoped["decisions_by_outcome"] == {"resolved": 1}


def test_a_link_declared_without_a_room_is_account_scoped(http):
    """The researched link is a workspace asset, so it is not room-owned."""
    seed_http(http)
    create_http_link(http)
    room_id = http.get("/api/records/room").json()["records"][0]["id"]
    assert http.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()["links"] == 0


def test_the_audit_trail_records_the_decision_and_the_booking_in_one_place(http):
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    )
    entries = http.get("/api/audit", params={"limit": 200}).json()["entries"]
    mine = [entry for entry in entries if PREFIX in (entry["source"] or "")]
    assert any(entry["collection"] == ROUTE_COLLECTION for entry in mine)
    assert any(entry["collection"] == BOOKING_COLLECTION for entry in mine)
    assert any(entry["collection"] == DECISION_COLLECTION for entry in mine)
    assert any(entry["collection"] == LINK_COLLECTION for entry in mine)


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_feature_records_names_a_mounted_route(http):
    """The defect the contract names by name, asserted against the live registry.

    A feature's audit log once kept recording a path the app had stopped serving,
    so this walks every row the feature wrote and checks it against what the host
    actually mounted - not against a list written here that could drift from the
    route table.
    """
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    )
    http.patch(f"{PREFIX}/links/{link['id']}", json={"name": "Renamed"})
    http.delete(f"{PREFIX}/links/{link['id']}")

    served = all_served_routes(http)
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    # Matched on the prefix *anywhere* in the source rather than at its start: the
    # routes write "POST /api/wf-053/links", so a `startswith` filter would match
    # nothing and this test would pass vacuously on an empty list.
    mine = [entry for entry in entries if PREFIX in (entry["source"] or "")]
    assert mine, "the feature recorded no audit rows at all"
    for entry in mine:
        assert source_names_a_mounted_route(entry["source"], served), (
            f"audit row names {entry['source']!r}, which the app does not serve"
        )


def test_the_seed_source_is_not_claimed_by_a_route(http):
    """``source="seed"`` is the seeder, not a route, so the rule above must exempt it."""
    assert not source_names_a_mounted_route("seed", all_served_routes(http))


def test_source_is_a_required_keyword_on_every_writing_method(engine):
    """Omitting it is a ``TypeError`` at the call site, not an untraceable row."""
    import inspect

    for name in (
        "create_link",
        "update_link",
        "delete_link",
        "init_simple",
        "book",
        "cancel_booking",
    ):
        method = getattr(OwnershipEngine, name)
        parameter = inspect.signature(method).parameters.get("source")
        assert parameter is not None, f"{name} takes no source"
        assert parameter.default is inspect.Parameter.empty, f"{name} has a default source"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_routes_pass_their_own_prefix_as_the_source(http, monkeypatch):
    """Each write route builds its source from ``router.prefix``, not a literal."""
    from dsr.features import load_feature as load

    feature = load(MODULE)
    seen: list[str] = []
    original = feature.OwnershipEngine

    class Recording(original):
        def init_simple(self, *args, source, **kwargs):
            seen.append(source)
            return super().init_simple(*args, source=source, **kwargs)

        def book(self, *args, source, **kwargs):
            seen.append(source)
            return super().book(*args, source=source, **kwargs)

        def create_link(self, *args, source, **kwargs):
            seen.append(source)
            return super().create_link(*args, source=source, **kwargs)

    monkeypatch.setattr(feature, "OwnershipEngine", Recording)
    room = seed_http(http)
    link = create_http_link(http).json()
    opened = http.post(
        f"{PREFIX}/rooms/{room['id']}/init-simple",
        json={"link_id": link["id"], "guestEmail": "lead@example.com"},
    ).json()
    http.post(
        f"{PREFIX}/rooms/{room['id']}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "lead@example.com",
        },
    )
    assert f"POST {feature.router.prefix}/links" in seen
    assert f"POST {feature.router.prefix}/rooms/{{room_id}}/init-simple" in seen
    assert f"POST {feature.router.prefix}/rooms/{{room_id}}/schedule-simple" in seen


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_a_mixed_demo(store, room):
    """Demo data of only successes teaches a reviewer nothing."""
    from dsr.features import load_feature as load

    db = store.db
    summary = load(MODULE).seed(db, {"room_ids": [(room["id"], "Northwind")], "now": NOW})
    assert "4 reps" in summary
    assert "catch_all" in summary
    assert "ownership_calendar_not_connected" in summary
    assert "ownership_owner_unknown" in summary


def test_the_seeded_demo_is_bookable_end_to_end(store, room):
    from dsr.features import load_feature as load

    load(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW})
    engine = OwnershipEngine(store, clock=lambda: NOW)
    assert engine.summary()["bookings_confirmed"] >= 1
    assert engine.summary()["decisions_by_outcome"].get("catch_all", 0) >= 1


def test_the_seed_says_so_when_there_are_no_rooms(store):
    from dsr.features import load_feature as load

    assert "no rooms" in load(MODULE).seed(store.db, {"room_ids": [], "now": NOW})


def test_the_seed_declares_the_l2a_setting_it_reads(store, room):
    """ "we will use the existing settings defined in your workspace"."""
    from dsr.features import load_feature as load

    load(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW})
    engine = OwnershipEngine(store, clock=lambda: NOW)
    assert engine.crm.lead_to_account_matching() is True


def test_the_seed_window_is_built_from_its_clock(store, room):
    """A fixed week would go stale the first time somebody seeds after it."""
    from dsr.features import load_feature as load

    load(MODULE).seed(store.db, {"room_ids": [(room["id"], "Northwind")], "now": NOW})
    engine = OwnershipEngine(store, clock=lambda: NOW)
    for link in engine.links():
        assert datetime.fromisoformat(link["data"]["interval"]["start"]) > NOW
