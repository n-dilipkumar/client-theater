"""The facade the HTTP layer calls. Owns the six collections and every write.

Everything in this package is reached through :class:`LeadScoreEngine`, which holds
nothing but a store handle and a clock. It is built per request by the feature module
rather than stored on ``app.state``, because an ``app.state`` entry is exactly the
edit to the shared ``dsr/api.py`` that the feature host exists to make unnecessary.
Building it here also leaves the engine a plain object, which is what a test
constructs.

The six things a caller can ask for, in the order the researched flow reaches them:

1. :meth:`register_integration` - step 1, the organisation and its two scopes.
2. :meth:`provision_property` - the Dock activity properties a criterion scores against.
3. :meth:`add_criterion` - steps 2 to 5, one "Add criteria" row.
4. :meth:`record_activity` - the source side: a DSR event arriving.
5. :meth:`score` - the continuous rule: re-evaluate and record what would be written.
6. :meth:`summary` and :meth:`history` - what a page reads.

``source=`` comes from the route
--------------------------------
Every write below takes a ``source`` and the feature module passes
``f"{METHOD} {router.prefix}..."``, so the audit row names the route that actually
served it. A hardcoded string inside a domain method is a defect, and the same class
of bug has shipped in this codebase before: a feature's audit log kept naming a path
the app had stopped serving.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.db.audited import AuditedDatabase
from dsr.lead_score import names
from dsr.lead_score.activity import normalise_activity
from dsr.lead_score.criteria import (
    describe_matcher,
    lint_criterion,
    matches,
    parse_criterion,
    tally_reasons,
)
from dsr.lead_score.errors import (
    CrmOrgDisabled,
    CrmOrgNotConnected,
    LeadScoreError,
    MissingCrmScope,
    UnknownCriterion,
)
from dsr.lead_score.inferences import describe as describe_inferences
from dsr.lead_score.scoring import (
    batch_plan_for,
    contributions_for,
    crm_plan_for,
    recompute,
    url_path,
)
from dsr.lead_score.vocabulary import (
    BUCKET_SIGN,
    CRM_PLAN,
    DEFAULT_INTEGRATION,
    EXECUTION_NOTE,
    FILTER_FAMILIES,
    REQUIRED_SCOPES,
    SCORE_PROPERTY,
    describe as describe_vocabulary,
    published_family_names,
)
from dsr.store import RecordStore

#: The activity a run reads for one contact. The store caps a page at 1000 rows, and
#: a report that says "here are the first 1000" is honest where silently truncating is
#: not. A contact with more than this has a finding rather than a quiet cut.
MAX_EVENTS_PER_CONTACT = 1000

#: Room scoping has two mechanisms and picking the wrong one silently returns nothing.
#:
#: ``room_id`` is a *column* on the records table, and :meth:`AuditedDatabase.find`
#: filters through ``record_index``, which is built only from ``data``. ``room_id`` is
#: stripped out of ``data`` on insert because it is one of the reserved envelope keys,
#: so ``find(collection, {"room_id": ...})`` matches nothing, ever, and does so without
#: an error. Every room-scoped read below therefore lists by the column and filters the
#: room in Python, or filters by a ``data`` key first and checks the column after.
#: This constant names the two ways so the next reader does not have to rediscover it.
ROOM_SCOPED_BY_COLUMN = "store.list(room_id=...) then filter in Python"
ROOM_FILTERED_IN_SQL = "store.find(<a data key>) then filter room_id in Python"


def _in_room(record: Mapping[str, Any], room_id: str) -> bool:
    """Whether a record belongs to a room, read from the column rather than the payload."""
    return str(record.get("room_id") or "") == str(room_id)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LeadScoreEngine:
    """Lead-score criteria, DSR activity, and the score each contact carries."""

    def __init__(
        self,
        store: RecordStore | AuditedDatabase,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.store = store if isinstance(store, RecordStore) else RecordStore(store)
        self._now = now or _utcnow

    # ------------------------------------------------------------------ #
    # Vocabulary and inferences
    # ------------------------------------------------------------------ #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data."""
        served = describe_vocabulary()
        served["matcher"] = describe_matcher()
        served["collections"] = {
            "integration": names.INTEGRATIONS,
            "property": names.PROPERTIES,
            "criterion": names.CRITERIA,
            "activity": names.ACTIVITY,
            "contact": names.CONTACTS,
            "run": names.RUNS,
        }
        return served

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return describe_inferences()

    # ------------------------------------------------------------------ #
    # Step 1: the CRM organisation
    # ------------------------------------------------------------------ #

    def integrations(self) -> dict[str, Any]:
        """The registered organisations, each with the scopes it still needs."""
        listed = [
            self._integration_view(record)
            for record in self.store.list(names.INTEGRATIONS, limit=100)
        ]
        return {
            "count": len(listed),
            "integrations": listed,
            "required_scopes": list(REQUIRED_SCOPES),
            "execution_note": EXECUTION_NOTE,
        }

    @staticmethod
    def _integration_view(record: Mapping[str, Any]) -> dict[str, Any]:
        """One organisation as a response row.

        The id is the record id rather than a copy kept inside ``data``. A copy would
        need a second write to fill in, and the store mints the id, so a copy can only
        ever be written by an update that duplicates an insert in the audit log.
        """
        data = record.get("data") or {}
        held = {str(scope) for scope in (data.get("scopes") or [])}
        missing = [scope for scope in REQUIRED_SCOPES if scope not in held]
        connections = dict(data.get("deal_connections") or {})
        return {
            "id": str(record.get("id") or ""),
            "vendor": str(data.get("vendor") or DEFAULT_INTEGRATION),
            "label": str(data.get("label") or ""),
            "enabled": bool(data.get("enabled", True)),
            "portal_id": str(data.get("portal_id") or ""),
            "scopes": sorted(held),
            "missing_scopes": missing,
            "writable": bool(data.get("enabled", True)) and not missing,
            "minimum_score": data.get("minimum_score"),
            "deal_connections": connections,
            "connected_rooms": sorted(connections),
            "crm_plan": dict(CRM_PLAN),
        }

    def register_integration(
        self, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Step 1: register the CRM organisation the criteria will score into.

        The two scopes ride on the row because they decide whether a criterion can be
        armed at all: a token with only ``crm.objects.contacts.read`` can look a
        contact up and cannot move its score.
        """
        vendor = str(payload.get("vendor") or DEFAULT_INTEGRATION).strip().lower()
        if not vendor:
            vendor = DEFAULT_INTEGRATION
        scopes = payload.get("scopes")
        held = (
            sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
            if isinstance(scopes, (list, tuple))
            else []
        )
        connections = dict(payload.get("deal_connections") or {})
        data = {
            "vendor": vendor,
            "label": str(payload.get("label") or f"{vendor.title()} portal"),
            "enabled": bool(payload.get("enabled", True)),
            "portal_id": str(payload.get("portal_id") or ""),
            "scopes": held,
            "deal_connections": connections,
            "minimum_score": payload.get("minimum_score"),
            "recorded_at": self._now(),
            "token_note": (
                "No token is stored and no token is used. This build records the organisation "
                "and its scopes; see the execution note at /api/wf-029/vocabulary."
            ),
        }
        record = self.store.create(names.INTEGRATIONS, data, actor=actor, source=source)
        return {"created": True, "integration": self._integration_view(record)}

    def amend_integration(
        self, integration_id: str, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Switch the organisation on or off, add a scope, or connect a room to a deal.

        ``deal_connections`` maps a room id onto the deal it is connected to. A null
        value removes that room's entry, which is how a merge patch expresses
        "unlink" everywhere else in this product - and step 1's "connected to a
        deal/account" has to be able to become untrue.
        """
        record = self.store.get(integration_id)
        if record is None or record["collection"] != names.INTEGRATIONS:
            raise CrmOrgNotConnected(
                f"no CRM organisation is registered under {integration_id!r}. Step 1 of the "
                f"researched flow registers one before any criterion can be saved."
            )
        current = dict(record["data"])
        patch: dict[str, Any] = {}
        for key in ("label", "portal_id"):
            if key in payload:
                patch[key] = payload[key]
        if "enabled" in payload:
            patch["enabled"] = bool(payload["enabled"])
        if "scopes" in payload and isinstance(payload["scopes"], (list, tuple)):
            patch["scopes"] = sorted({str(scope).strip() for scope in payload["scopes"]})
        if "minimum_score" in payload:
            minimum = payload["minimum_score"]
            patch["minimum_score"] = None if minimum is None else int(minimum)
        if "deal_connections" in payload:
            if not isinstance(payload["deal_connections"], Mapping):
                raise LeadScoreError("deal_connections must be an object keyed by room id")
            connections = dict(current.get("deal_connections") or {})
            for room_id, value in dict(payload["deal_connections"]).items():
                if value is None:
                    connections.pop(str(room_id), None)
                else:
                    connections[str(room_id)] = (
                        dict(value) if isinstance(value, Mapping) else {"deal_id": str(value)}
                    )
            patch["deal_connections"] = connections
        saved = self.store.update(integration_id, patch, actor=actor, source=source)
        return {"amended": True, "integration": self._integration_view(saved)}

    def _require_writable_org(self) -> dict[str, Any]:
        """The organisation a write may go through, or refuse and say what is missing."""
        records = self.store.list(names.INTEGRATIONS, limit=100)
        if not records:
            raise CrmOrgNotConnected(
                "no CRM organisation is registered. Step 1 of the researched flow is "
                "'Confirm the Dock and HubSpot integration is enabled', and without one there "
                "is no contact property to write and no token to write it with."
            )
        enabled = [r for r in records if r["data"].get("enabled", True)]
        if not enabled:
            raise CrmOrgDisabled(
                "every registered CRM organisation is switched off. The researched step is "
                "'Verify the HubSpot integration is on', and saving a criterion now would "
                "create a rule that silently never moves a score."
            )
        ready = [r for r in enabled if not self._missing_scopes(r["data"])]
        if not ready:
            missing = sorted(
                {scope for record in enabled for scope in self._missing_scopes(record["data"])}
            )
            raise MissingCrmScope(
                f"no registered token carries {', '.join(missing)}. The research names "
                f"{' and '.join(REQUIRED_SCOPES)}, and a criterion armed on a token that "
                f"cannot write would report a score nobody received."
            )
        return self._integration_view(ready[0])

    @staticmethod
    def _missing_scopes(data: Mapping[str, Any]) -> list[str]:
        held = {str(scope) for scope in (data.get("scopes") or [])}
        return [scope for scope in REQUIRED_SCOPES if scope not in held]

    # ------------------------------------------------------------------ #
    # Step 1b: the Dock activity properties
    # ------------------------------------------------------------------ #

    def properties(self) -> dict[str, Any]:
        """The Dock activity contact properties a criterion can score against.

        The issue records these as the output of provisioning the engagement object.
        A criterion naming a property that is not here still saves and still scores
        nothing, and the run says so - see the ``missing-dock-property-matches-nothing``
        inference.
        """
        listed = [
            self._property_view(record) for record in self.store.list(names.PROPERTIES, limit=200)
        ]
        provisioned = {entry["family"] for entry in listed if entry["state"] == "provisioned"}
        return {
            "count": len(listed),
            "properties": listed,
            "families": list(FILTER_FAMILIES),
            "provisioned": [name for name in FILTER_FAMILIES if name in provisioned],
            "awaiting_provisioning": [name for name in FILTER_FAMILIES if name not in provisioned],
            "provisioning_note": (
                "These properties come from provisioning the sales-room engagement object into "
                "the CRM. Until they exist, every criterion matches nothing and the score never "
                "moves, which is the dependency the issue records."
            ),
        }

    def provision_property(
        self, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Record one Dock activity contact property as provisioned.

        Recorded rather than created: the research's write path for a property is
        ``POST /crm/v3/properties``, and this build makes no outbound call. The row is
        the assertion a criteria checks against, so recording it is what arms the
        scoring.
        """
        data = {
            "family": str(payload.get("family") or "").strip().lower(),
            "name": str(payload.get("name") or "").strip(),
            "object": str(payload.get("object") or "contact"),
            "state": str(payload.get("state") or "provisioned"),
            "provisioned_at": self._now(),
            "note": str(payload.get("note") or ""),
        }
        existing = self.store.find(
            names.PROPERTIES, {"family": data["family"], "name": data["name"]}, limit=1
        )
        if existing:
            self.store.update(
                existing[0]["id"],
                {"state": data["state"], "provisioned_at": data["provisioned_at"]},
                actor=actor,
                source=source,
            )
            return {
                "created": False,
                "property": self._property_view(self.store.get(existing[0]["id"])),
            }
        record = self.store.create(names.PROPERTIES, data, actor=actor, source=source)
        return {"created": True, "property": self._property_view(record)}

    @staticmethod
    def _property_view(record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data") or {}
        return {
            "id": str(record.get("id") or ""),
            "family": str(data.get("family") or ""),
            "name": str(data.get("name") or ""),
            "object": str(data.get("object") or "contact"),
            "state": str(data.get("state") or "provisioned"),
            "provisioned_at": str(data.get("provisioned_at") or ""),
            "note": str(data.get("note") or ""),
        }

    # ------------------------------------------------------------------ #
    # Steps 2 to 5: the criteria
    # ------------------------------------------------------------------ #

    def criteria(self, *, bucket: str | None = None, family: Any = None) -> dict[str, Any]:
        """Every saved criterion, each with its lint and its resolved property."""
        where: dict[str, Any] = {}
        if bucket:
            where["bucket"] = str(bucket).strip().lower()
        if family:
            where["family"] = str(family).strip().lower()
        records = (
            self.store.find(names.CRITERIA, where, limit=500)
            if where
            else self.store.list(names.CRITERIA, limit=500)
        )
        provisioned = self._provisioned_families()
        listed = [self._criterion_view(record, provisioned) for record in records]
        return {
            "count": len(listed),
            "criteria": listed,
            "provisioned_families": sorted(provisioned),
            "armed": sum(1 for entry in listed if entry["armed"]),
        }

    def criterion(self, criterion_id: str) -> dict[str, Any] | None:
        record = self.store.get(criterion_id)
        if record is None or record["collection"] != names.CRITERIA:
            return None
        return self._criterion_view(record, self._provisioned_families())

    @staticmethod
    def _criterion_view(record: Mapping[str, Any], provisioned: set[str]) -> dict[str, Any]:
        """One criterion as a response row.

        The id is the record id, for the same reason the integration's is: the store
        mints it on insert, so a copy kept inside ``data`` is either empty or costs a
        second write to fill in.
        """
        data = record.get("data") or {}
        family = str(data.get("family") or "")
        armed = bool(data.get("enabled", True)) and family in provisioned
        return {
            "id": str(record.get("id") or ""),
            "family": family,
            "label": str(data.get("label") or ""),
            "refinements": dict(data.get("refinements") or {}),
            "bucket": str(data.get("bucket") or ""),
            "score": int(data.get("score") or 0),
            "score_property": str(data.get("score_property") or SCORE_PROPERTY),
            "property_resolved": bool(data.get("property_resolved", True)),
            "enabled": bool(data.get("enabled", True)),
            "armed": armed,
            "provisioned": family in provisioned,
            "lint": list(data.get("lint") or []),
            "created_at": str(data.get("created_at") or ""),
            "sign": BUCKET_SIGN.get(str(data.get("bucket") or ""), 0),
        }

    def _provisioned_families(self) -> set[str]:
        return {
            str(record["data"].get("family") or "")
            for record in self.store.list(names.PROPERTIES, limit=200)
            if record["data"].get("state") == "provisioned"
        }

    def add_criterion(
        self, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Steps 2 to 5: save one "Add criteria" row.

        Arming is refused against a switched-off organisation or a token without the two
        scopes, because a criterion that can never move a score is worse than one that
        will not save. The criterion needs no publish step: the researched flow ends at
        "Save; subsequent buyer activity in the DSR moves the contact's score
        automatically."
        """
        org = self._require_writable_org()
        parsed = parse_criterion(payload)
        parsed["lint"] = lint_criterion(parsed)
        parsed["enabled"] = bool(payload.get("enabled", True))
        parsed["integration_id"] = org["id"]
        parsed["created_at"] = self._now()
        record = self.store.create(names.CRITERIA, parsed, actor=actor, source=source)
        return {"criterion": self._criterion_view(record, self._provisioned_families())}

    def amend_criterion(
        self, criterion_id: str, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """Change a saved criterion in place.

        Amendable while armed, and that follows from the score being derived rather than
        accumulated: an edit takes effect at the next event with nothing to republish.
        """
        record = self.store.get(criterion_id)
        if record is None or record["collection"] != names.CRITERIA:
            raise UnknownCriterion(
                f"no saved criterion under {criterion_id!r}. Save one first, then change it."
            )
        current = dict(record["data"])
        merged = {**current, **dict(payload)}
        parsed = parse_criterion(merged)
        parsed["lint"] = lint_criterion(parsed)
        parsed["enabled"] = bool(merged.get("enabled", True))
        parsed["criterion_id"] = criterion_id
        parsed["created_at"] = current.get("created_at") or self._now()
        self.store.update(criterion_id, parsed, actor=actor, source=source)
        return {"criterion": self.criterion(criterion_id)}

    def withdraw_criterion(self, criterion_id: str, *, actor: str, source: str) -> dict[str, Any]:
        """Withdraw a criterion, so it stops scoring.

        Soft-deleted rather than destroyed, because the runs it produced name it and a
        score that cannot be explained is worse than a withdrawn row. Because the score
        is recomputed from history, its points come off the contact at that contact's
        next event.
        """
        record = self.store.get(criterion_id)
        if record is None or record["collection"] != names.CRITERIA:
            raise UnknownCriterion(f"no saved criterion under {criterion_id!r}.")
        removed = self.store.delete(criterion_id, actor=actor, source=source)
        return {"withdrawn": True, "id": removed["id"], "criterion_id": criterion_id}

    def _criteria(self) -> list[dict[str, Any]]:
        """Every live criterion, in the order the store returns them.

        The record id is attached as ``id`` so the contributions a run reports can
        name the criterion that scored, and so a finding can point at the row to open.
        """
        rows = []
        for record in self.store.list(names.CRITERIA, limit=500):
            row = dict(record["data"])
            row["id"] = record["id"]
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ #
    # The source side: DSR activity
    # ------------------------------------------------------------------ #

    def activities(
        self, room_id: str, *, contact: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """The DSR activity rows this workflow has read for a room."""
        wanted = str(contact).strip() if contact else ""
        records = [
            record
            for record in self.store.list(names.ACTIVITY, room_id=room_id, limit=500)
            if not wanted or str(record["data"].get("contact") or "") == wanted
        ]
        listed = [
            {
                "id": record["id"],
                "contact": str(record["data"].get("contact") or ""),
                "account": str(record["data"].get("account") or ""),
                "action": str(record["data"].get("action") or ""),
                "action_family": record["data"].get("action_family"),
                "link_name": record["data"].get("link_name"),
                "file_name": record["data"].get("file_name"),
                "task_name": record["data"].get("task_name"),
                "occurred_at": record["data"].get("occurred_at"),
                "duplicate_attempts": int(record["data"].get("duplicate_attempts") or 0),
                "warnings": list(record["data"].get("warnings") or []),
            }
            for record in records
        ]
        capped = listed[: max(1, min(int(limit), 500))]
        return {"count": len(capped), "activities": capped, "room_id": room_id}

    def record_activity(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str, source: str
    ) -> dict[str, Any]:
        """One DSR activity event arriving, and the continuous rule that follows it.

        The event is stored first and the score is recomputed from the second, so a
        contact whose event carries no usable timestamp is still on the record with the
        reason, rather than rejected at the door. The response carries both, and says
        whether the score moved.
        """
        normalised = normalise_activity(payload, room_id=room_id)
        key = normalised.get("idempotency_key")
        if key:
            existing = self.store.find(
                names.ACTIVITY, {"idempotency_key": str(key)}, limit=MAX_EVENTS_PER_CONTACT
            )
            kept = next((row for row in existing if _in_room(row, room_id)), None)
            if kept is not None:
                self.store.update(
                    kept["id"],
                    {"duplicate_attempts": int(kept["data"].get("duplicate_attempts") or 0) + 1},
                    actor=actor,
                    source=source,
                )
                return {
                    "created": False,
                    "duplicate": True,
                    "activity": self.store.get(kept["id"])["data"],
                    "score": self.score(
                        room_id,
                        {"contact": normalised["contact"]},
                        actor=actor,
                        source=source,
                        driver="duplicate_event",
                    ),
                }

        normalised["duplicate_attempts"] = 0
        normalised["stored_at"] = self._now()
        record = self.store.create(
            names.ACTIVITY, normalised, room_id=room_id, actor=actor, source=source
        )
        scored = self.score(
            room_id,
            {"contact": normalised["contact"]},
            actor=actor,
            source=source,
            driver="activity_event",
        )
        return {"created": True, "duplicate": False, "activity": record["data"], "score": scored}

    # ------------------------------------------------------------------ #
    # The continuous rule
    # ------------------------------------------------------------------ #

    def score(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str,
        source: str,
        driver: str = "manual",
    ) -> dict[str, Any]:
        """Re-evaluate one contact's score and record what would be written.

        Three outcomes, and only two of them are refusals: no CRM organisation at all,
        and no deal connected to this room. Everything else is a run, because the
        researched rule is continuous and a run that moved nothing is still the record
        of the rule having fired.
        """
        payload = payload or {}
        org = self._require_writable_org()
        contact = str(payload.get("contact") or payload.get("person") or "").strip()
        if not contact:
            raise LeadScoreError(
                "a scoring run needs the contact whose score is being recomputed. The "
                "researched score lands on a contact property, so there is no run without one."
            )

        findings: list[dict[str, Any]] = []
        connection = (org.get("deal_connections") or {}).get(room_id)
        if not connection:
            findings.append(
                {
                    "code": "room_not_deal_connected",
                    "severity": "warning",
                    "message": (
                        "This room is not connected to a deal, so the score has no account to "
                        "be attributed to. Step 1 pairs the integration with 'the workspace is "
                        "connected to a deal/account'."
                    ),
                }
            )

        events = self._events_for(room_id, contact)
        if len(events) >= MAX_EVENTS_PER_CONTACT:
            findings.append(
                {
                    "code": "history_truncated",
                    "severity": "warning",
                    "message": (
                        f"This contact has at least {len(events)} recorded events in this room "
                        f"and a run reads at most {MAX_EVENTS_PER_CONTACT}. The score is "
                        f"computed from the rows read, and the most recently stored are the "
                        f"ones kept."
                    ),
                }
            )

        criteria = self._criteria()
        if not criteria:
            findings.append(
                {
                    "code": "no_criteria_saved",
                    "severity": "info",
                    "message": (
                        "No criterion is saved, so there is nothing to score. Steps 2 to 5 of "
                        "the researched flow add one."
                    ),
                }
            )

        provisioned = self._provisioned_families()
        rows = contributions_for(criteria, events)
        for row in rows:
            if row["family"] not in provisioned:
                findings.append(
                    {
                        "code": "property_not_provisioned",
                        "severity": "warning",
                        "criterion_id": row["criterion_id"],
                        "message": (
                            f"No Dock activity property is provisioned for {row['family']!r}, "
                            f"so that criterion has nothing to score against and matches "
                            f"nothing. This is what happens without the engagement object "
                            f"provisioned into the CRM."
                        ),
                    }
                )

        previous = self._contact_row(room_id, contact)
        from_score = int(previous["data"].get("score") or 0) if previous else 0
        evaluated_at = self._now()
        recomputed = recompute(
            rows,
            previous=from_score,
            minimum_score=org.get("minimum_score"),
            evaluated_at=evaluated_at,
        )

        plan = crm_plan_for(
            contact=contact,
            room_id=room_id,
            account=str(events[0].get("account") or "") if events else "",
            score=recomputed["score"],
            score_property=self._primary_property(rows) or SCORE_PROPERTY,
            from_score=from_score,
            evaluated_at=evaluated_at,
            lifecyclestage=payload.get("lifecyclestage"),
            current_lifecycle_stage=payload.get("current_lifecycle_stage"),
        )
        payload_plan = {
            "executed": False,
            "endpoint": CRM_PLAN["create_property"],
            "method": "POST",
            "path": url_path(CRM_PLAN["create_property"]),
            "condition": (
                "only a criterion naming a property other than the researched one, and only "
                "where a third party chooses to run it"
            ),
            "unresolved_properties": sorted(
                {row["score_property"] for row in rows if not row["property_resolved"]}
            ),
        }

        run_body = {
            "room_id": room_id,
            "contact": contact,
            "driver": driver,
            "integration_id": org["id"],
            "from_score": recomputed["from_score"],
            "to_score": recomputed["to_score"],
            "delta": recomputed["delta"],
            "changed": recomputed["changed"],
            "raw_score": recomputed["raw_score"],
            "floor_applied": recomputed["floor_applied"],
            "minimum_score": recomputed["minimum_score"],
            "criterion_count": len(criteria),
            "matched_criteria": sum(1 for row in rows if row["matched_count"]),
            "event_count": len(events),
            "criteria": recomputed["criteria"],
            "contributions": recomputed["contributions"],
            "findings": findings,
            "crm_plan": plan,
            "payload_plan": payload_plan,
            "evaluated_at": evaluated_at,
        }
        run = self.store.create(names.RUNS, run_body, room_id=room_id, actor=actor, source=source)

        contact_body = {
            "contact": contact,
            "room_id": room_id,
            "account": run_body["crm_plan"]["account"],
            "score": recomputed["score"],
            "raw_score": recomputed["raw_score"],
            "from_score": recomputed["from_score"],
            "delta": recomputed["delta"],
            "minimum_score": recomputed["minimum_score"],
            "floor_applied": recomputed["floor_applied"],
            "event_count": len(events),
            "matched_criteria": run_body["matched_criteria"],
            "criterion_count": len(criteria),
            "contributions": recomputed["contributions"],
            "last_run_id": run["id"],
            "last_driver": driver,
            "evaluated_at": evaluated_at,
            "score_property": run_body["crm_plan"]["score_property"],
        }
        if previous:
            self.store.update(previous["id"], contact_body, actor=actor, source=source)
        else:
            self.store.create(
                names.CONTACTS, contact_body, room_id=room_id, actor=actor, source=source
            )

        return {
            "run_id": run["id"],
            "driver": driver,
            "contact": contact,
            "room_id": room_id,
            "score": recomputed["score"],
            "raw_score": recomputed["raw_score"],
            "from_score": recomputed["from_score"],
            "delta": recomputed["delta"],
            "changed": recomputed["changed"],
            "floor_applied": recomputed["floor_applied"],
            "minimum_score": recomputed["minimum_score"],
            "event_count": len(events),
            "criteria": recomputed["criteria"],
            "contributions": recomputed["contributions"],
            "findings": findings,
            "crm_plan": run_body["crm_plan"],
            "batch_plan": batch_plan_for([contact_body]),
            "payload_plan": payload_plan,
            "evaluated_at": evaluated_at,
        }

    def _events_for(self, room_id: str, contact: str) -> list[dict[str, Any]]:
        """One contact's activity for one room, oldest first.

        The contact is the indexed filter and the room is checked against the column,
        because ``room_id`` is not in ``data`` and a ``find`` on it matches nothing -
        see :data:`ROOM_SCOPED_BY_COLUMN`.

        Ordered by ``occurred_at`` rather than by the store's insertion order because
        the response reports the contributions in the order a reader would expect to
        meet them, and a run's own ``evaluated_at`` is not the event's time.
        """
        records = self.store.find(
            names.ACTIVITY, {"contact": contact}, limit=MAX_EVENTS_PER_CONTACT
        )
        events = []
        for record in records:
            if not _in_room(record, room_id):
                continue
            event = dict(record["data"])
            event["id"] = record["id"]
            events.append(event)
        events.sort(key=lambda row: (str(row.get("occurred_at") or ""), str(row.get("id") or "")))
        return events

    @staticmethod
    def _primary_property(rows: Any) -> str | None:
        """The property the run writes: the first resolved one, else the researched one."""
        for row in rows or ():
            if row.get("property_resolved"):
                return str(row.get("score_property") or "") or None
        return None

    def _contact_row(self, room_id: str, contact: str) -> dict[str, Any] | None:
        found = self.store.find(names.CONTACTS, {"contact": contact}, limit=MAX_EVENTS_PER_CONTACT)
        return next((row for row in found if _in_room(row, room_id)), None)

    # ------------------------------------------------------------------ #
    # Reads for the page
    # ------------------------------------------------------------------ #

    def scores(self, room_id: str, *, limit: int = 100) -> dict[str, Any]:
        """Every contact's score in a room, highest first."""
        listed = [
            {
                "contact": str(record["data"].get("contact") or ""),
                "account": str(record["data"].get("account") or ""),
                "score": int(record["data"].get("score") or 0),
                "raw_score": int(record["data"].get("raw_score") or 0),
                "event_count": int(record["data"].get("event_count") or 0),
                "matched_criteria": int(record["data"].get("matched_criteria") or 0),
                "criterion_count": int(record["data"].get("criterion_count") or 0),
                "contributions": list(record["data"].get("contributions") or []),
                "evaluated_at": str(record["data"].get("evaluated_at") or ""),
                "last_driver": str(record["data"].get("last_driver") or ""),
                "floor_applied": bool(record["data"].get("floor_applied")),
            }
            for record in self.store.list(names.CONTACTS, room_id=room_id, limit=500)
        ]
        listed.sort(key=lambda row: (-row["score"], row["contact"]))
        capped = listed[: max(1, min(int(limit), 500))]
        return {
            "count": len(capped),
            "total": len(listed),
            "scores": capped,
            "room_id": room_id,
        }

    def contact_score(self, room_id: str, contact: str) -> dict[str, Any] | None:
        """One contact's score, with the run that last produced it."""
        record = self._contact_row(room_id, contact)
        if record is None:
            return None
        history = [
            run
            for run in self.store.list(names.RUNS, room_id=room_id, limit=100)
            if str(run["data"].get("contact") or "") == contact
        ][:20]
        return {
            **record["data"],
            "history": [
                {
                    "run_id": run["id"],
                    "driver": str(run["data"].get("driver") or ""),
                    "from_score": int(run["data"].get("from_score") or 0),
                    "to_score": int(run["data"].get("to_score") or 0),
                    "delta": int(run["data"].get("delta") or 0),
                    "changed": bool(run["data"].get("changed")),
                    "evaluated_at": str(run["data"].get("evaluated_at") or ""),
                }
                for run in history
            ],
        }

    def history(
        self, room_id: str, *, contact: str | None = None, moved_only: bool = False, limit: int = 50
    ) -> dict[str, Any]:
        """Every run, or only the runs whose number moved.

        Both, because the researched rule is continuous: a run that moved nothing is
        still the record of the rule having fired, and a reader debugging "why is this
        score what it is" wants the ones that moved.
        """
        wanted = str(contact).strip() if contact else ""
        records = [
            record
            for record in self.store.list(names.RUNS, room_id=room_id, limit=500)
            if not wanted or str(record["data"].get("contact") or "") == wanted
        ]
        rows = []
        for record in records:
            data = record["data"]
            changed = bool(data.get("changed"))
            if moved_only and not changed:
                continue
            rows.append(
                {
                    "run_id": record["id"],
                    "contact": str(data.get("contact") or ""),
                    "driver": str(data.get("driver") or ""),
                    "from_score": int(data.get("from_score") or 0),
                    "to_score": int(data.get("to_score") or 0),
                    "delta": int(data.get("delta") or 0),
                    "changed": changed,
                    "raw_score": int(data.get("raw_score") or 0),
                    "floor_applied": bool(data.get("floor_applied")),
                    "matched_criteria": int(data.get("matched_criteria") or 0),
                    "criterion_count": int(data.get("criterion_count") or 0),
                    "event_count": int(data.get("event_count") or 0),
                    "findings": list(data.get("findings") or []),
                    "miss_tally": tally_reasons(
                        [
                            {"reason": row.get("reason")}
                            for row in (data.get("criteria") or [])
                            if int(row.get("points") or 0) == 0
                        ]
                    ),
                    "evaluated_at": str(data.get("evaluated_at") or ""),
                }
            )
        capped = rows[: max(1, min(int(limit), 500))]
        return {
            "count": len(capped),
            "runs": capped,
            "room_id": room_id,
            "moved_only": bool(moved_only),
        }

    def summary(self, room_id: str) -> dict[str, Any]:
        """What a page needs above the fold, in one response.

        The five researched numbers are the two buckets, the saved criteria, the
        scored contacts, and the runs whose number moved - plus the states that are not
        successes, because a page that only shows green teaches a reviewer nothing.
        """
        criteria_rows = self._criteria()
        by_bucket = {"positive": 0, "negative": 0}
        by_family: dict[str, int] = {name: 0 for name in FILTER_FAMILIES}
        for data in criteria_rows:
            bucket = str(data.get("bucket") or "")
            if bucket in by_bucket:
                by_bucket[bucket] += 1
            family = str(data.get("family") or "")
            if family in by_family:
                by_family[family] += 1

        contacts = self.store.list(names.CONTACTS, room_id=room_id, limit=500)
        runs = self.store.list(names.RUNS, room_id=room_id, limit=500)
        moved = sum(1 for run in runs if run["data"].get("changed"))
        scores = [int(row["data"].get("score") or 0) for row in contacts]
        provisioned = self._provisioned_families()

        org_records = self.store.list(names.INTEGRATIONS, limit=100)
        if not org_records:
            integration_state = "not_registered"
            missing_scopes = list(REQUIRED_SCOPES)
            deal_connected = False
        else:
            writable = False
            missing_scopes = sorted(
                {scope for r in org_records for scope in self._missing_scopes(r["data"])}
            )
            for record in org_records:
                if self._integration_view(record)["writable"]:
                    writable = True
                    break
            integration_state = "writable" if writable else "not_writable"
            deal_connected = any(
                room_id in (record["data"].get("deal_connections") or {}) for record in org_records
            )

        return {
            "room_id": room_id,
            "criteria_count": len(criteria_rows),
            "criteria_by_bucket": by_bucket,
            "criteria_by_family": by_family,
            "contacts_scored": len(contacts),
            "runs": len(runs),
            "runs_that_moved": moved,
            "top_score": max(scores) if scores else 0,
            "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "contacts_above_zero": sum(1 for score in scores if score > 0),
            "contacts_below_zero": sum(1 for score in scores if score < 0),
            "provisioned_families": sorted(provisioned),
            "awaiting_provisioning": [name for name in FILTER_FAMILIES if name not in provisioned],
            "integration_state": integration_state,
            "missing_scopes": missing_scopes,
            "deal_connected": deal_connected,
            "crm_plan": dict(CRM_PLAN),
            "execution_note": EXECUTION_NOTE,
            "states": [
                {
                    "code": code,
                    "count": count,
                    "meaning": meaning,
                }
                for code, count, meaning in (
                    (
                        "property_not_provisioned",
                        len(FILTER_FAMILIES) - len(provisioned),
                        "Dock activity properties still to provision into the CRM. Every "
                        "criterion on one of these matches nothing.",
                    ),
                    (
                        "no_criteria_saved",
                        1 if not criteria_rows else 0,
                        "No criterion saved, so no score can move.",
                    ),
                    (
                        "negative_scores",
                        sum(1 for score in scores if score < 0),
                        "Contacts whose score is below zero. No floor is applied unless a CRM "
                        "organisation sets one.",
                    ),
                    (
                        "runs_that_moved_nothing",
                        len(runs) - moved,
                        "Runs that fired and left the number alone. The rule is continuous, so "
                        "these are the record of it having fired.",
                    ),
                )
            ],
        }

    # ------------------------------------------------------------------ #
    # Dry run: what a criterion would do, before a buyer has done anything
    # ------------------------------------------------------------------ #

    def preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Match one unsaved criterion against one event, and say why.

        The researched flow is a UI flow, so the editor needs to answer "would this
        match?" without a buyer having to do anything first. Nothing is written, and the
        response says so.

        The payload is ``{"criterion": {...}, "event": {...}}`` rather than one flat
        object, because the criterion and the event share no keys and a flat shape
        would leave the reader guessing which half a key belonged to.
        """
        criterion = parse_criterion(payload.get("criterion") or {})
        criterion["lint"] = lint_criterion(criterion)
        event = normalise_activity(payload.get("event") or {})
        verdict = matches(criterion, event)
        return {
            "sends_nothing": True,
            "criterion": criterion,
            "event": {key: event.get(key) for key in ("contact", "action", "action_family")},
            "verdict": verdict,
            "would_add": (
                BUCKET_SIGN.get(str(criterion.get("bucket") or ""), 0)
                * int(criterion.get("score") or 0)
                if verdict["matched"]
                else 0
            ),
        }

    # ------------------------------------------------------------------ #
    # Helpers used by the seeder
    # ------------------------------------------------------------------ #

    def seed_properties(self, families: Any, *, actor: str, source: str) -> list[str]:
        """Record the stock Dock activity properties as provisioned."""
        recorded = []
        for family in published_family_names(families):
            self.provision_property(
                {
                    "family": family,
                    "name": f"dsr_{family}",
                    "note": "Provisioned from the sales-room engagement object.",
                },
                actor=actor,
                source=source,
            )
            recorded.append(family)
        return recorded
