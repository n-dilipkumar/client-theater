"""Every judgement call this package makes, named, bounded, and served over HTTP.

The research for WF-034 is unusually specific about a few things - the HubSpot
authorize URL, the bearer header, the four interfaces, the vault keyed by
org/account, the 401 sentence - and silent about almost everything around them.
The parts that are therefore judgement calls live here, one entry each, with the
reason and the one thing that would change them.

Two reasons this is a module and not a comment:

* a comment says "we inferred this" and leaves the reader to work out what from;
* an entry says what was inferred, why, what it would cost to be wrong, and how
  to change it, and is served next to the sourced facts at ``/vocabulary`` and
  ``/inferences`` so a reviewer can disagree with a *named* thing.

Every entry carries ``sourced_against``: the id of the quote that constrains it,
or ``None`` where the research is simply silent. The count of ``None`` entries
is the honest measure of how much of this build the research did not determine.
"""

from __future__ import annotations

from typing import Any

#: ``id -> {decision, why, change_how, sourced_against, cost_if_wrong}``.
INFERENCES: dict[str, dict[str, Any]] = {
    "state_parameter": {
        "decision": (
            "The callback requires a ``state`` that names one pending "
            "authorization, and a callback without a matching pending "
            "authorization is refused."
        ),
        "why": (
            "The researched callback carries a ``code`` and nothing else, but a "
            "callback still has to be matched to the connection that started it. "
            "Without that, two authorizations in flight in two tabs cross over, "
            "and a code delivered to the wrong connection is filed as a working "
            "credential for the wrong org."
        ),
        "change_how": (
            "The grant lookup is one function in the engine. Accepting a bare "
            "code when exactly one authorization is pending is a three-line "
            "change - but it re-opens the crossing, so it is not free."
        ),
        "sourced_against": "callback_carries_a_code",
        "cost_if_wrong": "credentials filed against the wrong org; the vault is keyed by org, so the damage is silent",
    },
    "grant_expiry_and_single_use": {
        "decision": "A pending authorization expires after 10 minutes, and a consumed one cannot be replayed.",
        "why": (
            "The researched transformation is 'an ephemeral redirect code into a "
            "long-lived refresh credential'. A grant that never came back should "
            "not leave a live one lying around, and a code that has already been "
            "exchanged has been spent at the vendor."
        ),
        "change_how": "GRANT_TTL_SECONDS in dsr.crm_oauth.engine, and the consumed check in exchange_callback().",
        "sourced_against": "code_to_token_is_the_transformation",
        "cost_if_wrong": "an abandoned tab leaves an authorization that can still be completed hours later",
    },
    "refresh_skew_seconds": {
        "decision": "A token is refreshed once it is within 60 seconds of expiring, not at expiry.",
        "why": (
            "'Token refresh before expiry' is the literal reading, and a token "
            "that expires between the check and the vendor's arrival is a "
            "spurious failure - so the window has to be positive."
        ),
        "change_how": "SKEW_SECONDS in dsr.crm_oauth.engine.",
        "sourced_against": "refresh_before_expiry",
        "cost_if_wrong": "60 seconds is short relative to a 1800-second token, so a clock skew between this host and the vendor can still produce a surprise",
    },
    "unknown_ttl_refreshes_every_use": {
        "decision": (
            "A token whose exchange produced no ``expires_in``, and whose vendor "
            "declares no default, is refreshed before **every** use and the "
            "connection is flagged ``ttl_unknown``."
        ),
        "why": (
            "The researched TTL protocol depends on ``expires_in`` being there. "
            "When it is not, there is no expiry to be before, and the two "
            "available readings are 'never refresh' (which fails silently, at "
            "some unrelated call, later) or 'refresh every time' (which is "
            "chatty and visible). The visible one is the right one for a surface "
            "whose whole job is telling an operator what state a connection is in."
        ),
        "change_how": "Set the connector's default_expires_in, or make a token endpoint without expires_in a refusal.",
        "sourced_against": "ttl_is_the_apps_job",
        "cost_if_wrong": "a token request per API call, against a vendor rate limit",
    },
    "probe_endpoints_are_inferences": {
        "decision": (
            "Each connector declares one probe path, and a default API version "
            "for Salesforce. All of them are inferences; the research asks for a "
            "low-cost authenticated endpoint without naming one."
        ),
        "why": "It is the only way to implement step 6 without inventing a requirement.",
        "change_how": "VendorInfo.probe_path / probe_query / api_version on the connector.",
        "sourced_against": "test_connection_is_a_low_cost_call",
        "cost_if_wrong": "the probe 404s on an org that has never seen the integration user, and the connection reads as broken when the token is fine",
    },
    "token_endpoint_urls_are_inferences": {
        "decision": "The three token endpoints are constructed from each vendor's documented authorize host plus the OAuth 2.0 framework's paths.",
        "why": (
            "The research names HubSpot's token endpoint but not its URL, and "
            "states outright that the Salesforce flow pages and the Dataverse "
            "auth page could not be read."
        ),
        "change_how": "VendorInfo.token_endpoint, or override token_endpoint() on the connector for a host that is computed.",
        "sourced_against": "hubspot_authorize_url",
        "cost_if_wrong": "the exchange fails with a connection error rather than a vendor error, which the health view has to distinguish",
    },
    "dataverse_authorize_is_an_inference": {
        "decision": "The Dataverse connector builds an Azure AD authorize URL and says so.",
        "why": (
            "The research is explicit: 'no Dataverse-specific auth quote is "
            "claimed'. The connector's own ``inferences`` list carries that, and "
            "the page shows it next to the endpoint."
        ),
        "change_how": "Nothing; it is already labelled. Reading the auth page and replacing this is the fix.",
        "sourced_against": "bearer_header_on_every_call",
        "cost_if_wrong": "a Dataverse connection cannot be authorized until someone reads the real page",
    },
    "org_id_resolution": {
        "decision": (
            "The vault key is the org/account id the token response carries "
            "(each connector declares which response fields count), falling back "
            "to the org the admin chose on the connection. If neither exists the "
            "exchange is refused."
        ),
        "why": (
            "The research states the vault is 'keyed by the org/account id' and "
            "that the consent screen asks the admin to choose the org/account, "
            "but it names no response field on any of the three vendors. Refusing "
            "rather than defaulting is what stops one connection's credential "
            "being filed under a placeholder key that another connection would "
            "also use."
        ),
        "change_how": "VendorInfo.org_id_keys, and the resolution order in engine._resolve_org_key().",
        "sourced_against": "vault_keyed_by_org",
        "cost_if_wrong": "a vendor that returns the org in a field we do not read needs that field added to org_id_keys",
    },
    "scopes_are_required": {
        "decision": "A connection with no scopes cannot be authorized.",
        "why": (
            "The researched URL carries a ``scope`` and the consent screen "
            "'grant[s] scopes'. An empty scope asks the vendor for nothing and "
            "produces a token that cannot do the work."
        ),
        "change_how": "The scopes check in engine.blockers(); the per-vendor suggestions are VendorInfo.suggested_scopes and are labelled as suggestions, never applied.",
        "sourced_against": "consent_chooses_the_org",
        "cost_if_wrong": "an admin who wants the vendor's own default scope set has to type it",
    },
    "scope_encoding_is_space_separated": {
        "decision": "``scope`` is space-separated.",
        "why": (
            "The research quotes ``scope=…`` for HubSpot and does not say how "
            "the values are separated. The OAuth 2.0 framework specifies "
            "space-separated, and the flow the research names is that framework's."
        ),
        "change_how": "SCOPE_SEPARATOR in dsr.crm_oauth.connectors.",
        "sourced_against": "hubspot_authorize_url",
        "cost_if_wrong": "the vendor parses one scope with spaces in it and grants nothing",
    },
    "exchange_is_a_form_post": {
        "decision": "The code exchange and the refresh are form-encoded POSTs carrying client_id, client_secret and grant_type.",
        "why": "Same reason as the scope encoding: the framework's shape, not a vendor's.",
        "change_how": "form_body() in dsr.crm_oauth.transport and _BaseConnector._token().",
        "sourced_against": "oauth_grants_restricted_access",
        "cost_if_wrong": "a vendor expecting JSON would answer 415, which the health view reports as an error rather than a refusal",
    },
    "client_secret_is_sealed": {
        "decision": (
            "The app client secret is sealed in the vault under the reserved org "
            "key ``app``, not kept in the connection row."
        ),
        "why": (
            "It is the same class of credential as the token, and the research "
            "names the vendor screen it comes from - HubSpot's 'Developer "
            "Platform → app Auth page (client ID / client secret)'. Only the "
            "client id, which is public by design, is in the settings row."
        ),
        "change_how": "APP_ORG_KEY in dsr.crm_oauth.vault.",
        "sourced_against": "stored_encrypted",
        "cost_if_wrong": "none; this is strictly more careful than the alternative",
    },
    "vault_cipher_and_key": {
        "decision": (
            "Credentials are sealed with a keyed HMAC-SHA256 stream and "
            "encrypt-then-MAC, under DSR_CRM_VAULT_KEY. With no key set, the "
            "feature's published demo constant is used and every surface says so."
        ),
        "why": (
            "The research says 'stored encrypted' and nothing about key "
            "management, and the standard library is the only dependency this "
            "feature can assume. A published default is honest rather than safe: "
            "it is why a fresh checkout's demo works, and the reason the "
            "connections list, the health view and the frontend all carry the "
            "warning. A per-process random key was the alternative and it makes "
            "every credential the seeder wrote unreadable in the process that "
            "serves the app, which would leave 'Test connection' permanently "
            "broken in a demo."
        ),
        "change_how": "seal() / open_sealed() in dsr.crm_oauth.vault, and resolve_key(). A deployment swaps in a KMS or a vetted AEAD there and nothing else changes.",
        "sourced_against": "stored_encrypted",
        "cost_if_wrong": "with the default key, an attacker who reads the database has the tokens; the fix is one environment variable",
    },
    "no_scheduler_thread": {
        "decision": "The health sweep is a route. Nothing here starts a background thread.",
        "why": (
            "'Connection-health polling is driven by the sales room's own "
            "scheduler' and this application has no scheduler. Starting one would "
            "mean editing a shared file the feature contract forbids, and a "
            "thread per feature is the kind of thing that makes a hundred "
            "features unmergeable again."
        ),
        "change_how": "Call POST /rooms/<room_id>/health-check on whatever interval health_interval_seconds says.",
        "sourced_against": "health_polling_is_ours",
        "cost_if_wrong": "nobody polls unless something calls the route; the connections list says when each one was last checked and when it is next due",
    },
    "status_and_health_are_separate": {
        "decision": (
            "A connection's ``status`` is its credential lifecycle "
            "(pending_authorization / authorized / expired / disconnected) and its "
            "``health`` is the last probe's outcome (unknown / ok / "
            "unauthorized / error). They are separate fields."
        ),
        "why": (
            "The researched sentence - '401 requests are not a valid indicator "
            "that a new access token must be retrieved' - only means something if "
            "a 401 and an expiry are recorded as different facts. One field would "
            "force the choice the research forbids."
        ),
        "change_how": "connection_status() and the health fields in engine.py.",
        "sourced_against": "401_is_not_a_refresh_signal",
        "cost_if_wrong": "none; this is the reading the research supports",
    },
    "refresh_refusal_means_re_authorize": {
        "decision": (
            "A refresh the vendor refuses is recorded, and the connection's next "
            "action becomes re-authorize rather than refresh again."
        ),
        "why": (
            "The TTL says the token is dead, so a refresh is the first thing to "
            "try - and when the vendor refuses it, that is the vendor saying this "
            "refresh token is not going to work. Repeating the request on an "
            "interval is the shape of a bug someone hits in production."
        ),
        "change_how": "The refresh_refused branch of needs_action() and the patch in _usable_token()'s refusal handler.",
        "sourced_against": "refresh_before_expiry",
        "cost_if_wrong": "a transient refusal (a vendor blip) sends one admin to the consent screen",
    },
    "a_401_changes_nothing_but_health": {
        "decision": (
            "A 401 from a probe updates health, ``last_unauthorized_at`` and the "
            "token-event log. It does not clear the token, does not change "
            "``expires_at``, does not set a flag the refresh path reads, and "
            "does not change ``next_check_at``."
        ),
        "why": (
            "The research says so directly. The observable consequence is that "
            "after a 401 the connection still shows its real expiry, and the "
            "next refresh still happens on the TTL rather than on the failure."
        ),
        "change_how": "test_connection() in engine.py. There is no other writer of a refresh trigger.",
        "sourced_against": "401_is_not_a_refresh_signal",
        "cost_if_wrong": "a genuinely revoked token is not refreshed until its TTL expires; readiness says 're-authorize' and that is the researched remedy anyway",
    },
    "unauthorized_says_re_authorize": {
        "decision": "A connection whose last probe was a 401 reports the action 're-authorize', not 'refresh'.",
        "why": (
            "If a 401 does not mean 'get a new access token', then it must still "
            "mean something, or the health view is decoration. It means the "
            "vendor stopped accepting this credential, and the only thing that "
            "fixes that is the researched flow: authorize again."
        ),
        "change_how": "needs_action() in engine.py.",
        "sourced_against": "401_is_not_a_refresh_signal",
        "cost_if_wrong": "an admin clicks Authorize for a token that merely had a bad moment; the flow is idempotent enough that this costs one consent screen",
    },
    "test_connection_reports_200": {
        "decision": "Test connection answers 200 with ``ok: false`` when the vendor rejects the token.",
        "why": (
            "The call succeeded; the answer is 'the vendor says no'. A 401 from "
            "this route would read as this room refusing the caller, which is a "
            "different thing and would be misleading in a log."
        ),
        "change_how": "The route in the feature module. apiRequest carries the status, so a client can still branch.",
        "sourced_against": "test_connection_is_a_low_cost_call",
        "cost_if_wrong": "a client that only checks the HTTP status will not notice a rejected token; the page reads the body",
    },
    "connection_is_tenant_scoped": {
        "decision": (
            "A connection belongs to a tenant. A room reaches the connections "
            "attached to it plus the tenant-wide ones, and a tenant-wide "
            "connection serves every room."
        ),
        "why": (
            "The research's data source is the 'integration settings table "
            "(tenant, connection status, encrypted refresh token)' - a tenant "
            "row, not a room row. Making the connection room-scoped would make "
            "re-authorize a room-level operation, which is not what the flow is."
        ),
        "change_how": "The room_id on the connection record, and the two filter helpers in engine.py.",
        "sourced_against": "settings_row_is_tenant_scoped",
        "cost_if_wrong": "none; both scopes are supported and the scope is shown on every row",
    },
    "salesforce_policy_is_a_connection_field": {
        "decision": (
            "A Salesforce connection records whether it authorizes against an "
            "external client app or a legacy connected app, defaulting to the "
            "external client app. The authorize URL is identical either way."
        ),
        "why": (
            "The research says the two differ at the 'policy screen' and that "
            "Salesforce 'recommend[s] using external client apps'. The difference "
            "is therefore in the org's configuration, not in this request, so it "
            "is recorded and surfaced rather than encoded in a query parameter "
            "the research does not document."
        ),
        "change_how": "VendorInfo.settings on the Salesforce connector, and the policy check in engine.blockers().",
        "sourced_against": "connected_apps_restricted_spring_26",
        "cost_if_wrong": "an operator on a legacy connected app sees a recommendation they cannot act on; the note quotes what Salesforce says about continuing to use it",
    },
    "operator_permissions_are_advisory": {
        "decision": "HubSpot's Super Admin / Marketplace Access requirement is advisory, never a blocker.",
        "why": (
            "The requirement is on the person installing, and this room cannot "
            "know that person's role. Blocking on it would be a guess; hiding it "
            "would be the failure mode a reviewer would catch - a connection that "
            "cannot be authorized with no explanation."
        ),
        "change_how": "VendorInfo.grant_requirements, surfaced by /rooms/<room_id>/readiness as advisory.",
        "sourced_against": "hubspot_installer_permission",
        "cost_if_wrong": "none; it is shown, not enforced",
    },
    "no_crm_record_writes": {
        "decision": "No route in this feature writes a CRM record, and no connector builds a create or update payload.",
        "why": (
            "'nothing is written to CRM records in this workflow'. The four "
            "interfaces have no method that could, and the probe is a GET."
        ),
        "change_how": "Nothing; there is a test that fails if a write-shaped route appears.",
        "sourced_against": "nothing_written_to_crm",
        "cost_if_wrong": "none",
    },
    "probe_uses_the_header_only": {
        "decision": "No connector ever places a token in a URL, and build_authorize_url drops empty parameters.",
        "why": (
            "'Authorization: Bearer token' is a header. A URL carrying a token "
            "reaches every proxy log, access log and support ticket between here "
            "and the vendor, none of which redact it. HubSpot's own token-"
            "inspection endpoint takes the token in the path, which is exactly why "
            "it is not the probe."
        ),
        "change_how": "assert_no_token_in_url(), called from _BaseConnector.execute().",
        "sourced_against": "bearer_header_on_every_call",
        "cost_if_wrong": "none",
    },
    "soft_delete_keeps_the_log": {
        "decision": "Disconnecting soft-deletes the connection and its grants; the token events and the audit trail outlive it.",
        "why": (
            "A connection that was authorized and then removed is the row an "
            "operator needs when a room stops syncing, and the audit log is the "
            "product's guarantee. Hard-deleting would trade the guarantee for "
            "tidiness."
        ),
        "change_how": "disconnect() in engine.py.",
        "sourced_against": None,
        "cost_if_wrong": "a tenant that wants credentials genuinely gone re-authorizes with a new client; the sealed row can be dropped explicitly",
    },
    "default_health_interval": {
        "decision": "A connection is checked every hour unless it says otherwise; a failed check is retried in 5 minutes.",
        "why": (
            "The research says polling is ours and gives no interval. An hour is "
            "long enough that a poll is not a load problem and short enough that "
            "a broken credential is noticed the same working day. The shorter "
            "retry is because a 5xx is usually transient and a 401 is not - and "
            "the 401 is not retried early, precisely because it is not a refresh "
            "trigger."
        ),
        "change_how": "DEFAULT_HEALTH_INTERVAL_SECONDS and ERROR_RETRY_SECONDS in engine.py, or the per-connection field.",
        "sourced_against": "health_polling_is_ours",
        "cost_if_wrong": "an hour of staleness on the health column",
    },
}


def by_id(inference_id: str) -> dict[str, Any] | None:
    return INFERENCES.get(str(inference_id or ""))


def describe() -> dict[str, Any]:
    """The registry, served. Includes the honest count of unsourced entries."""
    sourced = sum(1 for entry in INFERENCES.values() if entry.get("sourced_against"))
    return {
        "count": len(INFERENCES),
        "constrained_by_a_quote": sourced,
        "silent_in_the_research": len(INFERENCES) - sourced,
        "note": (
            "An entry with sourced_against=None is a decision the research is "
            "simply silent about. The count of those is the honest measure of how "
            "much of this build the research did not determine."
        ),
        "inferences": INFERENCES,
    }


__all__ = ["INFERENCES", "by_id", "describe"]
