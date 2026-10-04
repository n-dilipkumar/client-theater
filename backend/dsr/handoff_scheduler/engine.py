"""The handoff flow end to end, over the audited store.

The researched workflow, in its own order:

1. An admin builds a **Handoff Router** in a workspace, defining routing paths
   (e.g. region to an AE pod, product line to an AE).
2. An SDR opens the Handoff scheduler and enters the guest's email, or the CRM
   record id.
3. The router is evaluated and **one or more routing paths** come back, each with
   its own ``pathId`` and ``startTimes``.
4. The SDR picks a path and a slot. The meeting is booked with the AE as Assignee
   and the SDR as Booker, plus the path's Additional Invitees.

Collections
-----------

Four, all ordinary JSON in ``records.data``. No migration, no typed column, and a
team adding a field needs no coordination with anyone.

``handoff_workspace``
    One SDR/AE pod. Holds the user records: their roles, their calendar
    connection, and their busy blocks.
``handoff_router``
    The reusable asset. Names a workspace and its routing paths, each path naming
    one assignee, its match block, and its Additional Invitees with the Required
    toggle.
``handoff_routing``
    One opened evaluation. Holds the request, the booker, and every matched path
    with the ``startTimes`` that path offered, so the answer stays reproducible
    after the calendars move.
``handoff_meeting``
    The booked meeting. Names the workspace, router, path, booker and assignee, so
    a later reassignment reopens that same routing context.

The source argument
-------------------

Every writing method takes ``source`` as a required keyword. The route owns the
string, built from ``router.prefix``, so the audit row and the route table cannot
drift: a hardcoded source inside a domain method is a defect, and the same class
of bug has shipped in this codebase before, with a feature's audit log naming a
path the app had stopped serving. Making it required means omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from dsr.handoff_scheduler.availability import (
    busy_at,
    explain_missing,
    find_slot,
    normalise_interval,
    path_window,
)
from dsr.handoff_scheduler.errors import HandoffConflict, HandoffError, HandoffNotFound
from dsr.handoff_scheduler.paths import (
    assignee_can_be_assigned,
    gating_user_ids,
    ignored_user_ids,
    required_of,
    validate_paths,
)
from dsr.handoff_scheduler.rules import build_context, match_report, read_request
from dsr.handoff_scheduler.timeutil import parse, utcnow
from dsr.handoff_scheduler.vocabulary import (
    BOOKED,
    CANCELLED,
    CONFIRMED,
    LINK_TYPES,
    NO_AVAILABILITY,
    OPEN,
    PATHS_OFFERED,
    require_outcome,
)
from dsr.handoff_scheduler.workspaces import (
    ASSIGNEE,
    BOOKER,
    calendar_connected,
    find_user,
    has_role,
    require_user,
    role_article,
    roles_of,
    summarise,
    user_id,
    validate_workspace,
)
from dsr.store import RecordStore

WORKSPACE_COLLECTION = "handoff_workspace"
ROUTER_COLLECTION = "handoff_router"
ROUTING_COLLECTION = "handoff_routing"
MEETING_COLLECTION = "handoff_meeting"

#: The default meeting length when a router's interval names none.
DEFAULT_DURATION_MINUTES = 30


class HandoffSchedulerEngine:
    """The workflow over one :class:`~dsr.store.RecordStore`.

    The store and the clock are the only constructor arguments. Building the
    engine per request rather than holding it on ``app.state`` keeps the clock a
    plain argument, which is what lets a test drive the whole workflow with rows of
    its own and a clock it controls, and leaves ``app.state`` a thing no feature
    has to touch.
    """

    def __init__(self, store: RecordStore, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or utcnow

    def _now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------------ #
    # Workspaces
    # ------------------------------------------------------------------ #

    def create_workspace(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a workspace and its users. Step 1's outer half.

        Validated in full before the row is created, so a user with no id, a
        duplicated user, an unrecognised role or an unreadable busy block cannot
        leave a half-configured workspace behind that later looks usable.
        """
        body = validate_workspace(payload)
        return self.store.create(
            WORKSPACE_COLLECTION,
            {**body, "note": str(payload.get("note") or "")},
            room_id=payload.get("room_id"),
            actor=actor,
            source=source,
        )

    def workspaces(self, *, room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every declared workspace, newest first."""
        return self.store.list(WORKSPACE_COLLECTION, room_id=room_id, limit=limit)

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        return self.store.get(workspace_id)

    def require_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise HandoffNotFound(f"workspace {workspace_id} not found")
        return workspace

    def workspace_view(self, workspace: Mapping[str, Any]) -> dict[str, Any]:
        """A workspace with its users annotated by what each of them may do.

        A user who cannot be assigned is listed rather than hidden, with the reason
        beside it. "this lead cannot reach Sam" is the thing an SDR needs to see,
        and a user who silently vanishes from the list is the hardest version of
        that bug to diagnose.
        """
        users = list(workspace.get("data", {}).get("users") or [])
        return {
            **dict(workspace),
            "users": [
                {
                    **user,
                    "roles": roles_of(user),
                    "calendar_connected": calendar_connected(user),
                    "can_book": has_role(user, BOOKER),
                    "assignable": has_role(user, ASSIGNEE) and calendar_connected(user),
                    "assignable_reason": (
                        None
                        if has_role(user, ASSIGNEE) and calendar_connected(user)
                        else (
                            f"not {role_article(ASSIGNEE)} {ASSIGNEE} on this workspace"
                            if not has_role(user, ASSIGNEE)
                            else "calendar not connected"
                        )
                    ),
                }
                for user in users
            ],
            "summary": summarise(users),
        }

    # ------------------------------------------------------------------ #
    # Routers
    # ------------------------------------------------------------------ #

    def create_router(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Declare a Handoff Router and its routing paths. Step 1's inner half.

        Every path is validated against the live workspace rather than a snapshot,
        so a workspace that loses an AE before the router is used is visible here.
        """
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HandoffError("a Handoff Router needs a name")

        workspace_ref = str(payload.get("workspace_ref") or payload.get("workspace_id") or "")
        if not workspace_ref:
            raise HandoffError(
                "workspace_ref is required; which pod does this router hand off inside?"
            )
        workspace = self.require_workspace(workspace_ref)

        paths = validate_paths(payload, workspace)
        interval = normalise_interval(
            payload.get("interval"), default_minutes=DEFAULT_DURATION_MINUTES
        )

        return self.store.create(
            ROUTER_COLLECTION,
            {
                "name": name,
                "workspace_ref": workspace["id"],
                "workspace_name": str(workspace.get("data", {}).get("name") or ""),
                "paths": paths,
                "path_count": len(paths),
                "interval": interval,
                "note": str(payload.get("note") or ""),
            },
            room_id=payload.get("room_id"),
            actor=actor,
            source=source,
        )

    def routers(
        self,
        *,
        room_id: str | None = None,
        workspace_ref: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every declared router, newest first."""
        records = self.store.list(ROUTER_COLLECTION, room_id=room_id, limit=limit)
        if workspace_ref is not None:
            records = [r for r in records if r["data"].get("workspace_ref") == workspace_ref]
        return records

    def get_router(self, router_id: str) -> dict[str, Any] | None:
        return self.store.get(router_id)

    def require_router(self, router_id: str) -> dict[str, Any]:
        record = self.get_router(router_id)
        if record is None:
            raise HandoffNotFound(f"router {router_id} not found")
        return record

    def router_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A router with each path's gate set spelled out.

        The gate set is read from the declaration rather than from a stored copy of
        it, because a required invitee's calendar and a not-required invitee's
        calendar are treated differently and the SDR has to be able to see which
        is which before they pick a path.
        """
        paths = list(record.get("data", {}).get("paths") or [])
        return {
            **dict(record),
            "paths": [
                {
                    **path,
                    "gating_user_ids": gating_user_ids(path),
                    "ignored_user_ids": ignored_user_ids(path),
                }
                for path in paths
            ],
        }

    # ------------------------------------------------------------------ #
    # Steps 2 and 3: read the request and evaluate the router
    # ------------------------------------------------------------------ #

    def _window_for_path(
        self,
        path: Mapping[str, Any],
        workspace: Mapping[str, Any],
        interval: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """One path's ``startTimes``.

        ``path`` is a declared path body and ``interval`` is already normalised.
        The router's interval is the default and the caller's own interval
        overrides it, which is what the researched init payload implies: it sends
        its ``interval`` next to the request.
        """
        return path_window(
            path,
            workspace,
            start=parse(interval["start"]),
            end=parse(interval["end"]),
            duration_minutes=int(interval["duration_minutes"]),
            min_notice_minutes=int(interval["min_notice_minutes"]),
            now=now,
        )

    def _consulted_routers(
        self, workspace: Mapping[str, Any], router_ref: str | None
    ) -> list[dict[str, Any]]:
        """The routers this evaluation reads.

        The researched payload makes ``routerId`` optional. Absent, every router in
        the workspace is consulted, which is what lets an SDR open the Handoff
        scheduler and be offered every path the pod has declared rather than having
        to pick a router first.
        """
        if router_ref:
            router = self.require_router(router_ref)
            if str(router["data"].get("workspace_ref")) != str(workspace["id"]):
                raise HandoffError(
                    f"router {router_ref} belongs to workspace "
                    f"{router['data'].get('workspace_ref')}, not to {workspace['id']}"
                )
            return [router]
        found = self.routers(workspace_ref=str(workspace["id"]), limit=1000)
        if not found:
            raise HandoffError(
                f"workspace {workspace['id']} has no Handoff Router. "
                "Declare one before an SDR can open the Handoff scheduler"
            )
        return found

    def _evaluate(
        self, workspace_id: str, payload: Mapping[str, Any] | None, now: datetime
    ) -> dict[str, Any]:
        """Everything steps 2 and 3 produce, and nothing at all written.

        The read-only core that :meth:`check` serves directly and
        :meth:`init_simple` wraps in one routing record. Both call this, so the
        preview and the real evaluation can only ever disagree about the write.
        """
        body = dict(payload or {})
        workspace = self.require_workspace(workspace_id)

        request_type, guest_email, record_id, explicits = read_request(body)
        context, shadowed = build_context(
            request_type, guest_email=guest_email, crm_record_id=record_id, explicits=explicits
        )

        booker_ref = str(body.get("booker_ref") or body.get("booker") or "")
        if not booker_ref:
            raise HandoffError(
                "booker_ref is required; the researched call names the booker in its path and the "
                "meeting is booked with the SDR as the booker"
            )
        booker = require_user(workspace, booker_ref, role=BOOKER)

        router_ref = str(body.get("router_ref") or body.get("router_id") or "") or None
        routers = self._consulted_routers(workspace, router_ref)

        interval = normalise_interval(
            body.get("interval"), default_minutes=DEFAULT_DURATION_MINUTES
        )

        evaluated: list[dict[str, Any]] = []
        report: list[dict[str, Any]] = []
        for router in routers:
            router_body = router["data"]
            # The router's own interval is the default for that router. A caller's
            # interval overrides it, so the two are kept apart rather than merged
            # before the loop: one caller can name no interval and still get each
            # router's own default.
            router_interval = interval
            if not body.get("interval") and router_body.get("interval"):
                router_interval = normalise_interval(
                    router_body["interval"], default_minutes=DEFAULT_DURATION_MINUTES
                )

            declared = list(router_body.get("paths") or [])
            router_report = match_report(declared, context)
            report.extend({**entry, "router_ref": router["id"]} for entry in router_report)

            paths: list[dict[str, Any]] = []
            for entry, verdict in zip(declared, router_report, strict=True):
                if not verdict["matched"]:
                    continue
                window = self._window_for_path(entry, workspace, router_interval, now)
                paths.append(
                    {
                        "router_ref": router["id"],
                        "router_name": str(router_body.get("name") or ""),
                        "path_id": str(entry.get("path_id") or ""),
                        "path_name": str(entry.get("name") or ""),
                        "assignee_ref": str(entry.get("assignee_ref") or ""),
                        "assignee_name": str(entry.get("assignee_name") or ""),
                        "match": dict(entry.get("match") or {}),
                        "matched_fields": _matched_fields(entry.get("match") or {}, context),
                        "invitees": list(entry.get("invitees") or []),
                        # The interval this path's window was actually built from,
                        # which is the router's own default when the caller named
                        # none. Stored per path rather than only on the routing,
                        # because a routing can consult several routers and each
                        # may have a different default. Booking re-reads this so a
                        # slot is re-checked under the interval that produced it.
                        "interval": router_interval,
                        "start_times": [slot["start_at"] for slot in window["slots"]],
                        "slot_count": window["slot_count"],
                        "window": window,
                    }
                )
            evaluated.append(
                {
                    "router_ref": router["id"],
                    "router_name": str(router_body.get("name") or ""),
                    "paths": paths,
                    "interval": router_interval,
                }
            )

        flat = [path for entry in evaluated for path in entry["paths"]]
        if not flat:
            raise HandoffError(_unmatched_message(report, context, workspace))
        require_outcome(PATHS_OFFERED if any(p["slot_count"] for p in flat) else NO_AVAILABILITY)

        return {
            "workspace_id": workspace["id"],
            "workspace_name": str(workspace.get("data", {}).get("name") or ""),
            "booker_ref": user_id(booker),
            "booker_name": str(booker.get("name") or ""),
            "booker_email": str(booker.get("email") or ""),
            "request_type": request_type,
            "guest_email": guest_email,
            "crm_record_id": record_id,
            "crm_explicits": explicits,
            "context": context,
            "shadowed_explicit_keys": shadowed,
            "router_ref": router_ref,
            "router_refs": [entry["router_ref"] for entry in evaluated],
            "routers": evaluated,
            "paths": flat,
            "match_report": report,
            "path_count": len(flat),
            "paths_with_availability": sum(1 for path in flat if path["slot_count"]),
            "outcome": PATHS_OFFERED if any(p["slot_count"] for p in flat) else NO_AVAILABILITY,
            "interval": interval,
        }

    def check(self, workspace_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """What would happen. Steps 2 and 3, with no write at all.

        The read-only half of ``init-simple``, for an SDR who wants to see which
        paths a lead reaches before committing. The answer is identical to what
        ``init-simple`` would produce, because both call the same evaluation. Only
        the consequences differ.
        """
        return self._evaluate(workspace_id, payload, self._now())

    def init_simple(
        self,
        workspace_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 2 and 3: evaluate the router and answer with the routing paths.

        The researched init call sends either
        ``{type: "GuestEmailRequest", guestEmail, interval}`` or
        ``{type: "CrmRequest", id, interval}``, plus optional ``routerId`` and
        ``crmExplicits``, and answers with a ``routingId`` plus
        ``routers[].pathResults[].startTimes``.

        This writes one routing holding the request, the booker, every matched path
        and the ``startTimes`` that path offered, so the answer and the reason for
        it land in one transaction and the answer stays reproducible after the
        calendars move.

        Two outcomes are possible and both are successes. When at least one matched
        path has a free slot, the outcome is ``paths_offered``. When every matched
        path is booked out for the window, the outcome is ``no_availability`` and
        the matched paths still come back with their empty ``startTimes``: a busy
        week is a legitimate answer rather than an error, and the SDR's next move
        is to look at another path. A router that matched no path at all is the one
        refusal, and it names every path it measured.
        """
        evaluated = self._evaluate(workspace_id, payload, self._now())
        routing = self.store.create(
            ROUTING_COLLECTION,
            {
                "workspace_ref": evaluated["workspace_id"],
                "workspace_name": evaluated["workspace_name"],
                "booker_ref": evaluated["booker_ref"],
                "booker_name": evaluated["booker_name"],
                "booker_email": evaluated["booker_email"],
                "request_type": evaluated["request_type"],
                "guest_email": evaluated["guest_email"],
                "crm_record_id": evaluated["crm_record_id"],
                "crm_explicits": evaluated["crm_explicits"],
                "shadowed_explicit_keys": evaluated["shadowed_explicit_keys"],
                "router_ref": evaluated["router_ref"],
                "router_refs": evaluated["router_refs"],
                "paths": evaluated["paths"],
                "path_count": evaluated["path_count"],
                "paths_with_availability": evaluated["paths_with_availability"],
                "outcome": evaluated["outcome"],
                "interval": evaluated["interval"],
                "state": OPEN,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "routing_id": routing["id"],
            "routing": routing,
            **evaluated,
        }

    # ------------------------------------------------------------------ #
    # Routings
    # ------------------------------------------------------------------ #

    def routings(
        self,
        *,
        room_id: str | None = None,
        workspace_ref: str | None = None,
        state: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every opened evaluation, newest first."""
        records = self.store.list(ROUTING_COLLECTION, room_id=room_id, limit=limit)
        if workspace_ref is not None:
            records = [r for r in records if r["data"].get("workspace_ref") == workspace_ref]
        if state is not None:
            records = [r for r in records if r["data"].get("state") == state]
        if outcome is not None:
            records = [r for r in records if r["data"].get("outcome") == outcome]
        return records

    def get_routing(self, routing_id: str) -> dict[str, Any] | None:
        return self.store.get(routing_id)

    def require_routing(self, routing_id: str) -> dict[str, Any]:
        record = self.get_routing(routing_id)
        if record is None:
            raise HandoffNotFound(f"routing {routing_id} not found")
        return record

    def routing_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A routing with its paths read against their declared gates.

        The stored ``startTimes`` are what the SDR was shown and are never rewritten
        from a calendar that has since moved. What is recomputed here is only the
        per-path gate annotation, so a reader can see who was gating each path.
        """
        paths = []
        for path in record.get("data", {}).get("paths") or []:
            paths.append(
                {
                    **path,
                    "gating_user_ids": gating_user_ids(path),
                    "ignored_user_ids": ignored_user_ids(path),
                }
            )
        return {**dict(record), "paths": paths}

    # ------------------------------------------------------------------ #
    # Step 4: book
    # ------------------------------------------------------------------ #

    def schedule_simple(
        self,
        routing_id: str,
        router_id: str,
        path_id: str,
        booker_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Step 4: book one slot on one of the paths this routing offered.

        The researched schedule call is
        ``.../routing/{routingId}/router/{routerId}/path/{pathId}/booker/{userId}/schedule-simple``
        with ``{startTime}``, so all four references travel in the path and are
        checked against what the routing actually holds. Five refusals guard it:

        * a routing that has already been booked;
        * a router that was not one of the routers this routing consulted;
        * a path the router does not declare;
        * a booker who is not the booker the routing was opened for;
        * a ``startTime`` the path never offered, or one whose gate set has since
          taken it.

        The last one is re-checked against the live workspace rather than trusted
        from the stored ``startTimes``, because the routing was opened earlier and
        the assignee may have taken another meeting since.

        The meeting, the routing's transition and the gate re-check all land in one
        transaction, because a meeting that exists beside an open routing is the
        state that would let one slot be taken twice.
        """
        routing = self.require_routing(routing_id)
        body = routing["data"]
        if body.get("state") != OPEN:
            raise HandoffConflict(
                f"routing {routing_id} is {body.get('state')}, not {OPEN}; it cannot be booked again"
            )

        if str(booker_id) != str(body.get("booker_ref")):
            raise HandoffConflict(
                f"routing {routing_id} was opened by {body.get('booker_ref')}, not by {booker_id}. "
                "The meeting is booked with the SDR who opened the Handoff scheduler as the booker"
            )

        if router_id not in (body.get("router_refs") or []):
            raise HandoffConflict(
                f"router {router_id} is not one of the routers this routing consulted: "
                + (", ".join(str(one) for one in body.get("router_refs") or []) or "none")
            )

        router = self.require_router(router_id)
        if str(router["data"].get("workspace_ref")) != str(body.get("workspace_ref")):
            raise HandoffConflict(
                f"router {router_id} belongs to workspace {router['data'].get('workspace_ref')}, "
                f"not to the routing's workspace {body.get('workspace_ref')}"
            )

        declared = {str(path.get("path_id")): path for path in router["data"].get("paths") or []}
        path = declared.get(str(path_id))
        if path is None:
            raise HandoffError(
                f"path {path_id} is not declared on router {router_id}. "
                "It declares: " + (", ".join(sorted(declared)) or "none")
            )

        workspace = self.require_workspace(str(body.get("workspace_ref")))
        interval = _bookable_interval(body, router_id, path_id)
        now = self._now()

        start_time = str(
            (payload or {}).get("startTime") or (payload or {}).get("start_at") or ""
        ).strip()
        if not start_time:
            raise HandoffError("startTime is required; which slot did the SDR pick?")

        guest_email = str(body.get("guest_email") or "")
        offered_guest = (payload or {}).get("guestEmail") or (payload or {}).get("guest_email")
        if offered_guest and str(offered_guest).strip().lower() != guest_email.lower():
            raise HandoffError(f"this routing was opened for {guest_email}, not {offered_guest}")

        # Checked before the slot lookup, because a workspace edited since the
        # routing opened is a configuration problem and "now taken by ae" would
        # send the SDR to open the scheduler again when reopening it changes nothing.
        stale = assignee_can_be_assigned(path, workspace)
        if stale:
            raise HandoffConflict(f"path {path_id} cannot be booked: {stale}")

        # The stored startTimes are what the SDR saw. Recomputing is how a slot
        # that has since been taken is told apart from one that was never offered.
        window = self._window_for_path(path, workspace, interval, now)
        slot = find_slot(window, start_time)
        if slot is None:
            taken = _taken_at(window, workspace, path, start_time, interval)
            if taken:
                raise HandoffConflict(
                    f"{start_time} was on offer on path {path_id} and is now taken by "
                    + ", ".join(taken)
                    + ". Open the Handoff scheduler again for this lead's current start times"
                )
            raise HandoffError(explain_missing(window, start_time))

        assignee_ref = str(path.get("assignee_ref") or "")
        assignee = find_user(workspace, assignee_ref) or {
            "user_id": assignee_ref,
            "name": assignee_ref,
        }
        invitees = [
            {
                "user_ref": str(invitee.get("user_ref") or ""),
                "name": str(invitee.get("name") or ""),
                "email": str(invitee.get("email") or ""),
                "required": required_of(invitee),
                "gated_the_window": required_of(invitee),
            }
            for invitee in path.get("invitees") or []
        ]

        with self.store.db.transaction(actor=actor, source=source) as writer:
            meeting = writer.create(
                MEETING_COLLECTION,
                {
                    "routing_ref": routing["id"],
                    "workspace_ref": body.get("workspace_ref"),
                    "router_ref": router["id"],
                    "path_id": str(path_id),
                    "booker_ref": str(body.get("booker_ref") or ""),
                    "booker_name": str(body.get("booker_name") or ""),
                    "booker_role": BOOKER,
                    "assignee_ref": assignee_ref,
                    "assignee_name": str(assignee.get("name") or assignee_ref),
                    "assignee_email": str(assignee.get("email") or ""),
                    "assignee_role": ASSIGNEE,
                    "request_type": body.get("request_type"),
                    "guest_email": guest_email or None,
                    "crm_record_id": body.get("crm_record_id"),
                    "start_at": slot["start_at"],
                    "end_at": slot["end_at"],
                    "duration_minutes": slot.get("duration_minutes", DEFAULT_DURATION_MINUTES),
                    "state": CONFIRMED,
                    "invitees": invitees,
                    "invitee_refs": [entry["user_ref"] for entry in invitees],
                    "rechecked_at_booking": True,
                },
                room_id=room_id,
            )
            writer.update(
                routing["id"],
                {
                    "state": BOOKED,
                    "meeting_ref": meeting["id"],
                    "booked_router_ref": router["id"],
                    "booked_path_id": str(path_id),
                    "booked_start_at": slot["start_at"],
                },
            )

        return {
            "meeting": meeting,
            "meeting_id": meeting["id"],
            "routing_id": routing["id"],
            "router_id": router["id"],
            "path_id": str(path_id),
            "booker_ref": str(body.get("booker_ref") or ""),
            "assignee_ref": assignee_ref,
            "start_at": slot["start_at"],
            "end_at": slot["end_at"],
            "invitees": invitees,
            "rechecked_at_booking": True,
        }

    # ------------------------------------------------------------------ #
    # Meetings
    # ------------------------------------------------------------------ #

    def meetings(
        self,
        *,
        room_id: str | None = None,
        assignee_ref: str | None = None,
        booker_ref: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every booked meeting, newest first."""
        records = self.store.list(MEETING_COLLECTION, room_id=room_id, limit=limit)
        if assignee_ref is not None:
            records = [r for r in records if r["data"].get("assignee_ref") == assignee_ref]
        if booker_ref is not None:
            records = [r for r in records if r["data"].get("booker_ref") == booker_ref]
        if state is not None:
            records = [r for r in records if r["data"].get("state") == state]
        return records

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        return self.store.get(meeting_id)

    def cancel_meeting(
        self,
        meeting_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Cancel a booked meeting.

        The routing that produced it stays ``booked``, because its slot was taken
        and re-opening it would let the same slot be booked twice. A reassignment
        is a different transition and is owned by WF-063, not here.
        """
        meeting = self.store.get(meeting_id)
        if meeting is None:
            raise HandoffNotFound(f"meeting {meeting_id} not found")
        if meeting["data"].get("state") != CONFIRMED:
            raise HandoffConflict(
                f"meeting {meeting_id} is {meeting['data'].get('state')}, not {CONFIRMED}; "
                "it cannot be cancelled"
            )
        return self.store.update(meeting_id, {"state": CANCELLED}, actor=actor, source=source)

    # ------------------------------------------------------------------ #
    # Reads for the page
    # ------------------------------------------------------------------ #

    def catalog(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Workspaces and routers, with the gates that decide who can be reached.

        The gate sets are a product surface rather than an internal detail,
        because the Required toggle changes what a path can offer: a reader needs
        to see who gates a path and whose calendar is deliberately not read.
        """
        return {
            "link_types": [dict(entry) for entry in LINK_TYPES],
            "workspaces": [
                self.workspace_view(record)
                for record in self.workspaces(room_id=room_id, limit=200)
            ],
            "routers": [
                self.router_view(record) for record in self.routers(room_id=room_id, limit=200)
            ],
        }

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the filters return.

        Computed over the same rows the lists would return, so a room-scoped total
        above an unscoped list cannot be misread as a product-wide one.
        """
        workspaces = self.workspaces(room_id=room_id, limit=1000)
        routers = self.routers(room_id=room_id, limit=1000)
        routings = self.routings(room_id=room_id, limit=1000)
        meetings = self.meetings(room_id=room_id, limit=1000)
        by_outcome: dict[str, int] = {}
        for record in routings:
            name = str(record["data"].get("outcome") or "unset")
            by_outcome[name] = by_outcome.get(name, 0) + 1
        gated = 0
        for router in routers:
            for path in router["data"].get("paths") or []:
                gated += len(gating_user_ids(path))
                gated += len(ignored_user_ids(path))
        return {
            "workspaces": len(workspaces),
            "users": sum(len(record["data"].get("users") or []) for record in workspaces),
            "routers": len(routers),
            "paths": sum(len(record["data"].get("paths") or []) for record in routers),
            "routings": len(routings),
            "routings_open": sum(1 for r in routings if r["data"].get("state") == OPEN),
            "routings_by_outcome": by_outcome,
            "meetings": len(meetings),
            "meetings_confirmed": sum(1 for m in meetings if m["data"].get("state") == CONFIRMED),
            "routing_paths_offered": sum(int(r["data"].get("path_count") or 0) for r in routings),
            "gated_and_ignored_users": gated,
            "collections": {
                "workspace": WORKSPACE_COLLECTION,
                "router": ROUTER_COLLECTION,
                "routing": ROUTING_COLLECTION,
                "meeting": MEETING_COLLECTION,
            },
        }


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #


def _bookable_interval(body: Mapping[str, Any], router_id: str, path_id: str) -> dict[str, Any]:
    """The interval this path's slots were offered under, read back off the routing.

    Read from the stored path rather than from the routing's own ``interval``, and
    the difference is load-bearing. A routing can consult several routers and each
    may carry its own default, so the routing's top-level ``interval`` is only the
    interval the *caller* asked for. Re-checking a slot under the wrong interval
    would refuse a start time the SDR was legitimately shown, so each path records
    the interval its window was built from and booking reads that.
    """
    for path in body.get("paths") or []:
        if str(path.get("router_ref")) == str(router_id) and str(path.get("path_id")) == str(
            path_id
        ):
            stored = path.get("interval")
            if stored:
                return normalise_interval(stored, default_minutes=DEFAULT_DURATION_MINUTES)
    return normalise_interval(body.get("interval"), default_minutes=DEFAULT_DURATION_MINUTES)


def _matched_fields(match: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """The context values this path's ``match`` block was satisfied by.

    Recorded on the routing so a reader can see the values the rule actually saw,
    which is the difference between "it matched" and "it matched because the region
    was emea".
    """
    return {str(key): context[str(key)] for key in match if str(key).strip() in context}


def _taken_at(
    window: Mapping[str, Any],
    workspace: Mapping[str, Any],
    path: Mapping[str, Any],
    start_at: str,
    interval: Mapping[str, Any],
) -> list[str]:
    """Who on this path's gate set took the requested instant.

    Empty when nobody took it, which means the instant was never offered for an
    ordinary reason: outside the interval, inside the minimum notice, or not on
    the grid. That is a bad request rather than a conflict, and the two answers
    are different statuses for a reason an SDR can act on.
    """
    try:
        moment = parse(start_at)
    except ValueError:
        return []
    if not (parse(interval["start"]) <= moment < parse(interval["end"])):
        return []
    duration = int(interval.get("duration_minutes") or DEFAULT_DURATION_MINUTES)
    from datetime import timedelta

    gate_users = []
    for reference in gating_user_ids(path):
        user = find_user(workspace, reference)
        if user is not None:
            gate_users.append(user)
    return busy_at(gate_users, moment, moment + timedelta(minutes=duration))


def _unmatched_message(
    report: list[Mapping[str, Any]], context: Mapping[str, Any], workspace: Mapping[str, Any]
) -> str:
    """The one refusal an evaluation raises, with everything needed to fix it.

    Names each declared path, why it did not match, and the fields the router read,
    because "no routing path matched" on its own tells an SDR to go and change the
    router rather than to fix the value they typed. An empty path list would be
    indistinguishable from a router that was never configured.
    """
    lines = [f"no routing path matched this request in workspace {workspace['id']}"]
    for entry in report:
        lines.append(f"path {entry['path_id']} ({entry['name']}): {entry['reason']}")
    read = sorted({str(key) for entry in report for key in (entry.get("match") or {})})
    lines.append(f"the routers read: {', '.join(read) if read else 'no fields'}")
    lines.append(f"the request carried: {context!r}")
    return ". ".join(lines)
