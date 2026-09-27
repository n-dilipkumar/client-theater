"""The researched half of WF-034, as data.

The research for this workflow is specific about the *shape* of the flow and
silent about almost every concrete value in it. It names the four interfaces a
connector implements, the HubSpot authorize URL and its three parameters, the
bearer header every later call carries, the Dataverse resource URL, the
credential vault and the fact that it is keyed by the org/account id, the fact
that tokens are stored encrypted, the fact that refresh is driven by the app's
own TTL, and the sentence that shapes the whole token half:

    "`Unauthorized (401)` requests are not a valid indicator that a new access
    token must be retrieved."

It also records two gaps it could not close, and both are carried here rather
than quietly smoothed over: the Salesforce flow-detail pages live on
``help.salesforce.com``, which serves a JS shell, and no Dataverse
authentication page was read at all. So this module holds **no** Dataverse
authentication quote, and it is explicit about which of the three token
endpoints are inferences.

Everything this module asserts is traceable to
``docs/research/digital-sales-room-workflows/wf/WF-034.md``. Everything this
workflow decided for itself is in :mod:`dsr.crm_oauth.inferences`, and the two
are served side by side over HTTP so a reviewer can disagree with a named entry
instead of hunting through a Python file.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The quoted evidence, verbatim from the research
# --------------------------------------------------------------------------- #

#: ``id -> {quote, where, means}``. ``where`` is the research document this came
#: from, so a reviewer can open the line rather than trust the paraphrase.
SOURCED_QUOTES: dict[str, dict[str, str]] = {
    "hubspot_authorize_url": {
        "quote": (
            "HubSpot: `GET https://app.hubspot.com/oauth/authorize"
            "?client_id=…&scope=…&redirect_uri=…` (authorize screen); callback "
            "`https://example.com/?code=xxxx`; then the token endpoint for "
            "initial access + refresh tokens."
        ),
        "where": "WF-034 research evidence → apis_hit",
        "means": (
            "The authorize URL and the three parameters on it are researched. "
            "The token endpoint is named but its URL is not, so ours is an "
            "inference."
        ),
    },
    "callback_carries_a_code": {
        "quote": (
            "Vendor redirects back to the sales room's `redirect_uri` with a "
            "`code` query parameter."
        ),
        "where": "WF-034 research evidence → user_flow step 4",
        "means": (
            "`code` is the researched parameter. `state`, which is what binds the "
            "callback to the connection that started it, is an inference."
        ),
    },
    "vault_keyed_by_org": {
        "quote": (
            "Sales room exchanges the code server-side for an access token (+ "
            "refresh token) and stores the refresh token in the integration's "
            "credential vault, keyed by the org/account id."
        ),
        "where": "WF-034 research evidence → user_flow step 5",
        "means": (
            "The vault is keyed by the org/account id, so a credential with no "
            "org/account id is a violation of the researched rule and is "
            "refused rather than filed under a default."
        ),
    },
    "stored_encrypted": {
        "quote": "server-to-server token exchange → access token (bearer) stored encrypted",
        "where": "WF-034 research evidence → data_flow",
        "means": (
            "The bearer is stored encrypted. The research says nothing about "
            "key management, so the cipher and the key source are inferences."
        ),
    },
    "bearer_header_on_every_call": {
        "quote": (
            "Salesforce: OAuth 2.0 authorization flow over an external client app "
            "(new) or connected app (legacy). Every REST call then uses "
            "`Authorization: Bearer token`. Dataverse: `Authorization: Bearer "
            "<access token>` on `https://<org>.api.crm.dynamics.com"
            "/api/data/v9.2/…`."
        ),
        "where": "WF-034 research evidence → apis_hit",
        "means": (
            "The token travels in the header. No connector in this package ever "
            "places a token in a URL, and there is a test that says so."
        ),
    },
    "salesforce_needs_an_authorization": {
        "quote": (
            "For a client application to access REST API resources, it must be "
            "authorized as a safe visitor. To implement this authorization, use "
            "either an external client app or a connected app and an OAuth 2.0 "
            "authorization flow."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "Salesforce is on the OAuth 2.0 authorization flow, over an external "
            "client app or a connected app."
        ),
    },
    "connected_apps_restricted_spring_26": {
        "quote": (
            "Creating connected apps is restricted as of Spring '26. You can "
            "continue to use existing connected apps during and after Spring "
            "'26. However, we recommend using external client apps instead."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "A connection declares which kind of app it is authorizing against. "
            "External client app is the default and connected app stays "
            "available; both are surfaced with this quote on the connection."
        ),
    },
    "oauth_grants_restricted_access": {
        "quote": (
            "OAuth authorization flows grant a client app restricted access to "
            "REST API resources on a resource server … 1. To initiate an "
            "authorization flow, a connected app on behalf of a client app "
            "requests access to a REST API resource. 2. In response, an "
            "authorizing server grants access tokens … 3. A resource server "
            "validates these access tokens and approves access to the protected "
            "REST API resource."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "The flow is a three-party one. This room is the client app, so the "
            "token exchange and every later call are server-to-server."
        ),
    },
    "consent_chooses_the_org": {
        "quote": (
            "The vendor consent screen asks the admin to choose the org/account "
            "and grant scopes; admin approves."
        ),
        "where": "WF-034 research evidence → user_flow step 3",
        "means": (
            "The org/account and the scopes are chosen on the vendor's screen, "
            "which is why a connection can carry an org id the admin picked and "
            "why a connection with no scopes has nothing to ask for."
        ),
    },
    "hubspot_oauth_is_mandatory": {
        "quote": (
            "Any app designed for installation by multiple HubSpot accounts or "
            "listing on the HubSpot Marketplace must use OAuth."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "Multi-account distribution mandates OAuth, which is why this "
            "workflow is an OAuth connector at all rather than a static API key."
        ),
    },
    "hubspot_installer_permission": {
        "quote": (
            "Users installing apps in their HubSpot account must either be a "
            "Super Admin or have HubSpot Marketplace Access permissions."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "A researched precondition on the *operator*, not on the request. "
            "Surfaced as advisory on every HubSpot connection, because this room "
            "cannot know the operator's role and must not pretend otherwise."
        ),
    },
    "ttl_is_the_apps_job": {
        "quote": (
            "Apps are responsible for storing time-to-live (TTL) data and "
            "refreshing user access tokens in accordance with this protocol. "
            "When an access token is generated, it will include an `expires_in` "
            "parameter indicating how long it can be used to make API calls "
            "before refreshing."
        ),
        "where": "WF-034 research evidence → evidence",
        "means": (
            "`expires_in` is the clock. A token whose exchange never produced one "
            "cannot be refreshed before expiry, because there is no expiry to be "
            "before."
        ),
    },
    "refresh_before_expiry": {
        "quote": "Token refresh before expiry.",
        "where": "WF-034 research evidence → automations",
        "means": (
            "Refresh happens on the stored TTL, ahead of the expiry, not when a "
            "call happens to fail."
        ),
    },
    "401_is_not_a_refresh_signal": {
        "quote": (
            "`Unauthorized (401)` requests are not a valid indicator that a new "
            "access token must be retrieved."
        ),
        "where": "WF-034 research evidence → automations",
        "means": (
            "The one sentence this build is measured on. A 401 from a probe is "
            "recorded as a *health* fact and changes nothing about the stored "
            "token, the stored expiry, or the refresh schedule. It does not "
            "become a refresh trigger, and the code has no path that would make "
            "it one."
        ),
    },
    "health_polling_is_ours": {
        "quote": "Connection-health polling is driven by the sales room's own scheduler (not vendor-side).",
        "where": "WF-034 research evidence → automations",
        "means": (
            "Nothing is pushed at us, so a sweep has to be callable by whatever "
            "runs the scheduler. This app has no scheduler and adding one would "
            "mean editing shared files, so the sweep is a route and the report "
            "says so."
        ),
    },
    "test_connection_is_a_low_cost_call": {
        "quote": (
            "Admin clicks **Test connection**; the sales room calls a low-cost "
            "authenticated endpoint to verify the token."
        ),
        "where": "WF-034 research evidence → user_flow step 6",
        "means": (
            "The probe is a real authenticated call, not a validation of a "
            "string. The research names the property of the endpoint, not which "
            "endpoint, so all three probe paths are inferences."
        ),
    },
    "four_interfaces": {
        "quote": (
            "A third party adds a new vendor by implementing the same 4 "
            "interfaces — authorize-URL builder, code→token exchange, token "
            "refresh, and a bearer-authenticated request executor."
        ),
        "where": "WF-034 research evidence → extensibility",
        "means": (
            "A vendor is a plug-in. The registry takes any object with those four "
            "methods, and the suite registers a fourth vendor at test time and "
            "runs the whole flow on it."
        ),
    },
    "nothing_written_to_crm": {
        "quote": "nothing is written to CRM records in this workflow.",
        "where": "WF-034 research evidence → data_flow",
        "means": (
            "No route in this feature writes a CRM record, and none constructs a "
            "create or update payload. The only records are the room's own "
            "connection, grant, vault and token-event rows."
        ),
    },
    "code_to_token_is_the_transformation": {
        "quote": (
            "The code→token exchange converts an ephemeral redirect code into a "
            "long-lived refresh credential."
        ),
        "where": "WF-034 research evidence → data_flow",
        "means": (
            "The code is single-use and short-lived; the refresh token is what "
            "the room keeps. The code is never stored."
        ),
    },
    "settings_row_is_tenant_scoped": {
        "quote": (
            "sales-room integration settings table (tenant, connection status, "
            "encrypted refresh token)"
        ),
        "where": "WF-034 research evidence → data_sources",
        "means": (
            "A connection belongs to a tenant, not to a room. A room reaches the "
            "connections that serve it, and a tenant-wide connection serves "
            "every room."
        ),
    },
    "integrations_surface": {
        "quote": (
            "Salesforce Setup → External Client Apps (or legacy Connected App) "
            "policy screen; HubSpot Developer Platform → app Auth page (client "
            "ID / client secret) and the app-install consent screen; sales-room "
            "Integrations settings surface; a \"Test connection\" button."
        ),
        "where": "WF-034 research evidence → features_tools",
        "means": (
            "The client id and secret come from the vendor's own screen, and the "
            "consents happen on the vendor's own screen too. This page is the "
            "Integrations surface around them, not a substitute for them."
        ),
    },
}

#: The gaps the research declares. Served so nobody reads a missing Dataverse
#: authentication quote as an oversight here rather than upstream.
RESEARCH_GAPS: tuple[dict[str, str], ...] = (
    {
        "id": "salesforce_flow_details",
        "gap": (
            "The Salesforce OAuth 2.0 flow detail pages (web-server flow, PKCE, "
            "refresh-token rotation) live in help.salesforce.com, which serves a "
            "JS shell and could not be read."
        ),
        "effect": (
            "No Salesforce-specific flow detail is claimed. The Salesforce "
            "authorize and token endpoint URLs, the API version, and the probe "
            "path are inferences; only the flow type, the two app kinds and the "
            "bearer header are sourced."
        ),
    },
    {
        "id": "dataverse_auth_unread",
        "gap": (
            "The Dataverse auth page (webapi/authenticate-web-api) was reachable "
            "(HTTP 200) but its body was not extracted before the research budget "
            "ran out, so no Dataverse-specific auth quote is claimed."
        ),
        "effect": (
            "The Dataverse connector's authorize and token endpoints, and its "
            "probe path, are inferences. The resource base URL shape and the "
            "bearer header *are* sourced."
        ),
    },
)

# --------------------------------------------------------------------------- #
# The vendor vocabulary
# --------------------------------------------------------------------------- #

#: The three vendors the researched user flow offers. ``picked by the admin at
#: Integrations → Add CRM connection``: "picks Salesforce / HubSpot / Dynamics".
VENDOR_IDS: tuple[str, ...] = ("salesforce", "hubspot", "dataverse")


def describe_vocabulary() -> dict[str, Any]:
    """The researched contract, plus what this build deliberately leaves out.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, so a reviewer can read the researched facts
    without opening a Python file, and so a decision to add a fourth vendor
    shows up in one place rather than as a new page.
    """
    from dsr.crm_oauth.connectors import describe_connector, registered_vendors

    return {
        "vendors": VENDOR_IDS,
        "flow": [
            "Admin opens Integrations → Add CRM connection and picks a vendor.",
            "Admin clicks Authorize; the browser goes to the vendor's "
            "authorization URL with client_id, scope and redirect_uri.",
            "The vendor consent screen asks the admin to choose the org/account "
            "and grant scopes.",
            "The vendor redirects back to the sales room's redirect_uri with a "
            "code.",
            "The room exchanges the code server-side for an access token and a "
            "refresh token, and seals the refresh token in the credential vault "
            "keyed by the org/account id.",
            "Admin clicks Test connection; the room calls a low-cost "
            "authenticated endpoint with the bearer token to verify it.",
        ],
        "connector_interfaces": CONNECTOR_INTERFACES,
        "interface_rationale": SOURCED_QUOTES["four_interfaces"]["quote"],
        "sourced_quotes": SOURCED_QUOTES,
        "gaps": list(RESEARCH_GAPS),
        "connectors": {vendor: describe_connector(vendor) for vendor in registered_vendors()},
        "not_implemented": describe_adjacent_surfaces(),
    }


#: The four interface names, verbatim from the researched extensibility note.
#: They are the contract a third-party connector has to satisfy, so they are
#: published as data and checked in the suite.
CONNECTOR_INTERFACES: tuple[dict[str, str], ...] = (
    {
        "name": "authorize_url",
        "does": "builds the vendor authorization URL the browser is sent to",
        "sourced": "HubSpot's is the one the research quotes; the other two are inferences",
    },
    {
        "name": "exchange_code",
        "does": "turns the redirect code into an access token and a refresh token",
        "sourced": "the flow type is sourced; the request body is from the OAuth 2.0 framework spec",
    },
    {
        "name": "refresh",
        "does": "renews the access token from the refresh token the room kept",
        "sourced": "'refresh before expiry' is sourced; the request body is not",
    },
    {
        "name": "execute",
        "does": "makes one bearer-authenticated request, with the token in the header",
        "sourced": "'Authorization: Bearer token' is sourced for Salesforce and Dataverse",
    },
)


def describe_adjacent_surfaces() -> dict[str, Any]:
    """The surfaces this research names that this build does not implement.

    Published rather than omitted. A reader who finds the absence in the code
    should find the reason next to it, and a reviewer who disagrees has a named
    thing to disagree with.
    """
    return {
        "sync_of_crm_records": {
            "researched": "nothing is written to CRM records in this workflow",
            "this_build": "no route writes a CRM record, by design",
        },
        "refresh_token_rotation": {
            "researched": (
                "listed in the Salesforce gaps as a flow detail that could not be "
                "read (web-server flow, PKCE, refresh-token rotation)"
            ),
            "this_build": (
                "a refresh response that returns a new refresh token replaces the "
                "stored one; a response that omits it keeps the stored one. Which "
                "vendors rotate is not claimed."
            ),
        },
        "pkce": {
            "researched": "listed in the Salesforce gaps; not read",
            "this_build": (
                "not implemented. The research's flow is a server-side exchange "
                "with a client secret, and a public-client PKCE challenge is a "
                "different flow than the one specified."
            ),
        },
        "field_mapping_and_sync": {
            "researched": "this domain's other workflows, not this one",
            "this_build": "out of scope; this feature ends at a verified bearer token",
        },
        "background_scheduler": {
            "researched": "'Connection-health polling is driven by the sales room's own scheduler'",
            "this_build": (
                "the sweep is a route a scheduler calls. This application has no "
                "scheduler, and adding one would mean editing shared files the "
                "feature contract forbids."
            ),
        },
    }
