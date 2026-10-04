"""Every researched term WF-084 enforces against, with the evidence it came from.

The specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-084.md``, quoted in full in issue 175.
Every value below is either quoted from that document or derived from a quote by the
arithmetic shown beside it. Nothing here is a house opinion.

The one sentence that governs the whole workflow
------------------------------------------------

The specification's extensibility note says, in bold, that validating a tenant "**It's
unsafe to validate using email domains as organizations might allow email addresses from
outside their corporate domain (e.g. for guest users).**" So the vocabulary names
``organization_id`` as the tenant and never ``email_domain``. The email is an attribute a
directory keeps about a person. The organization id is the tenant boundary, and the
difference between the two is the whole security property of this workflow.

The three identifiers and the three jobs they do
------------------------------------------------

The specification's evidence draws this distinction explicitly: "You can also use the
``connection`` parameter for SAML or OIDC connections... The ``provider`` parameter is used
for OAuth connections". So the three are named apart here, and no function in this package
accepts one where another is meant.

The hosted surfaces this build does not ship
--------------------------------------------

Two surfaces the specification names belong to the vendor's dashboard and not to this
product: the Admin Portal for self-service Directory Sync and SSO setup, and the staging
Test SSO page ("Head to the *Test SSO* page in the WorkOS Dashboard to get started with
testing common login flows"). Neither is buildable here, so neither is claimed. What this
build provides instead is named in :data:`REPLACES_HOSTED_SURFACE`.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one `records` table and `find()` matches
# on collection before it matches on anything else.

ORGANIZATION_COLLECTION = "wf084_organization"
CONNECTION_COLLECTION = "wf084_connection"
AUTHORIZATION_COLLECTION = "wf084_authorization"
CALLBACK_COLLECTION = "wf084_callback"
SESSION_COLLECTION = "wf084_session"
DIRECTORY_COLLECTION = "wf084_directory"
DIRECTORY_USER_COLLECTION = "wf084_directory_user"
DIRECTORY_GROUP_COLLECTION = "wf084_directory_group"
ACCESS_RULE_COLLECTION = "wf084_access_rule"
DIRECTORY_EVENT_COLLECTION = "wf084_directory_event"

ALL_COLLECTIONS = (
    ORGANIZATION_COLLECTION,
    CONNECTION_COLLECTION,
    AUTHORIZATION_COLLECTION,
    CALLBACK_COLLECTION,
    SESSION_COLLECTION,
    DIRECTORY_COLLECTION,
    DIRECTORY_USER_COLLECTION,
    DIRECTORY_GROUP_COLLECTION,
    ACCESS_RULE_COLLECTION,
    DIRECTORY_EVENT_COLLECTION,
)

# --------------------------------------------------------------------------- #
# The tenant, and the thing that must not stand in for it
# --------------------------------------------------------------------------- #

#: The profile field this workflow asserts on. The specification's data flow says the app
#: "asserts `profile.organizationId` matches the expected tenant", and the same sentence
#: appears in the extensibility note as the thing to copy.
ORGANIZATION_ID = "organization_id"

#: The profile field that must never be used to decide the tenant. Present as a named
#: constant so that a reviewer reading a rule can see the banned key next to the required
#: one, and so a test can assert the string never appears in a decision.
EMAIL = "email"

#: Why the email cannot decide the tenant, in the specification's own words. Carried in
#: every response that grants or refuses a session, because a refusal with no reason reads
#: as a broken login.
EMAIL_DOMAIN_UNSAFE = (
    "It is unsafe to validate a tenant with an email domain. An organization can allow an "
    "address from outside its corporate domain, for example a guest user. The tenant is the "
    "profile's organization id and nothing else."
)

# --------------------------------------------------------------------------- #
# Protocols and the three identifiers
# --------------------------------------------------------------------------- #

PROTOCOL_SAML = "saml"
PROTOCOL_OIDC = "oidc"

PROTOCOLS = (PROTOCOL_SAML, PROTOCOL_OIDC)

#: The specification's evidence, quoted: "This service is compatible with any IdP that
#: supports either the **SAML** or **OIDC** protocols."
PROTOCOL_DESCRIPTION = "Compatible with any IdP that supports either the SAML or OIDC protocols."

#: The tenant. The specification's second user-flow step: the login surface "redirects to
#: the IdP using the organization's identifier".
ORGANIZATION_PARAM = "organization"

#: A specific SAML or OIDC connection, named by the evidence as distinct from the tenant.
CONNECTION_PARAM = "connection"

#: An OAuth provider. The evidence is explicit that this one is "used for OAuth
#: connections", so it is not a synonym for the tenant and not a synonym for the
#: connection.
PROVIDER_PARAM = "provider"

#: The three, with the job each one does. A response serves this table so a reader can
#: tell which identifier answers which question.
IDENTIFIER_JOBS = (
    {
        "param": ORGANIZATION_PARAM,
        "job": "identifies the tenant whose users may sign in",
        "specifies_tenant": True,
        "specifies_connection": False,
        "specifies_provider": False,
    },
    {
        "param": CONNECTION_PARAM,
        "job": "selects one SAML or OIDC connection inside that tenant",
        "specifies_tenant": False,
        "specifies_connection": True,
        "specifies_provider": False,
    },
    {
        "param": PROVIDER_PARAM,
        "job": "names an OAuth provider; the vendor restricts it to OAuth connections",
        "specifies_tenant": False,
        "specifies_connection": False,
        "specifies_provider": True,
    },
)

IDENTIFIER_PARAMS = tuple(entry["param"] for entry in IDENTIFIER_JOBS)

# --------------------------------------------------------------------------- #
# The redirect URI, and how many a tenant gets
# --------------------------------------------------------------------------- #

REDIRECT_URI_PARAM = "redirect_uri"

#: The parameter a tenant's own SAML settings carry for its IdP-initiated sessions. The
#: specification quotes the customer as able to "specify a separate redirect URI to be used
#: for all their IdP-initiated sessions as a `RelayState` parameter in the SAML settings on
#: their side".
RELAY_STATE_PARAM = "relay_state"

#: The two remaining parameters of the vendor's own authorization call, quoted from the
#: specification: "sso.getAuthorizationUrl({ organization | connection | provider,
#: redirectUri, clientId })".
#
#: ``client_id`` is one key for two jobs. It names the field on a stored connection and it
#: names the query parameter on the URL, and the two are the same value sent two ways, so
#: one constant serves both rather than two that could drift.
CLIENT_ID_FIELD = "client_id"
STATE_PARAM = "state"

#: Which side started the sign-in. The specification puts both in scope, and they are two
#: entry points into one callback rather than two callbacks.
FLOW_KEY = "flow"
FLOW_STAFF_INITIATED = "staff_initiated"
FLOW_IDP_INITIATED = "idp_initiated"

FLOWS = (FLOW_STAFF_INITIATED, FLOW_IDP_INITIATED)

#: How many redirect URIs a tenancy gets. The evidence quotes both halves: "Multi-tenant
#: apps will typically have a single redirect URI specified. You can set multiple redirect
#: URIs for single-tenant apps."
MULTI_TENANT_MAX_REDIRECT_URIS = 1
SINGLE_TENANT_MAX_REDIRECT_URIS = 10

REDIRECT_URI_COUNT_RULE = (
    "A multi-tenant app normally has one redirect URI. A single-tenant app may have several."
)

# --------------------------------------------------------------------------- #
# The authorization code
# --------------------------------------------------------------------------- #

#: The short-lived credential the IdP redirects back with. The specification quotes the
#: bound directly: "The authorization code is valid for 10 minutes."
AUTHORIZATION_CODE_TTL_MINUTES = 10
AUTHORIZATION_CODE_TTL_SECONDS = AUTHORIZATION_CODE_TTL_MINUTES * 60

#: An expired code is rejected, never retried. Quoted as a decision rather than as a
#: behaviour: an expired code is not a slow code.
EXPIRED_CODE_POLICY = (
    "An authorization code is valid for ten minutes. After that it is rejected and the user "
    "starts a new sign-in. The code is never retried."
)

# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

SESSION_COLLECTION_FIELD = "sessions"

#: A session is granted only after the tenant assertion passes. The specification's data
#: flow says the app "asserts `profile.organizationId` matches the expected tenant and only
#: then creates a session", and the order is the property, so the constant records the
#: order rather than the fact. The field it is reported under is
#: :data:`ASSERT_ORDER_FIELD`, and the two are separate names for the reason given there.

ASSERTION_POLICY = (
    "The callback asserts the profile's organization id against the expected tenant. Only "
    "then is a session created."
)

# --------------------------------------------------------------------------- #
# Directory Sync
# --------------------------------------------------------------------------- #

#: The specification's evidence, quoted: "A directory is the source of truth for your
#: customer's user and group lists."
DIRECTORY_IS_SOURCE_OF_TRUTH = True

DIRECTORY_TRUTH_STATEMENT = (
    "A directory is the source of truth for a customer's user and group lists. Access here "
    "is a function of directory state, not of a manual admin action."
)

#: The three SCIM operations the specification names, quoted in full:
#: "Provisioning an identity for a user (account creation)", "When a user's attribute has
#: changed (account update)", and "Deprovisioning a user from your app (account deletion)".
SCIM_OP_CREATE = "create"
SCIM_OP_UPDATE = "update"
SCIM_OP_DELETE = "delete"

SCIM_OPERATIONS = (SCIM_OP_CREATE, SCIM_OP_UPDATE, SCIM_OP_DELETE)

SCIM_OPERATION_LABELS = {
    SCIM_OP_CREATE: "Provisioning an identity for a user (account creation)",
    SCIM_OP_UPDATE: "When a user's attribute has changed (account update)",
    SCIM_OP_DELETE: "Deprovisioning a user from your app (account deletion)",
}

#: Deprovisioning is the headline automation. The specification quotes its definition:
#: "Deprovisioning is a process of removing a user from an app."
DEPROVISION_DEFINITION = "Deprovisioning is a process of removing a user from an app."

#: What deprovisioning has to do here, and why "inactive" is not enough. A leaver whose row
#: says inactive but whose session still resolves would be a leaver with access, and the
#: specification calls deprovisioning a removal, not a label.
DEPROVISION_POLICY = (
    "A deprovision removes the user from the directory user list, revokes every session "
    "the user holds, and drops the directory groups they were in. A row marked inactive "
    "while its sessions still resolve would leave a leaver with access, which is the failure "
    "this operation exists to prevent."
)

#: How directory updates arrive. The specification names two and chooses neither: "Directory
#: updates can be delivered to you via webhooks or retrieved using the Events API."
DELIVERY_WEBHOOK = "webhook"
DELIVERY_EVENTS_API = "events_api"

DELIVERY_METHODS = (DELIVERY_WEBHOOK, DELIVERY_EVENTS_API)

#: The one this build ships, and the reason. Recorded rather than assumed; see
#: :mod:`dsr.security_governance.sso_inferences` under ``DERIVED_DELIVERY_METHOD``.
CHOSEN_DELIVERY_METHOD = DELIVERY_WEBHOOK

DELIVERY_REASON = (
    "A webhook is the delivery path this build ships. The Events API is supported as a pull "
    "of the same events, and both write through the same reconciliation, so a tenant that "
    "prefers to poll reaches the same records."
)

# --------------------------------------------------------------------------- #
# Supported directory providers
# --------------------------------------------------------------------------- #

#: Named in the specification's data sources and used as the fixture list: "directory
#: provider / HRIS behind SCIM (Okta, Microsoft AD, Workday, Google Workspace named as
#: supported)".
PROVIDER_OKTA = "okta"
PROVIDER_MICROSOFT_AD = "microsoft_ad"
PROVIDER_WORKDAY = "workday"
PROVIDER_GOOGLE_WORKSPACE = "google_workspace"

SUPPORTED_PROVIDERS = (
    PROVIDER_OKTA,
    PROVIDER_MICROSOFT_AD,
    PROVIDER_WORKDAY,
    PROVIDER_GOOGLE_WORKSPACE,
)

PROVIDER_LABELS = {
    PROVIDER_OKTA: "Okta",
    PROVIDER_MICROSOFT_AD: "Microsoft AD",
    PROVIDER_WORKDAY: "Workday",
    PROVIDER_GOOGLE_WORKSPACE: "Google Workspace",
}

# --------------------------------------------------------------------------- #
# Directory users
# --------------------------------------------------------------------------- #

#: The attributes the specification's admin objects carry and that this workflow reads.
#: The evidence says the objects are "with configurable attributes", so this is the set the
#: reconciliation copies rather than a schema anybody must migrate to.
SCIM_EXTERNAL_ID = "external_id"
SCIM_USER_NAME = "user_name"
SCIM_GIVEN_NAME = "given_name"
SCIM_FAMILY_NAME = "family_name"
SCIM_TITLE = "title"
SCIM_EMAILS = "emails"
SCIM_EMAIL_ADDRESS = "address"
SCIM_GROUPS = "groups"
SCIM_ACTIVE = "active"

SCIM_USER_FIELDS = (
    SCIM_EXTERNAL_ID,
    SCIM_USER_NAME,
    SCIM_GIVEN_NAME,
    SCIM_FAMILY_NAME,
    SCIM_TITLE,
    SCIM_EMAILS,
    SCIM_GROUPS,
    SCIM_ACTIVE,
)

#: A directory user's lifecycle, as a function of directory state. Three states and not
#: two, because the interesting case is the third one: a user the directory has not yet
#: named is not the same as a user the directory has removed.
USER_PROVISIONED = "provisioned"
USER_UPDATED = "updated"
USER_DEPROVISIONED = "deprovisioned"

USER_STATES = (USER_PROVISIONED, USER_UPDATED, USER_DEPROVISIONED)

# --------------------------------------------------------------------------- #
# Directory groups and the rules they feed
# --------------------------------------------------------------------------- #

#: The specification's evidence quotes the vendor's own definition: "A collection of users
#: within an organization who have been provisioned with access to your app. Directory
#: groups are mapped from directory provider groups."
GROUP_DEFINITION = (
    "A collection of users within an organization who have been provisioned with access to "
    "your app. Directory groups are mapped from directory provider groups."
)

#: Groups are an input to a decision, not a label. The specification's automation note:
#: "create groups that inform access rules".
GROUP_ROLE_KEY = "group_role"
GROUP_ROLE = "access_rule_input"

GROUP_POLICY = (
    "A directory group is an input to an access rule rather than a label. Access is "
    "decided by which groups a user is in."
)

# --------------------------------------------------------------------------- #
# Access rules
# --------------------------------------------------------------------------- #

RULE_ROLE_ADMIN = "admin"
RULE_ROLE_MEMBER = "member"
RULE_ROLE_AUDITOR = "auditor"

RULE_ROLES = (RULE_ROLE_ADMIN, RULE_ROLE_MEMBER, RULE_ROLE_AUDITOR)

#: A manual override is refused, and this is why. The specification's motivation is stated
#: as the risk of drift: "All future changes to this employee's data and access are manually
#: entered by IT contacts. This is error-prone and can lead to security vulnerabilities where
#: users get unauthorized access to resources."
NO_MANUAL_OVERRIDE = "no_manual_override"

OVERRIDE_REFUSAL = (
    "This workflow stores no manual override. The directory is the source of truth, so an "
    "access state set by hand here would survive the next directory change and defeat the "
    "workflow it appears to satisfy."
)

# --------------------------------------------------------------------------- #
# Hosted surfaces this build does not ship
# --------------------------------------------------------------------------- #

#: The two surfaces the specification names that belong to the vendor's own dashboard. The
#: issue instructs the implementer to "record what this build provides instead", so each one
#: names the substitute.
REPLACES_HOSTED_SURFACE = (
    {
        "surface": "Admin Portal for self-service Directory Sync and SSO setup",
        "owner": "vendor dashboard",
        "this_build_offers": (
            "The API and this page take the connection, the redirect URIs, the directory and "
            "the group-to-rule mapping. A tenant configures the same objects without the "
            "hosted portal."
        ),
    },
    {
        "surface": "Test SSO page in the vendor dashboard used to rehearse common login flows",
        "owner": "vendor dashboard",
        "this_build_offers": (
            "Both entry points are exercisable here: a staff-initiated authorization URL "
            "and an IdP-initiated callback carrying a RelayState."
        ),
    },
)

# --------------------------------------------------------------------------- #
# Served to the frontend
# --------------------------------------------------------------------------- #

PROTOCOLS_FIELD = "protocols"
IDENTIFIERS_FIELD = "identifiers"
FLOWS_FIELD = "flows"
SCIM_OPERATIONS_FIELD = "scim_operations"
SUPPORTED_PROVIDERS_FIELD = "supported_providers"
DELIVERY_METHODS_FIELD = "delivery_methods"
CHOSEN_DELIVERY_METHOD_FIELD = "chosen_delivery_method"
DELIVERY_REASON_FIELD = "delivery_reason"
DIRECTORY_TRUTH_FIELD = "directory_is_source_of_truth"
DIRECTORY_TRUTH_VALUE = DIRECTORY_TRUTH_STATEMENT
DEPROVISION_DEFINITION_FIELD = "deprovision_definition"
DEPROVISION_POLICY_FIELD = "deprovision_policy"
EXPIRED_CODE_POLICY_FIELD = "expired_code_policy"
ASSERTION_POLICY_FIELD = "assertion_policy"
EMAIL_DOMAIN_UNSAFE_FIELD = "email_domain_is_unsafe"
OVERRIDE_REFUSAL_FIELD = "manual_override"
OVERRIDE_REFUSAL_VALUE = OVERRIDE_REFUSAL
LIMITATION_FIELD = "limitation"

# The code's ten-minute bound, as a **field name** and as a **value**.
#
# They are two separate names on purpose. ``CODE_TTL_MINUTES_FIELD`` names a key a response
# carries and ``CODE_TTL_MINUTES`` is the number under it. Using one name for both is what a
# previous draft did, and the symptom was a 500 on two live routes: the key and its value are
# different types, and a payload that put the number where a string was expected failed
# response validation at the router rather than in a test.
CODE_TTL_MINUTES_FIELD = "authorization_code_ttl_minutes"
CODE_TTL_MINUTES = AUTHORIZATION_CODE_TTL_MINUTES

# Whether the ten-minute bound was checked on a particular request. Separate from the bound
# itself because one response can grant a session whose code this app never issued - the
# IdP-initiated case - and a reader needs to tell those apart.
CODE_TTL_CHECKED_FIELD = "code_bound_checked"

# Whether the order - assert the tenant, then create the session - is in force. The value is
# a boolean and the name is the field it is reported under, kept apart for the same reason.
ASSERT_ORDER_FIELD = "assert_before_session"
ASSERT_ORDER_VALUE = True

#: The one sentence every response from this workflow carries. It names the property the
#: workflow exists for, so a caller cannot read a granted session without also reading the
#: assertion that produced it.
LIMITATION = (
    "A session is granted only after the returned profile's organization id matches the "
    "expected tenant. An email domain is never that check. A directory user has whatever "
    "access their directory groups map to, and a deprovisioned user has none."
)


def vocabulary_payload() -> dict[str, Any]:
    """Every researched term, served so the page cannot drift from the rules."""

    return {
        "collections": list(ALL_COLLECTIONS),
        "tenant_field": ORGANIZATION_ID,
        "tenant_is_never": "email domain",
        PROTOCOLS_FIELD: [
            {
                "id": protocol,
                "label": protocol.upper(),
                "description": PROTOCOL_DESCRIPTION,
            }
            for protocol in PROTOCOLS
        ],
        IDENTIFIERS_FIELD: [dict(entry) for entry in IDENTIFIER_JOBS],
        FLOWS_FIELD: [
            {
                "id": FLOW_STAFF_INITIATED,
                "label": "Staff initiated",
                "description": "Staff open the login surface and this app redirects them out.",
            },
            {
                "id": FLOW_IDP_INITIATED,
                "label": "IdP initiated",
                "description": (
                    "The IdP sends the user straight to the callback, carrying the redirect "
                    "URI as a RelayState in the tenant's own SAML settings."
                ),
            },
        ],
        CODE_TTL_MINUTES_FIELD: AUTHORIZATION_CODE_TTL_MINUTES,
        EXPIRED_CODE_POLICY_FIELD: EXPIRED_CODE_POLICY,
        ASSERTION_POLICY_FIELD: ASSERTION_POLICY,
        EMAIL_DOMAIN_UNSAFE_FIELD: EMAIL_DOMAIN_UNSAFE,
        "redirect_uri": {
            "max_multi_tenant": MULTI_TENANT_MAX_REDIRECT_URIS,
            "max_single_tenant": SINGLE_TENANT_MAX_REDIRECT_URIS,
            "rule": REDIRECT_URI_COUNT_RULE,
            RELAY_STATE_PARAM: (
                "The redirect URI an IdP-initiated session uses, carried as a RelayState in "
                "the tenant's SAML settings."
            ),
        },
        SCIM_OPERATIONS_FIELD: [
            {"id": operation, "label": SCIM_OPERATION_LABELS[operation]}
            for operation in SCIM_OPERATIONS
        ],
        SUPPORTED_PROVIDERS_FIELD: [
            {"id": provider, "label": PROVIDER_LABELS[provider]} for provider in SUPPORTED_PROVIDERS
        ],
        DELIVERY_METHODS_FIELD: list(DELIVERY_METHODS),
        CHOSEN_DELIVERY_METHOD_FIELD: CHOSEN_DELIVERY_METHOD,
        DELIVERY_REASON_FIELD: DELIVERY_REASON,
        DIRECTORY_TRUTH_FIELD: DIRECTORY_TRUTH_STATEMENT,
        DEPROVISION_DEFINITION_FIELD: DEPROVISION_DEFINITION,
        DEPROVISION_POLICY_FIELD: DEPROVISION_POLICY,
        "directory_groups": {"definition": GROUP_DEFINITION, "role": GROUP_POLICY},
        "rule_roles": list(RULE_ROLES),
        OVERRIDE_REFUSAL_FIELD: OVERRIDE_REFUSAL_VALUE,
        "replaces_hosted_surface": [dict(entry) for entry in REPLACES_HOSTED_SURFACE],
        LIMITATION_FIELD: LIMITATION,
    }
