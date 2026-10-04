"""WF-046's domain: one owner for rate limiting under a vendor's API limits.

This package exists because two questions were being answered twice. Which vendor
answers are a throttle, and how long to wait afterwards, were each decided in more
than one place, and two answers to one question drift. So:

* :mod:`dsr.throttle.classify` is the single table of throttle signals, with the
  vendor's own sentence beside each row. ``dsr.partial_failures`` and
  ``dsr.integ_monitor`` now read it rather than writing their own.
* :mod:`dsr.throttle.backoff` is the single wait ladder, moved here from
  ``dsr.partial_failures`` with every value unchanged. The jitter, the
  ``Retry-After`` handling and the HubSpot lock floor are the new behaviour built
  on top of it.
* :mod:`dsr.throttle.bucket` is the pre-emptive half no shipped code had: a
  per-connection token bucket that stops a call before it is sent rather than
  after the vendor refuses it.
* :mod:`dsr.throttle.keys` derives the per-row idempotency key a retry reuses.
* :mod:`dsr.throttle.engine` is the only thing here that writes.

**The domain depends on nothing but the store.** No framework, no socket, no vendor
credential. Every vendor answer is handed in by the connector, so the whole package
is testable without a network and a quota reading costs the vendor nothing to take.
"""

from __future__ import annotations

from dsr.throttle import (
    backoff,
    bucket,
    classify,
    errors,
    inferences,
    keys,
    policies,
    quota,
    timestamps,
    vocabulary,
)
from dsr.throttle.engine import (
    ThrottleEngine,
    require_batch,
    require_connection,
    require_room,
)
from dsr.throttle.errors import (
    InvalidPayload,
    ThrottleError,
    UnknownBatch,
    UnknownConnection,
    UnknownPolicy,
    UnknownRoom,
)

__all__ = [
    "backoff",
    "bucket",
    "classify",
    "errors",
    "inferences",
    "keys",
    "policies",
    "quota",
    "timestamps",
    "vocabulary",
    "ThrottleEngine",
    "ThrottleError",
    "UnknownBatch",
    "UnknownConnection",
    "UnknownPolicy",
    "UnknownRoom",
    "InvalidPayload",
    "require_batch",
    "require_connection",
    "require_room",
]
