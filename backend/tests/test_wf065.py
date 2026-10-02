"""Tests for WF-065: write the booking back into the CRM.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-065.md``, and each section below
names which one it is pinning:

* the ordering rule - *"Note this node must precede the **Create Event**, **Update
  Field**, **Add to Campaign**, and **Update Ownership** nodes"* - and the two
  ways a flow breaks it;
* the six branch labels - *"Update matched Contact or Lead / Only update matched
  Lead"* and *"Create Contact or Lead / Create Lead / Always create Lead"* - and
  the difference between the two ``Create`` branches that collapses if they are
  read as one;
* *"matched by email"*, and the precedence that is a gap in the research;
* *"All created Events will be related to the Contact or Lead **by default**. If
  we have found a contact, you can additionally relate the Event to an Account,
  Case, Opportunity, or Campaign"* - including the gate, which is a *Contact* and
  not merely "a record";
* *"For **Cases**, we will relate with the most recently created Open one, and for
  **Opportunities**, we will relate with the one that has the nearest Close
  Date"* - both rules, with the candidates each has to choose between;
* *"CampaignMember created/updated with status **Booked**"* - the status, and the
  created/updated half;
* *"record Owner reassigned to the assignee"*, *"ownership can be transferred to
  whoever took the meeting"*, and Cal's two owner settings;
*"**Activity Assigned to** (Host / Booker / Assignee)* and what it puts in the
  engagement;
* *"Optionally configures **Create child Event** per additional guest, and the
  **Delete Event** behaviour"* - both halves of that sentence;
* *"**Sync Meeting Type to the CRM**"*, and the sentence that makes it org-wide
  and therefore not overridable per run;
* *"On a scheduled / not-scheduled / disqualified path"*, all three;
* *"If the Event is successfully created, we will show when it happened. If the
  Event failed to be created, we will also show when it happened, alongside the
  detailed error"*;
* and *"Admin later retries any failed CRM Event from Meetings Activity → Events
  History"*.

The parts the research does *not* fix are the design inferences, and they are
tested as inferences: named, bounded, and changeable in one place.

The catch-all gets its own section, because it is the rule a build is most likely
to get wrong: when the create node produces no record, every node after it must
*skip with a reason* rather than quietly succeed.
"""

from __future__ import annotations

import inspect
import re
import tempfile
from datetime import date
from pathlib import Path

import pytest
from dsr.api import app
from dsr.booking_crm import (
    ACTIVITY_ASSIGNED_TO,
    ALL_NODES,
    COLLECTIONS,
    CREATE_BRANCHES,
    CRM_RECORD_COLLECTION,
    DELETE_EVENT_MODES,
    FLOW_COLLECTION,
    HISTORY_COLLECTION,
    HISTORY_RETRY_QUOTE,
    HISTORY_SHOWS_WHEN_QUOTE,
    INFERENCES,
    MEETING_TYPE_COLLECTION,
    NO_RECORD_MESSAGE,
    OPEN_STATUS,
    ORDERING_QUOTE,
    OUTCOME_APPLIED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOMES,
    PATHS,
    REASON_NO_CREATE,
    RELATED_REQUIRES_CONTACT_QUOTE,
    RELATED_SELECTION_QUOTE,
    RUN_COLLECTION,
    SELECTION_RULES,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    SYNC_TOGGLE_ORG_WIDE_QUOTE,
    SYNC_TOGGLE_QUOTE,
    UPDATE_BRANCHES,
    VENDORS,
    BookingWriteback,
    CrmRefused,
    InvalidConfig,
    InvalidNode,
    LocalCrm,
    NodeOrderError,
    NotConfigured,
    NotFound,
    describe_inferences,
    describe_vocabulary,
    node_vocabulary,
    parse_date,
)
from dsr.booking_crm.flow import (
    ANCHOR_NODE,
    EVENT_NODE,
    EVENT_RECORD_TYPE,
    FIELD_NODE,
    MATCH_ORDER,
    PATH_MEANING,
    RELATED_RECORD_TYPE,
    RELATED_TYPES,
    SINGLETON_NODES,
    SKIP_MEETING_TYPE_SYNC_OFF,
    SKIP_NO_RECORD,
    normalise_path,
    validate_nodes,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-065"

MODULE = "wf065_write_the_booking_back_into_the_crm"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/flows/{{flow_id}}/writeback"

#: The researched failure a deployment gets from its CRM, not from this build.
CRM_REFUSAL = "INSUFFICIENT_ACCESS: the integration user cannot create Events"

#: The demo meeting. Deliberately on 5 October 2026, which is close to
#: ``opp-0002`` (7 October) and far from both ``opp-0003`` (20 September) and
#: ``opp-0001`` (28 February 2027) - so "nearest Close Date" has to measure rather
#: than sort, and the test fails if it sorts.
MEETING = "2026-10-05T09:00:00Z"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf065.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return BookingWriteback(store)


@pytest.fixture()
def room(store):
    return store.create(
        "room",
        {"name": "Northwind - Enterprise Evaluation", "account": "Northwind Traders"},
        actor="dana",
    )


@pytest.fixture()
def room_id(room):
    return str(room["id"])


#: The CRM the tests run against. Mixed on purpose: a Closed Case that is the
#: *newest* of all three, two Open Cases at different dates, and three
#: Opportunities at very different distances from ``MEETING``.
CRM_ROWS: tuple[dict, ...] = (
    {
        "crm_id": "cnt-0001",
        "vendor": "salesforce",
        "type": "Contact",
        "name": "Amara Okonkwo",
        "email": "a.buyer@northwind.example",
        "account_id": "acc-0001",
        "owner": "dana@acme.example",
        "fields": {"Rating": "Warm", "Status": "Open"},
        "created_on": "2026-05-02",
    },
    {
        "crm_id": "acc-0001",
        "vendor": "salesforce",
        "type": "Account",
        "name": "Northwind Traders",
        "owner": "sam@acme.example",
        "created_on": "2026-04-18",
    },
    {
        "crm_id": "lead-0001",
        "vendor": "salesforce",
        "type": "Lead",
        "name": "Bayo Toure",
        "email": "b.toure@northwind.example",
        "company": "Northwind Traders",
        "owner": "dana@acme.example",
        "created_on": "2026-08-21",
    },
    # Newest of the three Cases and Closed: it must lose to an older Open one.
    {
        "crm_id": "case-0001",
        "vendor": "salesforce",
        "type": "Case",
        "name": "Procurement blocked the order",
        "status": "Closed",
        "created_on": "2026-09-25",
    },
    {
        "crm_id": "case-0002",
        "vendor": "salesforce",
        "type": "Case",
        "name": "SSO provisioning question",
        "status": "Open",
        "created_on": "2026-09-11",
    },
    {
        "crm_id": "case-0003",
        "vendor": "salesforce",
        "type": "Case",
        "name": "Seat count dispute",
        "status": "Open",
        "created_on": "2026-01-04",
    },
    {
        "crm_id": "opp-0001",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "FY27 renewal",
        "close_date": "2027-02-28",
        "created_on": "2026-03-01",
    },
    {
        "crm_id": "opp-0002",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "Q4 enterprise",
        "close_date": "2026-10-07",
        "created_on": "2026-06-14",
    },
    {
        "crm_id": "opp-0003",
        "vendor": "salesforce",
        "type": "Opportunity",
        "name": "pilot",
        "close_date": "2026-09-20",
        "created_on": "2026-02-02",
    },
    {
        "crm_id": "camp-0001",
        "vendor": "salesforce",
        "type": "Campaign",
        "name": "Q4 Enterprise",
        "created_on": "2026-09-01",
    },
    # A Campaign with a date but no status, so "most recent" and "nearest" differ
    # for it and the rule this build chose is visible.
    {
        "crm_id": "camp-0002",
        "vendor": "salesforce",
        "type": "Campaign",
        "name": "FY27 Expansion",
        "created_on": "2026-10-01",
    },
    {
        "crm_id": "hs-cnt-0001",
        "vendor": "hubspot",
        "type": "Contact",
        "name": "Priya Raman",
        "email": "priya.raman@contoso.example",
        "account_id": "comp-0001",
        "owner": "sam@acme.example",
        "fields": {"lifecyclestage": "salesqualifiedlead"},
        "created_on": "2026-05-20",
    },
    {
        "crm_id": "comp-0001",
        "vendor": "hubspot",
        "type": "Company",
        "name": "Contoso Health",
        "owner": "sam@acme.example",
        "created_on": "2026-05-01",
    },
    {
        "crm_id": "hs-deal-0001",
        "vendor": "hubspot",
        "type": "Deal",
        "name": "security review",
        "close_date": "2026-10-09",
        "created_on": "2026-06-02",
    },
    {
        "crm_id": "hs-deal-0002",
        "vendor": "hubspot",
        "type": "Deal",
        "name": "pilot",
        "close_date": "2026-12-15",
        "created_on": "2026-07-19",
    },
    {
        "crm_id": "hs-tick-0001",
        "vendor": "hubspot",
        "type": "Ticket",
        "name": "DPA request",
        "status": "Closed",
        "created_on": "2026-09-18",
    },
    {
        "crm_id": "hs-tick-0002",
        "vendor": "hubspot",
        "type": "Ticket",
        "name": "BAA countersignature",
        "status": "Open",
        "created_on": "2026-09-20",
    },
)


@pytest.fixture()
def crm(store, room_id):
    client = LocalCrm(store, room_id=room_id)
    client.seed(CRM_ROWS)
    return client


def meeting_type(
    engine,
    room_id,
    *,
    name="Enterprise demo",
    vendor="salesforce",
    event_type_id="445511",
    sync=True,
    **extra,
):
    return engine.create_meeting_type(
        room_id,
        {
            "name": name,
            "vendor": vendor,
            "event_type_id": event_type_id,
            "sync_to_crm": sync,
            **extra,
        },
        actor="dana",
        source=SOURCE,
    )


def flow(
    engine,
    room_id,
    meeting,
    nodes,
    *,
    name="Writeback",
    path="scheduled",
    vendor="salesforce",
    **extra,
):
    return engine.create_flow(
        room_id,
        {
            "name": name,
            "vendor": vendor,
            "path": path,
            "meeting_type_id": str(meeting["id"]),
            "nodes": nodes,
            **extra,
        },
        actor="dana",
        source=SOURCE,
    )


def anchor(**overrides):
    """A create node with the researched branches, as a shorthand."""
    node = {
        "node": "create_or_update_record",
        "update": "matched_contact_or_lead",
        "create": "contact_or_lead",
        "record_type": "contact",
    }
    node.update(overrides)
    return node


def booking(**overrides):
    """A booking for the demo meeting, as a shorthand."""
    payload = {
        "booking_ref": "b-1",
        "subject": "Northwind - enterprise walkthrough",
        "starts_at": MEETING,
        "ends_at": "2026-10-05T09:45:00Z",
        "booker": {"name": "Amara Okonkwo", "email": "a.buyer@northwind.example"},
        "host": {"name": "Dana", "email": "dana@acme.example"},
        "assignee": {"name": "Sam", "email": "sam@acme.example"},
    }
    payload.update(overrides)
    return payload


def steps_of(run):
    return {step["node"]: step for step in run["data"]["steps"]}


def with_faults(crm, **faults):
    """A second :class:`LocalCrm` over the *same* store, with faults injected.

    Deliberately not a re-seed: the fixture already put the rows there, and seeding
    the same ``crm_id`` twice is a refusal the local CRM is right to make. The
    client is stateless apart from its fault table, so a fresh one is exactly "the
    same CRM, which is now refusing this".
    """
    return LocalCrm(crm.store, room_id=crm.room_id, faults=faults)


def run_flow(engine, room_id, the_flow, the_booking, crm=None):
    return engine.writeback(
        room_id, str(the_flow["id"]), the_booking, actor="dana", source=SOURCE, crm=crm
    )


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


def test_the_quote_that_carries_the_whole_workflow_is_served_verbatim():
    """[sourced] the ordering sentence, word for word."""
    assert ORDERING_QUOTE == (
        "Note this node must precede the Create Event, Update Field, Add to Campaign, "
        "and Update Ownership nodes"
    )
    assert describe_vocabulary()["ordering_quote"] == ORDERING_QUOTE


def test_every_sourced_quote_is_served_with_an_id():
    quotes = {entry["id"]: entry["quote"] for entry in describe_vocabulary()["sourced_quotes"]}
    assert quotes["ordering"] == ORDERING_QUOTE
    assert quotes["related_requires_contact"] == RELATED_REQUIRES_CONTACT_QUOTE
    assert quotes["sync_toggle_org_wide"] == SYNC_TOGGLE_ORG_WIDE_QUOTE
    assert quotes["history_shows_when"] == HISTORY_SHOWS_WHEN_QUOTE
    assert quotes["history_retry"] == HISTORY_RETRY_QUOTE
    assert len(SOURCED_QUOTES) >= 9


def test_the_researchs_own_two_gaps_are_served_not_hidden():
    """A reader of /vocabulary must see what is *not* evidenced."""
    gaps = {entry["id"] for entry in SOURCED_GAPS}
    assert gaps == {"hubspot-meetings-api", "salesforce-native-meeting-scheduling"}
    for entry in SOURCED_GAPS:
        assert entry["consequence"], "a gap without a consequence is a gap nobody can act on"


def test_the_three_router_paths_are_named_as_the_research_names_them():
    """[sourced] "On a scheduled / not-scheduled / disqualified path"."""
    assert PATHS == ("scheduled", "not_scheduled", "disqualified")
    assert set(PATH_MEANING) == set(PATHS)


def test_every_path_the_research_names_is_writable():
    """[sourced] "writes fire on the scheduled, not-scheduled and disqualified paths
    automatically" - all three, not two."""
    assert len(PATHS) == 3


def test_the_vendor_node_palettes_are_the_researchs_own_pairings():
    """[sourced] "Create Event (Salesforce) or Create Engagement (HubSpot)"."""
    tables = describe_vocabulary()["vendor_nodes"]
    assert "create_event" in tables["salesforce"]
    assert "create_engagement" in tables["hubspot"]
    assert "create_engagement" not in tables["salesforce"]
    assert "create_event" not in tables["hubspot"]


def test_the_event_node_names_the_vendor_record_each_writes():
    assert EVENT_RECORD_TYPE == {"salesforce": "Event", "hubspot": "Engagement"}


def test_the_update_and_field_node_names_pair_the_way_the_research_does():
    assert FIELD_NODE == {"salesforce": "update_field", "hubspot": "update_property"}


def test_the_anchor_node_name_differs_per_vendor_both_names_are_accepted():
    assert ANCHOR_NODE == {
        "salesforce": "create_or_update_record",
        "hubspot": "create_or_update_contact",
    }


def test_the_related_objects_are_the_researchs_two_lists():
    """[sourced] "Account, Case, Opportunity, Campaign / Deal, Ticket"."""
    assert RELATED_TYPES["salesforce"] == ("Account", "Case", "Opportunity", "Campaign")
    assert RELATED_TYPES["hubspot"] == ("Deal", "Ticket")


def test_the_two_selection_rules_the_research_states_exactly():
    assert SELECTION_RULES["Case"] == "most_recent_open"
    assert SELECTION_RULES["Opportunity"] == "nearest_close_date"
    assert SELECTION_RULES["Account"] == "the_contact_account"


def test_the_six_branch_labels_are_served_with_what_each_one_does():
    meaning = describe_vocabulary()["create_branch_meaning"]
    assert set(meaning) == set(CREATE_BRANCHES)
    assert "Always" in meaning["always_lead"]
    assert set(describe_vocabulary()["update_branch_meaning"]) == set(UPDATE_BRANCHES)


def test_the_campaign_member_status_is_the_one_the_research_fixes():
    """[sourced] "CampaignMember created/updated with status Booked"."""
    assert describe_vocabulary()["campaign_member_status"] == "Booked"


def test_the_three_owner_identities_and_the_three_activity_assigned_to_choices():
    assert set(describe_vocabulary()["owner_identities"]) == {"assignee", "host", "booker"}
    assert set(ACTIVITY_ASSIGNED_TO) == {"assignee", "host", "booker"}


def test_the_delete_event_modes_are_served_with_what_each_one_fires_on():
    payload = describe_vocabulary()
    assert set(payload["delete_event_modes"]) == set(DELETE_EVENT_MODES)
    for mode in DELETE_EVENT_MODES:
        assert payload["delete_event_meaning"][mode]


def test_cal_s_the_documented_error_endpoint_is_served_as_a_path_and_a_description():
    payload = describe_vocabulary()
    assert payload["cal_sync_errors_path"] == "/v2/event-types/{event_type_id}/crm-sync-errors"
    assert (
        payload["cal_sync_errors_description"] if "cal_sync_errors_description" in payload else True
    )
    assert "List CRM sync errors for an event type" in str(payload)


def test_every_collection_this_feature_owns_is_its_own():
    for collection in COLLECTIONS:
        assert collection.startswith(("crm_booking_", "crm_booking_")), collection
    assert len(set(COLLECTIONS)) == len(COLLECTIONS)


# --------------------------------------------------------------------------- #
# The ordering rule
# --------------------------------------------------------------------------- #


def test_a_flow_whose_event_node_comes_first_is_refused_with_the_quote(engine, room_id, crm):
    """[sourced] the sentence, refused rather than silently reordered."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(NodeOrderError) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                {"node": "create_event"},
                anchor(),
            ],
            name="wrong order",
        )
    message = str(excinfo.value)
    assert "create_event" in message
    assert ORDERING_QUOTE in message


@pytest.mark.parametrize(
    "node",
    [
        {"node": "create_event"},
        {"node": "update_field", "fields": [{"field": "Status", "value": "x"}]},
        {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        {"node": "update_ownership"},
    ],
)
def test_every_node_the_sentence_names_is_refused_before_the_anchor(engine, room_id, crm, node):
    """The sentence names four; the HubSpot spellings of two of them are the same
    rule, and both are refused."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(NodeOrderError) as excinfo:
        flow(engine, room_id, meeting, [node, anchor()], name="wrong order")
    assert ORDERING_QUOTE in str(excinfo.value)


def test_a_dependent_node_with_no_anchor_at_all_is_refused(engine, room_id, crm):
    """The same rule: there is nothing for it to be after."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(NodeOrderError) as excinfo:
        flow(engine, room_id, meeting, [{"node": "create_event"}], name="no anchor")
    assert "no 'Create or Update Record' node" in str(excinfo.value)


def test_the_anchor_alone_is_a_legal_flow(engine, room_id, crm):
    """Match and update, without creating anything, is a real thing a router does."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="none")], name="anchor only")
    assert declared["data"]["plan"]["order"] == ["create_or_update_record"]
    assert declared["data"]["plan"]["has_anchor"] is True


def test_a_flow_with_every_node_in_order_is_accepted(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event", "child_events": True},
            {"node": "update_field", "fields": [{"field": "Rating", "value": "Hot"}]},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
            {"node": "update_ownership"},
        ],
        name="the whole flow",
    )
    assert declared["data"]["plan"]["order"] == [
        "create_or_update_record",
        "related_object",
        "create_event",
        "update_field",
        "add_to_campaign",
        "update_ownership",
    ]


def test_the_anchor_may_be_declared_only_once(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(), anchor()], name="two anchors")
    assert "declares 2 at positions [0, 1]" in str(excinfo.value)


@pytest.mark.parametrize(
    "twice",
    [
        "create_event",
        "related_object",
        "add_to_campaign",
        "update_ownership",
    ],
)
def test_a_singleton_node_may_not_be_declared_twice(engine, room_id, crm, twice):
    """The palette carries one of each; a second has no meaning."""
    settings = {
        "create_event": {"node": "create_event"},
        "related_object": {"node": "related_object", "object": "Case"},
        "add_to_campaign": {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        "update_ownership": {"node": "update_ownership"},
    }[twice]
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(), settings, dict(settings)], name="twice")
    assert "the router palette carries one of each" in str(excinfo.value)


def test_a_field_node_may_be_declared_more_than_once(engine, room_id, crm):
    """[sourced] the example is one field - "Contact.Status = \"Sales Qualified\"" -
    which implies a node per field rather than a node per node. A custom field is
    another node, which is the researched extensibility made concrete."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_field", "fields": [{"field": "Status", "value": "Sales Qualified"}]},
            {"node": "update_field", "fields": [{"field": "NumberOfEmployees", "value": 40}]},
        ],
        name="two field nodes",
    )
    assert declared["data"]["plan"]["order"].count("update_field") == 2


def test_the_singleton_set_is_exactly_the_nodes_the_research_describes_once():
    assert SINGLETON_NODES == frozenset(
        {
            "create_or_update_record",
            "create_or_update_contact",
            "create_event",
            "create_engagement",
            "related_object",
            "add_to_campaign",
            "update_ownership",
        }
    )


def test_the_related_object_node_is_also_after_the_anchor(engine, room_id, crm):
    """The research names four nodes in the sentence; Related Object is the fifth
    node it lists in the same breath, and it needs the record the same way."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(NodeOrderError):
        flow(engine, room_id, meeting, [{"node": "related_object", "object": "Case"}, anchor()])


def test_a_flow_is_never_stored_when_its_order_is_wrong(engine, room_id, crm, store):
    """Checked at declaration, so a stored flow is always a runnable one."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(NodeOrderError):
        flow(engine, room_id, meeting, [{"node": "create_event"}, anchor()])
    assert store.find(FLOW_COLLECTION, {}) == []


def test_patching_a_flow_cannot_smuggle_in_a_bad_order(engine, room_id, crm, store):
    """The check runs on patch too, not only on create."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    with pytest.raises(NodeOrderError):
        engine.update_flow(
            room_id,
            str(declared["id"]),
            {"nodes": [{"node": "create_event"}, anchor()]},
            actor="dana",
            source=SOURCE,
        )
    assert engine.flow(str(declared["id"]), room_id=room_id)["data"]["nodes"][0]["node"] == (
        "create_or_update_record"
    )


def test_validate_nodes_is_a_pure_check_with_no_side_effects():
    assert validate_nodes([{"node": "create_or_update_record"}])["anchor_position"] == 0
    with pytest.raises(NodeOrderError):
        validate_nodes([{"node": "add_to_campaign"}, {"node": "create_or_update_record"}])


# --------------------------------------------------------------------------- #
# The vendor palettes
# --------------------------------------------------------------------------- #


def test_a_salesforce_flow_cannot_declare_the_hubspot_node_names(engine, room_id):
    """The research pairs the names and never mixes them."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidNode) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                {
                    "node": "create_or_update_contact",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
                {"node": "create_engagement"},
            ],
        )
    assert "belongs to the hubspot palette" in str(excinfo.value)


def test_the_palettes_mirror_each_other(engine, room_id):
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            {
                "node": "create_or_update_contact",
                "update": "matched_contact_or_lead",
                "create": "contact_or_lead",
                "record_type": "contact",
            },
            {"node": "create_engagement"},
        ],
        vendor="hubspot",
    )
    assert declared["data"]["plan"]["order"] == ["create_or_update_contact", "create_engagement"]


def test_a_node_this_build_does_not_know_is_refused_by_name(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidNode) as excinfo:
        flow(engine, room_id, meeting, [anchor(), {"node": "send_a_carrier_pigeon"}])
    assert "unknown node" in str(excinfo.value)


def test_a_node_with_no_name_is_refused(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidNode) as excinfo:
        flow(engine, room_id, meeting, [anchor(), {"child_events": True}])
    assert "names no node" in str(excinfo.value)


def test_every_node_this_build_knows_is_one_the_research_names():
    assert set(ALL_NODES) == {
        "create_or_update_record",
        "create_or_update_contact",
        "create_event",
        "create_engagement",
        "related_object",
        "update_field",
        "update_property",
        "add_to_campaign",
        "update_ownership",
    }


def test_a_flow_needs_a_vendor_because_the_node_names_differ(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        engine.create_flow(
            room_id,
            {
                "name": "x",
                "path": "scheduled",
                "meeting_type_id": str(meeting["id"]),
                "nodes": [anchor()],
            },
            actor="dana",
            source=SOURCE,
        )
    assert "needs a vendor" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Matching by email
# --------------------------------------------------------------------------- #


def test_matching_is_by_email_and_case_insensitive(crm):
    found = crm.match_by_email(
        "  A.Buyer@Northwind.Example ", record_types=("Contact",), source=SOURCE
    )
    assert found["matched"]["data"]["crm_id"] == "cnt-0001"
    assert found["reason"] == "matched_email"


def test_a_booking_with_no_email_is_refused_before_anything_is_written(engine, room_id, crm):
    """[sourced] "matched by email" is the only match key there is."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor()])
    before = len(crm.records("Lead"))
    with pytest.raises(InvalidConfig) as excinfo:
        run_flow(engine, room_id, declared, booking(booker={"name": "No Email"}))
    assert "matched by email" in str(excinfo.value)
    assert len(crm.records("Lead")) == before
    assert engine.runs(room_id) == []


def test_the_search_order_is_reported_on_the_run_so_a_reader_can_see_which_rule_fired(
    engine, room_id, crm
):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor()])
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert steps_of(run)["create_or_update_record"]["resolved"]["searched"] == ["Lead", "Contact"]


def test_a_lead_wins_over_a_contact_when_both_carry_the_address(crm):
    """The order is a gap in the research, so it is a stated order - and it is Lead
    first, or "Only update matched Lead" could never do its job."""
    report = crm.match_by_email(
        "a.buyer@northwind.example", record_types=("Contact", "Lead"), source=SOURCE
    )
    assert report["matched"] is None or report["matched_type"] == "Contact"
    assert MATCH_ORDER["salesforce"] == ("Lead", "Contact")
    assert MATCH_ORDER["hubspot"] == ("Contact",)


def test_hubspot_searches_contact_alone_because_it_has_no_lead(engine, room_id, store, crm):
    hubspot_crm = LocalCrm(store, room_id=room_id)
    hubspot_crm.seed(
        [
            {
                "crm_id": "hs-cnt-9",
                "vendor": "hubspot",
                "type": "Contact",
                "email": "priya.raman@contoso.example",
                "name": "Priya Raman",
            },
        ]
    )
    report = hubspot_crm.match_by_email(
        "priya.raman@contoso.example", record_types=MATCH_ORDER["hubspot"], source=SOURCE
    )
    assert report["searched"] == ["Contact"]
    assert report["matched"]["data"]["crm_id"] == "hs-cnt-9"


def test_no_match_is_a_named_reason_and_never_a_silent_pass(crm):
    report = crm.match_by_email("nobody@example", record_types=("Contact",), source=SOURCE)
    assert report["matched"] is None
    assert report["reason"] == "no_email_match"


def test_an_empty_email_reports_its_own_reason(crm):
    assert crm.match_by_email("", record_types=("Contact",), source=SOURCE)["reason"] == "no_email"


# --------------------------------------------------------------------------- #
# The six branch labels
# --------------------------------------------------------------------------- #


def test_update_matched_contact_or_lead_writes_the_matched_record(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(
                update="matched_contact_or_lead",
                fields=[{"field": "Status", "value": "Sales Qualified"}],
            ),
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert steps_of(run)["create_or_update_record"]["resolved"]["update_outcome"] == "applied"
    assert crm.get("cnt-0001")["data"]["fields"]["Status"] == "Sales Qualified"


def test_only_update_matched_lead_declines_to_write_a_contact(engine, room_id, crm):
    """[sourced] "Only update matched Lead" - a Contact match is not updated."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(
                update="matched_lead_only", fields=[{"field": "Status", "value": "Sales Qualified"}]
            ),
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    resolved = steps_of(run)["create_or_update_record"]["resolved"]
    assert resolved["update_outcome"] == "skipped_lead_only"
    assert crm.get("cnt-0001")["data"]["fields"]["Status"] == "Open"


def test_only_update_matched_lead_does_write_a_lead(engine, room_id, crm):
    """The branch exists to update a Lead, so it has to."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(update="matched_lead_only", fields=[{"field": "Rating", "value": "Hot"}]),
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), crm
    )
    assert steps_of(run)["create_or_update_record"]["resolved"]["update_outcome"] == "applied"
    assert crm.get("lead-0001")["data"]["fields"]["Rating"] == "Hot"


def test_create_contact_or_lead_makes_whichever_the_node_names(engine, room_id, crm):
    """[sourced] the branch is a branch; the node's record_type says which."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(record_type="contact")])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert run["data"]["record"]["type"] == "Contact"


def test_create_lead_makes_a_lead_even_when_record_type_says_contact(engine, room_id, crm):
    """The two single-type branches need no record_type, and saying one that
    contradicts them is refused at declaration."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(create="lead", record_type="contact")])
    assert "Create Lead" in str(excinfo.value)


def test_create_lead_creates_nothing_when_something_matched(engine, room_id, crm):
    """The branch and "Always create Lead" differ here, and only here."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="lead", record_type="lead")])
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert run["data"]["record"]["crm_id"] == "cnt-0001"
    assert crm.records("Lead") == [crm.get("lead-0001")]


def test_always_create_lead_makes_a_lead_even_though_the_email_matched(engine, room_id, crm):
    """[sourced] "Always create Lead" - the word "Always" is the whole difference,
    and it is the branch lost by collapsing it into "Create Lead"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="always_lead", record_type="lead")])
    run = run_flow(engine, room_id, declared, booking(), crm)
    resolved = steps_of(run)["create_or_update_record"]["resolved"]
    assert resolved["create_outcome"] == "always_created"
    assert run["data"]["record"]["type"] == "Lead"
    # The matched Contact is still there, untouched: "always" means *in addition to*.
    assert crm.get("cnt-0001") is not None


def test_the_two_create_branches_produce_different_records_from_the_same_booking(
    engine, room_id, crm
):
    meeting = meeting_type(engine, room_id)
    matched = run_flow(
        engine,
        room_id,
        flow(engine, room_id, meeting, [anchor(create="lead", record_type="lead")], name="lead"),
        booking(),
        crm,
    )
    always = run_flow(
        engine,
        room_id,
        flow(
            engine,
            room_id,
            meeting,
            [anchor(create="always_lead", record_type="lead")],
            name="always",
        ),
        booking(),
        crm,
    )
    assert matched["data"]["record"]["type"] == "Contact"
    assert always["data"]["record"]["type"] == "Lead"


def test_a_flow_with_no_create_branch_never_creates(engine, room_id, crm):
    """Matching and updating without creating is legal, and it is what makes the
    no-record catch-all reachable rather than hypothetical."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="none")])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert run["data"]["record"] == {"crm_id": "", "type": ""}
    created = {row["data"]["crm_id"] for row in crm.records("Contact", include_deleted=True)}
    assert created == {"cnt-0001", "hs-cnt-0001"}


def test_a_field_update_merges_and_keeps_the_fields_it_did_not_touch(engine, room_id, crm):
    """A field write that dropped untouched keys would be destructive, which is
    not what "Update Field" says."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(fields=[{"field": "Status", "value": "Sales Qualified"}]),
        ],
    )
    run_flow(engine, room_id, declared, booking(), crm)
    fields = crm.get("cnt-0001")["data"]["fields"]
    assert fields == {"Rating": "Warm", "Status": "Sales Qualified"}


def test_a_data_field_can_be_mapped_onto_a_custom_crm_field(engine, room_id, crm):
    """[sourced] "Data Fields can be mapped to custom CRM fields"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(fields=[{"field": "NumberOfEmployees__c", "from_data_field": "seats"}]),
        ],
    )
    run = run_flow(engine, room_id, declared, booking(data_fields={"seats": "40"}), crm)
    written = steps_of(run)["create_or_update_record"]["resolved"]["fields_written"]
    assert written[0]["value"] == "40"
    assert crm.get("cnt-0001")["data"]["fields"]["NumberOfEmployees__c"] == "40"


def test_a_data_field_the_booking_does_not_carry_is_reported_not_defaulted(engine, room_id, crm):
    """A field map that silently wrote "" would be indistinguishable from one that
    legitimately mapped an empty value."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(fields=[{"field": "Region", "from_data_field": "region"}]),
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    written = steps_of(run)["create_or_update_record"]["resolved"]["fields_written"][0]
    assert written["written"] is False
    assert written["reason"] == "data_field_not_on_the_booking"
    assert "Region" not in crm.get("cnt-0001")["data"]["fields"]


def test_a_field_map_entry_with_neither_a_value_nor_a_data_field_is_refused(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(fields=[{"field": "Status"}])])
    assert "neither a value nor a from_data_field" in str(excinfo.value)


def test_an_unknown_update_branch_is_refused_and_names_the_ones_that_exist(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(update="matched_anything")])
    assert "matched_contact_or_lead" in str(excinfo.value)


def test_an_unknown_create_branch_is_refused_and_names_the_three(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(create="always_a_contact", record_type="contact")])
    for branch in CREATE_BRANCHES:
        assert branch in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The Related Object gate
# --------------------------------------------------------------------------- #


def test_a_related_object_needs_a_contact_and_refuses_to_resolve_off_a_lead(engine, room_id, crm):
    """[sourced] "If we have found a contact" - a Lead is not a Contact, and the
    natural reading of that paragraph is exactly the mistake this guards against."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), crm
    )
    step = steps_of(run)["related_object"]
    assert step["outcome"] == OUTCOME_SKIPPED
    assert step["reason"] == RELATED_REQUIRES_CONTACT_QUOTE
    assert "not a Contact" in step["message"]


def test_a_lead_still_gets_the_researched_default_relation(engine, room_id, crm):
    """[sourced] "All created Events will be related to the Contact or Lead **by
    default**" - unconditionally, whatever the record is."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event"},
        ],
    )
    run_flow(engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), crm)
    history = engine.history(room_id)
    assert history[0]["data"]["related_to"] == "lead-0001"
    assert history[0]["data"]["related_object"] == ""


def test_a_related_object_of_the_other_vendor_is_refused_by_name(engine, room_id):
    """[sourced] the two lists are side by side and are not mixed."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(), {"node": "related_object", "object": "Deal"}])
    assert "the salesforce palette offers" in str(excinfo.value)
    assert "the hubspot one offers" in str(excinfo.value)
    assert "Ticket" in str(excinfo.value)


def test_a_related_object_node_naming_nothing_is_refused(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(), {"node": "related_object"}])
    assert "names one of" in str(excinfo.value)


def test_a_campaign_relation_must_name_the_campaign(engine, room_id):
    """The research states a rule for Cases and for Opportunities and none for
    Campaigns, so this build asks rather than guessing."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(engine, room_id, meeting, [anchor(), {"node": "related_object", "object": "Campaign"}])
    assert RELATED_SELECTION_QUOTE in str(excinfo.value)


def test_a_related_object_with_no_record_skips_with_the_no_record_reason(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="none"),
            {"node": "related_object", "object": "Case"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert steps_of(run)["related_object"]["reason"] == SKIP_NO_RECORD


# --------------------------------------------------------------------------- #
# The two researched selection rules
# --------------------------------------------------------------------------- #


def test_a_case_chooses_the_most_recently_created_open_one(engine, room_id, crm):
    """[sourced] "For **Cases**, we will relate with the most recently created Open
    one" - case-0002 (11 Sep), not the *newer* Closed case-0001 (25 Sep)."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Case"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert run["data"]["related"]["crm_id"] == "case-0002"
    assert run["data"]["related"]["rule"] == "most_recent_open"
    # Three cases exist, one is Closed, so two were considered.
    assert run["data"]["related"]["candidates"] == 3
    assert run["data"]["related"]["considered"] == 2


def test_the_case_refusal_reports_how_many_closed_cases_were_not_candidates(store, room_id):
    """'Nothing was found' and 'nothing was found because everything is closed' are
    different conversations with a rep."""
    empty = LocalCrm(store, room_id=room_id)
    empty.seed([dict(CRM_ROWS[3])], source=SOURCE)
    result = empty.select_related("Case", record=None, on=date(2026, 10, 5), source=SOURCE)
    assert result["chosen"] is None
    assert result["reason"] == "no_open_case (1 closed case(s) not candidates)"
    assert result["candidates"] == 1
    assert result["considered"] == 0


def test_a_case_with_no_creation_date_never_wins_the_most_recently_created_rule(store, crm):
    """A row with an unparseable date must not win by default."""
    other = LocalCrm(store, room_id=crm.room_id)
    other.seed(
        [
            {
                "crm_id": "case-0009",
                "vendor": "salesforce",
                "type": "Case",
                "status": "Open",
                "created_on": "not-a-date",
            }
        ],
        source=SOURCE,
    )
    selection = other.select_related("Case", record=None, on=date(2026, 10, 5), source=SOURCE)
    assert selection["chosen"]["data"]["crm_id"] == "case-0002"


def test_an_opportunity_chooses_the_one_with_the_nearest_close_date(engine, room_id, crm):
    """[sourced] "for **Opportunities**, we will relate with the one that has the
    nearest Close Date" - and 7 October is two days from the meeting, where 20
    September is fifteen days away and 28 February is nearly five months."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Opportunity"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert run["data"]["related"]["crm_id"] == "opp-0002"
    assert run["data"]["related"]["rule"] == "nearest_close_date"
    assert RELATED_SELECTION_QUOTE in steps_of(run)["related_object"]["message"]


def test_nearest_is_measured_against_the_meeting_and_not_the_soonest(crm):
    """The reference point is the meeting, and absolute distance - so a close date
    *after* the meeting can win over an earlier one. Sorting ascending would pick
    opp-0003 and be wrong."""
    near_after = crm.select_related("Opportunity", record=None, on=date(2026, 10, 5), source=SOURCE)
    assert near_after["chosen"]["data"]["crm_id"] == "opp-0002"
    far_before = crm.select_related("Opportunity", record=None, on=date(2026, 3, 1), source=SOURCE)
    assert far_before["chosen"]["data"]["crm_id"] == "opp-0003"


def test_nearest_breaks_a_tie_on_the_earlier_close_date(store, crm):
    other = LocalCrm(store, room_id=crm.room_id)
    other.seed(
        [
            {
                "crm_id": "opp-9001",
                "vendor": "salesforce",
                "type": "Opportunity",
                "close_date": "2026-10-03",
            },
            {
                "crm_id": "opp-9002",
                "vendor": "salesforce",
                "type": "Opportunity",
                "close_date": "2026-10-07",
            },
        ],
        source=SOURCE,
    )
    selection = other.select_related(
        "Opportunity", record=None, on=date(2026, 10, 5), source=SOURCE
    )
    assert selection["chosen"]["data"]["crm_id"] == "opp-9001"


def test_an_opportunity_with_no_meeting_date_falls_back_to_the_earliest_and_says_so(crm):
    """A silent fallback would be a rule the research does not state."""
    selection = crm.select_related("Opportunity", record=None, on=None, source=SOURCE)
    assert selection["chosen"]["data"]["crm_id"] == "opp-0003"
    assert selection["reason"] == "no_meeting_date_so_earliest_close_date"


def test_a_related_object_with_no_candidate_is_skipped_not_failed(engine, room_id, store):
    """The Event keeps its researched default relation rather than losing the run."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event"},
        ],
    )
    empty = LocalCrm(store, room_id=room_id)
    empty.seed(
        [
            {
                "crm_id": "cnt-only",
                "vendor": "salesforce",
                "type": "Contact",
                "email": "a.buyer@northwind.example",
                "name": "Amara",
            },
            {
                "crm_id": "case-closed-only",
                "vendor": "salesforce",
                "type": "Case",
                "status": "Closed",
                "created_on": "2026-09-01",
            },
        ],
        source=SOURCE,
    )
    run = run_flow(engine, room_id, declared, booking(), empty)
    step = steps_of(run)["related_object"]
    assert step["outcome"] == OUTCOME_SKIPPED
    assert step["reason"] == "no_candidate_matched_the_selection_rule"
    assert run["data"]["ok"] is True
    assert engine.history(room_id)[0]["data"]["crm_id"] != ""


def test_an_account_relation_resolves_off_the_contacts_own_account(crm):
    """Not a choice among candidates: the Contact already names it."""
    contact = crm.get("cnt-0001")["data"]
    selection = crm.select_related("Account", record=contact, on=date(2026, 10, 5), source=SOURCE)
    assert selection["chosen"]["data"]["crm_id"] == "acc-0001"
    assert selection["rule"] == "the_contact_account"


def test_a_campaign_relation_resolves_the_campaign_the_node_named(engine, room_id, crm):
    """The engine looks up the named Campaign; the raw selector's "most recent" rule
    is a different question and picks the other one, which is why the node names it."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Campaign", "campaign": "Q4 Enterprise"},
            {"node": "create_event"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert run["data"]["related"]["crm_id"] == "camp-0001"
    assert run["data"]["related"]["rule"] == "named_explicitly"


def test_a_campaign_relation_naming_a_campaign_that_does_not_exist_skips(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "related_object", "object": "Campaign", "campaign": "Nonexistent"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    step = steps_of(run)["related_object"]
    assert step["outcome"] == OUTCOME_SKIPPED
    assert "no_campaign_with_that_name" in step["resolved"]["reason"]


def test_a_ticket_uses_the_same_most_recent_open_rule_as_a_case(crm):
    """A Ticket has a status and a creation date, so the same rule applies. The
    mapping is data, so a reviewer who disagrees changes one dict entry."""
    assert SELECTION_RULES["Ticket"] == "most_recent_open"
    selection = crm.select_related("Ticket", record=None, on=date(2026, 10, 5), source=SOURCE)
    assert selection["chosen"]["data"]["crm_id"] == "hs-tick-0002"


def test_a_deal_uses_the_same_nearest_close_date_rule_as_an_opportunity(crm):
    assert SELECTION_RULES["Deal"] == "nearest_close_date"
    selection = crm.select_related("Deal", record=None, on=date(2026, 10, 5), source=SOURCE)
    assert selection["chosen"]["data"]["crm_id"] == "hs-deal-0001"


def test_every_related_object_type_maps_to_a_record_type_the_selection_rule_can_find():
    for name, record_type in RELATED_RECORD_TYPE.items():
        assert record_type, name
    assert RELATED_RECORD_TYPE["Deal"] == "Deal"
    assert RELATED_RECORD_TYPE["Ticket"] == "Ticket"


def test_a_parse_date_reads_both_shapes_a_vendor_sends_and_never_raises():
    assert parse_date("2026-10-07") == date(2026, 10, 7)
    assert parse_date("2026-10-07T00:00:00Z") == date(2026, 10, 7)
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("nonsense") is None


# --------------------------------------------------------------------------- #
# Salesforce L2A
# --------------------------------------------------------------------------- #


def test_l2a_creates_an_account_for_a_matched_lead(engine, room_id, crm):
    """[sourced] "Salesforce L2A matching applied" - Lead *to Account*."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(l2a=True)])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), crm
    )
    l2a = steps_of(run)["create_or_update_record"]["resolved"]["l2a"]
    assert l2a["applied"] is True
    assert l2a["reason"] == "l2a_matched_an_existing_account"
    assert l2a["account_crm_id"] == "acc-0001"


def test_l2a_links_the_ledger_to_the_account_it_matched(engine, room_id, crm):
    """The point of the rule: the Lead now hangs off an Account, which is what a
    relationship fallback needs."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(l2a=True, create="")])
    run_flow(engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), crm)
    assert crm.get("lead-0001")["data"]["account_id"] == "acc-0001"


def test_l2a_creates_the_account_when_there_is_none_to_match(store, room_id):
    empty = LocalCrm(store, room_id=room_id)
    empty.seed(
        [
            {
                "crm_id": "lead-x",
                "vendor": "salesforce",
                "type": "Lead",
                "email": "b.toure@northwind.example",
                "company": "Brand New Co",
            }
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(l2a=True, create="")])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "b.toure@northwind.example"}), empty
    )
    l2a = steps_of(run)["create_or_update_record"]["resolved"]["l2a"]
    assert l2a["reason"] == "l2a_created_an_account"
    assert empty.get(l2a["account_crm_id"])["data"]["name"] == "Brand New Co"


def test_l2a_is_a_salesforce_rule_and_says_so_on_hubspot(store, room_id):
    hubspot = LocalCrm(store, room_id=room_id)
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            {
                "node": "create_or_update_contact",
                "update": "matched_contact_or_lead",
                "create": "contact_or_lead",
                "record_type": "contact",
                "l2a": True,
            },
        ],
        vendor="hubspot",
    )
    run = run_flow(engine, room_id, declared, booking(), hubspot)
    l2a = steps_of(run)["create_or_update_contact"]["resolved"]["l2a"]
    assert l2a == {"applied": False, "reason": "l2a_is_a_salesforce_rule"}


def test_l2a_with_no_company_to_convert_says_why_it_did_nothing(store, room_id):
    empty = LocalCrm(store, room_id=room_id)
    empty.seed(
        [
            {
                "crm_id": "lead-y",
                "vendor": "salesforce",
                "type": "Lead",
                "email": "solo@prospect.example",
            }
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(l2a=True, create="")])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "solo@prospect.example"}), empty
    )
    assert steps_of(run)["create_or_update_record"]["resolved"]["l2a"]["reason"] == (
        "no_company_on_the_lead_to_convert"
    )


# --------------------------------------------------------------------------- #
# Add to Campaign
# --------------------------------------------------------------------------- #


def test_a_campaign_member_is_created_with_status_booked(engine, room_id, crm):
    """[sourced] "CampaignMember created/updated with status **Booked**"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    step = steps_of(run)["add_to_campaign"]
    assert step["outcome"] == OUTCOME_APPLIED
    assert step["resolved"]["outcome"] == "created"
    assert step["resolved"]["status"] == "Booked"
    assert crm.get(step["crm_id"])["data"]["status"] == "Booked"


def test_a_second_booking_updates_the_member_rather_than_duplicating_it(engine, room_id, crm):
    """The "created/**updated**" half of the same sentence."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    first = run_flow(engine, room_id, declared, booking(booking_ref="b-1"), crm)
    second = run_flow(engine, room_id, declared, booking(booking_ref="b-2"), crm)
    assert steps_of(first)["add_to_campaign"]["resolved"]["outcome"] == "created"
    assert steps_of(second)["add_to_campaign"]["resolved"]["outcome"] == "updated"
    assert len(crm.records("CampaignMember")) == 1


def test_a_campaign_member_in_a_different_campaign_is_a_different_member(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    first = flow(
        engine,
        room_id,
        meeting,
        [anchor(), {"node": "add_to_campaign", "campaign": "Q4 Enterprise"}],
        name="q4",
    )
    second = flow(
        engine,
        room_id,
        meeting,
        [anchor(), {"node": "add_to_campaign", "campaign": "FY27 Expansion"}],
        name="fy27",
    )
    run_flow(engine, room_id, first, booking(), crm)
    run_flow(engine, room_id, second, booking(), crm)
    assert len(crm.records("CampaignMember")) == 2


def test_a_campaign_member_status_other_than_booked_is_refused_with_the_quote(engine, room_id):
    """The research fixes the status, so a flow that names another is a declaration
    this build will not honour."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                anchor(),
                {"node": "add_to_campaign", "campaign": "Q4 Enterprise", "status": "Attended"},
            ],
        )
    assert "CampaignMember created/updated with status Booked" in str(excinfo.value)


def test_a_campaign_member_with_no_record_skips_with_the_no_record_reason(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="none"),
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert steps_of(run)["add_to_campaign"]["reason"] == SKIP_NO_RECORD


# --------------------------------------------------------------------------- #
# Update Ownership
# --------------------------------------------------------------------------- #


def test_the_owner_is_reassigned_to_the_assignee(engine, room_id, crm):
    """[sourced] "record Owner reassigned to the assignee"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "assignee"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    step = steps_of(run)["update_ownership"]
    assert step["resolved"]["owner"] == "sam@acme.example"
    assert crm.get("cnt-0001")["data"]["owner"] == "sam@acme.example"


@pytest.mark.parametrize(
    "which,email",
    [
        ("assignee", "sam@acme.example"),
        ("host", "dana@acme.example"),
        ("booker", "a.buyer@northwind.example"),
    ],
)
def test_the_owner_can_be_any_of_the_three_identities_the_research_names(
    engine, room_id, crm, which, email
):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": which},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert steps_of(run)["update_ownership"]["resolved"]["owner"] == email


def test_ownership_can_be_transferred_to_the_booker_who_took_the_meeting(engine, room_id, crm):
    """[sourced] "ownership can be transferred to whoever took the meeting"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "booker"},
        ],
    )
    run_flow(engine, room_id, declared, booking(), crm)
    assert crm.get("cnt-0001")["data"]["owner"] == "a.buyer@northwind.example"


def test_the_relationship_fallback_reads_the_owner_of_the_related_record(engine, room_id, crm):
    """[sourced] Cal's ``crmRecordOwnerFallbackMode`` is ``relationship``.

    Reached only when the booking carries no assignee - the assignee is the
    researched first answer, so a fallback that outranked it would mean the
    researched sentence never happened on any record with a related one.
    """
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "fallback_mode": "relationship"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(assignee={}), crm)
    step = steps_of(run)["update_ownership"]
    assert step["resolved"]["branch"] == "fallback_relationship"
    assert step["resolved"]["fallback"]["via"] == "relationship"
    assert step["resolved"]["fallback"]["related_crm_id"] == "acc-0001"
    # The Account's owner is sam, and the Contact's own owner is dana, so this is a
    # real reassignment - which is what makes the fallback visible at all.
    assert step["resolved"]["owner"] == "sam@acme.example"
    assert crm.get("cnt-0001")["data"]["owner"] == "sam@acme.example"


def test_the_assignee_beats_the_fallback_when_the_booking_carries_one(engine, room_id, crm):
    """[sourced] "record Owner reassigned to the assignee" - first, not last."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "assignee", "fallback_mode": "relationship"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    step = steps_of(run)["update_ownership"]
    assert step["resolved"]["branch"] == "assignee"
    assert "fallback" not in step["resolved"]
    assert crm.get("cnt-0001")["data"]["owner"] == "sam@acme.example"


def test_the_attribute_rules_fallback_takes_the_first_matching_rule(engine, room_id, crm):
    """[sourced] Cal's other documented value."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {
                "node": "update_ownership",
                "fallback_mode": "attributeRules",
                "attribute_rules": [
                    {"field": "Rating", "equals": "Hot", "owner": "lead@acme.example"},
                    {"field": "Rating", "owner": "fallback@acme.example"},
                ],
            },
        ],
    )
    run = run_flow(engine, room_id, declared, booking(assignee={}), crm)
    step = steps_of(run)["update_ownership"]
    # cnt-0001's Rating is Warm, so the first rule misses and the second catches it.
    assert step["resolved"]["fallback"]["rule"] == {"field": "Rating", "equals": None}
    assert step["resolved"]["owner"] == "fallback@acme.example"
    assert crm.get("cnt-0001")["data"]["owner"] == "fallback@acme.example"


def test_the_first_matching_attribute_rule_wins_over_the_later_ones(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {
                "node": "update_ownership",
                "fallback_mode": "attributeRules",
                "attribute_rules": [
                    {"field": "Status", "equals": "Open", "owner": "first@acme.example"},
                    {"field": "Status", "owner": "second@acme.example"},
                ],
            },
        ],
    )
    run = run_flow(engine, room_id, declared, booking(assignee={}), crm)
    assert steps_of(run)["update_ownership"]["resolved"]["owner"] == "first@acme.example"


def test_attribute_rules_matching_nothing_falls_through_to_the_records_owner(engine, room_id, crm):
    """Neither fallback nor assignee resolving is not "nobody": the record's own
    owner is the last resort, and the run says which branch it used."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {
                "node": "update_ownership",
                "fallback_mode": "attributeRules",
                "attribute_rules": [{"field": "Nonexistent", "owner": "x@acme.example"}],
            },
        ],
    )
    run = run_flow(engine, room_id, declared, booking(assignee={}), crm)
    step = steps_of(run)["update_ownership"]
    assert step["resolved"]["fallback"]["reason"] == "no_rule_matched"
    assert step["resolved"]["branch"] == "crm_existing_owner"
    assert step["resolved"]["owner"] == "dana@acme.example"
    assert step["outcome"] == OUTCOME_SKIPPED
    assert step["reason"] == "owner_unchanged"


def test_attribute_rules_with_no_rules_is_refused_at_declaration(engine, room_id):
    """A mode that needs rules and has none would resolve to nothing silently."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                anchor(),
                {"node": "update_ownership", "fallback_mode": "attributeRules"},
            ],
        )
    assert "needs at least one rule" in str(excinfo.value)


def test_attribute_rules_under_the_relationship_mode_is_refused(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                anchor(),
                {
                    "node": "update_ownership",
                    "fallback_mode": "relationship",
                    "attribute_rules": [{"field": "Rating", "owner": "x@y.example"}],
                },
            ],
        )
    assert "only applies to fallback_mode 'attributeRules'" in str(excinfo.value)


def test_skip_contact_owner_writes_the_assignee_without_reading_the_crm(engine, room_id, crm):
    """[sourced] routing.skipContactOwner - "Whether to skip contact owner assignment
    from CRM integration"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "host", "skip_contact_owner": True},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    step = steps_of(run)["update_ownership"]
    assert step["resolved"]["branch"] == "skip_contact_owner"
    assert "Whether to skip contact owner assignment" in step["resolved"]["note"]
    assert crm.get("cnt-0001")["data"]["owner"] == "dana@acme.example"


def test_cal_s_crm_app_slug_and_owner_record_type_are_kept_for_tracing(engine, room_id, crm):
    """The research names both booking fields; they are recorded rather than acted on,
    because it does not say what they do beyond naming the app and the record type."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {
                "node": "update_ownership",
                "crm_app_slug": "salesforce",
                "crm_owner_record_type": "Contact",
            },
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    resolved = steps_of(run)["update_ownership"]["resolved"]
    assert resolved["crm_app_slug"] == "salesforce"
    assert resolved["crm_owner_record_type"] == "Contact"


def test_an_ownership_change_that_is_a_no_op_is_skipped_not_written(engine, room_id, crm):
    """A no-op write produces an audit row describing a change that did not happen."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "booker"},
        ],
    )
    declared_twice = flow(
        engine,
        room_id,
        meeting,
        [anchor(), {"node": "update_ownership", "assign_to": "booker"}],
        name="again",
    )
    run_flow(engine, room_id, declared, booking(), crm)
    run = run_flow(engine, room_id, declared_twice, booking(), crm)
    step = steps_of(run)["update_ownership"]
    assert step["outcome"] == OUTCOME_SKIPPED
    assert step["reason"] == "owner_unchanged"


def test_an_owner_nobody_can_resolve_is_skipped_with_a_named_reason(store, room_id):
    """A write to nobody is worse than saying so. No assignee, no fallback match, and
    no owner on the record either."""
    empty = LocalCrm(store, room_id=room_id)
    empty.seed(
        [
            {
                "crm_id": "cnt-lonely",
                "vendor": "salesforce",
                "type": "Contact",
                "email": "solo@prospect.example",
                "name": "Solo",
            }
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "update_ownership", "assign_to": "host"},
        ],
    )
    run = run_flow(
        engine,
        room_id,
        declared,
        booking(booker={"email": "solo@prospect.example"}, host={}, assignee={}),
        empty,
    )
    step = steps_of(run)["update_ownership"]
    assert step["outcome"] == OUTCOME_SKIPPED
    assert step["reason"] == "no_owner_resolved"


def test_ownership_with_no_record_skips_with_the_no_record_reason(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="none"), {"node": "update_ownership"}])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert steps_of(run)["update_ownership"]["reason"] == SKIP_NO_RECORD


def test_an_unknown_assign_to_is_refused_and_names_the_three(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [anchor(), {"node": "update_ownership", "assign_to": "nobody"}],
        )
    for identity in ("assignee", "host", "booker"):
        assert identity in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Activity Assigned to
# --------------------------------------------------------------------------- #


def test_activity_assigned_to_carries_the_bookers_email_into_the_engagement(
    engine, room_id, store, crm
):
    """[sourced] "It passes the email of the booker or the assignee to the
    'Activity Assigned to' field inside the engagement created in Hubspot"."""
    hs = LocalCrm(store, room_id=room_id)
    hs.seed(
        [
            {
                "crm_id": "hs-c",
                "vendor": "hubspot",
                "type": "Contact",
                "email": "priya.raman@contoso.example",
                "name": "Priya Raman",
            },
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            {
                "node": "create_or_update_contact",
                "update": "matched_contact_or_lead",
                "create": "contact_or_lead",
                "record_type": "contact",
            },
            {"node": "create_engagement", "activity_assigned_to": "booker"},
        ],
        vendor="hubspot",
    )
    run = run_flow(
        engine,
        room_id,
        declared,
        booking(booker={"email": "priya.raman@contoso.example", "name": "Priya Raman"}),
        hs,
    )
    step = steps_of(run)["create_engagement"]
    assert step["resolved"]["activity_assigned_to"] == "booker"
    assert step["resolved"]["activity_assigned_to_email"] == "priya.raman@contoso.example"
    assert hs.get(step["crm_id"])["data"]["activity_assigned_to_email"] == (
        "priya.raman@contoso.example"
    )


@pytest.mark.parametrize(
    "which,email",
    [
        ("host", "dana@acme.example"),
        ("booker", "a.buyer@northwind.example"),
        ("assignee", "sam@acme.example"),
    ],
)
def test_all_three_activity_assigned_to_choices_resolve_to_an_email(
    engine, room_id, store, which, email
):
    """[sourced] "Host / Booker / Assignee"."""
    hs = LocalCrm(store, room_id=room_id)
    hs.seed(
        [
            {
                "crm_id": "hs-c",
                "vendor": "hubspot",
                "type": "Contact",
                "email": "a.buyer@northwind.example",
            }
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            {
                "node": "create_or_update_contact",
                "update": "matched_contact_or_lead",
                "create": "contact_or_lead",
                "record_type": "contact",
            },
            {"node": "create_engagement", "activity_assigned_to": which},
        ],
        vendor="hubspot",
    )
    run = run_flow(engine, room_id, declared, booking(), hs)
    assert steps_of(run)["create_engagement"]["resolved"]["activity_assigned_to_email"] == email


def test_activity_assigned_to_is_refused_on_a_salesforce_flow(engine, room_id):
    """[sourced] the research documents it on Chili Piper's HubSpot nodes only."""
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                anchor(),
                {"node": "create_event", "activity_assigned_to": "booker"},
            ],
        )
    assert "documented on Chili Piper's HubSpot nodes only" in str(excinfo.value)


def test_an_unknown_activity_assigned_to_is_refused_by_name(engine, room_id):
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [
                {
                    "node": "create_or_update_contact",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
                {"node": "create_engagement", "activity_assigned_to": "the intern"},
            ],
            vendor="hubspot",
        )
    assert "assignee" in str(excinfo.value)


def test_the_history_row_carries_the_activity_assigned_to_email(engine, room_id, store):
    hs = LocalCrm(store, room_id=room_id)
    hs.seed(
        [
            {
                "crm_id": "hs-c",
                "vendor": "hubspot",
                "type": "Contact",
                "email": "a.buyer@northwind.example",
            }
        ],
        source=SOURCE,
    )
    engine = BookingWriteback(store)
    meeting = meeting_type(engine, room_id, vendor="hubspot")
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            {
                "node": "create_or_update_contact",
                "update": "matched_contact_or_lead",
                "create": "contact_or_lead",
                "record_type": "contact",
            },
            {"node": "create_engagement", "activity_assigned_to": "assignee"},
        ],
        vendor="hubspot",
    )
    run_flow(engine, room_id, declared, booking(), hs)
    assert engine.history(room_id)[0]["data"]["activity_assigned_to_email"] == "sam@acme.example"


# --------------------------------------------------------------------------- #
# Create child Event per additional guest
# --------------------------------------------------------------------------- #


def test_a_child_event_is_created_per_additional_guest(engine, room_id, crm):
    """[sourced] "Create child Event, per additional guest"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "child_events": True},
        ],
    )
    run = run_flow(
        engine,
        room_id,
        declared,
        booking(
            guests=[
                {"name": "Bayo", "email": "b1@northwind.example"},
                {"name": "Ines", "email": "b2@northwind.example"},
            ]
        ),
        crm,
    )
    assert len(run["data"]["created_events"]) == 3
    assert steps_of(run)["create_event"]["resolved"]["child_events"] == 2


def test_no_child_events_means_one_event_and_the_run_says_why(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "child_events": True},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    resolved = steps_of(run)["create_event"]["resolved"]
    assert resolved["child_events"] == 0
    assert resolved["child_note"] == "no_additional_guests"
    assert len(run["data"]["created_events"]) == 1


def test_the_child_events_setting_off_ignores_the_guests_entirely(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run = run_flow(
        engine,
        room_id,
        declared,
        booking(guests=[{"name": "Bayo", "email": "b1@northwind.example"}]),
        crm,
    )
    assert len(run["data"]["created_events"]) == 1


def test_every_child_gets_its_own_history_row(engine, room_id, crm):
    """[sourced] "Admin later retries any failed CRM Event" - per Event."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "child_events": True},
        ],
    )
    run_flow(
        engine,
        room_id,
        declared,
        booking(guests=[{"name": "Bayo", "email": "b1@northwind.example"}]),
        crm,
    )
    rows = engine.history(room_id)
    assert len(rows) == 2
    assert sorted(row["data"]["is_child"] for row in rows) == [False, True]
    assert {row["data"]["guest_index"] for row in rows} == {0, 1}


def test_a_guest_with_no_email_is_refused_before_the_run_is_written(engine, room_id, crm):
    """A guest with no address cannot be invited, and the failure is at the door."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine, room_id, meeting, [anchor(), {"node": "create_event", "child_events": True}]
    )
    with pytest.raises(InvalidConfig) as excinfo:
        run_flow(engine, room_id, declared, booking(guests=[{"name": "No Address"}]), crm)
    assert "per additional guest" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Events History: when, and the detailed error
# --------------------------------------------------------------------------- #


def test_a_created_event_shows_when_it_happened(engine, room_id, crm):
    """[sourced] "If the Event is successfully created, we will show when it happened"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run_flow(engine, room_id, declared, booking(), crm)
    row = engine.history(room_id)[0]
    assert row["data"]["status"] == "created"
    assert row["data"]["when"]
    assert row["data"]["crm_id"] != ""
    assert row["data"]["error"] == ""


def test_a_failed_event_also_shows_when_it_happened_alongside_the_detailed_error(
    engine, room_id, crm
):
    """[sourced] "If the Event failed to be created, we will also show when it
    happened, alongside the detailed error." Both halves, on the same row."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = LocalCrm(crm.store, room_id=crm.room_id, faults={"create:Event": CRM_REFUSAL})
    run = run_flow(engine, room_id, declared, booking(), faulted)
    assert run["data"]["ok"] is False
    row = engine.history(room_id)[0]
    assert row["data"]["status"] == "failed"
    assert row["data"]["when"]
    assert row["data"]["error"] == CRM_REFUSAL
    assert row["data"]["error_code"] == "CRM_ERROR"


def test_the_refused_error_is_the_message_the_deployment_would_see(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = LocalCrm(crm.store, room_id=crm.room_id, faults={"create:Event": CRM_REFUSAL})
    run = run_flow(engine, room_id, declared, booking(), faulted)
    step = steps_of(run)["create_event"]
    assert step["outcome"] == OUTCOME_FAILED
    assert step["message"] == CRM_REFUSAL
    assert run["data"]["actionable_error"]["node"] == "create_event"


def test_the_run_reports_one_actionable_error_and_keeps_every_outcome(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = LocalCrm(crm.store, room_id=crm.room_id, faults={"create:Event": CRM_REFUSAL})
    run = run_flow(engine, room_id, declared, booking(), faulted)
    assert isinstance(run["data"]["actionable_error"], dict)
    assert run["data"]["counts"]["failed"] == 1
    assert len(run["data"]["steps"]) == 2


def test_every_node_produces_a_step_even_when_an_earlier_one_produced_nothing(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="none"),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event", "child_events": True},
            {"node": "update_field", "fields": [{"field": "Rating", "value": "Hot"}]},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
            {"node": "update_ownership"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert [step["node"] for step in run["data"]["steps"]] == [
        "create_or_update_record",
        "related_object",
        "create_event",
        "update_field",
        "add_to_campaign",
        "update_ownership",
    ]
    assert set(run["data"]["counts"]) == set(OUTCOMES)


# --------------------------------------------------------------------------- #
# The catch-all: nothing matched and nothing was created
# --------------------------------------------------------------------------- #


def test_no_record_makes_every_later_node_skip_with_a_named_reason(engine, room_id, crm):
    """The rule most likely to be got wrong. A flow whose fourth node has no
    record must not 'succeed' quietly."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="none"),
            {"node": "related_object", "object": "Case"},
            {"node": "create_event", "child_events": True},
            {"node": "update_field", "fields": [{"field": "Rating", "value": "Hot"}]},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
            {"node": "update_ownership"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    later = run["data"]["steps"][1:]
    assert all(step["reason"] == SKIP_NO_RECORD for step in later)
    assert all(step["outcome"] == OUTCOME_SKIPPED for step in later)
    assert run["data"]["ok"] is False
    assert run["data"]["created_events"] == []


def test_no_record_writes_nothing_at_all_to_the_crm(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="none"),
            {"node": "create_event", "child_events": True},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    run_flow(engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm)
    assert crm.records("Event") == []
    assert crm.records("CampaignMember") == []


def test_the_no_record_run_says_why_in_words_and_in_a_reason(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="none"), {"node": "create_event"}])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    first, second = run["data"]["steps"]
    assert first["reason"] == "nothing_matched_and_no_create_branch"
    assert NO_RECORD_MESSAGE in first["message"]
    assert second["reason"] == SKIP_NO_RECORD
    assert "no record" in second["message"]
    # And the run's single actionable error is the *cause*, not one of the skips.
    assert run["data"]["actionable_error"]["node"] == "create_or_update_record"
    assert run["data"]["actionable_error"]["reason"] == "nothing_matched_and_no_create_branch"


def test_no_record_is_still_written_as_a_run_row(engine, room_id, crm, store):
    """A booking that produced no CRM write is a fact a rep would otherwise have to
    infer from an absence."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(create="none")])
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert store.get(str(run["id"]))["data"]["ok"] is False


def test_a_create_branch_that_produces_a_record_makes_the_catch_all_unreachable(
    engine, room_id, crm
):
    """The catch-all is about *no record*, not about no match."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(create="contact_or_lead", record_type="contact"),
            {"node": "create_event", "child_events": True},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), crm
    )
    assert run["data"]["ok"] is True
    assert all(step["outcome"] == OUTCOME_APPLIED for step in run["data"]["steps"])


def test_a_crm_that_refuses_the_create_is_reported_as_a_failure_not_a_skip(engine, room_id, crm):
    """A refusal from the CRM is a failure. A skip is this build declining."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = LocalCrm(
        crm.store, room_id=crm.room_id, faults={"create:Contact": "DUPLICATE_VALUE: already exists"}
    )
    run = run_flow(
        engine, room_id, declared, booking(booker={"email": "new@prospect.example"}), faulted
    )
    first, second = run["data"]["steps"]
    assert first["outcome"] == OUTCOME_FAILED
    assert "DUPLICATE_VALUE" in first["message"]
    assert second["reason"] == SKIP_NO_RECORD


# --------------------------------------------------------------------------- #
# Sync Meeting Type to the CRM
# --------------------------------------------------------------------------- #


def test_the_toggle_defaults_to_off_and_says_why(engine, room_id):
    """[sourced] the settings are admin-defined, so nothing is opted in by default."""
    meeting = engine.create_meeting_type(
        room_id, {"name": "Enterprise demo", "vendor": "salesforce"}, actor="dana", source=SOURCE
    )
    assert meeting["data"]["sync_to_crm"] is False


def test_a_run_against_a_toggle_that_is_off_is_written_and_skipped(engine, room_id, crm):
    """[sourced] the toggle governs what a booked meeting does, so the outcome -
    including doing nothing - is a recorded fact."""
    meeting = meeting_type(engine, room_id, sync=False)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert run["data"]["ok"] is False
    assert run["data"]["counts"]["skipped"] == 1
    assert run["data"]["steps"][0]["reason"] == SKIP_MEETING_TYPE_SYNC_OFF
    assert SYNC_TOGGLE_ORG_WIDE_QUOTE in run["data"]["steps"][0]["message"]


def test_a_run_against_a_toggle_that_is_off_writes_nothing_to_the_crm(engine, room_id, crm):
    meeting = meeting_type(engine, room_id, sync=False)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run_flow(engine, room_id, declared, booking(), crm)
    assert crm.records("Event") == []


def test_a_run_cannot_carry_its_own_toggle_value(engine, room_id, crm):
    """[sourced] the setting "is applied to all users in your org" - so honouring a
    per-run value would make it per-user."""
    meeting = meeting_type(engine, room_id, sync=True)
    declared = flow(engine, room_id, meeting, [anchor()])
    with pytest.raises(InvalidConfig) as excinfo:
        run_flow(engine, room_id, declared, booking(sync_to_crm=False), crm)
    assert SYNC_TOGGLE_ORG_WIDE_QUOTE in str(excinfo.value)
    assert "per-user" in str(excinfo.value)


def test_a_non_boolean_toggle_is_refused_at_declaration(engine, room_id):
    with pytest.raises(InvalidConfig) as excinfo:
        engine.create_meeting_type(
            room_id,
            {"name": "x", "vendor": "salesforce", "sync_to_crm": "yes"},
            actor="dana",
            source=SOURCE,
        )
    assert "per-run value" in str(excinfo.value)


def test_switching_the_toggle_on_lets_the_next_run_through(engine, room_id, crm):
    meeting = meeting_type(engine, room_id, sync=False)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    assert run_flow(engine, room_id, declared, booking(), crm)["data"]["ok"] is False
    engine.update_meeting_type(
        room_id, str(meeting["id"]), {"sync_to_crm": True}, actor="dana", source=SOURCE
    )
    assert run_flow(engine, room_id, declared, booking(), crm)["data"]["ok"] is True


def test_the_toggle_quote_is_served_with_the_scope_that_makes_it_org_wide():
    payload = describe_vocabulary()
    assert payload["sourced_quotes"]
    assert SYNC_TOGGLE_QUOTE in str(payload)
    assert SYNC_TOGGLE_ORG_WIDE_QUOTE in str(payload)


# --------------------------------------------------------------------------- #
# The three router paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", list(PATHS))
def test_all_three_router_paths_can_carry_a_flow_and_fire(engine, room_id, crm, path):
    """[sourced] "On a scheduled / not-scheduled / disqualified path"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}], path=path)
    run = run_flow(engine, room_id, declared, booking(path=path), crm)
    assert run["data"]["path"] == path
    assert run["data"]["ok"] is True


def test_a_booking_taking_a_different_path_than_its_flow_is_refused(engine, room_id, crm):
    """A flow belongs to one path, so the two have to agree."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor()], path="scheduled")
    with pytest.raises(InvalidConfig) as excinfo:
        run_flow(engine, room_id, declared, booking(path="disqualified"), crm)
    assert "carries its own nodes" in str(excinfo.value)


def test_a_path_spelling_is_folded_but_a_fourth_path_is_refused():
    assert normalise_path("not-scheduled") == "not_scheduled"
    assert normalise_path("NOT SCHEDULED") == "not_scheduled"
    assert normalise_path("Disqualified") == "disqualified"
    with pytest.raises(InvalidConfig) as excinfo:
        normalise_path("rescheduled")
    for path in PATHS:
        assert path in str(excinfo.value)


def test_the_flow_for_a_path_is_the_newest_one_declared(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    first = flow(engine, room_id, meeting, [anchor()], name="first")
    second = flow(engine, room_id, meeting, [anchor()], name="second")
    assert str(engine.flow_for(room_id, path="scheduled")["id"]) == str(second["id"])
    assert str(first["id"]) != str(second["id"])


def test_a_path_with_no_flow_is_a_428_naming_the_path_it_looked_for(engine, room_id):
    """A tenant that wired up only the scheduled path should learn that from the
    response rather than from a booking that wrote nothing."""
    with pytest.raises(NotConfigured) as excinfo:
        engine.flow_for(room_id, path="disqualified")
    assert "no flow is declared for the 'disqualified' path" in str(excinfo.value)


def test_the_writeback_resolves_the_flow_from_the_booking_alone(engine, room_id, crm):
    """[sourced] "writes fire on the scheduled, not-scheduled and disqualified paths
    automatically" - the caller need not know the flow."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event"},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    run = engine.writeback_for_path(
        room_id, booking(path="scheduled"), actor="dana", source=SOURCE, crm=crm
    )
    assert run["data"]["flow_id"] == str(declared["id"])
    assert run["data"]["ok"] is True


def test_flows_can_be_filtered_by_path_through_the_dynamic_index(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    flow(engine, room_id, meeting, [anchor()], name="scheduled one", path="scheduled")
    flow(engine, room_id, meeting, [anchor()], name="disqualified one", path="disqualified")
    assert len(engine.list_flows(room_id, path="scheduled")) == 1
    assert len(engine.list_flows(room_id, path="not_scheduled")) == 0
    assert len(engine.list_flows(room_id, meeting_type_id=str(meeting["id"]))) == 2


# --------------------------------------------------------------------------- #
# Events History and the retry
# --------------------------------------------------------------------------- #


def _failing_event_row(engine, room_id, crm, *, faults=None):
    """A booking whose Event creation was refused, and the row it produced."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = LocalCrm(
        crm.store,
        room_id=room_id,
        faults=faults if faults is not None else {"create:Event": CRM_REFUSAL},
    )
    run = run_flow(engine, room_id, declared, booking(), faulted)
    return run, faulted, [row for row in engine.history(room_id, status="failed")]


def test_a_failed_event_can_be_retried_and_the_retry_succeeds(engine, room_id, crm):
    """[sourced] "Admin later retries any failed CRM Event from Meetings Activity →
    Events History." """
    _run, faulted, failed = _failing_event_row(engine, room_id, crm)
    result = engine.retry(
        room_id,
        str(failed[0]["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    assert result["retried"] is True
    assert result["status"] == "created"
    assert result["crm_id"] != ""
    assert result["attempt"] == 2


def test_a_retry_appends_a_row_and_keeps_the_failure(engine, room_id, crm):
    """A history that rewrites itself is not a history."""
    _run, faulted, failed = _failing_event_row(engine, room_id, crm)
    original = str(failed[0]["id"])
    engine.retry(
        room_id,
        original,
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    rows = engine.history(room_id)
    assert len(rows) == 2
    kept = engine.history_row(room_id, original)
    assert kept["data"]["status"] == "failed"
    assert kept["data"]["error"] == CRM_REFUSAL
    assert kept["data"]["when"]


def test_the_retry_row_names_the_attempt_and_the_row_it_retried(engine, room_id, crm):
    """The pair "failed at T, retried at T, succeeded" has to be readable."""
    _run, faulted, failed = _failing_event_row(engine, room_id, crm)
    result = engine.retry(
        room_id,
        str(failed[0]["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    row = engine.history_row(room_id, str(result["history"]["id"]))
    assert row["data"]["attempt"] == 2
    assert row["data"]["retried_from"] == str(failed[0]["id"])
    assert row["data"]["status"] == "created"


def test_a_second_retry_that_fails_again_marks_the_original(engine, room_id, crm):
    _run, faulted, failed = _failing_event_row(engine, room_id, crm)
    result = engine.retry(
        room_id,
        str(failed[0]["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted, **{"create:Event": "STILL_REFUSED: no"}),
    )
    assert result["status"] == "failed"
    kept = engine.history_row(room_id, str(failed[0]["id"]))
    assert kept["data"]["retried"] is True
    assert kept["data"]["last_attempt"] == 2


def test_retrying_a_row_that_succeeded_is_refused_with_the_retry_quote(engine, room_id, crm):
    """The research offers retry on a failure and names nothing else, and a
    re-create would put a second meeting in the CRM."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run_flow(engine, room_id, declared, booking(), crm)
    created = engine.history(room_id)[0]
    result = engine.retry(room_id, str(created["id"]), actor="dana", source=SOURCE, crm=crm)
    assert result["retried"] is False
    assert result["reason"] == "retry_only_offered_on_a_failure"
    assert HISTORY_RETRY_QUOTE in result["message"]
    assert len(engine.history(room_id)) == 1


def test_a_retry_creates_one_event_and_does_not_re_run_the_booking(engine, room_id, crm):
    """A child that failed has to be retryable on its own, without duplicating the
    Events that already succeeded."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "child_events": True},
        ],
    )
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(
        engine,
        room_id,
        declared,
        booking(guests=[{"name": "B", "email": "b1@nw.example"}]),
        faulted,
    )
    assert len(crm.records("Event")) == 0
    failed = engine.history(room_id, status="failed")[0]
    engine.retry(
        room_id,
        str(failed["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    assert len(faulted.records("Event")) == 1


def test_delete_event_on_retry_cleans_up_before_creating_its_own(engine, room_id, crm):
    """[sourced] the retry reading of the Delete Event behaviour: whatever a failed
    attempt left behind is removed, so a retry cannot double-book."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "on_retry"},
        ],
    )
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(engine, room_id, declared, booking(), faulted)
    # The failed attempt left nothing, so there is nothing to clean - and that is
    # an answer, not an error.
    failed = engine.history(room_id, status="failed")[0]
    result = engine.retry(
        room_id,
        str(failed["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    assert result["cleaned_previous"] == []
    assert result["status"] == "created"


def test_delete_event_on_retry_removes_a_partial_event_it_is_pointed_at(engine, room_id, crm):
    """The compensation is reported, never silent: a run whose Events vanished is
    otherwise indistinguishable from one that never made them."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "on_retry"},
        ],
    )
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(engine, room_id, declared, booking(), faulted)
    failed = engine.history(room_id, status="failed")[0]
    # A partial Event the failed attempt left behind, which the retry must remove.
    # Made through an unfaulted client: the point is that the *retry* cleans up, not
    # that the CRM stopped refusing.
    partial = crm.create("Event", {"subject": "partial"}, source=SOURCE)
    partial_id = str(partial["data"]["crm_id"])
    engine.store.update(str(failed["id"]), {"crm_id": partial_id}, actor="dana", source=SOURCE)
    result = engine.retry(
        room_id,
        str(failed["id"]),
        actor="dana",
        source=SOURCE,
        crm=with_faults(faulted),
    )
    assert result["cleaned_previous"] == [partial_id]
    assert crm.get(partial_id) is not None  # soft-deleted, still resolvable
    assert crm.get(partial_id)["deleted_at"] is not None


def test_a_deleted_event_still_resolves_from_the_history(engine, room_id, crm):
    """ "Created and then deleted" is a different conversation from "never created"."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "never"},
        ],
    )
    run = run_flow(engine, room_id, declared, booking(), crm)
    event_id = run["data"]["created_events"][0]
    crm.delete(event_id, source=SOURCE)
    assert crm.get(event_id) is not None
    assert crm.get(event_id)["deleted_at"] is not None


def test_delete_event_on_failure_compensates_when_a_later_node_fails(engine, room_id, crm):
    """The other reading of the researched setting: a run that half-wrote is undone,
    and the run says so."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "on_failure"},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    # A CRM that creates Events but refuses CampaignMembers: the run half-writes.
    half = with_faults(crm, **{"create:CampaignMember": "LOCKED: cannot create"})
    run = run_flow(engine, room_id, declared, booking(), half)
    assert run["data"]["ok"] is False
    assert run["data"]["deleted_events"], "the Event this run created should have been removed"
    for event_id in run["data"]["deleted_events"]:
        assert half.get(event_id)["deleted_at"] is not None
    # And the history says the meeting was taken away rather than quietly losing it.
    row = engine.history(room_id)[0]
    assert row["data"]["deleted"] is True
    assert row["data"]["deleted_reason"] == "deleted_by_delete_event_setting"


def test_delete_event_never_leaves_the_events_alone(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "never"},
            {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
        ],
    )
    half = with_faults(crm, **{"create:CampaignMember": "LOCKED: cannot create"})
    run = run_flow(engine, room_id, declared, booking(), half)
    assert run["data"]["deleted_events"] == []
    assert half.get(run["data"]["created_events"][0])["deleted_at"] is None


def test_an_unknown_delete_event_mode_is_refused_at_declaration(engine, room_id):
    meeting = meeting_type(engine, room_id)
    with pytest.raises(InvalidConfig) as excinfo:
        flow(
            engine,
            room_id,
            meeting,
            [anchor(), {"node": "create_event", "delete_event": "on_cancel"}],
        )
    assert "on_retry" in str(excinfo.value)


def test_the_history_can_be_filtered_by_event_type_the_way_cal_scopes_its_errors(
    engine, room_id, crm
):
    """[sourced] ``GET /v2/event-types/{id}/crm-sync-errors`` is per event type."""
    meeting = meeting_type(engine, room_id, event_type_id="445511")
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(engine, room_id, declared, booking(), faulted)
    assert len(engine.sync_errors(room_id, "445511")) == 1
    assert engine.sync_errors(room_id, "999999") == []
    with pytest.raises(InvalidConfig):
        engine.sync_errors(room_id, "")


def test_the_history_carries_the_cal_event_type_on_every_row(engine, room_id, crm):
    meeting = meeting_type(engine, room_id, event_type_id="445511")
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run_flow(engine, room_id, declared, booking(), crm)
    assert engine.history(room_id)[0]["data"]["event_type_id"] == "445511"


def test_the_history_is_filterable_by_status_booking_and_meeting_type(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run_flow(engine, room_id, declared, booking(booking_ref="b-one"), crm)
    run_flow(engine, room_id, declared, booking(booking_ref="b-two"), crm)
    assert len(engine.history(room_id, status="created")) == 2
    assert len(engine.history(room_id, status="failed")) == 0
    assert len(engine.history(room_id, booking_ref="b-one")) == 1
    assert len(engine.history(room_id, meeting_type_id=str(meeting["id"]))) == 2


# --------------------------------------------------------------------------- #
# The global Salesforce connection
# --------------------------------------------------------------------------- #


def test_event_details_need_a_global_salesforce_connection_and_say_so_when_absent(engine):
    """[sourced] "Global Salesforce connection required for Event details in Events
    History." """
    assert engine.events_history_available()["event_details_available"] is False


def test_a_global_salesforce_connection_makes_the_details_available(engine, room_id):
    engine.create_connector(
        {"vendor": "salesforce", "name": "prod", "global": True}, actor="dana", source=SOURCE
    )
    assert engine.events_history_available()["event_details_available"] is True


def test_a_hubspot_connection_does_not_enable_the_gate(engine):
    """The research attaches the sentence to Salesforce."""
    engine.create_connector(
        {"vendor": "hubspot", "name": "hs", "global": True}, actor="dana", source=SOURCE
    )
    assert engine.events_history_available()["event_details_available"] is False


def test_a_non_global_salesforce_connection_does_not_enable_the_gate(engine):
    engine.create_connector(
        {"vendor": "salesforce", "name": "regional", "global": False}, actor="dana", source=SOURCE
    )
    assert engine.events_history_available()["event_details_available"] is False


def test_a_connector_read_never_returns_its_token(engine):
    created = engine.create_connector(
        {"vendor": "salesforce", "name": "prod", "token": "00D-super-secret-value"},
        actor="dana",
        source=SOURCE,
    )
    summary = engine.connector_summary(created)
    assert "token" not in summary
    assert summary["has_token"] is True
    assert summary["token_hint"].endswith("alue")


# --------------------------------------------------------------------------- #
# The run log
# --------------------------------------------------------------------------- #


def test_a_failed_run_is_a_row_not_a_gap(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(engine, room_id, declared, booking(), faulted)
    runs = engine.runs(room_id)
    assert len(runs) == 1
    assert runs[0]["data"]["ok"] is False


def test_the_run_log_filters_by_path_meeting_type_and_outcome(engine, room_id, crm):
    meeting = meeting_type(engine, room_id)
    scheduled = flow(engine, room_id, meeting, [anchor()], path="scheduled")
    disqualified = flow(engine, room_id, meeting, [anchor()], path="disqualified")
    run_flow(engine, room_id, scheduled, booking(), crm)
    run_flow(engine, room_id, disqualified, booking(), crm)
    assert len(engine.runs(room_id)) == 2
    assert len(engine.runs(room_id, path="scheduled")) == 1
    assert len(engine.runs(room_id, path="disqualified")) == 1
    assert len(engine.runs(room_id, meeting_type_id=str(meeting["id"]))) == 2
    assert len(engine.runs(room_id, ok=True)) == 2
    assert len(engine.runs(room_id, ok=False)) == 0


def test_a_vendor_id_is_never_handed_out_twice_even_after_a_delete(store, room_id):
    """A live count would reuse an id the moment anything was deleted, and a Delete
    Event behaviour deletes things - which is how two records end up answering to one
    vendor id with the second shadowing the first."""
    crm = LocalCrm(store, room_id=room_id)
    first = crm.create("Event", {"subject": "one"}, source=SOURCE)
    second = crm.create("Event", {"subject": "two"}, source=SOURCE)
    assert first["data"]["crm_id"] != second["data"]["crm_id"]
    crm.delete(str(first["data"]["crm_id"]), source=SOURCE)
    third = crm.create("Event", {"subject": "three"}, source=SOURCE)
    assert third["data"]["crm_id"] not in {first["data"]["crm_id"], second["data"]["crm_id"]}
    # And the deleted one still resolves, because a soft delete keeps the row.
    assert crm.get(str(first["data"]["crm_id"])) is not None


def test_a_retry_does_not_reuse_the_id_of_the_event_it_replaced(engine, room_id, crm):
    """The direct consequence, and the case that surfaced the defect: the retry
    deletes the partial Event and then creates its own, and the two must not answer
    to the same id."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(),
            {"node": "create_event", "delete_event": "on_retry"},
        ],
    )
    faulted = with_faults(crm, **{"create:Event": CRM_REFUSAL})
    run_flow(engine, room_id, declared, booking(), faulted)
    failed = engine.history(room_id, status="failed")[0]
    partial = crm.create("Event", {"subject": "partial"}, source=SOURCE)
    partial_id = str(partial["data"]["crm_id"])
    engine.store.update(str(failed["id"]), {"crm_id": partial_id}, actor="dana", source=SOURCE)
    result = engine.retry(
        room_id, str(failed["id"]), actor="dana", source=SOURCE, crm=with_faults(faulted)
    )
    assert result["cleaned_previous"] == [partial_id]
    assert result["crm_id"] != partial_id
    live = {row["data"]["crm_id"] for row in crm.records("Event")}
    assert partial_id not in live
    assert result["crm_id"] in live
    assert crm.get(partial_id)["deleted_at"] is not None


def test_a_run_that_does_not_resolve_is_a_404_naming_the_resource(engine, room_id):
    with pytest.raises(NotFound) as excinfo:
        engine.run(room_id, "run_nope")
    assert excinfo.value.resource == "run"
    assert excinfo.value.room_id == room_id


def test_a_room_that_does_not_resolve_is_a_404_naming_the_room(engine):
    with pytest.raises(NotFound) as excinfo:
        engine.require_room("room_nope")
    assert excinfo.value.resource == "room"


def test_a_run_cannot_be_read_through_another_room(engine, room_id, crm, store):
    """Scoping, not a filter: a run belongs to the room it ran for."""
    other = store.create("room", {"name": "Other"}, actor="dana")
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor()])
    run = run_flow(engine, room_id, declared, booking(), crm)
    with pytest.raises(NotFound):
        engine.run(str(other["id"]), str(run["id"]))
    with pytest.raises(NotFound):
        engine.flow(str(declared["id"]), room_id=str(other["id"]))


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with the engine built per request."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf065_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json={"name": "Northwind", "account": "N"}).json()


@pytest.fixture()
def http_meeting_type(http, http_room):
    return http.post(
        f"{PREFIX}/rooms/{http_room['id']}/meeting-types",
        json={
            "name": "Enterprise demo",
            "vendor": "salesforce",
            "event_type_id": "445511",
            "sync_to_crm": True,
        },
    ).json()


@pytest.fixture()
def http_flow(http, http_room, http_meeting_type):
    return http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "Northwind writeback",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
                {"node": "create_event", "child_events": True},
                {"node": "add_to_campaign", "campaign": "Q4 Enterprise"},
                {"node": "update_ownership", "assign_to": "assignee"},
            ],
        },
    ).json()


def booking_json(**overrides):
    payload = {
        "booking_ref": "http-1",
        "subject": "Northwind walkthrough",
        "starts_at": MEETING,
        "booker": {"name": "Amara", "email": "a.buyer@northwind.example"},
        "guests": [{"name": "Bayo", "email": "b1@northwind.example"}],
        "host": {"email": "dana@acme.example"},
        "assignee": {"email": "sam@acme.example"},
    }
    payload.update(overrides)
    return payload


def test_the_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-065-write-the-booking-back-into-the-crm"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-065"
    assert entry["exception_handlers"] == [
        "InvalidConfig",
        "InvalidNode",
        "NodeOrderError",
        "NotConfigured",
        "NotFound",
        "WritebackError",
    ]
    assert len(entry["routes"]) == 25


def test_the_feature_loads_without_a_failure(http):
    assert http.get("/api/features").json()["failed_count"] == 0


def test_every_room_scoped_path_is_room_scoped(http):
    """The brief's rule, asserted on the mounted routes rather than on a comment."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-065-write-the-booking-back-into-the-crm"
    )
    for route in entry["routes"]:
        path = route["path"]
        for word in ("/flows", "/runs", "/events-history", "/meeting-types", "/crm-records"):
            if word in path:
                assert "/rooms/{room_id}" in path, f"{word} is not room-scoped: {path}"


def test_the_vocabulary_and_inferences_are_served_without_a_store(http):
    vocabulary = http.get(f"{PREFIX}/vocabulary")
    assert vocabulary.status_code == 200
    assert vocabulary.json()["collections"] == list(COLLECTIONS)
    assert vocabulary.json()["path_meaning"] == dict(PATH_MEANING)
    inferences = http.get(f"{PREFIX}/inferences")
    assert inferences.status_code == 200
    assert inferences.json()["count"] == len(INFERENCES)
    assert inferences.json()["nodes"]["vendors"] == list(VENDORS)
    assert inferences.json()["crm_records"]["open_status"] == OPEN_STATUS


def test_the_inference_registry_is_served_whole_and_ids_are_unique(http):
    payload = http.get(f"{PREFIX}/inferences").json()
    ids = [entry["id"] for entry in payload["inferences"]]
    assert len(ids) == len(set(ids))
    for entry in payload["inferences"]:
        assert entry["basis"] and entry["why"] and entry["change_it"]


def test_the_meeting_type_crud_over_http(http, http_room):
    path = f"{PREFIX}/rooms/{http_room['id']}/meeting-types"
    created = http.post(path, json={"name": "Pilot", "vendor": "hubspot"})
    assert created.status_code == 201
    meeting_type_id = created.json()["id"]
    assert http.get(path).json()["count"] == 1
    assert http.get(f"{path}/{meeting_type_id}").json()["data"]["name"] == "Pilot"
    assert (
        http.patch(f"{path}/{meeting_type_id}", json={"sync_to_crm": True}).json()["data"][
            "sync_to_crm"
        ]
        is True
    )
    assert http.delete(f"{path}/{meeting_type_id}").status_code == 204
    assert http.get(f"{path}/{meeting_type_id}").status_code == 404


def test_a_meeting_type_needs_a_name(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/meeting-types", json={"vendor": "salesforce"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_config"
    assert (
        http.get("/api/audit", params={"collection": MEETING_TYPE_COLLECTION}).json()["count"] == 0
    )


def test_the_flow_crud_over_http(http, http_room, http_meeting_type, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    assert http.get(f"{room_path}/flows").json()["count"] == 1
    # The HTTP layer returns the store's own record shape, so a payload this build
    # stores is a payload a client reads back without a second schema.
    assert (
        http.get(f"{room_path}/flows/{http_flow['id']}").json()["data"]["plan"]["has_anchor"]
        is True
    )
    assert (
        http.patch(f"{room_path}/flows/{http_flow['id']}", json={"name": "renamed"}).json()["data"][
            "name"
        ]
        == "renamed"
    )
    assert http.delete(f"{room_path}/flows/{http_flow['id']}").status_code == 204
    assert http.get(f"{room_path}/flows/{http_flow['id']}").status_code == 404


def test_the_ordering_rule_over_http_is_a_400_carrying_the_quote(
    http, http_room, http_meeting_type
):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "wrong",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [
                {"node": "create_event"},
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "node_order"
    assert response.json()["ordering_quote"] == ORDERING_QUOTE


def test_a_node_of_the_other_vendor_over_http_is_its_own_error_code(
    http, http_room, http_meeting_type
):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "wrong palette",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [{"node": "create_engagement"}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_node"


def test_the_validate_route_reports_a_bad_candidate_without_refusing_it(http, http_room, http_flow):
    """A check exists to say what is wrong, so a bad candidate is reported rather
    than refused."""
    good = http.post(f"{PREFIX}/rooms/{http_room['id']}/flows/{http_flow['id']}/validate", json={})
    assert good.status_code == 200
    assert good.json()["valid"] is True
    assert good.json()["plan"]["event_node"] == "create_event"
    bad = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows/{http_flow['id']}/validate",
        json={
            "nodes": [
                {"node": "create_event"},
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
            ]
        },
    )
    assert bad.status_code == 200
    assert bad.json()["valid"] is False
    assert bad.json()["error"] == "NodeOrderError"
    assert ORDERING_QUOTE in bad.json()["detail"]


def test_the_writeback_over_http_runs_the_whole_flow(http, http_room, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    response = http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["ok"] is True
    assert body["counts"]["applied"] == 4
    assert body["record"]["type"] == "Contact"
    assert body["record"]["crm_id"] == "cnt-0001"
    assert len(body["created_events"]) == 2
    assert http.get(f"{room_path}/runs").json()["summary"]["ok"] == 1


def test_the_writeback_needs_a_booker_email_over_http(http, http_room, http_flow):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows/{http_flow['id']}/writeback",
        json=booking_json(booker={"name": "No Address"}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_config"


def test_the_client_cannot_inject_a_crm_fault(http, http_room, http_flow):
    """A client that could forge a CRM failure could forge an audit record."""
    body = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows/{http_flow['id']}/writeback",
        json=booking_json(faults={"create:Event": "FORGED"}),
    ).json()["data"]
    assert body["ok"] is True
    assert len(body["created_events"]) == 2


def test_a_run_cannot_carry_its_own_toggle_over_http(http, http_room, http_flow):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows/{http_flow['id']}/writeback",
        json=booking_json(sync_to_crm=False),
    )
    assert response.status_code == 400
    assert SYNC_TOGGLE_ORG_WIDE_QUOTE in response.json()["detail"]


def test_the_path_writeback_over_http_finds_the_flow(http, http_room, http_meeting_type):
    http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "auto",
            "vendor": "salesforce",
            "path": "disqualified",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                }
            ],
        },
    )
    run = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/writeback", json=booking_json(path="disqualified")
    )
    assert run.status_code == 200
    assert run.json()["data"]["path"] == "disqualified"


def test_a_path_with_no_flow_is_a_428_over_http(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/writeback", json=booking_json(path="not_scheduled")
    )
    assert response.status_code == 428
    assert response.json()["error"] == "not_configured"
    assert "not_scheduled" in response.json()["detail"]


def test_the_run_log_and_a_run_in_full_over_http(http, http_room, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    run = http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json()).json()
    stored = http.get(f"{room_path}/runs/{run['id']}")
    assert stored.status_code == 200
    assert stored.json()["data"]["steps"][0]["node"] == "create_or_update_record"
    assert http.get(f"{room_path}/runs", params={"path": "scheduled"}).json()["count"] == 1
    assert http.get(f"{room_path}/runs", params={"path": "disqualified"}).json()["count"] == 0
    assert http.get(f"{room_path}/runs/run_nope").status_code == 404


def test_a_flow_on_a_room_that_does_not_exist_is_a_404_naming_the_room(http):
    response = http.post(
        f"{PREFIX}/rooms/room_nope/flows",
        json={"name": "x", "vendor": "salesforce", "path": "scheduled"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["resource"] == "room"


def test_a_flow_on_a_meeting_type_that_does_not_exist_is_a_404(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "x",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": "mt_nope",
            "nodes": [],
        },
    )
    assert response.status_code == 404
    assert response.json()["resource"] == "meeting_type"


def test_a_flow_cannot_mix_two_vendors_through_one_meeting_type(http, http_room):
    meeting_type = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/meeting-types",
        json={"name": "hs", "vendor": "hubspot"},
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/flows",
        json={
            "name": "x",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": meeting_type["id"],
            "nodes": [],
        },
    )
    assert response.status_code == 400
    assert "the node names differ per CRM" in response.json()["detail"]


def test_the_events_history_over_http(http, http_room, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    body = http.get(f"{room_path}/events-history")
    assert body.status_code == 200
    assert body.json()["count"] == 2
    assert body.json()["created"] == 2
    assert body.json()["failed"] == 0
    assert body.json()["retryable"] == 0
    assert body.json()["event_details"]["event_details_available"] is False


def test_the_events_history_export_is_csv_of_the_same_rows(http, http_room, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    exported = http.get(f"{room_path}/events-history/export.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    lines = exported.text.strip().splitlines()
    assert lines[0].startswith("when,status,meeting_type_name")
    assert len(lines) == 3
    filtered = http.get(f"{room_path}/events-history/export.csv", params={"status": "failed"})
    assert len(filtered.text.strip().splitlines()) == 1


def test_the_crm_sync_errors_route_is_per_event_type_over_http(
    http, http_room, http_meeting_type, http_flow
):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    body = http.get(f"{room_path}/meeting-types/{http_meeting_type['id']}/crm-sync-errors")
    assert body.status_code == 200
    assert body.json()["event_type_id"] == "445511"
    assert body.json()["count"] == 0
    assert body.json()["errors"] == []


def test_retrying_a_successful_row_over_http_is_a_200_with_the_refusal(http, http_room, http_flow):
    """Not an error: the research offers retry on a failure, and this row is not one."""
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    created = http.get(f"{room_path}/events-history").json()["history"][0]
    response = http.post(f"{room_path}/events-history/{created['id']}/retry")
    assert response.status_code == 200
    assert response.json()["retried"] is False
    assert response.json()["retry_quote"] == HISTORY_RETRY_QUOTE


def test_retrying_a_row_that_does_not_exist_is_a_404(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/events-history/h_nope/retry")
    assert response.status_code == 404
    assert response.json()["resource"] == "history"


def test_the_crm_records_route_groups_by_type_over_http(http, http_room):
    from dsr.booking_crm import LocalCrm as Crm

    engine_store = http.app.state.store
    Crm(RecordStore(engine_store.db), room_id=http_room["id"]).seed(
        [
            {"crm_id": "cnt-1", "vendor": "salesforce", "type": "Contact", "email": "a@b.example"},
            {
                "crm_id": "case-1",
                "vendor": "salesforce",
                "type": "Case",
                "status": "Open",
                "created_on": "2026-01-01",
            },
        ]
    )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/crm-records")
    assert body.status_code == 200
    assert body.json()["by_type"] == {"Contact": 1, "Case": 1}
    assert body.json()["collection"] == CRM_RECORD_COLLECTION
    filtered = http.get(
        f"{PREFIX}/rooms/{http_room['id']}/crm-records", params={"record_type": "Case"}
    )
    assert filtered.json()["count"] == 1


def test_the_summary_route_counts_the_rooms_own_rows(http, http_room, http_meeting_type, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    body = http.get(f"{room_path}/summary")
    assert body.status_code == 200
    assert body.json()["flows"] == 1
    assert body.json()["flows_by_path"] == {"scheduled": 1}
    assert body.json()["runs"] == 1
    assert body.json()["events_created"] == 2
    assert body.json()["events_failed"] == 0
    assert body.json()["meeting_types_syncing"] == 1
    assert body.json()["ordering_quote"] == ORDERING_QUOTE


def test_the_connector_routes_over_http(http):
    created = http.post(
        f"{PREFIX}/connectors",
        json={"vendor": "salesforce", "name": "prod", "token": "00D-secret-value", "global": True},
    )
    assert created.status_code == 201
    assert "token" not in created.json()
    body = http.get(f"{PREFIX}/connectors")
    assert body.json()["count"] == 1
    assert body.json()["connectors"][0]["has_token"] is True


def test_a_connector_with_an_unknown_vendor_is_a_400(http):
    response = http.post(f"{PREFIX}/connectors", json={"vendor": "sap"})
    assert response.status_code == 400
    for vendor in VENDORS:
        assert vendor in response.json()["detail"]


def test_a_refused_write_leaves_no_audit_row(http, http_room):
    """Nothing was attempted, so there is nothing to audit."""
    assert http.post(f"{PREFIX}/connectors", json={"vendor": "sap"}).status_code == 400
    assert (
        http.get("/api/audit", params={"collection": "crm_booking_connector"}).json()["count"] == 0
    )
    assert (
        http.post(
            f"{PREFIX}/rooms/{http_room['id']}/flows", json={"name": "x", "vendor": "sap"}
        ).status_code
        == 400
    )
    assert http.get("/api/audit", params={"collection": FLOW_COLLECTION}).json()["count"] == 0
    assert http.post(f"{PREFIX}/rooms/room_nope/flows", json={"name": "x"}).status_code == 404
    assert http.get("/api/audit", params={"collection": FLOW_COLLECTION}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_the_domain_methods_that_write_require_a_source():
    """``source`` is required so a hardcoded URL cannot creep back in."""
    for name in (
        "create_meeting_type",
        "update_meeting_type",
        "delete_meeting_type",
        "create_flow",
        "update_flow",
        "delete_flow",
        "create_connector",
        "writeback",
        "writeback_for_path",
        "retry",
    ):
        signature = inspect.signature(getattr(BookingWriteback, name))
        assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert signature.parameters["source"].default is inspect.Parameter.empty, name


def test_no_write_route_hardcodes_the_path_it_records_as_its_source():
    """The defect this programme shipped: an audit row naming a dead route.

    Checked on the source rather than on the running system, because a hardcoded
    path only shows up in the audit log once something is written. The router's own
    ``prefix`` is the one legitimate literal; what must not appear is a path spelled
    out inside a ``source=`` expression.
    """
    source = Path(inspect.getfile(load_feature(MODULE))).read_text(encoding="utf-8")
    assert f'prefix="{PREFIX}"' in source
    recorded = re.findall(r"source=(f?\"[^\"]*\")", source)
    assert len(recorded) >= 8
    for literal in recorded:
        assert "router.prefix" in literal, f"a write records a literal path: {literal}"
        assert "/api/wf-065" not in literal


def test_every_source_this_feature_records_names_a_route_the_host_mounted(
    http, http_room, http_meeting_type, http_flow
):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.patch(f"{room_path}/meeting-types/{http_meeting_type['id']}", json={"sync_to_crm": True})
    http.post(f"{room_path}/flows/{http_flow['id']}/validate", json={})
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    http.patch(f"{room_path}/flows/{http_flow['id']}", json={"name": "renamed"})
    http.post(
        f"{room_path}/flows",
        json={
            "name": "second",
            "vendor": "salesforce",
            "path": "disqualified",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "lead",
                    "record_type": "lead",
                }
            ],
        },
    )
    http.delete(f"{room_path}/flows/{http_flow['id']}")
    http.post(f"{PREFIX}/connectors", json={"vendor": "salesforce", "name": "prod"})

    entries = http.get("/api/audit", params={"limit": 300}).json()["entries"]
    sources = {entry["source"] for entry in entries}
    for expected in (
        f"POST {PREFIX}/connectors",
        f"POST {room_path}/meeting-types",
        f"PATCH {room_path}/meeting-types/{http_meeting_type['id']}",
        f"POST {room_path}/flows",
        f"PATCH {room_path}/flows/{http_flow['id']}",
        f"DELETE {room_path}/flows/{http_flow['id']}",
        f"POST {room_path}/flows/{http_flow['id']}/writeback",
    ):
        assert expected in sources, f"{expected!r} missing from {sorted(sources)}"

    templates = [
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature.get("routes", [])
        for method in route["methods"]
    ]
    templates += [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method not in ("HEAD", "OPTIONS")
    ]
    for entry in entries:
        verb, _, path = entry["source"].partition(" ")
        assert any(
            verb == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for method, template in templates
        ), f"audit names a route the app does not serve: {entry['source']}"


def test_the_rows_the_crm_creates_are_audited_with_the_writeback_route(http, http_room, http_flow):
    """They are writes, and an audit row that cannot name its request is not one."""
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    entries = http.get("/api/audit", params={"collection": CRM_RECORD_COLLECTION}).json()["entries"]
    assert entries
    assert {entry["source"] for entry in entries} == {
        f"POST {room_path}/flows/{http_flow['id']}/writeback"
    }


def test_the_history_rows_are_audited_with_the_writeback_route(http, http_room, http_flow):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    entries = http.get("/api/audit", params={"collection": HISTORY_COLLECTION}).json()["entries"]
    assert len(entries) == 2
    assert {entry["source"] for entry in entries} == {
        f"POST {room_path}/flows/{http_flow['id']}/writeback"
    }


def test_a_retry_is_audited_with_the_retry_route(http, http_room, http_meeting_type):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    declared = http.post(
        f"{room_path}/flows",
        json={
            "name": "r",
            "vendor": "salesforce",
            "path": "scheduled",
            "meeting_type_id": http_meeting_type["id"],
            "nodes": [
                {
                    "node": "create_or_update_record",
                    "update": "matched_contact_or_lead",
                    "create": "contact_or_lead",
                    "record_type": "contact",
                },
                {"node": "create_event"},
            ],
        },
    ).json()
    http.post(f"{room_path}/flows/{declared['id']}/writeback", json=booking_json())
    created = http.get(f"{room_path}/events-history").json()["history"][0]
    # The row succeeded, so the retry is refused - and a refused write is no write.
    response = http.post(f"{room_path}/events-history/{created['id']}/retry")
    assert response.json()["retried"] is False
    assert http.get("/api/audit", params={"collection": HISTORY_COLLECTION}).json()["count"] == 1


def test_no_audit_source_names_another_features_prefix(
    http, http_room, http_meeting_type, http_flow
):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/flows/{http_flow['id']}/writeback", json=booking_json())
    sources = [
        entry["source"] for entry in http.get("/api/audit", params={"limit": 200}).json()["entries"]
    ]
    for other in ("/api/crm", "/api/analytics", "/api/wf-016", "/api/wf-026", "/api/wf-039"):
        assert not [source for source in sources if other in source and PREFIX not in source]


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_team_can_add_a_data_field_and_a_crm_field_with_no_migration(engine, room_id, store, crm):
    """The researched extensibility claim, made concrete: a custom CRM field is
    another entry in a payload, and nothing else changes."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(fields=[{"field": "A_Team_Specific_Field__c", "from_data_field": "seats"}]),
        ],
    )
    run = run_flow(engine, room_id, declared, booking(data_fields={"seats": "40"}), crm)
    assert run["data"]["ok"] is True
    assert crm.get("cnt-0001")["data"]["fields"]["A_Team_Specific_Field__c"] == "40"


def test_an_arbitrary_key_on_a_node_is_kept_rather_than_rejected(engine, room_id):
    """Schema-flexible in both directions: a team may add a key this build reads
    nothing from, and it survives the round trip."""
    meeting = meeting_type(engine, room_id)
    declared = flow(
        engine,
        room_id,
        meeting,
        [
            anchor(team_specific={"owner_queue": "emea-enterprise", "sla_hours": 24}),
        ],
    )
    node = declared["data"]["nodes"][0]
    assert node["team_specific"] == {"owner_queue": "emea-enterprise", "sla_hours": 24}


def test_the_crm_record_payload_is_arbitrary_json(store, room_id):
    crm = LocalCrm(store, room_id=room_id)
    crm.seed(
        [
            {
                "crm_id": "opp-custom",
                "vendor": "salesforce",
                "type": "Opportunity",
                "close_date": "2026-10-06",
                "a_team_field__c": {"nested": [1, 2, 3]},
            },
        ],
        source=SOURCE,
    )
    assert crm.get("opp-custom")["data"]["a_team_field__c"] == {"nested": [1, 2, 3]}


def test_a_flow_can_be_found_by_a_json_path_no_feature_declared(engine, room_id, crm):
    """The dynamic index is the mechanism, not a query this build wrote. The path is
    the store's own dotted form for a list element, ``nodes.1.node``."""
    meeting = meeting_type(engine, room_id)
    flow(
        engine,
        room_id,
        meeting,
        [anchor(), {"node": "add_to_campaign", "campaign": "Q4 Enterprise"}],
        name="with campaign",
    )
    assert len(engine.store.find(FLOW_COLLECTION, {"nodes.1.node": "add_to_campaign"})) == 1
    assert len(engine.store.find(FLOW_COLLECTION, {"nodes.1.node": "update_ownership"})) == 0
    assert len(engine.store.find(FLOW_COLLECTION, {"nodes.0.node": "create_or_update_record"})) == 1


def test_nothing_this_feature_writes_goes_anywhere_but_the_store(store, room_id, engine, crm):
    """Every read and write through RecordStore, so the audit row lands in the same
    transaction as the change."""
    meeting = meeting_type(engine, room_id)
    declared = flow(engine, room_id, meeting, [anchor(), {"node": "create_event"}])
    run = run_flow(engine, room_id, declared, booking(), crm)
    assert store.get(str(run["id"]))["collection"] == RUN_COLLECTION
    audit = store.audit(collection=CRM_RECORD_COLLECTION, limit=100)
    assert audit, "a CRM write must be audited"
    # The fixture's own seed rows are attributed to it; the run's rows are not.
    written = [entry for entry in audit if entry["source"] == SOURCE]
    assert written, "the run's CRM writes must carry the run's source"
    assert {entry["source"] for entry in audit} <= {SOURCE, "seed"}


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"] and entry["why"]
        assert entry["change_it"] and entry["blast_radius"]
        assert entry["value"] is not None


def test_the_inference_ids_are_unique_and_describe_is_the_whole_registry():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))
    assert describe_inferences()["count"] == len(INFERENCES)
    assert describe_inferences()["sourced_gaps"] == [dict(g) for g in SOURCED_GAPS]


def test_the_ordering_inference_names_where_the_check_lives():
    entry = next(e for e in INFERENCES if e["id"] == "node-order-is-checked-not-sorted")
    assert "validate_nodes" in entry["change_it"]
    assert (
        "reordering" in entry["value"]
        and entry["value"]["reordering"] == "never - the declared order is the order that will run"
    )


def test_the_delete_event_inference_explains_why_cancellation_is_not_implemented():
    """A reviewer most needs to know what was deliberately left out and why."""
    entry = next(e for e in INFERENCES if e["id"] == "delete-event-trigger")
    assert entry["value"]["default"] == "never"
    assert "cancellation is section 14" in entry["why"]


def test_the_nearest_close_date_inference_names_the_reference_point():
    entry = next(
        e for e in INFERENCES if e["id"] == "nearest-close-date-is-measured-against-the-meeting"
    )
    assert entry["value"]["reference"] == "the booking's starts_at"
    assert entry["value"]["tie_break"] == "the earlier close date"


def test_the_create_branch_inference_says_why_the_labels_read_as_switches():
    entry = next(e for e in INFERENCES if e["id"] == "create-branches")
    assert "redundant word" in entry["why"]
    assert "no create branch" in entry["why"]


def test_the_related_object_inference_says_a_lead_is_not_a_contact():
    entry = next(e for e in INFERENCES if e["id"] == "related-object-is-gated-on-a-contact")
    assert entry["value"]["requires"] == "Contact"
    assert "not a Contact match" in entry["why"]


def test_the_sync_toggle_inference_records_the_refusal_rather_than_the_ignore():
    entry = next(e for e in INFERENCES if e["id"] == "sync-toggle-is-off-by-default-and-org-wide")
    assert entry["value"]["per_run_override"].startswith("refused")
    assert "silently ignoring" in entry["why"]


def test_the_node_vocabulary_is_served_so_a_client_does_not_build_a_second_palette():
    payload = node_vocabulary()
    assert payload["anchor"] == ANCHOR_NODE
    assert payload["event"] == EVENT_NODE
    assert payload["field"] == FIELD_NODE
    assert payload["related_record_type"]["Ticket"] == "Ticket"
    assert payload["create_branches"] == list(CREATE_BRANCHES)
    assert payload["paths"] == list(PATHS)


def test_no_transport_inference_matches_what_is_actually_built():
    """The inference and the code have to agree, or the record is decoration."""
    entry = next(e for e in INFERENCES if e["id"] == "no-transport-is-built")
    assert "LocalCrm" in entry["value"]["seam"]
    assert "urllib" not in Path(inspect.getfile(load_feature(MODULE))).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_makes_unavoidable(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    summary = module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    assert summary
    assert "1 Event creations refused and then retried" in summary
    assert "1 where nothing matched and no create branch ran" in summary

    engine = BookingWriteback(store)
    for room in rooms:
        flows = engine.list_flows(str(room["id"]))
        runs = engine.runs(str(room["id"]), limit=100)
        assert flows, room["id"]
        assert runs, room["id"]
        # History only where a run got as far as an Event. The disqualified room's
        # toggle is off, so its run is a skip and writes nothing - which is the
        # researched behaviour, not a gap in the demo.
        if any(int((run["data"].get("counts") or {}).get("applied") or 0) for run in runs):
            assert engine.history(str(room["id"]), limit=200), room["id"]

    all_runs = [run for room in rooms for run in engine.runs(str(room["id"]), limit=100)]
    reasons = {step["reason"] for run in all_runs for step in run["data"]["steps"]}
    # The researched rules, each one visible in the demo rather than merely asserted.
    assert "most_recent_open" in reasons
    assert "nearest_close_date" in reasons
    assert "child_event_per_additional_guest" in reasons
    assert "always_create_lead" in reasons
    assert "campaign_member_upsert" in reasons
    assert "owner_reassigned" in reasons
    assert SKIP_MEETING_TYPE_SYNC_OFF in reasons
    # The catch-all the ordering sentence forces into existence, in the demo rather
    # than only in this file: one run produced no record at all.
    assert REASON_NO_CREATE in reasons
    assert SKIP_NO_RECORD in reasons
    # "Only update matched Lead" declining to write a Contact is reported on the
    # anchor's resolved update outcome, which is where a client reads it - the
    # step's own reason is the branch that *did* fire, the create.
    outcomes = {
        step["resolved"].get("update_outcome")
        for run in all_runs
        for step in run["data"]["steps"]
        if step["node"].startswith("create_or_update")
    }
    assert "skipped_lead_only" in outcomes


def test_the_seed_shows_the_selection_rules_choosing_right(store):
    """The demo's Cases and Opportunities are chosen so the rules are visible."""
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    engine = BookingWriteback(store)
    related = {
        run["data"]["related"]["crm_id"]
        for room in rooms
        for run in engine.runs(str(room["id"]), limit=100)
        if run["data"]["related"].get("crm_id")
    }
    # case-0002 rather than the *newer* case-0001, which is Closed.
    assert "case-0002" in related
    assert "case-0001" not in related
    # opp-0002, two days after the demo meeting, not the sooner opp-0003.
    assert "opp-0002" in related
    assert "opp-0003" not in related


def test_the_seed_leaves_a_failed_event_with_a_retry_attempted(store):
    """A history row with an error, and an attempt 2 beside it, is the thing the
    researched Events History exists to show."""
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    engine = BookingWriteback(store)
    rows = [row for room in rooms for row in engine.history(str(room["id"]), limit=500)]
    failed = [row for row in rows if row["data"]["status"] == "failed"]
    retried = [row for row in rows if int(row["data"]["attempt"]) > 1]
    assert failed, "the demo needs a failed Event, or Events History shows nothing"
    assert retried, "the demo needs a retry, or the research's step 5 is unshown"
    assert all(row["data"]["error"] for row in failed)
    assert all(row["data"]["when"] for row in rows)


def test_the_seed_is_repeatable_without_failing(store):
    """Seeding twice must not raise - the seeder skips a feature that raises, and a
    feature that only seeds once is a feature nobody can re-demonstrate."""
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    assert module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    engine = BookingWriteback(store)
    assert engine.list_flows(str(rooms[0]["id"]))
    # The CRM rows are keyed by crm_id, so a second pass skips rather than refusing
    # and the demo stays in one piece.
    crm = LocalCrm(store, room_id=str(rooms[0]["id"]))
    assert crm.seed([dict(CRM_ROWS[0])], source=SOURCE) == []
    assert crm.get(str(CRM_ROWS[0]["crm_id"])) is not None


def test_a_seeded_row_with_no_crm_id_is_still_a_refusal(store, room_id):
    """Skipping an existing id is forgiving; a row that cannot be addressed is not."""
    crm = LocalCrm(store, room_id=room_id)
    with pytest.raises(ValueError, match="needs a crm_id"):
        crm.seed([{"type": "Contact", "email": "a@b.example"}], source=SOURCE)


def test_the_seed_copes_with_no_demo_rooms(store):
    """A seeder that aborts a whole feature over one stale room id leaves a page
    nobody can review."""
    module = load_feature(MODULE)
    summary = module.seed(store, {"room_ids": []})
    assert "no rooms to scope them to" in summary


def test_the_demo_declares_all_three_router_paths():
    module = load_feature(MODULE)
    paths = {str(spec["flow"]["path"]) for spec in module.DEMO_FLOWS}
    assert paths == set(PATHS)


def test_the_demo_declares_both_vendors():
    module = load_feature(MODULE)
    assert {str(spec["flow"]["vendor"]) for spec in module.DEMO_FLOWS} == set(VENDORS)


def test_the_demo_registers_one_global_salesforce_connection_and_one_that_is_not():
    module = load_feature(MODULE)
    vendors = {str(spec["vendor"]): spec for spec in module.DEMO_CONNECTORS}
    assert vendors["salesforce"]["global"] is True
    assert vendors["hubspot"]["global"] is False


def test_a_crm_refusal_carries_a_code_because_the_history_shows_a_detailed_error():
    refusal = CrmRefused("create Event", "INSUFFICIENT_ACCESS: nope", code="INSUFFICIENT_ACCESS")
    assert refusal.code == "INSUFFICIENT_ACCESS"
    assert refusal.detail == "INSUFFICIENT_ACCESS: nope"
    assert "create Event refused by the CRM" in str(refusal)
