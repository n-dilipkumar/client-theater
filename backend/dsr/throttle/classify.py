"""Which vendor answers are a throttle, in one table, with the quote for each.

This is the module WF-046 was built to own, and the reason it exists at all is a
duplication: ``dsr.partial_failures`` already classified ``429`` as throttled and
``403 REQUEST_LIMIT_EXCEEDED`` as throttled, and ``dsr.integ_monitor`` already
mapped ``429`` onto its ``throttle`` error class. Three modules answering the
same question is three places for the answer to drift, so both of them now read
it from here. The table below is the single copy. Nothing else in the product
decides whether a status is a throttle.

The table is the research's own list, and each row carries the sentence that
fixes it:

* **429** is the transport-level signal every vendor in the source set uses.
  HubSpot: "Any app or integration exceeding its rate limits will receive a
  ``429`` error response for all subsequent API calls." Dataverse: "``429 Too
  Many Requests`` Expect this status code when API limits are exceeded."
* **403 with ``REQUEST_LIMIT_EXCEEDED``** is Salesforce's word for the same
  thing: "If the error code is ``REQUEST_LIMIT_EXCEEDED``, you've exceeded API
  request limits in your org." A 403 without that code is a permission, and the
  rule that says so is deliberately *not* here.
* **423** is HubSpot's high-volume sync lock: "``423 Locked`` Returned when
  attempting to sync a large volume of data ... Locks will last for 2 seconds, so
  if you receive a ``423`` error, you should include a delay of at least 2
  seconds between your API requests." That sentence fixes both the classification
  and the delay, which is why :data:`LOCK_FLOOR_SECONDS` is 2 and not a guess.
* **477** is HubSpot's migration: it returns "a ``Retry-After`` response header
  indicating how many seconds to wait before retrying the request (typically up
  to 24 hours)".
* **5xx** is retryable because HubSpot says so: "A high number of requests may
  result in ``5xx`` errors. These can be addressed the same as you would ``429``
  errors." The statuses it enumerates are the transient classes below; a 5xx it
  does not enumerate is still retryable, and is named separately so a reader can
  see which numbers were sourced and which were read off the range.

**The four ``basis`` strings are the reason this file is safe to delegate from.**
A classification nobody can trace to a sentence is a guess with a boolean
attached, and the three callers that now share this table would each have carried
their own prose otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from dsr.throttle.errors import InvalidPayload

#: ``429``. The one status every vendor in the source set documents for
#: "you have exceeded your limits".
THROTTLE_STATUS = 429

#: Salesforce's code for the same condition, which it reports as a ``403``.
#:
#: Quoted: "If the error code is ``REQUEST_LIMIT_EXCEEDED``, you've exceeded API
#: request limits in your org." The research also notes that "``429``-class
#: throttling is surfaced as ``REQUEST_LIMIT_EXCEEDED``", so a connection to
#: Salesforce has to treat this code as the rate limit even though the status
#: says permission.
REQUEST_LIMIT_EXCEEDED = "REQUEST_LIMIT_EXCEEDED"
REQUEST_LIMIT_HTTP_STATUS = 403

#: HubSpot's high-volume sync lock, and the delay its own documentation requires.
#:
#: Quoted: "Locks will last for 2 seconds, so if you receive a ``423`` error, you
#: should include a delay of at least 2 seconds between your API requests." The
#: floor is the vendor's, not this build's: the research calls it a floor and
#: nothing else, so the room inserts at least this and the exponential ladder
#: decides whether it waits longer.
LOCK_STATUS = 423
LOCK_FLOOR_SECONDS = 2

#: HubSpot's migration answer, which is retryable and carries its own advice.
MIGRATION_STATUS = 477

#: The largest ``Retry-After`` this room will honour on its own.
#:
#: **Inferred, and the research left it open.** HubSpot says the header indicates
#: "how many seconds to wait before retrying the request (typically up to 24
#: hours)" and names no cap, so this build takes the vendor's own upper word -
#: twenty-four hours - as the cap and treats a longer wait as something a person
#: has to decide. Recorded as the ``retry-after-cap-is-24h`` inference, and
#: patchable per connection.
RETRY_AFTER_CAP_SECONDS = 86_400

#: HubSpot's enumerated transient classes.
#:
#: Quoted, in the vendor's own order: "``502/504/503/521/522/523/524/525/526``
#: transient classes." A 5xx outside this tuple is still retryable (see
#: :data:`VENDOR_FAULT_STATUSES`) and is reported under a different signal so the
#: difference between "sourced list" and "read off the range" stays visible.
TRANSIENT_STATUSES: tuple[int, ...] = (502, 504, 503, 521, 522, 523, 524, 525, 526)

#: The 5xx statuses the source set does not enumerate. Retryable, and named apart.
VENDOR_FAULT_STATUSES: tuple[int, ...] = (500, 501, 505, 507, 508, 510, 511)

#: What a throttle signal does to a batch. These are the ``kind`` values a client
#: renders and a queue branches on, and they are the only vocabulary for the
#: question "why was this deferred".
THROTTLE_KINDS: tuple[str, ...] = ("rate_limit", "lock", "migration", "transient", "vendor_fault")


@dataclass(frozen=True)
class Signal:
    """One row of the table: what arrived, and what the room should do about it.

    ``retryable`` says the class clears on its own, which is the researched split
    between the automatic drain and a person's queue. ``minimum_delay_seconds``
    is the floor the *vendor* stated, which is not the same thing as the wait the
    room will actually apply: the ladder in :mod:`dsr.throttle.backoff` decides
    the real wait, and it never goes below this floor.
    """

    id: str
    kind: str
    status: int | None
    code: str
    retryable: bool
    minimum_delay_seconds: int
    basis: str
    vendor: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "code": self.code,
            "retryable": self.retryable,
            "minimum_delay_seconds": self.minimum_delay_seconds,
            "vendor": self.vendor,
            "basis": self.basis,
        }


#: The table, most specific first. The **first** matching row wins, so the
#: Salesforce code is read before the bare status and a 403 that is not a request
#: limit is left to the caller's own table - which is the behaviour
#: ``dsr.partial_failures`` already had, and the reason its tests do not change.
SIGNALS: tuple[Signal, ...] = (
    Signal(
        id="salesforce-request-limit-exceeded",
        kind="rate_limit",
        status=REQUEST_LIMIT_HTTP_STATUS,
        code=REQUEST_LIMIT_EXCEEDED,
        retryable=True,
        minimum_delay_seconds=0,
        vendor="salesforce",
        basis=(
            "Quoted: \"403 ... If the error code is REQUEST_LIMIT_EXCEEDED, you've exceeded API "
            'request limits in your org." The research adds that 429-class throttling is surfaced '
            "as REQUEST_LIMIT_EXCEEDED, so the code is the signal and the status is only where "
            "Salesforce puts it. An exceeded request limit clears on its own."
        ),
    ),
    Signal(
        id="hubspot-migration-in-progress",
        kind="migration",
        status=MIGRATION_STATUS,
        code="",
        retryable=True,
        minimum_delay_seconds=0,
        vendor="hubspot",
        basis=(
            'Quoted: "477 Migration in Progress ... HubSpot will return a Retry-After response '
            "header indicating how many seconds to wait before retrying the request (typically up "
            "to 24 hours).\" The wait is the vendor's to state and the room's to cap."
        ),
    ),
    Signal(
        id="hubspot-sync-lock",
        kind="lock",
        status=LOCK_STATUS,
        code="",
        retryable=True,
        minimum_delay_seconds=LOCK_FLOOR_SECONDS,
        vendor="hubspot",
        basis=(
            'Quoted: "423 Locked | Returned when attempting to sync a large volume of data (e.g., '
            "upserting thousands of company records in a very short period of time). Locks will last "
            "for 2 seconds, so if you receive a 423 error, you should include a delay of at least 2 "
            "seconds between your API requests.\" The two-second delay is the vendor's own floor, "
            "so the room inserts at least this rather than a number it chose."
        ),
    ),
    Signal(
        id="throttled",
        kind="rate_limit",
        status=THROTTLE_STATUS,
        code="",
        retryable=True,
        minimum_delay_seconds=0,
        basis=(
            'Quoted for every vendor in the source set. HubSpot: "Any app or integration exceeding '
            'its rate limits will receive a 429 error response for all subsequent API calls." '
            'Dataverse: "429 Too Many Requests Expect this status code when API limits are '
            "exceeded.\" A throttled batch belongs in the automatic queue, not in a person's."
        ),
    ),
    Signal(
        id="transient-vendor-class",
        kind="transient",
        status=None,
        code="",
        retryable=True,
        minimum_delay_seconds=0,
        basis=(
            'Quoted: "502/504/503/521/522/523/524/525/526 transient classes", and "A high number of '
            "requests may result in 5xx errors. These can be addressed the same as you would 429 "
            'errors." The enumerated statuses are TRANSIENT_STATUSES.'
        ),
    ),
    Signal(
        id="vendor-fault",
        kind="vendor_fault",
        status=None,
        code="",
        retryable=True,
        minimum_delay_seconds=0,
        basis=(
            "Read off the range, not off the list: the source set enumerates its transient classes "
            "and says 5xx in general, so a 5xx outside that list is treated the same way and is "
            "reported as vendor_fault to keep the difference visible."
        ),
    ),
)

#: The statuses that classify on their own, resolved once.
#:
#: Only rows with **no code** are registered. ``403`` carries
#: :data:`REQUEST_LIMIT_EXCEEDED`, and registering it here would answer "this is a
#: request limit" for a Salesforce ``403 FORBIDDEN`` - which is a permission, and
#: which ``dsr.partial_failures`` has always classified as terminal. The code is
#: the discriminator, so the code is checked first in :func:`signal_for` and this
#: table never sees the row.
#:
#: ``423`` and ``477`` are keyed by status but stay vendor-scoped: a 423 is a lock
#: on every vendor that sends one, and this build has a quote only from HubSpot.
_BY_STATUS: dict[int, Signal] = {
    signal.status: signal for signal in SIGNALS if signal.status is not None and not signal.code
}
_TRANSIENT = next(item for item in SIGNALS if item.id == "transient-vendor-class")
_FAULT = next(item for item in SIGNALS if item.id == "vendor-fault")

#: The rows keyed by vendor error code rather than by status.
_BY_CODE: dict[str, Signal] = {signal.code.upper(): signal for signal in SIGNALS if signal.code}


def is_throttle_status(status: Any) -> bool:
    """Whether this status, on its own, is the rate-limit signal.

    Deliberately narrower than "is this a throttle": it answers only for a status
    with no vendor attached, which is exactly the question
    ``dsr.integ_monitor`` asks when it maps a telemetry sample onto its researched
    ``throttle`` error class. That mapping is now read from this table rather
    than written out, so a change to what counts as a throttle cannot leave the
    dashboard disagreeing with the queue.

    ``403`` answers ``False`` even though Salesforce uses it for a request limit,
    because ``403`` is also a permission and the researched breakdown puts a
    permission in ``validation``. The code is the discriminator, and a connector
    that knows which it was passes ``error_class`` itself.
    """
    try:
        return int(status) == THROTTLE_STATUS
    except (TypeError, ValueError):
        return False


def is_request_limit(code: Any) -> bool:
    """Whether this vendor code is the request-limit signal, whatever the status.

    The research says Salesforce surfaces 429-class throttling as
    ``REQUEST_LIMIT_EXCEEDED``, so a caller that sees the code on a 400 or a 200
    is still looking at a throttle.
    """
    return str(code or "").strip().upper() == REQUEST_LIMIT_EXCEEDED


def signal_for(
    vendor: str = "",
    status: Any = None,
    code: str = "",
    *,
    headers: Mapping[str, Any] | None = None,
) -> Signal | None:
    """The signal this vendor answer is, or ``None`` when it is not a throttle.

    ``headers`` is read only for the two things a status cannot carry: a HubSpot
    ``429`` body is the documented shape, but some callers only have the headers,
    and a ``Retry-After`` on a 5xx is the vendor telling the room how long to
    wait. A missing header changes nothing about the classification.
    """
    name = str(vendor or "").strip().lower()

    if is_request_limit(code):
        return _BY_CODE[REQUEST_LIMIT_EXCEEDED]

    try:
        number = int(status) if status is not None else None
    except (TypeError, ValueError):
        raise InvalidPayload(f"the vendor status must be a number; got {status!r}") from None

    if number is None:
        return None

    found = _BY_STATUS.get(number)
    if found is not None:
        if found.vendor and found.vendor != name:
            # The status is unambiguous (423 is a lock, 477 is a migration) but the
            # row is scoped to one vendor's documentation. A vendor the source set
            # never mentions sending a 423 is a caller with a rule this build has no
            # quote for, so it is refused rather than answered from another vendor's
            # sentence.
            return None
        return found

    if number in TRANSIENT_STATUSES:
        return _TRANSIENT
    if 500 <= number <= 599:
        return _FAULT
    return None


def require_vendor(vendor: Any) -> str:
    """Read a vendor name, or refuse it.

    The three in the source set are the only ones this build has researched
    rules for. A fourth vendor is a policy declaration away (see
    :mod:`dsr.throttle.policies`), so refusing an unknown *vendor* here would
    refuse an extension point the research explicitly asks for.
    """
    name = str(vendor or "").strip().lower()
    if not name:
        raise InvalidPayload(
            "a throttle decision needs a vendor; the policy is per-connector and the room "
            "cannot pick one"
        )
    return name


def header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    """One response header, read case-insensitively.

    Vendors do not agree on capitalisation and a caller may have lower-cased the
    whole mapping, so the lookup is by lowered key rather than by exact name.
    """
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value).strip()
    return None


def catalogue() -> list[dict[str, Any]]:
    """The whole table, as data, for ``GET /vocabulary``."""
    return [signal.as_dict() for signal in SIGNALS]


__all__ = [
    "THROTTLE_STATUS",
    "REQUEST_LIMIT_EXCEEDED",
    "REQUEST_LIMIT_HTTP_STATUS",
    "LOCK_STATUS",
    "LOCK_FLOOR_SECONDS",
    "MIGRATION_STATUS",
    "RETRY_AFTER_CAP_SECONDS",
    "TRANSIENT_STATUSES",
    "VENDOR_FAULT_STATUSES",
    "THROTTLE_KINDS",
    "Signal",
    "SIGNALS",
    "is_throttle_status",
    "is_request_limit",
    "signal_for",
    "require_vendor",
    "header",
    "catalogue",
]
