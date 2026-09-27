"""Tests for WF-039: write account + contact + opportunity as one atomic transaction.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-039.md``, and each section below
names which one it is pinning:

* the six-step user flow, and step 2's "**in dependency order**";
* the four APIs hit - Salesforce Composite with ``allOrNone`` and
  ``collateSubrequests``, the sObject Tree endpoint and its four limits, the
  Dataverse ``$batch`` changeset with ``Content-ID`` and ``$1``, and HubSpot's
  contacts batch create and association ``PUT``;
* the two reference syntaxes, including the two shapes the research quotes by
  name: ``@{NewAccount.BillingAddress.city}`` and ``@{AccountInfo.recentItems[0].Id}``;
* the three rules the research states most plainly and implementations most often
  skip - **"Dependent subrequests aren't executed"**, **"the entire composite
  request is rolled back"**, and **"set collateSubrequests to false"** for an
  implicit dependency;
* the extensibility claim: the dependency graph is declared as data, so a
  deployment can add a 4th record type without touching the transport;
* and the closing requirement: **"the room shows a single actionable error"**.

The parts the research does *not* fix are the design inferences, and they are
tested as inferences: named, bounded, and changeable in one place.

Committing is driven through the in-process CRM, so the rollback, the skip and
the collation failure are all asserted without a socket and without a network
flake.
"""

from __future__ import annotations

import inspect
import json
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.atomic_bundle import (
    ATOMICITY,
    BUNDLE_COLLECTION,
    CONNECTOR_COLLECTION,
    DIALECTS,
    FAIL_COLLATION_VIOLATION,
    HS_ASSOCIATION_PATH,
    HS_CONTACTS_BATCH_PATH,
    INFERENCES,
    LIMITS,
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SKIPPED,
    POLICIES,
    POLICY_PARTIAL,
    POLICY_STRICT,
    RUN_COLLECTION,
    SOURCED_QUOTES,
    TARGET_COLLECTION,
    BundleCommitter,
    BundleShapeError,
    LimitExceeded,
    LocalCrm,
    NotFound,
    PolicyError,
    ReferenceError,
    RenderOptions,
    content_id,
    content_id_for,
    describe_inferences,
    describe_vocabulary,
    dv_references,
    plan_bundle,
    render,
    resolve_dv,
    resolve_sf,
    sf_references,
)
from dsr.atomic_bundle.engine import TRANSPORTS
from dsr.atomic_bundle.planner import (
    WARN_COLLATION_NOT_APPLICABLE,
    WARN_COMPENSATION_NOT_TRANSACTION,
    WARN_IMPLICIT_DEPENDENCY,
    WARN_TREE_HAS_NO_ORDERING_FLAG,
)
from dsr.atomic_bundle.transport import RESPONSE_NOT_PARSED_NOTE
from dsr.atomic_bundle.vocabulary import (
    DATAVERSE_ATOMICITY_QUOTE,
    SALESFORCE_TREE_ATOMICITY_QUOTE,
    USER_FLOW,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-039"

MODULE = "wf039_write_account_contact_opportunity_as_o"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/bundles/{{bundle_id}}/commit"

#: The researched bundle: Account -> Contact -> Opportunity, in dependency order.
BUNDLE: dict = {
    "name": "Northwind Q4 opportunity bundle",
    "records": [
        {
            "reference_id": "refAccount",
            "type": "Account",
            "fields": {"Name": "Northwind Traders", "BillingCountry": "GB"},
        },
        {
            "reference_id": "refContact",
            "type": "Contact",
            "fields": {"LastName": "Okonkwo", "Email": "a.buyer@northwind.example"},
            "parent": {"reference": "refAccount", "field": "AccountId"},
        },
        {
            "reference_id": "refOpportunity",
            "type": "Opportunity",
            "fields": {"Name": "Northwind - Q4 enterprise"},
            "parent": {"reference": "refAccount", "field": "AccountId"},
        },
    ],
}

CONNECTOR: dict = {
    "name": "Salesforce production",
    "dialect": "salesforce_composite",
    "policy": POLICY_STRICT,
    "collate_subrequests": False,
}

FAILURE = "REQUIRED_FIELD_MISSING: Required fields are missing: [Email]"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf039.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def committer(store):
    return BundleCommitter(store)


@pytest.fixture()
def room(store):
    return store.create(
        "room",
        {"name": "Northwind â€” Enterprise Evaluation", "account": "Northwind Traders"},
        actor="dana",
    )


@pytest.fixture()
def connector(committer):
    return committer.create_connector(CONNECTOR, actor="dana", source=SOURCE)


@pytest.fixture()
def bundle(committer, room, connector):
    return committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )


def crm(store, faults=None, room_id=""):
    return LocalCrm(store, faults=faults or {}, room_id=room_id)


def count(store, collection):
    """``RecordStore`` has no ``count``; this is the same query without a new seam."""
    return len(store.list(collection, limit=1000))


def rendered(
    bundle_spec, *, dialect, policy=POLICY_STRICT, collate_subrequests=False, options=None
):
    """The plan and the request a bundle renders into, for one dialect."""
    plan = plan_bundle(
        bundle_spec, dialect=dialect, policy=policy, collate_subrequests=collate_subrequests
    )
    return plan, render(plan, options or RenderOptions())


# --------------------------------------------------------------------------- #
# The researched vocabulary, served as data
# --------------------------------------------------------------------------- #


def test_the_four_documented_dialects_are_all_present():
    assert DIALECTS == (
        "salesforce_composite",
        "salesforce_sobject_tree",
        "dataverse_batch",
        "hubspot_associations",
    )


def test_the_vocabulary_carries_the_documented_endpoints():
    endpoints = describe_vocabulary()["endpoints"]
    assert endpoints["salesforce_composite"] == "/services/data/vXX.X/composite"
    assert endpoints["salesforce_sobject_tree"] == "/services/data/vXX.X/composite/tree/{sobject_name}"
    assert endpoints["dataverse_batch"] == "/api/data/v9.2/$batch"
    assert endpoints["hubspot_associations"]["batch_create"] == HS_CONTACTS_BATCH_PATH
    assert "2026-09" in endpoints["hubspot_associations"]["association"]


def test_the_vocabulary_carries_every_documented_limit_with_its_quote():
    limits = describe_vocabulary()["limits"]
    assert limits["salesforce_composite.subrequests"]["max"] == 25
    assert "up to 25 subrequests" in limits["salesforce_composite.subrequests"]["quote"]
    assert limits["salesforce_composite.collections"]["max"] == 5
    assert limits["salesforce_sobject_tree.records"]["max"] == 200
    assert limits["salesforce_sobject_tree.types"]["max"] == 5
    assert limits["salesforce_sobject_tree.depth"]["max"] == 5
    assert limits["dataverse_batch.requests"]["max"] == 1000
    assert set(limits) == set(LIMITS)


def test_both_rollback_policies_are_described_in_the_researchers_own_words():
    policies = {entry["id"]: entry["meaning"] for entry in describe_vocabulary()["policies"]}
    assert set(policies) == set(POLICIES)
    assert "entire composite request is rolled back" in policies[POLICY_STRICT]
    assert "Dependent subrequests aren't executed" in policies[POLICY_PARTIAL]


def test_the_two_documented_atomicity_quotes_are_carried_verbatim():
    assert "considered *atomic*" in DATAVERSE_ATOMICITY_QUOTE
    assert "entire request fails" in SALESFORCE_TREE_ATOMICITY_QUOTE
    for quote in (DATAVERSE_ATOMICITY_QUOTE, SALESFORCE_TREE_ATOMICITY_QUOTE):
        assert quote in " ".join(SOURCED_QUOTES)


def test_the_outcome_vocabulary_distinguishes_skipped_from_failed():
    """"Dependent subrequests aren't executed" is not the same as a refusal.

    A skipped subrequest has nothing wrong with it; its input never arrived. A
    vocabulary that collapsed the two would make a run over-report its failures.
    """
    outcomes = describe_vocabulary()["outcomes"]
    assert outcomes == [OUTCOME_CREATED, OUTCOME_ROLLED_BACK, OUTCOME_SKIPPED, OUTCOME_FAILED]
    assert describe_vocabulary()["skip_reasons"]["dependency_failed"].startswith("[sourced]")


def test_the_user_flow_is_carried_as_the_six_researched_steps():
    flow = [entry["text"] for entry in describe_vocabulary()["user_flow"]]
    assert list(USER_FLOW) == flow
    assert len(flow) == 6
    assert "in dependency order" in flow[1]
    assert "single actionable error" in flow[5]


def test_the_vocabulary_names_the_surfaces_this_build_does_not_implement():
    surfaces = {entry["surface"] for entry in describe_vocabulary()["adjacent_surfaces"]}
    assert any("continue-on-error" in surface for surface in surfaces)
    assert any("scheduled job" in surface for surface in surfaces)
    # Section 7 of the research is WF-040. Saying so is what stops a reader
    # treating its absence here as an oversight.
    assert any("WF-040" in entry["why_not"] for entry in describe_vocabulary()["adjacent_surfaces"])


def test_the_researchs_own_gaps_are_carried_next_to_the_facts():
    gaps = describe_vocabulary()["sourced_gaps"]
    assert any("sObject Tree request body" in gap for gap in gaps)
    assert any("cross-object transaction for HubSpot" in gap for gap in gaps)


# --------------------------------------------------------------------------- #
# Inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"] and entry["why"]
        assert "value" in entry and "change_it" in entry and "blast_radius" in entry


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_describe_carries_the_sourced_quotes_the_inferences_lean_on():
    described = describe_inferences()
    assert described["count"] == len(INFERENCES)
    assert "allOrNone" in " ".join(described["sourced_quotes"])
    assert described["sourced_gaps"]


def test_the_inference_registry_says_which_shapes_are_not_sourced():
    """A reviewer must be able to tell a reproduced shape from a designed one.

    The sObject Tree body and the changeset framing are this build's own reading;
    the limits and the atomicity sentences are not. If that distinction were lost,
    a deployment would trust a shape no source supports.
    """
    entries = {entry["id"]: entry for entry in INFERENCES}
    assert entries["sobject-tree-body"]["basis"].startswith("[not sourced]")
    assert entries["dataverse-mime-framing"]["basis"].startswith("[partly sourced]")
    assert entries["vendor-response-is-not-parsed"]["basis"].startswith("[not sourced]")
    assert "[sourced]" in entries["default-policy"]["basis"]


def test_the_default_policy_is_strict_and_the_reason_is_the_ticket_name():
    entries = {entry["id"]: entry for entry in INFERENCES}
    assert entries["default-policy"]["value"]["default"] == POLICY_STRICT
    assert "atomic transaction" in entries["default-policy"]["why"]


# --------------------------------------------------------------------------- #
# Reference resolution: the two documented syntaxes
# --------------------------------------------------------------------------- #


def test_the_salesforce_reference_pattern_accepts_the_two_documented_shapes():
    """The research names them: a dotted field path and an indexed one."""
    assert sf_references("@{NewAccount.BillingAddress.city}") == [
        ("NewAccount", "BillingAddress.city")
    ]
    assert sf_references("@{AccountInfo.recentItems[0].Id}") == [
        ("AccountInfo", "recentItems[0].Id")
    ]


def test_a_reference_is_found_wherever_it_sits_in_a_body():
    body = {"a": "@{refAccount.id}", "b": ["x", {"c": "see @{refContact.id}"}], "d": 3}
    assert sf_references(body) == [("refAccount", "id"), ("refContact", "id")]


def test_a_reference_must_name_an_identifier_before_a_dot():
    assert sf_references("@{not an id}") == []
    assert sf_references("@{9leading}") == []


def test_a_whole_string_that_is_one_reference_resolves_to_the_value_not_a_string():
    resolved = resolve_sf({"AccountId": "@{refAccount.id}"}, {"refAccount": {"id": "001ABC"}})
    assert resolved == {"AccountId": "001ABC"}


def test_a_reference_inside_a_larger_string_is_substituted_in_place():
    resolved = resolve_sf({"note": "city is @{NewAccount.BillingAddress.city}!"}, {
        "NewAccount": {"BillingAddress": {"city": "Leeds"}}
    })
    assert resolved == {"note": "city is Leeds!"}


def test_the_documented_indexed_path_resolves_into_a_list():
    assert resolve_sf(
        {"Id": "@{AccountInfo.recentItems[0].Id}"},
        {"AccountInfo": {"recentItems": [{"Id": "068ABC"}]}},
    ) == {"Id": "068ABC"}


def test_a_reference_to_a_path_the_result_does_not_carry_resolves_to_null():
    """"Real record URIs" means a field a record does not have has no value.

    Crashing here would turn a CRM that returned a thinner object than expected
    into a 500 on a write that would otherwise have gone through.
    """
    assert resolve_sf({"x": "@{refAccount.missing}"}, {"refAccount": {"id": "1"}}) == {"x": None}


def test_a_reference_to_a_subrequest_with_no_result_yet_raises_and_names_it():
    with pytest.raises(KeyError, match="refAccount"):
        resolve_sf({"AccountId": "@{refAccount.id}"}, {})


def test_the_dataverse_reference_pattern_reads_the_documented_dollar_form():
    assert dv_references({"originatingleadid@odata.bind": "$1"}) == [1]
    assert dv_references({"a": "$2", "b": ["$3", "$1"]}) == [2, 3, 1]


def test_a_content_id_is_the_one_based_position_of_a_step():
    order = ["refAccount", "refContact", "refOpportunity"]
    assert content_id(0) == "1"
    assert content_id_for("refAccount", order) == 1
    assert content_id_for("refOpportunity", order) == 3


def test_a_dataverse_reference_resolves_to_the_uri_the_crm_returned():
    resolved = resolve_dv({"originatingleadid@odata.bind": "$1"}, {1: "/api/data/v9.2/accounts(abc)"})
    assert resolved == {"originatingleadid@odata.bind": "/api/data/v9.2/accounts(abc)"}


def test_a_dataverse_reference_to_a_part_with_no_uri_raises():
    with pytest.raises(KeyError, match=r"\$2"):
        resolve_dv({"x": "$2"}, {1: "one"})


# --------------------------------------------------------------------------- #
# The plan: the dependency graph as data
# --------------------------------------------------------------------------- #


def test_the_plan_keeps_the_declared_dependency_order():
    plan = plan_bundle(BUNDLE)
    assert plan.order == ("refAccount", "refContact", "refOpportunity")
    assert plan.subrequests == 3
    assert plan.record_count == 3
    assert plan.distinct_types == ("Account", "Contact", "Opportunity")


def test_a_step_records_what_it_depends_on_and_which_of_those_are_explicit():
    plan = plan_bundle(BUNDLE)
    contact = plan.step("refContact")
    assert contact.explicit_dependencies() == ("refAccount",)
    assert contact.dependencies() == ("refAccount",)
    assert plan.step("refAccount").is_root


def test_a_forward_reference_is_refused_rather_than_reordered():
    """Step 2 says "in dependency order", so a forward reference is a contradiction."""
    spec = {
        "name": "backwards",
        "records": [
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "O"},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
            {"reference_id": "refAccount", "type": "Account", "fields": {"Name": "N"}},
        ],
    }
    with pytest.raises(ReferenceError, match="dependency order"):
        plan_bundle(spec)


def test_a_self_reference_is_refused():
    spec = {
        "name": "self",
        "records": [
            {
                "reference_id": "refA",
                "type": "Account",
                "fields": {"Name": "N"},
                "parent": {"reference": "refA", "field": "SelfId"},
            }
        ],
    }
    with pytest.raises(ReferenceError, match="itself"):
        plan_bundle(spec)


def test_a_dependency_on_a_subrequest_that_does_not_exist_is_refused():
    spec = {
        "name": "dangling",
        "records": [
            {
                "reference_id": "refA",
                "type": "Account",
                "fields": {"Name": "N"},
                "parent": {"reference": "refNope", "field": "AccountId"},
            }
        ],
    }
    with pytest.raises(ReferenceError, match="not in the bundle"):
        plan_bundle(spec)


def test_a_placeholder_in_a_body_that_names_a_later_subrequest_is_refused():
    spec = {
        "name": "future",
        "records": [
            {"reference_id": "refA", "type": "Account", "fields": {"OwnerId": "@{refB.id}"}},
            {"reference_id": "refB", "type": "Contact", "fields": {"LastName": "O"}},
        ],
    }
    with pytest.raises(ReferenceError, match="dependency order"):
        plan_bundle(spec)


def test_a_placeholder_that_names_nothing_is_refused():
    spec = {
        "name": "nowhere",
        "records": [
            {"reference_id": "refA", "type": "Account", "fields": {"OwnerId": "@{refNope.id}"}}
        ],
    }
    with pytest.raises(ReferenceError, match="no subrequest in this bundle"):
        plan_bundle(spec)


def test_the_parent_link_is_declared_once_and_only_once():
    """Two representations of one relationship become two links on the wire.

    On Dataverse a declared ``AccountId`` plus the renderer's bind property would
    put both on the wire with the same meaning.
    """
    spec = {
        "name": "twice",
        "records": [
            {"reference_id": "refA", "type": "Account", "fields": {"Name": "N"}},
            {
                "reference_id": "refB",
                "type": "Contact",
                "fields": {"AccountId": "@{refA.id}"},
                "parent": {"reference": "refA", "field": "AccountId"},
            },
        ],
    }
    with pytest.raises(BundleShapeError, match="Declare the link once"):
        plan_bundle(spec)


def test_a_record_with_no_reference_id_is_refused():
    with pytest.raises(BundleShapeError, match="no reference_id"):
        plan_bundle({"name": "x", "records": [{"type": "Account", "fields": {"Name": "N"}}]})


def test_a_reference_id_that_is_not_an_identifier_is_refused():
    with pytest.raises(BundleShapeError, match="not an identifier"):
        plan_bundle({"name": "x", "records": [{"reference_id": "a b", "type": "A", "fields": {"n": 1}}]})


def test_a_repeated_reference_id_is_refused():
    spec = {
        "name": "twice",
        "records": [
            {"reference_id": "refA", "type": "Account", "fields": {"Name": "N"}},
            {"reference_id": "refA", "type": "Contact", "fields": {"LastName": "O"}},
        ],
    }
    with pytest.raises(BundleShapeError, match="used twice"):
        plan_bundle(spec)


def test_a_record_with_no_type_is_refused():
    with pytest.raises(BundleShapeError, match="no type"):
        plan_bundle({"name": "x", "records": [{"reference_id": "refA", "fields": {"Name": "N"}}]})


def test_a_record_with_no_fields_is_refused():
    with pytest.raises(BundleShapeError, match="no fields"):
        plan_bundle({"name": "x", "records": [{"reference_id": "refA", "type": "Account"}]})


def test_a_bundle_with_no_records_is_refused():
    with pytest.raises(BundleShapeError, match="non-empty list"):
        plan_bundle({"name": "x", "records": []})
    with pytest.raises(BundleShapeError, match="non-empty list"):
        plan_bundle({"name": "x"})


def test_the_fourth_record_type_needs_no_transport_change():
    """The researched extensibility claim, as a test.

    "a deployment can add a 4th record type without touching the transport" - so
    a type this build has never heard of must plan, render and commit, and the
    renderer must be reading the type off the plan rather than off a list of
    names it happens to know.
    """
    spec = {
        "name": "with a fourth type",
        "records": BUNDLE["records"]
        + [
            {
                "reference_id": "refQuote",
                "type": "Quote__c",
                "fields": {"Name": "Q-1"},
                "parent": {"reference": "refOpportunity", "field": "OpportunityId"},
            }
        ],
    }
    plan = plan_bundle(spec)
    document = render(plan)
    assert plan.distinct_types[-1] == "Quote__c"
    assert len(document.parts) == 4
    assert json.loads(document.body)["compositeRequest"][-1]["url"].endswith("/Quote__c")

    # And it commits, through the real transport, with nothing special-cased.
    from dsr.db.audited import AuditedDatabase

    db = AuditedDatabase(":memory:")
    try:
        store = RecordStore(db)
        rows = BundleCommitter(store)
        room = store.create("room", {"name": "R"}, actor="dana", source=SOURCE)
        connector = rows.create_connector(CONNECTOR, actor="dana", source=SOURCE)
        bundle = rows.create_bundle(
            room["id"], spec | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
        )
        run = rows.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
        assert run["ok"] is True
        assert run["counts"]["created"] == 4
    finally:
        db.close()


def test_the_declared_depth_is_the_longest_parent_chain():
    spec = {
        "name": "chain",
        "records": [
            {"reference_id": "a", "type": "A", "fields": {"n": 1}},
            {"reference_id": "b", "type": "B", "fields": {"n": 1}, "parent": {"reference": "a", "field": "a"}},
            {"reference_id": "c", "type": "C", "fields": {"n": 1}, "parent": {"reference": "b", "field": "b"}},
        ],
    }
    assert plan_bundle(spec, dialect="salesforce_sobject_tree").depth == 3


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


def test_twenty_five_subrequests_is_the_composite_ceiling():
    spec = {
        "name": "twenty six",
        "records": [
            {"reference_id": f"ref{i:02d}", "type": "Account", "fields": {"Name": f"N{i}"}}
            for i in range(26)
        ],
    }
    with pytest.raises(LimitExceeded) as excinfo:
        plan_bundle(spec)
    assert "26 subrequests" in str(excinfo.value)
    assert "up to 25 subrequests" in str(excinfo.value)


def test_exactly_twenty_five_subrequests_is_allowed():
    spec = {
        "name": "twenty five",
        "records": [
            {"reference_id": f"ref{i:02d}", "type": "Account", "fields": {"Name": f"N{i}"}}
            for i in range(25)
        ],
    }
    assert plan_bundle(spec).subrequests == 25


def test_five_sobject_collections_is_the_ceiling():
    def collection(index):
        return {
            "reference_id": f"coll{index}",
            "type": "Account",
            "collection": {"field": "Name", "records": [{"Name": f"N{index}"}]},
        }

    spec = {"name": "six collections", "records": [collection(i) for i in range(6)]}
    with pytest.raises(LimitExceeded, match="sObject Collections"):
        plan_bundle(spec)
    assert plan_bundle({"name": "five", "records": [collection(i) for i in range(5)]}).collections == 5


def test_a_collection_counts_as_one_subrequest_and_many_records():
    spec = {
        "name": "one collection",
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "collection": {"field": "Name", "records": [{"Name": "A"}, {"Name": "B"}]},
            }
        ],
    }
    plan = plan_bundle(spec)
    assert plan.subrequests == 1
    assert plan.record_count == 2


def test_a_collection_reference_must_be_indexed_because_the_result_is_a_list():
    """The researched ``@{AccountInfo.recentItems[0].Id}`` form, used for a reason.

    A collection's result is a list, so the field map the plan publishes for it is
    indexed: ``@{refAccounts.Name}`` would resolve to nothing.
    """
    spec = {
        "name": "collection then child",
        "records": [
            {
                "reference_id": "refAccounts",
                "type": "Account",
                "collection": {"field": "Name", "records": [{"Name": "A"}, {"Name": "B"}]},
            },
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"LastName": "O", "Mirrored__c": "@{refAccounts.Name[0]}"},
                "parent": {"reference": "refAccounts", "field": "AccountId"},
            },
        ],
    }
    plan = plan_bundle(spec)
    assert plan.step("refAccounts").fields["Name"] == "@{refAccounts.Name[0]}"
    # And a later subrequest can actually reference it.
    assert sf_references(plan.step("refContact").fields) == [("refAccounts", "Name[0]")]
    envelope = json.loads(render(plan).body)
    assert envelope["compositeRequest"][0]["body"]["records"] == [{"Name": "A"}, {"Name": "B"}]


def test_two_hundred_records_is_the_tree_ceiling():
    # 201 single-record steps of 201 *distinct* types would trip the five-types
    # rule first, so the records are one step per type with a collection of 201
    # records - except the tree refuses a collection. So: 41 types x 5 records
    # is not allowed either. The honest shape is 201 records across 5 types, which
    # a tree cannot express without collections - so the record ceiling is
    # exercised through a bundle whose *steps* exceed it, which is what a
    # deployment would actually write.
    spec = {
        "name": "201 records",
        "records": [
            {"reference_id": f"t{index}", "type": "Custom__c", "fields": {"n": index}}
            for index in range(201)
        ],
    }
    with pytest.raises(LimitExceeded) as excinfo:
        plan_bundle(spec, dialect="salesforce_sobject_tree")
    assert "records across all trees" in str(excinfo.value)


def test_five_record_types_is_the_tree_ceiling():
    spec = {
        "name": "six types",
        "records": [
            {"reference_id": f"r{index}", "type": f"Type{index}", "fields": {"n": 1}}
            for index in range(6)
        ],
    }
    with pytest.raises(LimitExceeded, match="records of different types"):
        plan_bundle(spec, dialect="salesforce_sobject_tree")
    assert plan_bundle(spec, dialect="salesforce_composite").subrequests == 6


def test_five_levels_deep_is_the_tree_ceiling():
    def chain(length):
        return {
            "name": f"chain {length}",
            "records": [
                {
                    "reference_id": f"l{index}",
                    # One type throughout, so this is about depth and not the
                    # five-types rule - which is checked on its own.
                    "type": "Custom__c",
                    "fields": {"n": index},
                    **({"parent": {"reference": f"l{index - 1}", "field": "parent"}} if index else {}),
                }
                for index in range(length)
            ],
        }

    assert plan_bundle(chain(5), dialect="salesforce_sobject_tree").depth == 5
    with pytest.raises(LimitExceeded, match="levels of tree nesting"):
        plan_bundle(chain(6), dialect="salesforce_sobject_tree")


def test_a_collection_is_refused_on_the_tree_dialect_with_a_reason():
    spec = {
        "name": "collection on a tree",
        "records": [
            {
                "reference_id": "refAccounts",
                "type": "Account",
                "collection": {"field": "Name", "records": [{"Name": "A"}]},
            }
        ],
    }
    with pytest.raises(BundleShapeError, match="nested tree, not an sObject Collection"):
        plan_bundle(spec, dialect="salesforce_sobject_tree")


def test_a_thousand_batch_requests_is_the_dataverse_ceiling():
    spec = {
        "name": "1001",
        "records": [
            {"reference_id": f"r{index}", "type": "Account", "fields": {"name": f"N{index}"}}
            for index in range(1001)
        ],
    }
    with pytest.raises(LimitExceeded, match="individual requests in a \\$batch"):
        plan_bundle(spec, dialect="dataverse_batch")


# --------------------------------------------------------------------------- #
# Rollback policy
# --------------------------------------------------------------------------- #


def test_strict_maps_to_all_or_none_true_and_partial_to_false():
    strict = render(plan_bundle(BUNDLE, policy=POLICY_STRICT))  # composite
    partial = render(plan_bundle(BUNDLE, policy=POLICY_PARTIAL))  # composite
    assert json.loads(strict.body)["allOrNone"] is True
    assert json.loads(partial.body)["allOrNone"] is False


def test_a_partial_policy_is_refused_on_a_dialect_with_no_partial_mode():
    """"If an error occurs while creating a record, the entire request fails."

    There is no partial mode to ask for on the tree endpoint, and silently
    downgrading to "whatever the endpoint does" would make the run record lie.
    """
    with pytest.raises(PolicyError) as excinfo:
        plan_bundle(BUNDLE, dialect="salesforce_sobject_tree", policy=POLICY_PARTIAL)
    assert "entire request fails" in str(excinfo.value)


def test_a_partial_policy_is_refused_on_a_dataverse_changeset_with_the_quote():
    with pytest.raises(PolicyError) as excinfo:
        plan_bundle(BUNDLE, dialect="dataverse_batch", policy=POLICY_PARTIAL)
    assert "change set" in str(excinfo.value)
    assert "WF-040" in str(excinfo.value)


def test_an_unknown_policy_and_an_unknown_dialect_are_both_refused():
    with pytest.raises(PolicyError, match="unknown rollback policy"):
        plan_bundle(BUNDLE, policy="eventually")
    with pytest.raises(BundleShapeError, match="unknown dialect"):
        plan_bundle(BUNDLE, dialect="sap")


# --------------------------------------------------------------------------- #
# The composite rendering
# --------------------------------------------------------------------------- #


def test_the_composite_envelope_and_subrequest_keys_are_exactly_the_researched_ones():
    _plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    envelope = json.loads(document.body)
    assert set(envelope) == {"allOrNone", "collateSubrequests", "compositeRequest"}
    for subrequest in envelope["compositeRequest"]:
        assert set(subrequest) == {"method", "url", "referenceId", "body"}


def test_the_composite_reference_placeholder_is_in_the_subrequest_body():
    """The researched data flow: the body carries the placeholder, the CRM resolves it."""
    _plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    envelope = json.loads(document.body)
    contact = next(s for s in envelope["compositeRequest"] if s["referenceId"] == "refContact")
    assert contact["body"]["AccountId"] == "@{refAccount.id}"


def test_the_composite_subrequest_order_is_the_declared_order():
    _plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    envelope = json.loads(document.body)
    assert [s["referenceId"] for s in envelope["compositeRequest"]] == [
        "refAccount",
        "refContact",
        "refOpportunity",
    ]


def test_the_api_version_is_placeholder_until_a_connector_pins_one():
    _plan, bare = rendered(BUNDLE, dialect="salesforce_composite")
    assert "/services/data/vXX.X/" in bare.path
    _plan, pinned = rendered(
        BUNDLE, dialect="salesforce_composite", options=RenderOptions(api_version="v61.0")
    )
    assert "/services/data/v61.0/" in pinned.path
    assert "/services/data/v61.0/sobjects/Account" in pinned.body


def test_the_composite_collation_flag_is_the_declared_knob():
    _plan, on = rendered(BUNDLE, dialect="salesforce_composite", collate_subrequests=True)
    _plan, off = rendered(BUNDLE, dialect="salesforce_composite", collate_subrequests=False)
    assert json.loads(on.body)["collateSubrequests"] is True
    assert json.loads(off.body)["collateSubrequests"] is False


# --------------------------------------------------------------------------- #
# The sObject tree rendering
# --------------------------------------------------------------------------- #


def test_the_tree_nests_a_child_under_its_parent_type():
    _plan, document = rendered(BUNDLE, dialect="salesforce_sobject_tree")
    root = json.loads(document.body)["records"][0]
    assert root["attributes"] == {"type": "Account", "referenceId": "refAccount"}
    assert [r["attributes"]["referenceId"] for r in root["Contact"]["records"]] == ["refContact"]
    assert [r["attributes"]["referenceId"] for r in root["Opportunity"]["records"]] == [
        "refOpportunity"
    ]


def test_the_tree_does_not_write_the_parent_id_into_the_child():
    """A tree links by nesting, so an id in the child would be a second mechanism."""
    _plan, document = rendered(BUNDLE, dialect="salesforce_sobject_tree")
    root = json.loads(document.body)["records"][0]
    assert "AccountId" not in root["Contact"]["records"][0]


def test_the_tree_path_names_the_root_type():
    _plan, document = rendered(BUNDLE, dialect="salesforce_sobject_tree")
    assert document.path.endswith("/composite/tree/Account")


def test_the_tree_says_its_body_is_not_a_quoted_shape():
    _plan, document = rendered(BUNDLE, dialect="salesforce_sobject_tree")
    assert any("not a request body" in note for note in document.notes)


# --------------------------------------------------------------------------- #
# The Dataverse rendering
# --------------------------------------------------------------------------- #


def test_the_dataverse_body_is_multipart_mixed_with_a_changeset_boundary():
    _plan, document = rendered(BUNDLE, dialect="dataverse_batch")
    assert document.headers["Content-Type"] == "multipart/mixed"
    assert document.body.startswith("--changeset_")
    assert document.body.rstrip().endswith("--")


def test_the_dataverse_parts_are_numbered_one_two_three():
    _plan, document = rendered(BUNDLE, dialect="dataverse_batch")
    assert [part.headers["Content-ID"] for part in document.parts] == ["1", "2", "3"]
    assert document.content_id_of == {
        "refAccount": 1,
        "refContact": 2,
        "refOpportunity": 3,
    }


def test_the_dataverse_parent_link_is_the_researched_bind_property_with_a_dollar_reference():
    """"originatingleadid@odata.bind": "$1" - the research quotes this example."""
    _plan, document = rendered(BUNDLE, dialect="dataverse_batch")
    contact = next(p for p in document.parts if p.reference_id == "refContact")
    assert json.loads(contact.body)["AccountId@odata.bind"] == "$1"


def test_the_dataverse_bind_property_is_named_after_the_declared_parent_field():
    spec = {
        "name": "dataverse",
        "records": [
            {"reference_id": "refAccount", "type": "Account", "fields": {"name": "Litware"}},
            {
                "reference_id": "refContact",
                "type": "Contact",
                "fields": {"lastname": "Nguyen"},
                "parent": {"reference": "refAccount", "field": "parentaccountid"},
            },
        ],
    }
    _plan, document = rendered(spec, dialect="dataverse_batch")
    contact = next(p for p in document.parts if p.reference_id == "refContact")
    assert json.loads(contact.body)["parentaccountid@odata.bind"] == "$1"


def test_the_dataverse_entity_set_is_a_lower_cased_plural_and_can_be_overridden():
    _plan, bare = rendered(BUNDLE, dialect="dataverse_batch")
    assert "api/data/v9.2/accounts" in bare.body
    _plan, renamed = rendered(
        BUNDLE,
        dialect="dataverse_batch",
        options=RenderOptions(entity_sets={"Account": "contoso_accounts"}),
    )
    assert "api/data/v9.2/contoso_accounts" in renamed.body


def test_the_dataverse_boundary_is_derived_so_two_renders_are_byte_identical():
    first = render(plan_bundle(BUNDLE, dialect="dataverse_batch"))
    second = render(plan_bundle(BUNDLE, dialect="dataverse_batch"))
    assert first.body == second.body


def test_the_dataverse_body_is_reported_as_text_because_it_is_not_json():
    _plan, document = rendered(BUNDLE, dialect="dataverse_batch")
    payload = document.to_dict()
    assert payload["body"] is None
    assert payload["raw_body"].startswith("--changeset_")
    assert "atomic" in payload["notes"][0]


# --------------------------------------------------------------------------- #
# The HubSpot rendering
# --------------------------------------------------------------------------- #


def test_hubspot_renders_a_sequence_and_says_it_is_not_atomic():
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    assert document.is_sequence
    assert document.to_dict()["request_count"] == len(document.parts)
    assert not document.atomic
    assert ATOMICITY["hubspot_associations"]["atomic"] is False
    assert any("no transaction that spans them" in note for note in document.notes)


def test_hubspot_uses_the_documented_batch_create_and_association_put():
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    kinds = {part.kind for part in document.parts}
    assert kinds == {"batch-create", "create", "association"}
    assert document.parts[0].path == HS_CONTACTS_BATCH_PATH
    association = next(p for p in document.parts if p.kind == "association")
    assert association.method == "PUT"
    assert association.path.startswith("/crm/objects/2026-09/")


def test_a_contact_is_created_by_the_batch_not_also_by_its_own_request():
    """"with an associations array, **or** PUT" - the research's or, read per record.

    Emitting both for one contact would associate it twice.
    """
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    creates = [p for p in document.parts if p.kind == "create"]
    assert "refContact" not in {p.reference_id for p in creates}
    inputs = json.loads(document.parts[0].body)["inputs"]
    assert [entry["id"] for entry in inputs] == ["refContact"]


def test_the_batch_carries_the_researched_associations_array():
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    entry = json.loads(document.parts[0].body)["inputs"][0]
    assert entry["associations"][0]["to"]["id"] == "@{refAccount.id}"
    assert entry["associations"][0]["types"][0]["associationCategory"] == "HUBSPOT_DEFINED"


def test_the_hubspot_object_type_comes_from_the_record_and_can_be_declared():
    spec = {
        "name": "typed",
        "records": [
            {
                "reference_id": "refAccount",
                "type": "Account",
                "fields": {"name": "Wide World", "object_type_id": "2"},
            },
            {
                "reference_id": "refOpp",
                "type": "Opportunity",
                "fields": {"dealname": "APAC"},
                "parent": {"reference": "refAccount", "field": "3"},
            },
        ],
    }
    _plan, document = rendered(spec, dialect="hubspot_associations")
    create = next(p for p in document.parts if p.kind == "create")
    assert create.path == "/crm/v3/objects/2"
    # object_type_id addresses the URL; it is not a CRM property.
    assert "object_type_id" not in json.loads(create.body)["properties"]


def test_the_default_hubspot_object_type_is_the_lower_cased_record_type():
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    creates = {p.reference_id: p.path for p in document.parts if p.kind == "create"}
    assert creates["refAccount"] == "/crm/v3/objects/account"


# --------------------------------------------------------------------------- #
# Committing through the in-process CRM
# --------------------------------------------------------------------------- #


def test_a_clean_commit_creates_one_row_per_subrequest(store):
    plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    result = crm(store).send(document, source=SOURCE)
    assert result.ok
    assert result.committed == ("refAccount", "refContact", "refOpportunity")
    assert result.summary() == {
        OUTCOME_CREATED: 3,
        OUTCOME_ROLLED_BACK: 0,
        OUTCOME_SKIPPED: 0,
        OUTCOME_FAILED: 0,
    }
    assert count(store, TARGET_COLLECTION) == 3


def test_the_opportunity_lands_on_the_account_this_run_just_created(store):
    """"so the Opportunity is created against the just-created Account rather than a
    stale one" - step 5 of the researched flow."""
    plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    result = crm(store).send(document, source=SOURCE)
    account_id = next(s.record_id for s in result.steps if s.reference_id == "refAccount")
    opportunity = next(s for s in result.steps if s.reference_id == "refOpportunity")
    assert opportunity.resolved["AccountId"] == account_id
    assert opportunity.sent["AccountId"] == "@{refAccount.id}"


def test_a_strict_failure_rolls_back_every_row_the_run_created(store):
    """[sourced] "the entire composite request is rolled back"."""
    plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_STRICT)
    result = crm(store, {"refContact": FAILURE}).send(document, source=SOURCE)
    assert not result.ok
    assert set(result.rolled_back) == {"refAccount", "refOpportunity"}
    assert count(store, TARGET_COLLECTION) == 0
    outcomes = {s.reference_id: s.outcome for s in result.steps}
    assert outcomes == {
        "refAccount": OUTCOME_ROLLED_BACK,
        "refContact": OUTCOME_FAILED,
        "refOpportunity": OUTCOME_ROLLED_BACK,
    }


def test_a_rolled_back_row_still_says_it_existed(store):
    """The ids were captured before the rollback, and that is the fact a rep needs."""
    plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_STRICT)
    result = crm(store, {"refContact": FAILURE}).send(document, source=SOURCE)
    account = next(s for s in result.steps if s.reference_id == "refAccount")
    assert account.record_id
    assert account.reason == "all_or_none"
    assert "already succeeded" in account.message


def test_a_partial_failure_keeps_the_subrequests_that_do_not_depend_on_it(store):
    """[sourced] "the remaining subrequests that don't depend on the failed
    subrequest are executed"."""
    plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_PARTIAL)
    result = crm(store, {"refContact": FAILURE}).send(document, source=SOURCE)
    assert not result.ok
    assert result.committed == ("refAccount", "refOpportunity")
    assert result.rolled_back == ()
    assert count(store, TARGET_COLLECTION) == 2


def test_a_partial_failure_on_the_root_skips_rather_than_fails_its_dependents(store):
    """[sourced] "Dependent subrequests aren't executed."

    Skipped, not failed: nothing went wrong with the children, their input never
    arrived. A run that counted them as failures would report three problems for
    one mistake.
    """
    plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_PARTIAL)
    result = crm(store, {"refAccount": "INSUFFICIENT_ACCESS"}).send(document, source=SOURCE)
    outcomes = {s.reference_id: s.outcome for s in result.steps}
    assert outcomes == {
        "refAccount": OUTCOME_FAILED,
        "refContact": OUTCOME_SKIPPED,
        "refOpportunity": OUTCOME_SKIPPED,
    }
    assert set(result.skipped) == {"refContact", "refOpportunity"}
    assert "Dependent subrequests aren't executed" in next(
        s for s in result.steps if s.reference_id == "refContact"
    ).message


def test_the_single_actionable_error_is_the_first_failure_in_declared_order(store):
    """[sourced] "the room shows a single actionable error" - one, not three."""
    plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_STRICT)
    result = crm(
        store, {"refContact": FAILURE, "refOpportunity": "DUPLICATE_VALUE"}
    ).send(document, source=SOURCE)
    assert result.actionable_error["reference_id"] == "refContact"
    assert result.actionable_error["message"] == FAILURE
    assert "2 of 3 subrequests failed" in result.actionable_error["detail"]


def test_the_actionable_error_outranks_a_plain_refusal_when_it_is_a_setting(store):
    """A collation failure's fix is a setting, and getting that wrong fails again."""
    spec = {
        "name": "collation",
        "records": [
            {"reference_id": "refPrior", "type": "Audit__c", "fields": {"n": 1}},
            {"reference_id": "refAccount", "type": "Account", "fields": {"Name": "P"}},
            {"reference_id": "refAudit", "type": "Audit__c", "fields": {"n": 2},
             "implicit_depends_on": ["refAccount"]},
        ],
    }
    plan, document = rendered(spec, dialect="salesforce_composite", collate_subrequests=True)
    result = crm(store, {"refAccount": "DUPLICATE_VALUE"}).send(document, source=SOURCE)
    assert result.actionable_error["reason"] == FAIL_COLLATION_VIOLATION
    assert "collateSubrequests to false" in result.actionable_error["message"]


def test_a_clean_run_has_no_actionable_error(store):
    _plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    assert crm(store).send(document, source=SOURCE).actionable_error is None


# --------------------------------------------------------------------------- #
# Collation: the researched ordering knob
# --------------------------------------------------------------------------- #

IMPLICIT_SPEC: dict = {
    "name": "implicit dependency",
    "records": [
        {"reference_id": "refPriorAudit", "type": "AccountAudit__c", "fields": {"Reason__c": "prior"}},
        {"reference_id": "refAccount", "type": "Account", "fields": {"Name": "Proseware"}},
        {
            "reference_id": "refAudit",
            "type": "AccountAudit__c",
            "fields": {"Reason__c": "triggered"},
            "implicit_depends_on": ["refAccount"],
        },
    ],
}


def test_the_preview_warns_about_an_implicit_dependency_while_collation_is_on():
    plan = plan_bundle(IMPLICIT_SPEC, dialect="salesforce_composite", collate_subrequests=True)
    codes = {entry["code"] for entry in plan.warnings}
    assert WARN_IMPLICIT_DEPENDENCY in codes
    assert any("collateSubrequests to false" in entry["message"] for entry in plan.warnings)


def test_the_warning_names_the_steps_it_is_about():
    plan = plan_bundle(IMPLICIT_SPEC, dialect="salesforce_composite", collate_subrequests=True)
    warning = next(e for e in plan.warnings if e["code"] == WARN_IMPLICIT_DEPENDENCY)
    assert warning["steps"] == "refAudit"


def test_no_warning_about_an_implicit_dependency_once_collation_is_off():
    plan = plan_bundle(IMPLICIT_SPEC, dialect="salesforce_composite", collate_subrequests=False)
    assert WARN_IMPLICIT_DEPENDENCY not in {entry["code"] for entry in plan.warnings}


def test_a_collation_violation_fails_while_collation_is_on(store):
    """[sourced] "Collation can cause issues if there are implicit but not explicit
    dependencies between items." Modelled, not described."""
    _plan, document = rendered(IMPLICIT_SPEC, dialect="salesforce_composite", collate_subrequests=True)
    result = crm(store).send(document, source=SOURCE)
    audit = next(s for s in result.steps if s.reference_id == "refAudit")
    assert audit.outcome == OUTCOME_FAILED
    assert audit.reason == FAIL_COLLATION_VIOLATION
    assert not result.ok


def test_the_same_bundle_commits_once_the_knob_is_turned(store):
    """The whole point of the toggle: one setting, and the failure is gone."""
    _plan, document = rendered(IMPLICIT_SPEC, dialect="salesforce_composite", collate_subrequests=False)
    result = crm(store).send(document, source=SOURCE)
    assert result.ok
    assert result.committed == ("refPriorAudit", "refAccount", "refAudit")


def test_collation_groups_by_type_so_an_explicit_dependency_can_be_pulled_forward(store):
    spec = {
        "name": "pulled forward",
        "records": [
            {"reference_id": "refTag", "type": "Tag__c", "fields": {"n": 1}},
            {"reference_id": "refAccount", "type": "Account", "fields": {"Name": "A"}},
            {
                "reference_id": "refSecondTag",
                "type": "Tag__c",
                "fields": {"n": 2},
                "parent": {"reference": "refAccount", "field": "AccountId"},
            },
        ],
    }
    _plan, document = rendered(spec, dialect="salesforce_composite", collate_subrequests=True)
    result = crm(store).send(document, source=SOURCE)
    second = next(s for s in result.steps if s.reference_id == "refSecondTag")
    assert second.reason == FAIL_COLLATION_VIOLATION
    assert "collateSubrequests to false" in second.message


def test_collation_on_a_dialect_with_no_flag_warns_and_never_fails(store):
    _plan, document = rendered(IMPLICIT_SPEC, dialect="dataverse_batch", collate_subrequests=True)
    result = crm(store).send(document, source=SOURCE)
    assert result.ok
    codes = {entry["code"] for entry in document.plan.warnings}
    assert WARN_COLLATION_NOT_APPLICABLE in codes


def test_the_tree_dialect_says_the_order_is_structural():
    plan = plan_bundle(BUNDLE, dialect="salesforce_sobject_tree", collate_subrequests=True)
    assert WARN_TREE_HAS_NO_ORDERING_FLAG in {entry["code"] for entry in plan.warnings}


# --------------------------------------------------------------------------- #
# Sequence dialects: HubSpot's compensation
# --------------------------------------------------------------------------- #


def test_a_sequence_commit_creates_every_step_and_links_the_child(store):
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations")
    result = crm(store).send(document, source=SOURCE)
    assert result.ok
    assert result.committed == ("refAccount", "refContact", "refOpportunity")
    linked = [row["data"] for row in store.list(TARGET_COLLECTION)]
    assert any(row.get("linked_to") for row in linked)
    assert not result.atomic


def test_a_sequence_strict_failure_compensates_by_deleting(store):
    """Every row the run created goes, including the one whose link was refused.

    The Opportunity's row exists and is unlinked, so undoing it is right - and it
    is still reported as a failure, because a compensation is not a success.
    """
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations", policy=POLICY_STRICT)
    result = crm(store, {"refOpportunity-association": "VALIDATION_ERROR"}).send(
        document, source=SOURCE
    )
    assert not result.ok
    assert result.compensated
    assert set(result.rolled_back) == {"refAccount", "refContact", "refOpportunity"}
    assert result.failed == ("refOpportunity",)
    assert count(store, TARGET_COLLECTION) == 0


def test_a_refused_compensation_leaves_the_row_and_says_so(store):
    """A compensation is not a rollback, and the case where it fails is the one
    where the rows really are still there."""
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations", policy=POLICY_STRICT)
    result = crm(
        store,
        {"refOpportunity-association": "VALIDATION_ERROR", "refAccount-delete": "LOCKED"},
    ).send(document, source=SOURCE)
    assert list(result.compensation_failures) == ["refAccount"]
    assert "refAccount" not in result.rolled_back
    remaining = {row["data"]["reference_id"] for row in store.list(TARGET_COLLECTION)}
    assert remaining == {"refAccount"}
    account = next(s for s in result.steps if s.reference_id == "refAccount")
    assert account.reason == "compensation_refused"
    assert any("compensation is not a rollback" in note for note in result.notes)


def test_a_compensated_run_still_reports_the_original_failure(store):
    """After the undo the step says "rolled back"; the error the room sees must not."""
    _plan, document = rendered(BUNDLE, dialect="hubspot_associations", policy=POLICY_STRICT)
    result = crm(store, {"refOpportunity-association": "VALIDATION_ERROR"}).send(
        document, source=SOURCE
    )
    assert not result.ok
    assert result.actionable_error["message"] == "VALIDATION_ERROR"
    assert result.actionable_error["reference_id"] == "refOpportunity"


def test_a_rolled_back_failure_is_never_reported_as_success(store):
    """A failure that an undo then hid would be the one answer nobody can act on."""
    _plan, document = rendered(BUNDLE, dialect="salesforce_composite", policy=POLICY_STRICT)
    result = crm(store, {"refContact": FAILURE}).send(document, source=SOURCE)
    assert not result.ok
    assert result.failed == ("refContact",)


def test_a_hubspot_strict_bundle_warns_that_it_is_not_a_transaction():
    plan = plan_bundle(BUNDLE, dialect="hubspot_associations", policy=POLICY_STRICT)
    assert WARN_COMPENSATION_NOT_TRANSACTION in {e["code"] for e in plan.warnings}


# --------------------------------------------------------------------------- #
# The real transport
# --------------------------------------------------------------------------- #


def test_the_real_transport_says_it_does_not_parse_a_vendor_response():
    """The research quotes no composite response body, so this build does not
    invent a parser for it - the raw bytes are stored instead."""
    from dsr.atomic_bundle.transport import UrllibTransport

    _plan, document = rendered(BUNDLE, dialect="salesforce_composite")
    result = UrllibTransport(base_url="http://127.0.0.1:1").send(document, source=SOURCE)
    assert result.steps == ()
    assert RESPONSE_NOT_PARSED_NOTE in result.notes
    assert not result.ok


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_a_room_is_required_before_anything_else(committer):
    with pytest.raises(NotFound) as excinfo:
        committer.list_bundles("room_nope")
    assert excinfo.value.resource == "room"
    assert "room_nope" in str(excinfo.value)


def test_a_connector_read_never_returns_the_token(committer):
    created = committer.create_connector(
        CONNECTOR | {"token": "00Dxx-super-secret-token"},
        actor="dana",
        source=SOURCE,
    )
    assert "token" not in created
    assert created["has_token"] is True
    assert created["token_hint"].endswith("oken")
    assert "super-secret" not in json.dumps(created)
    assert "super-secret" not in json.dumps(committer.list_connectors())


def test_a_connector_refuses_an_unknown_dialect_policy_or_transport(committer):
    for payload, message in (
        ({"name": "x", "dialect": "sap"}, "unknown dialect"),
        ({"name": "x", "policy": "eventually"}, "unknown rollback policy"),
        ({"name": "x", "transport": "carrier-pigeon"}, "unknown transport"),
    ):
        with pytest.raises(BundleShapeError, match=message):
            committer.create_connector(payload, actor="dana", source=SOURCE)
    assert set(TRANSPORTS) == {"local", "urllib"}


def test_a_connector_with_no_name_is_refused(committer):
    with pytest.raises(BundleShapeError, match="name is required"):
        committer.create_connector({"dialect": "salesforce_composite"}, actor="dana", source=SOURCE)


def test_declaring_an_unplannable_bundle_writes_nothing(store, committer, room, connector):
    """A declaration that could never be sent is a bad declaration, not a record."""
    with pytest.raises(BundleShapeError):
        committer.create_bundle(
            room["id"],
            {"name": "x", "records": [{"reference_id": "refA"}], "connector_id": connector["id"]},
            actor="dana",
            source=SOURCE,
        )
    assert count(store, BUNDLE_COLLECTION) == 0


def test_a_bundle_belongs_to_one_room(committer, room, connector, store):
    other = store.create("room", {"name": "Other"}, actor="dana")
    bundle = committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )
    with pytest.raises(NotFound) as excinfo:
        committer.get_bundle(other["id"], bundle["id"])
    assert excinfo.value.resource == "bundle"


def test_a_bundle_list_is_filterable_by_dialect_and_policy(committer, room, connector):
    committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )
    assert len(committer.list_bundles(room["id"])) == 1
    assert committer.list_bundles(room["id"], dialect="salesforce_composite")
    assert not committer.list_bundles(room["id"], dialect="dataverse_batch")
    assert not committer.list_bundles(room["id"], policy=POLICY_PARTIAL)


def test_the_preview_writes_nothing_and_shows_the_subrequest_order(
    store, committer, room, connector, bundle
):
    before = store.stats()["audit_entries"]
    preview = committer.preview(room["id"], bundle["id"])
    assert store.stats()["audit_entries"] == before
    assert preview["plan"]["order"] == ["refAccount", "refContact", "refOpportunity"]
    assert preview["request"]["body"]["compositeRequest"][0]["referenceId"] == "refAccount"
    assert preview["ready"] is True
    assert preview["blockers"] == []


def test_the_preview_carries_the_six_step_researched_flow(committer, room, bundle):
    flow = committer.preview(room["id"], bundle["id"])["user_flow"]
    assert [entry["step"] for entry in flow] == ["1", "2", "3", "4", "5", "6"]
    assert "single actionable error" in flow[5]["text"]


def test_the_preview_names_the_inferences_this_bundles_choices_rest_on(committer, room, bundle):
    ids = committer.preview(room["id"], bundle["id"])["inferences"]
    assert "bundle-record-shape" in ids
    assert "default-policy" in ids
    assert "actionable-error-is-one-entry" in ids


def test_the_preview_reports_where_each_choice_came_from(committer, room, bundle):
    choices = committer.preview(room["id"], bundle["id"])["choices"]
    assert choices["policy"] == POLICY_STRICT
    assert choices["source_of"]["policy"] == "connector"
    assert choices["source_of"]["collate_subrequests"] == "connector"


def test_a_request_setting_wins_over_the_bundle_and_the_connector(committer, room, bundle):
    choices = committer.preview(room["id"], bundle["id"], policy=POLICY_PARTIAL)["choices"]
    assert choices["policy"] == POLICY_PARTIAL
    assert choices["source_of"]["policy"] == "request"


def test_the_preview_says_a_non_atomic_dialect_is_a_blocker(committer, room, connector, store):
    bundle = committer.create_bundle(
        room["id"],
        BUNDLE | {"dialect": "hubspot_associations", "connector_id": connector["id"]},
        actor="dana",
        source=SOURCE,
    )
    preview = committer.preview(room["id"], bundle["id"])
    assert preview["ready"] is False
    assert "not_atomic" in {entry["code"] for entry in preview["blockers"]}


def test_a_connector_with_no_base_url_blocks_a_urllib_commit_not_a_preview(
    committer, room, store
):
    from dsr.atomic_bundle.errors import BundleNotConfigured

    connector = committer.create_connector(
        CONNECTOR | {"transport": "urllib"}, actor="dana", source=SOURCE
    )
    bundle = committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )
    preview = committer.preview(room["id"], bundle["id"])
    assert "no_base_url" in {entry["code"] for entry in preview["blockers"]}
    with pytest.raises(BundleNotConfigured, match="no base_url"):
        committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)


def test_a_commit_with_no_connector_at_all_is_not_configured(committer, room):
    from dsr.atomic_bundle.errors import BundleNotConfigured

    bundle = committer.create_bundle(room["id"], BUNDLE, actor="dana", source=SOURCE)
    with pytest.raises(BundleNotConfigured, match="no CRM connector"):
        committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)


def test_a_commit_writes_exactly_one_run_record(store, committer, room, bundle):
    committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
    assert count(store, RUN_COLLECTION) == 1


def test_a_failed_commit_is_still_a_run_record(store, committer, room, bundle):
    """The failure is the thing a rep has to read, so it cannot be a gap."""
    committer.commit(
        room["id"], bundle["id"], actor="dana", source=SOURCE, faults={"refContact": FAILURE}
    )
    runs = committer.runs(room["id"])
    assert len(runs) == 1
    assert runs[0]["ok"] is False
    assert runs[0]["actionable_error"]["message"] == FAILURE


def test_a_refused_commit_writes_no_run_record(store, committer, room, store_bundle=None):
    connector = committer.create_connector(CONNECTOR, actor="dana", source=SOURCE)
    bundle = committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )
    with pytest.raises(BundleShapeError):
        committer.commit(
            room["id"], bundle["id"], actor="dana", source=SOURCE, faults={}, dialect="sap"
        )
    assert count(store, RUN_COLLECTION) == 0


def test_a_run_is_filterable_by_bundle_and_outcome(committer, room, connector):
    first = committer.create_bundle(
        room["id"], BUNDLE | {"connector_id": connector["id"]}, actor="dana", source=SOURCE
    )
    second = committer.create_bundle(
        room["id"],
        BUNDLE | {"name": "another", "connector_id": connector["id"]},
        actor="dana",
        source=SOURCE,
    )
    committer.commit(room["id"], first["id"], actor="dana", source=SOURCE)
    committer.commit(
        room["id"], second["id"], actor="dana", source=SOURCE, faults={"refContact": FAILURE}
    )
    assert len(committer.runs(room["id"])) == 2
    assert len(committer.runs(room["id"], ok=True)) == 1
    assert len(committer.runs(room["id"], ok=False)) == 1
    assert len(committer.runs(room["id"], bundle_id=first["id"])) == 1


def test_a_run_records_the_request_that_was_sent(committer, room, bundle):
    run = committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
    stored = committer.run(room["id"], run["id"])
    assert stored["request"]["body"]["allOrNone"] is True
    assert stored["request"]["path"].endswith("/composite")
    assert stored["steps"][0]["record_id"]


def test_a_run_cannot_be_read_through_another_room(committer, room, bundle, store):
    other = store.create("room", {"name": "Other"}, actor="dana")
    run = committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
    with pytest.raises(NotFound):
        committer.run(other["id"], run["id"])


def test_the_targets_view_is_what_the_crm_holds(committer, room, bundle, store):
    committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
    rows = committer.targets(room["id"])
    assert {row["data"]["object"] for row in rows} == {"Account", "Contact", "Opportunity"}


def test_a_rollback_is_visible_as_rows_leaving_the_targets(store, committer, room, bundle):
    committer.commit(
        room["id"], bundle["id"], actor="dana", source=SOURCE, faults={"refContact": FAILURE}
    )
    assert committer.targets(room["id"]) == []
    assert count(store, TARGET_COLLECTION) == 0


def test_patching_a_connector_swaps_the_dialect(committer, connector):
    updated = committer.update_connector(
        connector["id"], {"dialect": "dataverse_batch"}, actor="dana", source=SOURCE
    )
    assert updated["dialect"] == "dataverse_batch"


def test_a_deleted_connector_is_soft_so_its_runs_stay_readable(store, committer, room, bundle, connector):
    """A hard delete would leave a run pointing at a connector nobody can read."""
    run = committer.commit(room["id"], bundle["id"], actor="dana", source=SOURCE)
    committer.delete_connector(connector["id"], actor="dana", source=SOURCE)
    assert committer.list_connectors() == []
    with pytest.raises(NotFound):
        committer.get_connector(connector["id"])
    # The run still names it, and the request it sent is still on the run.
    assert committer.run(room["id"], run["id"])["connector_id"] == connector["id"]
    assert committer.run(room["id"], run["id"])["request"]["path"].endswith("/composite")
    assert store.audit(collection=CONNECTOR_COLLECTION, action="delete")


def test_patching_a_bundle_to_something_unplannable_writes_nothing(store, committer, room, bundle):
    with pytest.raises(ReferenceError):
        committer.update_bundle(
            room["id"],
            bundle["id"],
            # A dependency pointing at a record the bundle does not have.
            {
                "records": [
                    {"reference_id": "a", "type": "Account", "fields": {"n": 1}},
                    {
                        "reference_id": "b",
                        "type": "Account",
                        "fields": {"n": 1},
                        "parent": {"reference": "missing", "field": "a"},
                    },
                ]
            },
            actor="dana",
            source=SOURCE,
        )
    assert count(store, BUNDLE_COLLECTION) == 1
    assert committer.get_bundle(room["id"], bundle["id"])["name"] == BUNDLE["name"]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with the engine built per request."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf039_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json={"name": "Northwind", "account": "N"}).json()


@pytest.fixture()
def http_connector(http):
    return http.post(f"{PREFIX}/connectors", json=CONNECTOR).json()


@pytest.fixture()
def http_bundle(http, http_room, http_connector):
    return http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles",
        json=BUNDLE | {"connector_id": http_connector["id"]},
    ).json()


def test_the_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-039-write-account-contact-opportunity-as-o"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-039"
    assert entry["exception_handlers"] == ["BundleError", "BundleNotConfigured", "NotFound"]
    assert entry["routes"]


def test_the_feature_loads_without_a_failure(http):
    assert http.get("/api/features").json()["failed_count"] == 0


def test_every_room_scoped_path_is_room_scoped(http):
    """The brief's rule, asserted on the mounted routes rather than on a comment."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-039-write-account-contact-opportunity-as-o"
    )
    for route in entry["routes"]:
        path = route["path"]
        for word in ("/bundles", "/runs", "/targets"):
            if word in path:
                assert "/rooms/{room_id}" in path, f"{word} is not room-scoped: {path}"


def test_the_vocabulary_and_inferences_are_served_without_a_store(http):
    assert len(http.get(f"{PREFIX}/vocabulary").json()["dialects"]) == 4
    assert http.get(f"{PREFIX}/inferences").json()["count"] == len(INFERENCES)
    assert http.get(f"{PREFIX}/vocabulary").json()["collections"] == [
        CONNECTOR_COLLECTION,
        BUNDLE_COLLECTION,
        RUN_COLLECTION,
        TARGET_COLLECTION,
    ]


def test_the_connector_crud_over_http(http):
    created = http.post(f"{PREFIX}/connectors", json=CONNECTOR)
    assert created.status_code == 201
    connector_id = created.json()["id"]
    assert http.get(f"{PREFIX}/connectors").json()["count"] == 1
    assert http.get(f"{PREFIX}/connectors/{connector_id}").json()["name"] == CONNECTOR["name"]
    assert (
        http.patch(f"{PREFIX}/connectors/{connector_id}", json={"enabled": False}).json()["enabled"]
        is False
    )
    assert http.delete(f"{PREFIX}/connectors/{connector_id}").status_code == 204
    assert http.get(f"{PREFIX}/connectors/{connector_id}").status_code == 404


def test_a_connector_cannot_be_created_without_a_name(http):
    assert http.post(f"{PREFIX}/connectors", json={}).status_code == 400
    assert http.post(f"{PREFIX}/connectors", json={"name": "x", "dialect": "sap"}).status_code == 400


def test_the_bundle_crud_over_http(http, http_room, http_connector):
    created = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles",
        json=BUNDLE | {"connector_id": http_connector["id"]},
    )
    assert created.status_code == 201
    bundle_id = created.json()["id"]
    room_path = f"{PREFIX}/rooms/{http_room['id']}/bundles"
    assert http.get(room_path).json()["count"] == 1
    assert http.get(f"{room_path}/{bundle_id}").json()["order"][0] == "refAccount"
    assert (
        http.patch(f"{room_path}/{bundle_id}", json={"name": "renamed"}).json()["name"] == "renamed"
    )
    assert http.delete(f"{room_path}/{bundle_id}").status_code == 204
    assert http.get(f"{room_path}/{bundle_id}").status_code == 404


def test_a_bundle_cannot_be_declared_on_a_room_that_does_not_exist(http):
    response = http.post(f"{PREFIX}/rooms/room_nope/bundles", json=BUNDLE)
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["resource"] == "room"


def test_an_unplannable_bundle_is_a_400_with_a_message_naming_the_record(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles",
        json={"name": "x", "records": [{"reference_id": "refA", "type": "Account"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bundle_error"
    assert "no fields" in response.json()["detail"]


def test_the_preview_over_http_writes_nothing_and_returns_the_order(http, http_room, http_bundle):
    before = http.get("/api/stats").json()["records"]
    preview = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles/{http_bundle['id']}/preview", json={}
    )
    assert preview.status_code == 200
    assert preview.json()["plan"]["order"] == ["refAccount", "refContact", "refOpportunity"]
    assert http.get("/api/stats").json()["records"] == before


def test_the_preview_accepts_the_researched_knobs_and_the_body_changes(http, http_room, http_bundle):
    path = f"{PREFIX}/rooms/{http_room['id']}/bundles/{http_bundle['id']}/preview"
    strict = http.post(path, json={"policy": "strict"}).json()
    partial = http.post(path, json={"policy": "partial"}).json()
    assert strict["request"]["body"]["allOrNone"] is True
    assert partial["request"]["body"]["allOrNone"] is False
    on = http.post(path, json={"collate_subrequests": True}).json()
    off = http.post(path, json={"collate_subrequests": False}).json()
    assert on["request"]["body"]["collateSubrequests"] is True
    assert off["request"]["body"]["collateSubrequests"] is False


def test_the_preview_refuses_a_policy_the_dialect_cannot_honour(http, http_room, http_connector):
    bundle = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles",
        json=BUNDLE | {"dialect": "salesforce_sobject_tree", "connector_id": http_connector["id"]},
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/bundles/{bundle['id']}/preview",
        json={"policy": "partial"},
    )
    assert response.status_code == 400
    assert "entire request fails" in response.json()["detail"]


def test_the_commit_over_http_reports_the_outcome_and_the_crm_state(http, http_room, http_bundle):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    run = http.post(f"{room_path}/bundles/{http_bundle['id']}/commit", json={}).json()
    assert run["ok"] is True
    assert run["counts"]["created"] == 3
    targets = http.get(f"{room_path}/targets").json()
    assert targets["by_object"] == {"Account": 1, "Contact": 1, "Opportunity": 1}
    assert http.get(f"{room_path}/runs").json()["summary"]["created"] == 3


def test_a_run_can_be_read_back_in_full(http, http_room, http_bundle):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    run = http.post(f"{room_path}/bundles/{http_bundle['id']}/commit", json={}).json()
    stored = http.get(f"{room_path}/runs/{run['id']}")
    assert stored.status_code == 200
    assert stored.json()["steps"][0]["outcome"] == OUTCOME_CREATED
    assert stored.json()["request"]["body"]["compositeRequest"]


def test_the_runs_list_filters(http, http_room, http_bundle, http_connector):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    other = http.post(
        f"{room_path}/bundles", json=BUNDLE | {"name": "b", "connector_id": http_connector["id"]}
    ).json()
    http.post(f"{room_path}/bundles/{http_bundle['id']}/commit", json={})
    http.post(f"{room_path}/bundles/{other['id']}/commit", json={})
    assert http.get(f"{room_path}/runs").json()["count"] == 2
    assert http.get(f"{room_path}/runs", params={"bundle_id": http_bundle["id"]}).json()["count"] == 1
    assert http.get(f"{room_path}/runs", params={"ok": "false"}).json()["count"] == 0


def test_a_run_that_does_not_exist_is_a_404(http, http_room):
    response = http.get(f"{PREFIX}/rooms/{http_room['id']}/runs/run_nope")
    assert response.status_code == 404
    assert response.json()["resource"] == "run"


def test_the_client_cannot_inject_faults(http, http_room, http_bundle):
    """A client that could forge a CRM failure could forge an audit record."""
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    run = http.post(
        f"{room_path}/bundles/{http_bundle['id']}/commit",
        json={"faults": {"refContact": "FORGED"}},
    ).json()
    assert run["ok"] is True
    assert run["counts"]["failed"] == 0


def test_no_audit_source_names_another_features_prefix(http, http_room, http_bundle):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    connector_id = http.post(f"{PREFIX}/connectors", json=CONNECTOR).json()["id"]
    bundle_id = http.post(
        f"{room_path}/bundles", json=BUNDLE | {"connector_id": connector_id}
    ).json()["id"]
    http.patch(f"{PREFIX}/connectors/{connector_id}", json={"enabled": True})
    http.post(f"{room_path}/bundles/{bundle_id}/commit", json={})
    http.delete(f"{PREFIX}/connectors/{connector_id}")

    sources = [e["source"] for e in http.get("/api/audit", params={"limit": 200}).json()["entries"]]
    ours = [source for source in sources if PREFIX in source]
    assert ours
    for other in ("/api/crm", "/api/analytics", "/api/wf-016", "/api/wf-026"):
        assert not [source for source in sources if other in source and PREFIX not in source]


def test_every_source_this_feature_records_names_a_route_the_host_mounted(
    http, http_room, http_bundle, http_connector
):
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    bundle_path = f"{room_path}/bundles/{http_bundle['id']}"
    http.patch(f"{PREFIX}/connectors/{http_connector['id']}", json={"collate_subrequests": True})
    http.post(f"{bundle_path}/preview", json={})
    http.post(f"{bundle_path}/commit", json={})
    http.patch(f"{bundle_path}", json={"name": "renamed"})
    http.post(
        f"{room_path}/bundles",
        json=BUNDLE | {"name": "second", "connector_id": http_connector["id"]},
    )
    http.delete(f"{PREFIX}/connectors/{http_connector['id']}")

    entries = http.get("/api/audit", params={"limit": 300}).json()["entries"]
    sources = {entry["source"] for entry in entries}
    for expected in (
        f"POST {PREFIX}/connectors",
        f"PATCH {PREFIX}/connectors/{http_connector['id']}",
        f"DELETE {PREFIX}/connectors/{http_connector['id']}",
        f"POST {room_path}/bundles",
        f"PATCH {bundle_path}",
        f"POST {bundle_path}/commit",
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


def test_the_rows_the_crm_creates_are_audited_with_the_commit_route(
    http, http_room, http_bundle
):
    """They are writes, and an audit row that cannot name its request is not one."""
    room_path = f"{PREFIX}/rooms/{http_room['id']}"
    http.post(f"{room_path}/bundles/{http_bundle['id']}/commit", json={})
    entries = http.get("/api/audit", params={"collection": TARGET_COLLECTION}).json()["entries"]
    assert len(entries) == 3
    assert {entry["source"] for entry in entries} == {
        f"POST {room_path}/bundles/{http_bundle['id']}/commit"
    }


def test_a_refused_write_leaves_no_audit_row(http, http_room):
    """Nothing was attempted, so there is nothing to audit."""
    assert http.post(f"{PREFIX}/connectors", json={}).status_code == 400
    assert http.get("/api/audit", params={"collection": CONNECTOR_COLLECTION}).json()["count"] == 0
    assert (
        http.post(
            f"{PREFIX}/rooms/{http_room['id']}/bundles",
            json={"name": "x", "records": [{"reference_id": "a", "type": "Account"}]},
        ).status_code
        == 400
    )
    assert http.get("/api/audit", params={"collection": BUNDLE_COLLECTION}).json()["count"] == 0
    assert http.post(f"{PREFIX}/rooms/room_nope/bundles", json=BUNDLE).status_code == 404
    assert http.get("/api/audit", params={"collection": BUNDLE_COLLECTION}).json()["count"] == 0


def test_the_domain_methods_that_write_require_a_source():
    """``source`` is required so a hardcoded URL cannot creep back in."""
    for name in (
        "create_connector",
        "update_connector",
        "delete_connector",
        "create_bundle",
        "update_bundle",
        "delete_bundle",
        "commit",
    ):
        signature = inspect.signature(getattr(BundleCommitter, name))
        assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["source"].default is inspect.Parameter.empty


def test_no_write_route_hardcodes_the_path_it_records_as_its_source():
    """The defect this programme shipped: an audit row naming a dead route.

    Checked on the source rather than on the running system, because a hardcoded
    path only shows up in the audit log once something is written. The router's
    own ``prefix`` is the one legitimate literal; what must not appear is a path
    spelled out inside a ``source=`` expression.
    """
    source = Path(inspect.getfile(load_feature(MODULE))).read_text(encoding="utf-8")
    assert 'prefix="/api/wf-039"' in source
    recorded = re.findall(r'source=(f?"[^"]*")', source)
    assert len(recorded) >= 7
    for literal in recorded:
        assert "router.prefix" in literal, f"a write records a literal path: {literal}"
        assert "/api/wf-039" not in literal


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_makes_unavoidable(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    summary = module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    assert summary
    assert count(store, CONNECTOR_COLLECTION) == 3
    assert count(store, BUNDLE_COLLECTION) >= 8
    assert count(store, RUN_COLLECTION) >= 8

    committer = BundleCommitter(store)
    runs = [run for room in rooms for run in committer.runs(room["id"], limit=100)]
    outcomes = {outcome for run in runs for outcome, n in (run["counts"] or {}).items() if n}
    assert OUTCOME_CREATED in outcomes
    assert OUTCOME_FAILED in outcomes
    assert OUTCOME_ROLLED_BACK in outcomes
    assert OUTCOME_SKIPPED in outcomes

    dialects = {run["dialect"] for run in runs}
    assert dialects == set(DIALECTS)

    assert any(run["compensated"] for run in runs)
    assert any(run["compensation_failures"] for run in runs)
    assert any(
        entry["code"] == WARN_IMPLICIT_DEPENDENCY
        for run in runs
        for entry in run["warnings"]
    )
    assert any(
        run["actionable_error"] and run["actionable_error"]["reason"] == FAIL_COLLATION_VIOLATION
        for run in runs
    )


def test_the_seed_shows_the_ordering_knob_working_both_ways(store):
    """A toggle nobody can see working is a toggle nobody trusts."""
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    committer = BundleCommitter(store)
    runs = [run for room in rooms for run in committer.runs(room["id"], limit=100)]
    implicit = [run for run in runs if "Proseware" in (run["bundle_name"] or "")]
    assert len(implicit) == 2
    on = next(run for run in implicit if "collation off" not in run["bundle_name"])
    off = next(run for run in implicit if "collation off" in run["bundle_name"])
    assert on["ok"] is False
    assert off["ok"] is True


def test_the_seed_survives_a_dataset_with_no_rooms(store):
    module = load_feature(MODULE)
    summary = module.seed(store, {"room_ids": []})
    assert "no rooms" in summary
    assert count(store, BUNDLE_COLLECTION) == 0


def test_the_seed_skips_a_stale_room_id_rather_than_aborting(store):
    module = load_feature(MODULE)
    live = store.create("room", {"name": "Live"}, actor="dana")
    summary = module.seed(store, {"room_ids": [("room_gone", "X"), (live["id"], "Y")]})
    assert summary
    assert count(store, BUNDLE_COLLECTION) > 0


def test_the_seed_never_opens_a_socket(store, monkeypatch):
    """A real CRM would try to reach a hostname from the seeder."""
    import urllib.request

    def refuse(*args, **kwargs):  # pragma: no cover - only runs on a defect
        raise AssertionError("seeding must not open a socket")

    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request, "Request", refuse)
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    assert count(store, RUN_COLLECTION) > 0
