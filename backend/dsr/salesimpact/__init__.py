"""WF-023: relate buyer engagement to CRM pipeline and close rate.

The Sales Impact report, built from
``docs/research/digital-sales-room-workflows/wf/WF-023.md`` and designed in
``WF-023-design.md`` beside it. This package is the whole of the domain: the vocabulary
the report's figures are expressed in, the filters a caller may apply, the CRM deal mirror
those figures are computed over, and the arithmetic itself.

:class:`SalesImpact` is the one public surface. It holds nothing beyond the store handle
and the configuration it reads from it, so the feature module builds one per request from
a dependency rather than hanging it on ``app.state`` - which is what keeps
``dsr/api.py`` untouched and leaves the tests a seam to override.

Four modules, each with one job:

``errors``
    The four refusals this workflow makes, under one domain base type.
``vocabulary``
    The ``sales`` workspace type, the won/lost stage sets and the order they are matched
    in, the field synonym lists, and the researched API constraints served as data.
``filters``
    :class:`~dsr.salesimpact.filters.ReportFilter` and its parser.
``deals``
    :class:`~dsr.salesimpact.deals.DealBook` - the records this workflow writes.
``rollup``
    Pure functions: the population gate, the eight researched tiles, the funnel, the two
    panel charts, the engagement rollup, and the coverage panel.
``inferences``
    Every decision the research does not make, named and served at ``/inferences``.

Schema flexibility
------------------
No migration, no typed column, no new required field. A deal is whatever the CRM sent,
stored verbatim; every field this package *reads* is located by a synonym list a team can
replace in the ``sales_impact_config`` record; and which collection buyer engagement comes
from is configured rather than hard-coded, so a team with its own webhook-derived store
re-points it without a code change.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the routes
pass the route that served the request, built from ``router.prefix``. Hardcoding a source
string in a domain function puts a path in the audit log that the app might have stopped
serving, and that class of bug has shipped in this codebase before.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.salesimpact import inferences, rollup, vocabulary
from dsr.salesimpact.deals import (
    CONFIG_COLLECTION,
    CONFIG_KEY,
    DEAL_COLLECTION,
    DealBook,
    view_deal,
)
from dsr.salesimpact.errors import (
    DealConflict,
    InvalidDeal,
    InvalidFilter,
    SalesImpactError,
    UnknownWorkspace,
)
from dsr.salesimpact.filters import ReportFilter, parse_filters
from dsr.salesimpact.rollup import TILES
from dsr.salesimpact.vocabulary import SALES_TYPE, STAGE_CLASSES, describe
from dsr.store import RecordStore

__all__ = [
    "CONFIG_COLLECTION",
    "CONFIG_KEY",
    "DEAL_COLLECTION",
    "SALES_TYPE",
    "STAGE_CLASSES",
    "TILES",
    "DealBook",
    "DealConflict",
    "InvalidDeal",
    "InvalidFilter",
    "ReportFilter",
    "SalesImpact",
    "SalesImpactError",
    "UnknownWorkspace",
    "describe",
    "inferences",
    "parse_filters",
    "rollup",
    "view_deal",
    "vocabulary",
]


class SalesImpact:
    """The Sales Impact report, over a schema-flexible audited store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self.deals = DealBook(store)

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """The published vocabulary, so a client renders pickers from the same source
        the classifier enforces against."""
        return describe()

    def inferences(self) -> dict[str, Any]:
        """Every design decision the research does not fix, named and bounded."""
        return inferences.describe()

    # -- configuration ------------------------------------------------------ #

    def config(self) -> dict[str, Any]:
        """The effective configuration, defaults layered under the stored record."""
        return self.deals.config()

    def set_config(
        self, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Merge a patch into the configuration record. The one write that is not a deal."""
        return self.deals.save_config(patch, actor=actor, source=source)

    def integration(self) -> dict[str, Any]:
        """The CRM integration state the report's completeness depends on."""
        return self.deals.config()["integration"]

    # -- the report --------------------------------------------------------- #

    def _inputs(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Load the three populations once, and the configuration that reads them.

        Loaded here rather than inside the rollup so that one request reads each
        collection once and every figure in the response is computed over the same
        snapshot. Two reads per figure would let a CRM sync land between the tiles and
        produce a report whose own numbers do not add up.
        """
        config = self.deals.config()
        stage_sets = {
            "won": tuple(config["stages"]["won"]) or None,
            "lost": tuple(config["stages"]["lost"]) or None,
        }
        rooms = [
            rollup.project_room(record)
            for record in self.deals.rooms(config["collections"]["rooms"])
        ]
        deals = [
            rollup.project_deal(record, stage_sets=stage_sets) for record in self.deals.deals()
        ]
        events = [
            projected
            for record in self.deals.events(config["collections"]["engagement"])
            if (projected := rollup.project_event(record)) is not None
        ]
        return rooms, deals, events, {"config": config, "stage_sets": stage_sets}

    def report(self, report_filter: ReportFilter) -> dict[str, Any]:
        """The whole researched report. Computed on read; writes nothing."""
        rooms, deals, events, context = self._inputs()
        integration = context["config"]["integration"]
        return rollup.report(
            rooms,
            deals,
            events,
            report_filter,
            stage_sets=context["stage_sets"],
            crm_connected=bool(integration.get("connected")),
            provider=str(integration.get("provider") or ""),
        )

    def coverage(self) -> dict[str, Any]:
        """The completeness readout, on its own, for the page's banner and drill-in."""
        rooms, deals, _events, context = self._inputs()
        integration = context["config"]["integration"]
        scope = rollup.population(rooms, deals, stage_sets=context["stage_sets"])
        return rollup.coverage(
            scope,
            crm_connected=bool(integration.get("connected")),
            provider=str(integration.get("provider") or ""),
        )

    def room(self, room_id: str, report_filter: ReportFilter) -> dict[str, Any]:
        """One workspace's contribution, and whether it is in the report at all.

        Raises :class:`~dsr.salesimpact.errors.UnknownWorkspace` for an id that is not a
        live room. An existing room that is merely out of scope is a 200 with a reason,
        because the researched first step is setting the workspace type and a reader
        arriving here wants to know why their room is absent.
        """
        rooms, deals, events, context = self._inputs()
        record = self.deals.require_room(
            room_id, collection=context["config"]["collections"]["rooms"]
        )
        return rollup.room_report(
            rollup.project_room(record),
            rooms,
            deals,
            events,
            report_filter,
            stage_sets=context["stage_sets"],
        )

    # -- the drill-in lists ------------------------------------------------- #

    def list_deals(self, report_filter: ReportFilter) -> dict[str, Any]:
        """The filtered in-scope deals behind the tiles, with totals over the whole
        filtered population rather than over the returned page."""
        rooms, deals, _events, context = self._inputs()
        rows, scope, summary = rollup.deal_rows(
            rooms, deals, report_filter, stage_sets=context["stage_sets"]
        )
        page = rows[: report_filter.limit]
        room_by_id = {str(room["id"]): room for room in rooms}
        money = rollup.money_by_currency(rows, currency=summary["currency"])
        return {
            "count": len(rows),
            "returned": len(page),
            "currency": summary["currency"],
            "currencies": sorted(money) or [summary["currency"]],
            "money": money,
            "totals": summary["totals"],
            "filters": report_filter.echo(),
            "deals": [
                view_deal(
                    {"id": row["id"], "room_id": row["room_id"], "data": _as_stored(row)},
                    stage_sets=context["stage_sets"],
                    room=room_by_id.get(str(row["room_id"])),
                    in_scope=True,
                    reason=None,
                )
                for row in page
            ],
        }

    def buyers(self, report_filter: ReportFilter) -> dict[str, Any]:
        """The Most Engaged Buyers ranking, over the same population as the report."""
        body = self.report(report_filter)
        ranked: Sequence[Mapping[str, Any]] = body["engagement"]["most_engaged_buyers"]
        page = list(ranked[: report_filter.limit])
        return {
            "count": len(ranked),
            "returned": len(page),
            "unique_buyers": body["engagement"]["unique_buyers"],
            "filters": body["filters"],
            "buyers": page,
        }

    def engagement(self, report_filter: ReportFilter) -> dict[str, Any]:
        """Buyer views, actions, the average per workspace, and the views time series."""
        body = self.report(report_filter)
        return {**body["engagement"], "filters": body["filters"], "currency": body["currency"]}

    # -- writes ------------------------------------------------------------- #

    def register_deal(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Attach a CRM deal to a workspace. ``source`` is required, not defaulted."""
        config = self.deals.config()
        record = self.deals.create_deal(
            payload,
            room_id=room_id,
            room_collection=config["collections"]["rooms"],
            actor=actor,
            source=source,
        )
        return self.deal_view(record["id"], config=config, stage_sets=_stage_sets(config))

    def patch_deal(
        self, deal_id: str, patch: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """The researched stage/amount sync."""
        config = self.deals.config()
        self.deals.update_deal(deal_id, patch, actor=actor, source=source)
        return self.deal_view(deal_id, config=config, stage_sets=_stage_sets(config))

    def detach_deal(self, deal_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Soft-delete a deal, so the detachment stays auditable."""
        return self.deals.delete_deal(deal_id, actor=actor, source=source)

    def read_deal(self, deal_id: str) -> dict[str, Any]:
        """One deal, projected. Raises :class:`UnknownWorkspace` when there is no such
        live deal - the same 404 the write paths use, so a client has one shape to handle."""
        config = self.deals.config()
        record = self.deals.require_deal(deal_id)
        return self.deal_view(deal_id, config=config, stage_sets=_stage_sets(config), record=record)

    def deal_view(
        self,
        deal_id: str,
        *,
        config: Mapping[str, Any] | None = None,
        stage_sets: Mapping[str, Sequence[str]] | None = None,
        record: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The stored record plus the derived read projection.

        The derived half is never persisted: ``stage_class``, ``owner_source`` and
        ``days_to_close`` are recomputed on the way out, because the research says the
        stage changes by sync and a stored derived copy would be a cache that goes stale.
        """
        config = config or self.deals.config()
        stage_sets = stage_sets or _stage_sets(config)
        record = record or self.deals.deal(deal_id)
        room = None
        if record is not None and record.get("room_id"):
            room = self.store.get(str(record["room_id"]))
        in_scope, reason = self._deal_scope(record, config, stage_sets)
        return {
            "id": record.get("id") if record else deal_id,
            "collection": DEAL_COLLECTION,
            "room_id": record.get("room_id") if record else None,
            "revision": record.get("revision") if record else None,
            "created_at": record.get("created_at") if record else None,
            "updated_at": record.get("updated_at") if record else None,
            "data": dict(record.get("data") or {}) if record else {},
            "view": view_deal(
                record or {"id": deal_id, "room_id": None, "data": {}},
                stage_sets=stage_sets,
                room=room,
                in_scope=in_scope,
                reason=reason,
            ),
        }

    def _deal_scope(
        self,
        record: Mapping[str, Any] | None,
        config: Mapping[str, Any],
        stage_sets: Mapping[str, Sequence[str]],
    ) -> tuple[bool, str | None]:
        """Whether one deal is in the report, and if not, which test it failed.

        Resolved through the same :func:`~dsr.salesimpact.rollup.population` the report
        uses, so a single deal's ``in_scope`` can never disagree with the total it
        contributes to.
        """
        if record is None or not record.get("room_id"):
            return False, "not_attached"
        rooms, deals, _events, _context = self._inputs()
        del config, stage_sets
        scope = rollup.population(rooms, deals)
        room_id = str(record["room_id"])
        if any(str(entry["id"]) == room_id for entry in scope["included"]):
            return True, None
        excluded = next((row for row in scope["excluded"] if row["room_id"] == room_id), None)
        return False, (excluded["reason"] if excluded else "not_sales")


def _stage_sets(config: Mapping[str, Any]) -> dict[str, Sequence[str] | None]:
    return {
        "won": tuple(config["stages"]["won"]) or None,
        "lost": tuple(config["stages"]["lost"]) or None,
    }


def _as_stored(row: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a stored-shaped payload from a projected row, for the read projection.

    The list route's rows are already classified and resolved, so re-running the field
    discovery over them would be a second source of truth for the same numbers. This
    reshapes a row into the payload shape :func:`~dsr.salesimpact.deals.view_deal`
    expects, so one projection serves both the report and the drill-in.
    """
    stored = {
        "crm_deal_id": row.get("crm_deal_id") or "",
        "name": row.get("name") or "",
        "account": row.get("account") or "",
        "stage": row.get("stage") or "",
        "currency": row.get("currency") or "",
        "team": row.get("team") or "",
    }
    if row.get("amount") is not None:
        stored["amount"] = row["amount"]
    if row.get("created"):
        stored["created_at"] = row["created"].isoformat()
    if row.get("closed"):
        stored["closed_at"] = row["closed"].isoformat()
    return stored
