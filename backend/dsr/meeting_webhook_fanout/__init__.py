"""WF-066: fan out meeting lifecycle events via signed webhooks.

The domain behind the researched workflow. The research document is the
specification: ``docs/research/digital-sales-room-workflows/wf/WF-066.md``, built
from section 16 of ``docs/research/raw/scheduling-meetings.md``.

The shape of it
---------------
An admin registers a subscriber URL against one of the three researched event
types. When a meeting is created, updated or cancelled, this package serialises
the meeting payload once, signs the exact bytes, and POSTs them to every enabled
subscription naming that event type.

* :mod:`~dsr.meeting_webhook_fanout.vocabulary` - the researched vocabulary: three
  event types, the payload field names, the two headers, the deployment modes,
  the collection names.
* :mod:`~dsr.meeting_webhook_fanout.signing` - the signing rule, which is the exact
  rule the research quotes rather than a choice this build made.
* :mod:`~dsr.meeting_webhook_fanout.payloads` - the flat Chili Piper envelope and the
  exact bytes that get signed.
* :mod:`~dsr.meeting_webhook_fanout.transport` - the seam a destination has to have,
  and the standard-library implementation of it.
* :mod:`~dsr.meeting_webhook_fanout.fanout` - :class:`MeetingWebhookFanout`, the
  whole store-facing half.
* :mod:`~dsr.meeting_webhook_fanout.errors` - one hierarchy, one HTTP handler.
* :mod:`~dsr.meeting_webhook_fanout.inferences` - every judgement call, named,
  traceable, bounded, and served.

This package imports the store and the standard library. It imports no framework
and no HTTP layer, so it can be unit tested against a plain
:class:`~dsr.store.RecordStore`. Every writing method takes a required
``source=``, so the audit row names the route that actually served the write.

The envelope decision
---------------------
The research quotes two payload shapes and warns against mixing them silently.
This build sends one flat Chili Piper envelope for all three event types it fires
and does not implement Cal's wrapped envelope or its two flat exceptions. Jev
chose that option at confidence 0.96, audit
``jev-20261004T070110-25984-70596``. The full argument is in
``orchestration/decisions/WF-066-DESIGN.md`` and the entry
``one-flat-envelope-for-the-three-chili-types`` in
:mod:`~dsr.meeting_webhook_fanout.inferences`.
"""

from __future__ import annotations

from dsr.meeting_webhook_fanout.errors import (
    AlreadyExists,
    InvalidEventType,
    InvalidRequest,
    InvalidSubscriberUrl,
    MeetingWebhookError,
    MissingSecret,
    NoRoom,
    UnknownDelivery,
    UnknownEvent,
    UnknownSubscription,
)
from dsr.meeting_webhook_fanout.fanout import MeetingWebhookFanout, Transport, UrllibTransport
from dsr.meeting_webhook_fanout.inferences import (
    INFERENCES,
    by_id,
    describe as describe_inferences,
)
from dsr.meeting_webhook_fanout.payloads import (
    build_payload,
    canonical_bytes,
    event_type_for_payload,
    payload_type_for_event_type,
)
from dsr.meeting_webhook_fanout.signing import (
    MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    sign,
    signing_input,
    unix_seconds,
    verify,
)
from dsr.meeting_webhook_fanout.vocabulary import (
    COLLECTIONS,
    DEPLOYMENT_MODES,
    EVENT_TYPES,
    PAYLOAD_FIELDS,
    PRODUCT_FEATURE_TYPES,
    STATUS_DISABLED,
    STATUS_ENABLED,
    describe as describe_vocabulary,
)

__all__ = [
    "COLLECTIONS",
    "DEPLOYMENT_MODES",
    "EVENT_TYPES",
    "INFERENCES",
    "MAX_AGE_SECONDS",
    "PAYLOAD_FIELDS",
    "PRODUCT_FEATURE_TYPES",
    "SIGNATURE_HEADER",
    "STATUS_DISABLED",
    "STATUS_ENABLED",
    "TIMESTAMP_HEADER",
    "AlreadyExists",
    "InvalidEventType",
    "InvalidRequest",
    "InvalidSubscriberUrl",
    "MeetingWebhookError",
    "MeetingWebhookFanout",
    "MissingSecret",
    "NoRoom",
    "Transport",
    "UnknownDelivery",
    "UnknownEvent",
    "UnknownSubscription",
    "UrllibTransport",
    "build_payload",
    "by_id",
    "canonical_bytes",
    "describe_inferences",
    "describe_vocabulary",
    "event_type_for_payload",
    "payload_type_for_event_type",
    "sign",
    "signing_input",
    "unix_seconds",
    "verify",
]
