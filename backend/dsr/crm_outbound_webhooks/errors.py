"""One error hierarchy for the outbound-webhook package.

Every refusal this package makes is something the caller sent, so the types share
a base and the feature module registers a single handler for it. Anything that is
*not* a :class:`WebhookError` is a bug in this package and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because a malformed body and a delivery that conflicts with live state
are both this package's errors and a handler answering 400 for both would be lying
about the second. FastAPI only accepts exception handlers on the app object, so
the feature module exports this mapping as ``EXCEPTION_HANDLERS``; two features may
not map the same type, which is why the whole hierarchy hangs off one base.

The split that matters most is **refusal** against **report**. A delivery that
arrived, proved itself, and turned out to name a record this room cannot place is
a fact about the delivery, and it is recorded in the log with a reason rather than
thrown away - see :class:`UnresolvedDeal`. But a delivery that could not be
authenticated writes nothing at all, because the one guarantee an unauthenticated
caller must not have is a way to make this product write rows.
"""

from __future__ import annotations


class WebhookError(ValueError):
    """A webhook endpoint cannot be configured, published, or delivered to."""

    code = "crm_webhook_error"
    status = 400


# --------------------------------------------------------------------------- #
# Configuring the endpoint (steps 1 to 4, as the CRM-side editor records them)
# --------------------------------------------------------------------------- #


class InvalidEndpointUrl(WebhookError):
    """The endpoint URL is not an HTTPS URL.

    Quoted: *"Webhook URLs are restricted to a secure protocol and must begin with
    HTTPS."* The CRM refuses to save such a URL, so a room that accepted one would
    be holding a row the researched tool will never let the rep complete.
    """

    code = "endpoint_url_must_be_https"


class UnsupportedMethod(WebhookError):
    """A method other than POST or GET.

    Quoted: *"You can send both POST and GET requests using workflows."* Two, and
    the room serves both. Nothing else is a method this researched action can
    send, so a third is refused rather than stored looking armed.
    """

    code = "unsupported_method"


class UnknownAuthMode(WebhookError):
    """An authentication type outside the three the research names.

    The researched set is closed and enumerated: *"Include request signature in
    header"* with a HubSpot App ID, an ``API key`` in query params or a request
    header, or an ``Authorization`` header set to a secret in the form
    ``Bearer [YOUR_TOKEN]``. An endpoint whose authentication this build cannot
    verify is an endpoint nobody can call, so it is refused on save.
    """

    code = "unknown_auth_mode"


class InvalidAuthSetting(WebhookError):
    """A setting within a known authentication type that cannot be honoured.

    The API key's *location* is the case: *"Set the value of API key location to
    Request Header"* names two places it can go, and a third would send the key
    somewhere this endpoint never looks - so every request would be refused with a
    401 and the rep would have nothing to change.
    """

    code = "invalid_auth_setting"


class MissingAppId(WebhookError):
    """Signature authentication with no App ID.

    *"Then, enter your HubSpot App ID."* The App ID is what the room binds the
    signature to, so an endpoint that asks for a signature check without one has
    nothing to check against.
    """

    code = "signature_auth_requires_app_id"


class MissingSecret(WebhookError):
    """An authentication mode that needs a shared secret, with none stored."""

    code = "endpoint_secret_required"


class UnknownBodyMode(WebhookError):
    """A body mode outside the two the research names.

    *"To include all properties, select **Include all [object] properties**. To
    include only specific properties: Select **Customize request body**."* Two,
    and the room reads both - see :mod:`dsr.crm_outbound_webhooks.payloads`.
    """

    code = "unknown_body_mode"


class MalformedBody(WebhookError):
    """The body definition is not a table the CRM's body editor could produce.

    The researched editor is a table: *"enter the Key and select a property"*, and
    *"To add a static field, enter the Key and Value. To add another property, click
    **Add static value**."* A row with neither a property nor a static value has
    nothing to send.
    """

    code = "malformed_body_definition"


class InvalidTrigger(WebhookError):
    """A workflow start condition that names no property, or no change.

    The research's own two: *"deal stage becomes 'Contract Sent'"* is a property
    equalling a value, and *"a contact property changes"* is a property changing.
    Both name a property, and this is the one part of the CRM-side action the room
    cannot act on or report without.
    """

    code = "malformed_start_condition"


class InvalidRequest(WebhookError):
    """A request this route needs one more field for.

    The only such case in the researched flow, and it is here rather than letting a
    ``KeyError`` reach the client: an automation with no name cannot be found by a
    rep looking for it, and an acknowledgement that names nothing acknowledges
    nothing.
    """

    code = "invalid_request"


# --------------------------------------------------------------------------- #
# Prerequisites and state conflicts
# --------------------------------------------------------------------------- #


class EndpointExists(WebhookError):
    """The room already has an endpoint.

    The research's extensibility line is the reason this is a refusal rather than
    a second row: *"The room exposes one inbound endpoint per tenant with a
    versioned payload contract, so any number of CRM-side automations can target
    it."* Many automations, one endpoint - and *"it does not need a per-workflow
    secret"* is why a second endpoint would defeat the design.
    """

    code = "endpoint_already_registered"
    status = 409


class MissingPermission(WebhookError):
    """The caller lacks the permission the researched step requires.

    *"To set up webhook actions in workflows, users must have Edit permissions for
    workflows or Super Admin permissions. To publish workflows, users must have
    Publish permissions for workflows."* 403, and the message names the exact
    permission strings that would have been enough.
    """

    code = "permission_required"
    status = 403


class NoEndpoint(WebhookError):
    """The room has no endpoint registered.

    Nothing is written: there is no URL for the CRM to have been given, so a
    delivery to this path is a request nobody asked for rather than a fact about
    a configured integration.
    """

    code = "endpoint_not_registered"
    status = 409


class EndpointNotPublished(WebhookError):
    """A delivery arrived at an endpoint that was saved but never published.

    *"Workflows must be **published** to go live."* The room applies the rule it
    quotes, and the delivery is recorded rather than dropped: a rep who forgot to
    click Publish needs to see that their automation reached a room and was turned
    away, and the delivery log is where they will look.
    """

    code = "endpoint_not_published"
    reason = "endpoint_not_published"
    status = 409


class EndpointStateConflict(WebhookError):
    """A lifecycle change the endpoint's current state does not allow.

    Publishing a published endpoint, or unpublishing one that was never published.
    Both are 409: the request was well-formed and conflicts with state that
    already exists.
    """

    code = "endpoint_state_conflict"
    status = 409


class AlreadyPublished(EndpointStateConflict):
    """Publishing an endpoint that is already published."""

    code = "endpoint_already_published"


class AutomationLimitReached(WebhookError):
    """The app already carries the maximum number of webhook subscriptions.

    *"You can create up to 1,000 webhook subscriptions per app."* The 1,000 is the
    vendor's and it is per *app*, so the count is taken across every room bound to
    that App ID rather than per room - otherwise the cap would be enforced per
    tenant and would not be the cap the research quotes.
    """

    code = "app_webhook_subscription_limit_reached"
    status = 409


class UnknownAutomation(WebhookError):
    """A change to an automation that is not registered, or was retired.

    Soft-deleted rather than destroyed, so the deliveries that named it still
    resolve - the same reason ``dsr.db.audited`` keeps the row behind a soft
    delete, and the same reason the audit-source rule exists.
    """

    code = "automation_not_found"
    status = 404


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #


class UnauthenticatedDelivery(WebhookError):
    """The request did not prove it came from the room's own endpoint.

    401. And, unlike every other refusal here, **nothing is written**: not the
    delivery, not the deal, not a notice. A request that failed authentication is
    the one input to this product that has not been shown to be entitled to
    anything, and the row it would leave behind is indistinguishable from a real
    one to everyone who reads the log afterwards. Recorded as the
    ``unauthenticated-deliveries-write-nothing`` inference.
    """

    code = "delivery_unauthenticated"
    status = 401


class MalformedPayload(WebhookError):
    """The body cannot be read as a payload of the versioned contract.

    *"The room exposes one inbound endpoint per tenant with a versioned payload
    contract"*, so a body that is not an object is not a payload and is refused
    rather than half-applied.
    """

    code = "malformed_payload"
    reason = "malformed_payload"


class EmptyPayload(WebhookError):
    """A well-formed payload with nothing in it.

    Both researched body modes send at least one key - *"Include all [object]
    properties"* sends every property, and *"Customize request body"* is a table of
    keys - so a payload with no properties is a request that was never going to
    tell this room anything, and recording it as a delivery that changed nothing
    would be noise dressed as history.
    """

    code = "payload_carries_nothing"
    reason = "no_stage_or_properties"


class UnsupportedPayloadVersion(WebhookError):
    """The body declares a contract version this build does not serve.

    The endpoint path carries the version, so this only fires when a payload
    declares a *different* one - which is exactly the case a versioned contract
    exists to make loud instead of ambiguous.
    """

    code = "unsupported_payload_version"
    reason = "unsupported_payload_version"


class ObjectMismatch(WebhookError):
    """The payload is about a different object than the endpoint was configured for.

    The researched body control is *"Include all **[object]** properties"*: the
    object is part of the automation, and a payload whose ``object`` contradicts
    the endpoint is a CRM-side action aimed at the wrong automation.
    """

    code = "object_mismatch"
    reason = "object_mismatch"
    status = 409


class UnresolvedDeal(WebhookError):
    """The delivery named a record this room cannot place.

    *"The room's endpoint verifies the signature, resolves the record, and updates
    the buyer's room state."* A payload with no usable external id, in a room with
    more than one deal, cannot be resolved - and that is a fact about the delivery
    worth keeping, so it is recorded with this reason and answered 409.
    """

    code = "deal_unresolved"
    reason = "deal_unresolved"
    status = 409
