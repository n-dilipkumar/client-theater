"""Write DSR events into the seller activity feed (WF-026).

The researched flow, in four steps, and the domain that carries them:

``dsr.outreach_feed.vocabulary``
    The researched wire contract: the endpoint, the JSON:API body, the app-scoped
    event-name grammar, the ``{{prospect}}`` placeholder, the ``mailing*`` webhook
    resources, ``payloadVersion: 2``, the signature header, the 5 second timeout,
    and the payload builder that turns those into a request.
``dsr.outreach_feed.delivery``
    Outbound transport and the retry policy, with every attempt recorded.
``dsr.outreach_feed.webhooks``
    The inbound half: signature verification, payload-version checking, and
    normalising a delivery into a signal.
``dsr.outreach_feed.engine``
    :class:`~dsr.outreach_feed.engine.FeedPublisher` - the apps, the configured
    custom events, the prospect links, the publish sweep, the delivery log and the
    signal log.
``dsr.outreach_feed.inferences``
    Every judgement call this package makes, named and served over HTTP.
``dsr.outreach_feed.errors``
    One error hierarchy, so the HTTP layer maps each answer to one status.

Nothing in this package imports ``dsr.api``; the HTTP surface lives in
``dsr.features.wf026_write_dsr_events_into_the_seller_activ``, and nothing here
imports another feature's package either. The two shared seams it *does* use -
:class:`~dsr.store.RecordStore` and :class:`~dsr.deps.StoreDep` - are the seams
the contract names.
"""

from dsr.outreach_feed import webhooks
from dsr.outreach_feed.delivery import (
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    PostReport,
    PostResult,
    Transport,
    UrllibTransport,
    post_json,
    redact_headers,
)
from dsr.outreach_feed.engine import (
    APP_COLLECTION,
    BLOCKER_CODES,
    COLLECTIONS,
    DELIVERY_COLLECTION,
    DELIVERY_STATUSES,
    EVENT_FIELDS,
    EVENT_TYPE_COLLECTION,
    PROSPECT_COLLECTION,
    SIGNAL_COLLECTION,
    SKIP_REASONS,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    FeedPublisher,
    app_summary,
    card_text,
    delivery_key,
    delivery_summary,
    event_type_summary,
    matches,
    normalise_stream_event,
    prospect_summary,
    signal_summary,
)
from dsr.outreach_feed.errors import (
    AppError,
    EventNameError,
    EventTypeError,
    FeedError,
    FeedNotConfiguredError,
    ProspectLinkError,
    SignatureError,
    TemplateError,
    UnknownRoom,
    WebhookError,
)
from dsr.outreach_feed.inferences import (
    INFERENCES,
    SOURCED_QUOTES,
    by_id,
    describe as describe_inferences,
)
from dsr.outreach_feed.vocabulary import (
    EVENT_NAME_FORMAT,
    EVENT_NAME_SHAPE,
    EVENT_RESOURCE_TYPE,
    EVENTS_ENDPOINT,
    EXAMPLE_EVENT_NAME,
    INTENT_RESOURCES,
    KNOWN_PLACEHOLDERS,
    MAILING_FAMILY,
    MAILING_RESOURCES,
    PAYLOAD_VERSION,
    PROSPECT_PLACEHOLDER,
    PROSPECT_RESOURCE_TYPE,
    SENDER_TIMEOUT_SECONDS,
    SIGNATURE_HEADER,
    WEBHOOKS_ENDPOINT,
    build_event_payload,
    describe as describe_vocabulary,
    describe_adjacent_surfaces,
    parse_event_name,
    placeholders_in,
    require_absolute_url,
    require_event_name,
    require_localizations,
    require_template,
    room_deep_link,
    unknown_placeholders,
)

__all__ = [
    "APP_COLLECTION",
    "BLOCKER_CODES",
    "COLLECTIONS",
    "DEFAULT_BACKOFF",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TIMEOUT",
    "DELIVERY_COLLECTION",
    "DELIVERY_STATUSES",
    "EVENT_FIELDS",
    "EVENT_NAME_FORMAT",
    "EVENT_NAME_SHAPE",
    "EVENT_RESOURCE_TYPE",
    "EVENT_TYPE_COLLECTION",
    "EVENTS_ENDPOINT",
    "EXAMPLE_EVENT_NAME",
    "INFERENCES",
    "INTENT_RESOURCES",
    "KNOWN_PLACEHOLDERS",
    "MAILING_FAMILY",
    "MAILING_RESOURCES",
    "PAYLOAD_VERSION",
    "PROSPECT_COLLECTION",
    "PROSPECT_PLACEHOLDER",
    "PROSPECT_RESOURCE_TYPE",
    "SIGNAL_COLLECTION",
    "SIGNATURE_HEADER",
    "SKIP_REASONS",
    "SOURCED_QUOTES",
    "STATUS_DELIVERED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "SENDER_TIMEOUT_SECONDS",
    "WEBHOOKS_ENDPOINT",
    "AppError",
    "EventNameError",
    "EventTypeError",
    "FeedError",
    "FeedNotConfiguredError",
    "FeedPublisher",
    "PostReport",
    "PostResult",
    "ProspectLinkError",
    "SignatureError",
    "TemplateError",
    "Transport",
    "UnknownRoom",
    "UrllibTransport",
    "WebhookError",
    "app_summary",
    "build_event_payload",
    "by_id",
    "card_text",
    "delivery_key",
    "delivery_summary",
    "describe_adjacent_surfaces",
    "describe_inferences",
    "describe_vocabulary",
    "event_type_summary",
    "matches",
    "normalise_stream_event",
    "parse_event_name",
    "placeholders_in",
    "post_json",
    "prospect_summary",
    "redact_headers",
    "require_absolute_url",
    "require_event_name",
    "require_localizations",
    "require_template",
    "room_deep_link",
    "signal_summary",
    "unknown_placeholders",
    "webhooks",
]
