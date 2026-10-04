"""WF-081: the expiry rules, as pure functions over numbers.

Every rule here is a statement about an instant, and none of them reads or writes
anything. The engine beside this module turns them into records; this module is
what a test can call with two integers and check.

The four rules the rest of the product leans on
-----------------------------------------------

**Absence of an expiry expires nothing.** "Only signature requests that explicitly
set an ``expires_at`` will expire. By default signature requests do not expire."
So :func:`is_expired` answers ``False`` for ``None`` rather than for a deadline
that has passed, and every caller that loops over requests has to filter on
"this one has an expiry" first. The most likely defect in a build of this workflow
is a sweep that treats a missing field as zero and closes every agreement in the
product that nobody asked to close.

**The window is checked on the value the caller sent, and the stored value is
rounded down.** "``expires_at`` must be an integer epoch timestamp in seconds
between 1-90 days in the future" and "``expires_at`` will be rounded down to the
nearest hour" are two sentences, and the order matters: a value inside the range
is inside the range whatever hour it rounds into. Rounding up would move a
deadline later, and a rounding rule that grants extra time is not a rounding rule.

**A reminder is due when the request is inside its lead window, and it is sent at
most once per day.** "Signature request reminder emails will be sent to the signer
3 and 7 days before the signature request expires" names two windows. "If a signer
was already reminded within 24 hours, we will skip the automated reminder" names
the skip. The 7-day window is reached first, so a request read once at 7 days out
is reminded, and read again an hour later the 3-day window is not due yet and the
7-day one is deduped. Both facts fall out of the arithmetic rather than out of a
flag.

**A completed signer is never swept.** "On expiry, unsigned signatures flip to
``expired`` ... Completed signers stay ``signed``." So the sweep moves a row whose
status is incomplete and touches nothing else. A sweep that set every status would
erase the one fact a buyer and a seller both need afterwards.

What this module does not decide
--------------------------------

Whether a signer was reminded, and when. That needs the ledger, so it lives in the
engine. The 24-hour rule is stated here as a comparison (:func:`dedupe_blocks`)
and the rows are written there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.security_governance import expiry_vocabulary as vocab

#: The payload-side room reference. Not ``room_id``: that key is part of the
#: record envelope and ``AuditedDatabase`` strips it out of ``data`` before the
#: dynamic index is built, so a payload that stored its room there would be
#: unfilterable. This is the same key WF-073 uses, deliberately, so one
#: convention covers the package.
ROOM_REF = "room_ref"

#: What the response says when it could not honour a timezone the caller named.
#: The instant is still exact, and only the local reading fell back.
NOTE_UNRESOLVED_TIMEZONE = (
    "This build has no timezone database, so the deadline is shown in UTC. The "
    "instant itself is exact."
)


class ExpiryError(ValueError):
    """A refusal this workflow owns.

    Carries the status and the code, so the HTTP layer is one handler for the
    whole hierarchy and cannot disagree with the rules about which refusal is
    which. Its own type rather than a shared one: a feature may only map error
    types it raises itself, because registering a handler for a shared type would
    intercept that exception across the whole product.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        status, sentence = vocab.ERROR_CODES.get(
            code, (422, "This request is not one this workflow accepts.")
        )
        super().__init__(detail or sentence)
        self.code = code
        self.status = status
        self.detail = detail or sentence


class ExpiryRequestNotFound(LookupError):
    """No such agreement, or it is not one this workflow owns."""


class ExpirySignerNotFound(LookupError):
    """No such signer on that request. Same reasoning as the class above."""


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def epoch_seconds(moment: datetime | None = None) -> int:
    """One instant as an integer epoch timestamp in seconds.

    Truncated rather than rounded, because the stored value is compared against a
    deadline and a deadline that rounds up is a deadline that grants time.
    """
    return int((moment or utcnow()).astimezone(timezone.utc).timestamp())


def from_epoch(seconds: Any) -> datetime:
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the hour shown.

    Milliseconds are kept because the reminder dedupe is a 24-hour window and two
    ledger rows inside one second have to stay distinguishable in a log a reader
    is auditing.
    """
    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def local_text(seconds: Any, tz_name: str | None) -> dict[str, Any]:
    """The deadline as one signer reads it, in that signer's own timezone.

    The specification's step three is "Signer sees the expiry date in the banner
    next to the required-field count." Only the reading changes. The epoch seconds
    the sweep compares are never adjusted, because a deadline that moved with a
    timezone is a deadline two signers disagree about.

    A timezone this build cannot resolve falls back to UTC and is reported as
    unknown in ``known`` and named in ``note``. A banner with the wrong local time
    is a cosmetic defect. Refusing the read, or guessing a plausible offset, would
    be worse than either: a guessed offset is a deadline displayed as another
    instant, and a signer who trusts it signs on the wrong date.
    """
    moment = from_epoch(seconds)
    zone = _zone(tz_name)
    local = moment.astimezone(zone)
    resolved = tz_name is not None and not _unresolved(zone, tz_name)
    return {
        "epoch_seconds": int(seconds),
        "iso_utc": moment.isoformat(timespec="seconds"),
        "timezone": _zone_name(zone, tz_name),
        "local": local.isoformat(timespec="minutes"),
        "offset_minutes": _offset_minutes(local),
        "known": resolved,
        "note": None if resolved else NOTE_UNRESOLVED_TIMEZONE,
    }


def _offset_minutes(moment: datetime) -> int:
    """A zone's offset from UTC in whole minutes, 0 at UTC itself."""
    return int((moment.utcoffset() or timedelta(0)).total_seconds() // 60)


def _zone_name(zone: Any, tz_name: str | None) -> str:
    """What to call the zone in the response: the resolved name, or what was asked."""
    key = getattr(zone, "key", None)
    if key:
        return str(key)
    if tz_name:
        return str(tz_name)
    return "UTC"


def _unresolved(zone: Any, tz_name: str | None) -> bool:
    """Was the caller's zone actually honoured, or did we fall back?"""
    if not tz_name:
        return True
    if str(tz_name).strip().startswith(("+", "-")):
        return False
    return not getattr(zone, "key", None)


def _zone(tz_name: str | None) -> Any:
    """The named zone, or UTC for a name this build cannot resolve.

    A fixed offset such as ``+05:30`` is resolved from the string itself and is
    always honoured, because a mobile client sends that far more often than an
    IANA name and the offset it sends is one it computed for this moment.

    An IANA name needs the timezone database, which is a package that may not be
    installed. Where it is missing every named zone resolves to UTC and the
    response says so, rather than this module carrying a table of offsets that
    would silently be wrong for half the year.
    """
    if not tz_name:
        return timezone.utc
    text = str(tz_name).strip()
    if not text:
        return timezone.utc
    if text[0] in "+-" and ":" in text[1:]:
        try:
            sign = -1 if text[0] == "-" else 1
            hours, minutes = text[1:].split(":", 1)
            return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
        except (ValueError, TypeError):
            return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # noqa: BLE001 - a missing zone must not fail a read
        return timezone.utc


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def coerce_expires_at(value: Any, now: datetime | None = None) -> int | None:
    """The stored ``expires_at``: an integer epoch, or ``None`` for "no expiry".

    "``expires_at`` must be an integer epoch timestamp in seconds between 1-90
    days in the future."

    Four refusals are possible and each has its own code, because a caller filling
    in a form needs to be told which rule its value broke:

    * a value that is not a whole number of seconds. A float is refused rather
      than truncated: "must be an integer" is the specification's word, and
      silently dropping the fraction of a second a client sent is how a deadline
      ends up somewhere its sender did not choose.
    * a value in the past, or within the first day.
    * a value beyond ninety days.
    * a value that is not a number at all.

    The range is checked before the rounding, on the value the caller sent. See
    this module's docstring for why the order is the one it is.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # True is an int in Python, and a boolean deadline is not a deadline.
        raise ExpiryError("expiry_not_an_integer")
    if isinstance(value, str):
        text = value.strip()
        # An empty string is treated as "the caller did not set one", the same as an
        # absent key. An empty list and an empty object are not: they are values
        # that arrived where an integer was expected, and silently reading them as
        # "no expiry" would turn a client bug into an agreement that never closes.
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ExpiryError("expiry_not_an_integer") from exc
        if not text.lstrip("+-").isdigit():
            raise ExpiryError("expiry_not_an_integer")
        value = parsed
    if isinstance(value, float):
        if not value.is_integer():
            raise ExpiryError("expiry_not_an_integer")
        value = int(value)
    if not isinstance(value, int):
        raise ExpiryError("expiry_not_an_integer")

    moment = now or utcnow()
    current = epoch_seconds(moment)
    minimum = current + int(timedelta(days=vocab.MIN_EXPIRY_DAYS).total_seconds())
    maximum = current + int(timedelta(days=vocab.MAX_EXPIRY_DAYS).total_seconds())
    if value < minimum or value > maximum:
        raise ExpiryError("expiry_out_of_range")
    return round_down_to_hour(value)


def round_down_to_hour(value: int) -> int:
    """ "expires_at will be rounded down to the nearest hour."

    Flooring, always. The result is never greater than the value that went in, so
    the rule can only ever bring a deadline forward.
    """
    return (int(value) // vocab.ROUND_DOWN_SECONDS) * vocab.ROUND_DOWN_SECONDS


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""
    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


# --------------------------------------------------------------------------- #
# The deadline
# --------------------------------------------------------------------------- #


def expires_at_of(data: Mapping[str, Any]) -> int | None:
    """The stored deadline, or ``None``. Never guesses a value for a missing key."""
    value = data.get(vocab.EXPIRES_AT)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def has_expiry(data: Mapping[str, Any]) -> bool:
    """Did anybody explicitly set an expiry on this request?

    This is the whole of "Only signature requests that explicitly set an
    ``expires_at`` will expire." Every sweep and every deadline check asks this
    first, because a request without one must never be closed.
    """
    return expires_at_of(data) is not None


def is_expired(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Has the deadline passed?

    ``False`` for a request with no expiry, and that is the important half of this
    function. A missing field is not a deadline in the past; it is no deadline at
    all, and reading it as zero would close every agreement the product has.
    """
    deadline = expires_at_of(data)
    if deadline is None:
        return False
    return epoch_seconds(now) >= deadline


def seconds_remaining(data: Mapping[str, Any], now: datetime | None = None) -> int | None:
    """How long until the deadline, in seconds. ``None`` for no deadline.

    Negative once the deadline has passed, because the caller needs the number to
    render and a clamped zero would hide the fact that it is late.
    """
    deadline = expires_at_of(data)
    if deadline is None:
        return None
    return deadline - epoch_seconds(now)


def days_remaining(data: Mapping[str, Any], now: datetime | None = None) -> float | None:
    """The same, in days, rounded to two places because the page shows a day count."""
    remaining = seconds_remaining(data, now)
    if remaining is None:
        return None
    return round(remaining / 86400.0, 2)


# --------------------------------------------------------------------------- #
# The reminder cadence
# --------------------------------------------------------------------------- #


def due_reminders(data: Mapping[str, Any], now: datetime | None = None) -> list[int]:
    """The lead days whose window this request is inside, nearest deadline first.

    "Signature request reminder emails will be sent to the signer 3 and 7 days
    before the signature request expires." A window is ``lead_days`` wide rather
    than a single day, because the specification pairs the cadence with a 24-hour
    dedupe, and a dedupe only means something when the trigger fires more often
    than once a day. The windows therefore run from ``lead`` days out to ``lead - 1``
    days out, and the 24-hour rule does the rest.

    ``[7]`` and ``[3]`` and ``[]`` are the three answers for a request 6 days out,
    one 3 days out, and one 8 days out. A request past its deadline is ``[]``: the
    sweep owns that request now, and a reminder for an expired agreement is the
    one message that must not be sent.
    """
    deadline = expires_at_of(data)
    if deadline is None:
        return []
    remaining_days = seconds_remaining(data, now) / 86400.0
    due: list[int] = []
    for lead in sorted(vocab.REMINDER_LEAD_DAYS, reverse=True):
        if lead - 1 < remaining_days <= lead:
            due.append(lead)
    return due


def dedupe_blocks(last_sent_at: Any, now: datetime | None = None) -> bool:
    """Did this signer get a reminder within the last 24 hours?

    "If a signer was already reminded within 24 hours, we will skip the automated
    reminder." Only a real send blocks; a skip does not, because a skip recorded
    as a send would mean the second read of a request could never remind anybody
    at all.
    """
    if not last_sent_at:
        return False
    moment = _parse_iso(last_sent_at)
    if moment is None:
        return False
    window = timedelta(hours=vocab.DEDUPE_WINDOW_HOURS)
    return (now or utcnow()) - moment < window


def reminder_plan(
    data: Mapping[str, Any], ledger: Sequence[Mapping[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """Whether a reminder is due for one signer, and what the rule says about it.

    Three answers, and the caller needs all of them:

    * ``due: []`` - the request has no expiry, or is past it. No reminder.
    * ``due: [7]`` with ``blocked: False`` - send it, at the 7-day lead.
    * ``due: [7]`` with ``blocked: True`` - skip it, because this signer was
      reminded inside 24 hours. The ledger records the skip, so the rule is
      visible rather than merely asserted.

    ``ledger`` is that signer's reminder rows for this request, newest or oldest -
    the order does not matter, only the last real send.
    """
    moment = now or utcnow()
    due = due_reminders(data, moment)
    last_sent = _last_sent(ledger)
    blocked = dedupe_blocks(last_sent, moment) if due else False
    deadline = expires_at_of(data)
    return {
        "has_expiry": deadline is not None,
        "expires_at": deadline,
        "due": due,
        "due_now": bool(due),
        "blocked_by_dedupe": blocked,
        "lead_days": due[0] if due else None,
        "last_reminded_at": last_sent,
        "sent_count": sum(1 for row in ledger if row.get("outcome") == "sent"),
        "skipped_count": sum(1 for row in ledger if row.get("outcome") == "skipped"),
    }


def _last_sent(ledger: Sequence[Mapping[str, Any]]) -> str | None:
    """The most recent real send in the ledger, as an ISO instant."""
    stamps = [
        str(row.get("at")) for row in ledger if row.get("outcome") == "sent" and row.get("at")
    ]
    if not stamps:
        return None
    return max(stamps, key=_sort_key)


def _sort_key(text: str) -> float:
    moment = _parse_iso(text)
    return moment.timestamp() if moment else 0.0


def _parse_iso(text: Any) -> datetime | None:
    """Parse an instant this package wrote, or return ``None``.

    Only the two shapes :func:`stamp` produces are accepted, plus a bare epoch
    number for a ledger row a caller wrote by hand. Anything else is ``None``
    rather than a guess, because a misread timestamp that made a dedupe block
    forever would silence a signer's reminder permanently.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return from_epoch(text)
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


def swept_signers(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Which signature rows this sweep moves, and which it must not touch.

    "On expiry, unsigned signatures flip to ``expired`` ... Completed signers stay
    ``signed``." The set is decided by status, not by position: a signer who
    already signed keeps ``signed``, and one already ``expired`` keeps ``expired``
    without a second transition.
    """
    moved: list[dict[str, Any]] = []
    for index, signer in enumerate(data.get("signatures") or []):
        if not isinstance(signer, Mapping):
            continue
        code = str(signer.get("status_code") or "awaiting_signature")
        if code in (vocab.STATUS_SIGNED, "completed"):
            continue
        moved.append(
            {
                "index": index,
                "email": signer.get("email"),
                "from": code,
                "to": vocab.STATUS_EXPIRED,
            }
        )
    return moved


def apply_sweep(data: Mapping[str, Any]) -> dict[str, Any]:
    """The request data after the sweep, with every incomplete signer expired.

    Returns a new payload rather than mutating in place, so a caller that wants to
    know whether anything changed can compare the two, and so a rejected sweep
    leaves nothing half-applied.

    The completed signers are copied through untouched, which is what "Completed
    signers stay ``signed``" asks for and what makes the record still worth
    reading afterwards.
    """
    signatures: list[dict[str, Any]] = []
    for signer in data.get("signatures") or []:
        if not isinstance(signer, Mapping):
            signatures.append(signer)
            continue
        code = str(signer.get("status_code") or "awaiting_signature")
        if code in (vocab.STATUS_SIGNED, "completed"):
            signatures.append(dict(signer))
            continue
        entry = dict(signer)
        entry["status_code"] = vocab.STATUS_EXPIRED
        entry["expired_at"] = vocab.EXPIRES_AT
        signatures.append(entry)

    result = dict(data)
    result["signatures"] = signatures
    return result


def is_complete(data: Mapping[str, Any]) -> bool:
    """Did every signer complete, so the deadline never fires?

    A request with no signers at all is not complete: an agreement nobody has to
    sign is not an agreement that is done.
    """
    signatures = [row for row in (data.get("signatures") or []) if isinstance(row, Mapping)]
    if not signatures:
        return False
    return all(
        str(row.get("status_code") or "") in (vocab.STATUS_SIGNED, "completed")
        for row in signatures
    )


def request_status(data: Mapping[str, Any], now: datetime | None = None) -> str:
    """The request's own status, from its signers and its deadline.

    ``completed`` when every signer signed. ``expired`` when the deadline passed
    and at least one signer did not. ``pending`` otherwise. The order matters:
    an agreement whose last signer signed an hour before the deadline is
    completed, not expired, and a product that got that backwards would tell a
    buyer their signature was void.
    """
    if is_complete(data):
        return vocab.REQUEST_STATUS_COMPLETED
    if is_expired(data, now):
        return vocab.REQUEST_STATUS_EXPIRED
    return vocab.REQUEST_STATUS_PENDING


def is_terminal(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """ "Once a signature request has expired, it is considered to be in a final
    status like ``declined`` and ``completed`` signature requests."

    Terminal means the mutation path is shut. It does not mean the record is
    gone: "All parties to the signature request will still have access to the
    document including audit trail."
    """
    return request_status(data, now) in (
        vocab.REQUEST_STATUS_EXPIRED,
        vocab.REQUEST_STATUS_COMPLETED,
    )


def audit_note(data: Mapping[str, Any], now: datetime | None = None) -> str:
    """The audit sentence the specification asks for at expiry.

    "Audit writes an ``expired`` audit event 'with the expiration date listed along
    with all the signers who did not sign by the expiration date.'"

    Both halves are in it: the date, and the names of the people who did not sign
    by it. A log line that said only "expired" would leave the reader to join it
    against a record they may no longer be able to read.
    """
    deadline = expires_at_of(data)
    names = [
        str(signer.get("email") or f"signer {index + 1}")
        for index, signer in enumerate(swept_signers(data))
    ]
    when = from_epoch(deadline).isoformat(timespec="seconds") if deadline else "never"
    who = ", ".join(names) if names else "nobody"
    return f"expired at {when}; did not sign by that date: {who}"


# --------------------------------------------------------------------------- #
# Signer projection
# --------------------------------------------------------------------------- #


def normalise_signer(signer: Any) -> dict[str, Any] | None:
    """One signer row, as stored.

    A signer needs an email and nothing else. Every other field is optional and
    carried through, because the store is schema-flexible and a team adding a
    field must need no coordination with anyone.
    """
    if not isinstance(signer, Mapping):
        return None
    email = signer.get("email")
    if not email or not str(email).strip():
        return None
    entry = dict(signer)
    entry["email"] = str(email).strip()
    entry["status_code"] = str(entry.get("status_code") or "awaiting_signature")
    entry["name"] = entry.get("name") or entry["email"]
    tz_name = entry.get("preferred_timezone") or entry.get("timezone")
    entry["preferred_timezone"] = str(tz_name) if tz_name else None
    return entry


def normalise_signers(signers: Any) -> list[dict[str, Any]]:
    """Every signer on a request, in order, skipping the ones with no address.

    A signer with no email cannot be reminded and cannot be swept, so it is not a
    signer. Dropping it here means no later stage has to remember to check.
    """
    if not isinstance(signers, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for signer in signers:
        entry = normalise_signer(signer)
        if entry is not None:
            rows.append(entry)
    return rows
