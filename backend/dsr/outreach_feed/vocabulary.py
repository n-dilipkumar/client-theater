"""The researched vocabulary of the seller activity feed, and the payload builder.

Everything in this module is either quoted from
``docs/research/digital-sales-room-workflows/wf/WF-026.md`` or is a pure function
over that quote. Nothing here reads or writes the store, and nothing here decides
policy - :mod:`dsr.outreach_feed.engine` does that.

Sourced facts
-------------
* **The endpoint.** ``POST https://api.outreach.io/api/v2/events`` with
  ``Authorization: Bearer S2S_TOKEN``.
* **The body.** A JSON:API ``event`` create, verbatim from the research::

      {"data": {"type": "event",
                "attributes": {"name": "my-app:my-event",
                               "externalUrl": "…",
                               "body": "…"},
                "relationships": {"prospect": {"data": {"type": "prospect",
                                                          "id": "PROSPECT_ID"}}}}}

* **Event names are app-scoped.** "Event names are app-scoped
  (``<app identifier>:<event id>``)".
* **The template is configured, not sent.** "The event card will also contain the
  template string you have configured for the event. In the template string you
  can use the ``{{prospect}}`` placeholder which Outreach will replace with a
  link to the prospect." The ``{{prospect}}`` substitution happens on Outreach's
  side of a configuration the researcher made in the developer portal, so the
  template never appears in the payload. The payload carries the *name*, the
  *externalUrl* deep link back into the DSR, and an *optional* ``body``.
* **Localised descriptions are templates too.** "localized descriptions are
  configured as templates".
* **The webhook contract.** ``mailing*`` resources "created updated destroyed
  bounced delivered opened replied"; subscriptions are created with
  ``payloadVersion: 2``; deliveries carry an ``Outreach-Webhook-Signature`` HMAC
  header; "The timeout while waiting for response is set to 5 seconds."

What is *not* here
-----------------
The Salesloft endpoints in the research's ``extensibility`` field, the Mailing
links custom tracker, and the tab/tile client extensions. They are named, with
their researched descriptions and an explicit ``implemented: false``, in
:func:`adjacent_surfaces` - served at ``/api/wf-026/vocabulary`` - so a reviewer
can see they were read and deliberately left out rather than missed.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from dsr.outreach_feed.errors import EventNameError, FeedError, TemplateError

# --------------------------------------------------------------------------- #
# The wire contract
# --------------------------------------------------------------------------- #

#: [sourced] The one endpoint this workflow writes to.
EVENTS_ENDPOINT = "https://api.outreach.io/api/v2/events"

#: [sourced] Where the matching inbound subscription is created. Served as data so
#: whoever sets the subscription up does not have to read this module.
WEBHOOKS_ENDPOINT = "https://api.outreach.io/api/v2/webhooks"

#: [sourced] ``"data": {"type": "event", ...}`` and the prospect relationship's
#: own type. Both are part of the researched body and are not configurable.
EVENT_RESOURCE_TYPE = "event"
PROSPECT_RESOURCE_TYPE = "prospect"

#: [sourced] The researched event-name example, reproduced so a client and a
#: reviewer read the same shape this module enforces.
EXAMPLE_EVENT_NAME = "my-app:my-event"

#: [sourced] "In the template string you can use the {{prospect}} placeholder which
#: Outreach will replace with a link to the prospect." It is the only placeholder
#: the research documents, so it is the only one this build accepts.
PROSPECT_PLACEHOLDER = "{{prospect}}"
KNOWN_PLACEHOLDERS: tuple[str, ...] = (PROSPECT_PLACEHOLDER,)

#: [sourced] The research lists the resources as "mailing* created updated destroyed
#: bounced delivered opened replied" - a glob over the ``mailing`` family. The
#: family is the resource and the seven words are its types.
MAILING_FAMILY = "mailing"

#: [sourced] "mailing* created updated destroyed bounced delivered opened replied".
#: Order is the research's order and is asserted in the tests.
MAILING_RESOURCES: tuple[str, ...] = (
    "created",
    "updated",
    "destroyed",
    "bounced",
    "delivered",
    "opened",
    "replied",
)

#: The subset of :data:`MAILING_RESOURCES` that carries *intent* rather than
#: object lifecycle. The workflow is "write DSR events into the seller activity
#: feed" and its ``automations`` field is about intent coming back, so these four
#: are the ones a rep acts on; the other three are recorded but never alerted on.
INTENT_RESOURCES: tuple[str, ...] = ("bounced", "delivered", "opened", "replied")

#: [sourced] "POST https://api.outreach.io/api/v2/webhooks with ``payloadVersion: 2``
#: returns a ``beforeUpdate`` block."
PAYLOAD_VERSION = 2

#: [sourced] "deliveries carry an ``Outreach-Webhook-Signature`` HMAC header."
SIGNATURE_HEADER = "Outreach-Webhook-Signature"

#: [sourced] "The timeout while waiting for response is set to 5 seconds." That is
#: the *sender's* budget, so it is ours in both directions: the outbound write
#: gives up after this long, and the inbound handler is written to answer inside
#: it because the sender will not wait longer.
SENDER_TIMEOUT_SECONDS = 5.0

# --------------------------------------------------------------------------- #
# Field locations in the DSR event stream
# --------------------------------------------------------------------------- #

#: Where the fields this workflow reads live inside a DSR activity record. The
#: store declares no schema, so these are *discovered*, not declared - the same
#: approach :mod:`dsr.analytics` takes, with its own synonym lists rather than
#: borrowing that package's, because a feature does not import another feature's
#: module and a team may reshape the stream for this integration alone.
#:
#: An app record may override any of these under its own ``field_map``; a team
#: that names its buyer actions differently ships a record, not a change here.
EVENT_FIELDS: dict[str, list[str]] = {
    "action": ["action", "event", "activity", "kind", "type"],
    "person": ["person", "user", "visitor", "email", "actor", "by"],
    "target": ["target", "document", "document_title", "title", "page", "page_title"],
    "occurred_at": ["occurred_at", "at", "happened_at", "timestamp", "recorded_at"],
    "seconds": ["seconds_on_page", "seconds", "dwell_seconds", "duration_seconds"],
}

# --------------------------------------------------------------------------- #
# Event names
# --------------------------------------------------------------------------- #

#: [sourced] ``<app identifier>:<event id>``. A single colon, two non-empty parts,
#: no whitespace in either. The character sets are narrower than "any non-space
#: text" - an identifier that reaches an Outreach app's configuration by way of a
#: URL segment is conventionally these - and the narrower reading is recorded as
#: an inference rather than presented as sourced.
_NAME_PART = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
EVENT_NAME_SHAPE = re.compile(rf"^(?P<app>{_NAME_PART}):(?P<event>{_NAME_PART})$")

EVENT_NAME_FORMAT = "<app identifier>:<event id>"


def parse_event_name(name: Any) -> tuple[str, str]:
    """Split a researched event name into ``(app identifier, event id)``.

    Raises :class:`EventNameError` with the format in the message, because a
    caller that guessed the shape wrong should not have to read this docstring to
    find out.
    """
    if not isinstance(name, str) or not name.strip():
        raise EventNameError(
            f"event name is required and must look like {EVENT_NAME_FORMAT!r} "
            f"(for example {EXAMPLE_EVENT_NAME!r})"
        )
    text = name.strip()
    match = EVENT_NAME_SHAPE.match(text)
    if not match:
        raise EventNameError(
            f"event name {text!r} is not {EVENT_NAME_FORMAT!r}: exactly one colon, "
            f"both parts non-empty, no whitespace (for example {EXAMPLE_EVENT_NAME!r})"
        )
    return match.group("app"), match.group("event")


def require_event_name(name: Any, *, app_identifiers: Sequence[str] | None = None) -> str:
    """Validate an event name, and that it is scoped to an app that exists.

    [sourced] "Event names are app-scoped". Passing ``app_identifiers`` turns that
    from a shape check into a scope check: the identifier in the name has to be an
    app this installation has actually registered, because the name has to match
    the app whose custom event was configured in the developer portal.
    """
    app, event = parse_event_name(name)
    if app_identifiers is not None and app not in set(app_identifiers):
        known = ", ".join(sorted(app_identifiers)) or "none registered yet"
        raise EventNameError(
            f"event name {str(name).strip()!r} is scoped to app {app!r}, which is not "
            f"registered here (registered apps: {known})"
        )
    return f"{app}:{event}"


# --------------------------------------------------------------------------- #
# Card templates
# --------------------------------------------------------------------------- #

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def placeholders_in(template: Any) -> list[str]:
    """Every ``{{...}}`` token in a template, normalised to ``{{token}}``."""
    if not isinstance(template, str):
        return []
    return [f"{{{{{token.strip()}}}}}" for token in _PLACEHOLDER.findall(template)]


def unknown_placeholders(template: Any) -> list[str]:
    """Placeholders in ``template`` that Outreach is not documented to replace.

    The research documents exactly one, ``{{prospect}}``. A template carrying
    anything else would render literal braces into a rep's activity feed, so a
    configuration mistake is caught when the event type is declared rather than
    when a seller notices.
    """
    return [token for token in placeholders_in(template) if token not in KNOWN_PLACEHOLDERS]


def require_template(template: Any, *, field: str = "template", required: bool = True) -> str:
    """Validate a card template, and every localisation of it.

    [sourced] The template is a developer-portal configuration and the
    ``{{prospect}}`` placeholder is the one Outreach replaces. Localised
    descriptions are templates too, so each locale is checked by the same rule.
    """
    if template is None or (isinstance(template, str) and not template.strip()):
        if required:
            raise TemplateError(
                f"{field} is required: it is the string the rep sees on the event card"
            )
        return ""
    if not isinstance(template, str):
        raise TemplateError(f"{field} must be a string, got {type(template).__name__}")
    unknown = unknown_placeholders(template)
    if unknown:
        supported = ", ".join(KNOWN_PLACEHOLDERS)
        raise TemplateError(
            f"{field} carries {', '.join(unknown)}, which Outreach is not documented to "
            f"replace. The only supported placeholder is {supported}"
        )
    return template.strip()


def require_localizations(value: Any) -> dict[str, str]:
    """Validate the ``localizations`` block: a ``{locale: template}`` map."""
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise TemplateError(
            f"localizations must be an object of locale to template, got {type(value).__name__}"
        )
    result: dict[str, str] = {}
    for locale, template in value.items():
        key = str(locale).strip()
        if not key:
            raise TemplateError("a localization needs a locale key")
        result[key] = require_template(template, field=f"localizations[{key}]")
    return result


# --------------------------------------------------------------------------- #
# The payload
# --------------------------------------------------------------------------- #

_PLACEHOLDER_IN_TEXT = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_ALLOWED_SCHEME = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def require_absolute_url(value: Any, *, field: str) -> str:
    """An absolute http(s) URL.

    The ``externalUrl`` is a deep link a seller clicks straight out of the
    activity feed, so it has to be a real absolute URL. ``javascript:`` and
    ``data:`` in particular would turn a seller's click into script execution
    inside Outreach, so anything but http(s) is refused.
    """
    if not isinstance(value, str) or not value.strip():
        raise FeedError(f"{field} is required")
    text = value.strip()
    if not _ALLOWED_SCHEME.match(text):
        raise FeedError(f"{field} must be an absolute http(s) URL, got {text!r}")
    return text


def room_deep_link(base_url: str, room_id: str) -> str:
    """The ``externalUrl`` deep link back into the DSR for one room.

    [sourced] The flow says the POST carries "an ``externalUrl`` deep link back
    into the DSR" and that "the card links back to the DSR". It does not say how
    the link is formed, so the shape ``{base}/{room_id}`` is ours - recorded as an
    inference, overridable per prospect link when a deployment routes rooms by
    slug rather than by id.
    """
    base = require_absolute_url(base_url, field="room_base_url").rstrip("/")
    room = str(room_id).strip()
    if not room:
        raise FeedError("a deep link needs a room id")
    if "/" in room or "?" in room or "#" in room:
        raise FeedError(
            f"room id {room!r} cannot be placed in a deep link path unchanged; "
            f"give the prospect link an explicit external_url instead"
        )
    return f"{base}/{room}"


def event_name_of(name: str) -> str:
    """The ``name`` attribute as it goes on the wire, trimmed and re-validated."""
    return require_event_name(name)


def build_event_payload(
    *,
    name: str,
    external_url: str,
    prospect_id: str,
    body: str | None = None,
) -> dict[str, Any]:
    """The researched JSON:API ``event`` create, byte for byte.

    ``body`` is optional and *omitted* rather than sent as ``null`` when there is
    none: the research says "In the payload you can additionally send accompanying
    text", so an event with no accompanying text has no ``body`` key. The
    ``template`` is deliberately absent - it is configured in the developer
    portal, not sent.
    """
    app, _event = parse_event_name(name)
    attributes: dict[str, Any] = {
        "name": f"{app}:{_event}",
        "externalUrl": require_absolute_url(external_url, field="externalUrl"),
    }
    if isinstance(body, str) and body.strip():
        attributes["body"] = body.strip()
    return {
        "data": {
            "type": EVENT_RESOURCE_TYPE,
            "attributes": attributes,
            "relationships": {
                "prospect": {
                    "data": {"type": PROSPECT_RESOURCE_TYPE, "id": str(prospect_id).strip()}
                }
            },
        }
    }


def render_for_seller(payload: Mapping[str, Any]) -> str:
    """One line summarising what the seller's activity feed card will say.

    The card is rendered by Outreach from the app's configured template plus the
    ``body`` we sent, so this is a preview of *our* half of the card, not a
    rendering of theirs.
    """
    data = (payload or {}).get("data") or {}
    attributes = data.get("attributes") or {}
    parts = [str(attributes.get("name") or "event")]
    if attributes.get("body"):
        parts.append(str(attributes["body"]))
    return " · ".join(parts)


def describe_adjacent_surfaces() -> dict[str, Any]:
    """The researched surfaces this workflow deliberately does not implement.

    [sourced, not implemented] Named in the research's ``extensibility`` and
    ``features_tools`` fields. Listing them with their researched descriptions and
    an explicit reason is the difference between "we read it and decided" and
    "nobody noticed".
    """
    return {
        "implemented": False,
        "note": (
            "These are named in the WF-026 research as related or alternative "
            "extensions. None is part of the four-step user flow, and building "
            "them would be inventing scope, so they are recorded here instead."
        ),
        "outreach": [
            {
                "name": "Mailing links custom tracker",
                "kind": "client extension",
                "description": (
                    "Allows you to replace the embedded links in Outreach mailings "
                    "with custom tracking URLs."
                ),
                "why_not": (
                    "It changes links inside mailings the seller already sent. This "
                    "workflow writes new events to the activity feed and does not "
                    "edit anything already in a rep's mailbox."
                ),
            },
            {
                "name": "Tab and tile extensions",
                "kind": "client extension",
                "description": (
                    "Render a DSR widget on the Prospect, Opportunity or Account detail page."
                ),
                "why_not": (
                    "An Outreach client extension is a bundle Outreach installs; it "
                    "is a different artefact from a server-to-server event write, and "
                    "the researched user flow does not include it."
                ),
            },
            {
                "name": "Text editor extension",
                "kind": "client extension",
                "description": "A rich-text editor inside the Outreach client.",
                "why_not": (
                    "The research names it among the tools a rep uses; the flow this "
                    "workflow builds is a server-side write and never renders one."
                ),
            },
        ],
        "salesloft": [
            {
                "endpoint": "POST /v2/third_party_live_feed_items",
                "description": (
                    "Creates a live feed item that can be sent to users. May only be "
                    "used by whitelisted Frontend Integrations with "
                    "notifications:write, people:read, and accounts:read scopes."
                ),
                "why_not": (
                    "It is the Salesloft equivalent of this workflow's write, and the "
                    "research gates it behind an allowlist this build cannot obtain. "
                    "Shipping an endpoint that can only ever answer 403 would be worse "
                    "than not shipping it."
                ),
            },
            {
                "endpoint": "POST /v2/live_website_tracking_parameters",
                "description": "Creates a Live Website Tracking parameter to identify a person.",
                "why_not": (
                    "It identifies a *person* for a whitelisted Salesloft frontend "
                    "integration. This workflow's data source is the Outreach "
                    "``prospect`` object, and a DSR room is linked to a prospect "
                    "explicitly rather than resolved from a tracking parameter."
                ),
            },
        ],
    }


def describe() -> dict[str, Any]:
    """The sourced vocabulary, served at ``/api/wf-026/vocabulary``.

    Published as data so a client renders its pickers from the same source the
    validator enforces against, and a reviewer can read the researched facts
    without opening a Python file.
    """
    return {
        "write": {
            "method": "POST",
            "endpoint": EVENTS_ENDPOINT,
            "headers": {"Authorization": "Bearer S2S_TOKEN", "Content-Type": "application/json"},
            "resource_type": EVENT_RESOURCE_TYPE,
            "prospect_resource_type": PROSPECT_RESOURCE_TYPE,
            "example_name": EXAMPLE_EVENT_NAME,
            "example_body": {
                "data": {
                    "type": EVENT_RESOURCE_TYPE,
                    "attributes": {
                        "name": EXAMPLE_EVENT_NAME,
                        "externalUrl": "https://rooms.example/r/room_123",
                        "body": "Viewed the Security & Compliance Pack",
                    },
                    "relationships": {
                        "prospect": {"data": {"type": PROSPECT_RESOURCE_TYPE, "id": "PROSPECT_ID"}}
                    },
                }
            },
            "sourced_quote": (
                "curl https://api.outreach.io/api/v2/events -X POST "
                '-H "Authorization: Bearer S2S_TOKEN" -d …'
            ),
        },
        "event_name": {
            "format": EVENT_NAME_FORMAT,
            "shape": EVENT_NAME_SHAPE.pattern,
            "app_scoped": True,
            "note": (
                "The name has to be app-scoped and the app identifier has to be one "
                "this installation registered, because Outreach matches it to the app "
                "whose custom event was configured in the developer portal."
            ),
        },
        "template": {
            "placeholders": list(KNOWN_PLACEHOLDERS),
            "placeholder_note": (
                "The template is configured in the Outreach developer portal and is "
                "never sent. Outreach replaces {{prospect}} with a link to the "
                "prospect when it renders the event card."
            ),
            "localized": True,
            "sent_in_payload": False,
            "sourced_quote": (
                "The event card will also contain the template string you have "
                "configured for the event. In the template string you can use the "
                "{{prospect}} placeholder which Outreach will replace with a link to "
                "the prospect."
            ),
        },
        "webhook": {
            "subscription_endpoint": WEBHOOKS_ENDPOINT,
            "resources": list(MAILING_RESOURCES),
            "intent_resources": list(INTENT_RESOURCES),
            "payload_version": PAYLOAD_VERSION,
            "signature_header": SIGNATURE_HEADER,
            "timeout_seconds": SENDER_TIMEOUT_SECONDS,
            "sourced_quote": (
                'Outreach webhook resources: "mailing* created updated destroyed '
                'bounced delivered opened replied". "The timeout while waiting for '
                'response is set to 5 seconds."'
            ),
        },
        "no_retry": {
            "vendor_retries_webhook_deliveries": False,
            "sourced_quote": (
                "Outreach does not retry webhook deliveries upon receiving any of the "
                "Status Codes including 500 Internal Server Error and 429 Too Many "
                "Requests."
            ),
            "consequence": (
                "A refused inbound delivery is gone for good, so the receiver stores "
                "the signal before it answers and never answers anything but 2xx for "
                "a payload it could read. The outbound write is the mirror image: "
                "nobody will resend it for us, so every attempt is a stored record."
            ),
        },
        "event_fields": EVENT_FIELDS,
        "event_fields_note": (
            "Field *locations* are discovered, not declared. An app record may "
            "override any of them under its own field_map, so a team that names its "
            "buyer actions differently ships a record rather than a change to this "
            "package."
        ),
        "adjacent_surfaces": describe_adjacent_surfaces(),
    }
