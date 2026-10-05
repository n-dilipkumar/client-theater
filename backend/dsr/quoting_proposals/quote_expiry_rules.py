"""WF-098: the expiry and reminder rules, as pure functions over values.

Every rule here is a statement about a date, a count or a string. None of them reads
or writes anything. The engine beside this module turns them into records. This module
is what a test can call with two integers and a clock and check.

The rules the rest of the workflow leans on
-------------------------------------------

**The default window is bounded at 1 and 365 days.** "Set a default expiration period
for quotes... enter a default expiration time period between 1 and 365 days." The
bound is checked on the settings route, which is where the research says the number
is entered, and a value outside it is refused before a row is written.

**A quote's own expiration date wins, and a switched-off expiration never expires.**
Three separate controls sit in the header module: "click the **date picker** to set a
specific date, edit the **Label**, or toggle the **Expiration date** switch off." So
:func:`resolve_expiration` reads three fields and answers four ways, and the
``expiration_enabled`` switch is asked before anything else because a switch a seller
turned off must never be overridden by a stale stored date.

**The default applies only to quotes created after it was set.** "Any new quotes
created after the setting is turned on will automatically use the configured expiration
date." :func:`default_applies` is that sentence, and it is what keeps a default window
from rewriting the history of quotes that already exist.

**A past effective date is legal and changes the meaning of the field.** "because a
past *Effective date* is allowed, the **Expiration date** will be treated as the buyer's
sign-by deadline." Nothing here rejects such a quote. The deadline is computed and the
sign-by reading is reported beside it, because a deadline that reads as an expiry and
behaves as a deadline is the defect this rule prevents.

**Acceptance, signature and a marked signature save a quote. Countersignature and
payment do not decide the question.** "If a quote is accepted or signed before the
expiration date, but hasn't been countersigned or paid, the quote won't expire." So
:func:`survives` looks at exactly three actions and at the instant each happened. A
quote countersigned on time and never accepted is still expiring, and that is the
sentence above rather than a reading of it.

**Expiry is a state, not a deletion, and not a link death.** Three separate sentences
describe three separate outcomes: expired quotes "can still be downloaded, cloned,
voided or archived"; Void "will deactivate" the link URL; Archive "unpublishes" and
"prevents buyers from accessing" the quote. :func:`void_effects` and
:func:`archive_effects` are those three outcomes as distinct field sets. Collapsing
them into one ``inactive`` flag would lose which of them a caller asked for.

**Sending is a count.** "resending counts as a new send (consuming e-signature quota
again)". :func:`next_send` is the arithmetic, and a boolean ``sent`` cannot express it.

What this module does not decide
--------------------------------

Whether a reminder email actually left this product. It computes when one is due and
records the decision; delivery is the integrator's. And whether a quote is
commercially acceptable to send at all, which is not this workflow's question.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.quoting_proposals import quote_expiry_vocabulary as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Declared here and raised by nothing else in the product. That is what makes it safe
# for the feature module to map them: a handler for a shared type such as ``ValueError``
# would intercept that exception across the whole application.


class QuoteExpiryRefusal(ValueError):
    """An expiration date, offset, send time or acceptance the workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input that
    caused it rather than in one combined sentence.
    """

    def __init__(self, code: str, field: str | None = None, detail: str | None = None) -> None:
        status, sentence = vocab.ERROR_CODES.get(
            code, (422, "This request is not one this workflow accepts.")
        )
        message = detail or sentence
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = message
        self.errors: dict[str, str] = {field: message} if field else {"detail": message}


class QuoteNotFound(LookupError):
    """No such tracked quote.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as every
    other workflow: a feature may only map error types it raises itself.
    """


class ReminderRuleNotFound(LookupError):
    """No such reminder rule on this account. Same reasoning as the class above."""


class QuoteNotEditable(QuoteExpiryRefusal):
    """A change asked of a quote whose expiration date is fixed.

    Its own subclass because the remediation differs from a refusal. The caller did not
    send a bad value: the quote is past the point where its expiration date can move,
    and the fix is a new send rather than an edit.
    """

    code = "quote_not_editable"

    def __init__(self, state: str, reason: str) -> None:
        super().__init__("quote_not_editable")
        self.state = state
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": str(self),
            "state": self.state,
            "reason": self.reason,
            "remediation": (
                "Send the quote again to get a new expiration window. The research "
                "counts a resend as a new send."
            ),
        }


# --------------------------------------------------------------------------- #
# The payload-side room reference
# --------------------------------------------------------------------------- #
#
# Not ``room_id``: that key is part of the record envelope and ``AuditedDatabase``
# strips it out of ``data`` before the dynamic index is built, so a payload that stored
# its room there would be unfilterable. This is the same key WF-081 and WF-081's
# sibling use, so one convention covers the package.

ROOM_REF = "room_ref"


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""
    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | str | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept.

    A stored string is accepted as well as a ``datetime``, because every instant this
    workflow records is written as a string and read back as one. Accepting both here
    means a caller holding a stored value never has to know which it has, and a caller
    that passed the wrong one gets a correct stamp rather than an ``AttributeError``.
    """

    if moment is None:
        value = utcnow()
    elif isinstance(moment, datetime):
        value = moment
    else:
        value = coerce_instant(moment, "moment")
        if value is None:
            raise QuoteExpiryRefusal("expiration_not_an_instant", "moment")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def coerce_instant(value: Any, field: str = "instant") -> datetime | None:
    """One stored instant as an aware UTC datetime, or ``None`` when there is none.

    ``None`` is a real answer rather than an error. A quote with no expiration date is a
    quote that does not expire, and a seller who turned the switch off has said so on
    purpose.

    Three encodings are accepted, and only the three this repository stores: an ISO 8601
    string, a bare date string such as ``2026-11-30``, and Unix milliseconds. A string
    of only digits is read as milliseconds, because a bare number is how a millisecond
    timestamp survives a JSON round trip through a client. A date with no time is read
    as midnight UTC, which is a stated choice rather than an accident: the research
    gives no time-of-day for the date picker, and midnight UTC is the only reading that
    is the same instant on every machine.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QuoteExpiryRefusal("expiration_not_an_instant", field)
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(value, (int, float)):
        return _from_millis(float(value), field)
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return _from_millis(float(text), field)
    candidate = text.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        candidate = f"{candidate}T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise QuoteExpiryRefusal("expiration_not_an_instant", field) from exc
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_millis(number: float, field: str) -> datetime:
    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise QuoteExpiryRefusal("expiration_not_an_instant", field) from exc


def iso_date(moment: datetime | None) -> str | None:
    """The instant as a plain ``YYYY-MM-DD``, which is what a date picker holds."""

    if moment is None:
        return None
    return moment.astimezone(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# The default window
# --------------------------------------------------------------------------- #


def coerce_default_days(value: Any) -> int | None:
    """The account's default expiration period in days, or ``None`` for no default.

    "enter a default expiration time period between 1 and 365 days". Both bounds are the
    research's and the check runs on the value the admin typed, before anything is
    written, so a rejected setting leaves no row and no audit row describing a change
    that did not happen.

    A boolean is refused rather than read as 1 or 0, because ``True`` is an ``int`` in
    Python and a switch turned on by mistake is not a ninety-day window.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QuoteExpiryRefusal("default_expiration_out_of_range", vocab.DEFAULT_EXPIRATION_DAYS)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.lstrip("+-").isdigit():
            raise QuoteExpiryRefusal(
                "default_expiration_out_of_range", vocab.DEFAULT_EXPIRATION_DAYS
            )
        value = int(text)
    if isinstance(value, float):
        if not value.is_integer():
            raise QuoteExpiryRefusal(
                "default_expiration_out_of_range", vocab.DEFAULT_EXPIRATION_DAYS
            )
        value = int(value)
    if not isinstance(value, int):
        raise QuoteExpiryRefusal("default_expiration_out_of_range", vocab.DEFAULT_EXPIRATION_DAYS)
    if not (vocab.MIN_DEFAULT_EXPIRATION_DAYS <= value <= vocab.MAX_DEFAULT_EXPIRATION_DAYS):
        raise QuoteExpiryRefusal("default_expiration_out_of_range", vocab.DEFAULT_EXPIRATION_DAYS)
    return value


def default_applies(created_at: Any, default_set_at: Any) -> bool:
    """Does the account default reach this quote?

    "Any new quotes created after the setting is turned on will automatically use the
    configured expiration date."

    The comparison is on the quote's own creation instant against the instant the
    default was turned on. A quote that already existed has a date a seller chose, and a
    default applied to it would rewrite a decision already made.
    """

    created = coerce_instant(created_at, "created_at")
    since = coerce_instant(default_set_at, "default_set_at")
    if created is None or since is None:
        return False
    return created >= since


# --------------------------------------------------------------------------- #
# The expiration date
# --------------------------------------------------------------------------- #


def resolve_expiration(
    data: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """The expiration date this quote carries, and where it came from.

    Four answers, and the caller needs all of them:

    * ``enabled: False`` - the seller turned the switch off. ``expires_at`` is ``None``
      and no default is substituted, because a switch a seller turned off is a decision
      and a default that overrode it would make the control a lie.
    * ``source: stated_on_quote`` - the picker or the label set a date. It wins.
    * ``source: account_default`` - no date was stated and the default window applies to
      this quote.
    * ``source: expiration_off`` - no date and no default. The quote never expires.

    The default is derived from the quote's effective date where it has one, because
    the research pairs the two: the default expiration period is applied to a quote when
    it is created, and the effective date is the quote's own start. With no effective
    date the send instant is used, and with neither the current instant is.
    """

    enabled = _expiration_enabled(data)
    stated = coerce_instant(data.get(vocab.EXPIRATION_DATE), vocab.EXPIRATION_DATE)
    window = coerce_default_days(data.get("default_expiration_days"))
    anchor = _anchor_instant(data, now)

    if not enabled:
        return {
            "enabled": False,
            "expires_at": None,
            "iso_date": None,
            "source": vocab.EXPIRY_SOURCE_NONE,
            "window_days": window,
            "anchor": stamp(anchor) if anchor else None,
            "sign_by_deadline": False,
            "label": data.get(vocab.EXPIRATION_LABEL) or "",
        }

    if stated is not None:
        return {
            "enabled": True,
            "expires_at": stamp(stated),
            "iso_date": iso_date(stated),
            "source": vocab.EXPIRY_SOURCE_QUOTED,
            "window_days": window,
            "anchor": stamp(anchor) if anchor else None,
            "sign_by_deadline": _is_sign_by_deadline(data, now),
            "label": data.get(vocab.EXPIRATION_LABEL) or "",
        }

    if window is None:
        return {
            "enabled": True,
            "expires_at": None,
            "iso_date": None,
            "source": vocab.EXPIRY_SOURCE_NONE,
            "window_days": None,
            "anchor": stamp(anchor) if anchor else None,
            "sign_by_deadline": False,
            "label": data.get(vocab.EXPIRATION_LABEL) or "",
        }

    expires = anchor + timedelta(days=window)
    return {
        "enabled": True,
        "expires_at": stamp(expires),
        "iso_date": iso_date(expires),
        "source": vocab.EXPIRY_SOURCE_ACCOUNT_DEFAULT,
        "window_days": window,
        "anchor": stamp(anchor) if anchor else None,
        "sign_by_deadline": _is_sign_by_deadline(data, now),
        "label": data.get(vocab.EXPIRATION_LABEL) or "",
    }


def coerce_enabled(value: Any) -> bool:
    """Is this switch on?

    ``False`` only when the value is present and off. A payload with no key at all is on,
    because every researched flow turns the switch off explicitly and a missing field is
    not a switch somebody flipped. ``True`` and ``1`` are on; the strings ``"false"``,
    ``"0"`` and ``"no"`` are off, because a form that submits a checkbox as a string is a
    form this product receives.
    """

    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def _expiration_enabled(data: Mapping[str, Any]) -> bool:
    """Is the Expiration date switch on for this quote?"""

    return coerce_enabled(data.get(vocab.EXPIRATION_ENABLED))


def normalise_recipients(raw: Any) -> list[dict[str, Any]]:
    """Every quote recipient, in order, skipping the rows with no address.

    A recipient with no email cannot be reminded and cannot be reported on, so it is not a
    recipient. Dropping it here means no later stage has to remember to check.
    """

    if not isinstance(raw, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        email = str(entry.get("email") or "").strip()
        if not email:
            continue
        row = dict(entry)
        row["email"] = email
        row["name"] = entry.get("name") or email
        rows.append(row)
    return rows


def _anchor_instant(data: Mapping[str, Any], now: datetime | None) -> datetime:
    """The instant a default window is measured from: effective date, then send, then now."""

    effective = coerce_instant(data.get(vocab.EFFECTIVE_DATE), vocab.EFFECTIVE_DATE)
    if effective is not None:
        return effective
    sent = coerce_instant(data.get(vocab.SENT_AT), vocab.SENT_AT)
    if sent is not None:
        return sent
    published = coerce_instant(data.get(vocab.PUBLISHED_AT), vocab.PUBLISHED_AT)
    if published is not None:
        return published
    return now or utcnow()


def _is_sign_by_deadline(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Has the effective date already passed, so the expiration date is a sign-by deadline?

    "because a past *Effective date* is allowed, the **Expiration date** will be treated
    as the buyer's sign-by deadline."

    The reading is reported so a reader knows why the deadline reads the way it does.
    Nothing is refused on this basis. The test is against the current instant and not
    against the deadline: a future effective date is a quote that has not started yet, and
    a quote whose deadline has not arrived is an expiry date rather than a sign-by
    deadline.
    """

    effective = coerce_instant(data.get(vocab.EFFECTIVE_DATE), vocab.EFFECTIVE_DATE)
    return effective is not None and effective < (now or utcnow())


def expires_at_of(data: Mapping[str, Any], now: datetime | None = None) -> datetime | None:
    """The deadline as a datetime, or ``None``. Never guesses a value for a missing key."""

    resolved = resolve_expiration(data, now)
    if not resolved["expires_at"]:
        return None
    return coerce_instant(resolved["expires_at"], vocab.EXPIRATION_DATE)


def has_expiration(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Did anybody give this quote a deadline?

    This is the whole of the switch-off rule. Every sweep and every reminder check asks
    it first, because a quote whose switch is off must never close.
    """

    return expires_at_of(data, now) is not None


def is_expired(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Has the deadline passed?

    ``False`` for a quote with no deadline, and that is the important half of this
    function. A missing field is not a deadline in the past; it is no deadline at all, and
    reading it as zero would close every quote in the product that nobody asked to close.
    """

    deadline = expires_at_of(data, now)
    if deadline is None:
        return False
    return (now or utcnow()) >= deadline


def days_remaining(data: Mapping[str, Any], now: datetime | None = None) -> float | None:
    """How long until the deadline, in days, rounded to two places.

    ``None`` for no deadline, which the page renders as "no expiration date" rather than
    as a zero. Negative once the deadline has passed, because the caller needs the number
    to render and a clamped zero would hide the fact that it is late.

    The sign is the other way round from :func:`expires_at_of` arithmetic on purpose:
    a positive number is time still available and a negative one is time already lost, and
    the page reads it that way without having to negate anything.
    """

    deadline = expires_at_of(data, now)
    if deadline is None:
        return None
    return round((deadline - (now or utcnow())).total_seconds() / 86400.0, 2)


def seconds_remaining(data: Mapping[str, Any], now: datetime | None = None) -> int | None:
    """How long until the deadline, in seconds. ``None`` for no deadline."""

    deadline = expires_at_of(data, now)
    if deadline is None:
        return None
    return int((deadline - (now or utcnow())).total_seconds())


def is_sign_by_deadline(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Does this quote's expiration date read as the buyer's sign-by deadline?"""

    return bool(resolve_expiration(data, now).get("sign_by_deadline"))


# --------------------------------------------------------------------------- #
# Acceptance and survival
# --------------------------------------------------------------------------- #


def coerce_acceptance_method(value: Any) -> str:
    """The acceptance method, from the research's own three names.

    Each of the three has its own "won't expire if" rule, so the method is stored rather
    than normalised away. An unknown method is refused rather than defaulted: a method
    this product does not know about is a method whose survival rule it cannot apply.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return vocab.METHOD_CLICK_TO_ACCEPT
    aliases = {
        "esign": vocab.METHOD_E_SIGNATURE,
        "e_signature": vocab.METHOD_E_SIGNATURE,
        "electronic_signature": vocab.METHOD_E_SIGNATURE,
        "click_to_accept": vocab.METHOD_CLICK_TO_ACCEPT,
        "clickaccept": vocab.METHOD_CLICK_TO_ACCEPT,
        "accept": vocab.METHOD_CLICK_TO_ACCEPT,
        "print_and_sign": vocab.METHOD_PRINT_AND_SIGN,
        "printandsign": vocab.METHOD_PRINT_AND_SIGN,
        "print_sign": vocab.METHOD_PRINT_AND_SIGN,
    }
    if text not in aliases:
        raise QuoteExpiryRefusal("unknown_acceptance_method", "acceptance_method")
    return aliases[text]


def coerce_buyer_action(value: Any) -> str:
    """What the buyer did, from the data flow's own three words.

    "if the buyer has not accepted/e-signed/marked-signed by the expiration date".
    Countersigned and paid are accepted as values because they are things a quote really
    reaches, and they are deliberately not on the survival list: "If a quote is accepted
    or signed before the expiration date, but hasn't been countersigned or paid, the
    quote won't expire." A caller recording ``countersigned`` has recorded something
    true and has not thereby saved the quote, and this module says so rather than
    quietly widening the list to make the refusal disappear.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "accept": vocab.ACTION_ACCEPTED,
        "accepted": vocab.ACTION_ACCEPTED,
        "e_sign": vocab.ACTION_E_SIGNED,
        "esigned": vocab.ACTION_E_SIGNED,
        "e_signed": vocab.ACTION_E_SIGNED,
        "signed": vocab.ACTION_E_SIGNED,
        "marked_signed": vocab.ACTION_MARKED_SIGNED,
        "marksigned": vocab.ACTION_MARKED_SIGNED,
        "countersigned": vocab.ACTION_COUNTERSIGNED,
        "counter_signed": vocab.ACTION_COUNTERSIGNED,
        "paid": vocab.ACTION_PAID,
        "payment": vocab.ACTION_PAID,
    }
    if text not in aliases:
        raise QuoteExpiryRefusal("unknown_buyer_action", "action")
    return aliases[text]


def survives_expiry(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Did the buyer act in time, so this quote outlives its expiration date?

    "If a quote is accepted or signed before the expiration date, but hasn't been
    countersigned or paid, the quote won't expire."

    The predicate is narrow on purpose. It looks for exactly the three surviving actions
    and it requires the action to have happened at or before the deadline. A quote
    countersigned on time and never accepted returns ``False``, which is the sentence
    above read literally, and a test asserts it.
    """

    moment = now or utcnow()
    deadline = expires_at_of(data, moment)
    entries = _acceptance_entries(data)
    if not entries:
        return False
    for entry in entries:
        if entry.get("action") not in vocab.SURVIVING_ACTIONS:
            continue
        acted = coerce_instant(entry.get("at"), "accepted_at")
        if acted is None:
            continue
        if deadline is None:
            return True
        if acted <= deadline:
            return True
    return False


def _acceptance_entries(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every acceptance the quote carries, from either spelling a caller may send."""

    raw = data.get("acceptances")
    if not isinstance(raw, (list, tuple)):
        raw = []
    entries = [dict(entry) for entry in raw if isinstance(entry, Mapping)]
    single = data.get("accepted_at") or data.get("signed_at")
    if single and not entries:
        entries.append(
            {
                "action": vocab.ACTION_SIGNED if data.get("signed_at") else vocab.ACTION_ACCEPTED,
                "at": single,
            }
        )
    return entries


def quote_state(
    data: Mapping[str, Any],
    now: datetime | None = None,
) -> str:
    """The quote's own state, from its flags and its deadline.

    The order is the order of the rules, and it matters:

    * ``voided`` first, because Void "will deactivate" the link URL whatever else is
      true of the quote.
    * ``archived`` second, because Archive "unpublishes" it and "prevents buyers from
      accessing" it.
    * ``accepted`` and ``signed`` next, because an acted-on quote never expires.
    * ``expired`` after those, so a buyer who accepted an hour before the deadline is
      accepted rather than expired.
    * ``draft`` when no send or publish has happened, because the research's job
      "evaluates sent/published quotes".
    """

    moment = now or utcnow()
    if data.get("voided_at"):
        return vocab.QUOTE_VOIDED
    if data.get("archived_at"):
        return vocab.QUOTE_ARCHIVED

    acted = {entry.get("action") for entry in _acceptance_entries(data)}
    if vocab.ACTION_ACCEPTED in acted:
        return vocab.QUOTE_ACCEPTED
    if acted & {vocab.ACTION_E_SIGNED, vocab.ACTION_MARKED_SIGNED}:
        return vocab.QUOTE_SIGNED
    if is_expired(data, moment):
        return vocab.QUOTE_EXPIRED
    if data.get(vocab.PUBLISHED_AT):
        return vocab.QUOTE_PUBLISHED
    if data.get(vocab.SENT_AT):
        return vocab.QUOTE_SENT
    return vocab.QUOTE_DRAFT


def is_terminal(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Is this quote no longer waiting on the buyer?

    Terminal means the acceptance path is shut. It does not mean the quote is gone: "An
    expired quote can still be downloaded, cloned, voided or archived."
    """

    return quote_state(data, now) in vocab.TERMINAL_QUOTE_STATES


def expiry_skip_reason(data: Mapping[str, Any], now: datetime | None = None) -> str:
    """Why the expiry check will leave this quote alone, or ``""`` to expire it.

    The order is the order of the rules. No deadline first, because a quote whose switch
    is off must never close. Then an already-closed quote, because the check has to be safe
    to run twice: an hour of downtime is a job nobody ran, and a second pass would write a
    second ``Quote expired`` activity for one deadline.

    "Already closed" is read from the stored flags and never from the derived state, and
    that distinction is the whole reason this function is not
    :func:`~dsr.quoting_proposals.quote_expiry_rules.is_terminal`. A quote past its
    deadline derives ``expired`` on every read, so asking the derived state here would make
    every due quote look already-closed and nothing would ever expire.

    Then the deadline itself. Then survival, because "a quote accepted or signed before the
    expiration date... won't expire" is the last word.
    """

    moment = now or utcnow()
    if not has_expiration(data, moment):
        return vocab.SKIP_EXPIRATION_OFF if not _expiration_enabled(data) else "no_expiration_date"
    if _already_closed(data):
        return "already_terminal"
    if not is_expired(data, moment):
        return "deadline_not_passed"
    if survives_expiry(data, moment):
        return "buyer_already_acted"
    return ""


def _already_closed(data: Mapping[str, Any]) -> bool:
    """Did a stored flag already close this quote?

    Three flags and nothing derived. The check that runs this writes ``expired_at``, and the
    routes that void or archive write their own, so a second pass finds one of them and
    leaves the quote alone.
    """

    return bool(data.get("expired_at") or data.get("voided_at") or data.get("archived_at"))


def audit_note(data: Mapping[str, Any], now: datetime | None = None) -> str:
    """The activity sentence the data flow asks for at expiry.

    "the quote transitions to an Expired state (logged as ``Quote expired``)".

    Both halves are here: the activity name the research quotes verbatim, and the
    deadline it passed. A log line that said only "expired" would leave the reader to join
    it against a record they may no longer be able to read.
    """

    deadline = expires_at_of(data, now)
    when = iso_date(deadline) or "no date"
    return f"{vocab.QUOTE_EXPIRED_ACTIVITY} at the buyer's sign-by deadline of {when}"


# --------------------------------------------------------------------------- #
# Void and archive
# --------------------------------------------------------------------------- #


def void_effects() -> dict[str, Any]:
    """What Void changes, as a field set.

    "**Void:** ... The quote link URL will be deactivated." One consequence and one only.
    It is separate from archive because the two sentences describe different outcomes and
    a quote can be voided without being archived.
    """

    return {
        vocab.LINK_ACTIVE: False,
        "voided_at": True,
        "state": vocab.QUOTE_VOIDED,
        "quote": vocab.VOID_QUOTE,
    }


def archive_effects() -> dict[str, Any]:
    """What Archive changes, as a field set.

    "**Archive:** ... The quote is unpublished, hidden from the default index page view,
    and prevents buyers from accessing it." Three consequences and three fields, because
    they are separately observable: a client can list the quote, fetch it by id, and
    open its link, and archive has to answer differently to each.
    """

    return {
        vocab.PUBLISHED: False,
        vocab.HIDDEN_FROM_INDEX: True,
        vocab.BUYER_ACCESS: False,
        "archived_at": True,
        "state": vocab.QUOTE_ARCHIVED,
        "quote": vocab.ARCHIVE_QUOTE,
    }


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #


def next_send(data: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """The send a caller is about to make: the new count and the new quota consumption.

    "resending counts as a new send (consuming e-signature quota again)".

    A count and not a boolean, because the sentence is about consumption: the second send
    costs exactly what the first cost. The anchor the reminder offsets count from is the
    new send instant, so a resend restarts the "days after sending quote" schedule. The
    research does not say the offsets restart, and it does not say they do not; restarting
    is the reading that matches "a new send" and it is recorded as an inference.
    """

    moment = now or utcnow()
    previous = _counter(data.get(vocab.SEND_COUNT))
    consumed = _counter(data.get(vocab.ESIGNATURE_QUOTA_CONSUMED))
    published = data.get(vocab.PUBLISHED_AT)
    return {
        vocab.SEND_COUNT: previous + 1,
        vocab.ESIGNATURE_QUOTA_CONSUMED: consumed + 1,
        vocab.SENT_AT: stamp(moment),
        "resent": previous > 0,
        "previous_send_count": previous,
        "published": bool(published),
        "quote": vocab.RESEND_QUOTE,
    }


def _counter(value: Any) -> int:
    """A stored counter, read defensively.

    ``0`` for anything that is not a number, and for a boolean: ``True`` is an ``int`` in
    Python and a flag is not a count of sends. A counter that reads as absent must not stop
    a send, and the row it came from is not this module's to repair.
    """

    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# The reminder schedule
# --------------------------------------------------------------------------- #


def coerce_offset_kind(value: Any) -> str:
    """Which of the two offsets a rule uses.

    "set the number of days and choose **Days after sending quote** or **Days before
    expiration date**". The two are stored as separate kinds and never converted into one
    another: an offset from a send and an offset from an expiry are different questions
    and a schedule that silently translated between them would send a reminder on a day
    the seller never chose.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "after_send": vocab.OFFSET_AFTER_SEND,
        "days_after_sending_quote": vocab.OFFSET_AFTER_SEND,
        "after_sending": vocab.OFFSET_AFTER_SEND,
        "before_expiry": vocab.OFFSET_BEFORE_EXPIRY,
        "days_before_expiration_date": vocab.OFFSET_BEFORE_EXPIRY,
        "before_expiration": vocab.OFFSET_BEFORE_EXPIRY,
    }
    if text not in aliases:
        raise QuoteExpiryRefusal("unknown_offset_kind", "offset_kind")
    return aliases[text]


def coerce_offset_days(value: Any) -> int:
    """A rule's own number of days.

    The research puts no bound on this figure, so none is imposed: "set the number of
    days" is the whole sentence. Zero is legal and means the same day.
    """

    if isinstance(value, bool):
        raise QuoteExpiryRefusal("offset_days_not_an_integer", "days")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise QuoteExpiryRefusal("offset_days_not_an_integer", "days")
        try:
            value = int(text)
        except ValueError as exc:
            raise QuoteExpiryRefusal("offset_days_not_an_integer", "days") from exc
    if isinstance(value, float):
        if not value.is_integer():
            raise QuoteExpiryRefusal("offset_days_not_an_integer", "days")
        value = int(value)
    if not isinstance(value, int):
        raise QuoteExpiryRefusal("offset_days_not_an_integer", "days")
    if value < vocab.MIN_OFFSET_DAYS:
        raise QuoteExpiryRefusal("offset_days_not_an_integer", "days")
    return value


def coerce_send_time(value: Any) -> str:
    """The account's reminder send time as ``HH:MM``.

    "set *Reminder send time* (in the account time zone)". A wall-clock reading and not an
    instant, because the research says a time in the account time zone. Storing an
    instant instead would freeze the reading at whatever UTC offset happened to be in
    force when it was set, which is the bug this type makes impossible to write.
    """

    if value is None or value == "":
        return "09:00"
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", text):
        raise QuoteExpiryRefusal("send_time_not_a_time", vocab.REMINDER_SEND_TIME)
    hours, minutes = text.split(":")
    if int(hours) > 23 or int(minutes) > 59:
        raise QuoteExpiryRefusal("send_time_not_a_time", vocab.REMINDER_SEND_TIME)
    return f"{int(hours):02d}:{int(minutes):02d}"


# -- time zones ---------------------------------------------------------------- #


def zone_for(tz_name: Any) -> Any:
    """The named zone, or UTC for a name this build cannot resolve.

    A fixed offset such as ``+05:30`` is resolved from the string itself and is always
    honoured, because a mobile client sends that far more often than an IANA name and the
    offset it sends is one it computed for this moment.

    An IANA name needs the timezone database, which is a package that may not be
    installed. Where it is missing every named zone resolves to UTC and the response says
    so, rather than this module carrying a table of offsets that would be silently wrong
    for half the year.
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


def zone_resolved(zone: Any, tz_name: Any) -> bool:
    """Was the caller's zone actually honoured, or did we fall back to UTC?

    ``UTC`` counts as resolved, because it is the zone the fallback produces and a reader
    who asked for UTC got exactly UTC. Reporting that as unknown would put a note on every
    correct answer and train people to ignore the note, which is how a real fallback gets
    missed.
    """

    if not tz_name:
        return False
    text = str(tz_name).strip()
    if text.startswith(("+", "-")):
        return True
    if text.upper() in ("UTC", "Z", "GMT", "UTC+0", "UTC+00:00"):
        return True
    return bool(getattr(zone, "key", None))


def local_reading(instant: datetime, tz_name: Any) -> dict[str, Any]:
    """One instant as the account reads it, and whether that reading is trustworthy.

    "set *Reminder send time* (in the account time zone)" and "A reminder scheduled at
    09:00 local is not 09:00 UTC."

    Only the reading changes. The instant is never adjusted, because a deadline that moved
    with a timezone is a deadline two people disagree about.
    """

    zone = zone_for(tz_name)
    local = instant.astimezone(zone)
    resolved = zone_resolved(zone, tz_name)
    return {
        "epoch_seconds": int(instant.timestamp()),
        "iso_utc": instant.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "timezone": str(getattr(zone, "key", None) or tz_name or "UTC"),
        "local": local.isoformat(timespec="minutes"),
        "local_date": local.date().isoformat(),
        "offset_minutes": int((local.utcoffset() or timedelta(0)).total_seconds() // 60),
        "known": resolved,
        "note": None if resolved else vocab.NOTE_UNRESOLVED_TIMEZONE,
    }


# -- when a rule is due --------------------------------------------------------- #


def rule_due_at(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The instant this rule fires for this quote, and whether it can fire at all.

    Both offsets are anchored differently and neither is a day count from now:

    * ``after_send`` counts from the send or publish instant, because the research says
      "Days after sending quote" and the data flow counts from the send or publish
      timestamp.
    * ``before_expiry`` counts back from the expiration date, because the research says
      "Days before expiration date".

    The hour of day is the account's reminder send time read in the account's time zone.
    That is the whole of the timezone rule and it is applied here rather than at the
    dispatch site, so a test can check the instant without dispatching anything.

    ``anchor`` is ``None`` when the rule has nothing to count from or back from, and
    ``reason`` says which. A rule with no anchor is skipped with a named reason rather
    than fired at a guessed day.
    """

    moment = now or utcnow()
    config = dict(settings or {})
    tz_name = config.get(vocab.ACCOUNT_TIMEZONE)
    send_time = coerce_send_time(config.get(vocab.REMINDER_SEND_TIME))
    days = coerce_offset_days(rule.get("days"))
    kind = coerce_offset_kind(rule.get("offset_kind"))

    if kind == vocab.OFFSET_AFTER_SEND:
        anchor = coerce_instant(
            quote.get(vocab.SENT_AT) or quote.get(vocab.PUBLISHED_AT), vocab.SENT_AT
        )
        if anchor is None:
            return _not_due(vocab.SKIP_NO_SEND_ANCHOR)
        base = anchor + timedelta(days=days)
    else:
        anchor = expires_at_of(quote, moment)
        if anchor is None:
            return _not_due(
                vocab.SKIP_NO_EXPIRATION_DATE
                if _expiration_enabled(quote)
                else vocab.SKIP_EXPIRATION_OFF
            )
        base = anchor - timedelta(days=days)

    due = _at_local_time(base, send_time, tz_name)
    return {
        "due_at": stamp(due),
        "due_epoch": int(due.timestamp()),
        "offset_kind": kind,
        "offset_days": days,
        "send_time": send_time,
        "account_timezone": str(tz_name or "UTC"),
        "anchor": stamp(anchor),
        "local_reading": local_reading(due, tz_name),
        "reason": None,
        "cancellable": False,
    }


def _not_due(reason: str) -> dict[str, Any]:
    """The answer for a rule that has no anchor, with the reason named."""

    return {
        "due_at": None,
        "due_epoch": None,
        "offset_kind": None,
        "offset_days": None,
        "send_time": None,
        "account_timezone": None,
        "anchor": None,
        "local_reading": None,
        "reason": reason,
        "cancellable": True,
    }


def _at_local_time(day: datetime, send_time: str, tz_name: Any) -> datetime:
    """The instant ``send_time`` reads on ``day`` in the account's zone.

    The local date is taken first and the wall-clock time is then placed on it. That order
    is what makes the rule correct across a zone change: taking the UTC instant and adding
    an offset would put a 09:00 reminder at 08:00 or 10:00 local for half the year.
    """

    zone = zone_for(tz_name)
    local_day = day.astimezone(zone).date()
    hours, minutes = (int(part) for part in send_time.split(":"))
    local = datetime(local_day.year, local_day.month, local_day.day, hours, minutes, tzinfo=zone)
    return local.astimezone(timezone.utc)


def rule_is_due(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Has this rule's instant arrived, and is it still ahead of the deadline it serves?"""

    moment = now or utcnow()
    plan = rule_due_at(rule, quote, settings, moment)
    if plan["reason"]:
        return False
    return moment >= coerce_instant(plan["due_at"], "due_at")  # type: ignore[arg-type]


def skip_reason_for(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    ledger: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> str:
    """Why this rule will not be dispatched right now, or ``""`` to dispatch it.

    The checks run in the order the rules are stated in, and each one is a researched
    sentence rather than a policy this module invented:

    * the switch off - a quote nobody asked to close;
    * no deadline - a "days before expiration" rule has no day to count back from;
    * the buyer acted - "a quote accepted or signed before the expiration date... won't
      expire", and a reminder for such a quote would be a message asking for something
      already done;
    * already closed - the stored expiry, void or archive flag;
    * automated reminders off - the researched toggle;
    * this rule already fired for this quote - each rule is an account's deliberate single
      nudge, and a rule that fired twice would send the same reminder twice.

    Closed is read from the stored flags rather than from the derived state, for the same
    reason the expiry check reads it that way: a quote past its deadline derives ``expired``
    on every read, and asking the derived state would make every due quote look closed.

    The "not yet due" case is not here, because a rule that is simply early is not a
    decision the caller has to be told about: :func:`rule_is_due` answers that.
    """

    moment = now or utcnow()
    if not _expiration_enabled(quote):
        return vocab.SKIP_EXPIRATION_OFF
    plan = rule_due_at(rule, quote, settings, moment)
    if plan["reason"]:
        return str(plan["reason"])
    state = quote_state(quote, moment)
    # The buyer's own action is asked before the closed flags, because an accepted quote is
    # also closed and "they already said yes" is the reason a seller reading the ledger
    # needs. Both stop the reminder. Only one of them explains it.
    if state in (vocab.QUOTE_ACCEPTED, vocab.QUOTE_SIGNED) or survives_expiry(quote, moment):
        return vocab.SKIP_QUOTE_ACCEPTED
    if _already_closed(quote):
        return vocab.SKIP_QUOTE_CLOSED
    if not config_enabled(settings):
        return vocab.SKIP_AUTOMATED_DISABLED
    if rule_already_sent(rule, quote, ledger):
        return vocab.SKIP_RULE_ALREADY_SENT
    return ""


def config_enabled(settings: Mapping[str, Any] | None) -> bool:
    """Is "Send automated reminders to quote recipients" on?

    The toggle is on unless it is present and off, so an account with no settings row
    still gets its reminders rather than silently losing them.
    """

    value = (settings or {}).get(vocab.AUTOMATED_REMINDERS_ENABLED)
    if value is None:
        return True
    return bool(value)


def rule_already_sent(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    ledger: Sequence[Mapping[str, Any]],
) -> bool:
    """Has this exact rule already sent for this quote?

    Keyed on the rule's id rather than on its offset, so two rules with the same number of
    days are still two reminders and deleting one does not silence the other.
    """

    rule_id = rule.get("id")
    quote_id = quote.get("id")
    if not rule_id or not quote_id:
        return False
    for row in ledger:
        if row.get("outcome") != vocab.OUTCOME_SENT:
            continue
        if str(row.get("rule_id")) != str(rule_id):
            continue
        if str(row.get("quote_id")) != str(quote_id):
            continue
        return True
    return False


def preview_reminder(
    rule: Mapping[str, Any],
    quote: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The reminder a rule would send for this quote, without sending it.

    "**Preview reminder email**" is one of the researched product surfaces, so this is a
    route rather than a comment.

    Nothing is invented here. The subject and the body are composed from the values the
    store holds and every one of them is listed in ``fields`` so a reader can see which
    part of the text came from where. A reminder body this product cannot send is not
    fabricated: the preview names the channel and says the schedule has no documented
    public write API, which is the gap the research records.
    """

    moment = now or utcnow()
    plan = rule_due_at(rule, quote, settings, moment)
    title = str(quote.get("title") or "your quote")
    days = coerce_offset_days(rule.get("days")) if rule.get("days") is not None else 0
    kind = str(plan["offset_kind"] or vocab.OFFSET_AFTER_SEND)
    subject = f"Reminder: {title}"
    body = (
        f"{title} was sent to you and is waiting for your decision."
        if kind == vocab.OFFSET_AFTER_SEND
        else f"{title} expires soon and is waiting for your decision."
    )
    return {
        "rule_id": rule.get("id"),
        "quote_id": quote.get("id"),
        "subject": subject,
        "body": body,
        "channel": "email",
        "offset_kind": kind,
        "offset_days": days,
        "due_at": plan["due_at"],
        "local_reading": plan["local_reading"],
        "fields": {
            "title": quote.get("title"),
            "expiration_date": resolve_expiration(quote, moment)["iso_date"],
            "expiration_label": quote.get(vocab.EXPIRATION_LABEL) or "",
            "sent_at": quote.get(vocab.SENT_AT),
            "days": days,
            "offset_kind": kind,
        },
        "gap": (
            "The reminder schedule has no documented public write API on the pages read. "
            "This product's settings route is the store."
        ),
    }


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #


def normalise_rule(rule: Any) -> dict[str, Any] | None:
    """One reminder rule, as stored.

    A rule needs an offset kind and a number of days. Everything else is optional and
    carried through, because the store is schema-flexible and a team adding a field must
    need no coordination with anyone.
    """

    if not isinstance(rule, Mapping):
        return None
    if not rule.get("offset_kind"):
        return None
    entry = dict(rule)
    entry["offset_kind"] = coerce_offset_kind(rule.get("offset_kind"))
    entry["days"] = coerce_offset_days(rule.get("days"))
    entry["label"] = (
        str(rule.get("label") or "")
        or f"{entry['days']} {vocab.OFFSET_LABELS[entry['offset_kind']]}"
    )
    entry["enabled"] = bool(rule.get("enabled", True))
    return entry


def normalise_rules(rules: Any) -> list[dict[str, Any]]:
    """Every rule on an account, in order, skipping the ones with no offset kind.

    A row this workflow cannot interpret is dropped here rather than raising later, so one
    malformed rule cannot take the whole schedule down.
    """

    if not isinstance(rules, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for rule in rules:
        entry = normalise_rule(rule)
        if entry is not None:
            rows.append(entry)
    return rows


def reminders_due(
    rules: Sequence[Mapping[str, Any]],
    quote: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Which rules are due for this quote right now, with each rule's plan attached.

    The page renders its button from this list, so the button can never disagree with the
    rule that would run when it is pressed.
    """

    due: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        plan = rule_due_at(rule, quote, settings, now)
        if plan["reason"]:
            continue
        if rule_is_due(rule, quote, settings, now):
            due.append(dict(plan, rule_id=rule.get("id"), label=rule.get("label")))
    return due
