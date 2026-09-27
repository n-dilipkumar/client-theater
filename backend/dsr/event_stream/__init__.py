"""WF-025: stream workspace activity events to your own systems in real time.

The public surface is :class:`~dsr.event_stream.stream.EventStream`. Everything
else in the package is an implementation detail of it. The HTTP surface is not
here: the feature host mounts
``dsr/features/wf025_stream_workspace_activity_events_to_yo.py``, which owns the
routes under ``/api/wf-025`` and the mapping of
:class:`~dsr.event_stream.errors.EventStreamError` to a response.

Implemented from ``docs/research/digital-sales-room-workflows/wf/WF-025.md``,
whose research source is section 10 of ``docs/research/raw/analytics-intent.md``.

What is researched and what is not
----------------------------------
Sourced, and implemented as researched: the subscription-type vocabulary; the
``webhook-event`` payload with ``occurredAt``, the three property fields and
``associatedObjects``; the eight associated-object kinds; the rule that
**anonymous activity omits ``user``**; that ``presentation.*`` is share-link
activity only; the ``asset.*`` snapshot with ``trackingEnabled``;
``formQuestions`` + ``formQuestionResponses`` with ``file_upload``; the
one-hour presigned expiry; the account-``admin`` requirement to create a
webhook; the verifying POST; the 10-second response timeout; the 26-retry
ladder; signing-secret rotation with a previous-signature header; the
``properties`` parameter and its omission rule; ``429`` on rate limit.

Not researched, and therefore this build's own choices - each named, bounded,
and served at ``/api/wf-025/inferences`` so a reviewer can disagree with one by
name instead of finding it in a diff: the HTTP routes, the collection names, the
header names, the delivery state machine, the retry ladder's implementation as a
schedule rather than a sleep, the secret's storage, the filter grammar, the
rate-limit allowance, and the backfill-to-collection mapping.

The one decision most worth arguing with is
``event-type-set-is-a-floor``: the research says subscription types
"**include**" the published names, so a type outside the set is accepted and
flagged rather than refused. See :mod:`dsr.event_stream.inferences`.
"""

from __future__ import annotations

from dsr.event_stream import filters, payloads  # noqa: F401 - re-exported for callers
from dsr.event_stream.backfill import RateLimiter
from dsr.event_stream.delivery import (
    DELIVERY_STATES,
    RETRY_LADDER,
    DeliveryReport,
    DeliveryResult,
    Transport,
    UrllibTransport,
    attempt_delivery,
    attempts_remaining,
    retry_delay,
)
from dsr.event_stream.errors import (
    DeliveryError,
    EventPayloadError,
    EventStreamError,
    FilterError,
    NotPermitted,
    RateLimited,
    SubscriptionError,
    TargetError,
    VocabularyError,
)
from dsr.event_stream.inferences import INFERENCES, describe as describe_inferences
from dsr.event_stream.registry import (
    DELIVERY_COLLECTION,
    EVENT_COLLECTION,
    SUBSCRIPTION_COLLECTION,
    WEBHOOK_COLLECTION,
    EndpointBook,
    SubscriptionBook,
    summarise_subscription,
    summarise_webhook,
)
from dsr.event_stream.signing import (
    PREVIOUS_SIGNATURE_HEADER,
    SIGNATURE_HEADER,
    generate_secret,
    mask,
    sign,
    verify,
)
from dsr.event_stream.stream import ROLE_ADMIN, EventStream
from dsr.event_stream.targets import (
    PRESIGNED_TTL_SECONDS,
    is_expired,
    validate_target_url,
)
from dsr.event_stream.vocabulary import (
    ASSOCIATED_OBJECTS,
    EVENT_TYPES,
    MAX_RETRIES,
    PULL_RESOURCES,
    SEISMIC_EVENTS,
    vocabulary,
)

__all__ = [
    "EventStream",
    "ROLE_ADMIN",
    "EventStreamError",
    "VocabularyError",
    "FilterError",
    "TargetError",
    "NotPermitted",
    "SubscriptionError",
    "EventPayloadError",
    "DeliveryError",
    "RateLimited",
    "EndpointBook",
    "SubscriptionBook",
    "DeliveryResult",
    "DeliveryReport",
    "Transport",
    "UrllibTransport",
    "attempt_delivery",
    "retry_delay",
    "attempts_remaining",
    "RETRY_LADDER",
    "MAX_RETRIES",
    "DELIVERY_STATES",
    "RateLimiter",
    "SIGNATURE_HEADER",
    "PREVIOUS_SIGNATURE_HEADER",
    "generate_secret",
    "mask",
    "sign",
    "verify",
    "PRESIGNED_TTL_SECONDS",
    "is_expired",
    "validate_target_url",
    "EVENT_TYPES",
    "ASSOCIATED_OBJECTS",
    "SEISMIC_EVENTS",
    "PULL_RESOURCES",
    "vocabulary",
    "INFERENCES",
    "describe_inferences",
    "summarise_webhook",
    "summarise_subscription",
    "filters",
    "payloads",
    "WEBHOOK_COLLECTION",
    "SUBSCRIPTION_COLLECTION",
    "EVENT_COLLECTION",
    "DELIVERY_COLLECTION",
]
