"""WF-084's domain rules: the tenant assertion, the code's life, and the directory.

Organised by the property each group of tests defends, because this workflow is mostly one
property and a test file that does not say which is which reads as a list of assertions:

``the tenant assertion``
    The security property the specification states twice, once in bold. A profile from
    another organization must be refused, and a profile whose email is at the right domain
    but whose tenant is wrong must be refused too. Both directions are tested because the
    specification names both, and a rule that only refuses mismatches would pass the first
    and fail the second.
``the authorization code``
    Ten minutes, quoted from the research, and an expired code is refused rather than
    retried.
``the three identifiers``
    ``organization``, ``connection`` and ``provider`` do three different jobs and none of
    them substitutes for another.
``directory state decides access``
    Groups are the only input to an access answer, the strongest role wins whatever order
    the directory reported memberships in, and a deprovision removes access rather than
    relabelling a user.
``the build's own boundaries``
    The domain imports nothing but the store, the seed string is encodable by cp1252, and
    the feature module touches no shared file.

These tests run against a real audited store rather than a fake, so a write that skipped the
audit log would fail here rather than only in production.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.security_governance import (
    sso_inferences as inferences,
    sso_rules as rules,
    sso_vocabulary as vocab,
)
from dsr.security_governance.sso_engine import FederationEngine
from dsr.store import RecordStore

FEATURE_MODULE = "dsr.features.wf084_federate_staff_sso_and_auto_provision_via_scim"
PREFIX = "/api/wf-084"
FEATURE_ID = "wf-084-federate-staff-sso-and-auto-provision-via-scim"

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DOMAIN_PACKAGE = BACKEND / "dsr" / "security_governance"

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
CALLBACK = "https://app.example/wf-084/callback"
TENANT = "org_northwind"


@pytest.fixture
def engine(store: RecordStore) -> FederationEngine:
    """An engine over an empty store, with the clock under the test's control."""

    return FederationEngine(store, now=lambda: NOW)


@pytest.fixture
def tenant(engine: FederationEngine) -> dict:
    """One recorded tenant, with a registered redirect URI."""

    return engine.create_organization(
        {
            vocab.ORGANIZATION_ID: TENANT,
            "name": "Northwind Traders",
            "redirect_uris": [CALLBACK],
            vocab.CLIENT_ID_FIELD: "client_northwind",
        },
        actor="dana",
        source="test",
    )


def _shared_guard_needs_commits() -> bool:
    """Does ``check_feature_diff.py --base`` have a committed diff to report?

    True until this branch has a commit of its own, and True when ``origin/main`` is not
    reachable. Both cases mean the guard has nothing to measure rather than that it passed,
    so the caller skips instead of asserting.
    """

    result = subprocess.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    if result.returncode != 0:
        return True
    try:
        return int(result.stdout.strip() or 0) == 0
    except ValueError:
        return True


def profile(organization_id: str | None = TENANT, address: str = "dana@northwind.example") -> dict:
    """A profile the way an IdP returns one: an organization id and an email."""

    payload: dict = {vocab.SCIM_EMAILS: [{"address": address}]}
    if organization_id is not None:
        payload[vocab.ORGANIZATION_ID] = organization_id
    return payload


def sign_in(engine: FederationEngine, tenant_id: str = TENANT, **kwargs) -> dict:
    """A complete sign-in, with a fresh code unless the caller ages it."""

    payload = {
        vocab.ORGANIZATION_PARAM: tenant_id,
        "issued_at": rules.stamp(NOW),
        "profile": profile(tenant_id),
    }
    payload.update(kwargs)
    return engine.complete_sign_in(payload, actor="dana", source="test")


def make_directory(engine: FederationEngine, tenant_id: str = TENANT) -> dict:
    """A connected directory provider, with the token needed to post to its webhook."""

    created = engine.create_directory(
        tenant_id, {vocab.PROVIDER_PARAM: vocab.PROVIDER_OKTA}, actor="dana", source="test"
    )
    return created


def send_user(
    engine: FederationEngine,
    directory: dict,
    external_id: str,
    *,
    groups: list[str] | None = None,
    active: bool = True,
    address: str | None = None,
    operation: str = vocab.SCIM_OP_CREATE,
) -> dict:
    """One SCIM Users change through the webhook, as a directory provider would post it."""

    subject: dict = {
        vocab.SCIM_EXTERNAL_ID: external_id,
        vocab.SCIM_GIVEN_NAME: external_id.split("_")[-1].title(),
        vocab.SCIM_EMAILS: [address or f"{external_id}@northwind.example"],
        vocab.SCIM_GROUPS: list(groups or []),
        vocab.SCIM_ACTIVE: active,
    }
    if operation == vocab.SCIM_OP_DELETE:
        subject = {vocab.SCIM_EXTERNAL_ID: external_id}
    return engine.apply_directory_event(
        directory["id"],
        {"operation": operation, "user": subject},
        webhook_token=directory["webhook_token"],
        actor="directory",
        source="test",
    )


def send_group(engine: FederationEngine, directory: dict, external_id: str, name: str) -> dict:
    """One SCIM Groups change through the webhook."""

    return engine.apply_directory_event(
        directory["id"],
        {
            "operation": vocab.SCIM_OP_UPDATE,
            "group": {vocab.SCIM_EXTERNAL_ID: external_id, "name": name},
        },
        webhook_token=directory["webhook_token"],
        actor="directory",
        source="test",
    )


# --------------------------------------------------------------------------- #
# the tenant assertion
# --------------------------------------------------------------------------- #


class TestTheTenantAssertion:
    def test_a_matching_organization_id_is_the_tenant(self):
        assert rules.assert_tenant(profile(TENANT), TENANT) == TENANT

    def test_a_profile_from_another_organization_is_refused(self):
        """The case the specification names twice.

        The profile below is a guest of one organization signing in against another. Its
        email is at the *expected* tenant's own domain, so every shortcut this workflow was
        told not to take would have accepted it.
        """

        with pytest.raises(rules.TenantAssertionFailed) as caught:
            rules.assert_tenant(profile("org_globex", "guest@northwind.example"), TENANT)
        assert caught.value.reason == rules.REASON_TENANT_MISMATCH
        assert caught.value.expected == TENANT
        assert caught.value.actual == "org_globex"

    def test_a_profile_with_no_organization_id_is_refused_even_at_the_right_domain(self):
        """The case an email domain exists to paper over, and must not.

        A profile with a perfect address and no tenant has to be refused. A rule that fell
        back to the domain would grant this one, which is the whole hazard the
        specification's extensibility note describes.
        """

        with pytest.raises(rules.TenantAssertionFailed) as caught:
            rules.assert_tenant(profile(None, "dana@northwind.example"), TENANT)
        assert caught.value.reason == rules.REASON_TENANT_ABSENT

    def test_the_expected_tenant_being_absent_is_also_refused(self):
        with pytest.raises(rules.TenantAssertionFailed) as caught:
            rules.assert_tenant(profile(TENANT), None)
        assert caught.value.reason == rules.REASON_TENANT_ABSENT

    def test_case_differences_are_the_same_tenant(self):
        """An IdP and a tenant record can disagree about an opaque slug's case."""
        assert rules.assert_tenant(profile("ORG_NORTHWIND"), TENANT) == "ORG_NORTHWIND"

    def test_surrounding_whitespace_is_not_a_different_tenant(self):
        """Trimmed before comparison, and the id is returned as the profile wrote it.

        The return value is deliberately the profile's own spelling rather than the
        normalised one, so a caller records the id the IdP sent even though the comparison
        ignored its whitespace.
        """

        assert rules.assert_tenant(profile(f"  {TENANT}  "), TENANT) == f"  {TENANT}  "

    def test_an_email_domain_is_never_returned_as_the_tenant(self):
        """The rule has one forbidden input, and this asserts it has no output.

        ``email_domain_of`` exists so the page can show which address a session belongs to.
        The assertion is the only thing that can produce a tenant, and it cannot read a
        domain, so no caller can reach a grant decision through the display helper.
        """

        assert rules.email_domain_of(profile(TENANT, "dana@Northwind.Example")) == (
            "northwind.example"
        )
        assert rules.email_domain_is_never_the_tenant() is False

    def test_the_assertion_signature_cannot_receive_a_domain(self):
        """A structural check, because a rule is easier to break than to read.

        ``assert_tenant`` takes a profile and an expected tenant and nothing else. If a
        later edit added an email domain as a third input, this fails - which is the point,
        because the parameter list is the ban.
        """

        source = (DOMAIN_PACKAGE / "sso_rules.py").read_text(encoding="utf-8")
        signature = source.split("def assert_tenant(", 1)[1].split(")", 1)[0]
        assert "email" not in signature.casefold()

    def test_a_tenant_record_is_required_and_reported_separately(self, engine, tenant):
        """Jev chose this over asserting the ids alone, at 0.99.

        Two organization ids that are equal and one this app has never heard of is a
        different problem from a wrong id, and a caller needs to hear which.
        """

        # The tenant exists, so the assertion and the membership check both pass.
        assert sign_in(engine)[vocab.ORGANIZATION_ID] == TENANT

        known = [{"organization_id": TENANT}]
        assert rules.tenant_is_known(known, TENANT) is True
        assert rules.tenant_is_known(known, "org_never_seen") is False
        assert rules.tenant_is_known(known, None) is False

    def test_the_membership_check_is_case_folded_too(self):
        assert rules.tenant_is_known([{"organization_id": TENANT}], "ORG_NORTHWIND") is True

    def test_no_session_is_written_when_the_assertion_fails(self, engine, tenant):
        before = len(engine.sessions())
        with pytest.raises(rules.TenantAssertionFailed):
            sign_in(engine, profile=profile("org_globex", "guest@northwind.example"))
        assert len(engine.sessions()) == before

    def test_a_refusal_writes_no_audit_row(self, db: AuditedDatabase, engine, tenant):
        """An audit row for a session that was never granted is a row a reader must discount."""
        with pytest.raises(rules.TenantAssertionFailed):
            sign_in(engine, profile=profile("org_globex"))
        assert db.audit_count(collection=vocab.SESSION_COLLECTION) == 0

    def test_a_session_names_the_tenant_it_asserted(self, engine, tenant):
        row = sign_in(engine)
        assert row[vocab.ASSERT_ORDER_FIELD] is True
        assert row[vocab.ORGANIZATION_ID] == TENANT
        assert row[vocab.EMAIL_DOMAIN_UNSAFE_FIELD] == vocab.EMAIL_DOMAIN_UNSAFE

    def test_the_refusal_reasons_are_the_four_the_rules_name(self):
        """Three tenant reasons and one code reason, and no others.

        A refusal that cannot say which of these it is would leave a caller with one generic
        failure to act on, and the whole point of naming them is that they need different
        responses.
        """

        assert set(rules.REFUSAL_REASONS) == {
            rules.REASON_TENANT_MISMATCH,
            rules.REASON_TENANT_ABSENT,
            rules.REASON_TENANT_NOT_A_MEMBER,
            rules.REASON_CODE_EXPIRED,
        }


# --------------------------------------------------------------------------- #
# the authorization code
# --------------------------------------------------------------------------- #


class TestTheAuthorizationCode:
    def test_the_bound_is_ten_minutes_because_the_research_quotes_ten(self):
        assert vocab.AUTHORIZATION_CODE_TTL_MINUTES == 10
        assert vocab.AUTHORIZATION_CODE_TTL_SECONDS == 600

    def test_a_code_within_ten_minutes_is_fresh(self):
        assert rules.code_state(rules.stamp(NOW - timedelta(minutes=9)), NOW) == (
            rules.CODE_STATE_FRESH
        )

    def test_a_code_past_ten_minutes_is_expired(self):
        assert rules.code_state(rules.stamp(NOW - timedelta(minutes=11)), NOW) == (
            rules.CODE_STATE_EXPIRED
        )

    def test_a_code_exactly_at_the_bound_is_expired(self):
        """The vendor's sentence is that the code "is valid for 10 minutes".

        A code at ten minutes and zero seconds has had all ten, so the boundary belongs to
        the past. Asserting it here is what stops a rounding change from quietly moving it.
        """

        assert rules.code_state(rules.stamp(NOW - timedelta(minutes=10)), NOW) == (
            rules.CODE_STATE_EXPIRED
        )

    def test_a_code_one_second_inside_the_bound_is_still_fresh(self):
        assert (
            rules.code_state(rules.stamp(NOW - timedelta(minutes=10, seconds=-1)), NOW)
            == rules.CODE_STATE_FRESH
        )

    def test_a_code_with_no_issue_time_is_unknown_not_fresh(self):
        """Silence about a credential's age is not evidence that it is young."""
        assert rules.code_state(None, NOW) == rules.CODE_STATE_UNKNOWN
        assert rules.code_state("", NOW) == rules.CODE_STATE_UNKNOWN
        assert rules.code_state("not a timestamp", NOW) == rules.CODE_STATE_UNKNOWN

    def test_a_code_stamped_in_the_future_is_treated_as_fresh(self):
        """A clock a few seconds ahead is a fact about the machine, not about the code."""
        assert rules.code_state(rules.stamp(NOW + timedelta(minutes=5)), NOW) == (
            rules.CODE_STATE_FRESH
        )

    def test_seconds_remaining_counts_down_and_floors_at_zero(self):
        assert rules.seconds_until_expiry(rules.stamp(NOW), NOW) == 600
        assert rules.seconds_until_expiry(rules.stamp(NOW - timedelta(minutes=5)), NOW) == 300
        assert rules.seconds_until_expiry(rules.stamp(NOW - timedelta(minutes=20)), NOW) == 0
        assert rules.seconds_until_expiry(None, NOW) is None

    def test_an_expired_code_is_refused_and_the_reason_says_which(self, engine, tenant):
        aged = NOW - timedelta(minutes=11)
        later = FederationEngine(engine.store, now=lambda: aged + timedelta(minutes=11))
        with pytest.raises(rules.TenantAssertionFailed) as caught:
            later.complete_sign_in(
                {
                    vocab.ORGANIZATION_PARAM: TENANT,
                    "issued_at": rules.stamp(aged),
                    "profile": profile(),
                },
                actor="dana",
                source="test",
            )
        assert caught.value.reason == rules.REASON_CODE_EXPIRED

    def test_an_expired_code_is_never_retried(self, store: RecordStore, tenant):
        """The refusal is terminal. A retry would spend a second attempt on a dead code.

        Asserted on the store rather than on a mock: a second attempt is observable as a
        second write, and the audit log is the product's guarantee, so "did it try again"
        has to be answerable from the log rather than from a test's own bookkeeping.
        """

        aged = NOW - timedelta(minutes=11)
        later = FederationEngine(store, now=lambda: NOW)
        with pytest.raises(rules.TenantAssertionFailed):
            later.complete_sign_in(
                {
                    vocab.ORGANIZATION_PARAM: TENANT,
                    "issued_at": rules.stamp(aged),
                    "profile": profile(),
                },
                actor="dana",
                source="test",
            )
        assert store.db.audit_count(collection=vocab.SESSION_COLLECTION) == 0

    def test_the_deadline_is_ten_minutes_after_the_issue_stamp(self):
        assert rules.code_deadline(rules.stamp(NOW)) == rules.stamp(NOW + timedelta(minutes=10))
        assert rules.code_deadline(None) is None

    def test_an_authorization_carries_its_own_deadline_and_ttl(self, engine, tenant):
        authorization = engine.begin_sign_in(
            {
                vocab.ORGANIZATION_PARAM: TENANT,
                vocab.REDIRECT_URI_PARAM: CALLBACK,
                "issuer": "https://idp.northwind.example/authorize",
            },
            actor="dana",
            source="test",
        )
        assert authorization["ttl_seconds"] == 600
        assert authorization["expires_at"] == rules.stamp(NOW + timedelta(minutes=10))
        assert authorization[vocab.EXPIRED_CODE_POLICY_FIELD] == vocab.EXPIRED_CODE_POLICY
        # No session exists yet. The assertion happens at the callback.
        assert "id" in authorization
        assert engine.sessions() == []


# --------------------------------------------------------------------------- #
# the redirect URI
# --------------------------------------------------------------------------- #


class TestTheRedirectUri:
    def test_a_registered_uri_is_accepted(self):
        assert rules.validate_redirect_uri(CALLBACK, [CALLBACK]) == CALLBACK

    def test_an_unregistered_uri_is_refused(self):
        """A callback that accepts an unregistered destination sends the code somewhere else."""
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            rules.validate_redirect_uri("https://attacker.example/steal", [CALLBACK])
        assert vocab.REDIRECT_URI_PARAM in caught.value.errors

    def test_a_relative_uri_is_refused(self):
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.validate_redirect_uri("/wf-084/callback", [CALLBACK])

    def test_an_empty_uri_is_refused(self):
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.validate_redirect_uri("", [CALLBACK])

    def test_a_path_prefix_is_not_a_match(self):
        """A redirect URI is a destination. A prefix match is a different destination."""
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.validate_redirect_uri(CALLBACK + "/deep", [CALLBACK])

    def test_a_different_host_is_not_a_match(self):
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.validate_redirect_uri("https://app.example.evil/wf-084/callback", [CALLBACK])

    def test_a_different_scheme_is_not_a_match(self):
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.validate_redirect_uri("http://app.example/wf-084/callback", [CALLBACK])

    def test_a_multi_tenant_tenant_gets_one_and_a_single_tenant_one_gets_several(self):
        """The evidence quotes both halves and they disagree on purpose."""
        assert rules.redirect_uri_limit(False) == vocab.MULTI_TENANT_MAX_REDIRECT_URIS == 1
        assert rules.redirect_uri_limit(True) == vocab.SINGLE_TENANT_MAX_REDIRECT_URIS == 10

    def test_the_limit_follows_the_tenancy_the_tenant_recorded(self, engine):
        multi = engine.create_organization(
            {vocab.ORGANIZATION_ID: "org_multi", "redirect_uris": [CALLBACK]}, source="test"
        )
        single = engine.create_organization(
            {
                vocab.ORGANIZATION_ID: "org_single",
                "redirect_uris": [CALLBACK],
                "single_tenant": True,
            },
            source="test",
        )
        assert multi["max_redirect_uris"] == 1
        assert single["max_redirect_uris"] == 10


# --------------------------------------------------------------------------- #
# the three identifiers
# --------------------------------------------------------------------------- #


class TestTheThreeIdentifiers:
    def test_the_three_are_named_apart_in_the_vocabulary(self):
        """The evidence: a connection is "for SAML or OIDC", a provider is "used for OAuth".

        Treating the three as interchangeable is the defect this table exists to prevent, so
        the test asserts the table rather than any one function's behaviour.
        """

        assert set(vocab.IDENTIFIER_PARAMS) == {"organization", "connection", "provider"}
        by_param = {entry["param"]: entry for entry in vocab.IDENTIFIER_JOBS}
        assert by_param["organization"]["specifies_tenant"] is True
        assert by_param["connection"]["specifies_connection"] is True
        assert by_param["provider"]["specifies_provider"] is True
        # Exactly one flag each, so none of them is a synonym for another.
        for entry in vocab.IDENTIFIER_JOBS:
            flags = [
                entry["specifies_tenant"],
                entry["specifies_connection"],
                entry["specifies_provider"],
            ]
            assert sum(flags) == 1

    def test_identifier_kind_reports_which_one_was_given(self):
        assert rules.identifier_kind("org_a", None, None) == "organization"
        assert rules.identifier_kind(None, "conn_1", None) == "connection"
        assert rules.identifier_kind(None, None, "okta") == "provider"
        assert rules.identifier_kind(None, None, None) is None

    def test_an_authorization_url_needs_one_of_the_three(self):
        with pytest.raises(rules.IdentitySettingsInvalid):
            rules.build_authorization_url(issuer="https://idp.example/authorize")

    def test_the_url_carries_each_identifier_under_its_own_name(self):
        url = rules.build_authorization_url(
            issuer="https://idp.example/authorize",
            organization="org_a",
            connection="conn_1",
            provider="okta",
            redirect_uri=CALLBACK,
            client_id="client_a",
            state="s1",
        )
        assert "organization=org_a" in url
        assert "connection=conn_1" in url
        assert "provider=okta" in url
        assert "state=s1" in url

    def test_a_connection_sign_in_names_the_connection(self, engine, tenant):
        connection = engine.create_connection(
            TENANT,
            {
                "name": "Northwind Okta",
                "protocol": vocab.PROTOCOL_SAML,
                vocab.REDIRECT_URI_PARAM: CALLBACK,
            },
            source="test",
        )
        authorization = engine.begin_sign_in(
            {
                vocab.CONNECTION_PARAM: connection["id"],
                "issuer": "https://idp.northwind.example/authorize",
                vocab.REDIRECT_URI_PARAM: CALLBACK,
            },
            source="test",
        )
        assert authorization["identifier"] == "connection"
        # The URL names the connection by this app's record id, which is what the IdP
        # matches against, rather than by the connection's display name.
        assert connection["id"] in authorization["authorization_url"]

    def test_an_organization_sign_in_names_the_organization(self, engine, tenant):
        authorization = engine.begin_sign_in(
            {
                vocab.ORGANIZATION_PARAM: TENANT,
                "issuer": "https://idp.northwind.example/authorize",
                vocab.REDIRECT_URI_PARAM: CALLBACK,
            },
            source="test",
        )
        assert authorization["identifier"] == "organization"
        assert f"organization={TENANT}" in authorization["authorization_url"]

    def test_an_unknown_organization_is_a_refusal_not_a_session(self, engine):
        with pytest.raises(rules.OrganizationNotFound):
            engine.begin_sign_in(
                {
                    vocab.ORGANIZATION_PARAM: "org_never",
                    "issuer": "https://idp.example/a",
                    vocab.REDIRECT_URI_PARAM: CALLBACK,
                },
                source="test",
            )

    def test_an_unknown_connection_is_a_refusal(self, engine, tenant):
        with pytest.raises(rules.ConnectionNotFound):
            engine.begin_sign_in(
                {
                    vocab.CONNECTION_PARAM: "conn_absent",
                    "issuer": "https://idp.example/a",
                    vocab.REDIRECT_URI_PARAM: CALLBACK,
                },
                source="test",
            )

    def test_a_sign_in_with_no_identifier_at_all_is_a_refusal(self, engine, tenant):
        with pytest.raises(rules.IdentitySettingsInvalid):
            engine.begin_sign_in(
                {"issuer": "https://idp.example/a", vocab.REDIRECT_URI_PARAM: CALLBACK},
                source="test",
            )

    def test_a_sign_in_with_no_issuer_is_a_refusal(self, engine, tenant):
        with pytest.raises(rules.IdentitySettingsInvalid):
            engine.begin_sign_in(
                {vocab.ORGANIZATION_PARAM: TENANT, vocab.REDIRECT_URI_PARAM: CALLBACK},
                source="test",
            )

    def test_only_saml_and_oidc_are_accepted(self, engine, tenant):
        assert vocab.PROTOCOLS == ("saml", "oidc")
        for protocol in vocab.PROTOCOLS:
            row = engine.create_connection(
                TENANT,
                {"name": f"C {protocol}", "protocol": protocol, vocab.REDIRECT_URI_PARAM: CALLBACK},
                source="test",
            )
            assert row["protocol"] == protocol
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            engine.create_connection(
                TENANT,
                {"name": "C ldap", "protocol": "ldap", vocab.REDIRECT_URI_PARAM: CALLBACK},
                source="test",
            )
        assert "protocol" in caught.value.errors


# --------------------------------------------------------------------------- #
# the IdP-initiated flow
# --------------------------------------------------------------------------- #


class TestTheIdpInitiatedFlow:
    def test_both_flows_are_one_callback(self):
        """The specification puts both in scope, and they are two doors into one room."""
        assert vocab.FLOWS == (vocab.FLOW_STAFF_INITIATED, vocab.FLOW_IDP_INITIATED)

    def test_an_idp_initiated_session_is_recorded_as_such(self, engine, tenant):
        row = engine.complete_sign_in(
            {
                vocab.ORGANIZATION_PARAM: TENANT,
                vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                vocab.RELAY_STATE_PARAM: CALLBACK,
                "issued_at": rules.stamp(NOW),
                "profile": profile(),
            },
            actor="dana",
            source="test",
        )
        assert row[vocab.FLOW_KEY] == vocab.FLOW_IDP_INITIATED

    def test_a_staff_initiated_session_is_recorded_as_such(self, engine, tenant):
        assert sign_in(engine)[vocab.FLOW_KEY] == vocab.FLOW_STAFF_INITIATED

    def test_an_idp_initiated_flow_still_asserts_the_tenant(self, engine, tenant):
        """The other door has the same lock, and this is the test that says so."""
        with pytest.raises(rules.TenantAssertionFailed):
            engine.complete_sign_in(
                {
                    vocab.ORGANIZATION_PARAM: TENANT,
                    vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                    "issued_at": rules.stamp(NOW),
                    "profile": profile("org_globex", "guest@northwind.example"),
                },
                actor="dana",
                source="test",
            )
        assert engine.sessions() == []

    def test_the_relay_state_redirect_uri_is_still_checked_against_the_registry(
        self, engine, tenant
    ):
        """A RelayState the tenant did not register is a redirect URI it should not honour."""
        with pytest.raises(rules.IdentitySettingsInvalid):
            engine.complete_sign_in(
                {
                    vocab.ORGANIZATION_PARAM: TENANT,
                    vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                    vocab.REDIRECT_URI_PARAM: "https://attacker.example/steal",
                    "issued_at": rules.stamp(NOW),
                    "profile": profile(),
                },
                actor="dana",
                source="test",
            )
        assert engine.sessions() == []

    def test_a_registered_relay_state_is_accepted(self, engine, tenant):
        row = engine.complete_sign_in(
            {
                vocab.ORGANIZATION_PARAM: TENANT,
                vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                vocab.REDIRECT_URI_PARAM: CALLBACK,
                "issued_at": rules.stamp(NOW),
                "profile": profile(),
            },
            actor="dana",
            source="test",
        )
        assert row[vocab.ORGANIZATION_ID] == TENANT


# --------------------------------------------------------------------------- #
# directory state decides access
# --------------------------------------------------------------------------- #


class TestDirectoryStateDecidesAccess:
    def test_the_four_named_providers_are_supported(self):
        assert vocab.SUPPORTED_PROVIDERS == ("okta", "microsoft_ad", "workday", "google_workspace")
        assert set(vocab.PROVIDER_LABELS) == set(vocab.SUPPORTED_PROVIDERS)

    def test_every_named_provider_can_be_connected(self, engine, tenant):
        for provider in vocab.SUPPORTED_PROVIDERS:
            row = engine.create_directory(TENANT, {vocab.PROVIDER_PARAM: provider}, source="test")
            assert row[vocab.PROVIDER_PARAM] == provider
            assert row["provider_label"] == vocab.PROVIDER_LABELS[provider]

    def test_an_unsupported_provider_is_a_refusal(self, engine, tenant):
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            engine.create_directory(TENANT, {vocab.PROVIDER_PARAM: "ldap"}, source="test")
        assert vocab.PROVIDER_PARAM in caught.value.errors

    def test_a_joiner_is_provisioned(self, engine, tenant):
        directory = make_directory(engine)
        row = send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        assert row[vocab.SCIM_EXTERNAL_ID] == "okta_ada"
        assert row[vocab.SCIM_GROUPS] == ["grp_sales"]
        assert row[vocab.SCIM_ACTIVE] is True
        assert row["state"] == vocab.USER_PROVISIONED
        assert row["operation"] == vocab.SCIM_OP_CREATE
        assert row["emails"] == ["okta_ada@northwind.example"]

    def test_only_the_researched_attributes_are_stored(self, engine, tenant):
        """A directory user must not accumulate whatever the provider happened to send.

        The specification's admin objects are "with configurable attributes", so the stored
        row describes this product's user rather than the provider's payload.
        """

        directory = make_directory(engine)
        engine.apply_directory_event(
            directory["id"],
            {
                "operation": vocab.SCIM_OP_CREATE,
                "user": {
                    vocab.SCIM_EXTERNAL_ID: "okta_ada",
                    vocab.SCIM_EMAILS: ["ada@northwind.example"],
                    "someVendorOnlyField": "do not store me",
                    "department": "Sales",
                },
            },
            webhook_token=directory["webhook_token"],
            source="test",
        )
        stored = engine.read_directory_user(engine.directory_users(directory["id"])[0]["id"])
        assert "someVendorOnlyField" not in stored
        assert "department" not in stored

    def test_a_mover_is_updated_rather_than_duplicated(self, engine, tenant):
        """The specification's "When a user's attribute has changed (account update)"."""
        directory = make_directory(engine)
        first = send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        second = send_user(engine, directory, "okta_ada", groups=["grp_admin"])
        assert first["id"] == second["id"]
        assert second["state"] == vocab.USER_UPDATED
        assert second[vocab.SCIM_GROUPS] == ["grp_admin"]
        assert len(engine.directory_users(directory["id"])) == 1

    def test_a_leaver_is_deprovisioned_and_loses_their_row(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_kai")
        removed = send_user(engine, directory, "okta_kai", operation=vocab.SCIM_OP_DELETE)
        assert removed["state"] == vocab.USER_DEPROVISIONED
        assert engine.directory_users(directory["id"]) == []
        # Soft-deleted, so the history is still readable.
        assert len(engine.directory_users(directory["id"], include_deprovisioned=True)) == 1

    def test_a_deprovision_revokes_the_sessions_the_user_holds(self, engine, tenant):
        """The issue's own words: a deprovision "must remove access, not merely mark the
        user inactive in a way a session check ignores"."""

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        session = sign_in(engine, profile=profile(TENANT, "ada@northwind.example"))
        # The session is bound to the directory user, so a deprovision of that user has
        # something to revoke. Without the directory row the session would be unattached and
        # there would be nothing to take away.
        assert session["directory_user_id"] == "okta_ada"
        assert engine.is_session_live(session["id"]) is True

        removed = send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)
        assert removed["sessions_revoked"] == 1
        assert session["id"] in removed["revoked_session_ids"]
        assert engine.is_session_live(session["id"]) is False

    def test_a_deprovision_writes_a_row_for_everything_it_removed(self, engine, tenant):
        """The product guarantee: the change and its audit row share a transaction.

        Read through the store the engine itself writes to, rather than a second connection
        at the same path, so the assertion is about this transaction and not about whether
        two readers see each other.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        session = sign_in(engine, profile=profile(TENANT, "ada@northwind.example"))
        user_id = engine.directory_users(directory["id"])[0]["id"]
        send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)

        audit = engine.store.audit
        removals = [
            row
            for row in audit(collection=vocab.DIRECTORY_USER_COLLECTION)
            if row["record_id"] == user_id
        ]
        assert any(row["action"] == "delete" for row in removals)

        revocations = [
            row
            for row in audit(collection=vocab.SESSION_COLLECTION)
            if row["record_id"] == session["id"]
        ]
        assert revocations, "the session revocation wrote no audit row"
        # The trail is newest-first, and this session has exactly two rows: the insert that
        # granted it and the update that revoked it. Asserting on the newest rather than on
        # an index, because the order is what the audit contract guarantees and an index is
        # an assumption about this particular row's history.
        assert revocations[0]["action"] == "update"
        assert revocations[0]["after_state"]["revoked"] is True
        assert revocations[0]["source"] == "test"

    def test_a_refusal_writes_no_audit_row(self, engine, tenant, store):
        """The other half of the guarantee: a request that changed nothing leaves no row."""
        with pytest.raises(rules.TenantAssertionFailed):
            sign_in(engine, profile=profile("org_globex"))
        assert store.db.audit_count(collection=vocab.SESSION_COLLECTION) == 0

    def test_a_deprovision_names_what_it_is_doing(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_kai")
        removed = send_user(engine, directory, "okta_kai", operation=vocab.SCIM_OP_DELETE)
        assert removed[vocab.DEPROVISION_DEFINITION_FIELD] == vocab.DEPROVISION_DEFINITION
        assert removed[vocab.DEPROVISION_POLICY_FIELD] == vocab.DEPROVISION_POLICY
        assert removed[vocab.SCIM_OPERATIONS_FIELD][vocab.SCIM_OP_DELETE]

    def test_a_deprovision_of_an_unknown_user_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        with pytest.raises(rules.DirectoryUserNotFound):
            send_user(engine, directory, "okta_never", operation=vocab.SCIM_OP_DELETE)

    def test_the_three_scim_operations_are_the_three_the_research_quotes(self):
        assert vocab.SCIM_OPERATIONS == ("create", "update", "delete")
        assert vocab.SCIM_OPERATION_LABELS[vocab.SCIM_OP_CREATE] == (
            "Provisioning an identity for a user (account creation)"
        )
        assert vocab.SCIM_OPERATION_LABELS[vocab.SCIM_OP_UPDATE] == (
            "When a user's attribute has changed (account update)"
        )
        assert vocab.SCIM_OPERATION_LABELS[vocab.SCIM_OP_DELETE] == (
            "Deprovisioning a user from your app (account deletion)"
        )

    def test_an_unknown_operation_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            engine.apply_directory_event(
                directory["id"],
                {"operation": "upsert", "user": {vocab.SCIM_EXTERNAL_ID: "x"}},
                webhook_token=directory["webhook_token"],
                source="test",
            )
        assert "operation" in caught.value.errors

    def test_a_user_with_no_external_id_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            engine.apply_directory_event(
                directory["id"],
                {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EMAILS: ["a@b.example"]}},
                webhook_token=directory["webhook_token"],
                source="test",
            )
        assert vocab.SCIM_EXTERNAL_ID in caught.value.errors

    def test_a_change_naming_neither_user_nor_group_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        with pytest.raises(rules.IdentitySettingsInvalid):
            engine.apply_directory_event(
                directory["id"],
                {"operation": vocab.SCIM_OP_UPDATE},
                webhook_token=directory["webhook_token"],
                source="test",
            )


# --------------------------------------------------------------------------- #
# groups are an input to a decision
# --------------------------------------------------------------------------- #


class TestGroupsInformAccess:
    def test_a_group_is_derived_from_the_directory_not_copied_into_it(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_user(engine, directory, "okta_robin", groups=["grp_sales", "grp_admin"])
        send_group(engine, directory, "grp_sales", "Sales")
        sales = next(
            row
            for row in engine.directory_groups(directory["id"])
            if row[vocab.SCIM_EXTERNAL_ID] == "grp_sales"
        )
        assert sorted(sales["members"]) == ["okta_ada", "okta_robin"]
        assert sales[vocab.GROUP_ROLE_KEY] == vocab.GROUP_ROLE

    def test_a_deactivated_user_drops_out_of_the_group(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        send_user(engine, directory, "okta_ada", groups=["grp_sales"], active=False)
        send_group(engine, directory, "grp_sales", "Sales")
        sales = next(
            row
            for row in engine.directory_groups(directory["id"])
            if row[vocab.SCIM_EXTERNAL_ID] == "grp_sales"
        )
        assert sales["members"] == []

    def test_membership_is_deduplicated_so_a_grant_is_not_counted_twice(self):
        assert rules.normalise_groups(["a", "b", "a", " b "]) == ["a", "b"]
        assert rules.normalise_groups(None) == []
        assert rules.normalise_groups([{"id": "a"}, {"value": "b"}]) == ["a", "b"]

    def test_a_mapped_group_grants_its_role(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        sales = next(row for row in engine.directory_groups(directory["id"]))
        engine.set_access_rule(sales["id"], vocab.RULE_ROLE_MEMBER, source="test")
        access = engine.effective_access(directory["id"])
        assert access["users"][0]["access"]["role"] == vocab.RULE_ROLE_MEMBER
        assert access["users"][0]["access"]["source"] == "directory"

    def test_the_strongest_matched_group_wins_whatever_the_order(self, engine, tenant):
        """`DERIVED_GROUP_RESOLUTION`. A directory reports a set, not a sequence."""
        by_group = {
            "grp_sales": {"role": vocab.RULE_ROLE_MEMBER, vocab.SCIM_EXTERNAL_ID: "grp_sales"},
            "grp_admin": {"role": vocab.RULE_ROLE_ADMIN, vocab.SCIM_EXTERNAL_ID: "grp_admin"},
        }
        first = rules.resolve_access(["grp_sales", "grp_admin"], by_group)
        second = rules.resolve_access(["grp_admin", "grp_sales"], by_group)
        assert first["role"] == second["role"] == vocab.RULE_ROLE_ADMIN

    def test_membership_named_by_either_group_id_resolves(self, engine, tenant):
        """A SCIM user names the directory's group id; a rule here holds the record id.

        Keying on only one of the two makes every real payload match nothing, and an access
        page showing no access for an admin is worse than no access page.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        group = engine.directory_groups(directory["id"])[0]
        engine.set_access_rule(group["id"], vocab.RULE_ROLE_ADMIN, source="test")
        access = engine.effective_access(directory["id"])
        assert access["users"][0]["access"]["granted"] is True
        assert access["users"][0]["access"]["role"] == vocab.RULE_ROLE_ADMIN

    def test_a_user_in_no_mapped_group_is_granted_nothing(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_kai", groups=["grp_unmapped"])
        access = engine.effective_access(directory["id"])
        assert access["users"][0]["access"]["granted"] is False
        assert access["users"][0]["access"]["role"] is None
        assert access["unmapped_groups"] == ["grp_unmapped"]

    def test_there_is_no_field_a_human_can_set_instead(self, engine, tenant):
        """`DERIVED_NO_MANUAL_OVERRIDE`, asserted on the write surface.

        A manual override is not merely discouraged by the page. There is no route, no
        engine method and no stored field that grants a person access outside the directory.
        This is the test that says so, and it fails if somebody adds one.
        """

        forbidden = ("manual_override", "grant_access", "set_user_role", "force_access")
        source = (DOMAIN_PACKAGE / "sso_engine.py").read_text(encoding="utf-8")
        for name in forbidden:
            assert f"def {name}" not in source, f"{name} is a manual access override"

        directory = make_directory(engine)
        send_user(engine, directory, "okta_kai")
        access = engine.effective_access(directory["id"])
        assert access[vocab.OVERRIDE_REFUSAL_FIELD] == vocab.OVERRIDE_REFUSAL_VALUE
        # The refusal names the harm the specification describes, so a reader knows what the
        # override would have caused rather than only that it is unavailable.
        assert "survive the next directory change" in vocab.OVERRIDE_REFUSAL

    def test_an_unknown_role_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        group = engine.directory_groups(directory["id"])[0]
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            engine.set_access_rule(group["id"], "superuser", source="test")
        assert "role" in caught.value.errors

    def test_the_three_roles_are_the_only_ones(self):
        assert vocab.RULE_ROLES == ("admin", "member", "auditor")

    def test_an_absent_active_attribute_reads_as_inactive(self):
        """Silence is not employment."""
        assert rules.is_active({vocab.SCIM_ACTIVE: True}) is True
        assert rules.is_active({vocab.SCIM_ACTIVE: False}) is False
        assert rules.is_active({}) is False
        assert rules.is_active({"active": "true"}) is True  # a stored string is truthy, not absent


# --------------------------------------------------------------------------- #
# the two delivery paths
# --------------------------------------------------------------------------- #


class TestTheTwoDeliveryPaths:
    def test_both_are_offered_and_one_is_chosen_with_a_reason(self):
        assert vocab.DELIVERY_METHODS == ("webhook", "events_api")
        assert vocab.CHOSEN_DELIVERY_METHOD == "webhook"
        assert "Events API" in vocab.DELIVERY_REASON

    def test_a_directory_reports_which_path_it_uses(self, engine, tenant):
        directory = make_directory(engine)
        assert directory[vocab.CHOSEN_DELIVERY_METHOD_FIELD] == "webhook"
        assert directory[vocab.DIRECTORY_TRUTH_FIELD] == vocab.DIRECTORY_TRUTH_STATEMENT

    def test_a_post_without_the_token_is_refused(self, engine, tenant):
        """`DERIVED_WEBHOOK_AUTHENTICATION`. The cheapest attack here is a deprovision."""
        directory = make_directory(engine)
        with pytest.raises(rules.DirectoryNotFound):
            engine.apply_directory_event(
                directory["id"],
                {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EXTERNAL_ID: "x"}},
                source="test",
            )

    def test_a_post_with_the_wrong_token_is_refused(self, engine, tenant):
        directory = make_directory(engine)
        with pytest.raises(rules.DirectoryNotFound):
            engine.apply_directory_event(
                directory["id"],
                {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EXTERNAL_ID: "x"}},
                webhook_token="not-the-token",
                source="test",
            )

    def test_the_token_is_returned_once_and_never_again(self, engine, tenant):
        created = make_directory(engine)
        assert created["webhook_token"]
        read_back = engine.read_directory(created["id"])
        assert "webhook_token" not in read_back
        assert created["webhook_token"] not in str(read_back)

    def test_two_directories_get_two_different_tokens(self, engine, tenant):
        first = make_directory(engine)
        second = make_directory(engine)
        assert first["webhook_token"] != second["webhook_token"]

    def test_the_token_of_one_directory_does_not_work_on_another(self, engine, tenant):
        first = make_directory(engine)
        second = make_directory(engine)
        with pytest.raises(rules.DirectoryNotFound):
            engine.apply_directory_event(
                second["id"],
                {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EXTERNAL_ID: "x"}},
                webhook_token=first["webhook_token"],
                source="test",
            )

    def test_the_events_api_pull_reaches_the_same_records(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        pulled = engine.poll_events(directory["id"], source="test")
        assert pulled["delivery"] == "events_api"
        assert pulled["count"] == 1
        assert pulled["applied"][0][vocab.SCIM_EXTERNAL_ID] == "okta_ada"

    def test_the_pull_does_not_apply_one_event_twice(self, engine, tenant):
        """A pull that over-replays gets a counted skip, not a second deprovision."""
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_user(engine, directory, "okta_robin", groups=["grp_sales"])
        first = engine.poll_events(directory["id"], source="test")
        assert first["count"] == 2
        second = engine.poll_events(directory["id"], source="test")
        assert second["count"] == 0
        assert second["skipped"] == 2
        assert len(engine.directory_users(directory["id"])) == 2

    def test_a_cursor_narrows_the_pull(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada")
        pulled = engine.poll_events(directory["id"], source="test")
        cursor = pulled["cursor"]
        assert cursor
        send_user(engine, directory, "okta_robin")
        later = engine.poll_events(directory["id"], since=cursor, source="test")
        assert [row[vocab.SCIM_EXTERNAL_ID] for row in later["applied"]] == ["okta_robin"]

    def test_a_cursor_does_not_skip_an_event_sharing_a_millisecond(self, engine, tenant):
        """The stamp alone is not a safe cursor, because a batch shares one millisecond.

        The engine's clock is frozen, so every event in this test carries an identical
        stamp. A cursor that filtered on the timestamp would resume after that stamp and
        drop the remaining events - and a dropped event is a leaver who keeps access.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada")
        send_user(engine, directory, "okta_robin")
        first = engine.poll_events(directory["id"], source="test")
        assert first["count"] == 2

        send_user(engine, directory, "okta_kai")
        later = engine.poll_events(directory["id"], since=first["cursor"], source="test")
        assert [row[vocab.SCIM_EXTERNAL_ID] for row in later["applied"]] == ["okta_kai"]

    def test_an_unrecognised_cursor_replays_rather_than_skipping(self, engine, tenant):
        """A cursor this build cannot read replays; it never skips.

        The applied-event marker then skips what was already done, so a joiner is not lost
        and a deprovision is not applied twice. Skipping would be the unrecoverable
        direction, so an unknown cursor resolves to the start of the log.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada")
        engine.poll_events(directory["id"], source="test")
        send_user(engine, directory, "okta_robin")

        replay = engine.poll_events(directory["id"], since="not-a-cursor", source="test")
        assert [row[vocab.SCIM_EXTERNAL_ID] for row in replay["applied"]] == ["okta_robin"]
        assert replay["skipped"] == 1

    def test_a_pull_replaying_a_deprovision_does_not_fail(self, engine, tenant):
        """A pull that has lost its cursor re-reads removals it already applied.

        This is the case the HTTP verification found and the earlier domain tests missed.
        The user is already gone, so there is nothing left to delete, and deleting a
        soft-deleted row raises from the store. Reported as a 404, a stale pull would be
        unusable - and a pull that cannot be re-run stops receiving directory changes.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        engine.poll_events(directory["id"], source="test")
        send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)

        # No cursor, so the pull reads from the start over a window holding that removal.
        replayed = engine.poll_events(directory["id"], source="test")
        assert replayed["skipped"] >= 1
        assert engine.directory_users(directory["id"]) == []

    def test_a_directory_naming_a_departed_person_again_provisions_a_new_row(self, engine, tenant):
        """A re-hire is a new row, not an update to a deleted one.

        The directory is the source of truth, so its latest word is the person's current
        state. Writing it onto the deleted row would raise from the store, and a replayed
        Events API pull hits exactly this: it re-reads the create that preceded a deprovision
        it already applied. The two rows stay side by side, so the trail keeps both accounts.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)
        rehired = send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        assert rehired["state"] == vocab.USER_PROVISIONED
        assert rehired["operation"] == vocab.SCIM_OP_CREATE
        assert len(engine.directory_users(directory["id"])) == 1
        # Both rows are still readable, so the departure is not erased by the return.
        assert len(engine.directory_users(directory["id"], include_deprovisioned=True)) == 2

    def test_a_pull_can_be_run_repeatedly_over_a_window_with_a_deprovision(self, engine, tenant):
        """The whole replay sequence, which is what the HTTP verification exercises.

        A pull that worked once and failed the second time is not a working pull: a tenant
        that polls on a timer would stop applying directory changes after the first retry.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        send_user(engine, directory, "okta_kai")
        send_user(engine, directory, "okta_kai", operation=vocab.SCIM_OP_DELETE)

        for _ in range(3):
            pulled = engine.poll_events(directory["id"], source="test")
            assert pulled is not None
            assert pulled["cursor"].startswith("events:")

    def test_a_repeated_deprovision_says_it_happened_already(self, engine, tenant):
        """The idempotent path reports rather than pretending to remove again.

        It still revokes if a session appeared afterwards, or a leaver could sign in again
        after their removal and keep access.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        first = send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)
        assert first["sessions_revoked"] == 0

        again = send_user(engine, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)
        assert again.get("already_deprovisioned") is True
        assert again["sessions_revoked"] == 0
        assert again["state"] == vocab.USER_DEPROVISIONED

    def test_a_post_of_a_user_this_store_never_held_is_still_a_404(self, engine, tenant):
        """Idempotence must not turn "never existed" into a success.

        The two are different situations and a provider needs to tell them apart: a user who
        was never provisioned is a mistake, and a user already removed is not.
        """

        directory = make_directory(engine)
        with pytest.raises(rules.DirectoryUserNotFound):
            send_user(engine, directory, "okta_never", operation=vocab.SCIM_OP_DELETE)

    def test_a_directory_reports_the_provider_label_not_its_id(self, engine, tenant):
        """The page shows what a provider is called, and the id stays in the data.

        `okta` is what the store holds and "Okta" is what a reader needs. Serving the
        label keeps the page from having to carry its own copy of the mapping, which is the
        drift this table exists to prevent.
        """

        directory = make_directory(engine)
        assert directory[vocab.PROVIDER_PARAM] == vocab.PROVIDER_OKTA
        assert directory["provider_label"] == "Okta"

    def test_a_read_only_engine_reads_the_stored_records(self, engine, tenant, store):
        """The summary and the projections read what is stored rather than a cached copy.

        A test that builds a second engine over the same store and reads through it is the
        only way to know a projection is derived rather than accumulated in memory.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", groups=["grp_sales"])
        send_group(engine, directory, "grp_sales", "Sales")
        group = engine.directory_groups(directory["id"])[0]
        engine.set_access_rule(group["id"], vocab.RULE_ROLE_MEMBER, source="test")

        fresh = FederationEngine(store, now=lambda: NOW)
        assert fresh.read_directory(directory["id"])["users"] == 1
        assert fresh.directory_groups(directory["id"])[0]["member_count"] == 1
        assert fresh.effective_access(directory["id"])["users"][0]["access"]["granted"] is True
        assert fresh.summary()["access_rules"] == 1

    def test_reading_a_user_of_another_workflow_is_a_404(self, engine, tenant, store):
        """A feature may not read across into another workflow's collection.

        The store holds every feature's rows in one table, so the collection check is the
        only thing standing between this workflow and somebody else's record.
        """

        other = store.create("some_other_feature", {"name": "not ours"}, source="test")
        with pytest.raises(rules.DirectoryUserNotFound):
            engine.read_directory_user(str(other["id"]))
        with pytest.raises(rules.DirectoryUserNotFound):
            engine.read_directory_group(str(other["id"]))

    def test_a_deactivated_user_loses_their_access_and_their_session(self, engine, tenant):
        """Deactivation is directory state, so it takes effect on the next read.

        A session behind a user the directory has deactivated reads as not live, which is
        the same property a deprovision relies on and the case between provisioned and gone.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        session = sign_in(engine, profile=profile(TENANT, "ada@northwind.example"))
        assert engine.is_session_live(session["id"]) is True

        send_user(engine, directory, "okta_ada", address="ada@northwind.example", active=False)
        assert engine.is_session_live(session["id"]) is False

    def test_a_session_with_no_directory_user_stays_live(self, engine, tenant):
        """Provisioning and authentication are separate, as the research has them.

        A staff member who has authenticated but is not yet in any directory has no
        directory state that could revoke their session, so the assertion alone stands.
        Refusing them here would fold provisioning into authentication.
        """

        session = sign_in(engine)
        assert session["directory_user_id"] is None
        assert engine.is_session_live(session["id"]) is True

    def test_a_session_for_a_departed_user_stops_resolving(self, engine, tenant):
        """A session whose directory user is gone entirely grants nothing.

        Distinct from a revocation: the row was never marked revoked, because the deprovision
        that removed the user found no session to revoke. The answer is the same.
        """

        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada", address="ada@northwind.example")
        session = sign_in(engine, profile=profile(TENANT, "ada@northwind.example"))
        assert engine.is_session_live(session["id"]) is True

        # Remove the directory user behind the session's back, as a directory that dropped
        # the record without telling this app would.
        user = engine.directory_users(directory["id"])[0]
        engine.store.delete(str(user["id"]), source="test")
        assert engine.is_session_live(session["id"]) is False

    def test_reading_an_unknown_authorization_is_a_400(self, engine, tenant):
        with pytest.raises(rules.IdentitySettingsInvalid):
            sign_in(engine, authorization_id="auth_absent")

    def test_a_disabled_directory_refuses_a_change(self, engine, tenant):
        directory = make_directory(engine)
        engine.store.update(directory["id"], {"sync_enabled": False}, source="test")
        with pytest.raises(rules.IdentitySettingsInvalid) as caught:
            send_user(engine, directory, "okta_ada")
        assert "sync_enabled" in caught.value.errors

    def test_the_cursor_is_a_position_rather_than_a_time(self, engine, tenant):
        directory = make_directory(engine)
        send_user(engine, directory, "okta_ada")
        pulled = engine.poll_events(directory["id"], source="test")
        assert pulled["cursor"].startswith("events:")
        assert pulled["cursor"] == "events:1"

    def test_a_pull_of_a_disabled_directory_is_a_refusal(self, engine, tenant):
        directory = make_directory(engine)
        engine.store.update(directory["id"], {"sync_enabled": False}, source="test")
        with pytest.raises(rules.IdentitySettingsInvalid):
            engine.poll_events(directory["id"], source="test")


# --------------------------------------------------------------------------- #
# the build's own boundaries
# --------------------------------------------------------------------------- #


class TestTheBuildsBoundaries:
    def test_the_domain_imports_the_store_and_nothing_else_out_of_dsr(self):
        """The rule the brief states: the domain module imports nothing but the store.

        Read from the tree rather than the text, so a re-export or an alias is still seen as
        what it is. ``dsr.api`` in particular would reintroduce the coupling this layering
        exists to remove.
        """

        allowed = {"dsr.store", "dsr.security_governance"}
        for path in sorted(DOMAIN_PACKAGE.glob("sso_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dsr"):
                    assert node.module in allowed, f"{path.name} imports {node.module}"
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("dsr"):
                            assert alias.name in allowed, f"{path.name} imports {alias.name}"

    def test_no_sso_module_opens_a_database_of_its_own(self):
        """The audit guarantee is that the row and the change share a transaction."""
        for path in sorted(DOMAIN_PACKAGE.glob("sso_*.py")):
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, f"{path.name} imports sqlite3"
            assert "sqlite3.connect" not in text, f"{path.name} opens its own connection"

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        source = (BACKEND / "dsr" / "features" / f"{FEATURE_MODULE.split('.')[-1]}.py").read_text(
            encoding="utf-8"
        )
        assert "from dsr.deps import" in source
        assert "from dsr.api import" not in source

    def test_the_feature_module_opens_no_database_of_its_own(self):
        source = (BACKEND / "dsr" / "features" / f"{FEATURE_MODULE.split('.')[-1]}.py").read_text(
            encoding="utf-8"
        )
        assert "import sqlite3" not in source
        assert "sqlite3.connect" not in source

    def test_the_feature_exports_its_id_and_a_ticket_derived_prefix(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == FEATURE_ID
        assert module.FEATURE["ticket"] == "WF-084"
        assert module.router.prefix == PREFIX
        for key in ("id", "name", "description"):
            assert module.FEATURE[key]

    def test_the_feature_ships_a_seed(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert callable(module.seed)

    def test_every_seed_string_is_encodable_by_cp1252(self, memory_db: AuditedDatabase):
        """A single RIGHTWARDS ARROW in a recovered feature broke the whole seeder.

        The seeder prints each feature's return string to a Windows console. One character
        outside cp1252 and the print raises, which aborts the seed for every feature after
        it. So the assertion is the encode itself, not a check for a known-bad character.
        """

        module = importlib.import_module(FEATURE_MODULE)
        line = module.seed(
            memory_db, {"now": NOW, "room_ids": [("room_a", "Northwind"), ("room_b", "Contoso")]}
        )
        assert line
        line.encode("cp1252", errors="strict")
        assert line.isascii()

    def test_the_seed_prints(self, memory_db: AuditedDatabase, capsys):
        """Not just encodable: actually printable on the console that runs the seeder."""
        module = importlib.import_module(FEATURE_MODULE)
        line = module.seed(
            memory_db, {"now": NOW, "room_ids": [("room_a", "Northwind"), ("room_b", "Contoso")]}
        )
        print(line)
        assert capsys.readouterr().out.strip() == line.strip()

    def test_the_seed_returns_nothing_without_a_room(self, memory_db: AuditedDatabase):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.seed(memory_db, {"now": NOW, "room_ids": []}) == ""

    def test_the_seed_describes_states_that_are_not_all_successes(self, memory_db, capsys):
        """A feature whose page is empty in the demo is a feature nobody can review.

        The line has to name a deprovision, because the specification calls deprovisioning
        "a process of removing a user from an app" and a demo that never removes one cannot
        show a removal.
        """

        module = importlib.import_module(FEATURE_MODULE)
        line = module.seed(
            memory_db, {"now": NOW, "room_ids": [("room_a", "Northwind"), ("room_b", "Contoso")]}
        )
        assert "deprovisioned" in line
        assert "revoked" in line

    def test_the_seed_shows_a_leaver_losing_a_live_session(self, memory_db, store):
        """The headline automation has to be visible, not merely claimed.

        A demo that deprovisions somebody with no session would report zero revocations and
        would show the least interesting version of the operation.
        """

        module = importlib.import_module(FEATURE_MODULE)
        module.seed(memory_db, {"now": NOW, "room_ids": [("room_a", "Northwind")]})
        engine = FederationEngine(store, now=lambda: NOW)
        assert engine.summary()["live_sessions"] == 0
        assert engine.summary()["revoked_sessions"] == 1
        assert engine.summary()["deprovisioned_users"] == 1

    def test_every_decision_records_the_alternative_it_rejected(self):
        """A derivation with no rejected alternative is a guess in a derivation's clothes."""
        assert inferences.count() == len(inferences.DECISIONS)
        for decision in inferences.describe():
            assert decision["question"]
            assert decision["left_open_by"]
            assert len(decision["options"]) >= 2
            assert decision["chosen"] in decision["options"]
            assert decision["rejected_because"]
            assert decision["cost_of_the_choice"]

    def test_the_membership_decision_names_its_jev_audit_id(self):
        decision = inferences.describe_one("DERIVED_TENANT_MEMBERSHIP_CHECK")
        assert decision["chosen"] == "assert_and_require_a_tenant_record"
        assert decision["jev_audit_id"].startswith("jev-")
        assert decision["jev_verdict"] == "pass"
        assert decision["jev_confidence"] >= 0.75

    def test_an_unknown_decision_is_an_empty_mapping(self):
        assert inferences.describe_one("NOPE") == {}

    def test_the_branch_touches_no_shared_file(self):
        """The CI guard refuses a branch that edits one, and this says why locally.

        The guard's ``--base`` form needs a committed diff, so on a work in progress it has
        nothing to report and says so. That is not a pass and not a fail: the working tree is
        checked here instead, and the committed form runs once the branch has commits. A
        clean tree is the state this branch is in for most of its life, and a test that only
        passed after a commit would be a test that never ran.
        """

        if _shared_guard_needs_commits():
            pytest.skip("the committed guard runs once this branch has commits on it")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "check_feature_diff.py"),
                "--base",
                "origin/main",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        output = result.stdout + result.stderr
        if "unknown revision" in output or "no changed files" in output:
            pytest.skip("no committed diff against origin/main in this checkout")
        assert "OK" in result.stdout, output

    def test_the_working_tree_touches_no_shared_file(self):
        """The guard above, for a branch whose work is not committed yet.

        Walks the files this workflow owns and refuses if any of them is in the shared list.
        That is the check that can run at every point in a feature's life, and it is the one
        that has to pass before the first commit.
        """

        sys.path.insert(0, str(ROOT))
        from tools.contract import SHARED

        owned = [
            *(
                DOMAIN_PACKAGE / name
                for name in (
                    "sso_vocabulary.py",
                    "sso_rules.py",
                    "sso_inferences.py",
                    "sso_engine.py",
                )
            ),
            BACKEND
            / "dsr"
            / "features"
            / "wf084_federate_staff_sso_and_auto_provision_via_scim.py",
            BACKEND / "tests" / "test_wf084.py",
            BACKEND / "tests" / "test_wf084_http.py",
        ]
        rel = lambda path: path.resolve().relative_to(ROOT).as_posix()  # noqa: E731

        for path in owned:
            if not path.exists():
                continue
            assert rel(path) not in SHARED, f"{rel(path)} is a shared file"

        # And the guard itself, fed the files that do not exist yet, must still accept them.
        present = [rel(path) for path in owned if path.exists()]
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_feature_diff.py"), "--files", *present],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheVocabulary:
    def test_every_collection_is_namespaced_to_the_ticket(self):
        for name in vocab.ALL_COLLECTIONS:
            assert name.startswith("wf084_"), name

    def test_the_tenant_field_is_the_organization_id_and_not_a_domain(self):
        assert vocab.ORGANIZATION_ID == "organization_id"
        assert vocab.vocabulary_payload()["tenant_is_never"] == "email domain"

    def test_the_payload_names_the_surfaces_this_build_does_not_ship(self):
        """Two of the specification's ten product surfaces are somebody else's dashboard."""
        rows = vocab.vocabulary_payload()["replaces_hosted_surface"]
        assert len(rows) == 2
        for row in rows:
            assert row["owner"] == "vendor dashboard"
            assert row["this_build_offers"]

    def test_the_payload_says_the_directory_is_the_source_of_truth(self):
        payload = vocab.vocabulary_payload()
        assert payload[vocab.DIRECTORY_TRUTH_FIELD] == vocab.DIRECTORY_TRUTH_STATEMENT
        assert "function of directory state" in vocab.DIRECTORY_TRUTH_STATEMENT

    def test_the_limitation_names_the_assertion(self):
        assert "organization id" in vocab.LIMITATION
        assert "never" in vocab.LIMITATION

    def test_every_served_field_is_defined(self):
        """A response built from an undefined constant is a 500 on a live route."""
        payload = vocab.vocabulary_payload()
        assert payload
        assert vocab.vocabulary_payload() == payload  # stable across calls
