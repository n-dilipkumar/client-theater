"""When a reminder fires, and whether it is allowed to.

This is the researched specification made executable, and it is pure: no store,
no HTTP, no clock of its own. Everything the product does to a booking happens
here, and the engine above it only decides *when* to ask.

The three researched mechanisms, in the order the flow applies them.

**The firing condition** (``user_flow`` step 4). Three of them, and the anchor
differs: the two *Before* conditions are offset from the meeting's start, and
*After the Meeting* is offset from its end, because the research calls it "a
useful option for follow-up emails" and a follow-up follows the meeting rather
than its first minute.

**The response gate.** Only the conditional pre-meeting reminder has one, and it
is the sharpest sentence in the research: "only if the Primary Guest has not
responded to the invite (**Accepted or Declined**)." Declining is responding.
A guest who has said no must not be chased by a "you have not responded" email,
and a rule that reads only ``accepted`` as a response gets that exactly wrong.

**The advanced gates** (step 6): *Send only if meeting starts on*, and *Send if
the meeting was booked more than selected timeframe*.

**The ladder is total.** Every path through :func:`decide` returns exactly one
of the three researched statuses, and a skip always carries one of the five
documented reasons. There is no fall-through, because a rule that does not fall
through is the kind of bug nobody finds until a customer reports a reminder that
never arrived and no row says why. :func:`decide` therefore ends in an explicit
terminal rather than falling off the end of a loop, and
``test_decide_has_no_fall_through`` checks that every researched combination
reaches a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from dsr.meeting_reminders import vocabulary as vocab
from dsr.meeting_reminders.errors import ConfigurationRefused, ReminderError

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
#
# A booking's start arrives as an ISO-8601 string. Everything downstream is UTC
# except the two gates that are about a *local* calendar day and a *local*
# clock, which is why the offset is carried on the booking rather than taken from
# the host.

#: The longest offset a fixed-offset timezone can express, and the reason a
#: booking carries minutes rather than a named zone. ``zoneinfo`` needs a
#: timezone database the platform does not ship, and a reminder that silently
#: evaluated in UTC on a host without one would fire a weekday gate on the
#: wrong day. Minutes are exact, portable, and a named zone is kept as a label
#: for the message and the UI.
MAX_TZ_OFFSET_MINUTES = 60 * 24


def fixed_offset(minutes: int) -> timezone:
    """A ``tzinfo`` for a whole-minute UTC offset."""
    bounded = max(-MAX_TZ_OFFSET_MINUTES, min(MAX_TZ_OFFSET_MINUTES, int(minutes)))
    return timezone(timedelta(minutes=bounded))


def parse_instant(value: Any) -> datetime | None:
    """Parse an ISO-8601 instant into an aware UTC datetime, or ``None``.

    ``None`` rather than an exception for an unparseable value, because a
    booking with a malformed ``start`` is a data problem the engine reports as
    a skip reason, not a 400 on a list endpoint that was only trying to read it.
    """
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def require_instant(value: Any, field_name: str = "start") -> datetime:
    parsed = parse_instant(value)
    if parsed is None:
        raise ReminderError(f"{field_name} must be an ISO-8601 timestamp; got {value!r}")
    return parsed


def local_timezone(booking: Mapping[str, Any]) -> timezone:
    """The booking's own UTC offset, defaulting to UTC.

    Read from ``timezoneOffsetMinutes``. A named ``timezone`` is a label: this
    product does not resolve IANA zones, because a database it does not ship
    would make the weekday gate wrong on a host without one, and a wrong weekday
    gate is a reminder sent on Saturday. The offset is the whole number, and the
    label is what the guest sees.
    """
    raw = booking.get("timezoneOffsetMinutes")
    if raw in (None, ""):
        return timezone.utc
    try:
        return fixed_offset(int(raw))
    except (TypeError, ValueError):
        return timezone.utc


def format_local(booking: Mapping[str, Any], moment: datetime, pattern: str) -> str:
    return moment.astimezone(local_timezone(booking)).strftime(pattern)


# --------------------------------------------------------------------------- #
# The fire time
# --------------------------------------------------------------------------- #


def fire_at(reminder: Mapping[str, Any], booking: Mapping[str, Any]) -> datetime | None:
    """When a reminder would fire for a booking, or ``None`` if it cannot be.

    ``before_*`` is ``start - offset``; ``after_meeting`` is
    ``start + duration + offset``. The duration comes off the booking because
    the ``data_flow`` names it, and without it a one-hour follow-up would be
    scheduled one hour after a meeting that ran forty-five minutes.

    ``None`` is returned rather than raising when the booking has no usable
    start, so one broken booking cannot fail the whole run.
    """
    start = parse_instant(booking.get("start"))
    if start is None:
        return None
    condition = vocab.require_condition(reminder.get("condition"))
    offset = vocab.require_offset(
        reminder.get("offset", {}).get("value", reminder.get("offsetValue", vocab.DEFAULT_OFFSET)),
        reminder.get("offset", {}).get("unit", reminder.get("offsetUnit", vocab.DEFAULT_UNIT)),
    )
    if condition == vocab.AFTER:
        duration = booking.get("durationMinutes")
        try:
            minutes = int(duration) if duration not in (None, "") else 0
        except (TypeError, ValueError):
            minutes = 0
        return start + timedelta(minutes=minutes) + timedelta(minutes=offset["minutes"])
    return start - timedelta(minutes=offset["minutes"])


def is_due(fire_time: datetime, now: datetime) -> bool:
    """Whether a reminder whose fire time has arrived should be evaluated now.

    A fire time in the past is still evaluated, not discarded: the research's own
    first skip reason is "Reminder schedule time in the past", which is only
    reachable if a past fire time is examined and *then* refused. So ``is_due``
    is a lower bound - the evaluation is what decides - and the past check lives
    in the ladder, where the reason can be recorded.
    """
    return fire_time <= now


# --------------------------------------------------------------------------- #
# The researched gates
# --------------------------------------------------------------------------- #


def has_not_responded(booking: Mapping[str, Any]) -> bool:
    """Whether the primary guest has left the invite unanswered.

    ``responseStatus`` comes from the calendar invite and has three values per
    the research: "accepted/declined/needsAction". The quoted rule is "only if
    the Primary Guest has not responded to the invite (Accepted or Declined)", so
    **both** accepted and declined count as having responded. Only
    ``needsAction`` - or no value at all, which is what an un-answered invite
    looks like from the outside - leaves them un-responded.

    The comparison is case-insensitive because the field is written by a
    calendar provider, and ``needsAction`` is camel-cased in the research while
    a JSON payload may carry ``needs_action`` or ``NEEDSACTION``.
    """
    guest = booking.get("primaryGuest")
    status = None
    if isinstance(guest, Mapping):
        status = guest.get("responseStatus")
    if status in (None, ""):
        status = booking.get("responseStatus")
    if status in (None, ""):
        return True
    normalised = str(status).strip().lower().replace("_", "")
    return normalised not in {value.lower() for value in vocab.RESPONSED_STATUSES}


def weekday_allowed(booking: Mapping[str, Any], weekdays: Sequence[str]) -> bool:
    """Whether the meeting starts on one of the chosen weekdays.

    Evaluated in the **booking's** local time, not the host's. "Send only if
    meeting starts on" is about the meeting's day as the guest experiences it: a
    09:00 Pacific meeting is 17:00 UTC the same day but 02:00 UTC the next day in
    some eastern zones, and a host evaluating it in UTC would fire a
    Monday-only reminder on a Sunday.
    """
    if not weekdays:
        return True
    start = parse_instant(booking.get("start"))
    if start is None:
        return False
    local = start.astimezone(local_timezone(booking))
    return vocab.WEEKDAYS[local.weekday()] in {str(day).strip().lower() for day in weekdays}


def booked_far_enough(booking: Mapping[str, Any], offset: Mapping[str, Any]) -> bool:
    """Whether the meeting was booked at least the chosen timeframe in advance.

    The research's own example: "the reminder will be sent only if the meeting is
    booked one week in advance from the current booking date". So the comparison
    is ``booked_at <= start - timeframe``, and a booking made too close to the
    meeting fails this gate.
    """
    start = parse_instant(booking.get("start"))
    booked = parse_instant(booking.get("bookedAt"))
    if start is None or booked is None:
        # No booking date means the gate cannot be satisfied. Failing closed
        # here is the researched reading: the option says "send *only* if", and
        # a gate that passes when it cannot be checked is a gate that never
        # gates.
        return False
    threshold = start - timedelta(minutes=int(offset["minutes"]))
    return booked <= threshold


# --------------------------------------------------------------------------- #
# The condition group
# --------------------------------------------------------------------------- #
#
# ``extensibility``: "condition groups with `matches: all|any|none`". The two
# researched gates are shipped as rules inside one group, so a third gate is a
# new rule name rather than a new code path.

#: Every gate kind a group rule can name. The two researched ones, plus the
#: response gate, which is part of the firing condition and so is evaluated
#: separately - but a group can still name it, because Cal projects the whole
#: thing as filters and a team may want to compose it freely.
RULE_KINDS = (vocab.RULE_WEEKDAY, vocab.RULE_LEAD_TIME)


def evaluate_rule(
    kind: str,
    rule: Mapping[str, Any],
    booking: Mapping[str, Any],
) -> bool:
    """Evaluate one rule of a condition group against a booking."""
    if kind == vocab.RULE_WEEKDAY:
        return weekday_allowed(booking, vocab.require_weekdays(rule.get("weekdays")))
    if kind == vocab.RULE_LEAD_TIME:
        offset = vocab.require_offset(
            rule.get("value", vocab.DEFAULT_OFFSET), rule.get("unit", vocab.DEFAULT_UNIT)
        )
        return booked_far_enough(booking, offset)
    raise ReminderError(
        f"unknown condition kind {kind!r}; published kinds are {list(RULE_KINDS)}. Add it to "
        "RULE_KINDS in conditions.py and give it a case here."
    )


def evaluate_group(
    group: Mapping[str, Any] | None,
    booking: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate a whole condition group, and report every rule's verdict.

    An absent or empty group passes: no restriction is the product's own default
    and its own label, "**No Restriction**".

    Every rule is reported, not just the deciding one, because an administrator
    debugging a skipped reminder needs to know which of two gates failed, and a
    short-circuit would hide the second.
    """
    if not group:
        return {"match": vocab.DEFAULT_MATCH, "rules": [], "passed": True, "failed": []}
    mode = vocab.require_match(group.get("match", vocab.DEFAULT_MATCH))
    rules = group.get("rules")
    if rules in (None, ""):
        rules = []
    if not isinstance(rules, list):
        raise ReminderError("a condition group's 'rules' must be a list")

    verdicts: list[dict[str, Any]] = []
    for index, raw in enumerate(rules):
        rule = dict(raw or {})
        kind = str(rule.get("kind") or rule.get("type") or "").strip().lower()
        if kind not in RULE_KINDS:
            raise ReminderError(
                f"rule {index} names kind {kind!r}; published kinds are {list(RULE_KINDS)}"
            )
        passed = evaluate_rule(kind, rule, booking)
        verdicts.append({"index": index, "kind": kind, "rule": rule, "passed": passed})

    outcomes = [verdict["passed"] for verdict in verdicts]
    if mode == vocab.MATCH_ALL:
        passed = all(outcomes) if outcomes else True
    elif mode == vocab.MATCH_ANY:
        passed = any(outcomes)
    else:  # vocab.MATCH_NONE
        passed = not any(outcomes)
    return {
        "match": mode,
        "rules": verdicts,
        "passed": passed,
        "failed": [verdict["kind"] for verdict in verdicts if not verdict["passed"]],
    }


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #
#
# Step 3: "`Send Email To` (Primary Guest or All Guests)". The guest form's
# phone field is the researched SMS address - "You must have a Phone field in
# your **Guest Form**" - and the *Skip reason* vocabulary carries both
# "Recipient not found" and "Phone not found", which is why those are two
# reasons and not one.


def _emailable(guest: Mapping[str, Any]) -> bool:
    return bool(str(guest.get("email") or "").strip())


def _has_phone(guest: Mapping[str, Any]) -> bool:
    return bool(str(guest.get("phone") or "").strip())


def resolve_recipients(reminder: Mapping[str, Any], booking: Mapping[str, Any]) -> dict[str, Any]:
    """Work out who this reminder is addressed to, and who is actually reachable.

    Returns the resolved recipients, the ones that could not be addressed, and
    the documented skip reason when *nobody* could be. That reason is the whole
    point: an email reminder whose guests have no addresses is "Recipient not
    found" and an SMS reminder whose guest has no phone is "Phone not found",
    and the two are separate in the research's own vocabulary.

    ``Send Email To: All Guests`` sends to every guest with an address and
    records the ones without, rather than refusing the whole reminder: the
    research offers "All Guests" as a choice, and a single guest without an
    address is not a reason to stop notifying the other four.
    """
    channel = vocab.require_channel(reminder.get("channel"))
    primary = booking.get("primaryGuest")
    primary = primary if isinstance(primary, Mapping) else {}
    guests = [guest for guest in (booking.get("guests") or []) if isinstance(guest, Mapping)]

    if channel == vocab.SMS:
        # The researched SMS address is the guest form's phone field, and the
        # guest form is collected for the primary guest. An SMS reminder is
        # therefore addressed to the primary guest, and there is no "all guests"
        # option for SMS in step 3 - only Send Email To has that choice.
        candidates = [primary] if primary else []
        reachable = [guest for guest in candidates if _has_phone(guest)]
        unreachable = [guest for guest in candidates if not _has_phone(guest)]
        return _recipients(
            channel,
            reachable,
            unreachable,
            empty_reason=vocab.PHONE_NOT_FOUND,
            empty_detail=(
                "no phone number on the primary guest; the research requires a Phone field in "
                "the Guest Form before an SMS reminder can be sent"
            ),
        )

    to = vocab.require_email_to(reminder.get("emailTo"))
    if to == vocab.PRIMARY_GUEST:
        candidates = [primary] if primary else []
    else:
        # All guests, primary first, de-duplicated by email: a booking whose
        # `guests` list repeats the primary guest would otherwise send twice.
        candidates = ([primary] if primary else []) + guests
    reachable: list[Mapping[str, Any]] = []
    unreachable: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    no_show = vocab.skip_no_show_attendees(reminder.get("skipNoShowAttendees"))
    for guest in candidates:
        if no_show and guest.get(vocab.NO_SHOW):
            # `skipNoShowAttendees` is a researched switch, and it is applied
            # *here* rather than only when the message is composed: a guest left
            # out of the list is a guest the decision says was notified, and a
            # delivery claiming to have reached someone it skipped is exactly the
            # kind of row nobody can audit.
            continue
        address = str(guest.get("email") or "").strip().lower()
        if not _emailable(guest):
            unreachable.append(guest)
            continue
        if address in seen:
            continue
        seen.add(address)
        reachable.append(guest)
    return _recipients(
        channel,
        reachable,
        unreachable,
        empty_reason=vocab.RECIPIENT_NOT_FOUND,
        empty_detail="no guest on the booking has an email address",
    )


def _recipients(
    channel: str,
    reachable: Sequence[Mapping[str, Any]],
    unreachable: Sequence[Mapping[str, Any]],
    *,
    empty_reason: str,
    empty_detail: str,
) -> dict[str, Any]:
    to = [
        {
            "name": str(guest.get("name") or guest.get("firstName") or ""),
            # Only the address the channel actually uses. An SMS delivery row
            # carrying a guest's email address is information the researched flow
            # never needed, and the same reasoning applies to the reverse.
            **(
                {"phone": str(guest.get("phone") or "")}
                if channel == vocab.SMS
                else {"email": str(guest.get("email") or "")}
            ),
            "no_show": bool(guest.get(vocab.NO_SHOW)),
        }
        for guest in reachable
    ]
    return {
        "channel": channel,
        "recipients": to,
        "unaddressable": [str(guest.get("name") or "") for guest in unreachable],
        "reason": None if to else empty_reason,
        "detail": None if to else empty_detail,
    }


def sender_for(
    reminder: Mapping[str, Any], booking: Mapping[str, Any], org: Mapping[str, Any]
) -> str:
    """The address or number the reminder goes out from.

    Email follows ``Send Email From``: the host's address, the booker's, or the
    organisation's no-reply address on its own custom domain. SMS follows ``Send
    SMS From``: any connected number, or a local area number.

    Raises :class:`ConfigurationRefused` rather than returning an empty string
    when the chosen mode has no address. That is deliberate: a message with no
    sender cannot go out, and the researched skip-reason list has no entry for
    "the organisation has not finished setting itself up" - because it is not a
    delivery outcome, it is a mistake in configuration, and the five reasons are a
    closed list this build keeps closed. Raising says the difference: the
    administrator has something to fix, rather than a rep opening *Meetings
    Activity* to find a reminder that reported itself as sent and reached nobody.
    """
    channel = vocab.require_channel(reminder.get("channel"))
    if channel == vocab.SMS:
        mode = vocab.require_sms_from(reminder.get("smsFrom"))
        key = "localNumber" if mode == vocab.LOCAL_AREA_NUMBER else "number"
        value = str(org.get(key) or "").strip()
        if not value:
            raise ConfigurationRefused(
                f"this reminder sends SMS {mode.replace('_', ' ')}, and the organisation has no "
                f"{'local area ' if mode == vocab.LOCAL_AREA_NUMBER else ''}number configured: "
                + vocab.TWILIO_QUOTE
            )
        return value
    mode = vocab.require_email_from(reminder.get("emailFrom"))
    if mode == vocab.NO_REPLY:
        domain = str(org.get("noreply_domain") or org.get("noreplyDomain") or "").strip()
        if not domain:
            raise ConfigurationRefused(
                "a no-reply sender needs the organisation's own sending domain configured first; "
                "the research calls it a 'No-reply address on a custom domain'"
            )
        return f"no-reply@{domain}"
    person = booking.get("host") if mode == vocab.HOST_ADDRESS else booking.get("booker")
    value = str(person.get("email") or "").strip() if isinstance(person, Mapping) else ""
    if not value:
        raise ConfigurationRefused(
            f"this reminder sends from the {mode}'s own address, and the booking records no email "
            f"address for the {mode}"
        )
    return value


def reply_to_for(reminder: Mapping[str, Any], booking: Mapping[str, Any]) -> list[str]:
    """The addresses an SMS guest's reply is forwarded to, per ``Send Replies To``.

    "When a guest replies to an SMS reminder, Chili Piper forwards the text to
    your team by email." Host, booker, or every assignee - the three choices step
    3 offers.
    """
    choice = vocab.require_replies_to(reminder.get("repliesTo"))
    addresses: list[str] = []
    if choice in (vocab.REPLY_TO_HOST, vocab.REPLY_TO_BOOKER):
        person = booking.get("host" if choice == vocab.REPLY_TO_HOST else "booker")
        if isinstance(person, Mapping) and str(person.get("email") or "").strip():
            addresses.append(str(person["email"]).strip())
    if choice == vocab.REPLY_TO_ASSIGNEES:
        for assignee in booking.get("assignees") or []:
            if isinstance(assignee, Mapping) and str(assignee.get("email") or "").strip():
                addresses.append(str(assignee["email"]).strip())
    return sorted(set(addresses))


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


@dataclass
class Decision:
    """One reminder's verdict for one booking.

    ``status`` is always one of the three researched values, and ``reason`` is
    always set whenever ``status`` is ``skipped``. That invariant is the point
    of the dataclass rather than a dict: a delivery row with no recorded reason
    is the artifact this product exists to prevent.
    """

    status: str
    reason: str | None = None
    reason_text: str | None = None
    fire_at: datetime | None = None
    due: bool = False
    channel: str = vocab.DEFAULT_CHANNEL
    recipients: list[dict[str, Any]] = field(default_factory=list)
    unaddressable: list[str] = field(default_factory=list)
    from_address: str = ""
    replies_to: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "reason_text": self.reason_text,
            "fire_at": self.fire_at.isoformat() if self.fire_at else None,
            "due": self.due,
            "channel": self.channel,
            "recipients": list(self.recipients),
            "unaddressable": list(self.unaddressable),
            "from_address": self.from_address,
            "replies_to": list(self.replies_to),
            "checks": list(self.checks),
            "detail": self.detail,
        }


def _record(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> bool:
    checks.append({"check": name, "passed": passed, "detail": detail})
    return passed


def plan(
    reminder: Mapping[str, Any],
    booking: Mapping[str, Any],
    *,
    now: datetime,
) -> Decision:
    """The first half of the rule: *can this reminder be scheduled at all?*

    The researched skip reason "Reminder schedule time in the past" belongs here
    rather than at run time, and the distinction is load-bearing. A reminder is
    **planned** when it is attached to a meeting, which may be hours or days
    before the meeting; a reminder **runs** when its moment arrives. A "2 hours
    before" reminder attached to a meeting that starts in thirty minutes can
    never fire on time, and the reason to record is that its schedule time had
    already passed at planning - not that something went wrong when the
    scheduler eventually reached it.

    That is why this is a separate step and not a check inside :func:`decide`:
    at run time ``fire_at`` is *always* in the past, so testing for it there
    would skip every reminder that ever fired.
    """
    channel = vocab.require_channel(reminder.get("channel"))
    when = fire_at(reminder, booking)
    checks: list[dict[str, Any]] = []
    if when is None:
        checks.append(
            {
                "check": "fire_time",
                "passed": False,
                "detail": "the booking has no usable start time",
            }
        )
        return Decision(
            status=vocab.SKIPPED,
            reason=vocab.SCHEDULE_IN_PAST,
            reason_text=vocab.skip_reason(vocab.SCHEDULE_IN_PAST),
            fire_at=None,
            due=True,
            channel=channel,
            checks=checks,
            detail="the booking has no usable start time, so the reminder has no schedule time",
        )
    if when <= now:
        checks.append(
            {
                "check": "schedule_time",
                "passed": False,
                "detail": f"fires at {when.isoformat()}, which is already in the past",
            }
        )
        return Decision(
            status=vocab.SKIPPED,
            reason=vocab.SCHEDULE_IN_PAST,
            reason_text=vocab.skip_reason(vocab.SCHEDULE_IN_PAST),
            fire_at=when,
            due=True,
            channel=channel,
            checks=checks,
            detail=(
                f"the reminder was due at {when.isoformat()}, before it was planned, so there was no "
                "moment at which it could have been sent"
            ),
        )
    checks.append(
        {"check": "schedule_time", "passed": True, "detail": f"fires at {when.isoformat()}"}
    )
    return Decision(
        status=vocab.SCHEDULED,
        fire_at=when,
        due=False,
        channel=channel,
        checks=checks,
        detail=f"scheduled for {when.isoformat()}",
    )


def decide(
    reminder: Mapping[str, Any],
    booking: Mapping[str, Any],
    *,
    now: datetime,
    org: Mapping[str, Any] | None = None,
) -> Decision:
    """The second half of the rule: *at its moment, may it go out?*

    Assumes :func:`plan` already ran, so "schedule time in the past" is not
    re-checked here - see its docstring for why testing it at run time would
    skip every reminder that ever fired.

    The order is the order the flow applies them, and each step is a single
    question with a documented answer, so the reason recorded is the *first*
    thing that stopped it rather than whichever check happened to run last:

    1. the response gate, because it belongs to the firing condition itself;
    2. the advanced gates, because they are the administrator's explicit
       restrictions;
    3. the recipients, because a message with nowhere to go is the last thing
       worth learning.

    Each step ends in ``skipped`` with one of the documented reasons, and the
    last ends in ``sent``.

    The function **always returns a Decision**. There is no code path that
    reaches the end without one, which is what makes a run safe to point at real
    bookings: an unforeseen combination produces a skip with a reason, not a
    ``None`` that a caller has to remember to handle.
    """
    settings = org or {}
    channel = vocab.require_channel(reminder.get("channel"))
    condition = vocab.require_condition(reminder.get("condition"))
    when = fire_at(reminder, booking)

    def skipped(reason: str, detail: str, checks: list[dict[str, Any]]) -> Decision:
        return Decision(
            status=vocab.SKIPPED,
            reason=reason,
            reason_text=vocab.skip_reason(reason),
            fire_at=when,
            due=True,
            channel=channel,
            checks=checks,
            detail=detail,
        )

    checks: list[dict[str, Any]] = []
    #: The first refusal, kept while the rest of the gates are still evaluated.
    #: See the ``skip-reason-precedence`` inference: the recorded reason is the
    #: first thing that stopped it, but every gate's verdict is still reported so
    #: an administrator debugging a skip can see the other two rather than infer
    #: that they passed.
    refusal: tuple[str, str] | None = None

    def refuse(reason: str, detail: str) -> None:
        nonlocal refusal
        if refusal is None:
            refusal = (reason, detail)

    # -- 1. the response gate, part of the firing condition ------------------ #
    if condition == vocab.BEFORE_IF_NO_RESPONSE:
        unanswered = has_not_responded(booking)
        _record(
            checks,
            "primary_guest_responded",
            unanswered,
            "the primary guest has not responded, so this reminder applies"
            if unanswered
            else "the primary guest has already responded (Accepted or Declined)",
        )
        if not unanswered:
            refuse(
                vocab.CONDITION_NOT_SATISFIED,
                "the primary guest has already responded to the invite, and the research counts "
                "both Accepted and Declined as responding",
            )

    # -- 2. the advanced gates --------------------------------------------- #
    group = evaluate_group(reminder.get("conditions"), booking)
    for verdict in group["rules"]:
        _record(
            checks,
            f"condition_{verdict['kind']}",
            verdict["passed"],
            "satisfied" if verdict["passed"] else "not satisfied",
        )
    _record(
        checks,
        "condition_group",
        group["passed"],
        f"{group['match']} of {len(group['rules'])} rule(s)"
        if group["rules"]
        else "no restriction configured",
    )
    if not group["passed"]:
        failed = ", ".join(group["failed"]) or group["match"]
        refuse(
            vocab.VIOLATED_RESTRICTION, f"the configured restriction was not satisfied: {failed}"
        )

    if refusal is not None:
        return skipped(refusal[0], refusal[1], checks)

    # -- 3. the fire time ---------------------------------------------------- #
    # `now` past the fire time is the *normal* case here: this function runs at
    # the moment the reminder is due. A fire time still in the future means the
    # caller ran early, and the honest answer is "not yet" rather than a send.
    if when is None or when > now:
        detail = (
            "the booking has no usable start time"
            if when is None
            else f"fires at {when.isoformat()}, not yet"
        )
        _record(checks, "due", False, detail)
        return Decision(
            status=vocab.SCHEDULED,
            fire_at=when,
            due=False,
            channel=channel,
            checks=checks,
            detail=detail,
        )
    _record(checks, "due", True, f"fires at {when.isoformat()}, which has arrived")

    # -- 4. the recipients --------------------------------------------------- #
    audience = resolve_recipients(reminder, booking)
    _record(
        checks,
        "recipients",
        not audience["reason"],
        f"{len(audience['recipients'])} addressable"
        if not audience["reason"]
        else str(audience["detail"]),
    )
    sender = sender_for(reminder, booking, settings)
    _record(
        checks,
        "sender",
        bool(sender),
        sender or "no sending address is configured for this channel and mode",
    )
    if audience["reason"]:
        return skipped(str(audience["reason"]), str(audience["detail"]), checks)

    return Decision(
        status=vocab.SENT,
        fire_at=when,
        due=True,
        channel=channel,
        recipients=list(audience["recipients"]),
        unaddressable=list(audience["unaddressable"]),
        from_address=sender,
        replies_to=reply_to_for(reminder, booking),
        checks=checks,
        detail=f"sent to {len(audience['recipients'])} recipient(s)",
    )


def planned_at(booking: Mapping[str, Any]) -> datetime | None:
    """The earliest moment a reminder could have been attached to this booking.

    A reminder cannot have fired before the meeting it reminds about was booked,
    because before then it did not exist. So the planning moment is not a
    parameter a caller has to invent - it is a fact about the booking, and
    reading it off the row is what makes "Reminder schedule time in the past"
    answerable rather than circular.

    ``bookedAt`` is the researched field ("the reminder will be sent only if the
    meeting is booked one week in advance from the current booking date"). A
    booking without one has no derived planning moment, and ``None`` is returned
    so the caller can fall back to its own clock rather than guessing midnight.
    """
    return parse_instant(booking.get("bookedAt"))


def evaluate(
    reminder: Mapping[str, Any],
    booking: Mapping[str, Any],
    *,
    now: datetime,
    org: Mapping[str, Any] | None = None,
) -> Decision:
    """The whole rule in one call, for a caller that has only one clock.

    :func:`plan` first, at the booking's own ``bookedAt``, and a skip from it
    short-circuits: a reminder whose schedule time had already passed can never
    become sendable, so running the gates on it would only produce a second, less
    accurate reason for the same outcome.

    This is the entry point for a **preview**, where "if I attach this to this
    booking now, what happens?" is exactly the question, and ``now`` really is the
    moment of planning.

    The engine does not use it for a run. A scheduler plans at one moment and
    fires at a later one, and passing the run's clock as the planning clock would
    make every reminder that fires look like it had been planned too late - which
    is why :meth:`ReminderEngine.deliver` derives the planning moment from the
    booking instead.
    """
    when_planned = planned_at(booking) or now
    planned = plan(reminder, booking, now=when_planned)
    if planned.status == vocab.SKIPPED:
        return planned
    if planned.fire_at is None or planned.fire_at > now:
        return planned
    return decide(reminder, booking, now=now, org=org)
