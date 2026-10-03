"""The published vocabulary of WF-044, and the rules the research quotes.

Everything a client needs to render the endpoint editor lives here rather than
being compiled into a page, so a vocabulary this build adds server-side reaches
every client at once and the editor can never disagree with the validator about
what is legal. :func:`describe` serves the whole thing.

Each constant carries the sentence from
``docs/research/digital-sales-room-workflows/wf/WF-044.md`` that makes it part of
the specification rather than a preference of this build.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: One endpoint per room. The research's extensibility line is why this is a
#: collection of *one per tenant* and not a list per workflow: "The room exposes
#: one inbound endpoint per tenant with a versioned payload contract, so any
#: number of CRM-side automations can target it."
COLLECTION_ENDPOINT = "crm_outbound_endpoint"

#: The CRM-side automations pointed at one endpoint. "Any number" is unbounded
#: here and capped at 1,000 per app by :data:`SUBSCRIPTION_LIMIT_PER_APP`.
COLLECTION_AUTOMATION = "crm_outbound_automation"

#: Every request that reached the endpoint, including the ones it turned away.
COLLECTION_DELIVERY = "crm_outbound_delivery"

#: The deal panel: what the room currently believes about a deal, from the CRM.
COLLECTION_DEAL = "crm_outbound_deal"

#: What the delivery told the rep. "Maps payload onto the room model -> updates
#: deal panel and notifies the rep."
COLLECTION_NOTICE = "crm_outbound_notice"

COLLECTIONS: tuple[str, ...] = (
    COLLECTION_ENDPOINT,
    COLLECTION_AUTOMATION,
    COLLECTION_DELIVERY,
    COLLECTION_DEAL,
    COLLECTION_NOTICE,
)

# --------------------------------------------------------------------------- #
# Steps 1 to 4: what the CRM-side editor holds
# --------------------------------------------------------------------------- #

#: "You can send both POST and GET requests using workflows." Two methods, and
#: this build serves both. A GET carries its properties in the query string, which
#: is why the receive path takes them from both places.
METHODS: tuple[str, ...] = ("POST", "GET")

METHOD_QUOTE = "You can send both POST and GET requests using workflows."

#: "Webhook URLs are restricted to a secure protocol and must begin with HTTPS."
URL_SCHEME = "https"

URL_RULE = "Webhook URLs are restricted to a secure protocol and must begin with HTTPS."

#: The authentication types the research enumerates, and nothing else:
#:
#: * ``signature`` - "Include request signature in header", with a HubSpot App ID.
#: * ``api_key``   - "Set the value of API key name to ``Authorization``. Set the
#:   value of API key location to ``Request Header``" - or query params.
#: * ``bearer``    - "The secret value must be in the format ``Bearer [YOUR_TOKEN]``".
AUTH_MODES: tuple[str, ...] = ("signature", "api_key", "bearer")

AUTH_LABELS: dict[str, str] = {
    "signature": "Include request signature in header",
    "api_key": "API key",
    "bearer": "Authorization: Bearer [YOUR_TOKEN]",
}

AUTH_RULES: dict[str, str] = {
    "signature": (
        "To use a request signature in your webhook header: click the Authentication "
        "type dropdown, select Include request signature in header, then enter your "
        "HubSpot App ID."
    ),
    "api_key": (
        "Set the value of API key name to Authorization. Set the value of API key "
        "location to Request Header - or to a query parameter."
    ),
    "bearer": "The secret value must be in the format `Bearer [YOUR_TOKEN]`.",
}

#: Where an ``api_key`` may arrive. "or request header" against "API key (query
#: params or a request header)" in the research's own summary of the options.
API_KEY_LOCATIONS: tuple[str, ...] = ("header", "query")

#: The literal the vendor documentation spells out for a bearer credential. The
#: scheme is compared case-insensitively (RFC 7235 makes it so) but the space and
#: the token are compared exactly.
BEARER_PREFIX = "Bearer "

#: The header the vendor's request signature is carried in, and the companion
#: header carrying the timestamp the signature covers. Not published by this
#: workflow's three sources - see the ``hubspot-request-signature-scheme``
#: inference in :mod:`dsr.crm_outbound_webhooks.inferences`.
SIGNATURE_HEADER = "x-hubspot-signature-v3"
SIGNATURE_TIMESTAMP_HEADER = "x-hubspot-request-timestamp"

#: "To include all properties, select Include all [object] properties. To include
#: only specific properties: Select Customize request body." Two modes.
BODY_MODES: tuple[str, ...] = ("include_all", "customize")

#: What an automation that adds no body override records. Not a third researched
#: body mode - the researched pair is closed, and this is the *absence* of an
#: override rather than a third choice in the CRM's dropdown. It says the
#: endpoint's own body mode applies to this automation, which is what a workflow
#: that customises nothing does in the CRM.
BODY_INHERIT = "inherit"

BODY_LABELS: dict[str, str] = {
    "include_all": "Include all [object] properties",
    "customize": "Customize request body",
}

BODY_QUOTE = (
    "To include all properties, select **Include all [object] properties**. To "
    "include only specific properties: Select **Customize request body** ... To "
    "customize the request body using a HubSpot property, enter the Key and select "
    "a property ... To add a static field, enter the Key and Value. To add another "
    "property, click **Add static value**."
)

#: The object the automation is about. The research's own start conditions name
#: two of the three: "deal stage becomes 'Contract Sent'" (a deal) "or a contact
#: property changes" (a contact). ``companies`` is here because "Include all
#: [object] properties" is a control the object picker fills, and a room whose
#: account-level property changes is the same automation with a different object.
OBJECTS: tuple[str, ...] = ("deals", "contacts", "companies")

OBJECT_LABELS: dict[str, str] = {
    "deals": "Deals",
    "contacts": "Contacts",
    "companies": "Companies",
}

#: Endpoint lifecycle. "Rep clicks **Save**, then **Publish**".
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUSES: tuple[str, ...] = (STATUS_DRAFT, STATUS_PUBLISHED)

PUBLISH_RULE = "Workflows must be **published** to go live."

#: "To set up webhook actions in workflows, users must have Edit permissions for
#: workflows or Super Admin permissions. To publish workflows, users must have
#: Publish permissions for workflows."
PERMISSIONS: tuple[str, ...] = ("edit_workflows", "publish_workflows", "super_admin")

PERMISSION_LABELS: dict[str, str] = {
    "edit_workflows": "Edit permissions for workflows",
    "publish_workflows": "Publish permissions for workflows",
    "super_admin": "Super Admin",
}

PERMISSION_QUOTE = (
    "To set up webhook actions in workflows, users must have Edit permissions for "
    "workflows or Super Admin permissions. To publish workflows, users must have "
    "Publish permissions for workflows."
)

#: The permission that writes the endpoint configuration, and the one that
#: publishes it. Two, not one: the source separates them with "or" for setup and
#: does not mention Super Admin for publishing.
SETUP_PERMISSIONS: tuple[str, ...] = ("edit_workflows", "super_admin")
PUBLISH_PERMISSIONS: tuple[str, ...] = ("publish_workflows",)

#: "You can create up to 1,000 webhook subscriptions per app."
SUBSCRIPTION_LIMIT_PER_APP = 1000

SUBSCRIPTION_QUOTE = (
    "You can create up to 1,000 webhook subscriptions per app. Webhook calls made "
    "via workflows do not count towards the API rate limit."
)

#: "HubSpot regulates webhook traffic separately from other workflow processes ...
#: When a webhook is slow or times out, the workflow action may take longer than
#: expected to execute." The room's obligation is to answer quickly, so the whole
#: receive path is one transaction and no outbound call.
SLOW_SENDER_QUOTE = (
    "HubSpot regulates webhook traffic separately from other workflow processes. This "
    "is done to streamline workflow and webhook performance. When a webhook is slow or "
    "times out, the workflow action may take longer than expected to execute."
)

# --------------------------------------------------------------------------- #
# Delivery outcomes and their reasons
# --------------------------------------------------------------------------- #

OUTCOME_ACCEPTED = "accepted"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_REFUSED = "refused"
OUTCOMES: tuple[str, ...] = (OUTCOME_ACCEPTED, OUTCOME_DUPLICATE, OUTCOME_REFUSED)

#: What a delivery did to the deal panel, beside the outcome. A delivery that
#: arrived with nothing new is still ``accepted`` - it was a real, authenticated,
#: well-formed request - but it changed nothing and told nobody.
EFFECT_STAGE_CHANGED = "stage_changed"
EFFECT_STAGE_UNCHANGED = "stage_unchanged"
EFFECT_PROPERTIES_ONLY = "properties_only"
EFFECT_NONE = "none"
EFFECTS: tuple[str, ...] = (
    EFFECT_STAGE_CHANGED,
    EFFECT_STAGE_UNCHANGED,
    EFFECT_PROPERTIES_ONLY,
    EFFECT_NONE,
)

#: Every reason a delivery can be refused, and every reason an accepted delivery
#: can report about its own body. Served so a client can label them without
#: compiling a list of its own.
REASONS: dict[str, str] = {
    "endpoint_not_registered": "The room has no inbound endpoint, so no URL was ever given to the CRM.",
    "endpoint_not_published": "The endpoint was saved but never published, and workflows must be published to go live.",
    "signature_mismatch": "The request signature did not match the one computed over this method, URI, body and timestamp.",
    "signature_missing": "The endpoint authenticates by request signature and the request carried no signature header.",
    "signature_stale": "The timestamp the signature covers is outside the replay window.",
    "api_key_missing": "The endpoint authenticates by API key and the request carried no key in the configured place.",
    "api_key_mismatch": "The API key did not match the one stored for this endpoint.",
    "bearer_missing": "The endpoint authenticates by bearer token and the request carried no Authorization header.",
    "bearer_malformed": "The Authorization header was not in the form `Bearer [YOUR_TOKEN]`.",
    "bearer_mismatch": "The bearer token did not match the one stored for this endpoint.",
    "malformed_payload": "The body could not be read as a payload of the versioned contract.",
    "unsupported_payload_version": "The body declared a contract version this endpoint does not serve.",
    "object_mismatch": "The payload is about a different object than the endpoint was configured for.",
    "deal_unresolved": "The payload carried no usable external id, and the room has more than one deal to choose between.",
    "no_stage_or_properties": "The payload carried neither the stage property nor any other property.",
    "body_key_missing": "A key the endpoint's body definition names was absent from the payload.",
    "automation_unknown": "The payload named an automation this room has not registered. Applied anyway.",
    "duplicate_delivery": "This delivery was already recorded; the first one was kept.",
}

#: The keys the versioned contract reserves for its own control fields. Everything
#: else in a body is a property, whichever of the two researched body modes
#: produced it - see :mod:`dsr.crm_outbound_webhooks.payloads`.
RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "v",
        "version",
        "object",
        "objectType",
        "object_id",
        "objectId",
        "recordId",
        "id",
        "delivery_id",
        "automation",
        "occurred_at",
        "timestamp",
    }
)

#: The keys an external record id is looked for under, in order, before the
#: endpoint's configured ``id_key`` is consulted. Named keys rather than a guess
#: at a vendor's field names, so a team whose CRM calls it something else sets
#: ``id_key`` and nothing else changes.
ID_KEYS: tuple[str, ...] = ("objectId", "object_id", "id", "recordId")

#: The contract version this endpoint serves. It is in the path, so a payload that
#: declares no version is read as this one; a payload that declares a *different*
#: one is refused.
CONTRACT_VERSION = 1

#: How far either side of now a signed request may be timestamped. Part of the
#: same inference as the signature scheme itself.
REPLAY_WINDOW_SECONDS = 300

#: The page the research names: "room **Webhook settings** page showing the
#: endpoint URL and secret".
SETTINGS_PAGE_NOTE = (
    "The room's Webhook settings page shows the endpoint URL and the secret. Because "
    "the room verifies the request signature, the endpoint needs no per-workflow "
    "secret - one endpoint serves any number of CRM-side automations."
)


def default_id_key(obj: str) -> str:
    """The property a room reads an external id from, per object."""
    return {"deals": "deal_id", "contacts": "contact_id", "companies": "company_id"}.get(obj, "id")


def default_stage_key(obj: str) -> str:
    """The property a room reads the stage from, per object.

    A deal's stage is ``stage``. A contact and a company have no pipeline stage,
    so the researched second start condition - "a contact property changes" - has
    no stage to read, and the room maps whatever property the endpoint was
    pointed at instead.
    """
    return "stage" if obj == "deals" else "property"


def describe() -> dict[str, Any]:
    """The whole vocabulary, served as data."""
    return {
        "methods": [{"name": name, "note": METHOD_QUOTE} for name in METHODS],
        "url": {"scheme": URL_SCHEME, "rule": URL_RULE},
        "auth_modes": [
            {
                "mode": mode,
                "label": AUTH_LABELS[mode],
                "rule": AUTH_RULES[mode],
                "requires_app_id": mode == "signature",
                "requires_secret": mode in ("api_key", "bearer", "signature"),
                "locations": list(API_KEY_LOCATIONS) if mode == "api_key" else [],
            }
            for mode in AUTH_MODES
        ],
        "bearer_prefix": BEARER_PREFIX,
        "signature_header": SIGNATURE_HEADER,
        "signature_timestamp_header": SIGNATURE_TIMESTAMP_HEADER,
        "replay_window_seconds": REPLAY_WINDOW_SECONDS,
        "body_modes": [{"mode": mode, "label": BODY_LABELS[mode]} for mode in BODY_MODES],
        "body_inherit": BODY_INHERIT,
        "body_quote": BODY_QUOTE,
        "objects": [
            {
                "name": obj,
                "label": OBJECT_LABELS[obj],
                "id_key": default_id_key(obj),
                "stage_key": default_stage_key(obj),
            }
            for obj in OBJECTS
        ],
        "statuses": list(STATUSES),
        "publish_rule": PUBLISH_RULE,
        "permissions": [{"name": name, "label": PERMISSION_LABELS[name]} for name in PERMISSIONS],
        "setup_permissions": list(SETUP_PERMISSIONS),
        "publish_permissions": list(PUBLISH_PERMISSIONS),
        "permission_quote": PERMISSION_QUOTE,
        "subscription_limit_per_app": SUBSCRIPTION_LIMIT_PER_APP,
        "subscription_quote": SUBSCRIPTION_QUOTE,
        "slow_sender_quote": SLOW_SENDER_QUOTE,
        "outcomes": list(OUTCOMES),
        "effects": list(EFFECTS),
        "reasons": dict(REASONS),
        "contract_version": CONTRACT_VERSION,
        "collections": list(COLLECTIONS),
        "settings_page_note": SETTINGS_PAGE_NOTE,
    }
