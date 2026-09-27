"""The engine: connectors, bundles, the preview, and the commit.

:class:`BundleCommitter` holds a :class:`~dsr.store.RecordStore` and a transport
factory and nothing else. The HTTP surface is
:mod:`dsr.features.wf039_write_account_contact_opportunity_as_o`, which builds
one of these per request.

Every method that writes takes ``source`` as a **required keyword** and threads it
to the store. That is not a style choice: this programme has shipped a feature
whose audit log kept naming a route the app had stopped serving, and a required
keyword cannot be forgotten. The route passes ``f"{router.prefix}/..."``; nothing
in this module contains a URL.

The commit is the one place three things meet - the researched flow, the
transport, and the audit log - so it is worth reading in order:

1. read the room's bundle and the connector's defaults;
2. plan it, which refuses anything the documented limits already reject;
3. render it into the dialect's request shape;
4. send it, once, through the transport;
5. write **one** run record carrying every per-subrequest outcome, the counts,
   and the single actionable error the research asks the room to show.

Step 5 happens whether the commit succeeded or failed, because the failure *is*
the record. A refusal at step 2 writes nothing at all: nothing was attempted, so
there is nothing to audit.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from dsr.atomic_bundle.dialects import RenderOptions, render
from dsr.atomic_bundle.errors import (
    BundleError,
    BundleNotConfigured,
    BundleShapeError,
    NotFound,
)
from dsr.atomic_bundle.planner import plan_bundle
from dsr.atomic_bundle.transport import (
    TARGET_COLLECTION,
    CommitResult,
    LocalCrm,
    Transport,
    UrllibTransport,
)
from dsr.atomic_bundle.vocabulary import ATOMICITY, DIALECTS, POLICIES
from dsr.store import RecordStore

#: The three record kinds this feature owns. Namespaced so nothing collides with
#: the core dataset or another feature's collections, and named ``crm_`` because
#: the research's own data sources are "CRM account, contact, opportunity tables"
#: and "sales-room bundle table".
CONNECTOR_COLLECTION = "crm_atomic_connector"
BUNDLE_COLLECTION = "crm_atomic_bundle"
RUN_COLLECTION = "crm_atomic_run"

COLLECTIONS: tuple[str, ...] = (CONNECTOR_COLLECTION, BUNDLE_COLLECTION, RUN_COLLECTION, TARGET_COLLECTION)

#: The transport names a connector may ask for. ``local`` is the in-process CRM
#: from :mod:`dsr.atomic_bundle.transport`; ``urllib`` is the real HTTP one.
TRANSPORT_LOCAL = "local"
TRANSPORT_URLLIB = "urllib"
TRANSPORTS: tuple[str, ...] = (TRANSPORT_LOCAL, TRANSPORT_URLLIB)

#: What a connector summary shows instead of its token.
TOKEN_MASK = "…"


class BundleCommitter:
    """Bundles in, atomic commits out, everything through the audited store."""

    def __init__(
        self,
        store: RecordStore,
        *,
        transport_factory: Callable[[Mapping[str, Any], str], Transport] | None = None,
    ) -> None:
        self.store = store
        self._transport_factory = transport_factory or self._default_transport

    # -- rooms -------------------------------------------------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        """The room, or a refusal that says which id did not resolve.

        Every room-scoped route starts here, so a typo in a room id is a 404
        naming the room rather than an empty list that looks like a room with
        nothing in it.
        """
        record = self.store.get(str(room_id))
        if record is None or record["collection"] != "room":
            raise NotFound("room", room_id)
        return record

    # -- connectors ---------------------------------------------------------- #

    def create_connector(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        data = _connector_payload(payload, require_base_url=False)
        return _connector_summary(
            self.store.create(CONNECTOR_COLLECTION, data, actor=actor, source=source)
        )

    def list_connectors(self) -> list[dict[str, Any]]:
        return [
            _connector_summary(record)
            for record in self.store.list(CONNECTOR_COLLECTION, limit=1000)
        ]

    def get_connector(self, connector_id: str) -> dict[str, Any]:
        record = self._live(CONNECTOR_COLLECTION, connector_id)
        if record is None:
            raise NotFound("connector", connector_id)
        return _connector_summary(record)

    def update_connector(
        self,
        connector_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self._live(CONNECTOR_COLLECTION, connector_id)
        if record is None:
            raise NotFound("connector", connector_id)
        patch = _connector_payload(payload, require_base_url=False, partial=True)
        if not patch:
            return _connector_summary(record)
        return _connector_summary(self.store.update(record["id"], patch, actor=actor, source=source))

    def delete_connector(
        self, connector_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self._live(CONNECTOR_COLLECTION, connector_id)
        if record is None:
            raise NotFound("connector", connector_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    def default_connector(self) -> dict[str, Any] | None:
        """The connector a bundle with no ``connector_id`` uses.

        The newest one, because a deployment that adds a second connector means
        to use it; the newest is the one it just added.
        """
        records = self.store.list(CONNECTOR_COLLECTION, limit=1, order_by="created_at")
        return records[0] if records else None

    # -- bundles ------------------------------------------------------------- #

    def create_bundle(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        self.require_room(room_id)
        data = _bundle_payload(payload)
        data["effective"] = self._validate(data)
        return _bundle_summary(
            self.store.create(
                BUNDLE_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
            )
        )

    def _validate(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Plan a bundle once, at declaration, and keep the resolved choices.

        The preview is where a rep reads about a bundle they cannot send, and
        that is a fine place to find out - but not the *only* place. A declaration
        that could never be planned is a bad declaration, and storing it means the
        room lists a bundle that cannot be committed and the audit log records a
        create for it.

        The resolved choices are kept under ``effective`` rather than written over
        the declared ``dialect`` / ``policy`` / ``collate_subrequests``, for two
        reasons: the bundle still says what its author wrote, and the inherited
        value is still a queryable JSON path - so ``?where=effective.dialect=…``
        works through the dynamic index and a room can list its bundles by
        dialect without every read re-planning all of them.
        """
        connector = self._connector_for(data)
        resolved = _resolve_choices(data, connector, None, None, None)
        plan_bundle(
            data,
            dialect=resolved["dialect"],
            policy=resolved["policy"],
            collate_subrequests=resolved["collate_subrequests"],
        )
        return {
            "dialect": resolved["dialect"],
            "policy": resolved["policy"],
            "collate_subrequests": resolved["collate_subrequests"],
            "source_of": resolved["source_of"],
        }

    def list_bundles(
        self,
        room_id: str,
        *,
        dialect: str | None = None,
        policy: str | None = None,
    ) -> list[dict[str, Any]]:
        """The bundles declared for this room, newest first.

        Filtered on the *resolved* dialect and policy - the ones a commit would
        actually use, inherited from the connector included - through the dynamic
        index, so a room can list "the bundles that go to Dataverse" without
        every read re-planning all of them.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if dialect:
            where["effective.dialect"] = dialect
        if policy:
            where["effective.policy"] = policy
        records = (
            self.store.find(BUNDLE_COLLECTION, where, limit=1000)
            if where
            else self.store.list(BUNDLE_COLLECTION, room_id=str(room_id), limit=1000)
        )
        return [
            _bundle_summary(record)
            for record in records
            if record.get("room_id") in (None, str(room_id))
        ]

    def get_bundle(self, room_id: str, bundle_id: str) -> dict[str, Any]:
        self.require_room(room_id)
        record = self._bundle_record(room_id, bundle_id)
        return _bundle_summary(record)

    def update_bundle(
        self,
        room_id: str,
        bundle_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        self.require_room(room_id)
        record = self._bundle_record(room_id, bundle_id)
        patch = _bundle_payload(payload, partial=True)
        if not patch:
            return _bundle_summary(record)
        merged = {**record["data"], **patch}
        patch["effective"] = self._validate(merged)
        return _bundle_summary(
            self.store.update(record["id"], patch, actor=actor, source=source)
        )

    def delete_bundle(
        self, room_id: str, bundle_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        self.require_room(room_id)
        record = self._bundle_record(room_id, bundle_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    # -- the researched preview ---------------------------------------------- #

    def preview(
        self,
        room_id: str,
        bundle_id: str,
        *,
        dialect: str | None = None,
        policy: str | None = None,
        collate_subrequests: bool | None = None,
    ) -> dict[str, Any]:
        """The researched **Sync → bundle preview**: the subrequest order, and why.

        Writes nothing. Every field the room shows before it commits is computed
        here - the plan, the rendered request, the warnings, the blockers - so
        what a rep reads and what a commit does cannot drift apart.
        """
        self.require_room(room_id)
        record = self._bundle_record(room_id, bundle_id)
        connector = self._connector_for(record)

        resolved = _resolve_choices(record, connector, dialect, policy, collate_subrequests)
        plan = plan_bundle(
            record["data"],
            dialect=resolved["dialect"],
            policy=resolved["policy"],
            collate_subrequests=resolved["collate_subrequests"],
            room_id=str(room_id),
            bundle_id=record["id"],
        )
        options = _render_options(connector)
        document = render(plan, options)

        return {
            "room_id": str(room_id),
            "bundle": _bundle_summary(record),
            "connector": _connector_summary(connector) if connector else None,
            "choices": resolved,
            "plan": plan.to_dict(),
            "request": document.to_dict(),
            "user_flow": _user_flow(),
            "warnings": [dict(entry) for entry in plan.warnings],
            "blockers": self._blockers(connector, plan),
            "ready": not self._blockers(connector, plan),
            "inferences": _inferences_for(plan),
        }

    # -- the researched commit ----------------------------------------------- #

    def commit(
        self,
        room_id: str,
        bundle_id: str,
        *,
        actor: str | None,
        source: str,
        dialect: str | None = None,
        policy: str | None = None,
        collate_subrequests: bool | None = None,
        faults: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send the bundle as one request and record what it did.

        ``faults`` is deliberately **not** reachable over HTTP. It exists so the
        demo and the suite can produce a CRM-side refusal without a CRM: a
        deployment's failures come from the CRM, and a client that could inject
        them would be a client that could forge an audit record.
        """
        self.require_room(room_id)
        record = self._bundle_record(room_id, bundle_id)
        connector = self._connector_for(record)
        if connector is None:
            raise BundleNotConfigured(
                "no CRM connector is configured, so there is nowhere to send the bundle. "
                "Register one before committing."
            )

        resolved = _resolve_choices(record, connector, dialect, policy, collate_subrequests)
        # Plan and render before the base URL check: a bundle that cannot be sent
        # at all should say which of the two is wrong, and planning first means a
        # malformed bundle is refused as a malformed bundle rather than as a
        # configuration problem.
        plan = plan_bundle(
            record["data"],
            dialect=resolved["dialect"],
            policy=resolved["policy"],
            collate_subrequests=resolved["collate_subrequests"],
            room_id=str(room_id),
            bundle_id=record["id"],
        )
        document = render(plan, _render_options(connector))
        transport = self._transport_for(connector, room_id=str(room_id), faults=faults or {})

        result = transport.send(document, source=source)

        run = self.store.create(
            RUN_COLLECTION,
            _run_payload(
                room_id=str(room_id),
                bundle=record,
                connector=connector,
                plan=plan,
                document=document,
                result=result,
                choices=resolved,
            ),
            room_id=str(room_id),
            actor=actor,
            source=source,
        )
        return _run_summary(run, result)

    # -- reads ---------------------------------------------------------------- #

    def runs(
        self,
        room_id: str,
        *,
        bundle_id: str | None = None,
        ok: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every commit attempted for this room, newest first.

        Filters are JSON paths in each row's own payload, resolved through the
        dynamic index, so a new dialect or a new outcome needs no change here.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if bundle_id:
            where["bundle_id"] = str(bundle_id)
        if ok is not None:
            where["ok"] = bool(ok)
        records = (
            self.store.find(RUN_COLLECTION, where, limit=limit)
            if where
            else self.store.list(RUN_COLLECTION, room_id=str(room_id), limit=limit)
        )
        return [
            _run_summary(record)
            for record in records
            if record.get("room_id") in (None, str(room_id))
        ]

    def run(self, room_id: str, run_id: str) -> dict[str, Any]:
        self.require_room(room_id)
        record = self.store.get(str(run_id))
        if record is None or record["collection"] != RUN_COLLECTION:
            raise NotFound("run", run_id, room_id)
        if record.get("room_id") not in (None, str(room_id)):
            raise NotFound("run", run_id, room_id)
        return _run_summary(record, None)

    def targets(self, room_id: str) -> list[dict[str, Any]]:
        """The rows the CRM holds for this room.

        The researched flow's fourth step - "The CRM executes subrequests in "
        "order, capturing each created record id" - made inspectable, and a
        rollback visible as rows leaving this list rather than as a claim.
        """
        self.require_room(room_id)
        return self.store.list(TARGET_COLLECTION, room_id=str(room_id), limit=1000)

    # -- internals ------------------------------------------------------------ #

    def _bundle_record(self, room_id: str, bundle_id: str) -> dict[str, Any]:
        record = self.store.get(str(bundle_id))
        if record is None or record["collection"] != BUNDLE_COLLECTION:
            raise NotFound("bundle", bundle_id, room_id)
        if record.get("room_id") not in (None, str(room_id)):
            raise NotFound("bundle", bundle_id, room_id)
        return record

    def _live(self, collection: str, record_id: str) -> dict[str, Any] | None:
        record = self.store.get(str(record_id))
        if record is None or record["collection"] != collection:
            return None
        return record

    def _connector_for(self, bundle: Mapping[str, Any]) -> dict[str, Any] | None:
        """The connector's own fields, flattened together with its id.

        Returns the ``data`` payload rather than the record, because every caller
        here reads *fields* - ``policy``, ``transport``, ``base_url``,
        ``dialect`` - and reading those off a record envelope silently yields
        ``None``. That is not a cosmetic choice: it made a connector's own
        rollback policy invisible to the commit that was supposed to use it, and
        a connector asking for the HTTP transport quietly got the in-process one.
        """
        connector_id = bundle.get("data", bundle).get("connector_id")
        if connector_id:
            record = self._live(CONNECTOR_COLLECTION, str(connector_id))
            if record is None:
                raise BundleNotConfigured(
                    f"bundle points at connector {connector_id}, which is not a live connector"
                )
            return {**record["data"], "id": record["id"]}
        record = self.default_connector()
        return {**record["data"], "id": record["id"]} if record else None

    def _default_transport(self, connector: Mapping[str, Any], source: str) -> Transport:
        """The transport a connector asks for.

        ``local`` is the in-process CRM and is the default, because an
        installation with no CRM attached should still be able to show a bundle
        committing and rolling back. ``urllib`` is the real one and needs a base
        URL; there is no defaulted hostname anywhere in this package.
        """
        name = str(connector.get("transport") or TRANSPORT_LOCAL)
        if name == TRANSPORT_URLLIB:
            base_url = str(connector.get("base_url") or "")
            if not base_url:
                raise BundleNotConfigured(
                    "this connector sends over HTTP but has no base_url; set one on the "
                    "connector before committing"
                )
            return UrllibTransport(base_url=base_url)
        return LocalCrm(self.store, run_id=source, room_id=str(connector.get("room_id") or ""))

    def _transport_for(
        self, connector: Mapping[str, Any], *, room_id: str, faults: Mapping[str, str]
    ) -> Transport:
        if str(connector.get("transport") or TRANSPORT_LOCAL) == TRANSPORT_URLLIB:
            if faults:
                raise BundleNotConfigured(
                    "faults can only be injected into the local CRM; a real CRM produces its "
                    "own failures"
                )
            return self._default_transport(connector, "")
        return LocalCrm(self.store, faults=faults, room_id=room_id)

    def _blockers(self, connector: Mapping[str, Any] | None, plan: Any) -> list[dict[str, str]]:
        """What stands between this bundle and a commit.

        Reported rather than thrown, because the researched flow has a preview
        step: a rep should be able to see that the connector has no base URL
        before they press the button, not after.
        """
        blockers: list[dict[str, str]] = []
        if connector is None:
            blockers.append(
                {
                    "code": "no_connector",
                    "message": (
                        "No CRM connector is registered, so there is nowhere to send this "
                        "bundle. The preview below is still the real request."
                    ),
                }
            )
        elif str(connector.get("transport")) == TRANSPORT_URLLIB and not connector.get("base_url"):
            blockers.append(
                {
                    "code": "no_base_url",
                    "message": (
                        "This connector sends over HTTP and has no base_url. Nothing is "
                        "defaulted here on purpose - a guessed host is a request to somewhere "
                        "unexpected."
                    ),
                }
            )
        if not plan.atomic:
            blockers.append(
                {
                    "code": "not_atomic",
                    "message": (
                        f"{plan.dialect} has no documented cross-object transaction: "
                        f"{plan.atomicity['basis']}"
                    ),
                }
            )
        return blockers


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #


def _connector_payload(
    payload: Mapping[str, Any], *, require_base_url: bool, partial: bool = False
) -> dict[str, Any]:
    data = dict(payload or {})
    if not partial:
        if not str(data.get("name") or "").strip():
            raise BundleShapeError("name is required: a connector with no name cannot be picked")

    dialect = data.get("dialect")
    if dialect is not None and str(dialect) not in DIALECTS:
        raise BundleShapeError(
            f"unknown dialect {dialect!r}; expected one of {list(DIALECTS)}"
        )
    policy = data.get("policy")
    if policy is not None and str(policy) not in POLICIES:
        raise BundleShapeError(
            f"unknown rollback policy {policy!r}; expected one of {list(POLICIES)}"
        )
    transport = data.get("transport")
    if transport is not None and str(transport) not in TRANSPORTS:
        raise BundleShapeError(
            f"unknown transport {transport!r}; expected one of {list(TRANSPORTS)}"
        )
    if require_base_url and not str(data.get("base_url") or "").strip():
        raise BundleNotConfigured("base_url is required")

    if partial:
        return {key: value for key, value in data.items() if key not in ("id", "room_id", "revision")}

    data.setdefault("dialect", DIALECTS[0])
    data.setdefault("policy", POLICIES[0])
    data.setdefault("transport", TRANSPORT_LOCAL)
    data.setdefault("collate_subrequests", True)
    data.setdefault("enabled", True)
    data["dialect"] = str(data["dialect"])
    data["policy"] = str(data["policy"])
    data["transport"] = str(data["transport"])
    return data


def _bundle_payload(payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    data = dict(payload or {})
    records = data.get("records")
    if records is not None and (
        not isinstance(records, (list, tuple)) or isinstance(records, (str, bytes))
    ):
        raise BundleShapeError("records must be a list")
    if not partial:
        if not str(data.get("name") or "").strip():
            raise BundleShapeError("name is required")
        if not records:
            raise BundleShapeError(
                "records is required: a bundle with no records has nothing to commit"
            )
    dialect = data.get("dialect")
    if dialect is not None and str(dialect) not in DIALECTS:
        raise BundleShapeError(
            f"unknown dialect {dialect!r}; expected one of {list(DIALECTS)}"
        )
    policy = data.get("policy")
    if policy is not None and str(policy) not in POLICIES:
        raise BundleShapeError(
            f"unknown rollback policy {policy!r}; expected one of {list(POLICIES)}"
        )
    if partial:
        return {
            key: value
            for key, value in data.items()
            if key not in ("id", "room_id", "revision", "created_at", "updated_at")
        }
    data.setdefault("dialect", None)
    data.setdefault("policy", None)
    data.setdefault("collate_subrequests", None)
    data.setdefault("field_map", {})
    if data["dialect"] is None:
        del data["dialect"]
    if data["policy"] is None:
        del data["policy"]
    if data["collate_subrequests"] is None:
        del data["collate_subrequests"]
    return data


def _resolve_choices(
    bundle: Mapping[str, Any],
    connector: Mapping[str, Any] | None,
    dialect: str | None,
    policy: str | None,
    collate_subrequests: bool | None,
) -> dict[str, Any]:
    """The one place the bundle, the connector and the caller are reconciled.

    Precedence is caller > bundle > connector > package default, and it is
    applied in exactly one function so the preview and the commit cannot
    disagree about what was chosen.
    """
    data = bundle.get("data", bundle)
    connector = connector or {}
    chosen_dialect = str(
        dialect or data.get("dialect") or connector.get("dialect") or DIALECTS[0]
    )
    chosen_policy = str(policy or data.get("policy") or connector.get("policy") or POLICIES[0])
    collate = collate_subrequests
    if collate is None:
        collate = data.get("collate_subrequests")
    if collate is None:
        collate = connector.get("collate_subrequests", True)
    return {
        "dialect": chosen_dialect,
        "policy": chosen_policy,
        "collate_subrequests": bool(collate),
        "source_of": {
            "dialect": _precedence(dialect, data.get("dialect"), connector.get("dialect")),
            "policy": _precedence(policy, data.get("policy"), connector.get("policy")),
            "collate_subrequests": _precedence(
                collate_subrequests, data.get("collate_subrequests"), connector.get("collate_subrequests")
            ),
        },
    }


def _precedence(*candidates: Any) -> str:
    for value, label in zip(
        candidates, ("request", "bundle", "connector"), strict=False
    ):
        if value is not None:
            return label
    return "default"


def _render_options(connector: Mapping[str, Any] | None) -> RenderOptions:
    connector = connector or {}
    entity_sets = connector.get("entity_sets")
    return RenderOptions(
        api_version=str(connector.get("api_version") or "vXX.X"),
        base_url=str(connector.get("base_url") or ""),
        changeset=str(connector.get("changeset") or ""),
        entity_sets=dict(entity_sets) if isinstance(entity_sets, Mapping) else {},
    )


def _user_flow() -> list[dict[str, str]]:
    from dsr.atomic_bundle.vocabulary import describe_user_flow

    return describe_user_flow()


def _inferences_for(plan: Any) -> list[str]:
    """Which named inferences this plan's choices actually rest on.

    Served on the preview so a reader of a specific bundle is pointed at the
    specific judgement calls behind it, rather than at a list of fifteen.
    """
    relevant = ["bundle-record-shape", "declared-order-is-dependency-order", "default-policy"]
    if plan.policy != "strict":
        relevant.append("partial-on-a-changeset")
    if not plan.atomic:
        relevant.append("hubspot-has-no-atomic-batch")
    if plan.dialect == "salesforce_sobject_tree":
        relevant.append("sobject-tree-body")
    if plan.dialect == "dataverse_batch":
        relevant.append("dataverse-mime-framing")
    if plan.dialect == "hubspot_associations":
        relevant.append("entity-set-and-object-type-names")
    if any(step.implicit_depends_on for step in plan.steps):
        relevant.append("implicit-dependency-is-declared-not-inferred")
        if plan.dialect == "salesforce_composite" and plan.collate_subrequests:
            relevant.append("collation-is-modelled-not-simulated")
    relevant.append("actionable-error-is-one-entry")
    relevant.append("rollback-rows-are-recorded")
    return sorted(set(relevant))


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #


def _connector_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """A connector read, with the token masked.

    The token is the ``Authorization`` header on every request, and a connector
    row is readable by anyone who can call the API - so a read answers *whether*
    there is one and the last four characters, never the value. This build has no
    envelope field to put a secret in and may not add a column, so the value
    stays in ``data`` where every other field does and simply is not returned.
    """
    data = record.get("data", {})
    token = str(data.get("token") or "")
    summary = {
        "id": record.get("id"),
        "name": data.get("name"),
        "dialect": data.get("dialect"),
        "policy": data.get("policy"),
        "transport": data.get("transport", TRANSPORT_LOCAL),
        "collate_subrequests": data.get("collate_subrequests", True),
        "base_url": data.get("base_url") or "",
        "has_base_url": bool(str(data.get("base_url") or "").strip()),
        "has_token": bool(token),
        "token_hint": f"{TOKEN_MASK}{token[-4:]}" if token else "",
        "api_version": data.get("api_version", "vXX.X"),
        "entity_sets": dict(data.get("entity_sets") or {}),
        "enabled": bool(data.get("enabled", True)),
        "atomic": bool(ATOMICITY.get(str(data.get("dialect") or ""), {}).get("atomic", False)),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }
    for key, value in data.items():
        if key not in summary and key not in ("token",):
            summary[key] = value
    return summary


def _bundle_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data", {})
    records = data.get("records") or []
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "name": data.get("name"),
        "connector_id": data.get("connector_id"),
        "dialect": data.get("dialect"),
        "policy": data.get("policy"),
        "collate_subrequests": data.get("collate_subrequests"),
        "field_map": dict(data.get("field_map") or {}),
        "effective": dict(data.get("effective") or {}),
        "record_count": len(records),
        "records": records,
        "order": [
            str(entry.get("reference_id"))
            for entry in records
            if isinstance(entry, Mapping)
        ],
        "types": sorted(
            {
                str(entry.get("type") or entry.get("record_type") or "")
                for entry in records
                if isinstance(entry, Mapping)
            }
            - {""}
        ),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _run_payload(
    *,
    room_id: str,
    bundle: Mapping[str, Any],
    connector: Mapping[str, Any],
    plan: Any,
    document: Any,
    result: CommitResult,
    choices: Mapping[str, Any],
) -> dict[str, Any]:
    """The run record. One row per commit, whether it worked or not.

    The per-subrequest outcomes are the researched "capturing each created record
    id" made durable, the counts answer the room's roll-up, and
    ``actionable_error`` is the single error the flow says the room shows - the
    rest stays available on ``steps`` for anyone reading the detail.
    """
    return {
        "room_id": room_id,
        "bundle_id": bundle.get("id"),
        "bundle_name": bundle.get("data", {}).get("name"),
        "connector_id": connector.get("id"),
        "dialect": plan.dialect,
        "policy": plan.policy,
        "collate_subrequests": plan.collate_subrequests,
        "atomic": result.atomic,
        "compensated": result.compensated,
        "compensation_failures": list(result.compensation_failures),
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "counts": result.summary(),
        "committed": list(result.committed),
        "failed": list(result.failed),
        "skipped": list(result.skipped),
        "rolled_back": list(result.rolled_back),
        "created_record_ids": list(result.created_record_ids),
        "actionable_error": result.actionable_error,
        "steps": [step.to_dict() for step in result.steps],
        "request": document.to_dict(),
        "response_body": result.response_body or result.body,
        "notes": list(result.notes) + [entry["message"] for entry in plan.warnings],
        "warnings": [dict(entry) for entry in plan.warnings],
        "choices": dict(choices),
        "order": list(plan.order),
    }


def _run_summary(
    record: Mapping[str, Any], result: CommitResult | None = None
) -> dict[str, Any]:
    data = record.get("data", {})
    summary = {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "bundle_id": data.get("bundle_id"),
        "bundle_name": data.get("bundle_name"),
        "connector_id": data.get("connector_id"),
        "dialect": data.get("dialect"),
        "policy": data.get("policy"),
        "collate_subrequests": data.get("collate_subrequests"),
        "ok": bool(data.get("ok")),
        "status": data.get("status"),
        "atomic": bool(data.get("atomic", True)),
        "compensated": bool(data.get("compensated")),
        "compensation_failures": list(data.get("compensation_failures") or []),
        "counts": dict(data.get("counts") or {}),
        "committed": list(data.get("committed") or []),
        "failed": list(data.get("failed") or []),
        "skipped": list(data.get("skipped") or []),
        "rolled_back": list(data.get("rolled_back") or []),
        "created_record_ids": list(data.get("created_record_ids") or []),
        "actionable_error": data.get("actionable_error"),
        "notes": list(data.get("notes") or []),
        "warnings": list(data.get("warnings") or []),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }
    if result is not None:
        summary["steps"] = [step.to_dict() for step in result.steps]
    else:
        summary["steps"] = list(data.get("steps") or [])
        summary["request"] = data.get("request")
        summary["response_body"] = data.get("response_body")
    return summary


__all__ = [
    "BUNDLE_COLLECTION",
    "COLLECTIONS",
    "CONNECTOR_COLLECTION",
    "RUN_COLLECTION",
    "TRANSPORTS",
    "BundleCommitter",
]
