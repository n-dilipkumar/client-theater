"""The reassignment decision: one pure function, two entry points.

:func:`decide` answers "what would happen if this request were made" and raises
only for the things that are not decisions at all - a meeting that does not
exist, a host that does not exist, a meeting already cancelled. Everything the
researched *rules* have an opinion about comes back as a :class:`Decision` with
``allowed=False`` and a reason, so that:

* :meth:`ReassignEngine.preview` can return that verdict to a client, and
* :meth:`ReassignEngine.reassign` can raise it.

Both call this one function, so the answer a form is shown before the operator
commits is the answer the commit produces. A preview that could disagree with
the write would be worse than no preview.

Check order, and why it is this order
------------------------------------

A request can break more than one rule at once, and the first one reported is
the one a person fixes first. The order is: **what the request body claims**,
then **whether the caller may act at all**, then **who the new host is**, then
**whether they are free**. A body that names a different Meeting Type is wrong
no matter who called it, so it is reported before the add-on check rather than
after, and a host who is out of scope is reported before their calendar.

The refusal vocabulary is the whole of it. Each constant is one researched
sentence, and the reason text quotes that sentence rather than paraphrasing it,
because the reason is what a person reads when the workflow says no.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from dsr.reassign import distribution as dist
from dsr.reassign import webhooks
from dsr.reassign.errors import ReassignError
from dsr.reassign.vocabulary import (
    ASSIGNED,
    BOUNDS_ARE_IGNORED,
    EDITABLE_AND_LOCKED,
    EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN,
    LOCKED_FIELDS,
    SURFACES_REQUIRING_ADDON,
    AUTO_IS_ROUND_ROBIN_ONLY,
    AUTO_MODE,
    SPECIFIC_MODE,
    invite_for,
    mode_for_kind,
    require_assignment_kind,
    require_surface,
)

# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #

REASSIGNED = ASSIGNED
REFUSED_LOCKED_FIELD = "refused_locked_field"
REFUSED_ADDON_NOT_READY = "refused_addon_not_ready"
REFUSED_DISTRIBUTION_CONTEXT = "refused_distribution_context"
REFUSED_SAME_HOST = "refused_same_host"
REFUSED_NOT_IN_DISTRIBUTION = "refused_not_in_distribution"
REFUSED_HOST_UNAVAILABLE = "refused_host_unavailable"
REFUSED_NOT_ROUND_ROBIN = "refused_not_round_robin"
REFUSED_NO_ELIGIBLE_HOST = "refused_no_eligible_host"
REFUSED_INACTIVE_HOST = "refused_inactive_host"

#: Every outcome a decision can have. Published so a client can render a legend
#: without hard-coding the list and a new refusal cannot be invisible.
OUTCOMES: tuple[str, ...] = (
    REASSIGNED,
    REFUSED_LOCKED_FIELD,
    REFUSED_ADDON_NOT_READY,
    REFUSED_DISTRIBUTION_CONTEXT,
    REFUSED_SAME_HOST,
    REFUSED_NOT_IN_DISTRIBUTION,
    REFUSED_HOST_UNAVAILABLE,
    REFUSED_NOT_ROUND_ROBIN,
    REFUSED_NO_ELIGIBLE_HOST,
    REFUSED_INACTIVE_HOST,
)

#: The subset that is a refusal. A decision in this set writes nothing.
REFUSED: frozenset[str] = frozenset(o for o in OUTCOMES if o != REASSIGNED)


def is_refused(outcome: str) -> bool:
    return outcome in REFUSED


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


@dataclass
class Decision:
    """What a reassignment request resolves to, and everything behind it.

    Carries the whole trail rather than a verdict alone: the candidates and why
    each was excluded, the bounds that were ignored, the credit movement, and
    the webhooks that would fire. A rep who is told "no" is owed the reason, and
    a reviewer checking that the researched note is honoured can see the bounds
    that were bypassed without reading the code.
    """

    meeting_id: str
    outcome: str
    reason: str
    mode: str
    assign_to: dict[str, Any]
    from_host: dict[str, Any] | None = None
    to_host: dict[str, Any] | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    slot_changed: bool = False
    bounds: dict[str, Any] = field(default_factory=dict)
    availability: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    locked_fields: dict[str, Any] = field(default_factory=dict)
    add_on: dict[str, Any] = field(default_factory=dict)
    credit: dict[str, Any] = field(default_factory=dict)
    webhooks: list[str] = field(default_factory=list)
    invite_before: dict[str, Any] = field(default_factory=dict)
    invite_after: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome == REASSIGNED

    @property
    def refused(self) -> bool:
        """True for every outcome that is not the one that books the meeting.

        A property rather than a field so it cannot disagree with ``allowed``:
        the two are inverses, and a decision carrying both as stored values
        could be inconsistent.
        """
        return self.outcome in REFUSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "outcome": self.outcome,
            "refused": self.outcome in REFUSED,
            "reason": self.reason,
            "meeting_id": self.meeting_id,
            "mode": self.mode,
            "assign_to": dict(self.assign_to),
            "from_host": dict(self.from_host) if self.from_host else None,
            "to_host": dict(self.to_host) if self.to_host else None,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "slot_changed": self.slot_changed,
            "bounds": dict(self.bounds),
            "availability": dict(self.availability),
            "candidates": [dict(row) for row in self.candidates],
            "locked_fields": dict(self.locked_fields),
            "add_on": dict(self.add_on),
            "credit": dict(self.credit),
            "webhooks": list(self.webhooks),
            "invite_before": dict(self.invite_before),
            "invite_after": dict(self.invite_after),
        }


# --------------------------------------------------------------------------- #
# The request, normalised
# --------------------------------------------------------------------------- #


def _host_summary(host: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if host is None:
        return None
    return {"id": host.get("id"), "name": host.get("name"), "email": host.get("email")}


def _requested_locked_fields(
    request: Mapping[str, Any], meeting: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Compare every locked field the request bothers to mention.

    Returns ``(changed, checked)`` in that order: which fields were changed
    first, because that is what the caller branches on, and the per-field detail
    second.

    A field the request omits is not a change. A field it repeats *unchanged*
    is also not a change, and that matters: a scheduler that always echoes the
    context it reopened would otherwise be refused for sending back what it was
    given. Only a field that is present *and different* is a change.
    """
    from dsr.reassign.vocabulary import normalise_key

    checked: dict[str, Any] = {}
    changed: list[str] = []
    for name in LOCKED_FIELDS:
        if name not in request or request.get(name) in (None, ""):
            continue
        requested = normalise_key(request.get(name))
        current = normalise_key(meeting.get(name))
        differs = requested != current
        checked[name] = {"requested": requested, "meeting": current, "changed": differs}
        if differs:
            changed.append(name)
    return changed, checked


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def decide(
    meeting: Mapping[str, Any],
    distribution: Mapping[str, Any],
    hosts: list[Mapping[str, Any]],
    request: Mapping[str, Any],
    now: datetime,
    *,
    distribution_id: str = "",
    require_target: bool = True,
) -> Decision:
    """Decide one reassignment request. Pure; touches nothing.

    ``hosts`` is the whole host list, and the candidates are computed from it
    here rather than pre-filtered by the caller, so the "in scope but busy" and
    "out of scope" cases are separated by this function and by nothing else.

    ``require_target=False`` is for the availability listing, where the operator
    has not chosen a host yet and asking "who could take this?" must not be
    answered with an error about the host not being named. A commitment
    (``reassign``) always requires one.
    """
    meeting_id = str(meeting.get("id") or "")
    surface = require_surface(request.get("surface"), field="surface")
    assign_to_raw = request.get("assign_to") or {}
    if not isinstance(assign_to_raw, Mapping):
        raise ReassignError("assign_to must be an object with a kind of individual, team or distribution")
    kind = require_assignment_kind(assign_to_raw.get("kind"))
    mode = mode_for_kind(kind)
    assign_to = {"kind": kind, "id": str(assign_to_raw.get("id") or "").strip() or None}

    # The window: the meeting's own, unless the request opens a new slot. This
    # is the researched "Edit Meeting ... pick a new slot, and book" step; the
    # bounds it breaches are still ignored, because the note is unconditional.
    current_start = dist.parse_instant(meeting.get("starts_at"), field="meeting.starts_at")
    current_end = dist.parse_instant(meeting.get("ends_at"), field="meeting.ends_at")
    raw_start = request.get("starts_at")
    if raw_start in (None, ""):
        starts_at, ends_at = current_start, current_end
    else:
        starts_at = dist.parse_instant(raw_start, field="starts_at")
        raw_end = request.get("ends_at")
        ends_at = (
            dist.parse_instant(raw_end, field="ends_at")
            if raw_end not in (None, "")
            else starts_at + (current_end - current_start)
        )
    if ends_at <= starts_at:
        raise ReassignError("the reassigned slot ends at or before it starts")

    bounds = dist.bounds_summary(distribution, starts_at, now)
    changed_fields, locked_fields = _requested_locked_fields(request, meeting)
    current_host = _find_host(hosts, meeting.get("host_id"))
    add_on = _add_on_state(surface, request)

    base = dict(
        meeting_id=meeting_id,
        mode=mode,
        assign_to=assign_to,
        from_host=_host_summary(current_host),
        starts_at=dist.format_instant(starts_at),
        ends_at=dist.format_instant(ends_at),
        slot_changed=starts_at != current_start,
        bounds=bounds,
        locked_fields=locked_fields,
        add_on=add_on,
        webhooks=webhooks.events_for(meeting),
    )

    # 1. The request body. "You cannot change the Meeting Type or Workspace."
    if changed_fields:
        named = " and ".join(changed_fields)
        return Decision(
            **base,
            outcome=REFUSED_LOCKED_FIELD,
            reason=(
                f"cannot change {named}: {EDITABLE_AND_LOCKED} "
                f"This meeting is booked as {meeting.get('meeting_type')} in {meeting.get('workspace')}."
            ),
            candidates=_candidate_rows(hosts, meeting, distribution, starts_at, ends_at, kind=kind),
        )

    # 2. Whether the caller may act. "You must have ChiliCal's extension
    #    installed and be logged in there."
    if not add_on["ready"]:
        return Decision(
            **base,
            outcome=REFUSED_ADDON_NOT_READY,
            reason=(
                f"reassigning from {surface} needs the ChiliCal add-on: "
                f"{' and '.join(add_on['missing'])} "
                f"{'are' if len(add_on['missing']) > 1 else 'is'} missing. "
                f"{EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN}"
            ),
            candidates=_candidate_rows(hosts, meeting, distribution, starts_at, ends_at, kind=kind),
        )

    rows = _candidate_rows(hosts, meeting, distribution, starts_at, ends_at, kind=kind)

    # 3a. The Distribution context. The scheduler reopens the *same* one.
    if kind == "distribution" and assign_to["id"] not in (None, "", distribution_id, distribution.get("name")):
        return Decision(
            **base,
            outcome=REFUSED_DISTRIBUTION_CONTEXT,
            reason=(
                f"this meeting was booked from the {distribution.get('name')} distribution, and the "
                f"scheduler reopens that same Distribution rather than another one. "
                f"Reassignment will take into account ... the Distribution settings of the meeting booked."
            ),
            candidates=rows,
        )

    # 3b. The assignment granularity. A group assignment is the automatic path,
    #     and "Currently only supports reassigning host for round robin bookings".
    if mode == AUTO_MODE and not meeting.get("round_robin"):
        return Decision(
            **base,
            outcome=REFUSED_NOT_ROUND_ROBIN,
            reason=(
                f"choosing a {kind} means letting the host be auto-selected, and that is only supported "
                f"for round robin bookings. This meeting is not one; name the individual host instead. "
                f"({AUTO_IS_ROUND_ROBIN_ONLY})"
            ),
            candidates=rows,
        )

    if mode == SPECIFIC_MODE:
        # A picker that has not been told who to pick yet is not an error; it is
        # the "known and free" step before the choice is made. `require_target`
        # is what separates a listing from a commitment.
        if not assign_to.get("id") and not require_target:
            return Decision(
                **base,
                outcome=REFUSED_NO_ELIGIBLE_HOST,
                reason=(
                    "no host named yet; this is the candidate list, so nothing has been decided. "
                    "Name the individual, or ask for a team to have one auto-selected."
                ),
                candidates=rows,
            )
        target, refusal = _resolve_named_host(hosts, rows, assign_to, current_host)
    else:
        target, refusal = dist.auto_select(rows), None

    if refusal is not None:
        return Decision(**base, outcome=refusal[0], reason=refusal[1], candidates=rows)

    if target is None:
        return Decision(
            **base,
            outcome=REFUSED_NO_ELIGIBLE_HOST,
            reason=(
                f"no host in the {distribution.get('name')} distribution is free for this slot and in "
                f"scope for a {kind} assignment. Use Edit Meeting to pick a different time, or a "
                f"different individual."
            ),
            candidates=rows,
        )

    # 4. Availability. "If the target person is known and free." The bounds are
    #    already recorded as bypassed above; a calendar clash is not exempt.
    clashes = dist.conflicts_with(target, starts_at, ends_at)
    if clashes:
        where = clashes[0].get("label") or clashes[0]["starts_at"]
        return Decision(
            **base,
            outcome=REFUSED_HOST_UNAVAILABLE,
            reason=(
                f"{target.get('name')} is not free for this slot; it clashes with {where}. Reassignment "
                f"does not ignore a calendar conflict - only {BOUNDS_ARE_IGNORED}"
            ),
            to_host=_host_summary(target),
            availability={"free": False, "conflicts": clashes},
            candidates=rows,
        )

    invite_before = dict(meeting.get("invite") or {})
    invite_after = invite_for(target)
    credit = dist.move_credit(
        current_host or {},
        target,
        already_returned=bool(meeting.get("no_show_credit_back")),
    )

    return Decision(
        **base,
        outcome=REASSIGNED,
        reason=(
            f"{current_host.get('name') if current_host else 'the current host'} -> "
            f"{target.get('name')}, booked from the {distribution.get('name')} distribution"
            + (
                f". {BOUNDS_ARE_IGNORED} (bypassed: {', '.join(bounds['bypassed'])})"
                if bounds["bypassed"]
                else ""
            )
        ),
        to_host=_host_summary(target),
        availability={"free": True, "conflicts": []},
        candidates=rows,
        credit=credit,
        invite_before=invite_before,
        invite_after=invite_after,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _find_host(hosts: list[Mapping[str, Any]], host_id: Any) -> dict[str, Any] | None:
    wanted = str(host_id or "")
    for host in hosts:
        if str(host.get("id")) == wanted:
            return dict(host)
    return None


def _candidate_rows(
    hosts: list[Mapping[str, Any]],
    meeting: Mapping[str, Any],
    distribution: Mapping[str, Any],
    starts_at: datetime,
    ends_at: datetime,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    return dist.candidates(hosts, meeting, distribution, starts_at, ends_at, kind=kind)


def _add_on_state(surface: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """Is the ChiliCal add-on ready, for the one surface that needs it?

    Only ``chilical_home`` is checked, and the check is the quoted one: the
    extension has to be installed *and* logged in. A missing key is a missing
    requirement rather than a default-true, because defaulting it to ready would
    make the guard pass for every caller who forgets to send it.
    """
    required = surface in SURFACES_REQUIRING_ADDON
    extension = request.get("extension") or {}
    installed = bool(extension.get("installed")) if isinstance(extension, Mapping) else False
    logged_in = bool(extension.get("logged_in")) if isinstance(extension, Mapping) else False
    missing: list[str] = []
    if required:
        if not installed:
            missing.append("the extension")
        if not logged_in:
            missing.append("being logged in")
    return {
        "required": required,
        "installed": installed,
        "logged_in": logged_in,
        "ready": not missing,
        "missing": missing,
    }


def _resolve_named_host(
    hosts: list[Mapping[str, Any]],
    rows: list[dict[str, Any]],
    assign_to: Mapping[str, Any],
    current_host: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    """The named host, or the refusal naming it.

    Checksed in the order the research states them - "known and free" - with
    the two scope refusals separated so the message can say *which* of them
    applies. A host who is out of scope and also busy is reported as out of
    scope: widening the scope is the thing to try first, and telling someone
    they are busy when the real answer is that they are not on the distribution
    would send them to the wrong page.
    """
    wanted = assign_to.get("id")
    if not wanted:
        raise ReassignError(
            "assign_to.kind is 'individual', so assign_to.id must name the host to hand the booking to"
        )

    row = next((candidate for candidate in rows if str(candidate.get("id")) == str(wanted)), None)
    if row is None:
        raise ReassignError(
            f"host {wanted} does not exist; a reassignment books a new meeting for another user, so the "
            f"host has to be one this product already knows about"
        )

    if "already_the_host" in row["ineligible_because"]:
        return None, (
            REFUSED_SAME_HOST,
            f"{row.get('name')} already hosts this meeting. Reassign it to somebody else, or leave it alone.",
        )
    if "inactive" in row["ineligible_because"]:
        return None, (
            REFUSED_INACTIVE_HOST,
            f"{row.get('name')} is not taking bookings. Deactivate-then-reassign is refused rather than "
            f"quietly booking someone who has left the team.",
        )
    if "not_in_distribution" in row["ineligible_because"]:
        return None, (
            REFUSED_NOT_IN_DISTRIBUTION,
            f"{row.get('name')} is not in the Distribution settings of this meeting, and this distribution "
            f"does not allow rescheduling with any team member.",
        )

    target = _find_host(hosts, wanted)
    assert target is not None  # the row came from this list
    return target, None


def outcome_table() -> dict[str, Any]:
    """Each outcome, and the researched sentence that produces it.

    Served so a page can render a legend, and so a reviewer can check the whole
    refusal vocabulary against the four sources in one place rather than by
    reading six branches.
    """
    return {
        REASSIGNED: {
            "allowed": True,
            "quote": "When Reassigning a meeting with Chili Piper, you are booking a new meeting for another user.",
            "means": "the booking moves to the chosen host and the invite takes their details",
        },
        REFUSED_LOCKED_FIELD: {
            "allowed": False,
            "quote": EDITABLE_AND_LOCKED,
            "means": "the request tried to change the Meeting Type or the Workspace",
        },
        REFUSED_ADDON_NOT_READY: {
            "allowed": False,
            "quote": EXTENSION_MUST_BE_INSTALLED_AND_LOGGED_IN,
            "means": "the ChiliCal Home surface was used without the add-on installed and logged in",
        },
        REFUSED_DISTRIBUTION_CONTEXT: {
            "allowed": False,
            "quote": "Reassignment will take into account ... the Distribution settings of the meeting booked.",
            "means": "the request named a different Distribution; the scheduler reopens the same one",
        },
        REFUSED_SAME_HOST: {"allowed": False, "quote": None, "means": "the named host already has the booking"},
        REFUSED_INACTIVE_HOST: {"allowed": False, "quote": None, "means": "the named host is not taking bookings"},
        REFUSED_NOT_IN_DISTRIBUTION: {
            "allowed": False,
            "quote": "Reassignment will take into account ... the Distribution settings of the meeting booked.",
            "means": "the host is outside the distribution and the distribution does not widen to the team",
        },
        REFUSED_HOST_UNAVAILABLE: {
            "allowed": False,
            "quote": "If the target person is known and free, pick them and hit Reassign.",
            "means": "the new host's calendar already has a block over the slot",
        },
        REFUSED_NOT_ROUND_ROBIN: {
            "allowed": False,
            "quote": AUTO_IS_ROUND_ROBIN_ONLY,
            "means": "a team or distribution assignment asks for an auto-selected host, which needs round robin",
        },
        REFUSED_NO_ELIGIBLE_HOST: {
            "allowed": False,
            "quote": None,
            "means": "nothing in scope is free for the slot; use Edit Meeting to move the time",
        },
    }
