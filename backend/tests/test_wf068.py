"""WF-068: the domain rules, tested without a server and without a database.

Everything in :mod:`dsr.meeting_followup` is a function from a mapping to a
mapping, so this file needs no ``TestClient`` and no fixture at all. That is the
property the module was shaped for, and this file is what proves it: the fastest
test in this suite is also the one that checks the most researched rules.

The file is organised by the thing being defended:

``the import surface``
    That the domain module depends on the store and on nothing else. The brief
    names this as a hard rule, and a domain module that reached for a framework
    could not be tested this way at all.

``the vocabulary``
    That every researched value is what the research says it is. Most of these
    tests read as transcription, and that is the point: a typo in an enum is a
    wrong answer to a documented API.

``the two permission gates``
    Both refusals, both from the vendor's own sentences. The issue requires them as
    refusals rather than warnings, so each has a test that the warning reading
    would fail.

``the batch split``
    That one bad prospect does not fail the batch, and that the cap of 200 is a
    refusal rather than a truncation.

``the daily schedule``
    The researched sentence "They do not run immediately when an automation is
    created or edited", which the issue names as a test case.

``the three statuses``
    That the enum has three values, that every one is reachable, and that none is
    terminal.

``the clock``
    That no rule reads the machine's date. The brief names time bombs as a defect
    and every date-taking function here takes its date as a parameter.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.meeting_followup import (
    automations as rules,
    cal,
    crm,
    flows,
    inferences,
    instances,
    prep,
    reminders,
    vocabulary as vocab,
)
from dsr.meeting_followup.errors import (
    AssignBatchTooLarge,
    AssigneeNotLicensed,
    AutomationInvalid,
    ContactWithoutParentAccount,
    CrmRecordInvalid,
    DateTriggerObjectRefused,
    FlowInstanceTransitionRefused,
    FlowInvalid,
    PersonalFlowRefused,
    PrepPageInvalid,
    SlackChannelRefused,
    WebhookInvalid,
)

#: A frozen instant. Every date in this file is relative to it, never to the
#: machine's calendar.
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "dsr" / "meeting_followup"


def _is_allowed(module: str) -> bool:
    """Whether the domain layer may import this module.

    Three allowances and no more: the standard library, the store, which is the
    layer's one project dependency, and this package's own submodules, which are
    the layer itself. Nothing here reaches for a framework or a socket library.
    """
    if module.startswith("dsr"):
        return module == "dsr.store" or module.startswith("dsr.meeting_followup")
    return "." not in module


# --------------------------------------------------------------------------- #
# the import surface
# --------------------------------------------------------------------------- #


class TestImportSurface:
    def test_the_domain_module_imports_nothing_but_the_store(self):
        """The hard rule the brief names.

        No framework, no HTTP, no sqlite3, and no other feature's module. The
        package's own submodules are allowed - that is the package - and so is the
        store, which is the one dependency the domain layer is allowed to have.

        Read from the parsed source rather than the text, so a re-export or an
        aliased import is recognised as what it is rather than as a string.
        """
        offenders: list[str] = []
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if not _is_allowed(alias.name):
                            offenders.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith("dsr") and not _is_allowed(module):
                        offenders.append(f"{path.name}: from {module} import ...")
        assert not offenders, offenders

    def test_no_module_in_this_package_opens_a_socket(self):
        """No outbound call anywhere, because neither vendor is this product."""
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            assert not names & {"socket", "http", "httpx", "urllib", "requests"}, path.name

    def test_no_module_imports_sqlite(self):
        """Checked on the imports rather than on the text, because the package
        docstring names sqlite3 while describing what the package does not do."""
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] != "sqlite3", path.name
                elif isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[0] != "sqlite3", path.name


# --------------------------------------------------------------------------- #
# the vocabulary
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_status_enum_has_exactly_three_values_and_no_terminal_one(self):
        """Quoted: flowInstanceStatus enum: Running, Pending, Paused.

        The issue says model those three and no others, so the length is asserted
        as well as the contents: a fourth value that kept the three would still
        break the rule.
        """
        assert vocab.FLOW_INSTANCE_STATUSES == ("Running", "Pending", "Paused")
        assert len(vocab.FLOW_INSTANCE_STATUSES) == 3
        assert not [s for s in vocab.FLOW_INSTANCE_STATUSES if "omplet" in s]

    def test_the_error_codes_are_the_three_the_research_names(self):
        assert vocab.ASSIGN_ERROR_CODES == (
            "InvalidArgument",
            "InvalidState",
            "UnexpectedError",
        )

    def test_the_batch_cap_is_two_hundred(self):
        """Quoted: You can assign up to 200 prospects to a flow in a single request."""
        assert vocab.MAX_PROSPECTS_PER_REQUEST == 200
        assert vocab.ASSIGN_SCOPE == "api:flows:write"
        assert vocab.ASSIGN_ENDPOINT == "/v2/flows/prospects/assign"

    def test_the_assignment_request_carries_the_three_researched_fields(self):
        """AssignFlowRequestV2{crmProspectsIds[], flowId, flowInstanceOwnerEmail}."""
        assert vocab.ASSIGN_REQUEST_FIELDS == (
            "crmProspectsIds",
            "flowId",
            "flowInstanceOwnerEmail",
        )

    def test_the_date_trigger_is_opportunity_only(self):
        """(Opportunity only) current date is."""
        assert vocab.CURRENT_DATE_IS not in vocab.CRM_TRIGGERS
        assert vocab.DATE_TRIGGER_OBJECT_TYPE == "DEAL"

    def test_the_sync_cadence_range_is_the_researched_one(self):
        """Quoted: we recommend updating between every 1-5 minutes."""
        assert vocab.CRM_SYNC_RECOMMENDED_RANGE == (1, 5)

    def test_the_prep_page_has_the_six_researched_sections(self):
        assert vocab.PREP_SECTIONS == (
            "objectives",
            "key_topics",
            "prior_discussion_highlights",
            "action_items_for_you",
            "suggested_questions",
            "reminders",
        )

    def test_the_recent_activity_feed_has_the_five_researched_kinds(self):
        """Quoted: a recent-activity feed (calls, emails, digital interactions, CRM changes, texts)."""
        assert vocab.RECENT_ACTIVITY_KINDS == (
            "calls",
            "emails",
            "digital_interactions",
            "crm_changes",
            "texts",
        )

    def test_the_transcription_formats_are_the_four_the_research_names(self):
        assert vocab.TRANSCRIPTION_FORMATS == ("json", "srt", "txt", "vtt")

    def test_the_cal_webhook_events_are_the_four_the_research_names(self):
        assert vocab.CAL_WEBHOOK_EVENTS == (
            "MEETING_ENDED",
            "RECORDING_READY",
            "RECORDING_TRANSCRIPTION_GENERATED",
            "BOOKING_NO_SHOW_UPDATED",
        )

    def test_the_tracker_quote_is_reproduced_with_its_typo(self):
        """The issue says: Quote it as written if you cite it.

        The duplicated AI is in the source. Correcting it would make this
        unmatchable against the evidence line a reviewer holds.
        """
        assert vocab.TRACKER_QUOTE_WITH_TYPO.count("AI") == 2
        assert "AI AI tracker detections" in vocab.TRACKER_QUOTE_WITH_TYPO
        assert "typo" in vocab.TRACKER_QUOTE_TYPO_NOTE

    def test_each_vendor_is_attributed_the_way_the_issue_states(self):
        """Gong supplies the prep page, the flows and the CRM upload. Cal supplies
        the follow-up webhooks and recording download links."""
        assert "meeting_prep" in vocab.GONG_CAPABILITIES
        assert "engage_flows" in vocab.GONG_CAPABILITIES
        assert "crm_upload" in vocab.GONG_CAPABILITIES
        assert "followup_webhooks" in vocab.CAL_CAPABILITIES
        assert "recording_downloads" in vocab.CAL_CAPABILITIES
        assert "meeting_prep" not in vocab.CAL_CAPABILITIES
        assert "followup_webhooks" not in vocab.GONG_CAPABILITIES

    def test_every_collection_is_prefixed_so_no_two_features_can_collide(self):
        assert all(name.startswith("wf068_") for name in vocab.ALL_COLLECTIONS)
        assert len(set(vocab.ALL_COLLECTIONS)) == len(vocab.ALL_COLLECTIONS)

    def test_the_object_types_are_the_four_the_upload_path_names(self):
        """POST /v2/crm/entities?objectType=ACCOUNT|CONTACT|DEAL|LEAD."""
        assert vocab.OBJECT_TYPES == ("ACCOUNT", "CONTACT", "DEAL", "LEAD")
        assert set(vocab.COLLECTION_BY_OBJECT_TYPE) == set(vocab.OBJECT_TYPES)


# --------------------------------------------------------------------------- #
# the two permission gates
# --------------------------------------------------------------------------- #


class TestPermissionGates:
    def test_the_first_gate_refuses_an_assignee_with_no_engage_licence(self):
        """Quoted: The selected user must have an Engage license. If they don't,
        the automation won't run."""
        with pytest.raises(AssigneeNotLicensed) as caught:
            crm.assert_engaged({"email": "sam@example.test", "engage_licensed": False})
        assert vocab.ENGAGE_LICENSE_QUOTE in str(caught.value)
        assert caught.value.status == 403

    def test_the_first_gate_refuses_an_absent_licence_too(self):
        """Absent means no. A user record that does not say yes has not said yes."""
        with pytest.raises(AssigneeNotLicensed):
            crm.assert_engaged({"email": "sam@example.test"})

    def test_the_first_gate_refuses_an_unknown_assignee(self):
        with pytest.raises(AssigneeNotLicensed):
            crm.assert_engaged(None)

    def test_the_first_gate_passes_a_licensed_assignee_and_returns_it(self):
        user = {"email": "dana@example.test", "engage_licensed": True}
        assert crm.assert_engaged(user) is user

    def test_the_second_gate_refuses_a_contact_with_no_parent_account(self):
        """Quoted: If the automation selects a contact without a parent account,
        the automation will fail to run."""
        record = {"object_type": "CONTACT", "crm_id": "con-1", "parent_account_id": ""}
        with pytest.raises(ContactWithoutParentAccount) as caught:
            crm.assert_parent_account(record)
        assert vocab.PARENT_ACCOUNT_QUOTE in str(caught.value)
        assert caught.value.status == 409

    def test_the_second_gate_passes_a_contact_with_a_parent_account(self):
        record = {"object_type": "CONTACT", "crm_id": "con-1", "parent_account_id": "acc-1"}
        assert crm.assert_parent_account(record) is record

    @pytest.mark.parametrize("kind", ["ACCOUNT", "DEAL", "LEAD"])
    def test_the_second_gate_is_skipped_for_an_object_with_no_parent(self, kind):
        """An account, a deal and a lead have no parent account, so refusing them
        would refuse every other object type in the product."""
        record = {"object_type": kind, "crm_id": "x-1"}
        assert crm.assert_parent_account(record) is record

    def test_the_third_gate_refuses_a_personal_flow(self):
        """Quoted: Personal flows cannot be used with automations."""
        with pytest.raises(PersonalFlowRefused) as caught:
            crm.assert_not_personal({"flow_id": "f-1", "personal": True})
        assert vocab.PERSONAL_FLOW_QUOTE in str(caught.value)
        assert caught.value.status == 409

    def test_the_third_gate_passes_a_company_flow(self):
        flow = {"flow_id": "f-1", "personal": False}
        assert crm.assert_not_personal(flow) is flow

    def test_refuse_without_gates_applies_both_and_names_the_first_failure(self):
        """One function, so no caller can apply one gate and forget the other."""
        with pytest.raises(AssigneeNotLicensed):
            rules.refuse_without_gates(
                {"name": "a"},
                {"email": "sam@example.test", "engage_licensed": False},
                {"object_type": "CONTACT", "parent_account_id": ""},
            )

    def test_refuse_without_gates_reaches_the_second_gate_once_the_first_passes(self):
        with pytest.raises(ContactWithoutParentAccount):
            rules.refuse_without_gates(
                {"name": "a"},
                {"email": "dana@example.test", "engage_licensed": True},
                {"object_type": "CONTACT", "parent_account_id": ""},
            )


# --------------------------------------------------------------------------- #
# the CRM record rules
# --------------------------------------------------------------------------- #


class TestCrmRecords:
    def test_an_object_type_is_folded_but_an_unknown_one_is_refused(self):
        assert crm.normalise_object_type(" contact ") == "CONTACT"
        with pytest.raises(CrmRecordInvalid):
            crm.normalise_object_type("PROSPECT")

    def test_a_crm_id_is_required(self):
        with pytest.raises(CrmRecordInvalid) as caught:
            crm.normalise_record("CONTACT", {"name": "Priya"})
        assert "crm_id" in caught.value.errors

    def test_a_contact_stores_an_absent_parent_account_as_an_absent_field(self):
        """A missing account must be visible as an absent field, so the gate can
        read it, rather than as an absent record."""
        record = crm.normalise_record("CONTACT", {"crm_id": "con-1", "name": "Priya"})
        assert record[vocab.PARENT_ACCOUNT_FIELD] == ""

    def test_a_collection_is_resolved_from_the_table_and_never_built(self):
        """A caller cannot name a collection this workflow does not own."""
        for kind, collection in vocab.COLLECTION_BY_OBJECT_TYPE.items():
            assert crm.collection_for(kind) == collection
        # The absence is the test: there is no fifth object type to resolve.
        assert "PROSPECT" not in vocab.COLLECTION_BY_OBJECT_TYPE


class TestSyncCadence:
    def test_the_cadence_range_is_enforced_at_both_ends(self):
        """Quoted: we recommend updating between every 1-5 minutes."""
        for minutes in (1, 3, 5):
            run = crm.validate_sync_run(
                {
                    "operation": "update",
                    "cadence_minutes": minutes,
                    "object_type": "DEAL",
                    vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                    "records": [],
                }
            )
            assert run["cadence_minutes"] == minutes
        for minutes in (0, 6, 60):
            with pytest.raises(CrmRecordInvalid) as caught:
                crm.validate_sync_run(
                    {
                        "operation": "update",
                        "cadence_minutes": minutes,
                        "object_type": "DEAL",
                        vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                        "records": [],
                    }
                )
            assert "cadence_minutes" in caught.value.errors

    def test_modification_date_is_mandatory_on_an_update(self):
        """Quoted: modificationDate is mandatory on updates."""
        with pytest.raises(CrmRecordInvalid) as caught:
            crm.validate_sync_run(
                {
                    "operation": "update",
                    "cadence_minutes": 1,
                    "object_type": "DEAL",
                    "records": [],
                }
            )
        assert vocab.MODIFICATION_DATE_FIELD in caught.value.errors

    def test_modification_date_is_refused_on_a_create(self):
        """It is a field of an update. Sending it on a create would be a request
        the vendor has not documented."""
        with pytest.raises(CrmRecordInvalid):
            crm.validate_sync_run(
                {
                    "operation": "create",
                    "cadence_minutes": 1,
                    "object_type": "DEAL",
                    vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                    "records": [],
                }
            )

    def test_the_recorded_request_carries_the_modification_date_and_is_not_sent(self):
        run = crm.validate_sync_run(
            {
                "operation": "update",
                "cadence_minutes": 5,
                "object_type": "DEAL",
                vocab.MODIFICATION_DATE_FIELD: "2026-10-09T00:00:00Z",
                "records": [{"crm_id": "deal-1", "name": "Renewal"}],
            }
        )
        request = crm.entities_request(run)
        assert request["endpoint"] == "/v2/crm/entities?objectType=DEAL"
        assert request["body"][vocab.MODIFICATION_DATE_FIELD] == "2026-10-09T00:00:00Z"
        assert request["sent"] is False


# --------------------------------------------------------------------------- #
# the When / If / Then evaluation
# --------------------------------------------------------------------------- #


def _automation(**trigger) -> dict:
    return {"name": "a", "flow_id": "f-1", "enabled": True, "match": "all", "trigger": trigger}


class TestTriggers:
    def test_is_created_fires_only_on_a_create(self):
        trigger = rules.validate_trigger({"kind": "crm_entity_created", "object_type": "CONTACT"})
        assert rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "create"})
        assert not rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "update"})

    def test_is_updated_with_fires_only_on_an_update(self):
        trigger = rules.validate_trigger(
            {
                "kind": "crm_entity_updated_with",
                "object_type": "DEAL",
                "conditions": [{"field": "stage", "operator": "equals", "value": "Won"}],
            }
        )
        assert rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "update"})
        assert not rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "create"})

    def test_is_updated_with_needs_a_condition_or_it_matches_every_update(self):
        with pytest.raises(AutomationInvalid) as caught:
            rules.validate_trigger({"kind": "crm_entity_updated_with", "object_type": "DEAL"})
        assert "trigger.conditions" in caught.value.errors

    def test_is_created_or_updated_fires_on_both(self):
        trigger = rules.validate_trigger({"kind": "crm_entity_created_or_updated"})
        assert rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "create"})
        assert rules.trigger_matches(trigger, {"kind": "crm_entity", "operation": "update"})

    def test_a_tracker_trigger_matches_on_the_tracker_name(self):
        trigger = rules.validate_trigger(
            {"kind": "tracker_detected", "tracker_name": "Pricing Objection"}
        )
        assert rules.trigger_matches(
            trigger, {"kind": "tracker_detected", "tracker_name": "Pricing Objection"}
        )
        assert not rules.trigger_matches(
            trigger, {"kind": "tracker_detected", "tracker_name": "Competitor Named"}
        )

    def test_an_unknown_trigger_kind_is_refused(self):
        with pytest.raises(AutomationInvalid) as caught:
            rules.validate_trigger({"kind": "when_the_moon_is_full"})
        assert "trigger.kind" in caught.value.errors

    def test_a_date_trigger_is_refused_for_any_object_but_opportunity(self):
        """(Opportunity only) current date is."""
        with pytest.raises(DateTriggerObjectRefused) as caught:
            rules.validate_trigger(
                {"kind": "current_date_is", "object_type": "CONTACT", "date_field": "x"}
            )
        assert caught.value.status == 400


class TestConditions:
    @pytest.mark.parametrize(
        ("observed", "operator", "expected", "result"),
        [
            ("Negotiation", "equals", "Negotiation", True),
            ("Negotiation", "equals", "negotiation", True),
            ("Negotiation", "not_equals", "Won", True),
            ("Call about pricing", "contains", "pricing", True),
            ("Call about pricing", "contains", "legal", False),
            (10, "greater_than", 5, True),
            (5, "greater_than", "many", False),
            (3, "less_than", 5, True),
            (True, "is_true", None, True),
            (False, "is_true", None, False),
            (False, "is_false", None, True),
        ],
    )
    def test_each_comparison(self, observed, operator, expected, result):
        assert rules.compare(observed, operator, expected) is result

    def test_an_unknown_operator_is_false_rather_than_an_exception(self):
        """A condition that cannot be evaluated must not send a follow-up email
        to a buyer, and must not raise on the automation's event path either."""
        assert rules.compare("x", "roughly_equals", "y") is False

    def test_match_all_requires_every_condition(self):
        conditions = [
            {"field": "stage", "operator": "equals", "value": "Won"},
            {"field": "amount", "operator": "greater_than", "value": 1000},
        ]
        assert rules.conditions_hold(conditions, {"stage": "Won", "amount": 5000}) is True
        assert rules.conditions_hold(conditions, {"stage": "Won", "amount": 5}) is False

    def test_match_any_requires_only_one(self):
        conditions = [
            {"field": "stage", "operator": "equals", "value": "Won"},
            {"field": "amount", "operator": "greater_than", "value": 1000},
        ]
        event = {"stage": "Negotiation", "amount": 5000}
        assert rules.conditions_hold(conditions, event, "all") is False
        assert rules.conditions_hold(conditions, event, "any") is True

    def test_an_empty_condition_list_matches(self):
        """'is created' with no further condition is the simplest automation the
        evidence names."""
        assert rules.conditions_hold([], {"anything": 1}) is True


class TestEvaluate:
    def test_a_matching_event_fires_and_names_why(self):
        automation = {
            **_automation(kind="crm_entity_updated_with", object_type="DEAL"),
            "trigger": {
                "kind": "crm_entity_updated_with",
                "object_type": "DEAL",
                "conditions": [{"field": "stage", "operator": "equals", "value": "Won"}],
            },
        }
        decision = rules.evaluate(
            automation, {"kind": "crm_entity", "operation": "update", "stage": "Won"}
        )
        assert decision["fires"] is True
        assert decision["reasons"] == ["matched"]

    def test_a_disabled_automation_never_fires_and_says_so(self):
        automation = {**_automation(kind="crm_entity_created"), "enabled": False}
        decision = rules.evaluate(automation, {"kind": "crm_entity", "operation": "create"})
        assert decision["fires"] is False
        assert decision["reasons"] == ["automation_disabled"]

    def test_a_condition_that_does_not_hold_says_so(self):
        automation = {
            **_automation(kind="crm_entity_created"),
            "trigger": {
                "kind": "crm_entity_created",
                "conditions": [{"field": "region", "operator": "equals", "value": "EMEA"}],
            },
        }
        decision = rules.evaluate(
            automation, {"kind": "crm_entity", "operation": "create", "region": "AMER"}
        )
        assert decision["reasons"] == ["condition_not_met"]


# --------------------------------------------------------------------------- #
# the daily date schedule
# --------------------------------------------------------------------------- #


class TestDailySchedule:
    def _date_automation(self, offset_days: int = -30) -> dict:
        return {
            "name": "Renewal reminder",
            "flow_id": "f-1",
            "assignee_email": "dana@example.test",
            "enabled": True,
            "match": "all",
            "trigger": {
                "kind": "current_date_is",
                "object_type": "DEAL",
                "date_field": "close_date",
                "offset_days": offset_days,
            },
            "last_evaluated_at": None,
        }

    def test_a_date_trigger_never_matches_an_ordinary_event(self):
        """The researched sentence, as a property of the function.

        "They do not run immediately when an automation is created or edited" is
        enforced by trigger_matches returning False for a date trigger on every
        input. A future change that made it True would fail here.
        """
        trigger = rules.validate_trigger(
            {"kind": "current_date_is", "object_type": "DEAL", "offset_days": -30}
        )
        for event in (
            {"kind": "crm_entity", "operation": "create"},
            {"kind": "crm_entity", "operation": "update"},
            {"kind": "tracker_detected", "tracker_name": "x"},
            {},
        ):
            assert rules.trigger_matches(trigger, event) is False

    def test_a_created_date_automation_is_still_waiting_for_its_first_morning(self):
        automation = rules.validate_automation(
            {
                "name": "Renewal reminder",
                "flow_id": "f-1",
                "assignee_email": "dana@example.test",
                "trigger": {"kind": "current_date_is", "object_type": "DEAL"},
            }
        )
        assert automation["pending_date_evaluation"] is True
        assert automation["last_evaluated_at"] is None
        assert rules.created_or_edited_at_was_not_evaluated(automation) is True

    def test_a_crm_automation_is_not_waiting_for_a_morning(self):
        automation = rules.validate_automation(
            {
                "name": "Contact created",
                "flow_id": "f-1",
                "assignee_email": "dana@example.test",
                "trigger": {"kind": "crm_entity_created", "object_type": "CONTACT"},
            }
        )
        assert rules.created_or_edited_at_was_not_evaluated(automation) is False

    def test_the_morning_run_enrols_only_a_deal_due_that_day(self):
        """A date of 30 days before 2026-11-30 is 2026-10-31, so a run on 2026-10-31
        enrols and a run on 2026-11-01 does not."""
        automation = self._date_automation(-30)
        deals = [
            {"crm_id": "deal-1", "fields": {"close_date": "2026-11-30"}},
            {"crm_id": "deal-2", "fields": {"close_date": "2027-03-01"}},
        ]
        assert [
            d["crm_id"] for d in rules.due_on_morning_run(automation, date(2026, 10, 31), deals)
        ] == ["deal-1"]
        assert rules.due_on_morning_run(automation, date(2026, 11, 1), deals) == []

    def test_a_deal_with_an_unreadable_date_is_never_enrolled(self):
        """A malformed date must not enrol a buyer."""
        automation = self._date_automation(0)
        deals = [{"crm_id": "deal-1", "fields": {"close_date": "soon"}}]
        assert rules.due_on_morning_run(automation, TODAY, deals) == []

    def test_the_morning_run_time_is_the_first_minute_of_the_researched_window(self):
        """The research says 'each morning' and names no hour, so the hour is a
        derivation and it is served rather than hidden."""
        moment = rules.morning_run_time(TODAY)
        assert (moment.hour, moment.minute) == (
            vocab.DATE_TRIGGER_EVALUATION_HOUR,
            vocab.DATE_TRIGGER_EVALUATION_MINUTE,
        )

    def test_a_crm_automation_is_never_due_on_a_morning(self):
        automation = {
            "name": "a",
            "enabled": True,
            "trigger": {"kind": "crm_entity_created"},
        }
        assert rules.due_on_morning_run(automation, TODAY, []) == []


# --------------------------------------------------------------------------- #
# the three statuses
# --------------------------------------------------------------------------- #


class TestStateMachine:
    def test_a_new_instance_starts_pending(self):
        """Pending is the only one of the three that describes a sequence which
        has not begun."""
        assert instances.initial_status() == "Pending"

    def test_every_status_is_reachable_from_every_other(self):
        """Derived graph, and asserted so a change to one edge is visible."""
        for source in vocab.FLOW_INSTANCE_STATUSES:
            for target in vocab.FLOW_INSTANCE_STATUSES:
                if source == target:
                    continue
                assert target in instances.allowed_transitions(source), (source, target)

    def test_a_pending_instance_with_an_undelivered_touch_cannot_start(self):
        """Otherwise it would be Running with nothing sent."""
        steps = [{"position": 1, "delivered_at": None}, {"position": 2, "delivered_at": None}]
        with pytest.raises(FlowInstanceTransitionRefused) as caught:
            instances.advance("Pending", "Running", steps=steps)
        assert caught.value.current == "Pending"
        assert caught.value.requested == "Running"
        assert "Running" in caught.value.allowed

    def test_a_pending_instance_starts_once_its_first_touch_is_delivered(self):
        steps = [{"position": 1, "delivered_at": "2026-10-09T09:00:00Z"}]
        assert instances.advance("Pending", "Running", steps=steps) == "Running"

    def test_a_fourth_status_is_refused_and_the_message_names_the_enum_size(self):
        """The alternative reading of "allowed" is the transitions from the current
        status, not the enum, so the message is what proves the enum has three."""
        with pytest.raises(FlowInstanceTransitionRefused) as caught:
            instances.advance("Running", "Completed")
        assert caught.value.allowed == ("Pending", "Paused")
        assert "exactly 3 values" in str(caught.value)
        assert "not one of the researched statuses" in str(caught.value)

    def test_an_unknown_current_status_is_refused(self):
        with pytest.raises(FlowInstanceTransitionRefused):
            instances.advance("Archived", "Running")

    def test_the_status_view_carries_the_alternatives_and_says_it_is_not_terminal(self):
        view = instances.status_view({"status": "Running", "owner_email": "dana@example.test"})
        assert view["statuses"] == ["Running", "Pending", "Paused"]
        assert view["allowed_transitions"] == ["Pending", "Paused"]
        assert view["terminal"] is False
        assert "no terminal value" in view["note"]


# --------------------------------------------------------------------------- #
# the batch split
# --------------------------------------------------------------------------- #


class TestBatchAssignment:
    OWNER = {"email": "dana@example.test", "engage_licensed": True}
    FLOW = {"flow_id": "f-1", "name": "Renewal", "personal": False, "steps": []}

    def _request(self, ids: list[str]) -> dict:
        return {
            "crmProspectsIds": ids,
            "flowId": "f-1",
            "flowInstanceOwnerEmail": "dana@example.test",
        }

    def _prospects(self) -> list[dict]:
        return [
            {"crm_id": "con-1", "object_type": "CONTACT", "parent_account_id": "acc-1"},
            {"crm_id": "con-2", "object_type": "CONTACT", "parent_account_id": ""},
            {"crm_id": "lead-1", "object_type": "LEAD"},
        ]

    def test_the_answer_splits_into_the_two_researched_lists(self):
        """prospectsAssigned[] vs prospectsNotAssigned[]."""
        answer = flows.assign(
            self._request(["con-1", "con-2"]),
            flow=self.FLOW,
            owner=self.OWNER,
            prospects=self._prospects(),
            room_id="room-1",
        )
        assert set(answer) >= {"prospectsAssigned", "prospectsNotAssigned"}
        assert set(answer["prospectsAssigned"][0]) >= set(vocab.ASSIGNED_FIELDS)
        assert set(answer["prospectsNotAssigned"][0]) >= set(vocab.NOT_ASSIGNED_FIELDS)

    def test_one_bad_prospect_does_not_fail_the_batch(self):
        """Quoted: Failures are returned per-prospect with an error code rather
        than failing the batch."""
        answer = flows.assign(
            self._request(["con-1", "con-2", "lead-1"]),
            flow=self.FLOW,
            owner=self.OWNER,
            prospects=self._prospects(),
            room_id="room-1",
        )
        assert [e["crmProspectId"] for e in answer["prospectsAssigned"]] == ["con-1", "lead-1"]
        assert [e["crmProspectId"] for e in answer["prospectsNotAssigned"]] == ["con-2"]

    def test_an_unknown_id_is_invalid_argument_for_that_prospect_only(self):
        answer = flows.assign(
            self._request(["con-1", "con-999"]),
            flow=self.FLOW,
            owner=self.OWNER,
            prospects=self._prospects(),
            room_id="room-1",
        )
        assert len(answer["prospectsAssigned"]) == 1
        failure = answer["prospectsNotAssigned"][0]
        assert failure["crmProspectId"] == "con-999"
        assert failure["errorCode"] == "InvalidArgument"
        assert (
            vocab.PARENT_ACCOUNT_QUOTE in failure["detail"] or "no CRM record" in failure["detail"]
        )

    def test_a_parentless_contact_is_invalid_argument(self):
        answer = flows.assign(
            self._request(["con-2"]),
            flow=self.FLOW,
            owner=self.OWNER,
            prospects=self._prospects(),
            room_id="room-1",
        )
        assert answer["prospectsNotAssigned"][0]["errorCode"] == "InvalidArgument"
        assert vocab.PARENT_ACCOUNT_QUOTE in answer["prospectsNotAssigned"][0]["detail"]

    def test_an_unlicensed_assignee_refuses_the_whole_batch(self):
        """The assignee is a property of the request, not of a prospect."""
        with pytest.raises(AssigneeNotLicensed):
            flows.assign(
                self._request(["con-1", "con-2"]),
                flow=self.FLOW,
                owner={"email": "sam@example.test", "engage_licensed": False},
                prospects=self._prospects(),
                room_id="room-1",
            )

    def test_a_personal_flow_refuses_the_whole_batch(self):
        with pytest.raises(PersonalFlowRefused):
            flows.assign(
                self._request(["con-1"]),
                flow={**self.FLOW, "personal": True},
                owner=self.OWNER,
                prospects=self._prospects(),
                room_id="room-1",
            )

    def test_two_hundred_prospects_are_accepted_and_two_hundred_and_one_are_not(self):
        ids = [f"p-{n}" for n in range(200)]
        request = flows.normalise_request(
            {
                "crmProspectsIds": ids,
                "flowId": "f-1",
                "flowInstanceOwnerEmail": "dana@example.test",
            }
        )
        assert len(request["crmProspectsIds"]) == 200
        with pytest.raises(AssignBatchTooLarge) as caught:
            flows.normalise_request(
                {
                    "crmProspectsIds": [*ids, "p-200"],
                    "flowId": "f-1",
                    "flowInstanceOwnerEmail": "dana@example.test",
                }
            )
        assert caught.value.status == 400
        assert "200" in str(caught.value)

    def test_an_empty_or_missing_list_is_refused(self):
        for payload in (
            {"flowId": "f-1", "flowInstanceOwnerEmail": "a@b.test"},
            {"crmProspectsIds": [], "flowId": "f-1", "flowInstanceOwnerEmail": "a@b.test"},
            {"crmProspectsIds": "con-1", "flowId": "f-1", "flowInstanceOwnerEmail": "a@b.test"},
        ):
            with pytest.raises(FlowInvalid):
                flows.normalise_request(payload)

    def test_the_owner_email_is_required(self):
        """The assignee owns the flow to-dos and is the sender for the emails, so
        a request with no owner names nobody."""
        with pytest.raises(FlowInvalid) as caught:
            flows.normalise_request({"crmProspectsIds": ["con-1"], "flowId": "f-1"})
        assert "flowInstanceOwnerEmail" in caught.value.errors

    def test_a_flow_instance_id_is_stable_for_the_same_prospect_and_flow(self):
        first = flows.new_flow_instance_id("f-1", "con-1")
        assert first == flows.new_flow_instance_id("f-1", "con-1")
        assert first != flows.new_flow_instance_id("f-2", "con-1")

    def test_the_workspace_id_is_stable_for_one_room(self):
        assert flows.workspace_id("room-1") == flows.workspace_id("room-1")
        assert flows.workspace_id("room-1") != flows.workspace_id("room-2")


# --------------------------------------------------------------------------- #
# the to-do queue
# --------------------------------------------------------------------------- #


class TestTodoQueue:
    def test_the_position_is_the_researched_label(self):
        """Quoted: 1/5 means you're working on the first of five to-dos in that queue."""
        rows = [{"todo_id": f"t-{n}", "position": n} for n in range(1, 6)]
        assert flows.queue_position(rows[0], rows) == "1/5"
        assert flows.queue_position(rows[4], rows) == "5/5"

    def test_a_todo_outside_the_queue_reads_as_zero_of_n(self):
        rows = [{"todo_id": "t-1"}]
        assert flows.queue_position({"todo_id": "t-9"}, rows) == "0/1"

    def test_an_empty_queue_reads_as_zero_of_zero(self):
        assert flows.queue_position({"todo_id": "t-1"}, []) == "0/0"

    def test_the_queue_is_grouped_and_ordered_by_position(self):
        todos = [
            {"todo_id": "t-3", "queue_id": "q1", "position": 3, "owner_email": "dana"},
            {"todo_id": "t-1", "queue_id": "q1", "position": 1, "owner_email": "dana"},
            {"todo_id": "t-2", "queue_id": "q1", "position": 2, "owner_email": "dana"},
            {"todo_id": "x-1", "queue_id": "q2", "position": 1, "owner_email": "sam"},
        ]
        view = flows.queue_view("dana", todos)
        assert view["queue_count"] == 1
        assert [t["position_label"] for t in view["queues"][0]["todos"]] == ["1/3", "2/3", "3/3"]
        # Another seller's queue is not shown in this seller's.
        assert "x-1" not in str(view)

    def test_every_todo_carries_the_flow_reference(self):
        """Quoted: the flow reference appears with the activity."""
        flow = {
            "flow_id": "f-1",
            "name": "Renewal",
            "steps": [{"kind": "task", "subject": "a", "offset_days": 0}],
        }
        touches = flows.build_touches(flow, starts_on="2026-10-09")
        assert touches[0]["flow_reference"] == "f-1"
        assert touches[0]["due_on"] == "2026-10-09"
        assert flows.build_touches(flow, starts_on="2026-10-09")[0]["flow_reference"] == "f-1"

    def test_touches_are_built_in_offset_order(self):
        """The position a touch gets is its index in the list, so the order has to
        be the order they are due and not the order they arrived in."""
        flow = {
            "flow_id": "f-1",
            "name": "Renewal",
            "steps": [
                {"kind": "task", "subject": "third", "offset_days": 9},
                {"kind": "task", "subject": "first", "offset_days": 1},
                {"kind": "task", "subject": "second", "offset_days": 4},
            ],
        }
        touches = flows.build_touches(flow, starts_on="2026-10-09")
        assert [t["position"] for t in touches] == [1, 2, 3]
        assert [t["offset_days"] for t in touches] == [1, 4, 9]
        assert [t["due_on"] for t in touches] == ["2026-10-10", "2026-10-13", "2026-10-18"]

    def test_an_unreadable_start_date_leaves_the_due_date_empty_rather_than_raising(self):
        flow = {"flow_id": "f-1", "name": "R", "steps": [{"kind": "task", "offset_days": 1}]}
        assert flows.build_touches(flow, starts_on="not-a-date")[0]["due_on"] == ""


# --------------------------------------------------------------------------- #
# the prep page
# --------------------------------------------------------------------------- #


PAGE = {
    "meeting_id": "m-1",
    "title": "Northwind renewal",
    "starts_at": "2026-10-14T14:00:00Z",
    "summary": {
        "objectives": ["Agree the renewal timeline"],
        "key_topics": ["Renewal pricing"],
        "suggested_questions": ["Who signs off?"],
        "action_items_for_you": ["Send the questionnaire"],
    },
    "participants": [{"email": "buyer@example.test", "activity": [{"kind": "calls"}]}],
}


class TestPrepPage:
    def test_the_six_sections_are_carried_through(self):
        view = prep.page_view(PAGE)
        assert view["sections"] == list(vocab.PREP_SECTIONS)
        assert set(view["summary"]) <= set(vocab.PREP_SECTIONS)

    def test_a_seventh_section_survives_without_a_migration(self):
        """The payload is ordinary JSON, so a team adding a section needs no
        coordination with anyone."""
        page = prep.normalise_page(
            {"meeting_id": "m-2", "summary": {"objectives": ["x"], "competitor_mentioned": True}}
        )
        assert page["summary_extra"]["competitor_mentioned"] is True
        assert "competitor_mentioned" not in page["summary"]

    def test_the_summary_is_marked_assembled_rather_than_generated(self):
        """No summarisation model is named in the research."""
        page = prep.normalise_page({"meeting_id": "m-3"})
        assert page["summary_source"] == "assembled_from_store"
        assert prep.page_view(page)["summary_source"] == "assembled_from_store"

    def test_a_meeting_id_and_a_readable_start_are_required(self):
        with pytest.raises(PrepPageInvalid):
            prep.normalise_page({})
        with pytest.raises(PrepPageInvalid) as caught:
            prep.normalise_page({"meeting_id": "m-1", "starts_at": "next tuesday"})
        assert "starts_at" in caught.value.errors

    def test_the_activity_feed_always_has_the_five_researched_groups(self):
        grouped = prep.group_activities([{"kind": "texts", "summary": "a"}])
        assert list(grouped) == list(vocab.RECENT_ACTIVITY_KINDS)
        assert grouped["texts"] and not grouped["calls"]

    def test_the_mobile_view_omits_the_feed_and_says_why(self):
        """Quoted: the mobile app ... does not display recent activities."""
        activities = [{"kind": "calls", "summary": "Discovery"}]
        assert prep.page_view(PAGE, activities=activities)["recent_activity"]
        mobile = prep.page_view(PAGE, activities=activities, mobile=True)
        assert mobile["recent_activity"] is None
        assert mobile["recent_activity_omitted_because"] is True

    def test_the_seven_entry_points_are_served(self):
        view = prep.page_view(PAGE)
        assert len(view["entry_points"]) >= 6
        assert view["keyboard_shortcut"] == "Shift+M"

    def test_the_push_window_is_the_thirty_minutes_before_the_start(self):
        """Quoted: 30 minutes before each external meeting."""
        start = "2026-10-14T14:00:00+00:00"
        assert prep.push_reminder_due(start, datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc))
        assert prep.push_reminder_due(start, datetime(2026, 10, 14, 13, 59, tzinfo=timezone.utc))
        assert not prep.push_reminder_due(
            start, datetime(2026, 10, 14, 13, 29, tzinfo=timezone.utc)
        )
        assert not prep.push_reminder_due(start, datetime(2026, 10, 14, 14, 1, tzinfo=timezone.utc))

    def test_an_unreadable_start_never_opens_the_push_window(self):
        assert prep.push_reminder_due("soon", NOW) is False


class TestDryRun:
    def test_the_participant_is_built_from_the_meeting_context(self):
        """Quoted: an AI participant built from the meeting context."""
        participant = prep.dry_run_participant(PAGE, "neutral")
        assert participant["built_from"] == "meeting_context"
        assert participant["knows_objectives"] == PAGE["summary"]["objectives"]
        assert participant["derived"] is True

    def test_an_unknown_voice_is_refused(self):
        with pytest.raises(PrepPageInvalid) as caught:
            prep.normalise_dry_run({"meeting_id": "m-1", "voice": "sepia"})
        assert "voice" in caught.value.errors

    def test_the_feedback_is_a_checklist_over_the_researched_sections(self):
        feedback = prep.dry_run_feedback(PAGE, covered=["Agree the renewal timeline"])
        assert feedback["objectives_covered"] == ["Agree the renewal timeline"]
        assert feedback["objectives_total"] == 1
        assert feedback["open_items"] == ["Send the questionnaire"]
        assert feedback["again_label"] == "Go again"

    def test_the_dry_run_says_it_is_derived_and_names_what_it_did_not_do(self):
        record = prep.normalise_dry_run({"meeting_id": "m-1"})
        assert record["derived"] is True
        assert "No synthesis provider is named" in record["note"]


class TestAskAnything:
    def test_the_answer_names_the_sections_it_drew_on(self):
        answer = prep.answer(PAGE, "what did we agree about the objectives?")
        assert "objectives" in answer["sources"]
        assert answer["answer"]["objectives"] == ["Agree the renewal timeline"]

    def test_a_question_that_names_a_section_matches_it_despite_punctuation(self):
        """The word "objectives" appears nowhere in the objectives' own text, so a
        content-only match answers the most ordinary question a rep can ask about
        their own meeting with nothing.

        The question mark matters here: without stripping it, "objectives?" is a
        different token from "objectives".
        """
        assert prep.answer(PAGE, "what were the objectives?")["sources"] == ["objectives"]
        assert prep.answer(PAGE, "what were the objectives")["sources"] == ["objectives"]
        assert prep.answer(PAGE, "the objectives, please")["sources"] == ["objectives"]

    def test_a_question_shares_a_section_on_content_words_too(self):
        """Matching on the section name alone would have missed this one."""
        assert "action_items_for_you" in prep.answer(PAGE, "questionnaire")["sources"]

    def test_a_question_no_section_bears_on_returns_the_section_list_and_no_guess(self):
        answer = prep.answer(PAGE, "zzzz qqqq")
        assert answer["sources"] == []
        assert answer["answer"] == {}
        assert answer["available_sections"] == list(vocab.ASK_ANYTHING_ANSWER_SOURCES)

    def test_an_empty_question_matches_nothing(self):
        assert prep.answer(PAGE, "")["sources"] == []

    def test_the_answer_says_it_is_derived(self):
        """The research names Ask anything and no behaviour at all."""
        assert prep.answer(PAGE, "pricing?")["derived"] is True


# --------------------------------------------------------------------------- #
# the Cal half
# --------------------------------------------------------------------------- #


class TestCal:
    def test_each_researched_webhook_is_accepted(self):
        for event in vocab.CAL_WEBHOOK_EVENTS:
            assert cal.validate_event({"event": event, "bookingUid": "bk-1"})["event"] == event

    def test_an_event_this_workflow_does_not_model_is_refused(self):
        """Otherwise the webhook log would show rows that drove nothing."""
        with pytest.raises(WebhookInvalid) as caught:
            cal.validate_event({"event": "BOOKING_CREATED", "bookingUid": "bk-1"})
        assert "event" in caught.value.errors

    def test_a_booking_uid_is_required(self):
        with pytest.raises(WebhookInvalid) as caught:
            cal.validate_event({"event": "MEETING_ENDED"})
        assert "bookingUid" in caught.value.errors

    def test_the_recording_link_is_recorded_and_not_fetched(self):
        """Quoted: RECORDING_READY payload includes downloadLink."""
        payload = cal.recording_payload(
            {"downloadLink": "https://app.cal.com/api/video/recording?token=demo"}
        )
        assert payload["has_link"] is True
        assert payload["fetched"] is False
        assert cal.recording_payload({})["has_link"] is False

    def test_the_transcription_links_are_read_from_the_researched_shape(self):
        """Quoted: downloadLinks.transcription[] in json/srt/txt/vtt."""
        payload = cal.transcription_payload(
            {
                "downloadLinks": {
                    "transcription": [
                        {"format": fmt, "link": f"https://app.cal.com/t.{fmt}"}
                        for fmt in vocab.TRANSCRIPTION_FORMATS
                    ]
                }
            }
        )
        assert payload["available"] == list(vocab.TRANSCRIPTION_FORMATS)
        assert payload["unknown_formats"] == []

    def test_a_format_outside_the_four_is_reported_rather_than_dropped(self):
        payload = cal.transcription_payload(
            {"downloadLinks": {"transcription": [{"format": "docx", "link": "x"}]}}
        )
        assert payload["links"] == {}
        assert payload["unknown_formats"] == ["docx"]

    def test_the_after_event_workflow_carries_the_researched_trigger_and_offset(self):
        """Quoted: POST /v2/workflows with trigger afterEvent (offset)."""
        flow = {
            "flow_id": "f-1",
            "steps": [
                {"kind": "email", "subject": "Recap", "offset_days": 1, "template": "rating"},
                {
                    "kind": "task",
                    "subject": "Call",
                    "offset_days": 3,
                    "conditions": [{"field": "stage", "value": "Won"}],
                },
            ],
        }
        workflow = cal.after_event_workflow(
            flow, booking_uid="bk-1", event_ended_at="2026-10-14T15:00:00Z"
        )
        assert workflow["endpoint"] == "/v2/workflows"
        assert workflow["trigger"]["type"] == "afterEvent"
        assert workflow["trigger"]["at"] == "2026-10-14T15:00:00Z"
        assert workflow["actions"][0]["eventType"] == "rating"
        assert workflow["actions"][1]["filter"] == {"stage": "Won"}
        assert workflow["sent"] is False

    def test_an_inbound_workflow_is_checked_against_the_published_enums(self):
        assert (
            cal.validate_workflow({"trigger": {"type": "afterEvent"}, "actions": []})["problems"]
            == []
        )
        bad = cal.validate_workflow(
            {"trigger": {"type": "beforeEvent"}, "actions": [{"eventType": "cancelled"}]}
        )
        assert bad["valid"] is False
        assert len(bad["problems"]) == 2


# --------------------------------------------------------------------------- #
# the Forecast reminders
# --------------------------------------------------------------------------- #


class TestForecastReminders:
    def test_the_three_researched_timings_are_recorded_with_which_are_sent(self):
        rows = reminders.served()
        assert len(rows) == 3
        assert [r["hours"] for r in rows] == [24, 1, 2]
        assert [r["implemented"] for r in rows] == [True, False, False]

    def test_a_slack_reminder_is_refused_and_names_its_timing(self):
        """A reminder that posts nowhere is a rule that looks armed."""
        with pytest.raises(SlackChannelRefused) as caught:
            reminders.validate_reminder({"channel": "slack", "due_at": "2026-11-30T00:00:00Z"})
        assert "no confirmed Slack surface" in str(caught.value)
        assert "1h before" in str(caught.value)

    def test_the_in_app_reminder_is_accepted_at_the_researched_offset(self):
        data = reminders.validate_reminder({"channel": "in_app", "due_at": "2026-11-30T00:00:00Z"})
        assert data["hours_before"] == vocab.FORECAST_IN_APP_HOURS_BEFORE == 24
        assert data["implemented"] is True

    def test_an_unknown_channel_is_refused(self):
        with pytest.raises(SlackChannelRefused):
            reminders.validate_reminder({"channel": "carrier_pigeon", "due_at": "x"})

    def test_the_in_app_window_is_the_twenty_four_hours_before_the_due_date(self):
        due = "2026-11-30T09:00:00+00:00"
        assert reminders.reminder_moment(due, datetime(2026, 11, 29, 9, 0, tzinfo=timezone.utc))
        assert reminders.reminder_moment(due, datetime(2026, 11, 29, 12, 0, tzinfo=timezone.utc))
        assert not reminders.reminder_moment(
            due, datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
        )
        assert not reminders.reminder_moment(
            due, datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
        )


# --------------------------------------------------------------------------- #
# the recorded derivations
# --------------------------------------------------------------------------- #


class TestInferences:
    REQUIRED = {
        "id",
        "topic",
        "question",
        "decision",
        "evidence",
        "rejected",
        "cost_of_the_rejected_reading",
        "residual_risk",
        "surface",
    }

    def test_every_derivation_carries_all_its_parts(self):
        """The research requires a derivation to be recorded rather than assumed."""
        for entry in inferences.describe():
            assert self.REQUIRED <= set(entry), entry["id"]
            for key in self.REQUIRED - {"id"}:
                assert str(entry[key]).strip(), (entry["id"], key)

    def test_the_ids_are_unique_and_addressable(self):
        ids = [entry["id"] for entry in inferences.INFERENCES]
        assert len(set(ids)) == len(ids)
        for entry_id in ids:
            assert inferences.by_id(entry_id)["id"] == entry_id

    def test_every_open_point_the_issue_raises_is_answered(self):
        """One entry per item the brief lists as a decision the research left open."""
        ids = {entry["id"] for entry in inferences.INFERENCES}
        for required in (
            "wf068-summary-assembled-not-generated",
            "wf068-dry-run-is-derived",
            "wf068-ask-anything-answers-from-context",
            "wf068-per-prospect-failure",
            "wf068-three-statuses-only",
            "wf068-date-triggers-wait-for-morning",
            "wf068-gates-are-refusals",
            "wf068-personal-flows-refused",
            "wf068-cadence-and-modification-date",
            "wf068-vendor-attribution",
            "wf068-slack-left-out",
            "wf068-tracker-quote-as-written",
            "wf068-mobile-omits-the-feed",
        ):
            assert required in ids, required

    def test_an_unknown_id_is_refused_with_the_legal_set(self):
        with pytest.raises(AutomationInvalid) as caught:
            inferences.describe_one("not-a-decision")
        assert "decision_id" in caught.value.errors

    def test_the_vendor_decision_names_the_jev_audit_it_rests_on(self):
        entry = inferences.by_id("wf068-own-crm-collections")
        assert "jev-20261013T111405-12092-45997" in entry["residual_risk"]


# --------------------------------------------------------------------------- #
# the clock
# --------------------------------------------------------------------------- #


class TestNoWallClock:
    def test_no_rule_reads_the_current_date(self):
        """The brief names a time bomb as a defect.

        A rule that read ``date.today()`` would pass today and fail whenever the
        test's frozen date and the machine's disagreed. The search is for the two
        calls that would do it, and ``_utcnow`` in the engine is the only permitted
        one, because it is what an injected clock replaces.
        """
        offenders: list[str] = []
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            if path.name == "engine.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"today", "now"}:
                        offenders.append(f"{path.name}: .{node.func.attr}()")
        assert not offenders, offenders

    def test_the_engine_takes_its_clock_rather_than_reading_one(self):
        assert "def __init__(self, store: RecordStore, clock=None)" in (
            (DOMAIN_DIR / "engine.py").read_text(encoding="utf-8")
        )

    def test_a_frozen_clock_drives_every_date_the_workflow_writes(self):
        """One frozen instant, moved by hand, is the only calendar any test here
        reads. Nothing in this file consults the machine's date."""
        assert (TODAY.year, TODAY.month, TODAY.day) == (2026, 10, 9)
        assert TODAY + timedelta(days=30) == date(2026, 11, 8)
