"""WF-062: hold a requested slot for host approval before confirming.

The researched workflow, made executable. An admin turns **requires
confirmation** on for an event type; a prospect or an agent asks for a slot; the
booking is created ``PENDING`` with a ``oneTimePassword`` and
``requiresConfirmation: true``; a ``BOOKING_REQUESTED`` webhook fires and the
rep is notified; the host either confirms it (``ACCEPTED``) or declines it with
a reason (``REJECTED``), and a decline fires ``BOOKING_REJECTED`` carrying that
``rejectionReason`` back to the attendee.

Everything here is **pure**: no database, no framework, no clock of its own
beyond the ``now`` it is handed. That is deliberate. The researched rules here
are the ones worth being sure about - who may decide, which bypass flags are
honoured and for whom, what a pending request does to the host's availability -
and a rule that needs a database to be exercised is a rule that is only ever
tested through the thing it is a rule about. :mod:`dsr.booking_approval` is
therefore the specification made testable, and the feature module beside it is
storage, HTTP and the audit row.

Two spellings of the same four statuses
----------------------------------------
The research quotes the webhook payloads in upper case (``"status": "PENDING"``,
``"status": "REJECTED"``) and names the API enum in lower case
(``cancelled|accepted|rejected|pending``). Those are two researched spellings of
one vocabulary, not a disagreement, so both are published and storage uses the
upper-case form the evidence quotes.

What this module deliberately does not do
-----------------------------------------
* It does not cancel a booking. ``CANCELLED`` is in the researched status enum
  and is published here so a client can render it, but rescheduling and
  cancelling a meeting is WF-064's workflow and this one must not take it over.
* It fixes no numbers for the bounds and limits its checks apply. The research
  names ``allowBookingOutOfBounds`` and ``skipBookingLimits``, so the checks
  exist, but it does not say how much notice a meeting needs or how far ahead
  bookings open. Those are the event type's own JSON fields
  (``minimumNoticeMinutes``, ``maximumRangeDays``, ``bookingLimitPerAttendee``)
  and an absent field means the bound is not applied. Inventing a constant here
  would be a rule nobody sourced, and it would be a rule in the one place a
  reader would assume is only the researched ones.
* It does not send anything. There is no mail or SMS transport in this product,
  so a notification is a *recorded* dispatch with ``delivered_by: "simulated"``
  on it. A confirmation that reads like a fact about a real inbox is the exact
  kind of thing a reviewer must not have to discover from a diff.
"""

from __future__ import annotations

import hmac
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
#
# Published as data at ``GET /api/wf-062/vocabulary`` so a client renders its
# pickers from the server rather than from a list compiled into a page, and a
# value added here reaches every client at once.

#: The four statuses the research names. ``PENDING`` is the one this workflow is
#: about; the other three are what a request can turn into, or already is.
STATUSES: tuple[str, ...] = ("PENDING", "ACCEPTED", "REJECTED", "CANCELLED")

#: The same four values in the API reference's own spelling. Both are researched;
#: storage uses the upper case the webhook evidence quotes.
API_STATUS: dict[str, str] = {
    "PENDING": "pending",
    "ACCEPTED": "accepted",
    "REJECTED": "rejected",
    "CANCELLED": "cancelled",
}

STATUS_MEANING: dict[str, str] = {
    "PENDING": (
        "The slot is held and a host has not decided. requiresConfirmation is true "
        "and a oneTimePassword has been issued."
    ),
    "ACCEPTED": "The host confirmed it. Only now is a calendar event created.",
    "REJECTED": "The host declined it, with a rejectionReason, and the hold is released.",
    "CANCELLED": (
        "Published because the researched status enum names it. Cancelling is "
        "WF-064's workflow and nothing in this module writes it."
    ),
}

#: Which statuses occupy the host's time. A pending request *holds* the slot -
#: that is the whole point of routing a requested slot for approval - and only
#: releasing it (a decline, or a cancel elsewhere) hands it back.
OCCUPYING_STATUSES: frozenset[str] = frozenset({"PENDING", "ACCEPTED"})

#: The statuses a request can only be in before anyone has decided. Every decision
#: route requires this; deciding twice is a conflict, not a second decision.
DECIDABLE_STATUS = "PENDING"

#: The two webhooks the research names for this workflow. Nothing else is
#: published: an acceptance is recorded as a decision and a calendar event, not
#: as a webhook, because the research names no such event and inventing one would
#: put a vendor-shaped fiction in the audit trail.
WEBHOOK_EVENTS: tuple[str, ...] = ("BOOKING_REQUESTED", "BOOKING_REJECTED")

WEBHOOK_MEANING: dict[str, str] = {
    "BOOKING_REQUESTED": (
        "Fires when a booking is created as a request. Its payload carries "
        "requiresConfirmation, oneTimePassword and status PENDING."
    ),
    "BOOKING_REJECTED": (
        "Fires when the host declines. Its payload carries rejectionReason and "
        "status REJECTED, and is what propagates to the CRM and the room."
    ),
}

#: The two workflow triggers this workflow can raise. The vendor's trigger enum
#: is much wider; the rest of it belongs to the reminders workflow (WF-061), and
#: publishing a trigger here that nothing raises would be a lie in the vocabulary.
WORKFLOW_TRIGGERS: tuple[str, ...] = ("bookingRequested", "bookingRejected")

TRIGGER_MEANING: dict[str, str] = {
    "bookingRequested": (
        "A rep-facing to-do, SMS or email can be kicked off the moment a slot is "
        "requested, without anybody opening the room."
    ),
    "bookingRejected": "Re-routing. A declined request can hand the slot to somebody else.",
}

#: The channels the research names for a rep-facing action. A dispatch on one of
#: these is recorded, not sent; see the module docstring.
NOTIFICATION_CHANNELS: tuple[str, ...] = ("email", "sms", "to_do")

#: Nobody may read a recorded dispatch as proof of delivery.
DISPATCHED_BY = "simulated"

#: The researched sentence a decline falls back to when the host gives no reason
#: of their own. The data flow says the reason is optional, and the
#: ``BOOKING_REJECTED`` evidence quotes this exact string, so it is the default
#: rather than an empty field the attendee would receive.
DEFAULT_REJECTION_REASON = "The organizer is no longer available at this time."

#: The three bypass flags the research names, and the check each one skips.
BYPASS_FLAGS: tuple[str, ...] = ("allowConflicts", "allowBookingOutOfBounds", "skipBookingLimits")

BYPASS_CHECK: dict[str, str] = {
    "allowConflicts": "slot_conflict",
    "allowBookingOutOfBounds": "out_of_bounds",
    "skipBookingLimits": "booking_limit",
}

#: "only on API versions 2026-02-25 / 2026-05-01". A version outside this set
#: means every flag is ignored, and the check the flag would have skipped applies
#: as though the flag had not been sent.
BYPASS_API_VERSIONS: tuple[str, ...] = ("2026-02-25", "2026-05-01")

#: "an authenticated user who has access through the existing event owner, host,
#: assigned user, team admin or organization admin checks". Any one of them is
#: enough; the set is published so a client can say which check failed.
PRIVILEGED_ROLES: tuple[str, ...] = (
    "event_owner",
    "host",
    "assigned_user",
    "team_admin",
    "org_admin",
)

PRIVILEGED_ROLE_MEANING: dict[str, str] = {
    "event_owner": "Owns the event type the request was made against.",
    "host": "The host the slot was requested on.",
    "assigned_user": "Named in the event type's assignedUserIds.",
    "team_admin": "Declared by the caller; a team-scoped administration right.",
    "org_admin": "Declared by the caller; an organisation-scoped administration right.",
}

#: How a decision was reached. ``unattended`` is the researched "unattended
#: approvals can be bypassed server-side for trusted hosts by the newer bypass
#: flags": it is refused unless the bypass *would* be honoured.
DECISION_DRIVERS: tuple[str, ...] = ("host", "unattended")

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Names, not tables. Nothing below is a schema: a field is an ordinary JSON key
# inside a record's own ``data``, so a team can add one with no migration and
# filter on it through the dynamic index.

EVENT_TYPES = "event_type"
BOOKING_REQUESTS = "booking_request"
BOOKING_WEBHOOKS = "booking_webhook"
BOOKING_DISPATCHES = "booking_dispatch"
BOOKING_EVENTS = "booking_event"
EMAIL_VERIFICATIONS = "email_verification"
BOOKING_AUTOMATIONS = "booking_automation"

COLLECTIONS: dict[str, str] = {
    "event_type": EVENT_TYPES,
    "booking_request": BOOKING_REQUESTS,
    "booking_webhook": BOOKING_WEBHOOKS,
    "booking_dispatch": BOOKING_DISPATCHES,
    "booking_event": BOOKING_EVENTS,
    "email_verification": EMAIL_VERIFICATIONS,
    "booking_automation": BOOKING_AUTOMATIONS,
}

#: What each collection is for, served so a reader does not have to infer it.
COLLECTION_MEANING: dict[str, str] = {
    EVENT_TYPES: "The researched event type config: requiresConfirmation, and the optional email verification gate.",
    BOOKING_REQUESTS: "The booking table. A request is PENDING until a host confirms or declines it.",
    BOOKING_WEBHOOKS: "Every BOOKING_REQUESTED and BOOKING_REJECTED payload this workflow emitted.",
    BOOKING_DISPATCHES: "A recorded rep-facing action: to-do, SMS or email, raised by a workflow trigger.",
    BOOKING_EVENTS: "The calendar event, created only when a host confirms.",
    EMAIL_VERIFICATIONS: "The separate optional email-verification gate: a code, when it was sent, and when it verified.",
    BOOKING_AUTOMATIONS: "Rules keyed on bookingRequested or bookingRejected, so a request can act without anybody opening the room.",
}

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ApprovalError(RuntimeError):
    """A refusal by this workflow, carrying the status and code it becomes.

    One type for the whole hierarchy, because FastAPI only accepts exception
    handlers on the app and the host refuses two features mapping the same type.
    The status rides on the exception rather than being decided in the handler:
    a request that is not well formed (422) and a slot somebody already holds
    (409) are both this package's errors and only one of them is a conflict with
    state that already exists.

    ``refusals`` carries *every* check that failed, not only the one that
    stopped the request, so a caller fixing a rejection is told about all of it
    in one round trip.
    """

    code = "approval_error"
    status = 422

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status: int | None = None,
        refusals: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.refusals: list[dict[str, Any]] = [dict(entry) for entry in (refusals or [])]


def refusal(code: str, detail: str, *, status: int, **extra: Any) -> dict[str, Any]:
    """One failed check, in the shape both the exception and the trace use."""
    return {"code": code, "detail": detail, "status": status, **extra}


def raise_refusals(refusals: Sequence[Mapping[str, Any]]) -> None:
    """Raise the first refusal, carrying all of them.

    The first one is what the status and code come from, so the order the checks
    run in is part of the contract: the email-verification gate is a gate on the
    request body and is reported before a conflict anybody could fix by moving
    the slot.
    """
    if not refusals:
        return
    first = dict(refusals[0])
    raise ApprovalError(
        str(first["detail"]),
        code=str(first["code"]),
        status=int(first["status"]),
        refusals=refusals,
    )


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(moment: datetime) -> datetime:
    """A datetime in UTC, whatever it arrived as.

    A naive datetime is *assumed* UTC rather than rejected: the product stores
    every timestamp in UTC, and refusing a naive value would be a rule about
    spelling rather than about time.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_moment(value: Any, field: str) -> datetime:
    """An ISO 8601 instant, as a UTC datetime.

    Raises :class:`ApprovalError` 422 rather than ``ValueError``: a caller that
    sent a bad timestamp wants the same answer shape as a caller that sent a
    bad flag.
    """
    if isinstance(value, datetime):
        return as_utc(value)
    if not isinstance(value, str) or not value.strip():
        raise ApprovalError(
            f"{field} is required, as an ISO 8601 instant",
            code="invalid_request",
            status=422,
        )
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return as_utc(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ApprovalError(
            f"{field} is not an ISO 8601 instant: {value!r}",
            code="invalid_request",
            status=422,
        ) from exc


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else as_utc(moment).isoformat()


def overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    """Whether two half-open intervals share any time.

    Half-open on purpose: a meeting ending at 10:00 and one starting at 10:00 do
    not conflict, and treating them as conflicting would refuse every
    back-to-back booking a host ever made.
    """
    return start < other_end and other_start < end


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def mint_one_time_password() -> str:
    """The ``oneTimePassword`` the research shows beside ``requiresConfirmation``.

    The evidence quotes the shape - ``"00000000-0000-0000-0000-000000000000"`` -
    so this is a UUID in that form and not a shorter token. It is issued *only*
    for a request, because the payload shows it beside ``"requiresConfirmation":
    true`` and a booking created without confirmation has nothing to manage while
    it waits.
    """
    return str(uuid.uuid4())


def mint_verification_code(rng: random.Random | None = None) -> str:
    """A six-digit email verification code.

    ``rng`` is a constructor argument rather than a module global so a test can
    make a code reproducible without a seam in production code. The route does
    not pass one, so a real request draws from the system entropy source.
    """
    source = rng or random.SystemRandom()
    return f"{source.randint(0, 999_999):06d}"


def passwords_match(supplied: str | None, stored: str | None) -> bool:
    """Constant-time comparison, so a mismatch leaks no timing information."""
    if not supplied or not stored:
        return False
    return hmac.compare_digest(str(supplied), str(stored))


# --------------------------------------------------------------------------- #
# Authorisation
# --------------------------------------------------------------------------- #


def privilege_roles(subject: Mapping[str, Any], caller: Mapping[str, Any] | None) -> list[str]:
    """Which researched privileged roles this caller holds over ``subject``.

    ``subject`` is an event type or a booking: the fields it is matched on are
    ``ownerId``, ``hostId`` and ``assignedUserIds``, all of which a booking
    carries a copy of so a decision does not have to re-read the event type to
    know who may make it.

    ``team_admin`` and ``org_admin`` cannot be derived from a booking, so they
    are read from the caller's own declarations. A caller that declares a role it
    does not hold is a caller whose declaration is wrong, which is a deployment
    problem rather than something this module can detect - so the declared
    value is taken at face value and the role that derived it is published
    alongside.
    """
    if not caller:
        return []
    user_id = str(caller.get("userId") or "").strip()
    if not user_id:
        return []
    roles: list[str] = []
    if user_id and str(subject.get("ownerId") or "") == user_id:
        roles.append("event_owner")
    if user_id and str(subject.get("hostId") or "") == user_id:
        roles.append("host")
    assigned = {str(entry) for entry in (subject.get("assignedUserIds") or [])}
    if user_id in assigned:
        roles.append("assigned_user")
    for declared in caller.get("roles") or []:
        name = str(declared)
        if name in ("team_admin", "org_admin") and name not in roles:
            roles.append(name)
    return roles


def is_authenticated(caller: Mapping[str, Any] | None) -> bool:
    """Whether the caller presented an identity at all.

    Deliberately a separate question from :func:`privilege_roles`. The researched
    sentence conditions a *bypass* on "an authenticated user who has access
    through the existing event owner, host, assigned user, team admin or
    organization admin checks", so authentication gates the bypass and the role
    check gates the decision. This product has no session layer, so "authenticated"
    is whatever the caller asserts plus the presence of an identity - and the
    asymmetry is published in ``inferences`` rather than papered over.
    """
    if not caller:
        return False
    if not str(caller.get("userId") or "").strip():
        return False
    return bool(caller.get("authenticated", True))


def resolve_bypasses(
    *,
    requested: Mapping[str, Any] | None,
    api_version: str | None,
    roles: Sequence[str],
    authenticated: bool,
) -> dict[str, Any]:
    """Which bypass flags are honoured, and why each of the others is not.

    The researched rule is a conjunction of three conditions - the flag was
    asked for, the API version is one of two, and the caller is authenticated
    with one of five privileged roles - and this returns the whole conjunction
    per flag rather than a single yes or no.

    A flag that is not honoured is **ignored, not refused**, and the check it
    would have skipped then applies as though it had never been sent. The
    research says the flags are "only honoured" for such a caller, which is a
    statement about effect and not about status codes, and silently downgrading
    is also what keeps an old client from learning that a privilege exists.
    """
    asked = {flag: bool((requested or {}).get(flag)) for flag in BYPASS_FLAGS}
    honoured: list[str] = []
    ignored: list[dict[str, Any]] = []
    for flag in BYPASS_FLAGS:
        if not asked[flag]:
            continue
        if api_version not in BYPASS_API_VERSIONS:
            ignored.append(
                {
                    "flag": flag,
                    "code": "api_version_not_supported",
                    "detail": (
                        f"{flag} is honoured only on API versions "
                        f"{', '.join(BYPASS_API_VERSIONS)}; {api_version or 'no version supplied'} is not one"
                    ),
                    "skips": BYPASS_CHECK[flag],
                }
            )
        elif not authenticated:
            ignored.append(
                {
                    "flag": flag,
                    "code": "caller_not_authenticated",
                    "detail": f"{flag} is honoured only for an authenticated caller",
                    "skips": BYPASS_CHECK[flag],
                }
            )
        elif not roles:
            ignored.append(
                {
                    "flag": flag,
                    "code": "caller_is_not_privileged",
                    "detail": (
                        f"{flag} is honoured only for a caller with one of "
                        f"{', '.join(PRIVILEGED_ROLES)} over this event type"
                    ),
                    "skips": BYPASS_CHECK[flag],
                }
            )
        else:
            honoured.append(flag)
    return {
        "requested": {flag: True for flag in BYPASS_FLAGS if asked[flag]},
        "honoured": honoured,
        "ignored": ignored,
        "api_version": api_version,
        "roles": list(roles),
        "authenticated": authenticated,
        "supported_api_versions": list(BYPASS_API_VERSIONS),
    }


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


def validate_event_type(payload: Mapping[str, Any]) -> dict[str, Any]:
    """An event type's own configuration, normalised and checked.

    Only the three things the researched flow turns on are validated:
    ``requiresConfirmation`` (the toggle the admin enables), ``emailVerification``
    (the separate optional gate) and the numbers the bounds and limits checks
    read. Everything else is passed through untouched, because a record payload
    is arbitrary JSON and a team adding a field must not need this function to
    know about it.
    """
    data = dict(payload or {})
    for reserved in (
        "id",
        "collection",
        "room_id",
        "revision",
        "created_at",
        "updated_at",
        "deleted_at",
    ):
        data.pop(reserved, None)

    title = str(data.get("title") or "").strip()
    if not title:
        raise ApprovalError("title is required", code="invalid_request", status=422)

    requires_confirmation = data.get("requiresConfirmation", False)
    if not isinstance(requires_confirmation, bool):
        raise ApprovalError(
            "requiresConfirmation must be true or false",
            code="invalid_event_type",
            status=422,
        )
    email_verification = data.get("emailVerification", False)
    if not isinstance(email_verification, bool):
        raise ApprovalError(
            "emailVerification must be true or false",
            code="invalid_event_type",
            status=422,
        )

    duration = data.get("durationMinutes", 30)
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise ApprovalError(
            "durationMinutes must be a positive whole number",
            code="invalid_event_type",
            status=422,
        )

    # The bounds and the limit are configuration, not constants: absent means the
    # bound is not applied, which is the only reading the research supports (it
    # names the checks and the flags, and fixes no numbers).
    for field in ("minimumNoticeMinutes", "maximumRangeDays", "bookingLimitPerAttendee"):
        if field in data and data[field] is not None:
            value = data[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ApprovalError(
                    f"{field} must be a whole number of minutes, days or bookings, or absent",
                    code="invalid_event_type",
                    status=422,
                )

    return {
        **data,
        "title": title,
        "requiresConfirmation": requires_confirmation,
        "emailVerification": email_verification,
        "durationMinutes": duration,
    }


def requires_confirmation(event_type: Mapping[str, Any]) -> bool:
    """Whether a slot requested against this event type needs a host to say yes.

    The researched flow has two entries: the admin enabled the toggle, "or the
    booking is created as a request". So a per-request ``requiresConfirmationOverride``
    is honoured - but only in the direction that *adds* a request. ``true`` on a
    type whose toggle is off is the flow's second entry and does produce a
    request; ``false`` on a type whose toggle is on does not, because a request
    that could be made not to be a request is an approval step any caller can
    step over, and the host would never learn a slot had been taken.

    Named in ``inferences`` as ``a-per-request-override-can-only-add-approval``.
    """
    if event_type.get("requiresConfirmationOverride") is True:
        return True
    return bool(event_type.get("requiresConfirmation"))


# --------------------------------------------------------------------------- #
# Email verification: the separate optional gate
# --------------------------------------------------------------------------- #


def verification_state(
    event_type: Mapping[str, Any],
    *,
    email: str,
    supplied_code: str | None,
    record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The state of the researched email-verification gate for one request.

    The gate is *separate* and *optional*: "emailVerification is a separate
    optional gate", and the booking body carries ``emailVerificationCode``
    because it is "required when event type has email verification enabled". So
    a booking on an event type without the gate is never asked for a code, and a
    booking on one with it is refused until a verified code matches.

    Three failures are distinguished, because the caller's next move differs:
    nothing supplied (ask for a code), a code supplied that no verified record
    matches (verify it), and a code that is known but not yet verified (the
    ``verify`` call has not happened).
    """
    required = bool(event_type.get("emailVerification"))
    state: dict[str, Any] = {
        "required": required,
        "satisfied": not required,
        "code": None,
        "verified_at": None,
    }
    if not required:
        return state
    if not (supplied_code or "").strip():
        state["code"] = "email_verification_required"
        state["detail"] = (
            "emailVerificationCode is required when event type has email verification enabled"
        )
        return state
    if record is None or not passwords_match(supplied_code, str(record.get("code") or "")):
        state["code"] = "invalid_verification_code"
        state["detail"] = "no email verification record matches that code for this address"
        return state
    if not record.get("verifiedAt"):
        state["code"] = "email_verification_not_verified"
        state["detail"] = "that code has been sent but not verified for this address"
        return state
    state["satisfied"] = True
    state["code"] = None
    state["verified_at"] = record.get("verifiedAt")
    return state


# --------------------------------------------------------------------------- #
# The request path
# --------------------------------------------------------------------------- #


def evaluate_request(
    event_type: Mapping[str, Any],
    *,
    now: datetime,
    start: Any,
    attendee: Mapping[str, Any],
    caller: Mapping[str, Any] | None = None,
    api_version: str | None = None,
    bypass: Mapping[str, Any] | None = None,
    email_verification_code: str | None = None,
    verification: Mapping[str, Any] | None = None,
    overlapping: Sequence[Mapping[str, Any]] = (),
    attendee_bookings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Decide what a slot request becomes, and why.

    Pure: the caller supplies the clock, the host's other requests and the
    attendee's history, so every check in here is a function of its arguments
    and a test can produce a conflict, an out-of-bounds request and a booking
    limit without writing a row.

    The returned ``checks`` list is the whole trace - one entry per check, in
    the order they ran, each saying whether it passed, which researched rule it
    came from, and which bypass flag would have skipped it. ``refusals`` is the
    subset that failed. Nothing is raised here; :func:`raise_refusals` turns a
    refusal into the HTTP answer, and the trace is returned either way so a
    caller can see which checks *did* pass.
    """
    moment = as_utc(now)
    begins = parse_moment(start, "start")
    who = dict(attendee or {})
    address = str(who.get("email") or "").strip()
    if not address:
        raise ApprovalError("attendee.email is required", code="invalid_request", status=422)
    if not _EMAIL.match(address):
        raise ApprovalError(
            f"attendee.email is not an address: {address!r}",
            code="invalid_request",
            status=422,
        )

    duration = int(event_type.get("durationMinutes") or 30)
    ends = begins + timedelta(minutes=duration)

    roles = privilege_roles(event_type, caller)
    resolved_bypasses = resolve_bypasses(
        requested=bypass,
        api_version=api_version,
        roles=roles,
        authenticated=is_authenticated(caller),
    )
    honoured = set(resolved_bypasses["honoured"])
    checks: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []

    def record(
        code: str,
        ok: bool,
        detail: str,
        *,
        rule: str,
        flag: str | None = None,
        status: int = 409,
    ) -> None:
        """Add one check to the trace, and to the refusals if it failed.

        ``status`` is per check rather than derived from the code: the email
        gate is a problem with the request body (422) and the other three are
        conflicts with state that already exists (409), and no table is needed
        to say that where the call is.
        """
        entry: dict[str, Any] = {"code": code, "ok": ok, "detail": detail, "rule": rule}
        if flag:
            entry["bypassed_by"] = flag
            entry["bypass_honoured"] = flag in honoured
        checks.append(entry)
        if not ok and not (flag and flag in honoured):
            # The bypass context is repeated onto the refusal, not just onto the
            # trace: a refusal is answered by the exception handler, which returns
            # `refusals` and never the `checks`, so a client fixing the rejection
            # would otherwise be told a check failed without being told a flag
            # would have covered it.
            extra: dict[str, Any] = {}
            if flag:
                extra = {"bypassed_by": flag, "bypass_honoured": flag in honoured}
            refusals.append(refusal(code, detail, status=status, **extra))

    # 1. The email-verification gate. It is a gate on the request body, so it is
    #    reported before a conflict the caller could fix by moving the slot.
    gate = verification_state(
        event_type, email=address, supplied_code=email_verification_code, record=verification
    )
    record(
        str(gate["code"] or "email_verification_not_required"),
        bool(gate["satisfied"]),
        str(
            gate.get("detail")
            or "this event type has email verification off, so no code is required"
        ),
        rule="emailVerification is a separate optional gate",
        status=422,
    )

    # 2. Availability. A pending request and an accepted booking both hold the
    #    slot, so a request that overlaps either is a conflict.
    clashes = [
        other
        for other in overlapping
        if str(other.get("status") or "") in OCCUPYING_STATUSES
        and overlaps(
            begins,
            ends,
            parse_moment(other.get("start"), "start"),
            parse_moment(other.get("end"), "end"),
        )
    ]
    record(
        "slot_conflict",
        not clashes,
        (
            f"{len(clashes)} live booking(s) already hold {iso(begins)}"
            if clashes
            else f"the host is free from {iso(begins)} to {iso(ends)}"
        ),
        rule="availability conflict checks are bypassed for a privileged caller",
        flag="allowConflicts",
    )

    # 3. The bounds. Absent fields mean the bound is not applied; see the module
    #    docstring for why no number is defaulted here.
    notice = event_type.get("minimumNoticeMinutes")
    horizon = event_type.get("maximumRangeDays")
    out_of_bounds = False
    reasons: list[str] = []
    if isinstance(notice, int) and begins < moment + timedelta(minutes=int(notice)):
        out_of_bounds = True
        reasons.append(f"the host needs {int(notice)} minutes' notice and this is {iso(begins)}")
    if isinstance(horizon, int) and begins > moment + timedelta(days=int(horizon)):
        out_of_bounds = True
        reasons.append(f"bookings open {int(horizon)} days ahead and this is {iso(begins)}")
    record(
        "out_of_bounds",
        not out_of_bounds,
        "; ".join(reasons) if reasons else f"{iso(begins)} is inside the booking window",
        rule="allowBookingOutOfBounds skips the window check for a privileged caller",
        flag="allowBookingOutOfBounds",
    )

    # 4. The limit. Counted over the attendee's live bookings on this event type.
    limit = event_type.get("bookingLimitPerAttendee")
    live = [
        other
        for other in attendee_bookings
        if str(other.get("status") or "") in OCCUPYING_STATUSES
        and str(other.get("eventTypeId") or "") == str(event_type.get("id") or "")
    ]
    over_limit = isinstance(limit, int) and len(live) >= int(limit)
    record(
        "booking_limit",
        not over_limit,
        (
            f"{address} already holds {len(live)} live booking(s) on this event type and the limit is {int(limit)}"
            if over_limit
            else f"{address} holds {len(live)} live booking(s) on this event type"
        ),
        rule="skipBookingLimits skips the limit for a privileged caller",
        flag="skipBookingLimits",
    )

    confirmation = requires_confirmation(event_type)
    decision: dict[str, Any] = {
        "status": "PENDING" if confirmation else "ACCEPTED",
        "requires_confirmation": confirmation,
        "start": begins,
        "end": ends,
        "duration_minutes": duration,
        "email_verification": gate,
        "bypasses": resolved_bypasses,
        "roles": roles,
        "checks": checks,
        "refusals": refusals,
        # The one-time password is issued only for a request. The evidence shows
        # it beside `"requiresConfirmation": true`, so a booking created without
        # confirmation has nothing to manage while it waits and carries none.
        "one_time_password": mint_one_time_password() if confirmation else None,
        "holds_slot": True,
    }
    return decision


# --------------------------------------------------------------------------- #
# The decision path
# --------------------------------------------------------------------------- #


def evaluate_decision(
    booking: Mapping[str, Any],
    *,
    decision: str,
    actor: str | None = None,
    driver: str = "host",
    caller: Mapping[str, Any] | None = None,
    one_time_password: str | None = None,
    reason: str | None = None,
    api_version: str | None = None,
    bypass: Mapping[str, Any] | None = None,
    overlapping: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Decide whether a host may confirm or decline this request, and with what.

    The researched rule for both routes is one sentence: "The provided
    authorization header refers to the owner of the booking." So the primary
    credential is ownership - the host, the event owner, an assigned user, a team
    admin or an organisation admin - and that check runs first and is published
    in the returned ``authorisation`` block whichever way it goes.

    The one-time password is accepted as an *alternative* credential, and that
    is an inference rather than something the research states. The research
    issues a ``oneTimePassword`` and puts it in the ``BOOKING_REQUESTED`` payload
    but never says what, if anything, may be done with it. Accepting it is
    chosen because a credential that authorises nothing is a secret that lies
    about its power, and because a request somebody cannot be routed to is the
    case the research's own flow cares about ("the sales room / rep is notified
    and can surface the request"). It is reported in ``inferences`` so a reviewer
    can disagree with it by name.

    ``driver="unattended"`` is the researched "unattended approvals can be
    bypassed server-side for trusted hosts by the newer bypass flags". It is
    refused unless the bypass *would* be honoured - a privileged, authenticated
    caller, on one of the two named API versions, asking for a flag that
    applies. That is the same conjunction
    :func:`resolve_bypasses` already applies, run against the decision rather
    than the request, and a flag that is ignored makes an unattended approval
    illegal rather than merely ineffectual.
    """
    action = str(decision or "").strip().lower()
    if action not in ("confirm", "decline"):
        raise ApprovalError(
            f"decision must be confirm or decline, not {decision!r}",
            code="invalid_request",
            status=422,
        )
    how = str(driver or "host").strip().lower()
    if how not in DECISION_DRIVERS:
        raise ApprovalError(
            f"driver must be one of {', '.join(DECISION_DRIVERS)}, not {driver!r}",
            code="invalid_request",
            status=422,
        )

    roles = privilege_roles(booking, caller)
    authenticated = is_authenticated(caller)
    presented_password = passwords_match(
        one_time_password, str(booking.get("oneTimePassword") or "")
    )
    resolved_bypasses = resolve_bypasses(
        requested=bypass,
        api_version=api_version,
        roles=roles,
        authenticated=authenticated,
    )
    honoured = set(resolved_bypasses["honoured"])

    authorisation = {
        "roles": roles,
        "authenticated": authenticated,
        "one_time_password_accepted": presented_password,
        "by_ownership": bool(roles),
        "by_one_time_password": presented_password,
        "permitted": bool(roles) or presented_password,
    }

    if how == "unattended" and not honoured:
        ignored = resolved_bypasses["ignored"]
        detail = (
            "an unattended approval needs a bypass this caller is entitled to, and these were not: "
            + "; ".join(str(entry["detail"]) for entry in ignored)
            if ignored
            else "an unattended approval needs one of "
            + ", ".join(BYPASS_FLAGS)
            + f" honoured, which needs an authenticated caller holding one of {', '.join(PRIVILEGED_ROLES)}"
        )
        raise ApprovalError(
            detail,
            code="unattended_not_permitted",
            status=403,
            refusals=[dict(entry) for entry in ignored],
        )

    if not authorisation["permitted"]:
        raise ApprovalError(
            "the authorization header does not refer to the owner of this booking, and no "
            f"one-time password was presented; a decision needs one of {', '.join(PRIVILEGED_ROLES)} "
            "over this booking",
            code="not_booking_owner",
            status=403,
        )

    status = str(booking.get("status") or "")
    if status != DECIDABLE_STATUS:
        raise ApprovalError(
            f"booking {booking.get('uid') or booking.get('id')} is {status or 'unknown'}, and only a "
            f"{DECIDABLE_STATUS} request can be confirmed or declined",
            code="not_pending",
            status=409,
        )

    outcome: dict[str, Any] = {
        "decision": action,
        "driver": how,
        "decided_by": str((caller or {}).get("userId") or actor or "unknown"),
        "authorisation": authorisation,
        "bypasses": resolved_bypasses,
        "status": "ACCEPTED" if action == "confirm" else "REJECTED",
        "one_time_password": None,
        "holds_slot": True,
        "release_reason": None,
    }

    if action == "decline":
        text = str(reason or "").strip()
        outcome["rejection_reason"] = text or DEFAULT_REJECTION_REASON
        outcome["reason_supplied"] = bool(text)
        # The decline is what releases the hold: the booking stops being in
        # OCCUPYING_STATUSES, so the slot returns to the host's availability. A
        # calendar event is *not* created and none is released, because the
        # research says the event is "created only on confirm".
        outcome["holds_slot"] = False
        outcome["release_reason"] = f"declined: {outcome['rejection_reason']}"
        return outcome

    # Confirming. The slot may have been taken since the request was made, which
    # is the one race this workflow really has: a request holds a slot, and two
    # people can want the same one.
    clashes = [
        other
        for other in overlapping
        if str(other.get("uid") or "") != str(booking.get("uid") or "")
        and str(other.get("status") or "") in OCCUPYING_STATUSES
        and overlaps(
            parse_moment(booking.get("start"), "start"),
            parse_moment(booking.get("end"), "end"),
            parse_moment(other.get("start"), "start"),
            parse_moment(other.get("end"), "end"),
        )
    ]
    if clashes and "allowConflicts" not in honoured:
        raise ApprovalError(
            f"the slot is now held by {len(clashes)} other live booking(s), so this request cannot be confirmed",
            code="slot_conflict",
            status=409,
            refusals=[
                refusal(
                    "slot_conflict",
                    f"the slot is now held by {len(clashes)} other live booking(s)",
                    status=409,
                    bypassed_by="allowConflicts",
                    bypass_honoured=False,
                )
            ],
        )
    outcome["holds_slot"] = True
    return outcome


# --------------------------------------------------------------------------- #
# Routing and dispatch
# --------------------------------------------------------------------------- #


def resolve_routing(
    event_type: Mapping[str, Any],
    *,
    skip_contact_owner: bool = False,
    contact_owner: str | None = None,
) -> dict[str, Any]:
    """Who a request is routed to, honouring the researched ``skipContactOwner``.

    The research lists "``skipContactOwner`` on routing" as a tool this workflow
    has and does not say what it skips *past*. The reading taken here is the
    ordinary one: the contact owner is the person who owns the relationship the
    request came from, and skipping them routes the request past them to the
    host instead - which is what a room does when a request should go straight to
    the person who has to say yes. So the flag removes one *role*, not one
    person: an id can appear in both ``recipients`` and ``skipped`` when the host
    is also the contact owner, because the flag skipped their routing role and
    not their ability to decide. It is reported in ``inferences``.
    """
    host = str(event_type.get("hostId") or "").strip() or None
    owner = str(contact_owner or "").strip() or None
    recipients: list[str] = []
    skipped: list[str] = []
    if owner and not skip_contact_owner:
        recipients.append(owner)
    elif owner and skip_contact_owner:
        skipped.append(owner)
    if host and host not in recipients:
        recipients.append(host)
    return {
        "skipContactOwner": bool(skip_contact_owner),
        "contactOwnerId": owner,
        "hostId": host,
        "recipients": recipients,
        "skipped": skipped,
        "assignedTo": recipients[0] if recipients else None,
    }


def matching_rules(rules: Iterable[Mapping[str, Any]], trigger: str) -> list[dict[str, Any]]:
    """The enabled rules a trigger fires, in the order the rules were declared.

    A rule whose ``trigger`` is not the researched vocabulary, or whose
    ``channels`` is empty, matches nothing: a rule that names a trigger this
    workflow does not raise is inert, and saying so is better than firing
    everything.
    """
    fired: list[dict[str, Any]] = []
    for rule in rules:
        data = rule.get("data") if isinstance(rule.get("data"), Mapping) else rule
        # `enabled` is read from the payload with the envelope as a fallback,
        # because a rule written through the generic record surface has no
        # envelope to read it from. Absent means enabled, which is why a rule
        # that never mentions it fires.
        enabled = data.get("enabled", rule.get("enabled", True))
        if not enabled:
            continue
        if str(data.get("trigger") or "") != trigger:
            continue
        channels = [
            str(name) for name in (data.get("channels") or []) if str(name) in NOTIFICATION_CHANNELS
        ]
        if not channels:
            continue
        fired.append(
            {
                "id": rule.get("id"),
                "label": data.get("label"),
                "trigger": trigger,
                "channels": channels,
            }
        )
    return fired


def validate_automation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A workflow rule on one of this workflow's two researched triggers."""
    data = dict(payload or {})
    trigger = str(data.get("trigger") or "")
    if trigger not in WORKFLOW_TRIGGERS:
        raise ApprovalError(
            f"trigger must be one of {', '.join(WORKFLOW_TRIGGERS)}, not {trigger!r}",
            code="invalid_automation",
            status=422,
        )
    channels = data.get("channels")
    if not isinstance(channels, (list, tuple)) or not channels:
        raise ApprovalError(
            f"channels must name at least one of {', '.join(NOTIFICATION_CHANNELS)}",
            code="invalid_automation",
            status=422,
        )
    unknown = [str(name) for name in channels if str(name) not in NOTIFICATION_CHANNELS]
    if unknown:
        raise ApprovalError(
            f"unknown channel(s) {', '.join(unknown)}; this workflow dispatches "
            f"{', '.join(NOTIFICATION_CHANNELS)}",
            code="invalid_automation",
            status=422,
        )
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ApprovalError("enabled must be true or false", code="invalid_automation", status=422)
    return {
        **data,
        "trigger": trigger,
        "channels": [str(name) for name in channels],
        "enabled": enabled,
        "label": str(data.get("label") or f"{trigger} dispatch"),
    }


# --------------------------------------------------------------------------- #
# Webhook payloads
# --------------------------------------------------------------------------- #


def _webhook(event: str, booking: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
    """The researched webhook envelope around a booking payload.

    Envelope keys are the vendor's own: ``event``, ``createdAt``,
    ``triggerEvent`` and ``payload``. ``triggerEvent`` is ``None`` because this
    workflow raises these webhooks itself rather than in response to another one.
    """
    data = dict(booking.get("data") if isinstance(booking.get("data"), Mapping) else booking)
    return {
        "event": event,
        "createdAt": created_at,
        "triggerEvent": None,
        "payload": {
            "uid": data.get("uid"),
            "title": data.get("title"),
            "status": data.get("status"),
            "requiresConfirmation": bool(data.get("requiresConfirmation")),
            "oneTimePassword": data.get("oneTimePassword"),
            "rejectionReason": data.get("rejectionReason"),
            "eventTypeId": data.get("eventTypeId"),
            "hostId": data.get("hostId"),
            "startTime": data.get("start"),
            "endTime": data.get("end"),
            "booker": data.get("booker"),
            "attendee": data.get("attendee"),
            "organizer": data.get("organizer"),
            "location": data.get("location"),
            "createdAt": data.get("requestedAt"),
        },
    }


def booking_requested_payload(booking: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
    """``BOOKING_REQUESTED``, in the shape the research quotes."""
    return _webhook("BOOKING_REQUESTED", booking, created_at=created_at)


def booking_rejected_payload(booking: Mapping[str, Any], *, created_at: str) -> dict[str, Any]:
    """``BOOKING_REJECTED``, carrying the ``rejectionReason`` the research quotes."""
    return _webhook("BOOKING_REJECTED", booking, created_at=created_at)


def calendar_event_for(booking: Mapping[str, Any], *, uid: str, now: datetime) -> dict[str, Any]:
    """The calendar event a confirmation creates.

    The research is explicit that a calendar event is "created only on confirm",
    so this is built here and nowhere else: there is no path in this module that
    produces one for a pending or rejected booking.
    """
    data = dict(booking.get("data") if isinstance(booking.get("data"), Mapping) else booking)
    return {
        "uid": uid,
        "bookingUid": data.get("uid"),
        "eventTypeId": data.get("eventTypeId"),
        "title": data.get("title"),
        "hostId": data.get("hostId"),
        "start": data.get("start"),
        "end": data.get("end"),
        "location": data.get("location"),
        "attendee": data.get("attendee"),
        "createdAt": iso(now),
        "createdBecause": "a host confirmed the request",
    }


# --------------------------------------------------------------------------- #
# Reference data, served as data
# --------------------------------------------------------------------------- #


def vocabulary() -> dict[str, Any]:
    """Every published vocabulary, in one payload."""
    return {
        "ticket": "WF-062",
        "statuses": [
            {
                "value": status,
                "api": API_STATUS[status],
                "meaning": STATUS_MEANING[status],
                "occupies_the_host": status in OCCUPYING_STATUSES,
                "decidable": status == DECIDABLE_STATUS,
                "written_by_this_workflow": status != "CANCELLED",
            }
            for status in STATUSES
        ],
        "webhook_events": [
            {"event": event, "meaning": WEBHOOK_MEANING[event]} for event in WEBHOOK_EVENTS
        ],
        "workflow_triggers": [
            {"trigger": trigger, "meaning": TRIGGER_MEANING[trigger]}
            for trigger in WORKFLOW_TRIGGERS
        ],
        "notification_channels": list(NOTIFICATION_CHANNELS),
        "dispatched_by": DISPATCHED_BY,
        "decision_drivers": list(DECISION_DRIVERS),
        "bypass_flags": [
            {
                "flag": flag,
                "skips": BYPASS_CHECK[flag],
                "honoured_only_on": list(BYPASS_API_VERSIONS),
                "honoured_only_for": list(PRIVILEGED_ROLES),
            }
            for flag in BYPASS_FLAGS
        ],
        "privileged_roles": [
            {"role": role, "meaning": PRIVILEGED_ROLE_MEANING[role]} for role in PRIVILEGED_ROLES
        ],
        "default_rejection_reason": DEFAULT_REJECTION_REASON,
        "collections": dict(COLLECTIONS),
        "collection_meanings": dict(COLLECTION_MEANING),
        "counts": {
            "statuses": len(STATUSES),
            "webhook_events": len(WEBHOOK_EVENTS),
            "workflow_triggers": len(WORKFLOW_TRIGGERS),
            "notification_channels": len(NOTIFICATION_CHANNELS),
            "bypass_flags": len(BYPASS_FLAGS),
            "privileged_roles": len(PRIVILEGED_ROLES),
        },
    }


def capabilities() -> dict[str, Any]:
    """The researched ``apis_hit``, as a description of what this feature does.

    The research names five vendor surfaces. This is where a reader sees which of
    them this workflow actually implements and where each one lives in the
    feature's own routes, which is the difference between the list being true of
    the code and true only of the documentation.
    """
    return {
        "ticket": "WF-062",
        "apis": [
            {
                "researched": "POST /v2/bookings",
                "here": "POST /api/wf-062/rooms/{room_id}/requests",
                "implements": (
                    "Creating the booking with requiresConfirmation semantics. Returns "
                    "requiresConfirmation, oneTimePassword, status and rejectionReason."
                ),
                "implemented": True,
            },
            {
                "researched": "POST /v2/bookings/{bookingUid}/confirm",
                "here": "POST /api/wf-062/rooms/{room_id}/requests/{uid}/confirm",
                "implements": (
                    "Confirm a booking. The authorization header refers to the owner of the "
                    "booking; a one-time password is accepted as an alternative credential."
                ),
                "implemented": True,
            },
            {
                "researched": "POST /v2/bookings/{bookingUid}/decline",
                "here": "POST /api/wf-062/rooms/{room_id}/requests/{uid}/decline",
                "implements": (
                    "Decline a booking, optionally with a reason. Emits BOOKING_REJECTED "
                    "carrying rejectionReason and releases the held slot."
                ),
                "implemented": True,
            },
            {
                "researched": (
                    "GET/POST /v2/bookings/email-verification/... "
                    "(check required, send code, verify with code)"
                ),
                "here": (
                    "GET /api/wf-062/rooms/{room_id}/email-verification/required, "
                    "POST .../send, POST .../verify"
                ),
                "implements": (
                    "The separate optional gate. A booking on an event type with email "
                    "verification enabled needs a verified code in the request body."
                ),
                "implemented": True,
            },
            {
                "researched": "Webhooks BOOKING_REQUESTED and BOOKING_REJECTED",
                "here": "GET /api/wf-062/rooms/{room_id}/requests/{uid}/webhooks",
                "implements": "Both payloads are recorded in full, in the researched shape.",
                "implemented": True,
            },
        ],
        "not_implemented": [
            {
                "surface": "Reschedule and cancel a booking",
                "why": (
                    "The researched status enum includes CANCELLED, but rescheduling and "
                    "cancelling a meeting is WF-064's workflow. This feature publishes the "
                    "status and never writes it."
                ),
            },
            {
                "surface": "The wider vendor workflow trigger enum",
                "why": (
                    "beforeEvent, afterEvent, newEvent and the rest belong to the reminders "
                    "workflow (WF-061). This one raises exactly the two triggers it owns."
                ),
            },
            {
                "surface": "Real mail and SMS delivery",
                "why": (
                    "This product has no mail or SMS transport. A dispatch is recorded with "
                    "dispatched_by: 'simulated' so nobody reads it as a delivery receipt."
                ),
            },
        ],
    }


def inferences() -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research is specific about the states, the webhooks, the optional email
    gate, the bypass flags and who may use them. It is silent about the edges,
    and an edge left as a comment in a function body is one nobody re-reads. Each
    entry is named, says what the research does and does not say, and says what
    to change to move it.
    """
    return {
        "ticket": "WF-062",
        "inferences": [
            {
                "id": "a-decline-releases-the-held-slot",
                "topic": "what a decline does to the host's availability",
                "basis": (
                    "The data flow says the decision leads to a 'calendar event created or "
                    "released', and lists 'host identity/availability' as a data source, but "
                    "does not say what is released when a request is declined."
                ),
                "value": {
                    "reading": "A decline releases the hold the request was holding.",
                    "mechanism": (
                        "A PENDING request occupies the slot; a REJECTED one does not, so the "
                        "slot returns to the host and the same time can be requested again."
                    ),
                    "not_done": "No calendar event is released, because none is ever created for a request.",
                },
                "why": (
                    "The alternative reading - a decline releases a calendar event - is "
                    "impossible here, because the research says the event is 'created only on "
                    "confirm'. Reading the sentence any other way leaves 'released' naming "
                    "nothing at all."
                ),
                "change_it": "OCCUPYING_STATUSES in dsr/booking_approval.py.",
                "blast_radius": "Which bookings conflict, and what a request can be confirmed onto.",
            },
            {
                "id": "the-one-time-password-authorises-a-decision",
                "topic": "what the issued oneTimePassword is for",
                "basis": (
                    "The research issues a oneTimePassword, puts it in the BOOKING_REQUESTED "
                    "payload, and never says what may be done with it. The confirm and decline "
                    "routes are governed by ownership alone."
                ),
                "value": {
                    "reading": "It is accepted as an alternative credential on the decision routes.",
                    "still_required": (
                        "Ownership is checked first and is the rule the API reference states; the "
                        "password is an additional way in, not a replacement."
                    ),
                },
                "why": (
                    "A credential that authorises nothing is a secret that lies about its power, "
                    "and the researched flow cares about the case where the person who should "
                    "decide is not the host ('the sales room / rep is notified and can surface "
                    "the request')."
                ),
                "change_it": "evaluate_decision in dsr/booking_approval.py; drop `presented_password` from the permitted set.",
                "blast_radius": "Only requests decided by somebody who is not the owner.",
            },
            {
                "id": "an-ignored-bypass-is-not-a-refusal",
                "topic": "what happens when an unprivileged caller sends a bypass flag",
                "basis": (
                    "The research says the flags are 'only honoured' for an authenticated user "
                    "with one of five roles, on two named API versions. It does not give a status "
                    "code for the caller that is not entitled to one."
                ),
                "value": {
                    "reading": "The flag is ignored and the check it would have skipped applies.",
                    "reported": "Every ignored flag is returned with the reason it was ignored.",
                },
                "why": (
                    "'Only honoured' is a statement about effect and not about status. Ignoring "
                    "also keeps an old client from learning that a privilege exists, and it is "
                    "the behaviour that matches a server that simply does not implement the flag "
                    "for that caller."
                ),
                "change_it": "resolve_bypasses in dsr/booking_approval.py; raise instead of recording.",
                "blast_radius": "Every request that sends a flag it is not entitled to.",
            },
            {
                "id": "unattended-approval-needs-an-entitled-bypass",
                "topic": "what 'unattended approvals can be bypassed server-side' means here",
                "basis": (
                    "The research says unattended approvals can be bypassed for trusted hosts by "
                    "the newer bypass flags. It does not say which flag, or on which surface."
                ),
                "value": {
                    "reading": (
                        "driver='unattended' is honoured only when resolve_bypasses would honour "
                        "a flag this caller asked for: authenticated, one of the five roles, on "
                        "2026-02-25 or 2026-05-01."
                    ),
                    "refusal": "Otherwise 403 unattended_not_permitted, with the ignored flags' reasons.",
                },
                "why": (
                    "The conditions the research attaches to a bypass are the only conditions it "
                    "gives for an unattended approval, so applying exactly those is the reading "
                    "with the fewest assumptions in it."
                ),
                "change_it": "evaluate_decision in dsr/booking_approval.py.",
                "blast_radius": "Only decisions that claim nobody human made them.",
            },
            {
                "id": "the-bounds-and-limits-are-configuration",
                "topic": "the numbers the out-of-bounds and limit checks need",
                "basis": (
                    "The research names allowBookingOutOfBounds and skipBookingLimits, so the "
                    "checks exist, and fixes no number for either."
                ),
                "value": {
                    "reading": (
                        "minimumNoticeMinutes, maximumRangeDays and bookingLimitPerAttendee are "
                        "the event type's own JSON fields."
                    ),
                    "absent_means": "The check is not applied. No constant is defaulted.",
                },
                "why": (
                    "A default would be a rule nobody sourced, in the one file a reader would "
                    "assume holds only sourced rules. Absent-means-unbounded is also the only "
                    "reading that lets a team configure the gate without a code change."
                ),
                "change_it": "evaluate_request in dsr/booking_approval.py; validate_event_type validates the fields.",
                "blast_radius": "Which requests are refused as out of bounds or over the limit.",
            },
            {
                "id": "skip-contact-owner-removes-one-recipient",
                "topic": "what skipContactOwner skips past",
                "basis": (
                    "The research lists 'skipContactOwner on routing' among this workflow's "
                    "tools and never explains it."
                ),
                "value": {
                    "reading": "It removes the contact owner from the routing and leaves the host.",
                    "still_routed": "The host is notified either way, because the host is who decides.",
                },
                "why": (
                    "The host has to see the request whatever the routing says - a routing flag "
                    "that could withhold a request from the only person authorised to decide "
                    "would strand it."
                ),
                "change_it": "resolve_routing in dsr/booking_approval.py.",
                "blast_radius": "Who is notified when a slot is requested.",
            },
            {
                "id": "authentication-gates-the-bypass-not-the-decision",
                "topic": "where the 'authenticated' condition applies",
                "basis": (
                    "The researched sentence conditions the bypass on 'an authenticated user'. "
                    "The confirm and decline sentence conditions them on ownership and says "
                    "nothing about authentication."
                ),
                "value": {
                    "reading": "Ownership alone permits a decision; authentication is additionally required for a bypass.",
                    "authenticated_means": "The caller asserts it and presents an identity; this product has no session layer.",
                },
                "why": (
                    "Adding an authentication requirement to a decision the research does not "
                    "put one on would be a rule invented to look careful. Keeping the asymmetry "
                    "visible is better than hiding it, so it is here and in the vocabulary."
                ),
                "change_it": "is_authenticated and privilege_roles in dsr/booking_approval.py.",
                "blast_radius": "Every decision and every bypass in the product.",
            },
            {
                "id": "a-per-request-override-can-only-add-approval",
                "topic": "whether a request can declare itself not to need approval",
                "basis": (
                    "The user flow has two entries: the admin enables requires confirmation on "
                    "the event type, 'or the booking is created as a request'. It does not say "
                    "which of the two wins when a type requires confirmation and a caller asks "
                    "for the booking directly."
                ),
                "value": {
                    "reading": (
                        "requiresConfirmationOverride may only add approval. true on a type whose "
                        "toggle is off produces a request; false on a type whose toggle is on "
                        "does not."
                    ),
                    "one_way": "The event type's own toggle is never weakened by a request.",
                },
                "why": (
                    "Read the other way, the host's approval is not a step at all - any caller "
                    "who knows the field name can turn it off, and the host is never told a slot "
                    "was taken. The flow's two entries are two ways *in* to a request, not a "
                    "priority rule between them, so honouring the stronger one costs nothing."
                ),
                "change_it": "requires_confirmation in dsr/booking_approval.py.",
                "blast_radius": "Every request whose type has the toggle on.",
            },
            {
                "id": "confirmation-can-lose-the-slot",
                "topic": "what a host does when the slot was taken while the request waited",
                "basis": (
                    "The research says a pending request waits for a host and that availability "
                    "conflict checks exist. It does not say whether confirming a stale request "
                    "succeeds."
                ),
                "value": {
                    "reading": "Confirming onto a slot another live booking holds is a 409, unless allowConflicts is honoured.",
                    "checked_against": "Bookings in PENDING or ACCEPTED that overlap the request.",
                },
                "why": (
                    "A request holds its slot, so this only bites when two requests for one slot "
                    "were both admitted - and then the honest answer is a conflict the host can "
                    "override with the researched flag, not a double booking."
                ),
                "change_it": "evaluate_decision in dsr/booking_approval.py, the confirm branch.",
                "blast_radius": "Only confirmations of a slot somebody else is holding.",
            },
        ],
        "count": 9,
    }
