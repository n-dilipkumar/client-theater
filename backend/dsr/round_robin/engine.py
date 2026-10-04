"""The round robin flow end to end, over the audited store.

The researched flow, in its own order:

1. An admin creates a Team and a Round Robin distribution (Strict or Flexible)
   with per-member weights and credits.
2. A prospect opens the team link, or a backend calls the researched init call.
3. The distribution is evaluated and one combined availability window is returned.
4. The prospect books. The chosen member is credited and the distribution advances.
5. If a rep no-shows, an admin marks the prospect No-Show so credits can be
   credited back.

Collections
-----------

Five, all ordinary JSON in ``records.data``. No migration, no typed column, and a
team adding a field needs no coordination with anyone.

``round_robin_team``
    A team and its members. A member carries identity, licensing, calendar
    connection and busy blocks.
``round_robin_distribution``
    The reusable asset. Names a team, a mode, the weights and credits, the
    interval, and the rotation state: cursor, cycle and the credit ledger.
``round_robin_route``
    One opened evaluation. Holds the offered slots, the chosen member, the credit
    it will consume, and its state.
``round_robin_booking``
    The booked meeting. Names the distribution and the route, so a later
    reassignment reopens that same Distribution.
``round_robin_no_show``
    The admin's decision and the credit it returned. A record rather than a flag
    on the booking, because a rep's credit history is what an administrator reads.

The source argument
-------------------

Every writing method takes ``source`` as a required keyword. The route owns the
string, built from ``router.prefix``, so the audit row and the route table cannot
drift: a hardcoded source inside a domain method is a defect, and the same class
of bug has shipped here before, with a feature's audit log naming a path the app
had stopped serving. Making it required means omitting it is a ``TypeError`` at
the call site rather than an untraceable row in production.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from dsr.round_robin import credits as credit_rules, selection
from dsr.round_robin.availability import (
    combined_window,
    explain_missing,
    find_slot,
    free_members_at,
    normalise_interval,
)
from dsr.round_robin.errors import (
    NoEligibleMember,
    RoundRobinConflict,
    RoundRobinError,
    RoundRobinNotFound,
)
from dsr.round_robin.teams import (
    exclusion_reason,
    member_id,
    summarise,
    validate_team,
)
from dsr.round_robin.timeutil import parse, utcnow
from dsr.round_robin.vocabulary import (
    ALLOCATION,
    BOOKED,
    CANCELLED,
    CONFIRMED,
    ELIGIBLE,
    LINK_TYPES,
    NO_SHOW,
    OPEN,
    ROUND_ROBIN,
    require_link_type,
    require_mode,
    require_outcome,
)
from dsr.store import RecordStore

TEAM_COLLECTION = "round_robin_team"
DISTRIBUTION_COLLECTION = "round_robin_distribution"
ROUTE_COLLECTION = "round_robin_route"
BOOKING_COLLECTION = "round_robin_booking"
NO_SHOW_COLLECTION = "round_robin_no_show"
CREDIT_MOVEMENT_COLLECTION = "round_robin_credit_movement"

#: The default meeting length when a distribution names none.
DEFAULT_DURATION_MINUTES = 30

#: The link types this feature declares. Only RoundRobin: the other four route by
#: something this workflow does not consult, and declaring one would make the
#: link's own type a lie.
SUPPORTED_LINK_TYPES: frozenset[str] = frozenset({ROUND_ROBIN})


class RoundRobinEngine:
    """The workflow over one :class:`~dsr.store.RecordStore`.

    The store and the clock are the only constructor arguments. Building the
    engine per request rather than holding it on ``app.state`` keeps the clock a
    plain argument, which is what lets a test drive the whole workflow with rows
    of its own and a clock it controls, and leaves ``app.state`` a thing no
    feature has to touch.
    """

    def __init__(self, store: RecordStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or utcnow

    def _now(self) -> datetime:
        return self.clock()

    @staticmethod
    def _refuse_when_no_one_is_assignable(members: list[Mapping[str, Any]]) -> None:
        """Refuse when every member of the team is excluded from assignment.

        The researched licensing rule is a hard gate, and a team whose members are
        all unlicensed has nobody to route to. That is the researched Not
        Scheduled path with nothing left on it, so it is a refusal naming each
        excluded member rather than an empty result the caller has to interpret.

        An empty team is included: it can be declared but cannot answer, and
        saying "the team is empty" is more useful than "no slots are on offer".
        """
        if [member for member in members if exclusion_reason(member) is None]:
            return
        detail = (
            "Excluded: "
            + ", ".join(
                f"{member_id(member)} ({exclusion_reason(member)})"
                for member in members
                if exclusion_reason(member)
            )
            or "the team has no members"
        )
        raise NoEligibleMember(f"no member of this team can be assigned. {detail}")

    # ------------------------------------------------------------------ #
    # Teams
    # ------------------------------------------------------------------ #

    def create_team(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a team and its members. Step 1's first half.

        Validated in full before the row is created, so a member with no id, a
        duplicated member, or an unreadable busy block cannot leave a
        half-configured team behind that later looks usable.
        """
        body = validate_team(payload)
        return self.store.create(
            TEAM_COLLECTION,
            {**body, "note": str(payload.get("note") or "")},
            room_id=payload.get("room_id"),
            actor=actor,
            source=source,
        )

    def teams(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every declared team, newest first."""
        return self.store.list(TEAM_COLLECTION, room_id=room_id, limit=limit)

    def get_team(self, team_id: str) -> dict[str, Any] | None:
        return self.store.get(team_id)

    def require_team(self, team_id: str) -> dict[str, Any]:
        team = self.get_team(team_id)
        if team is None:
            raise RoundRobinNotFound(f"team {team_id} not found")
        return team

    def team_view(self, team: Mapping[str, Any]) -> dict[str, Any]:
        """A team with its members annotated by eligibility.

        An unlicensed or unconnected member is listed rather than hidden, with the
        reason beside it. "this prospect cannot reach Sam" is the thing an admin
        needs to see, and a member who silently vanishes from the list is the
        hardest version of that bug to diagnose.
        """
        members = list(team.get("data", {}).get("members") or [])
        return {
            **dict(team),
            "members": [
                {
                    **member,
                    "eligible": exclusion_reason(member) is None,
                    "excluded_reason": exclusion_reason(member),
                }
                for member in members
            ],
            "eligibility": summarise(members),
        }

    # ------------------------------------------------------------------ #
    # Distributions
    # ------------------------------------------------------------------ #

    def create_distribution(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a Round Robin distribution. Step 1's second half.

        The distribution is the reusable asset, so it is validated against its
        team rather than against a snapshot: a team that gains a member before the
        distribution is used must be visible to it. The mode is required, the link
        type is refused unless it is RoundRobin, and the weights are normalised
        onto the team's members.
        """
        mode = require_mode(payload.get("mode"))
        link_type = str(payload.get("link_type") or ROUND_ROBIN)
        if link_type not in SUPPORTED_LINK_TYPES:
            raise RoundRobinError(
                f"link type {link_type!r} is not routed by this workflow; it routes {ROUND_ROBIN!r}"
            )
        require_link_type(link_type)

        team_ref = str(payload.get("team_ref") or payload.get("team_id") or "")
        if not team_ref:
            raise RoundRobinError("team_ref is required; which team does this distribution rotate?")
        team = self.require_team(team_ref)

        declared = payload.get("members")
        weights: dict[str, dict[str, Any]] = {}
        if declared is not None:
            if not isinstance(declared, list):
                raise RoundRobinError("members must be a list of {member_id, weight} objects")
            for entry in declared:
                if not isinstance(entry, Mapping):
                    raise RoundRobinError(f"member entry is not an object: {entry!r}")
                identifier = str(entry.get("member_id") or entry.get("id") or "")
                if not identifier:
                    raise RoundRobinError(f"member entry has no member_id: {entry!r}")
                try:
                    weight = float(entry.get("weight", 1.0))
                except (TypeError, ValueError) as exc:
                    raise RoundRobinError(
                        f"member {identifier} has a weight that is not a number: "
                        f"{entry.get('weight')!r}"
                    ) from exc
                if weight < 0:
                    raise RoundRobinError(f"member {identifier} has a negative weight: {weight}")
                weights[identifier] = {"member_id": identifier, "weight": weight}

        team_members = list(team.get("data", {}).get("members") or [])
        unknown = [
            identifier
            for identifier in weights
            if identifier not in {member_id(m) for m in team_members}
        ]
        if unknown:
            raise RoundRobinError(
                f"distribution declares weights for members not on team {team_ref}: "
                + ", ".join(sorted(unknown))
            )

        interval = normalise_interval(
            payload.get("interval"), default_minutes=DEFAULT_DURATION_MINUTES
        )

        record = self.store.create(
            DISTRIBUTION_COLLECTION,
            {
                "name": str(
                    payload.get("name") or f"Round Robin on {team.get('data', {}).get('name')}"
                ),
                "link_type": link_type,
                "mode": mode,
                "team_ref": team_ref,
                "team_name": str(team.get("data", {}).get("name") or ""),
                "members": [weights[key] for key in sorted(weights)],
                "interval": interval,
                "credit_back_on_no_show": bool(payload.get("credit_back_on_no_show", False)),
                "cursor": 0,
                "cycle": 0,
                "credits": credit_rules.empty_ledger(),
            },
            room_id=payload.get("room_id"),
            actor=actor,
            source=source,
        )
        return record

    def distributions(
        self,
        *,
        room_id: str | None = None,
        mode: str | None = None,
        team_ref: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every declared distribution, newest first."""
        records = self.store.list(DISTRIBUTION_COLLECTION, room_id=room_id, limit=limit)
        if mode is not None:
            records = [record for record in records if record["data"].get("mode") == mode]
        if team_ref is not None:
            records = [record for record in records if record["data"].get("team_ref") == team_ref]
        return records

    def get_distribution(self, distribution_id: str) -> dict[str, Any] | None:
        return self.store.get(distribution_id)

    def require_distribution(self, distribution_id: str) -> dict[str, Any]:
        record = self.get_distribution(distribution_id)
        if record is None:
            raise RoundRobinNotFound(f"distribution {distribution_id} not found")
        return record

    def distribution_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A distribution with its ledger read against its own team.

        The ledger is stored keyed by member id, so it is rendered against the
        team's member list. A ledger entry for a member who has left the team
        would otherwise be invisible, and that is exactly the entry an
        administrator is looking for when asking whether a rotation is fair.
        """
        body = record.get("data", {})
        team = self.get_team(str(body.get("team_ref") or ""))
        members = list(team.get("data", {}).get("members") or []) if team else []
        return {
            **dict(record),
            "ledger": selection.ledger(body, members),
            "credit_totals": credit_rules.totals(body.get("credits") or {}),
            "eligibility": summarise(members),
            "member_weights": selection.weight_scores(
                body, members, {"free_minutes_by_member": {}}
            ),
        }

    # ------------------------------------------------------------------ #
    # Step 2 and 3: evaluate the distribution
    # ------------------------------------------------------------------ #

    def _window_for(
        self, distribution: Mapping[str, Any], payload: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        """The combined window for this evaluation.

        ``distribution`` is the record's ``data`` body, not the envelope. Reading
        a nested ``data`` key here would silently fall through to an empty team
        reference on every call, so the reference is read off the body directly.

        The distribution's interval is the default and a caller's interval
        overrides it, which is what the researched init payload implies: it sends
        its own ``interval`` alongside the link.
        """
        team = self.require_team(str(distribution.get("team_ref") or ""))
        members = list(team.get("data", {}).get("members") or [])
        interval = normalise_interval(
            payload.get("interval") or distribution.get("interval"),
            default_minutes=DEFAULT_DURATION_MINUTES,
        )
        window = combined_window(
            members,
            start=parse(interval["start"]),
            end=parse(interval["end"]),
            duration_minutes=interval["duration_minutes"],
            min_notice_minutes=interval["min_notice_minutes"],
            now=now,
        )
        window["interval"] = interval
        window["team_ref"] = team["id"]
        return window

    def check(
        self, distribution_id: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """What would happen. Steps 2 to 4, with no write at all.

        The read-only half of ``init-simple``, for a form that wants to say "this
        reaches Sam" before the prospect commits. The answer is identical to what
        ``init-simple`` would produce, because both call the same selection and the
        same calendar arithmetic. Only the consequences differ.
        """
        distribution = self.require_distribution(distribution_id)
        body = distribution["data"]
        now = self._now()
        window = self._window_for(body, dict(payload or {}), now)
        members = list(
            (self.get_team(str(window["team_ref"])) or {}).get("data", {}).get("members") or []
        )
        require_outcome(ALLOCATION if window["slots"] else ELIGIBLE)
        if not window["slots"]:
            # Same gate as init_simple: a team with nobody assignable is a
            # configuration problem, not a busy week, and the preview has to say
            # so rather than report an empty window as though the team were simply
            # fully booked.
            self._refuse_when_no_one_is_assignable(members)
            return {
                "distribution_id": distribution_id,
                "mode": body.get("mode"),
                "outcome": ELIGIBLE,
                "window": window,
                "chosen": None,
                "eligibility": summarise(members),
            }
        chosen = selection.select_member(
            body, members, window, free_member_ids=window["slots"][0]["free_member_ids"]
        )
        return {
            "distribution_id": distribution_id,
            "mode": body.get("mode"),
            "outcome": ALLOCATION,
            "window": window,
            "chosen": chosen,
            "eligibility": summarise(members),
        }

    def init_simple(
        self,
        distribution_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 2 to 4: evaluate the distribution and open a route.

        The researched init call sends ``{"link": {...}, "interval": {...}}`` and
        answers with a routing id plus the offered start times. This writes the
        route, the chosen member and the credit it will consume, and records the
        decision, so the answer and the reason for it land in one transaction.

        A distribution whose team has nobody assignable is refused with
        :class:`NoEligibleMember`. A distribution whose team is fully booked out
        for the window succeeds with no slots: that is the researched Not
        Scheduled path, and it is a legitimate answer rather than an error.
        """
        distribution = self.require_distribution(distribution_id)
        body = distribution["data"]
        now = self._now()
        window = self._window_for(body, dict(payload or {}), now)
        team = self.require_team(str(window["team_ref"]))
        members = list(team.get("data", {}).get("members") or [])
        guest_email = str((payload or {}).get("guestEmail") or "").strip()
        if not guest_email:
            raise RoundRobinError("guestEmail is required; which prospect is booking?")

        require_outcome(ALLOCATION if window["slots"] else ELIGIBLE)
        chosen = None
        route: dict[str, Any] | None = None
        if window["slots"]:
            chosen = selection.select_member(
                body, members, window, free_member_ids=window["slots"][0]["free_member_ids"]
            )
            route = self.store.create(
                ROUTE_COLLECTION,
                {
                    "distribution_ref": distribution_id,
                    "team_ref": window["team_ref"],
                    "mode": body.get("mode"),
                    "state": OPEN,
                    "guest_email": guest_email,
                    "chosen_member_id": chosen["member_id"],
                    "offered_slots": window["slots"],
                    "slot_count": len(window["slots"]),
                    "interval": window["interval"],
                    "credit_pending": credit_rules.CREDIT_PER_BOOKING,
                    "scores": chosen["scores"],
                    "rule": chosen["rule"],
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        else:
            # Nobody on this team can be assigned at all, which is a configuration
            # the admin has to fix rather than a busy week. Checked here rather
            # than left to `select_member` because a team of unlicensed members
            # produces an empty window, and an empty window alone would answer
            # "nobody is free" rather than the researched Not Scheduled path with
            # nothing left on it.
            self._refuse_when_no_one_is_assignable(members)

        return {
            "routing_id": route["id"] if route else None,
            "route": route,
            "distribution_id": distribution_id,
            "mode": body.get("mode"),
            "outcome": ALLOCATION if route else ELIGIBLE,
            "start_times": [slot["start_at"] for slot in window["slots"]],
            "slots": window["slots"],
            "window": window,
            "chosen": chosen,
            "eligibility": summarise(members),
            "room_id": room_id,
        }

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #

    def routes(
        self,
        *,
        room_id: str | None = None,
        distribution_id: str | None = None,
        state: str | None = None,
        member_id_filter: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every opened evaluation, newest first."""
        records = self.store.list(ROUTE_COLLECTION, room_id=room_id, limit=limit)
        if distribution_id is not None:
            records = [r for r in records if r["data"].get("distribution_ref") == distribution_id]
        if state is not None:
            records = [r for r in records if r["data"].get("state") == state]
        if member_id_filter is not None:
            records = [r for r in records if r["data"].get("chosen_member_id") == member_id_filter]
        return records

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        return self.store.get(route_id)

    def require_route(self, route_id: str) -> dict[str, Any]:
        record = self.get_route(route_id)
        if record is None:
            raise RoundRobinNotFound(f"routing session {route_id} not found")
        return record

    # ------------------------------------------------------------------ #
    # Step 4: book
    # ------------------------------------------------------------------ #

    def book(
        self,
        route_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Book one of the offered slots. Step 4.

        The researched schedule-simple payload is ``{startTime, guestEmail}`` and
        the route id travels in the path. Three refusals guard it, and a fourth is
        the union's re-check:

        * a session that has already been booked;
        * a ``startTime`` the session never offered;
        * a ``guestEmail`` that is not the one the session was opened for;
        * a chosen member who is no longer free at that instant, because another
          booking landed since the route opened.

        The re-check advances the distribution to the next eligible member rather
        than refusing, because the union offered the slot on the strength of that
        member being free then, and a busy rep is not the prospect's error.

        The booking, the consumed credit, the route's transition and the
        distribution's advance land in one transaction, because a booking that
        exists beside an open route is the state that lets one slot be taken
        twice.
        """
        route = self.require_route(route_id)
        route_body = route["data"]
        if route_body.get("state") != OPEN:
            raise RoundRobinConflict(
                f"routing session {route_id} is {route_body.get('state')}, not {OPEN}; "
                "it cannot be booked again"
            )
        guest_email = str(route_body.get("guest_email") or "")
        payload = dict(payload or {})
        start_time = str(payload.get("startTime") or payload.get("start_at") or "").strip()
        if not start_time:
            raise RoundRobinError("startTime is required; which slot did the prospect choose?")
        offered = payload.get("guestEmail") or payload.get("guest_email")
        if offered and str(offered).strip().lower() != guest_email.lower():
            raise RoundRobinError(
                f"this routing session was opened for {guest_email}, not {offered}"
            )

        slot = find_slot({"slots": route_body.get("offered_slots") or []}, start_time)
        if slot is None:
            raise RoundRobinError(
                explain_missing({"slots": route_body.get("offered_slots") or []}, start_time)
            )

        distribution = self.require_distribution(str(route_body.get("distribution_ref") or ""))
        distribution_body = distribution["data"]
        team = self.require_team(
            str(route_body.get("team_ref") or distribution_body.get("team_ref"))
        )
        members = list(team.get("data", {}).get("members") or [])
        window = self._window_for(
            distribution_body, {"interval": route_body.get("interval")}, self._now()
        )

        chosen_id = str(route_body.get("chosen_member_id") or "")
        free_now = free_members_at(members, parse(start_time), parse(slot["end_at"]))
        rechecked = False
        if chosen_id not in free_now:
            rechecked = True
            replacement = selection.next_candidate(
                distribution_body,
                members,
                window,
                free_member_ids=free_now,
                after=chosen_id,
            )
            chosen_id = replacement["member_id"]
            chosen = replacement
        else:
            chosen = next(
                (member for member in members if member_id(member) == chosen_id),
                {"member_id": chosen_id, "name": None, "email": None},
            )

        # One transaction: the booking, the consumed credit, the route's
        # transition and the distribution's advance commit together or not at all.
        with self.store.db.transaction(actor=actor, source=source) as writer:
            booking = writer.create(
                BOOKING_COLLECTION,
                {
                    "route_ref": route_id,
                    "distribution_ref": distribution["id"],
                    "team_ref": team["id"],
                    "member_id": chosen_id,
                    "member_name": chosen.get("name"),
                    "guest_email": guest_email,
                    "start_at": slot["start_at"],
                    "end_at": slot["end_at"],
                    "duration_minutes": slot.get("duration_minutes", DEFAULT_DURATION_MINUTES),
                    "status": CONFIRMED,
                    "credit_consumed": credit_rules.CREDIT_PER_BOOKING,
                    "rechecked_at_booking": rechecked,
                },
                room_id=room_id,
            )
            ledger = credit_rules.apply_consumption(
                distribution_body.get("credits") or {}, chosen_id, booking_id=booking["id"]
            )
            advanced = selection.advance(distribution_body, members)
            writer.update(
                distribution["id"],
                {"credits": ledger, "cursor": advanced["cursor"], "cycle": advanced["cycle"]},
            )
            writer.update(
                route_id,
                {"state": BOOKED, "booked_member_id": chosen_id, "booking_ref": booking["id"]},
            )
            writer.create(
                CREDIT_MOVEMENT_COLLECTION,
                {
                    "direction": credit_rules.CREDIT_CONSUMED,
                    "member_id": chosen_id,
                    "distribution_ref": distribution["id"],
                    "booking_ref": booking["id"],
                    "amount": credit_rules.CREDIT_PER_BOOKING,
                    "room_id": room_id,
                },
                room_id=room_id,
            )

        return {
            "booking": booking,
            "booking_id": booking["id"],
            "route_id": route_id,
            "distribution_id": distribution["id"],
            "member_id": chosen_id,
            "credited": chosen_id,
            "rechecked_at_booking": rechecked,
            "cursor": advanced["cursor"],
            "cycle": advanced["cycle"],
            "credit": credit_rules.movement(credit_rules.CREDIT_CONSUMED, booking_id=booking["id"]),
        }

    # ------------------------------------------------------------------ #
    # Step 5: no-show
    # ------------------------------------------------------------------ #

    def mark_no_show(
        self,
        booking_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Mark the prospect No-Show and credit the member back. Step 5.

        Admin-triggered, but the distribution's ``credit_back_on_no_show`` flag
        makes the return a standing rule, so a distribution with the flag off
        refuses rather than quietly marking the booking without returning the
        credit: an admin who pressed the button expects one or the other.
        """
        booking = self.store.get(booking_id)
        if booking is None:
            raise RoundRobinNotFound(f"booking {booking_id} not found")
        body = booking["data"]
        # The specific message for a repeat, checked before the general status
        # guard. "already been marked No-Show" is what an admin pressing the
        # button twice needs to read; the status guard would tell them the
        # booking "is no_show, not confirmed", which is true and unhelpful.
        if body.get("status") == NO_SHOW:
            raise RoundRobinConflict(f"booking {booking_id} has already been marked No-Show")
        if body.get("status") != CONFIRMED:
            raise RoundRobinConflict(
                f"booking {booking_id} is {body.get('status')}, not {CONFIRMED}; it cannot be "
                "marked No-Show"
            )
        existing = self.store.list(NO_SHOW_COLLECTION, limit=1000)
        if any(entry["data"].get("booking_ref") == booking_id for entry in existing):
            # A no-show row without the status change. Reachable only if the two
            # writes ever diverge, and it is the guard that keeps that from
            # returning a second credit.
            raise RoundRobinConflict(f"booking {booking_id} already has a recorded No-Show")

        distribution = self.require_distribution(str(body.get("distribution_ref") or ""))
        distribution_body = distribution["data"]
        member = str(body.get("member_id") or "")
        note = str((payload or {}).get("note") or (payload or {}).get("reason") or "")

        # Refuse before writing anything when the return is not permitted, so a
        # no-show that returns nothing cannot exist.
        ledger = credit_rules.apply_return(
            distribution_body.get("credits") or {},
            member,
            booking_id=booking_id,
            distribution=distribution_body,
        )

        with self.store.db.transaction(actor=actor, source=source) as writer:
            no_show = writer.create(
                NO_SHOW_COLLECTION,
                {
                    "booking_ref": booking_id,
                    "distribution_ref": distribution["id"],
                    "member_id": member,
                    "credit_returned": credit_rules.CREDIT_PER_BOOKING,
                    "note": note,
                },
                room_id=room_id,
            )
            writer.update(distribution["id"], {"credits": ledger})
            writer.update(booking_id, {"status": NO_SHOW, "no_show_ref": no_show["id"]})
            writer.create(
                "round_robin_credit_movement",
                {
                    "direction": credit_rules.CREDIT_RETURNED,
                    "member_id": member,
                    "distribution_ref": distribution["id"],
                    "booking_ref": booking_id,
                    "amount": credit_rules.CREDIT_PER_BOOKING,
                    "room_id": room_id,
                },
                room_id=room_id,
            )

        return {
            "no_show": no_show,
            "booking_id": booking_id,
            "member_id": member,
            "credited_back": credit_rules.CREDIT_PER_BOOKING,
            "credit": credit_rules.movement(credit_rules.CREDIT_RETURNED, booking_id=booking_id),
        }

    # ------------------------------------------------------------------ #
    # Bookings
    # ------------------------------------------------------------------ #

    def bookings(
        self,
        *,
        room_id: str | None = None,
        member_id_filter: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every booking, newest first."""
        records = self.store.list(BOOKING_COLLECTION, room_id=room_id, limit=limit)
        if member_id_filter is not None:
            records = [r for r in records if r["data"].get("member_id") == member_id_filter]
        if status is not None:
            records = [r for r in records if r["data"].get("status") == status]
        return records

    def get_booking(self, booking_id: str) -> dict[str, Any] | None:
        return self.store.get(booking_id)

    def cancel_booking(
        self,
        booking_id: str,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Cancel a booking, releasing its slot.

        A cancelled booking returns no credit. The research describes a
        credit-back for a no-show, not for a cancellation, and a rep who was
        booked and then cancelled did attend to nothing but also did not fail to
        attend, so treating the two alike would invent a rule the research does
        not state.
        """
        booking = self.store.get(booking_id)
        if booking is None:
            raise RoundRobinNotFound(f"booking {booking_id} not found")
        if booking["data"].get("status") != CONFIRMED:
            raise RoundRobinConflict(
                f"booking {booking_id} is {booking['data'].get('status')}, not {CONFIRMED}; "
                "it cannot be cancelled"
            )
        updated = self.store.update(booking_id, {"status": CANCELLED}, actor=actor, source=source)
        return updated

    def no_shows(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every recorded no-show, newest first."""
        return self.store.list(NO_SHOW_COLLECTION, room_id=room_id, limit=limit)

    def credit_movements(
        self, *, distribution_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Every credit that moved, in either direction."""
        records = self.store.list(CREDIT_MOVEMENT_COLLECTION, limit=limit)
        if distribution_id is not None:
            records = [r for r in records if r["data"].get("distribution_ref") == distribution_id]
        return records

    # ------------------------------------------------------------------ #
    # Reads for the page
    # ------------------------------------------------------------------ #

    def catalog(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Teams and distributions, with the eligibility that decides assignment.

        The licensing rule is a hard gate, so a reader needs to see which members
        are on which team and why a member cannot be reached.
        """
        teams = self.teams(room_id=room_id, limit=200)
        return {
            "link_types": [dict(entry) for entry in LINK_TYPES],
            "supported_link_types": sorted(SUPPORTED_LINK_TYPES),
            "teams": [self.team_view(team) for team in teams],
            "distributions": [
                self.distribution_view(record)
                for record in self.distributions(room_id=room_id, limit=200)
            ],
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the filters return.

        Computed over the same rows the lists would return, so a room-scoped
        total above an unscoped list cannot be misread as a product-wide one.
        """
        routes = self.routes(room_id=room_id, limit=1000)
        bookings = self.bookings(room_id=room_id, limit=1000)
        no_shows = self.no_shows(room_id=room_id, limit=1000)
        teams = self.teams(room_id=room_id, limit=1000)
        distributions = self.distributions(room_id=room_id, limit=1000)
        by_mode: dict[str, int] = {}
        for record in distributions:
            name = str(record["data"].get("mode") or "unset")
            by_mode[name] = by_mode.get(name, 0) + 1
        excluded = 0
        for team in teams:
            members = list(team.get("data", {}).get("members") or [])
            excluded += len(members) - len([m for m in members if exclusion_reason(m) is None])
        return {
            "teams": len(teams),
            "distributions": len(distributions),
            "distributions_by_mode": by_mode,
            "routes": len(routes),
            "routes_open": sum(1 for r in routes if r["data"].get("state") == OPEN),
            "bookings": len(bookings),
            "bookings_confirmed": sum(1 for b in bookings if b["data"].get("status") == CONFIRMED),
            "no_shows": len(no_shows),
            "excluded_members": excluded,
            "collections": {
                "team": TEAM_COLLECTION,
                "distribution": DISTRIBUTION_COLLECTION,
                "route": ROUTE_COLLECTION,
                "booking": BOOKING_COLLECTION,
                "no_show": NO_SHOW_COLLECTION,
                "credit_movement": CREDIT_MOVEMENT_COLLECTION,
            },
        }
