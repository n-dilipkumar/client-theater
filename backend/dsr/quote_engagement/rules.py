"""WF-097 rules: the pure decisions, with no store and no framework.

Every function here is total and takes plain values. Nothing in this module
reads or writes a record, which is what makes each rule testable on its own and
is why it imports no storage at all: the engine in :mod:`.engagement` is the
only place in the package that touches the store.

Three decisions in here were put to Jev rather than guessed, and each records
its audit id beside the rule it governs. Two of them are the reason a reader
can check this module against the research rather than take its word for it:

* the equality case at the threshold (audit ``jev-20261013T111832-11972-12671``);
* the third counter with no email channel (audit ``jev-20261013T111835-5236-15033``);
* and the absence of a default threshold (audit ``jev-20261013T111655-4412-15602``).

Source: ``docs/research/digital-sales-room-workflows/wf/WF-097.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.quote_engagement.vocabulary import (
    ACCEPTED_STATUSES,
    ACTIVITY_CLASSES,
    ACTIVITY_COUNT,
    ACTIVITY_HIGH,
    ACTIVITY_LOW,
    ACTIVITY_SINCE_COMMUNICATION,
    ACTIVITY_UNCLASSIFIED,
    ALL_EVENTS,
    BUYER,
    EMAIL_CHANNEL_CONNECTED,
    EVENT_SIDES,
    EXPIRATION_FIELDS,
    HIGH_ACTIVITY_THRESHOLD,
    KNOWN_SURFACES,
    LAST_ACTIVITY_AT,
    LAST_COMMUNICATION_AT,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    NOT_CHASABLE_STATUSES,
    OWNER_FIELDS,
    REASON_EXPIRED_UNACCEPTED,
    REASON_EXPIRING_SOON,
    REASON_NO_ACTIVITY,
    REASON_NO_REPLY,
    SELLER,
    SIGNING_STATUS_FIELDS,
    SINCE_COMMUNICATION_NO_BASELINE,
    SINCE_COMMUNICATION_UNAVAILABLE,
    SURFACE_EMAIL_PDF,
    VIEW_COUNT,
    VIEW_EVENT,
)


class QuoteEngagementRuleError(ValueError):
    """A rule refused the request. The HTTP layer maps this to 422.

    It is deliberately a different type from the ``QuoteRuleError`` the publish
    workflow exports. Two features may not map one error type to a handler, so
    sharing the class would make this feature's 422 depend on which module the
    host loaded first.
    """


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def parse_timestamp(raw: Any) -> datetime | None:
    """Read a timestamp as an aware UTC datetime, or ``None``.

    A naive timestamp is read as UTC rather than refused. The store writes
    ``datetime.now(timezone.utc).isoformat()``, which carries an offset, and a
    team adding a field by hand is far likelier to write ``2026-10-01`` than to
    construct a timezone. Reading it as UTC costs nothing when the offset is
    right and saves a whole class of "cannot compare naive and aware datetime"
    failure when it is absent.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        moment = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(moment: datetime | None) -> str | None:
    """Render a datetime the way the store renders one."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


def normalise_event(raw: Any) -> str:
    """Read an event label as one of :data:`ALL_EVENTS`.

    The researched labels are prose with capitals and spaces, and a client that
    sends ``quote_viewed`` means the same event. The comparison folds case and
    treats an underscore, a hyphen and a space as the same separator, so a caller
    is not refused for typing a slug.

    A label this workflow does not know is refused rather than stored: an activity
    log that accepts any string stops being the list the research enumerated, and
    every reader of it has to handle an unknown row.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        raise QuoteEngagementRuleError("an activity record needs an event label")
    folded = _fold(text)
    for event in ALL_EVENTS:
        if _fold(event) == folded:
            return event
    raise QuoteEngagementRuleError(
        f"{text!r} is not a quote activity event; expected one of: {', '.join(ALL_EVENTS)}"
    )


def _fold(text: str) -> str:
    """Fold a label to its comparable form: lower case, one space per separator.

    An underscore, a hyphen, a slash and a space are the same separator as far as
    an event label is concerned. That is what lets ``quote_viewed`` and ``Quote
    viewed`` reach the same event instead of making one of them a row no rule has
    checked.
    """
    lowered = text.casefold()
    for separator in ("_", "-", "/"):
        lowered = lowered.replace(separator, " ")
    return " ".join(lowered.split())


def event_side(event: Any) -> str:
    """Which side an event belongs to: ``buyer`` or ``seller``.

    The research lists ``Chat started`` inside the seller list and defines it as
    "buyer started a chat with the closing agent". The definition is the more
    specific statement, so that is the side this returns, and the lookup table in
    the vocabulary is where the exception is written down.
    """
    label = normalise_event(event)
    return EVENT_SIDES.get(label, SELLER)


def normalise_surface(raw: Any, event: Any = None) -> str:
    """Read the surface an event arrived on, defaulting it from the event.

    A surface this workflow has never heard of is refused rather than treated as
    a view, because :func:`counts_as_view` is the only thing that decides the
    View Count and an unrecognised surface must not slip past it.
    """
    text = " ".join(str(raw or "").split()).casefold().replace("-", "_").replace(" ", "_")
    if not text:
        return default_surface(event)
    if text not in KNOWN_SURFACES:
        allowed = ", ".join(KNOWN_SURFACES)
        raise QuoteEngagementRuleError(
            f"{text!r} is not a known activity surface; expected one of: {allowed}"
        )
    return text


def default_surface(event: Any) -> str:
    """The surface an event arrives on when a caller names none.

    A view defaults to the quote link, which is the only surface the research
    says produces a view, and every other event defaults to the seller console,
    which is where a seller and an approver record their own work.
    """
    label = normalise_event(event)
    return "quote_link" if label == VIEW_EVENT else "seller_console"


def counts_as_view(event: Any, surface: Any = None) -> bool:
    """Did this event increment the View Count?

    "**Quote viewed:** the buyer has opened the quote using the quote link. If a
    buyer opens the PDF attached to the email, this doesn't count as a quote
    view."

    Two conditions, both from that sentence: the event must be the viewed event,
    and it must not have arrived on the attached PDF. The PDF case is the whole
    reason this function exists. The event is still recorded and still on the
    timeline; only the counter differs, which is what lets a seller see that the
    buyer opened something while the count stays honest about what that was.
    """
    try:
        label = normalise_event(event)
    except QuoteEngagementRuleError:
        return False
    if label != VIEW_EVENT:
        return False
    where = normalise_surface(surface, label) if surface else default_surface(label)
    return where != SURFACE_EMAIL_PDF


# --------------------------------------------------------------------------- #
# The threshold
# --------------------------------------------------------------------------- #


def require_threshold(raw: Any) -> int | None:
    """Read a configured high-activity threshold, or ``None`` when unset.

    ``None`` is the answer for a workspace that has configured none, and it is a
    real answer rather than a missing value: the research names the setting and
    gives no number, so this workflow ships none. A workspace that never set a
    threshold gets :data:`ACTIVITY_UNCLASSIFIED` rather than a classification
    nobody chose.

    A threshold that is present must be a whole number inside
    ``[1, 1000]``. Zero is refused because it would classify every quote High and
    make the chart say nothing, and the ceiling is refused because no quote could
    reach it, which would classify every quote Low.

    A fractional value is refused rather than rounded. An Activity Count is a
    number of events, so 3.5 activities is not a fact about any quote, and
    silently rounding it would store a threshold the admin never typed.
    """
    return _bounded_int(
        raw,
        name=HIGH_ACTIVITY_THRESHOLD,
        minimum=MIN_THRESHOLD,
        maximum=MAX_THRESHOLD,
        unit="",
    )


def require_window(raw: Any, name: str) -> int | None:
    """Read a configured day window, or ``None`` when the workspace set none.

    This is the threshold rule's shape applied to the two follow-up windows. The
    research says "no activity in a long time" and "quotes that'll expire soon"
    and names no number for either, so a workspace that has not set one is given
    ``None`` and the reason it could not be evaluated, rather than a window this
    workflow invented.

    A window is refused outside ``[1, 3650]``, which is the same shape of refusal
    as the threshold: a window of zero would recommend every quote, and a window
    of ten thousand years would recommend none, so both are a workspace that made
    a mistake rather than one that made a choice.
    """
    return _bounded_int(raw, name=name, minimum=1, maximum=3650, unit=" days")


def _bounded_int(raw: Any, *, name: str, minimum: int, maximum: int, unit: str) -> int | None:
    """Read one setting that must be a whole number inside a range.

    Both numeric settings go through here, so the bounds, the refusal for a
    boolean, the refusal for text and the refusal for a fraction are written once
    rather than twice with two slightly different messages. An absent value is
    ``None``, which every caller treats as "this workspace set nothing".
    """
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None
    if isinstance(raw, bool):
        raise QuoteEngagementRuleError(f"{name} must be a whole number")
    if isinstance(raw, float) and not raw.is_integer():
        raise QuoteEngagementRuleError(f"{name} must be a whole number, not {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise QuoteEngagementRuleError(f"{name} must be a whole number, not {raw!r}") from exc
    if value < minimum or value > maximum:
        raise QuoteEngagementRuleError(
            f"{name} must be between {minimum} and {maximum}{unit}, not {value}"
        )
    return value


def classify_activity(count: Any, threshold: Any) -> str:
    """Classify a quote's activity count against the configured threshold.

    "Published quotes with activity below this threshold are classified *Low
    activity*." So a count strictly below the threshold is Low activity, and a
    count at or above it is High activity.

    The equality case is the one the research leaves open: both of its sentences
    use the word "below" and neither names the point on the threshold. Reading
    "below" strictly, as its plain English meaning, makes equality High, and
    that is also what makes the setting usable: a super admin who types 10 has
    said that 10 activities is the engagement level, and a page that then called
    a quote with 10 activities Low would make the only way to see it green be to
    raise the setting to 9. The rejected alternative, classifying equality Low,
    is recorded here rather than dropped. Jev chose this at confidence 0.92
    (audit ``jev-20261013T111832-11972-12671``).

    With no threshold configured there is nothing to compare against, so the
    quote is :data:`ACTIVITY_UNCLASSIFIED` (audit
    ``jev-20261013T111655-4412-15602``).
    """
    limit = require_threshold(threshold)
    if limit is None:
        return ACTIVITY_UNCLASSIFIED
    try:
        total = int(count or 0)
    except (TypeError, ValueError):
        total = 0
    return ACTIVITY_LOW if total < limit else ACTIVITY_HIGH


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #


def event_moment(event: Mapping[str, Any]) -> datetime | None:
    """When an activity record happened, from either key it may use.

    ``occurred_at`` is what this workflow writes. ``created_at`` is what the
    store writes for every row, so a row written by a teammate through the
    generic records endpoint still has a usable time.
    """
    return parse_timestamp(event.get("occurred_at") or event.get("created_at"))


def summarise_events(
    events: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    threshold: Any = None,
    email_channel_connected: bool = False,
    last_communication_at: Any = None,
) -> dict[str, Any]:
    """Maintain the four researched counters from a quote's activity records.

    "counters (``Last Activity Date``, ``Activity Count``, ``Activity Count
    Since Last Communication``, ``View Count``) are maintained".

    The counters are recomputed from the whole activity log on every call rather
    than incremented. A counter that increments can disagree with the log it was
    incremented from, and nothing in this product reads the log to correct it; a
    counter that recomputes cannot. The cost is one query per quote, which this
    product can afford and which a quote's activity log stays small enough to
    repay many times over.

    The third counter is the researched one the research cannot support here.
    "connected email channel (for 'since last communication')" is the named
    source, this product has no mail transport, and an inbound buyer reply is a
    communication this workflow never records. So with no channel connected the
    counter is ``None`` with the reason beside it, rather than a count of
    outbound events that would tell a seller the buyer is silent when the buyer
    may have replied. The other three counters do not need the channel and are
    reported either way. Jev chose this over falling back to the last recorded
    send (audit ``jev-20261013T111835-5236-15033``).
    """
    limit = require_threshold(threshold)
    counted = 0
    views = 0
    buyer_events = 0
    seller_events = 0
    pdf_opens = 0
    last: datetime | None = None
    moments: list[datetime] = []

    for event in events or ():
        counted += 1
        moment = event_moment(event)
        if moment is not None:
            moments.append(moment)
            if last is None or moment > last:
                last = moment
        label = str(event.get("event") or "")
        side = EVENT_SIDES.get(label, SELLER)
        if side == BUYER:
            buyer_events += 1
        else:
            seller_events += 1
        if counts_as_view(label, event.get("surface")):
            views += 1
        elif label == VIEW_EVENT:
            pdf_opens += 1

    baseline = parse_timestamp(last_communication_at)
    since: int | None = None
    since_basis: str | None = None
    buyer_since: int | None = None
    if not email_channel_connected:
        since_basis = SINCE_COMMUNICATION_UNAVAILABLE
    elif baseline is None:
        since_basis = SINCE_COMMUNICATION_NO_BASELINE
    else:
        since = 0
        buyer_since = 0
        for event in events or ():
            moment = event_moment(event)
            if moment is None or moment <= baseline:
                continue
            since += 1
            if EVENT_SIDES.get(str(event.get("event") or ""), SELLER) == BUYER:
                buyer_since += 1
        since_basis = "email_channel"

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    return {
        LAST_ACTIVITY_AT: iso(last),
        ACTIVITY_COUNT: counted,
        VIEW_COUNT: views,
        ACTIVITY_SINCE_COMMUNICATION: since,
        "since_last_communication_basis": since_basis,
        "buyer_activity_count": buyer_events,
        "seller_activity_count": seller_events,
        "buyer_activity_since_last_communication": buyer_since,
        "pdf_opens_not_counted_as_views": pdf_opens,
        "first_activity_at": iso(min(moments) if moments else None),
        "days_since_last_activity": (None if last is None else max(0, (reference - last).days)),
        "activity_class": classify_activity(counted, limit),
        HIGH_ACTIVITY_THRESHOLD: limit,
        "threshold_configured": limit is not None,
        EMAIL_CHANNEL_CONNECTED: bool(email_channel_connected),
        LAST_COMMUNICATION_AT: iso(baseline),
    }


# --------------------------------------------------------------------------- #
# Quote fields this workflow reads
# --------------------------------------------------------------------------- #


def first_present(data: Mapping[str, Any] | None, keys: Sequence[str]) -> Any:
    """The first of ``keys`` the payload carries, in order, or ``None``.

    Record payloads are arbitrary JSON in this product, so a field a workflow
    reads has several possible spellings and a workflow that demanded one would
    refuse data it could have used. The order of ``keys`` is the order of
    preference, and the caller documents which spelling it prefers.
    """
    payload = data or {}
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def read_expiration(data: Mapping[str, Any] | None) -> datetime | None:
    """When a quote expires, from whichever field the workspace named.

    Read-only. The expiry workflow owns writing this field; this one reads it so
    the "expires soon" recommendation has a date to work from, and refuses to
    write one so it cannot expire a quote it does not own.
    """
    return parse_timestamp(first_present(data, EXPIRATION_FIELDS))


def read_owner(data: Mapping[str, Any] | None) -> str | None:
    """A quote's owner, for the index-page owner filter."""
    value = first_present(data, OWNER_FIELDS)
    return None if value is None else str(value)


def read_signing_status(data: Mapping[str, Any] | None) -> str | None:
    """A quote's signing status, for the index-page signing filter.

    Read-only, like the rest. The signing workflow owns writing it, and a
    workspace that does not use signatures simply has no value here.
    """
    value = first_present(data, SIGNING_STATUS_FIELDS)
    return None if value is None else str(value)


def read_status(data: Mapping[str, Any] | None) -> str:
    """A quote's status, read case-insensitively."""
    payload = data or {}
    raw = payload.get("status") or payload.get("hs_status") or ""
    return str(raw).strip().upper()


def is_accepted(data: Mapping[str, Any] | None) -> bool:
    """Has the buyer answered this quote?

    "Quotes can have the following statuses, with a corresponding color
    indicator: ... **Pending approval** ... **Changes requested** ...
    **Accepted**." A quote the buyer accepted is never chased, so this is the
    first thing the follow-up rules test.
    """
    return read_status(data) in ACCEPTED_STATUSES


def is_chasable(data: Mapping[str, Any] | None) -> bool:
    """Is this a quote a seller could still follow up on?

    Three refusals, each for a reason a seller would give.

    A quote the buyer accepted, or one that is closed, is not chased: nobody needs
    chasing on a deal that already landed. A quote with no status is not chased,
    because it has not been placed in a pipeline yet. A quote in
    :data:`NOT_CHASABLE_STATUSES` is not chased, because nothing has reached the
    buyer, so a follow up to it is a first send wearing the wrong label.

    Anything else is fair game, including a status this feature does not
    recognise. A workspace may name a status another workflow wrote, and refusing
    to chase it would hide a real quote from a seller who is waiting on it. That
    is why the refusals are three named sets and not one allow list.
    """
    status = read_status(data)
    if not status:
        return False
    if status in ACCEPTED_STATUSES:
        return False
    return status not in NOT_CHASABLE_STATUSES


# --------------------------------------------------------------------------- #
# Follow-ups
# --------------------------------------------------------------------------- #


def recommend_follow_ups(
    quotes: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    no_activity_after_days: Any = None,
    expiring_within_days: Any = None,
    email_channel_connected: bool = False,
    last_communication_at: Any = None,
) -> list[dict[str, Any]]:
    """The quotes that need a follow-up, each with the reason it was chosen.

    "**Recommended follow-ups,** view recommended follow-ups for quotes based on
    activity on the quote, and expiration dates (e.g., quotes with no activity in
    a long time, or quotes that'll expire soon)."

    Both named reasons are here, and each has a second edge: a quote past its
    expiration date with no acceptance, and a quote the buyer has not answered
    since the last communication. The research says "e.g.", so the two examples
    are the shape of the list rather than the whole of it, and a seller waiting
    on a quote that has already expired has the same problem as one that is about
    to.

    Both windows are settings, and neither has a default number, for the same
    reason the threshold does not: the research gives no number, so this
    workflow ships none. A window that is unset reports the reason it could not
    be evaluated rather than a default the workspace never chose. The last
    reason additionally needs a connected email channel, because it measures
    silence since a communication this product cannot observe without one.

    A quote can carry more than one reason. They are reported together rather
    than collapsed, because a quote that is both quiet and about to expire is one
    row in a seller's list and two facts about it.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    stale_days = require_window(no_activity_after_days, "no_activity_after_days")
    expiry_days = require_window(expiring_within_days, "expiring_within_days")
    baseline = parse_timestamp(last_communication_at)

    out: list[dict[str, Any]] = []
    for quote in quotes or ():
        data = quote.get("data") or {}
        if not is_chasable(data):
            continue
        counters = dict(quote.get("engagement") or {})
        reasons: list[dict[str, Any]] = []

        last = parse_timestamp(counters.get(LAST_ACTIVITY_AT))
        if stale_days is not None:
            idle = (reference - last).days if last is not None else None
            if last is None:
                reasons.append(
                    {
                        "reason": REASON_NO_ACTIVITY,
                        "days_idle": None,
                        "detail": "This quote has no recorded activity at all.",
                    }
                )
            elif idle >= stale_days:
                reasons.append(
                    {
                        "reason": REASON_NO_ACTIVITY,
                        "days_idle": idle,
                        "detail": f"No activity for {idle} days.",
                    }
                )

        expires = read_expiration(data)
        if expires is not None:
            remaining = (expires - reference).days
            if expiry_days is not None and 0 <= remaining <= expiry_days:
                reasons.append(
                    {
                        "reason": REASON_EXPIRING_SOON,
                        "days_remaining": remaining,
                        "detail": f"Expires in {remaining} days.",
                    }
                )
            elif remaining < 0 and not is_accepted(data):
                reasons.append(
                    {
                        "reason": REASON_EXPIRED_UNACCEPTED,
                        "days_remaining": remaining,
                        "detail": f"Expired {-remaining} days ago and not accepted.",
                    }
                )

        if email_channel_connected and baseline is not None:
            buyer_since = counters.get("buyer_activity_since_last_communication")
            since = counters.get(ACTIVITY_SINCE_COMMUNICATION)
            if buyer_since == 0 and isinstance(since, int) and since > 0 and not is_accepted(data):
                # At least one recorded event after the communication and none of
                # them the buyer's: the seller has worked the quote and the buyer
                # has not answered. A quote with no events at all after the
                # communication is the no-activity reason instead, and a quote
                # with no events at all has no first day to measure from.
                reasons.append(
                    {
                        "reason": REASON_NO_REPLY,
                        "days_idle": (None if last is None else max(0, (reference - last).days)),
                        "detail": "Nothing from the buyer since the last communication.",
                    }
                )

        if not reasons:
            continue
        out.append(
            {
                "quote_id": quote.get("id"),
                "title": data.get("title") or data.get("hs_title") or quote.get("id"),
                # Two workspaces can carry two quotes with the same title, and a
                # follow-up list that shows only the title makes them one row.
                # The quote number is carried beside it for exactly that reason.
                "quote_number": data.get("quote_number") or data.get("hs_quote_number"),
                "status": read_status(data),
                "owner": read_owner(data),
                "expires_at": iso(expires),
                "reasons": reasons,
                "reason_codes": [reason["reason"] for reason in reasons],
                "activity_class": counters.get("activity_class"),
                "activity_count": counters.get(ACTIVITY_COUNT, 0),
                "last_activity_at": counters.get(LAST_ACTIVITY_AT),
            }
        )

    out.sort(key=lambda row: (len(row["reason_codes"]), _idle_sort(row)), reverse=True)
    return out


def _idle_sort(row: Mapping[str, Any]) -> tuple[int, str]:
    """Sort key for the quietest quote first inside one reason count."""
    last = str(row.get("last_activity_at") or "")
    return (0 if last else 1, last)


# --------------------------------------------------------------------------- #
# Pipeline overview
# --------------------------------------------------------------------------- #


def pipeline_overview(
    quotes: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    threshold: Any = None,
    expiring_within_days: Any = None,
) -> dict[str, Any]:
    """The three figures the Pipeline overview panel reads.

    "Under **Pipeline overview** read the value of high-activity quotes, quotes
    at risk of expiring, and the quote acceptance rate."

    Each figure carries the basis it was computed on, and each says when it
    cannot be computed rather than showing a zero that reads like a measurement.
    A high-activity count of zero with no threshold configured is not the same
    fact as a high-activity count of zero against a threshold of 10, and a panel
    that cannot tell them apart teaches a seller that nothing is happening.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    limit = require_threshold(threshold)
    expiry_days = require_window(expiring_within_days, "expiring_within_days")

    high = 0
    low = 0
    unclassified = 0
    accepted = 0
    at_risk = 0
    expiring = 0
    expired = 0
    total_amount = 0.0
    counted_value = 0.0
    dated = 0

    for quote in quotes or ():
        data = quote.get("data") or {}
        counters = dict(quote.get("engagement") or {})
        klass = counters.get("activity_class")
        if klass == ACTIVITY_HIGH:
            high += 1
        elif klass == ACTIVITY_LOW:
            low += 1
        else:
            unclassified += 1

        if is_accepted(data):
            accepted += 1

        amount = _as_float(data.get("hs_quote_amount") or data.get("locked_amount"))
        if amount is not None:
            total_amount += amount
            if klass == ACTIVITY_HIGH:
                counted_value += amount
            dated += 1

        expires = read_expiration(data)
        if expires is None:
            continue
        remaining = expires - reference
        if remaining < timedelta(0):
            expired += 1
            if not is_accepted(data):
                at_risk += 1
        elif expiry_days is not None and remaining <= timedelta(days=expiry_days):
            at_risk += 1
            expiring += 1

    total = len(quotes or ())
    return {
        "quotes": total,
        "high_activity_quotes": high,
        "low_activity_quotes": low,
        "unclassified_quotes": unclassified,
        "high_activity_value": round(counted_value, 2),
        "total_value": round(total_amount, 2),
        "quotes_with_a_value": dated,
        "quotes_at_risk": at_risk,
        "quotes_expiring_soon": expiring,
        "quotes_expired": expired,
        "accepted_quotes": accepted,
        "acceptance_rate": round(accepted / total, 4) if total else None,
        "threshold": limit,
        "threshold_configured": limit is not None,
        "expiring_within_days": expiry_days,
        "basis": {
            "high_activity": (
                f"Activity Count at or above {limit}"
                if limit is not None
                else "No threshold configured, so no quote is classified"
            ),
            "at_risk": (
                f"Expires within {expiry_days} days or already expired and not accepted"
                if expiry_days is not None
                else "No expiry window configured, so no quote is at risk"
            ),
            "acceptance_rate": "Quotes in an accepted status over all quotes",
        },
    }


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Chart points
# --------------------------------------------------------------------------- #


def chart_points(
    quotes: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    threshold: Any = None,
    include_expired: bool = False,
) -> dict[str, Any]:
    """One point per quote for the expiration-by-engagement bubble chart.

    "in the *Your published quotes* bubble chart, quotes below the threshold
    render black and above it green; hover for name, expiration date and activity
    count; tick **Show older expired quotes**."

    The three hover facts are carried on every point, so the page renders them
    from this answer rather than from a second query it might disagree with. The
    two chart colours map onto the two researched classes: black is
    :data:`ACTIVITY_LOW` and green is :data:`ACTIVITY_HIGH`. A point whose class is
    unclassified carries no chart colour at all, because there is no threshold to
    compare it against and a grey point beside two coloured ones would read as a
    third finding.

    ``include_expired`` is the researched tick box. It is honoured exactly: with
    it off, a quote past its expiration date is not on the chart at all, which is
    why the answer also reports how many it hid. The tick box with no count
    beside it is a control that appears to do nothing.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    limit = require_threshold(threshold)

    points: list[dict[str, Any]] = []
    hidden_expired = 0
    for quote in quotes or ():
        data = quote.get("data") or {}
        counters = dict(quote.get("engagement") or {})
        expires = read_expiration(data)
        expired = expires is not None and expires < reference
        if expired and not include_expired:
            hidden_expired += 1
            continue
        klass = counters.get("activity_class") or classify_activity(
            counters.get(ACTIVITY_COUNT, 0), limit
        )
        points.append(
            {
                "quote_id": quote.get("id"),
                "name": data.get("title") or data.get("hs_title") or quote.get("id"),
                "expires_at": iso(expires),
                "expired": expired,
                "activity_count": counters.get(ACTIVITY_COUNT, 0),
                "view_count": counters.get(VIEW_COUNT, 0),
                "last_activity_at": counters.get(LAST_ACTIVITY_AT),
                "activity_class": klass,
                "chart_tone": chart_tone(klass),
                "value": _as_float(data.get("hs_quote_amount") or data.get("locked_amount")),
            }
        )

    points.sort(key=lambda point: (point["expired"], str(point["expires_at"] or "")))
    return {"points": points, "count": len(points), "expired_hidden": hidden_expired}


def chart_tone(activity_class: Any) -> str | None:
    """The colour a bubble chart point renders for a class.

    Black and green are the researched pair, and this product has one accent, so
    the mapping is onto semantic tokens rather than onto named colours: the low
    class takes the warning tone, which is the closest token to black that stays
    readable on a light ground, and the high class takes the success tone, which
    is the green the research names. No colour alone carries the finding: the
    page prints the class name beside every point and this returns ``None`` for a
    class the threshold cannot decide.
    """
    if activity_class == ACTIVITY_HIGH:
        return "success"
    if activity_class == ACTIVITY_LOW:
        return "warning"
    return None


__all__ = (
    "ACTIVITY_CLASSES",
    "QuoteEngagementRuleError",
    "chart_points",
    "chart_tone",
    "classify_activity",
    "counts_as_view",
    "default_surface",
    "event_moment",
    "event_side",
    "first_present",
    "is_accepted",
    "is_chasable",
    "iso",
    "normalise_event",
    "normalise_surface",
    "parse_timestamp",
    "pipeline_overview",
    "read_expiration",
    "read_owner",
    "read_signing_status",
    "read_status",
    "recommend_follow_ups",
    "require_threshold",
    "require_window",
    "summarise_events",
)
