"""The engine: read the router, evaluate the chain, answer with the researched shape.

This is the only module in the package that touches the store, and it touches it
through :class:`~dsr.store.RecordStore`, which is the product's only write path.
Every write it makes names the route that made it, so the audit row and the route
table cannot drift.

The guarantee this package exists to keep
-----------------------------------------

``qualify`` writes **nothing**. It reads a router, evaluates the chain in memory,
derives the ``routeId`` as a digest and returns. A caller can therefore prove the
side-effect-free claim rather than take it on trust: count the rows before and
after the call.

``record_verdict`` is the only writing path, it writes exactly one row, and that
row is the recorded verdict with its CRM writeback staged inside it. Nothing is
pushed anywhere: the research is explicit that "none fire beyond rule evaluation
- deliberately. No reminder, no assign, no calendar side effects."
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.lead_qualification.errors import (
    AssigneeNotFound,
    AssigneeRefused,
    IntervalSupplied,
    PayloadRefused,
    QualificationError,
    RoomRequired,
    RouterAlreadyExists,
    RouterDisabled,
    RouterNotFound,
    RouterRefused,
    VerdictNotFound,
)
from dsr.lead_qualification.rules import (
    Match,
    Rule,
    evaluate as walk_chain,
    parse_rules,
    refuses_interval,
    route_id_for,
    routing_link_for,
    validate_router,
)
from dsr.lead_qualification.vocabulary import DEFAULT_TENANT
from dsr.store import RecordStore

#: The three collections. Namespaced, because a hundred features share one table
#: and a generic name like ``router`` would collide with the next one.
ROUTER_COLLECTION = "lead_qualification_router"
ASSIGNEE_COLLECTION = "lead_qualification_assignee"
VERDICT_COLLECTION = "lead_qualification_verdict"

COLLECTIONS = (ROUTER_COLLECTION, ASSIGNEE_COLLECTION, VERDICT_COLLECTION)

#: The counter every answer carries, so "nothing happened" is visible in the
#: response and not only in this module's docstring.
NO_SIDE_EFFECTS: dict[str, int] = {
    "records_written": 0,
    "routing_sessions_consumed": 0,
    "availability_queries": 0,
    "calendar_reads": 0,
    "slots_computed": 0,
    "automations_fired": 0,
}


def _now() -> str:
    """UTC, in the same format the audited store writes."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LeadQualificationEngine:
    """Qualify a lead against a Concierge router, without scheduling anything.

    The clock is a constructor argument rather than a call to
    :func:`datetime.now` inside the body so a test can drive the whole workflow
    with rows of its own and a clock it holds still.
    """

    def __init__(self, store: RecordStore, *, clock: Callable[[], str] | None = None) -> None:
        self.store = store
        self.clock = clock or _now

    # -- routers ------------------------------------------------------------ #

    def get_router(self, router_slug: str) -> dict[str, Any] | None:
        """The live router with that slug, or None."""
        found = self.store.find(ROUTER_COLLECTION, {"router_slug": str(router_slug)}, limit=1)
        return found[0] if found else None

    def require_router(self, router_slug: str) -> dict[str, Any]:
        """The live router, or a 404 that names the slug."""
        record = self.get_router(router_slug)
        if record is None:
            raise RouterNotFound(f"router {router_slug} is not declared")
        return record

    def routers(self, *, enabled: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Every live router, newest first, optionally filtered on ``enabled``."""
        records = self.store.list(ROUTER_COLLECTION, limit=limit)
        if enabled is None:
            return records
        return [row for row in records if bool(row["data"].get("enabled", True)) is enabled]

    def create_router(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Declare a router, refusing anything the validator objects to.

        Validated in full before the row is created, so a chain with no catch-all
        cannot leave a router behind that would fail to route an inbound lead.
        """
        spec = dict(payload or {})
        self._refuse_router_problems(spec)
        slug = str(spec["router_slug"]).strip()
        if self.get_router(slug) is not None:
            raise RouterAlreadyExists(f"router {slug} is already declared")
        return self.store.create(
            ROUTER_COLLECTION, self._router_data(spec), actor=actor, source=source
        )

    def update_router(
        self, router_slug: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Patch a router, re-validating the whole merged result.

        Re-validated rather than patched, because every field the validator checks
        interacts: adding a second catch-all has to fail the way a fresh
        declaration would, and reordering the chain must not leave the floor
        somewhere unreachable.

        The slug is dropped from the patch rather than merged. It is the researched
        path segment, and a record that no longer answers to its own path is a
        record nobody can find again.
        """
        existing = self.require_router(router_slug)
        patch = {key: value for key, value in dict(payload or {}).items() if key != "router_slug"}
        merged = {**existing["data"], **patch}
        merged["router_slug"] = existing["data"].get("router_slug", router_slug)
        self._refuse_router_problems(merged)
        return self.store.update(existing["id"], patch, actor=actor, source=source)

    def delete_router(self, router_slug: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Soft-delete a router. Its recorded verdicts stay readable.

        The verdicts are the history of who a lead was proposed to, and that
        outlives the router that proposed them.
        """
        existing = self.require_router(router_slug)
        return self.store.delete(existing["id"], actor=actor, source=source)

    def preview_router(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a draft router without saving it, and report what would match.

        The advisory half of the save: an admin can see that a draft has no
        catch-all, and can see which rule a sample lead would hit, before anyone
        publishes anything.
        """
        spec = dict(payload or {})
        problems = validate_router(spec)
        sample = spec.get("sample") if isinstance(spec.get("sample"), Mapping) else {}
        form, crm = self._lead_parts(sample) if sample else ({}, {})
        trial = self._evaluate(dict(spec), form, crm)
        return {
            "problems": problems,
            "valid": not problems,
            "rules": len(spec.get("rules") or [])
            if isinstance(spec.get("rules"), (list, tuple))
            else 0,
            "sample_verdict": trial["verdict"] if form else None,
            "sample_rule": trial["matched_rule"]["name"] if trial["matched_rule"] else None,
            "sample_conditions": trial["conditions"] if form else [],
            "saved": False,
        }

    # -- assignees ---------------------------------------------------------- #

    def assignees(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every user a rule is allowed to propose, newest first."""
        return self.store.list(ASSIGNEE_COLLECTION, limit=limit)

    def get_assignee(self, user_id: str) -> dict[str, Any] | None:
        """The assignee row for a user id, or None."""
        found = self.store.find(ASSIGNEE_COLLECTION, {"user_id": str(user_id)}, limit=1)
        return found[0] if found else None

    def require_assignee(self, user_id: str) -> dict[str, Any]:
        """The assignee row, or a 404 that names the id."""
        record = self.get_assignee(user_id)
        if record is None:
            raise AssigneeNotFound(f"assignee {user_id} is not declared")
        return record

    def create_assignee(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Declare a user a rule may propose as an owner."""
        spec = dict(payload or {})
        user_id = str(spec.get("user_id") or "").strip()
        if not user_id:
            raise AssigneeRefused("user_id is required; a rule names an owner by this id.")
        if not str(spec.get("name") or "").strip():
            raise AssigneeRefused(f"assignee {user_id} needs a name to be listed.")
        if self.get_assignee(user_id) is not None:
            raise AssigneeRefused(f"assignee {user_id} is already declared")
        data = {"user_id": user_id}
        data.update({key: value for key, value in spec.items() if key != "user_id"})
        return self.store.create(ASSIGNEE_COLLECTION, data, actor=actor, source=source)

    def delete_assignee(self, user_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Soft-delete an assignee row.

        Routers that name the id keep working, and their leads answer
        ``unroutable``. That is the state a rep needs to see, so it is reported
        rather than refused.
        """
        existing = self.require_assignee(user_id)
        return self.store.delete(existing["id"], actor=actor, source=source)

    # -- the researched call ------------------------------------------------ #

    def qualify(
        self,
        router_slug: str,
        payload: Mapping[str, Any],
        *,
        router: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate the chain and answer. Writes nothing at all.

        Refuses a body carrying an ``interval``: that field is the researched
        difference between this call and the booking one, so accepting it here
        would answer half of the other workflow.

        A disabled router refuses rather than qualifying: the research publishes a
        router before it serves a lead.
        """
        body = dict(payload or {})
        if refuses_interval(body):
            raise IntervalSupplied(
                "This call qualifies a lead without offering a calendar, so it takes no interval. "
                "The difference is whether you pass an interval. Use the scheduling workflow to "
                "compute slots."
            )
        record = (
            dict(router) if router is not None else self.require_router(router_slug).get("data", {})
        )
        if not bool(record.get("enabled", True)):
            raise RouterDisabled(
                f"router {router_slug} is not published, so it does not qualify a lead"
            )
        form, crm = self._lead_parts(body)
        if not form:
            raise PayloadRefused(
                "form is required: the researched call takes form data and no interval"
            )
        answer = self._evaluate(record, form, crm)
        answer["router_slug"] = str(record.get("router_slug") or router_slug)
        answer["evaluated_at"] = self.clock()
        return answer

    def record_verdict(
        self,
        router_slug: str,
        payload: Mapping[str, Any],
        *,
        room_id: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Qualify a lead and keep the answer as one audited row.

        The second of the research's two caller paths: "or simply writes the
        qualification result into the CRM". The CRM writeback is **staged** inside
        the row rather than pushed anywhere, because the research says no
        automation fires beyond rule evaluation. Applying it is a downstream
        node's work, and this workflow does not do it.
        """
        answer = self.qualify(router_slug, payload)
        body = dict(payload or {})
        data = {
            "router_slug": answer["router_slug"],
            "route_id": answer["route_id"],
            "verdict": answer["verdict"],
            "scheduling_allowed": answer["scheduling_allowed"],
            "assignment": answer["assignment"],
            "matched_rule": answer["matched_rule"],
            "conditions": answer["conditions"],
            "fallthrough": answer["fallthrough"],
            "form": body.get("form") or {},
            "crm": body.get("crm") or {},
            "routing_link": answer["routing_link"],
            "evaluated_at": answer["evaluated_at"],
            "crm_writeback": answer["crm_writeback"],
            "side_effects": {**answer["side_effects"], "records_written": 1},
        }
        record = self.store.create(
            VERDICT_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )
        return {**record, "qualification": answer}

    # -- recorded verdicts -------------------------------------------------- #

    def verdicts(
        self,
        *,
        room_id: str | None = None,
        router_slug: str | None = None,
        verdict: str | None = None,
        scheduling_allowed: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recorded verdicts, newest first, narrowed by the dynamic index."""
        filters: dict[str, Any] = {}
        if router_slug:
            filters["router_slug"] = str(router_slug)
        if verdict:
            filters["verdict"] = str(verdict)
        if scheduling_allowed is not None:
            filters["scheduling_allowed"] = bool(scheduling_allowed)
        if filters:
            found = self.store.find(VERDICT_COLLECTION, filters, limit=limit)
            if room_id is not None:
                found = [row for row in found if row["room_id"] == room_id]
            return found
        return self.store.list(VERDICT_COLLECTION, room_id=room_id, limit=limit)

    def get_verdict(self, verdict_id: str) -> dict[str, Any] | None:
        """One recorded verdict, or None."""
        return self.store.get(verdict_id)

    def require_verdict(self, verdict_id: str) -> dict[str, Any]:
        """One recorded verdict, or a 404 that names the id."""
        record = self.get_verdict(verdict_id)
        if record is None:
            raise VerdictNotFound(f"verdict {verdict_id} is not recorded")
        return record

    # -- summary ------------------------------------------------------------ #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts for the page header, over exactly the rows the filters return.

        Computed from the same call the list endpoint uses, so a room-scoped total
        above an unscoped list cannot be read as a product-wide one.
        """
        verdicts = self.verdicts(room_id=room_id, limit=1000)
        by_verdict: dict[str, int] = {}
        by_router: dict[str, int] = {}
        for record in verdicts:
            by_verdict[str(record["data"].get("verdict"))] = (
                by_verdict.get(str(record["data"].get("verdict")), 0) + 1
            )
            slug = str(record["data"].get("router_slug"))
            by_router[slug] = by_router.get(slug, 0) + 1
        allowed = sum(1 for row in verdicts if row["data"].get("scheduling_allowed") is True)
        return {
            "room_id": room_id,
            "routers": len(self.routers(limit=1000)),
            "assignees": len(self.assignees(limit=1000)),
            "verdicts": len(verdicts),
            "scheduling_allowed": allowed,
            "scheduling_refused": len(verdicts) - allowed,
            "by_verdict": dict(sorted(by_verdict.items())),
            "by_router": dict(sorted(by_router.items())),
            "collections": list(COLLECTIONS),
            "side_effects": dict(NO_SIDE_EFFECTS),
        }

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _lead_parts(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split a request into the form payload and the CRM values it carries."""
        form = payload.get("form")
        crm = payload.get("crm")
        return (
            dict(form) if isinstance(form, Mapping) and form else {},
            dict(crm) if isinstance(crm, Mapping) else {},
        )

    @staticmethod
    def _router_data(spec: Mapping[str, Any]) -> dict[str, Any]:
        """The stored shape of a router. Fields are plain JSON, so a team adds one
        without a migration and without touching this module."""
        return {
            "router_slug": str(spec["router_slug"]).strip(),
            "name": str(spec.get("name") or "").strip(),
            "tenant": str(spec.get("tenant") or DEFAULT_TENANT).strip(),
            "enabled": bool(spec.get("enabled", True)),
            "rules": list(spec.get("rules") or []),
            "notes": str(spec.get("notes") or ""),
        }

    @staticmethod
    def _refuse_router_problems(spec: Mapping[str, Any]) -> None:
        """Refuse a declaration the validator objects to, listing every problem."""
        problems = validate_router(spec)
        if problems:
            raise RouterRefused("; ".join(problems))

    def _evaluate(
        self, record: Mapping[str, Any], form: Mapping[str, Any], crm: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Walk the chain and answer in the researched shape.

        No write happens here and none can: the only value produced from the
        store is a lookup, and the only id produced is a digest.
        """
        slug = str(record.get("router_slug") or "")
        parsed = parse_rules(record.get("rules"))
        match: Match = walk_chain(parsed, form, crm)
        route_id = route_id_for(slug, {"form": form, "crm": crm})
        link = routing_link_for(str(record.get("tenant") or ""), slug, route_id)
        verdict, assignment, reason = self._decide(match, self.assignee_ids())
        rule = match.rule
        return {
            "route_id": route_id,
            "routeId": route_id,
            "routing_link": link,
            "routingLink": link,
            "verdict": verdict,
            "scheduling_allowed": verdict == "qualified",
            "schedulingAllowed": verdict == "qualified",
            "assignment": assignment,
            "reason": reason,
            "matched_rule": self._rule_summary(rule),
            "conditions": match.conditions,
            "fallthrough": match.fallthrough,
            "evaluated_rules": match.evaluated,
            "crm_writeback": self._writeback(match, verdict),
            "interval_supplied": False,
            "side_effects": dict(NO_SIDE_EFFECTS),
        }

    def assignee_ids(self) -> set[str]:
        """Every user id this workspace may propose as an owner."""
        return {str(row["data"].get("user_id")) for row in self.assignees(limit=1000)}

    @staticmethod
    def _decide(match: Match, known: set[str]) -> tuple[str, dict[str, Any], str]:
        """The three verdicts, and the reason for the one that is not qualified.

        ``unroutable`` is what the research asks for when it says "Confirm a lead
        is routable" and does not say what an unroutable lead answers with. It is
        a verdict rather than an error because the caller still needs the
        ``routeId``: it is the lead's own answer, not a failed request.
        """
        rule = match.rule
        if rule is None:
            return (
                "unroutable",
                {"userId": "", "type": "unassigned"},
                "No rule in the chain held, and the chain has no catch-all to fall through to.",
            )
        if not rule.assign_user_id:
            return (
                "unroutable",
                {"userId": "", "type": "unassigned"},
                f"Rule '{rule.name}' matched but names no owner.",
            )
        if rule.assign_user_id not in known:
            return (
                "unroutable",
                {"userId": "", "type": "unassigned"},
                f"Rule '{rule.name}' proposes {rule.assign_user_id}, which is not a user in this "
                "workspace.",
            )
        if rule.scheduling_allowed:
            return (
                "qualified",
                {"userId": rule.assign_user_id, "type": "user"},
                f"Rule '{rule.name}' matched and proposes {rule.assign_user_id}.",
            )
        return (
            "not_scheduled",
            {"userId": rule.assign_user_id, "type": "user"},
            f"Rule '{rule.name}' matched and refuses a scheduler, so no calendar is offered.",
        )

    @staticmethod
    def _rule_summary(rule: Rule | None) -> dict[str, Any] | None:
        """The matched rule as a caller reads it."""
        if rule is None:
            return None
        return {
            "index": rule.index,
            "name": rule.name,
            "assign_user_id": rule.assign_user_id,
            "scheduling_allowed": rule.scheduling_allowed,
        }

    @staticmethod
    def _writeback(match: Match, verdict: str) -> dict[str, Any] | None:
        """The CRM write a Not Scheduled or Disqualified path would apply.

        Staged, never applied. The research lists these writeback nodes as
        available "separately on Not Scheduled / Disqualified paths", so the row
        carries what a node would write and records that nothing wrote it.
        """
        rule = match.rule
        if verdict == "qualified" or rule is None:
            return None
        fields: dict[str, Any] = {
            "qualification_verdict": verdict,
            "scheduling_allowed": verdict == "qualified",
            "proposed_owner_id": rule.assign_user_id,
        }
        if rule.crm_writeback:
            fields.update(dict(rule.crm_writeback))
        return {"object": "lead", "fields": fields, "applied": False, "staged_by": "wf-052"}


def require_room(engine: LeadQualificationEngine, room_id: str) -> None:
    """Refuse a room-scoped write against a room that is not there.

    Lives here rather than in the route so the domain tests cover it without
    building an app.
    """
    if engine.store.get(str(room_id)) is None:
        raise RoomRequired(f"room {room_id} does not exist")


__all__ = [
    "COLLECTIONS",
    "ASSIGNEE_COLLECTION",
    "LeadQualificationEngine",
    "NO_SIDE_EFFECTS",
    "QualificationError",
    "ROUTER_COLLECTION",
    "VERDICT_COLLECTION",
    "require_room",
]
