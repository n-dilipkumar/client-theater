"""The pull-based backfill: ``properties``, and the documented ``429``.

Sourced
-------
"REST API for pull-based backfill: base ``https://api.dock.us``,
``Authorization: Bearer <Your-Token>``; ``GET /v1/workspaces``,
``GET /v1/workspaces/{id}``, ``GET /v1/assets``, ``GET /v1/forms/{id}/responses``,
``GET /v1/workspace-plan-tasks``; ``properties`` query param; ``429`` on rate
limit."

And the ``properties`` rule in full, which is the one that bites:

"Endpoints that return a resource accept a ``properties`` query parameter that
controls which fields are included in the response. If you omit it, the response
contains **only** the resource's ``id``, ``object``, and ``url``."

That omission behaviour is implemented as a constant,
:data:`dsr.event_stream.vocabulary.MINIMAL_PROPERTIES`, rather than as an ``if``
in a route. Asking for no properties must mean the minimum, never everything -
a backfill that silently returns whole records because the client forgot a
parameter is a data-exfiltration bug wearing a convenience feature's clothes.

The unknown-property rule
-------------------------
An unknown property is **refused**, with the names listed. That is a judgement
call, and the opposite of the obvious one, so it is worth the argument: this
product can know whether a field exists, because
:meth:`dsr.store.RecordStore.fields` reports the JSON paths actually in use for
a collection. A ``properties`` typo therefore narrows the response to nothing at
all, the client's backfill silently imports zero fields, and the only symptom is
an empty warehouse three days later. Refusing costs the caller one clear 400 at
the moment they can still fix it. Recorded as inference
``unknown-property-is-refused``.

The rate limit
--------------
"``429`` on rate limit" is all the research says, so the allowance itself is an
inference - :data:`DEFAULT_LIMIT` per :data:`DEFAULT_WINDOW` seconds, per caller,
in process. In-process because the research's backfill seam is the documented
one and this build has no shared cache; a multi-process deployment would need a
shared counter, which is named in ``inferences.py`` as the one thing to change.

The limiter lives on the :class:`~dsr.event_stream.stream.EventStream` rather
than on a module global for the same reason the store does: a new test database
must get a fresh allowance, or one test's backfill calls would rate-limit
another's.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

from dsr.event_stream.errors import RateLimited, VocabularyError
from dsr.event_stream.vocabulary import (
    MINIMAL_PROPERTIES,
    PULL_RESOURCES,
    backfill_route,
    project_properties,
)

#: The documented allowance. A per-minute budget in the low tens is the ordinary
#: shape for a pull API of this size, and it is *low* on purpose: a backfill that
#: has to page through a large estate is exactly the caller most likely to need
#: the limit to be a real one.
DEFAULT_LIMIT = 60
DEFAULT_WINDOW = 60.0


@dataclass
class RateLimiter:
    """A fixed-window counter per caller.

    Fixed window rather than a sliding one because the research says nothing
    about the shape and a fixed window is the one an operator can predict: at
    most ``limit`` calls in any ``window`` seconds, restarting on the boundary.
    A sliding window would be smoother and would make "why was I limited" a harder
    question to answer from a log.
    """

    limit: int = DEFAULT_LIMIT
    window: float = DEFAULT_WINDOW
    now: Callable[[], float] = time.monotonic
    _calls: dict[str, list[float]] = field(default_factory=dict)

    def check(self, caller: str) -> dict[str, Any]:
        """Consume one unit of ``caller``'s allowance, or refuse.

        Returns the state it consumed so a client can see the budget rather than
        guess at it; raises :class:`~dsr.event_stream.errors.RateLimited` with
        ``Retry-After`` when there is nothing left.
        """
        moment = self.now()
        calls = [at for at in self._calls.get(caller, ()) if moment - at < self.window]
        if len(calls) >= self.limit:
            oldest = min(calls)
            retry_after = max(1, int(round(self.window - (moment - oldest))))
            self._calls[caller] = calls
            raise RateLimited(
                f"backfill rate limit reached: {self.limit} requests per {self.window:g}s for {caller!r}",
                remediation=(
                    "Wait for the window to reset, or page through the estate more slowly. "
                    "The researched backfill seam answers 429 when a caller is limited."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        calls.append(moment)
        self._calls[caller] = calls
        return self.state(caller)

    def state(self, caller: str) -> dict[str, Any]:
        """The caller's remaining allowance, without consuming any."""
        moment = self.now()
        calls = [at for at in self._calls.get(caller, ()) if moment - at < self.window]
        return {
            "caller": caller,
            "limit": self.limit,
            "window_seconds": self.window,
            "used": len(calls),
            "remaining": max(0, self.limit - len(calls)),
        }

    def reset(self) -> None:
        self._calls.clear()


def require_properties(raw: Any, available: list[str]) -> list[str]:
    """Resolve the ``properties`` parameter against the fields a collection has.

    The researched omission behaviour first: absent, empty, and whitespace-only
    all mean the minimal projection, which is ``id``, ``object`` and ``url``.

    ``id``, ``object`` and ``url`` are always available because they are not
    fields of the record - they are the envelope the researched rule names - so
    they are not looked for in the collection's discovered paths.
    """
    if raw is None:
        return list(MINIMAL_PROPERTIES)
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(part).strip() for part in raw]
    else:
        raise VocabularyError(
            f"properties must be a comma-separated string, got {type(raw).__name__}",
            code="properties_invalid",
            remediation="Send ?properties=name,stage or omit it for id, object and url only.",
        )
    candidates = [name for name in candidates if name]
    if not candidates:
        return list(MINIMAL_PROPERTIES)

    resolvable = set(MINIMAL_PROPERTIES) | set(available)
    unknown = sorted({name for name in candidates if name not in resolvable})
    if unknown:
        raise VocabularyError(
            f"unknown property/properties: {', '.join(unknown)}",
            code="unknown_property",
            remediation=(
                f"This collection exposes {', '.join(sorted(resolvable)) or '(nothing yet)'}. "
                "A misspelled property would otherwise narrow the response to nothing and "
                "the backfill would import no fields at all."
            ),
        )
    seen: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.append(name)
    return seen


def pull(
    store: Any,
    resource: str,
    *,
    properties: list[str],
    limit: int = 100,
    room_id: str | None = None,
    form_id: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """One researched pull resource, projected to ``properties``.

    ``room_id``, ``form_id`` and ``record_id`` are the scoping parameters the
    researched route list implies: a workspace resource can be scoped to a room
    or to one id, and form responses are scoped to their form. They filter in
    Python rather than through ``find()``, because ``formId`` is a payload field
    this product does not guarantee is indexed, and a scoped query that silently
    returned everything would be worse than one that is obviously a list.
    """
    spec = PULL_RESOURCES[resource]
    collection = str(spec["collection"])
    available = [field["path"] for field in store.fields(collection)]
    if record_id:
        record = store.get(record_id)
        records = [record] if record is not None and record["collection"] == collection else []
    else:
        records = store.list(collection, limit=limit, room_id=room_id)
    if resource == "forms/{formId}/responses" and form_id:
        records = [r for r in records if str((r.get("data") or {}).get("formId")) == str(form_id)]
    return {
        "resource": resource,
        "object": spec["object"],
        "vendor_path": spec["vendor_path"],
        "url": backfill_route(str(spec["route"]), workspaceId=form_id or "id", formId=form_id or "id"),
        "properties": list(properties),
        "count": len(records),
        "results": [project_properties(record, spec, properties) for record in records],
        "available_properties": sorted(set(MINIMAL_PROPERTIES) | set(available)),
    }


def describe_rate_limit(limiter: RateLimiter) -> dict[str, Any]:
    """The limit, served in the vocabulary so a client can pace itself."""
    return {
        "limit": limiter.limit,
        "window_seconds": limiter.window,
        "status": 429,
        "scope": "per caller, in process, fixed window",
        "header": "Retry-After",
        "basis": "the research documents '429 on rate limit' on the backfill seam and no allowance",
    }
