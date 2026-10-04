"""The two researched calls, and every write this package makes.

The research names exactly two calls and this module models both:

``POST /org/concierge/routers/[routerSlug]/rest``
    "route/qualify a lead; returns ``routeId``, ``routingLink``,
    ``schedulingAllowed``, ``assignment{userId,type}``"

``POST /org/concierge/routing/[routeId]/schedule-simple``
    "commit the chosen slot; returns ``meetingId``"

Neither is actually issued. The endpoints are held as data in
:mod:`~dsr.concierge_router.vocabulary` and served at ``GET /api/wf-051/vocabulary``
so a client renders the mapping rather than compiling it, and so the response
shapes this module returns are the researched ones rather than ones invented
here.

Every write takes ``source`` as a keyword with no default. That is deliberate:
a write that forgot its source would produce an audit row naming nothing, and the
audit row naming a route the app stopped serving is a defect this project has
shipped before. Making the argument required makes the omission a ``TypeError``
at the call site rather than a silent unaudited row.

Every ``find`` here uses a dotted JSON path, so the collections stay
schema-flexible. A team adds a field to a router or a booking by writing it into
``data``, with no migration and no coordination with anyone.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from dsr.concierge_router import availability, inferences, nodes, rules, vocabulary
from dsr.concierge_router.errors import (
    AssignmentNotBookable,
    GuestIdentityRequired,
    GuestMismatch,
    MeetingTypeNotOffered,
    RouteConsumed,
    RouteNotFound,
    RouteNotSchedulable,
    RouterNotPublished,
    RouterUnavailable,
    SellerNotUsable,
    SlotNotOffered,
    UnknownRouter,
)

ROUTERS = "concierge_router"
BOOKINGS = "concierge_booking"
SELLERS = "concierge_seller"

#: The collection this package owns. Three, and each is JSON in ``data``.
OWNED_COLLECTIONS: tuple[str, ...] = (ROUTERS, BOOKINGS, SELLERS)

#: The guest fields a research-derived payload carries. Held here as the names
#: the engine writes, and sourced from the vocabulary so the payload and the
#: published field list cannot drift apart.
GUEST_FIELDS = tuple(str(field["name"]) for field in vocabulary.PRIMARY_GUEST_FIELDS)
REQUIRED_GUEST_FIELDS = tuple(
    str(field["name"]) for field in vocabulary.PRIMARY_GUEST_FIELDS if field["required"]
)


class ConciergeRouterEngine:
    """The researched flow, as rules and records.

    One class, holding the store. Everything it decides is a function of the
    records it reads, and everything it writes goes through the audited wrapper
    that ``RecordStore`` wraps.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- reads -------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every researched term, as data."""
        return vocabulary.published_vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every decision the research left open, with what it was chosen against."""
        return {"count": len(inferences.INFERENCES), "inferences": inferences.published()}

    def routers(self, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every router, newest first."""
        rows = self.store.list(ROUTERS, room_id=room_id, limit=limit)
        return [self._router_view(row) for row in rows]

    def require_router(self, router_id: str) -> dict[str, Any]:
        """One router, or refuse an id that names none."""
        record = self.store.get(router_id)
        if record is None or record.get("collection") != ROUTERS:
            raise UnknownRouter(f"no concierge router with id {router_id!r}")
        return self._router_view(record)

    def sellers(self, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every registered seller."""
        rows = self.store.list(SELLERS, room_id=room_id, limit=limit)
        return [self._seller_view(row) for row in rows]

    def bookings(
        self, room_id: str | None = None, limit: int = 100, *, source: str
    ) -> list[dict[str, Any]]:
        """Every booking, newest first, with any expired session already moved.

        ``source`` is required for the same reason as on :meth:`session`: listing
        bookings can write, because a due session is retired as it is read.
        """
        rows = self.store.list(BOOKINGS, room_id=room_id, limit=limit)
        return [self._booking_view(self._expire_if_due(row, source=source)) for row in rows]

    def session(self, route_id: str, *, source: str) -> dict[str, Any]:
        """One routing session, with its Time Elapsed timer applied if it is due.

        Reading a session is when expiry is computed. A session whose deadline has
        passed is moved to ``not_scheduled`` by a real audited write, so the state
        change is in the audit trail rather than a flag the caller happened to ask
        for. See the ``the-time-elapsed-timer-is-computed-on-read`` inference.

        ``source`` is required rather than defaulted even though this is a read,
        because the read may write. Naming a default here would let an audit row
        name a string that is not a route the app serves.
        """
        record = self.store.get(route_id)
        if record is None or record.get("collection") != BOOKINGS:
            raise RouteNotFound(f"no routing session with routeId {route_id!r}")
        moved = self._expire_if_due(record, source=source, room_id=record.get("room_id"))
        return self._booking_view(moved)

    def slots(self, route_id: str, *, source: str) -> dict[str, Any]:
        """The slot list the ``Display Calendar`` modal would render.

        "slot list rendered in the ``Display Calendar`` modal". Returned for a
        session only while it is still open, because a session whose timer expired
        is no longer offering a calendar to anybody.
        """
        session = self.session(route_id, source=source)
        return {
            "routeId": session["id"],
            "state": session["state"],
            "seller": session["seller_name"],
            "meetingTypes": session["meeting_types"],
            "timerExpiresAt": session["timer_expires_at"],
            "count": len(session["slots"]),
            "slots": session["slots"],
        }

    def summary(self) -> dict[str, Any]:
        """Counts by publish state, booking state and routing outcome."""
        routers = self.store.list(ROUTERS, limit=1000)
        bookings = self.store.list(BOOKINGS, limit=1000)
        return {
            "routers": len(routers),
            "sellers": len(self.store.list(SELLERS, limit=1000)),
            "bookings": len(bookings),
            "routers_by_state": _tally(routers, "publish_state"),
            "bookings_by_state": _tally(bookings, "state"),
            "bookings_by_outcome": _tally(bookings, "routing_outcome"),
            "deployed": sum(
                1
                for row in routers
                if (row.get("data") or {}).get("publish_state") == vocabulary.PUBLISHED
            ),
        }

    # -- declarations ------------------------------------------------------- #

    def create_router(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a router, refusing any shape the research says could not work."""
        declaration = nodes.validate_declaration(payload)
        data = {
            **declaration,
            "slug": declaration["slug"] or str(payload.get("id") or ""),
            "publish_state": vocabulary.require_publish_state(
                str(payload.get("publish_state") or vocabulary.DRAFT)
            ),
            "deployment": [
                vocabulary.require_deployment_surface(str(surface))
                for surface in (payload.get("deployment") or [])
            ],
            "enabled": bool(payload.get("enabled", True)),
        }
        record = self.store.create(ROUTERS, data, room_id=room_id, actor=actor, source=source)
        return self._router_view(record)

    def publish_router(
        self,
        router_id: str,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Change a router's publish state and deployment surfaces.

        "The router is published and deployed (embedded/deployed to web form,
        in-app button, or a router link)." Publishing is refused without at least
        one surface, because a router nobody can reach answers no inbound request
        and a published flag with no deployment is a claim about a deployment
        that does not exist.
        """
        record = self.store.get(router_id)
        if record is None or record.get("collection") != ROUTERS:
            raise UnknownRouter(f"no concierge router with id {router_id!r}")

        state = vocabulary.require_publish_state(
            str(payload.get("publish_state") or (record.get("data") or {}).get("publish_state"))
        )
        raw_surfaces = payload.get("deployment")
        if raw_surfaces is None:
            raw_surfaces = (record.get("data") or {}).get("deployment") or []
        if isinstance(raw_surfaces, str):
            raw_surfaces = [raw_surfaces]
        surfaces = [vocabulary.require_deployment_surface(str(surface)) for surface in raw_surfaces]
        if state == vocabulary.PUBLISHED and not surfaces:
            raise RouterNotPublished(
                "a published router must name at least one deployment surface: a web form, "
                "an in-app button, or a router link"
            )

        patched = self.store.update(
            router_id,
            {
                "publish_state": state,
                "deployment": surfaces,
                "enabled": bool(payload.get("enabled", True)),
            },
            actor=actor,
            source=source,
        )
        return self._router_view(patched)

    def register_seller(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Register a seller, their team, and their calendar's busy blocks.

        The researched availability step reads "Google/Outlook calendars", and the
        choice of a ``Display Calendar`` node is between Owner, Round-Robin and
        Individual user. A seller row is what all three resolve to, and what the
        busy blocks are read from, so it is declared here rather than inside a
        booking.
        """
        name = str(payload.get("name") or "").strip()
        if not name:
            raise SellerNotUsable(
                "a seller needs a name, because every researched assignment resolves to one"
            )
        data = {
            "name": name,
            "email": str(payload.get("email") or "").strip().lower(),
            "team": str(payload.get("team") or "").strip(),
            "calendar_connected": bool(payload.get("calendar_connected", True)),
            "busy": list(payload.get("busy") or []),
            "working_hours": payload.get("working_hours") or {},
            "round_robin_position": int(payload.get("round_robin_position", 0)),
        }
        record = self.store.create(SELLERS, data, room_id=room_id, actor=actor, source=source)
        return self._seller_view(record)

    # -- the first researched call ------------------------------------------ #

    def route(
        self,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Route and qualify a prospect, opening a session the second call commits.

        Modelled on "``POST /org/concierge/routers/[routerSlug]/rest`` - route/qualify
        a lead; returns ``routeId``, ``routingLink``, ``schedulingAllowed``,
        ``assignment{userId,type}``".

        The returned mapping uses the researched field names, so a client written
        against the vendor's sample works unchanged. The stored ``routingLink`` is
        the path only; see the ``routing-link-is-a-relative-path`` inference.
        """
        router_id = str(payload.get("router_id") or payload.get("router_slug") or "").strip()
        router = self.require_router(router_id)

        declared = router["nodes"]
        if router["publish_state"] != vocabulary.PUBLISHED:
            raise RouterNotPublished(
                f"router {router['slug']!r} is {router['publish_state']!r}; a router that "
                "is not published and deployed accepts no inbound request"
            )
        if not router.get("enabled", True):
            raise RouterUnavailable(f"router {router['slug']!r} is switched off")

        form_fields = payload.get("form_fields") or payload.get("fields") or {}
        if not isinstance(form_fields, dict):
            form_fields = {}

        guest = self._guest(form_fields)
        data_fields = self._map_fields(router["field_map"], form_fields)

        crm_records = rules.find_crm_records(self.store, guest["email"], room_id=room_id)
        chain = nodes.rule_nodes(declared)
        matched = rules.raise_if_no_rule(rules.matched_rule(chain, data_fields, crm_records))

        calendar = rules.calendar_for(matched, declared)
        if calendar is None:
            return self._record_decline(
                router, guest, matched, room_id=room_id, actor=actor, source=source
            )

        sellers = self.sellers(room_id=room_id)
        seller = self._resolve_seller(calendar, matched, crm_records, sellers)

        offered = [
            {"name": name, "duration_minutes": int(calendar.get("durations", {}).get(name, 30))}
            for name in calendar.get("meeting_types") or []
        ]
        now = vocabulary.utcnow()
        slots = availability.offer_slot(seller, offered, now=now)

        timer_minutes = int(calendar.get("timer_minutes") or vocabulary.DEFAULT_TIMER_MINUTES)

        data = {
            "router_id": router_id,
            "router_slug": router["slug"],
            "rule": str(matched.get("name") or matched["type"]),
            "routing_outcome": (
                vocabulary.CATCH_ALL_MATCHED
                if matched["type"] == vocabulary.CATCH_ALL
                else vocabulary.RULE_MATCHED
            ),
            "state": vocabulary.PENDING,
            "scheduling_allowed": True,
            "guest": guest,
            "data_fields": data_fields,
            "assignment": {
                "type": vocabulary.ASSIGNMENT_WIRE_TYPE.get(
                    str((calendar.get("assignment") or {}).get("type") or ""),
                    str((calendar.get("assignment") or {}).get("type") or ""),
                ),
                "user_id": str(seller.get("id") or ""),
                "policy": str((calendar.get("assignment") or {}).get("policy") or ""),
            },
            "seller_id": str(seller.get("id") or ""),
            "seller_name": str(seller.get("name") or ""),
            "meeting_types": [entry["name"] for entry in offered],
            "slots": slots,
            "routing_link": self._routing_link(router, guest),
            "timer_minutes": timer_minutes,
            "timer_expires_at": vocabulary.iso(now + timedelta(minutes=timer_minutes)),
            "routing_link_base": router.get("routing_link_base", ""),
            # The post-booking nodes and the redirect destination are copied onto
            # the session when the session is opened. A router may be edited after
            # a prospect arrives, and a booking must fire the nodes the router
            # carried when the prospect was routed, not the ones it carries now.
            "post_booking_nodes": [
                str(node.get("type"))
                for node in declared
                if node.get("type") in vocabulary.POST_BOOKING_NODE_TYPES
            ],
            "redirect_url": next(
                (
                    str(node.get("url") or "")
                    for node in declared
                    if node.get("type") == vocabulary.REDIRECT_TO
                ),
                "",
            ),
        }
        record = self.store.create(BOOKINGS, data, room_id=room_id, actor=actor, source=source)
        return self._route_response(self._booking_view(record))

    def _record_decline(
        self,
        router: dict[str, Any],
        guest: dict[str, Any],
        matched: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Record a rule that matched and then deliberately offered no calendar.

        The vocabulary names this ``rule_declined``. The session is still written,
        because the researched ``Not Scheduled`` path needs the prospect, and
        "the meeting will be considered not scheduled" is the researched outcome
        for a prospect who was offered no calendar.
        """
        now = vocabulary.utcnow()
        data = {
            "router_id": router["id"],
            "router_slug": router["slug"],
            "rule": str(matched.get("name") or matched["type"]),
            "routing_outcome": vocabulary.RULE_DECLINED,
            "state": vocabulary.NOT_OFFERED,
            "scheduling_allowed": False,
            "guest": guest,
            "data_fields": {},
            "assignment": {
                "type": vocabulary.ASSIGNMENT_WIRE_TYPE[vocabulary.OWNER_ASSIGNMENT],
                "user_id": "",
                "policy": "",
            },
            "seller_id": "",
            "seller_name": "",
            "meeting_types": [],
            "slots": [],
            "routing_link": self._routing_link(router, guest),
            "timer_minutes": 0,
            "timer_expires_at": vocabulary.iso(now),
            "notification": self._notification(guest, "no calendar was offered"),
        }
        record = self.store.create(BOOKINGS, data, room_id=room_id, actor=actor, source=source)
        return self._route_response(self._booking_view(record))

    # -- the second researched call ----------------------------------------- #

    def schedule_simple(
        self,
        route_id: str,
        payload: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Commit the chosen slot, returning a ``meetingId``.

        Modelled on "``POST /org/concierge/routing/[routeId]/schedule-simple`` -
        commit the chosen slot; returns ``meetingId``".

        Every refusal here is about the state the session is in. A booking against
        a session that has already been committed is a 409 rather than a 400,
        because the request is well formed and what it conflicts with is the state
        of the system.
        """
        record = self.store.get(route_id)
        if record is None or record.get("collection") != BOOKINGS:
            raise RouteNotFound(f"no routing session with routeId {route_id!r}")

        self._expire_if_due(record, source=source, room_id=room_id, actor=actor)
        session = self.store.get(route_id) or record
        state = str((session.get("data") or {}).get("state") or "")

        if not (session.get("data") or {}).get("scheduling_allowed", True):
            raise RouteNotSchedulable(
                f"route {route_id!r} answered schedulingAllowed false, so this prospect "
                "may not book"
            )
        if state == vocabulary.BOOKED:
            raise RouteConsumed(
                f"route {route_id!r} has already been committed; a second commit would put "
                "two prospects in one slot on one seller's calendar"
            )
        if state in vocabulary.TERMINAL_BOOKING_STATES:
            raise RouteConsumed(
                f"route {route_id!r} is {state!r}, which the researched Time Elapsed timer "
                "made final; the meeting will be considered not scheduled"
            )

        guest = payload.get("guest") or payload.get("primaryGuestDataFields") or {}
        if not isinstance(guest, dict):
            guest = {}
        self._require_same_guest((session.get("data") or {}).get("guest") or {}, guest, route_id)

        wanted = str(payload.get("start") or payload.get("slot") or "").strip()
        meeting_type = str(payload.get("meeting_type") or "").strip()
        self._require_offered_type(
            (session.get("data") or {}).get("meeting_types") or [], meeting_type, route_id
        )

        slot = self._require_offered_slot(
            (session.get("data") or {}).get("slots") or [], wanted, route_id
        )

        seller_id = str((session.get("data") or {}).get("seller_id") or "")
        seller = self._require_seller(seller_id, room_id=room_id)
        self._reserve(seller, slot, room_id=room_id, actor=actor, source=source)

        now = vocabulary.utcnow()
        meeting_id = f"mtg_{record['id'][:12]}"
        # Read from the session, which copied them when it was opened, rather than
        # re-reading the router. See the note at the write in ``route``.
        post_booking = [
            str(name) for name in (session.get("data") or {}).get("post_booking_nodes") or []
        ]
        patched = self.store.update(
            route_id,
            {
                "state": vocabulary.BOOKED,
                "meeting_id": meeting_id,
                "slot": slot,
                "meeting_type": meeting_type,
                "guest": {**((session.get("data") or {}).get("guest") or {}), **guest},
                "post_booking_nodes": post_booking,
                "redirect_url": str((session.get("data") or {}).get("redirect_url") or ""),
                "booked_at": vocabulary.iso(now),
            },
            actor=actor,
            source=source,
        )
        return {
            "meetingId": meeting_id,
            "routeId": route_id,
            "start": slot["start"],
            "end": slot["end"],
            "meetingType": meeting_type,
            "seller": {
                "userId": seller_id,
                "name": str(seller.get("name") or ""),
                "type": str((session.get("data") or {}).get("assignment", {}).get("type") or ""),
            },
            "postBookingNodes": post_booking,
            "redirectTo": str((patched.get("data") or {}).get("redirect_url") or ""),
            "primaryGuestDataFields": self.guest_payload(patched),
        }

    # -- the researched Time Elapsed timer ---------------------------------- #

    def expire(
        self,
        route_id: str,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Run the ``Display Calendar`` node's Time Elapsed timer now.

        "when it expires the meeting will be considered not scheduled, and you can
        notify your rep to follow up". Nothing polls for this; see the
        ``the-time-elapsed-timer-is-computed-on-read`` inference. A read that
        finds a due session performs the same transition this route performs.
        """
        record = self.store.get(route_id)
        if record is None or record.get("collection") != BOOKINGS:
            raise RouteNotFound(f"no routing session with routeId {route_id!r}")
        moved = self._expire_if_due(record, source=source, room_id=room_id, actor=actor, force=True)
        return self._booking_view(moved)

    def cancel(
        self,
        booking_id: str,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Cancel a booking, which runs the researched Not Scheduled path.

        Cancelling a booked meeting frees the seller's block again, so the slot
        returns to the availability engine and another prospect can take it.
        """
        record = self.store.get(booking_id)
        if record is None or record.get("collection") != BOOKINGS:
            raise RouteNotFound(f"no booking with id {booking_id!r}")
        data = record.get("data") or {}
        if str(data.get("state")) == vocabulary.BOOKED:
            self._release(
                str(data.get("seller_id") or ""),
                data.get("slot") or {},
                room_id=room_id,
                actor=actor,
                source=source,
            )
            record = (
                self.store.update(
                    booking_id,
                    {
                        "state": vocabulary.NOT_SCHEDULED,
                        "cancelled_at": vocabulary.iso(vocabulary.utcnow()),
                    },
                    actor=actor,
                    source=source,
                )
                or record
            )
        return self._booking_view(record)

    # -- helpers ------------------------------------------------------------ #

    def _expire_if_due(
        self,
        record: dict[str, Any],
        *,
        source: str,
        room_id: str | None = None,
        actor: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Move a pending session to ``not_scheduled`` once its timer is due.

        Idempotent, because the researched follow-up path must not run twice: a
        page refresh re-reads the session and must not notify a rep again.
        """
        data = record.get("data") or {}
        if str(data.get("state")) != vocabulary.PENDING:
            return record
        deadline = str(data.get("timer_expires_at") or "")
        if not deadline:
            return record
        try:
            due = vocabulary.parse_timestamp(deadline)
        except ValueError:
            return record
        if not force and vocabulary.utcnow() < due:
            return record
        return self._to_not_scheduled(record, room_id=room_id, actor=actor, source=source)

    def _to_not_scheduled(
        self,
        record: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Apply the researched Not Scheduled path exactly once.

        "``Not Scheduled`` paths run ``Assign To`` (distribute the prospect) and
        ``Send Notification`` (email or Slack)". Both run here, and neither is
        duplicated on a second read.
        """
        data = record.get("data") or {}
        guest = data.get("guest") or {}
        reassigned = self._reassign(data, room_id=room_id)
        patched = self.store.update(
            record["id"],
            {
                "state": vocabulary.NOT_SCHEDULED,
                "reassigned_to": reassigned,
                "notification": self._notification(
                    guest,
                    "the Display Calendar node's Time Elapsed timer expired",
                    recipient=str(reassigned.get("name") or data.get("seller_name") or ""),
                ),
                "not_scheduled_at": vocabulary.iso(vocabulary.utcnow()),
            },
            actor=actor,
            source=source,
        )
        return patched or record

    def _reassign(self, data: dict[str, Any], *, room_id: str | None) -> dict[str, Any]:
        """The researched ``Assign To``: distribute the prospect to a new seller.

        Under a round-robin assignment this advances the rotation, which is the
        researched "one path can serve every territory". The original assignment
        is left alone, so the trail shows who first received the lead.
        """
        sellers = self.sellers(room_id=room_id)
        connected = [seller for seller in sellers if seller.get("calendar_connected")]
        if not connected:
            return {}
        if str(data.get("assignment", {}).get("type")) == "round_robin":
            position = 0
            for seller in connected:
                position += int(seller.get("round_robin_position") or 0)
            return connected[position % len(connected)]
        return connected[0]

    def _notification(
        self,
        guest: dict[str, Any],
        reason: str,
        *,
        recipient: str = "",
    ) -> dict[str, Any]:
        """The researched ``Send Notification``, recorded rather than sent.

        The research names the channel and no delivery contract, and this product
        has no outbound transport. See the ``notification-is-recorded-not-sent``
        inference.
        """
        return {
            "channel": vocabulary.EMAIL_CHANNEL,
            "recipient": recipient or str(guest.get("email") or ""),
            "reason": reason,
            "at": vocabulary.iso(vocabulary.utcnow()),
            "sent": False,
        }

    def _guest(self, form_fields: dict[str, Any]) -> dict[str, Any]:
        """Read the researched ``primaryGuestDataFields`` out of the mapped fields.

        Both spellings are accepted, because a marketing webform posts whatever
        HTML ``name`` the page author wrote and Chili Piper's own payload is
        camelCase. Normalised to the researched names.
        """
        normalised = {
            vocabulary.normalise_field_name(key): value for key, value in form_fields.items()
        }
        guest: dict[str, Any] = {}
        for name, aliases in _GUEST_ALIASES.items():
            for alias in aliases:
                if alias in normalised and normalised[alias] not in (None, ""):
                    guest[name] = str(normalised[alias]).strip()
                    break
        if not guest.get("email"):
            raise GuestIdentityRequired(
                "the inbound request carries no guest email, so the CRM half of the "
                "researched rule evaluation has no object to match"
            )
        return guest

    def _map_fields(self, field_map: dict[str, str], form_fields: dict[str, Any]) -> dict[str, Any]:
        """Map webform fields onto Data Fields, as the ``Trigger`` node declares.

        "Admin maps the webform fields to Chili Piper **Data Fields** in the
        Trigger node". A form field the map does not name is dropped rather than
        passed through, because a rule reading a Data Field must read the value
        the admin mapped onto it, not whatever happened to share a name.
        """
        normalised = {
            vocabulary.normalise_field_name(key): value for key, value in form_fields.items()
        }
        return {
            target: normalised[field] for field, target in field_map.items() if field in normalised
        }

    def _require_same_guest(
        self, opened: dict[str, Any], sent: dict[str, Any], route_id: str
    ) -> None:
        """Refuse a commit from somebody other than the guest the session was opened for."""
        if not sent:
            return
        address = str(sent.get("email") or "").strip().lower()
        if address and address != str(opened.get("email") or "").strip().lower():
            raise GuestMismatch(
                f"route {route_id!r} was opened for {opened.get('email')!r}; a booking from "
                f"{address!r} is not the prospect who chose the slot"
            )

    def _require_offered_type(self, offered: list[Any], meeting_type: str, route_id: str) -> None:
        """Refuse a Meeting Type the matched ``Display Calendar`` did not offer."""
        names = [str(name) for name in offered]
        if meeting_type and meeting_type not in names:
            raise MeetingTypeNotOffered(
                f"route {route_id!r} offered {', '.join(names) or 'no meeting types'}; "
                f"{meeting_type!r} is not one of them"
            )

    def _require_offered_slot(
        self, offered: list[Any], wanted: str, route_id: str
    ) -> dict[str, Any]:
        """Refuse a start time the route never offered.

        "a time that was never offered is not merely out of stock: it is a time
        the seller's calendar was never shown to have free."
        """
        if not wanted:
            raise SlotNotOffered(f"route {route_id!r} needs a start time to commit")
        wanted_key = self._slot_key(wanted)
        for slot in offered:
            if isinstance(slot, dict) and self._slot_key(slot.get("start", "")) == wanted_key:
                return dict(slot)
        raise SlotNotOffered(
            f"route {route_id!r} never offered a slot starting {wanted!r}; the slot list "
            "is what the Display Calendar modal rendered"
        )

    @staticmethod
    def _slot_key(value: str) -> str:
        """A start time as an instant, so ``Z`` and ``+00:00`` compare equal."""
        try:
            return vocabulary.iso(vocabulary.parse_timestamp(value))
        except ValueError:
            return str(value or "").strip()

    def _resolve_seller(
        self,
        calendar: dict[str, Any],
        rule: dict[str, Any],
        crm_records: list[dict[str, Any]],
        sellers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Resolve the ``Display Calendar`` node's assignment to one seller.

        The three researched choices resolve three different ways, and that
        difference is the researched extensibility note: "Assignment Tables let one
        path serve every territory".
        """
        assignment = calendar.get("assignment") or {}
        kind = str(assignment.get("type") or "")

        if kind == vocabulary.INDIVIDUAL_USER_ASSIGNMENT:
            wanted = str(assignment.get("user_id") or calendar.get("user_id") or "")
            for seller in sellers:
                if wanted and wanted in {str(seller.get("id")), str(seller.get("email"))}:
                    return seller
            for seller in sellers:
                if str(seller.get("email") or "") == str(assignment.get("email") or ""):
                    return seller
            raise AssignmentNotBookable(
                f"the Display Calendar node names individual user {wanted!r}, and no "
                "registered seller answers to it"
            )

        if kind == vocabulary.OWNER_ASSIGNMENT:
            owner = rules._resolve_crm_value(rules.CRM_OWNER_ID, crm_records)
            if owner:
                for seller in sellers:
                    if str(owner) in {
                        str(seller.get("id")),
                        str(seller.get("email")),
                        str(seller.get("name")),
                    }:
                        return seller
            for seller in sellers:
                if seller.get("calendar_connected"):
                    return seller
            raise AssignmentNotBookable("no seller has a connected calendar to book on")

        connected = [seller for seller in sellers if seller.get("calendar_connected")]
        if not connected:
            raise AssignmentNotBookable(
                "the Display Calendar node chose Round-Robin, and no seller has a "
                "connected calendar"
            )
        position = sum(int(seller.get("round_robin_position") or 0) for seller in connected)
        return connected[position % len(connected)]

    def _require_seller(self, seller_id: str, *, room_id: str | None) -> dict[str, Any]:
        """The seller's row, or refuse an assignee with no readable calendar."""
        if not seller_id:
            raise AssignmentNotBookable("the route named no seller to book on")
        record = self.store.get(seller_id)
        if record is None or record.get("collection") != SELLERS:
            raise AssignmentNotBookable(
                f"the matched assignment named seller {seller_id!r}, which is not a "
                "registered seller"
            )
        if room_id is not None and record.get("room_id") not in (None, room_id):
            raise AssignmentNotBookable(
                f"seller {seller_id!r} belongs to another room, so its calendar is not "
                "readable from here"
            )
        return self._seller_view(record)

    def _reserve(
        self,
        seller: dict[str, Any],
        slot: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> None:
        """Add the committed slot to the seller's busy blocks.

        Written before the booking so the block and the booking cannot disagree:
        a second prospect who saw the same free slot then finds it occupied.
        """
        blocks = availability.book_block(seller, slot)
        self.store.update(
            seller["id"],
            {"busy": blocks},
            actor=actor,
            source=source,
        )

    def _release(
        self,
        seller_id: str,
        slot: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> None:
        """Remove a cancelled booking's block, so the slot returns to availability."""
        if not seller_id or not slot:
            return
        record = self.store.get(seller_id)
        if record is None or record.get("collection") != SELLERS:
            return
        data = record.get("data") or {}
        remaining = [
            block
            for block in (data.get("busy") or [])
            if self._slot_key((block or {}).get("start", ""))
            != self._slot_key(slot.get("start", ""))
        ]
        self.store.update(
            seller_id,
            {"busy": remaining},
            actor=actor,
            source=source,
        )

    def _routing_link(self, router: dict[str, Any], guest: dict[str, Any]) -> str:
        """The researched ``routingLink``, stored as a path.

        See the ``routing-link-is-a-relative-path`` inference: the sample's host is
        a tenant placeholder, so only the path is stored and the tenant base lives
        once on the router.
        """
        slug = router.get("slug") or router.get("id")
        return f"/concierge-router/{slug}/routing/{str(guest.get('email') or '').strip().lower()}"

    def guest_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        """The researched ``primaryGuestDataFields`` payload.

        The research names the payload and no field of it; see the
        ``primary-guest-data-fields`` inference.
        """
        data = record.get("data") or {}
        guest = data.get("guest") or {}
        assignment = data.get("assignment") or {}
        values = {
            "firstName": guest.get("firstName", ""),
            "lastName": guest.get("lastName", ""),
            "email": guest.get("email", ""),
            "company": guest.get("company", ""),
            "phone": guest.get("phone", ""),
            "ownerId": assignment.get("user_id", ""),
            "meetingType": data.get("meeting_type", ""),
        }
        return {name: values.get(name, "") for name in GUEST_FIELDS}

    # -- views -------------------------------------------------------------- #

    def _router_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "slug": str(data.get("slug") or record["id"]),
            "name": str(data.get("name") or ""),
            "nodes": list(data.get("nodes") or []),
            "field_map": dict(data.get("field_map") or {}),
            "catch_all": str(data.get("catch_all") or vocabulary.CATCH_ALL),
            "publish_state": str(data.get("publish_state") or vocabulary.DRAFT),
            "deployment": list(data.get("deployment") or []),
            "enabled": bool(data.get("enabled", True)),
            "routing_link_base": str(data.get("routing_link_base") or ""),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _seller_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "name": str(data.get("name") or ""),
            "email": str(data.get("email") or ""),
            "team": str(data.get("team") or ""),
            "calendar_connected": bool(data.get("calendar_connected", True)),
            "busy": list(data.get("busy") or []),
            "working_hours": dict(data.get("working_hours") or {}),
            "round_robin_position": int(data.get("round_robin_position") or 0),
        }

    def _booking_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "router_id": str(data.get("router_id") or ""),
            "router_slug": str(data.get("router_slug") or ""),
            "rule": str(data.get("rule") or ""),
            "routing_outcome": str(data.get("routing_outcome") or ""),
            "state": str(data.get("state") or ""),
            "scheduling_allowed": bool(data.get("scheduling_allowed", True)),
            "guest": dict(data.get("guest") or {}),
            "data_fields": dict(data.get("data_fields") or {}),
            "assignment": dict(data.get("assignment") or {}),
            "seller_id": str(data.get("seller_id") or ""),
            "seller_name": str(data.get("seller_name") or ""),
            "meeting_types": list(data.get("meeting_types") or []),
            "slots": list(data.get("slots") or []),
            "routing_link": str(data.get("routing_link") or ""),
            "routing_link_url": self._routing_link_url(data),
            "timer_minutes": int(data.get("timer_minutes") or 0),
            "timer_expires_at": str(data.get("timer_expires_at") or ""),
            "meeting_id": str(data.get("meeting_id") or ""),
            "slot": dict(data.get("slot") or {}),
            "meeting_type": str(data.get("meeting_type") or ""),
            "booked_at": str(data.get("booked_at") or ""),
            "notification": dict(data.get("notification") or {}),
            "reassigned_to": dict(data.get("reassigned_to") or {}),
            "post_booking_nodes": list(data.get("post_booking_nodes") or []),
            "redirect_to": str(data.get("redirect_url") or ""),
            "primaryGuestDataFields": self.guest_payload(record),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _routing_link_url(self, data: dict[str, Any]) -> str:
        """The full link, joining the router's tenant base to the stored path."""
        base = str(data.get("routing_link_base") or "").strip()
        path = str(data.get("routing_link") or "").strip()
        if not path:
            return ""
        return f"{base}{path}" if base else path

    def _route_response(self, booking: dict[str, Any]) -> dict[str, Any]:
        """The researched first-call response, under its researched field names.

        The sample is "``routeId``, ``routingLink``, ``schedulingAllowed``,
        ``assignment{userId,type}``". The camelCase keys are the vendor's wire
        spelling and are kept so a client written against the sample works
        unchanged. ``slots`` and the guest payload are additions this build needs,
        because the researched response carries no slot list and the ``Display
        Calendar`` modal has to render one.
        """
        return {
            "routeId": booking["id"],
            "routingLink": booking["routing_link_url"],
            "schedulingAllowed": booking["scheduling_allowed"],
            "assignment": {
                "userId": booking["assignment"].get("user_id", ""),
                "type": booking["assignment"].get("type", ""),
            },
            "state": booking["state"],
            "rule": booking["rule"],
            "routingOutcome": booking["routing_outcome"],
            "timerExpiresAt": booking["timer_expires_at"],
            "meetingTypes": booking["meeting_types"],
            "slots": booking["slots"],
            vocabulary.PRIMARY_GUEST_PAYLOAD: booking["primaryGuestDataFields"],
        }


_GUEST_ALIASES: dict[str, tuple[str, ...]] = {
    "firstName": vocabulary.GUEST_FIRST_NAME_ALIASES,
    "lastName": vocabulary.GUEST_LAST_NAME_ALIASES,
    "email": vocabulary.GUEST_EMAIL_ALIASES,
    "company": vocabulary.GUEST_COMPANY_ALIASES,
    "phone": vocabulary.GUEST_PHONE_ALIASES,
}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Count rows by one ``data`` key, naming every state with a zero."""
    counts: dict[str, int] = {}
    for row in rows:
        value = str((row.get("data") or {}).get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def slot_times(booking: dict[str, Any]) -> list[str]:
    """The starts a session offered, as the modal would list them."""
    return [str(slot.get("start") or "") for slot in booking.get("slots") or [] if slot]
