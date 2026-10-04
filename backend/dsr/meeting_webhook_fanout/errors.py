"""Every refusal this package makes, in one hierarchy.

Every refusal is something the caller sent, so the types share a base and the
feature module registers a single handler for it. Anything that is *not* a
:class:`MeetingWebhookError` is a bug in this package and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because a malformed subscriber URL and a subscription that names a room
that does not exist are both this package's errors and a handler answering 400
for both would be lying about the second. FastAPI only accepts exception handlers
on the app object, so the feature module exports the mapping as
``EXCEPTION_HANDLERS``; two features may not map the same type, which is why the
whole hierarchy hangs off one base.
"""

from __future__ import annotations


class MeetingWebhookError(ValueError):
    """A meeting webhook cannot be registered, or an event cannot be sent."""

    code = "meeting_webhook_error"
    status = 400


# --------------------------------------------------------------------------- #
# Configuring a subscription
# --------------------------------------------------------------------------- #


class InvalidSubscriberUrl(MeetingWebhookError):
    """The subscriber URL is not one this deployment accepts.

    The research gives two deployment modes and they disagree. Quoted: *"Cal.com
    SaaS: Only HTTPS URLs are accepted. HTTP, private/internal IP addresses (e.g.,
    10.x.x.x, 192.168.x.x, 127.0.0.1), and localhost are blocked. Self-hosted:
    Both HTTP and HTTPS URLs are accepted, and private IP addresses are allowed
    for internal webhooks."* The mode decides which rule applies, and the message
    names the mode so a reader does not have to guess which rule they hit.
    """

    code = "subscriber_url_rejected"


class InvalidEventType(MeetingWebhookError):
    """An event type outside the three the research names.

    *"picks a type: **For New Meeting**, **For Meeting Update**, or **For Canceled
    Meeting**"*. Three, and the room serves exactly those. A fourth would be
    stored looking armed, and nothing would ever fire it.
    """

    code = "unknown_event_type"


class MissingSecret(MeetingWebhookError):
    """A room asked to sign with no secret to sign with.

    Not a UI problem: the research says the secret comes from support. Quoted:
    *"Optionally emails support to obtain the tenant's HMAC signing secret (not
    shown in the UI)."* So the create route supplies one, and this only fires
    when a room is asked to send and has neither a stored key nor a supplied one.
    """

    code = "signing_secret_required"


class AlreadyExists(MeetingWebhookError):
    """The room already has a subscription with this URL and this event type.

    The research says several types may share one URL and several URLs may serve
    one type, so the pair is the identity rather than either half. A second row
    for the same pair would deliver the same event twice to the same address, and
    a subscriber counting meetings would count them wrong.
    """

    code = "subscription_already_exists"
    status = 409


class InvalidRequest(MeetingWebhookError):
    """A request this route needs one more field for.

    A subscription with no URL has nothing to POST to, and an event with no
    meeting has nothing to serialise. Both are here rather than letting a
    ``KeyError`` reach the client.
    """

    code = "invalid_request"


# --------------------------------------------------------------------------- #
# Prerequisites
# --------------------------------------------------------------------------- #


class NoRoom(MeetingWebhookError):
    """The room these rows would belong to does not exist.

    404. Every other write in this package is room-scoped, so this is the one
    refusal that stops rather than refuses.
    """

    code = "unknown_room"
    status = 404


class UnknownSubscription(MeetingWebhookError):
    """A subscription this room does not have, or has retired.

    Soft-deleted rather than destroyed, so the deliveries that named it still
    resolve. The same reason ``dsr.db.audited`` keeps the row behind a soft
    delete.
    """

    code = "subscription_not_found"
    status = 404


class UnknownEvent(MeetingWebhookError):
    """An event this room does not have, or has retired."""

    code = "event_not_found"
    status = 404


class UnknownDelivery(MeetingWebhookError):
    """A delivery attempt this room does not have.

    A real type rather than ``MeetingWebhookError(..., code="delivery_not_found")``
    at the raise site. That spelling looks like it sets the code, and it does not:
    the base inherits ``ValueError.__init__``, which takes no ``code`` keyword, so
    the call raised ``TypeError`` and the route answered 500. A 404 belongs in the
    hierarchy beside the two it sits next to, where ``status`` and ``code`` are
    class attributes and a handler can read them off any instance.
    """

    code = "delivery_not_found"
    status = 404
