"""The engine: the researched user flow, in order, over the audited store.

Steps 1 to 5 of ``WF-053.md``, and the whole of them:

    1. Admin creates an Ownership scheduling link     -> :meth:`OwnershipEngine.create_link`
    2. A prospect lands, or a backend calls the API    -> :meth:`OwnershipEngine.init_simple`
    3. The owner is resolved at booking time           -> :meth:`OwnershipEngine.resolve`
    4. Availability is read, the prospect books       -> :meth:`OwnershipEngine.book`
    5. A CRM Ownership rule routes to that rep        -> :meth:`OwnershipEngine.rules` on the link

And the researched alternative at step 5, which is a rule chain rather than a
direct hop, is :meth:`OwnershipEngine.route` driving :mod:`dsr.ownership_routing.rules`.

Three design points worth stating outright, because all three are constraints
rather than choices:

**``source`` is a required keyword on every write.** The audit row must name the
route that actually served the write, so the route builds it from ``router.prefix``
and passes it down. A domain method that hardcoded a URL string would let the audit
log name a path the app had stopped serving - that defect has shipped in this
codebase before, and making the parameter required is what stops it regressing
silently.

**The two researched calls are two methods, not one.** ``init-simple`` answers with
"Available ``startTimes`` plus a ``routingId`` for the second call" and
``schedule-simple`` takes ``{startTime, guestEmail}``. Collapsing them into one
``book`` call would lose the property that makes the flow safe: the prospect chose
from a list, and the second call is checked against that list.

**The CRM is a class, not a socket.** :class:`~dsr.ownership_routing.crm.StoreCrm`
answers owner lookups out of the audited store. The research documents request
shapes for Chili Piper and Salesforce but no endpoint this product can reach, and
the product has no CRM client; calling a fake HTTP client at a real vendor would be
a claim it cannot back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from dsr.ownership_routing import calendars, rules as rules_module
from dsr.ownership_routing.crm import StoreCrm
from dsr.ownership_routing.errors import (
    BookingStateError,
    GuestEmailRequired,
    GuestMismatch,
    IntervalError,
    LinkError,
    LinkTypeNotSupported,
    NoOwnerResolved,
    OwnershipError,
    OwnerUnknown,
    RouteConsumed,
    RouteNotFound,
    SlotNotOffered,
)
from dsr.ownership_routing.inferences import describe as describe_inferences
from dsr.ownership_routing.vocabulary import (
    CATCH_ALL,
    CRM_OBJECT_TYPES,
    OWNERSHIP,
    SUPPORTED_LINK_TYPES,
    normalise_email,
    published_vocabulary,
    require_interval,
    require_link_type,
    require_object_type,
    require_resolution_source,
)
from dsr.store import RecordStore

#: The scheduling links. Account-scoped rather than room-scoped, because the
#: researched link is a workspace asset that prospects meet before any room
#: exists - a buyer arrives on a link, not on a room.
LINK_COLLECTION = "crm_owner_link"

#: One routing session per init call, holding the offered slots and the owner they
#: came from. The researched ``routingId`` is this record's id.
ROUTE_COLLECTION = "crm_owner_route"

#: One booking per schedule call.
BOOKING_COLLECTION = "crm_owner_booking"

#: One decision per routing attempt, whether it resolved, fell to the catch-all, or
#: was refused. A rep reading the page asks "why did this prospect reach me", and
#: the answer has to be a record rather than a re-derivation.
DECISION_COLLECTION = "crm_owner_decision"

#: The field on a link row carrying the routing chain, in the shape the evaluator
#: consumes. Validated on save, never patched piecemeal.
LINK_RULES_FIELD = "rules"

#: How many entries a link keeps in its own rolling history of recent decisions.
#: The decision records are the durable copy; this is the convenience copy the page
#: reads first, capped so a busy link does not grow an unbounded array.
LINK_HISTORY_LIMIT = 20

#: The booking that has not been cancelled.
CONFIRMED = "confirmed"
CANCELLED = "cancelled"


class OwnershipEngine:
    """The workflow, over one audited store.

    Built per request from ``StoreDep`` rather than held on ``app.state``, because
    an ``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the
    plugin host exists to make unnecessary. It holds nothing but the store handle,
    the CRM seam and a clock, all of which are constructor arguments, so a test
    constructs one with rows of its own and a clock it controls.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        crm: StoreCrm | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.crm = crm or StoreCrm(store)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- clock -------------------------------------------------------------- #

    def now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _at(self) -> str:
        return self.now().isoformat()

    # -- reference data ----------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data."""
        return published_vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return describe_inferences()

    def catalog(self) -> dict[str, Any]:
        """The CRM records, reps and teams the resolution reads.

        The extensibility note says "Teams + Distributions can hold many owners", so
        team membership is a product surface here rather than an internal detail: a
        reader needs to see which reps are on which team to understand why a CRM
        Ownership rule did or did not match.
        """
        reps = self.crm.reps()
        return {
            "records": self.crm.records(limit=500),
            "record_count": len(self.crm.records(limit=500)),
            "reps": reps,
            "rep_count": len(reps),
            "teams": self.teams(),
            "rep_collections": sorted(
                {str(rep.get("team") or "") for rep in reps if rep.get("team")}
            ),
            "link_types_supported": list(SUPPORTED_LINK_TYPES),
            "crm_object_types": list(CRM_OBJECT_TYPES),
        }

    # -- teams -------------------------------------------------------------- #

    def teams(self) -> dict[str, list[str]]:
        """Team name to the owner ids on it, read off the rep rows.

        Derived rather than stored: a rep joins a team by carrying its name, so a
        second place recording membership could disagree with the first, and the
        research's rule is stated in terms of reps ("one of the reps from Team A").
        """
        members: dict[str, list[str]] = {}
        for rep in self.crm.reps():
            team = str(rep.get("team") or "").strip()
            if not team:
                continue
            members.setdefault(team, []).append(str(rep.get("owner_id") or rep.get("id") or ""))
        return {team: sorted(ids) for team, ids in sorted(members.items())}

    # -- links -------------------------------------------------------------- #

    def create_link(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare an Ownership scheduling link.

        "Admin creates an **Ownership** scheduling link (link type ``Ownership``)."
        The type is validated against the five Chili Piper publishes and only
        Ownership is accepted, because the other four route by something this
        workflow does not consult - declaring one here and resolving it by CRM owner
        would make the link's own type a lie.

        Validated in full before the row is created: a bad type, a malformed
        interval, a chain with no catch-all, or a forbidden node must not leave a
        half-configured link behind that later looks usable.
        """
        data = self._link_payload(payload, room_id=room_id)
        record = self.store.create(
            LINK_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return self.link_view(record)

    def links(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """The declared links, newest first."""
        records = self.store.list(LINK_COLLECTION, room_id=room_id, limit=limit)
        if room_id is not None:
            records = [record for record in records if record.get("room_id") == room_id]
        return [self.link_view(record) for record in records]

    def get_link(self, link_id: str) -> dict[str, Any] | None:
        record = self.store.get(link_id)
        if record is None or record["collection"] != LINK_COLLECTION:
            return None
        return self.link_view(record)

    def require_link(self, link_id: str) -> dict[str, Any]:
        """The link, or a 404-shaped refusal naming it."""
        found = self.get_link(link_id)
        if found is None:
            from dsr.ownership_routing.errors import OwnershipError as _OwnershipError

            raise _OwnershipError(f"no scheduling link with id {link_id!r}")
        return found

    def update_link(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Patch a link, re-validating the whole merged result.

        Re-validated rather than patched, because every field this workflow checks
        interacts: changing ``type`` to Round Robin has to fail the same way a fresh
        declaration would, and changing ``rules`` to a chain with no catch-all must
        not leave the link holding one.
        """
        current = self.require_link(link_id)
        merged = {**current["data"], **dict(payload or {})}
        data = self._link_payload(merged)
        record = self.store.update(link_id, data, actor=actor, source=source)
        return self.link_view(record)

    def delete_link(self, link_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete a link.

        Its routes and bookings stay auditable, which is the point: the history of
        who a prospect was routed to outlives the link that routed them.
        """
        self.require_link(link_id)
        return self.store.delete(link_id, actor=actor, source=source)

    def _link_payload(
        self, payload: Mapping[str, Any], *, room_id: str | None = None
    ) -> dict[str, Any]:
        """Validate a whole link declaration into the row that gets stored.

        ``room_id`` is carried on the link so a room-scoped ``/links?room_id=``
        filter and a room-scoped summary answer about the links that room declared,
        while the link itself stays an account-level asset a prospect meets before
        any room exists.
        """
        if not isinstance(payload, Mapping):
            raise LinkError("a scheduling link must be an object")

        raw_type = payload.get("type", payload.get("link_type"))
        try:
            link_type = require_link_type(raw_type)
        except ValueError as exc:
            raise LinkTypeNotSupported(str(exc)) from exc
        if link_type not in SUPPORTED_LINK_TYPES:
            raise LinkTypeNotSupported(
                f"this workflow routes {OWNERSHIP} links; a {link_type} link routes by "
                f"{_routes_by(link_type)}, which is a different researched workflow"
            )

        link_id = str(payload.get("linkId") or payload.get("link_id") or "").strip()
        if not link_id:
            # Minted rather than required: the researched payload names it, but an
            # admin declaring a link in the UI is not pasting an Edge API id.
            link_id = f"own_{self.now().strftime('%Y%m%d')}_{abs(hash(link_type)) % 100000:05d}"

        try:
            interval = (
                require_interval(payload.get("interval")) if payload.get("interval") else None
            )
        except ValueError as exc:
            raise IntervalError(str(exc)) from exc

        ownership_path = bool(payload.get("ownership_path", True))
        # Nodes before the chain, on purpose. Both are refusals, and the order
        # decides which one the caller is told about: a link carrying an `Assign To`
        # node *and* no rules has two problems, and answering "your chain has no
        # catch-all" first would send somebody to write a chain rather than to
        # remove the node the research says cannot sit here.
        nodes = rules_module.check_nodes(payload.get("nodes"), ownership_path=ownership_path)
        declared_rules = payload.get("rules")
        chain = rules_module.normalise_rules(declared_rules, ownership_path=ownership_path)

        try:
            source = require_resolution_source(payload.get("resolution_source"))
        except ValueError as exc:
            raise LinkError(str(exc)) from exc

        order = payload.get("resolution_order") or list(CRM_OBJECT_TYPES)
        if not isinstance(order, (list, tuple)) or not order:
            raise LinkError("resolution_order must be a non-empty list of CRM object types")
        try:
            resolved_order = [require_object_type(entry) for entry in order]
        except ValueError as exc:
            raise LinkError(str(exc)) from exc

        data: dict[str, Any] = {
            "type": link_type,
            "linkId": link_id,
            "room_id": room_id,
            "name": str(payload.get("name") or f"Ownership link {link_id}"),
            "interval": interval,
            "rules": chain,
            "nodes": nodes,
            "ownership_path": ownership_path,
            "resolution_source": source,
            "resolution_order": resolved_order,
            "enabled": bool(payload.get("enabled", True)),
            "history": [],
        }
        for passthrough in (
            "meeting_type",
            "duration_minutes",
            "timezone",
            "data_fields",
            "teams",
            "owner",
        ):
            if passthrough in payload and payload[passthrough] is not None:
                data[passthrough] = payload[passthrough]
        return data

    @staticmethod
    def link_view(record: Mapping[str, Any]) -> dict[str, Any]:
        """The full envelope, plus a `has_catch_all` a page can key off."""
        view = dict(record)
        chain = list((record.get("data") or {}).get("rules") or [])
        view["has_catch_all"] = bool(chain) and str(chain[-1].get("kind")) == CATCH_ALL
        return view

    # -- the resolution ----------------------------------------------------- #

    def resolve(
        self,
        payload: Mapping[str, Any],
        *,
        link: Mapping[str, Any] | None = None,
        tolerate_unresolved: bool = False,
    ) -> dict[str, Any]:
        """The owner for one guest, and how it was found. Writes nothing.

        The researched arrow "guest email (or CRM record id) -> CRM lookup for
        owner id". Read-only so a form can answer "who would this reach" before the
        prospect commits, and so a reviewer can inspect a resolution without
        producing a decision record.

        Takes no ``room_id``: the CRM is account-level, and a lookup scoped to one
        room would hide a record that exists and send the prospect to the catch-all
        for a reason nothing in the decision would record.
        """
        guest_email = calendars.guest_email_of(payload)
        record_id = str(payload.get("record_id") or payload.get("crm_record_id") or "").strip()
        # The link arrives as the store's envelope, so its declared fields are
        # under `data`. Accepting a bare payload too keeps `resolve` usable on its
        # own, which is what a caller testing a resolution before declaring a link
        # wants to do.
        declared = (
            (link or {}).get("data")
            if isinstance((link or {}).get("data"), Mapping)
            else (link or {})
        )
        order = list(declared.get("resolution_order") or CRM_OBJECT_TYPES)
        resolution_source = str(declared.get("resolution_source") or "crm")

        if resolution_source == "pre_resolved":
            owner_id = calendars.pre_resolved_owner_id(payload)
            if not owner_id:
                raise OwnershipError(
                    "a pre_resolved link needs the owner id the caller already knows; the "
                    "extensibility note calls this 'a lead-owner link resolved from your CRM'"
                )
            rep = self.crm.require_rep(owner_id)
            return {
                "owner_id": owner_id,
                "owner_source": "pre_resolved",
                "rep": rep,
                "matched_object_type": str(payload.get("object_type") or "").casefold(),
                "matched_record_id": record_id,
                "matched_email": guest_email,
                "considered": [],
                "skipped": [],
                "resolution_order": order,
                "guest_email": guest_email,
                "record_id": record_id,
            }

        if not guest_email and not record_id:
            raise GuestEmailRequired(
                "an Ownership link's init call must carry guestEmail - it is required so Chili "
                "Piper can resolve the owner from your CRM"
            )

        try:
            found = self.crm.resolve_owner(
                # No room scoping here, and the omission is deliberate. The CRM is
                # account-level: the researched flow resolves "the guest's CRM
                # record", and that record belongs to the account, not to whichever
                # room the prospect happens to be browsing. Scoping the lookup by
                # room would silently hide a perfectly good record and send the
                # prospect to the catch-all with nothing in the decision explaining
                # why. The room scopes the *routing decision*, not the lookup.
                guest_email=guest_email,
                record_id=record_id or None,
                order=order,
            )
        except NoOwnerResolved:
            # A guest nobody owns is exactly the case the researched catch-all node
            # exists for, so the chain gets to see it. Refusing here would make the
            # catch-all reachable only for a prospect who *did* resolve to the wrong
            # rep, which is the opposite of what a terminal catch-all is for.
            #
            # Only honoured when the caller asked for it: `check` and `init_simple`
            # pass True because they are about to evaluate a chain, and the bare
            # `resolve` a caller makes for itself still raises, because a resolution
            # with no owner is a legitimate answer to "who owns this record" and
            # None is the honest one.
            if not tolerate_unresolved:
                raise
            return {
                "owner_id": "",
                "owner_source": "crm",
                "matched_object_type": "",
                "matched_record_id": "",
                "matched_email": "",
                "considered": [],
                "skipped": [],
                "unresolved": True,
                "resolution_order": order,
                "guest_email": guest_email,
                "record_id": record_id,
            }

        try:
            rep = self.crm.require_rep(found["owner_id"])
        except OwnerUnknown:
            # The record names an owner, but no rep in this workspace answers to
            # that id. That is still a routing situation rather than a malformed
            # request, and the researched catch-all is the place that gets handled:
            # a workspace that has not caught up with its CRM would otherwise refuse
            # every prospect whose record it has, which is worse than routing them
            # to the deal desk and saying so in the decision.
            if not tolerate_unresolved:
                raise
            # `owner_id` is deliberately emptied. The id the CRM names is not
            # dropped from the record - `record` below still carries it - but the
            # *routing* owner becomes nobody, because there is no calendar behind
            # that id to offer. Leaving it populated would let a chain whose only
            # rule is a catch-all report `resolved` and then hand the booking to an
            # owner id with no rep, which is the failure the catch-all exists to
            # prevent.
            return {
                **found,
                "owner_id": "",
                "named_owner_id": found["owner_id"],
                "rep": None,
                "record": self.crm.record(found["matched_record_id"])
                if found.get("matched_record_id")
                else None,
                "owner_unknown": True,
                "guest_email": guest_email,
                "record_id": record_id,
            }
        record_view = (
            self.crm.record(found["matched_record_id"]) if found.get("matched_record_id") else None
        )
        return {
            **found,
            "rep": rep,
            "record": record_view,
            "guest_email": guest_email,
            "record_id": record_id,
        }

    def route(
        self,
        resolution: Mapping[str, Any],
        *,
        link: Mapping[str, Any],
        guest_fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the link's chain against a resolution.

        A link with no declared chain routes straight to the resolved owner, which
        is the researched Ownership behaviour: "routes to the owner of the guest's
        CRM record". The chain exists for the researched *alternative* - a CRM
        Ownership rule that additionally checks team membership - so a link without
        one is the simple case and not a degraded one.

        A chain of only catch-alls is also the simple case with a fallback attached:
        it declares no decision, so the resolved owner still wins and the catch-all
        is reached only when there is nobody to route to. A rule chain that *does*
        decide replaces the plain ownership hop, which is what "Alternatively a CRM
        Ownership routing rule in a Concierge router can ... route to that rep"
        describes.
        """
        # `link` is the envelope the store hands back, so its fields live under
        # `data`. Reading `link.get("rules")` here would find nothing and every
        # link would take the no-chain branch - which looks correct (it routes to
        # the resolved owner) while silently making the researched catch-all
        # unreachable, so the bug would survive as product behaviour rather than
        # fail. `resolve` takes the same view and reads `resolution_order` from it
        # for the same reason.
        data = link.get("data") if isinstance(link.get("data"), Mapping) else link
        chain = list(data.get("rules") or [])
        if not chain:
            return {
                "outcome": "resolved",
                "rule": None,
                "owner_id": str(resolution.get("owner_id") or ""),
                "considered": [],
                "resolution": dict(resolution),
                "chain_declared": False,
            }
        outcome = rules_module.evaluate(
            chain, resolution=resolution, teams=self.teams(), guest_fields=guest_fields
        )
        outcome["chain_declared"] = True
        return outcome

    # -- init-simple -------------------------------------------------------- #

    def init_simple(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 2 to 4 of the researched flow, answering with slots and a routingId.

        The researched init call sends ``{link, guestEmail, interval}`` and answers
        with "Available ``startTimes`` plus a ``routingId`` for the second call". So
        this method:

        1. refuses a disabled link, which would otherwise route nothing;
        2. resolves the owner (guestEmail, or a record id, or pre-resolved);
        3. runs the link's chain, if it has one;
        4. reads the owner's calendar and computes the slots;
        5. writes the routing session, the decision, and the link's own history
           entry - all in one transaction, so a decision can never exist without
           the route that produced it.
        """
        link = self.require_link(link_id)
        if not link["data"].get("enabled", True):
            raise LinkError(
                f"link {link['data'].get('linkId') or link_id} is disabled; a disabled link is "
                "not offered to prospects and cannot open a routing session"
            )

        interval = link["data"].get("interval")
        if not interval:
            raise IntervalError(
                "this link declares no interval; the researched init payload carries "
                "'interval': {...} and the availability to offer comes from it"
            )

        resolution = self.resolve(payload, link=link, tolerate_unresolved=True)
        decision = self.route(resolution, link=link, guest_fields=payload.get("data_fields"))

        owner_id = str(decision.get("owner_id") or "")
        if not owner_id:
            # Unreachable for a chain that saved, because the catch-all is
            # required. Reachable if the chain was written straight into the store
            # by something other than this engine, so it refuses with the message
            # that says what is wrong rather than proceeding with nobody.
            raise NoOwnerResolved(
                "the link's routing chain named no owner and could not fall through; the "
                "catch-all node must name one"
            )

        rep = self.crm.require_rep(owner_id)
        exclude = self._booked_starts(owner_id, room_id=room_id)
        slots = calendars.available_slots(rep, interval, now=self.now(), exclude=exclude)

        started_at = self._at()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            route_record = tx.create(
                ROUTE_COLLECTION,
                {
                    "link_id": link_id,
                    "link_key": link["data"].get("linkId"),
                    "guest_email": resolution.get("guest_email")
                    or calendars.guest_email_of(payload),
                    "owner_id": owner_id,
                    "owner_name": str(rep.get("name") or rep.get("owner_id") or ""),
                    "start_times": slots,
                    "interval": dict(interval),
                    "outcome": decision["outcome"],
                    "rule": decision.get("rule") or {},
                    "matched_object_type": resolution.get("matched_object_type") or "",
                    "matched_record_id": resolution.get("matched_record_id") or "",
                    "owner_source": resolution.get("owner_source") or "crm",
                    "state": "open",
                    "opened_at": started_at,
                },
                room_id=room_id,
                actor=actor,
            )
            decision_record = tx.create(
                DECISION_COLLECTION,
                {
                    "route_id": route_record["id"],
                    "link_id": link_id,
                    "outcome": decision["outcome"],
                    "owner_id": owner_id,
                    "owner_name": str(rep.get("name") or rep.get("owner_id") or ""),
                    "guest_email": resolution.get("guest_email")
                    or calendars.guest_email_of(payload),
                    "matched_object_type": resolution.get("matched_object_type") or "",
                    "matched_record_id": resolution.get("matched_record_id") or "",
                    "owner_source": resolution.get("owner_source") or "crm",
                    # The id the CRM record named, kept even when it resolved to
                    # nobody. A decision that says only "catch_all" leaves a rep
                    # unable to tell "no record matched" from "the record matched and
                    # named a rep who has left".
                    "named_owner_id": resolution.get("named_owner_id") or "",
                    "owner_unknown": bool(resolution.get("owner_unknown")),
                    "rule": decision.get("rule") or {},
                    "considered": decision.get("considered") or [],
                    "resolution_order": resolution.get("resolution_order")
                    or list(CRM_OBJECT_TYPES),
                    "slots_offered": len(slots),
                    "chain_declared": bool(decision.get("chain_declared")),
                    "started_at": started_at,
                },
                room_id=room_id,
                actor=actor,
            )
            self._append_history(tx, link_id, decision_record["id"], decision, room_id=room_id)

        return {
            "routing_id": route_record["id"],
            "link_id": link_id,
            "link_key": link["data"].get("linkId"),
            "guest_email": resolution.get("guest_email") or "",
            "owner_id": owner_id,
            "owner_name": str(rep.get("name") or rep.get("owner_id") or ""),
            "outcome": decision["outcome"],
            "rule": decision.get("rule") or {},
            "matched_object_type": resolution.get("matched_object_type") or "",
            "owner_source": resolution.get("owner_source") or "crm",
            "start_times": slots,
            "slots_offered": len(slots),
            "decision_id": decision_record["id"],
            "state": "open",
            "opened_at": started_at,
            "room_id": room_id,
        }

    def check(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """What ``init_simple`` would answer, writing nothing.

        The read-only half, for a form that wants to say "this reaches Sam" before
        the prospect commits. It runs the same resolution, the same chain and the
        same calendar arithmetic as the real call, so the answer cannot disagree
        with the write that follows.
        """
        link = self.require_link(link_id)
        interval = link["data"].get("interval")
        if not interval:
            raise IntervalError("this link declares no interval, so it has no slots to offer")
        resolution = self.resolve(payload, link=link, tolerate_unresolved=True)
        decision = self.route(resolution, link=link, guest_fields=payload.get("data_fields"))
        owner_id = str(decision.get("owner_id") or "")
        rep = self.crm.require_rep(owner_id) if owner_id else {}
        slots = (
            calendars.available_slots(
                rep,
                interval,
                now=self.now(),
                exclude=self._booked_starts(owner_id, room_id=room_id),
            )
            if owner_id
            else []
        )
        return {
            "link_id": link_id,
            "would_route_to": owner_id,
            "would_route_to_name": str(rep.get("name") or "") if owner_id else "",
            "outcome": decision["outcome"],
            "rule": decision.get("rule") or {},
            "considered": decision.get("considered") or [],
            "matched_object_type": resolution.get("matched_object_type") or "",
            "owner_source": resolution.get("owner_source") or "crm",
            "start_times": slots,
            "slots_offered": len(slots),
            "wrote": False,
        }

    # -- schedule-simple ---------------------------------------------------- #

    def book(
        self,
        route_id: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Step 4's second half: book one of the offered slots.

        The researched schedule-simple payload is ``{startTime, guestEmail}``, and
        three refusals guard it:

        * an unknown ``routingId`` - nothing was offered;
        * a session already booked - two prospects would share one slot;
        * a ``startTime`` the session never offered - the owner's calendar was never
          shown to be free then;
        * a ``guestEmail`` that is not the one the session was opened for.

        The booking, the session's transition to ``booked``, and the link's history
        entry land in one transaction. A booking that exists with an open session is
        the state that would let the same slot be taken twice.
        """
        route_record = self.store.get(route_id)
        if route_record is None or route_record["collection"] != ROUTE_COLLECTION:
            raise RouteNotFound(f"no routing session with id {route_id!r}")
        route = route_record["data"] or {}

        if route.get("state") != "open":
            raise RouteConsumed(
                f"routing session {route_id} was already booked at "
                f"{route.get('booked_start_time') or 'an earlier call'}; a session is consumed by "
                "one booking"
            )

        start_time = str(payload.get("startTime") or payload.get("start_time") or "").strip()
        if not start_time:
            raise OwnershipError("the researched schedule-simple payload carries a startTime")
        offered = [str(slot) for slot in (route.get("start_times") or [])]
        if not _same_instant(start_time, offered):
            raise SlotNotOffered(
                f"{start_time} is not one of the start times this session offered; the init call "
                "returned the availability the owner's calendar was read as having"
            )

        guest_email = calendars.guest_email_of(payload)
        route_guest = normalise_email(route.get("guest_email"))
        if not guest_email:
            raise GuestEmailRequired(
                "the researched schedule-simple payload carries a guestEmail, and the booking has "
                "to be attributable to a prospect"
            )
        if guest_email != route_guest:
            raise GuestMismatch(
                f"this session was opened for {route_guest or '(no email)'}, so {guest_email} cannot "
                "book the slot the other prospect chose"
            )

        link_id = str(route.get("link_id") or "")
        owner_id = str(route.get("owner_id") or "")
        booked_at = self._at()
        with self.store.db.transaction(actor=actor, source=source) as tx:
            booking = tx.create(
                BOOKING_COLLECTION,
                {
                    "route_id": route_id,
                    "link_id": link_id,
                    "link_key": route.get("link_key") or "",
                    "guest_email": guest_email,
                    "owner_id": owner_id,
                    "owner_name": route.get("owner_name") or "",
                    "start_time": start_time,
                    "end_time": _end_of(start_time, route.get("interval") or {}),
                    "state": CONFIRMED,
                    "outcome": route.get("outcome") or "",
                    "matched_object_type": route.get("matched_object_type") or "",
                    "matched_record_id": route.get("matched_record_id") or "",
                    "owner_source": route.get("owner_source") or "crm",
                    "booked_at": booked_at,
                },
                room_id=room_id,
                actor=actor,
            )
            tx.update(
                route_id,
                {"state": "booked", "booked_start_time": start_time, "booking_id": booking["id"]},
                actor=actor,
            )
            self._append_history(
                tx, link_id, booking["id"], {"outcome": "booked", "rule": {}}, room_id=room_id
            )
        return booking

    def cancel_booking(
        self,
        booking_id: str,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Cancel a booking and release its slot.

        Releasing rather than merely marking it: the slot was taken out of the
        owner's availability when the booking was made, and a cancellation that did
        not put it back would quietly shrink a rep's bookable week by one meeting
        per cancellation.
        """
        record = self.store.get(booking_id)
        if record is None or record["collection"] != BOOKING_COLLECTION:
            raise OwnershipError(f"no booking with id {booking_id!r}")
        booking = record["data"] or {}
        if booking.get("state") == CANCELLED:
            raise BookingStateError(f"booking {booking_id} is already cancelled")

        with self.store.db.transaction(actor=actor, source=source) as tx:
            updated = tx.update(
                booking_id, {"state": CANCELLED, "cancelled_at": self._at()}, actor=actor
            )
            self._append_history(
                tx,
                link_id_of(booking),
                booking_id,
                {"outcome": "cancelled", "rule": {}},
                room_id=room_id,
            )
        return updated

    # -- reads -------------------------------------------------------------- #

    def routes(
        self,
        *,
        room_id: str | None = None,
        link_id: str | None = None,
        state: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every routing session, newest first."""
        records = self.store.list(ROUTE_COLLECTION, room_id=room_id, limit=limit)
        return [
            self._session_view(record)
            for record in records
            if (link_id is None or (record["data"] or {}).get("link_id") == link_id)
            and (state is None or (record["data"] or {}).get("state") == state)
            and (owner_id is None or (record["data"] or {}).get("owner_id") == owner_id)
        ]

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        record = self.store.get(route_id)
        if record is None or record["collection"] != ROUTE_COLLECTION:
            return None
        return self._session_view(record)

    def bookings(
        self,
        *,
        room_id: str | None = None,
        link_id: str | None = None,
        owner_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every booking, newest first."""
        records = self.store.list(BOOKING_COLLECTION, room_id=room_id, limit=limit)
        return [
            record
            for record in records
            if (link_id is None or (record["data"] or {}).get("link_id") == link_id)
            and (owner_id is None or (record["data"] or {}).get("owner_id") == owner_id)
            and (state is None or (record["data"] or {}).get("state") == state)
        ]

    def get_booking(self, booking_id: str) -> dict[str, Any] | None:
        record = self.store.get(booking_id)
        if record is None or record["collection"] != BOOKING_COLLECTION:
            return None
        return record

    def decisions(
        self,
        *,
        room_id: str | None = None,
        link_id: str | None = None,
        outcome: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every routing decision, newest first."""
        records = self.store.list(DECISION_COLLECTION, room_id=room_id, limit=limit)
        return [
            record
            for record in records
            if (link_id is None or (record["data"] or {}).get("link_id") == link_id)
            and (outcome is None or (record["data"] or {}).get("outcome") == outcome)
            and (owner_id is None or (record["data"] or {}).get("owner_id") == owner_id)
        ]

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the filters return."""
        links = self.links(room_id=room_id)
        routes = self.routes(room_id=room_id, limit=1000)
        bookings = self.bookings(room_id=room_id, limit=1000)
        decisions = self.decisions(room_id=room_id, limit=1000)
        reps = self.crm.reps()
        connected = [
            rep
            for rep in reps
            if isinstance(rep.get("calendar"), Mapping) and rep["calendar"].get("connected", True)
        ]
        outcomes: dict[str, int] = {}
        for decision in decisions:
            key = str((decision["data"] or {}).get("outcome") or "")
            outcomes[key] = outcomes.get(key, 0) + 1
        by_object: dict[str, int] = {}
        for decision in decisions:
            key = str((decision["data"] or {}).get("matched_object_type") or "") or "none"
            by_object[key] = by_object.get(key, 0) + 1
        return {
            "links": len(links),
            "links_enabled": sum(1 for link in links if link["data"].get("enabled", True)),
            # Read off the payload, not the envelope: `link_view` returns the
            # store's record, so its fields live under `data`. Counting `link.get
            # ("rules")` here would silently report zero links with a chain, which
            # is the one number on this endpoint a reader would trust.
            "links_with_chain": sum(1 for link in links if (link.get("data") or {}).get("rules")),
            "routes": len(routes),
            "routes_open": sum(
                1 for route in routes if (route["data"] or {}).get("state") == "open"
            ),
            "routes_booked": sum(
                1 for route in routes if (route["data"] or {}).get("state") == "booked"
            ),
            "bookings": len(bookings),
            "bookings_confirmed": sum(
                1 for booking in bookings if (booking["data"] or {}).get("state") == CONFIRMED
            ),
            "bookings_cancelled": sum(
                1 for booking in bookings if (booking["data"] or {}).get("state") == CANCELLED
            ),
            "decisions": len(decisions),
            "decisions_by_outcome": dict(sorted(outcomes.items())),
            "decisions_by_object_type": dict(sorted(by_object.items())),
            "slots_offered": sum(
                len((route["data"] or {}).get("start_times") or []) for route in routes
            ),
            "reps": len(reps),
            "reps_with_calendar": len(connected),
            "crm_records": len(self.crm.records(limit=1000)),
            "teams": len(self.teams()),
        }

    @staticmethod
    def _session_view(record: Mapping[str, Any]) -> dict[str, Any]:
        view = dict(record)
        data = view.get("data") or {}
        view["slots_offered"] = len(data.get("start_times") or [])
        view["is_open"] = data.get("state") == "open"
        return view

    def _booked_starts(self, owner_id: str, *, room_id: str | None = None) -> list[datetime]:
        """The start times this owner already has confirmed, for exclusion.

        Read straight from the bookings rather than from the sessions, because a
        cancelled booking released its slot and must not keep blocking it.
        """
        if not owner_id:
            return []
        from dsr.ownership_routing.calendars import _utc

        starts: list[datetime] = []
        for booking in self.bookings(
            owner_id=owner_id, state=CONFIRMED, room_id=room_id, limit=1000
        ):
            raw = (booking["data"] or {}).get("start_time")
            if raw:
                try:
                    starts.append(_utc(raw))
                except ValueError:
                    continue
        return starts

    def _append_history(
        self,
        tx,
        link_id: str,
        decision_id: str,
        decision: Mapping[str, Any],
        *,
        room_id: str | None,
    ) -> None:
        """Prepend one entry to the link's capped rolling history.

        Written inside the caller's transaction so the history entry cannot exist
        without the route or booking it describes.
        """
        if not link_id:
            return
        record = self.store.get(link_id)
        if record is None or record["collection"] != LINK_COLLECTION:
            return
        history = list((record["data"] or {}).get("history") or [])
        history.insert(
            0,
            {
                "decision_id": decision_id,
                "outcome": decision.get("outcome") or "",
                "rule": decision.get("rule") or {},
                "at": self._at(),
            },
        )
        tx.update(link_id, {"history": history[:LINK_HISTORY_LIMIT]})


def _routes_by(link_type: str) -> str:
    from dsr.ownership_routing.vocabulary import LINK_TYPES

    for entry in LINK_TYPES:
        if entry["type"] == link_type:
            return entry["routes_by"]
    return "something else"


def _same_instant(candidate: Any, offered: Sequence[str]) -> bool:
    """Whether a requested start is one of the offered ones.

    Compared as instants rather than as strings, because the offered slots are
    serialised from aware datetimes and a caller echoing one back is entitled to
    write it with a ``Z`` where the offer used ``+00:00``. A string comparison would
    refuse a correct booking.
    """
    from dsr.ownership_routing.calendars import _utc

    try:
        wanted = _utc(candidate)
    except (ValueError, TypeError):
        return False
    for slot in offered:
        try:
            if _utc(slot) == wanted:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _end_of(start_time: str, interval: Mapping[str, Any]) -> str:
    """The meeting's end, from its start and the interval's duration."""
    from datetime import timedelta

    from dsr.ownership_routing.calendars import _utc

    try:
        return (
            _utc(start_time) + timedelta(minutes=int(interval.get("duration_minutes") or 0))
        ).isoformat()
    except (ValueError, TypeError):
        return ""


def link_id_of(booking: Mapping[str, Any]) -> str:
    """The link a booking belongs to. Kept trivial so the cancel path reads plainly."""
    return str(booking.get("link_id") or "")
