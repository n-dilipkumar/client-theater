"""The vendor seam: "create job" and "read page", and nothing else.

The research's extensibility note is the contract this module implements:

    "The cursor is an interface, so a vendor-specific cursor (Bulk job id, delta
    link, ``DataToken``) is swapped for a standard ``{vendor, connectionId,
    cursor, updatedAt}`` record. This is also what makes backfill idempotent
    under retry - a third party can add a new vendor by implementing only 'create
    job' and 'read page'."

So there are three methods and one of them is for the reverse direction. ``start``
is "create job". ``read_page`` is "read page", and it is what a pull run calls.
``submit_page`` exists only because the researched data flow also says "or push
into CRM for a reverse backfill", and pushing needs somewhere to put a page;
that is the one addition to the researched pair, and
:mod:`dsr.crm_backfill.inferences` records it as such.

**Adapters hold no state.** Everything a vendor "remembers" between polls - how
many times a job has been asked for, whether the file is ready yet - lives in
the run record, which is durable. That is not tidiness: a run that resumes after
the process restarts has to find the same job in the same state, and a counter
in a Python object would be gone.

**No adapter opens a socket.** Each one reads from a :class:`HistorySource`, so
the whole workflow runs in a test, in the seeder, and in a deployment with no
credentials. Swapping in a real transport is a change to one constructor
argument, which is the seam :class:`VendorAdapter` exists to provide.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Protocol, Sequence

from dsr.crm_backfill.errors import (
    DirectionNotSupported,
    MissingScope,
    UnsupportedStrategy,
    UnsupportedVendor,
)
from dsr.crm_backfill.vocabulary import REQUIRED_SCOPE, VENDORS

#: The export states the HubSpot adapter reports. The research says "read export
#: status and the download URL" and does not name the values, so these are this
#: build's, published rather than buried - see the register entry
#: ``hubspot-export-status-vocabulary``.
HUBSPOT_EXPORT_STATUSES: tuple[str, ...] = ("IN_PROGRESS", "COMPLETED", "FAILED")


class HistorySource(Protocol):
    """A CRM's historical rows, as the room would read them.

    Paged on purpose: the research's "room writes rows in pages" means the room
    must never need the whole result set at once, and a source that only offers
    ``all()`` would hide a whole class of bug in the code above it.
    """

    def count(self, connection: Mapping[str, Any], scope: Mapping[str, Any]) -> int:
        """How many rows the scope covers, before any paging."""

    def rows(
        self,
        connection: Mapping[str, Any],
        scope: Mapping[str, Any],
        *,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """One page of rows, in a stable order."""


class VendorPage:
    """What one read of a vendor produced.

    ``cursor`` is the handle to persist *after* this page has landed, never
    before. That single ordering is the whole of the no-duplicates property: a
    crash between the write and the cursor write replays the page, and the
    upsert absorbs the replay.
    """

    __slots__ = ("rows", "cursor", "job_state", "total", "processed", "more", "detail", "failure")

    def __init__(
        self,
        *,
        rows: Sequence[Mapping[str, Any]] | None = None,
        cursor: str | None = None,
        job_state: str = "running",
        total: int | None = None,
        processed: int | None = None,
        more: bool = False,
        detail: str = "",
        failure: str | None = None,
    ) -> None:
        self.rows = [dict(row) for row in (rows or [])]
        self.cursor = cursor
        self.job_state = job_state
        self.total = total
        self.processed = processed
        self.more = more
        self.detail = detail
        self.failure = failure

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows),
            "cursor": self.cursor,
            "job_state": self.job_state,
            "total": self.total,
            "processed": self.processed,
            "more": self.more,
            "detail": self.detail,
            "failure": self.failure,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"VendorPage({self.as_dict()})"


class VendorAdapter:
    """The interface a third party implements to add a vendor.

    Subclass and set the five class attributes; the engine never reaches past
    them. The abstract shape is deliberately two methods, because that is what
    the research says it takes.
    """

    #: The key in :data:`dsr.crm_backfill.vocabulary.VENDORS`.
    vendor: str = ""
    #: Which of ``async_job`` / ``delta_read`` / ``paged_read`` this adapter
    #: implements. The engine picks the highest-priority one the plan allows.
    strategies: tuple[str, ...] = ()
    #: Whether this vendor can receive a reverse backfill.
    supports_push: bool = False
    #: Scopes the connection's token must carry before ``start`` is called.
    required_scopes: tuple[str, ...] = ()
    #: Whether ``object_type_id`` is how this vendor wants a non-default object
    #: named. HubSpot's rule is sourced; the others default to a plain name.
    addresses_objects_by_id: bool = False

    # -- the researched pair ---------------------------------------------- #

    def start(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> VendorPage:
        """Create the job. Returns a page, because a job may already have a first one."""
        raise NotImplementedError

    def read_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: str | None,
        poll_count: int,
    ) -> VendorPage:
        """Read the next page of the job's results."""
        raise NotImplementedError

    # -- the reverse direction -------------------------------------------- #

    def submit_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        page_number: int,
    ) -> dict[str, Any]:
        """Accept a page of replica rows for a reverse backfill."""
        raise DirectionNotSupported(
            f"{self.vendor or type(self).__name__} is read-only in this build; a reverse backfill "
            "needs an adapter that implements submit_page"
        )

    # -- preflight --------------------------------------------------------- #

    def preflight(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> list[str]:
        """Refusals this vendor knows about before any job is created.

        Returned as strings rather than raised, so the engine can put all of
        them in the run's log at once: an operator fixing a connection should not
        have to resubmit once per missing grant.
        """
        return []

    # -- helpers ----------------------------------------------------------- #

    @property
    def label(self) -> str:
        return VENDORS.get(self.vendor, {}).get("label", self.vendor or type(self).__name__)

    @property
    def cursor_kind(self) -> str:
        return VENDORS.get(self.vendor, {}).get("cursor_kind", "none")

    def supports(self, strategy: str) -> bool:
        return strategy in self.strategies

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.vendor!r})"


class _SourceBacked(VendorAdapter):
    """Shared plumbing: the history source, and a deterministic handle."""

    def __init__(self, source: HistorySource) -> None:
        self.source = source

    def _handle(self, prefix: str, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> str:
        """A stable, reproducible id for this run's job.

        Derived from the run rather than counted, so two processes building the
        same run reach the same handle and a test does not have to reset a
        counter between cases. ``attempt`` is part of the material because a run
        that reconsiders its strategy abandons the first job and opens a second,
        and the second needs its own handle rather than a handle that answers
        for a job nobody is reading any more.
        """
        scope = plan.get("scope") or {}
        material = "|".join(
            str(part)
            for part in (
                self.vendor,
                connection.get("id"),
                plan.get("run_id"),
                int(plan.get("attempt") or 0),
                scope.get("kind"),
                scope.get("from"),
                scope.get("to"),
            )
        )
        return f"{prefix}{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"

    def _rows(
        self, connection: Mapping[str, Any], scope: Mapping[str, Any], offset: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = self.source.rows(connection, scope, offset=offset, limit=limit)
        # The source tags each row with the object it belongs to, so one history
        # can serve three vendors whose accounts hold different things. That tag
        # is routing, not record data, so it is stripped here: left in, it would
        # collide with the replica's own `object` bookkeeping and a field map
        # pointing at `object` would be refused for a name the source invented.
        return [{key: value for key, value in row.items() if key != "object"} for row in rows]

    def _count(self, connection: Mapping[str, Any], scope: Mapping[str, Any]) -> int:
        return self.source.count(connection, scope)


# --------------------------------------------------------------------------- #
# Salesforce
# --------------------------------------------------------------------------- #


class SalesforceBulkAdapter(_SourceBacked):
    """Salesforce Bulk API 2.0: submit a job, come back for it.

    "You submit a request and come back for the results later. Salesforce
    processes the request in the background." The background part is modelled by
    ``ready_after``: a freshly created job is not readable for the first poll, so
    the poller the research describes - "the room runs a poller on a fixed
    interval" - is exercised rather than assumed.

    The cursor here is the job id, and the research names it as exactly that.
    Re-reading a job from its start is therefore what a restart does, and the
    no-duplicates property is carried by the replica's upsert key rather than by
    the cursor's position. That is a real difference from Dataverse, and it is
    why :func:`dsr.crm_backfill.cursors.is_expired` only ages ``data_token``.
    """

    vendor = "salesforce"
    strategies = ("async_job", "paged_read")
    supports_push = True

    def start(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> VendorPage:
        handle = self._handle("750", connection, plan)
        total = self._count(connection, plan.get("scope") or {})
        # `ready_after` is not read here: `read_page` takes it from the job
        # payload the first page carries, so a poll count starts at 1.
        return VendorPage(
            cursor=handle,
            job_state="queued",
            total=total,
            processed=0,
            more=True,
            detail=(
                "job 750 submitted; Salesforce processes the request in the background and "
                "guarantees no service level agreement, so this run reports progress and not an ETA"
            ),
        )

    def read_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: str | None,
        poll_count: int,
    ) -> VendorPage:
        handle = cursor or self._handle("750", connection, plan)
        ready_after = int(job.get("ready_after", plan.get("ready_after", 1)))
        total = job.get("total")
        if poll_count < ready_after:
            return VendorPage(
                cursor=handle,
                job_state="running",
                total=total,
                processed=int(job.get("processed") or 0),
                more=True,
                detail=(
                    f"job 750{handle[3:]} is still being prepared; polled {poll_count} of "
                    f"{ready_after} before its results are readable"
                ),
            )

        page_size = int(plan.get("page_size") or 5000)
        offset = int(job.get("processed") or 0)
        # A job the room has just created may not carry a total yet. Asking the
        # source for one is cheap, and it means the percentage has a denominator
        # from the first page rather than from the second.
        if job.get("total") is None:
            job = {**job, "total": self._count(connection, plan.get("scope") or {})}
        total = job.get("total")
        rows = self._rows(connection, plan.get("scope") or {}, offset, page_size)
        processed = offset + len(rows)
        more = total is not None and processed < int(total)
        return VendorPage(
            rows=rows,
            # The cursor does not move: it is the job, not a position in it.
            cursor=handle,
            job_state="running" if more else "complete",
            total=total,
            processed=processed,
            more=more,
            detail=f"read {len(rows)} row(s) from job 750{handle[3:]} at offset {offset}",
        )

    def submit_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        page_number: int,
    ) -> dict[str, Any]:
        # "Use them to insert, update, upsert, or delete many records
        # asynchronously." A push is submitted the same way a query is, and
        # returns a job rather than a result.
        return {
            "job_id": self._handle("750", connection, plan),
            "submitted": len(rows),
            "page_number": page_number,
            "state": "queued",
        }


# --------------------------------------------------------------------------- #
# Dataverse
# --------------------------------------------------------------------------- #


class DataverseDeltaAdapter(_SourceBacked):
    """Dataverse change tracking: a ``DataToken`` that only stays good for a week.

    "The first time you use this message, it returns all records for the table.
    ... The message also returns a version number that you send back with the
    next use of the ``RetrieveEntityChanges`` message so that only data for those
    changes that occurred since that version is returned."

    So a token with no history behind it is a full read, and a token with history
    is a delta. This adapter treats the token as a page boundary as well: the
    token handed back with page *n* is the one that starts page *n+1*. A crash
    before the room stores that token replays page *n*, and the upsert absorbs
    it - which is the "no duplicates, no gaps" property stated as a mechanism
    rather than as a hope.
    """

    vendor = "dataverse"
    strategies = ("delta_read", "paged_read")
    supports_push = True

    def start(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> VendorPage:
        scope = plan.get("scope") or {}
        total = self._count(connection, scope)
        page_size = int(plan.get("page_size") or 5000)
        # A DataToken is a position in the table's *change stream*, not a handle
        # on a job, so a new read can legitimately be asked to continue from it.
        # That is the research's whole point about the version number, and it is
        # why a scheduled Dataverse backfill costs one page per run rather than
        # the whole table every time.
        try:
            offset = int(plan.get("cursor") or 0)
        except (TypeError, ValueError):
            offset = 0
        first = self._rows(connection, scope, offset, page_size)
        processed = offset + len(first)
        more = total is not None and processed < int(total)
        return VendorPage(
            rows=first,
            # Token 0 is the "first time you use this message" case: the whole
            # table, and the version number that turns the next call into a delta.
            # The token handed back is the one at the *end* of this page, so a
            # crash before the room stores it replays the page rather than
            # skipping it.
            cursor=str(processed),
            job_state="running" if more else "complete",
            total=total,
            processed=processed,
            more=more,
            detail=(
                "first RetrieveEntityChanges call returns the whole table and a DataToken; the "
                "token is sent back so the next call returns only what changed since"
                if offset == 0
                else f"continuing from DataToken {offset}; only the changes since it are returned"
            ),
        )

    def read_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: str | None,
        poll_count: int,
    ) -> VendorPage:
        scope = plan.get("scope") or {}
        total = job.get("total")
        try:
            offset = int(cursor) if cursor is not None else 0
        except (TypeError, ValueError):
            # An unreadable token is a full read, which is what the vendor itself
            # does: the first use of the message has no version to send.
            offset = 0
        page_size = int(plan.get("page_size") or 5000)
        rows = self._rows(connection, scope, offset, page_size)
        processed = offset + len(rows)
        more = total is not None and processed < int(total)
        return VendorPage(
            rows=rows,
            cursor=str(total if not more else processed),
            job_state="running" if more else "complete",
            total=total,
            processed=processed,
            more=more,
            detail=(
                f"changes since DataToken {cursor} returned {len(rows)} row(s); the next token is "
                f"{total if not more else processed}"
            ),
        )


# --------------------------------------------------------------------------- #
# HubSpot
# --------------------------------------------------------------------------- #


class HubSpotExportAdapter(_SourceBacked):
    """HubSpot: ask for an export, then read its status and download URL.

    "To start an export, make a ``POST`` request to
    ``/crm/exports/2026-09/export/async`` ... then read export status and the
    download URL."

    Two refusals here are the research's own, checked before a job is created:
    the ``crm.export`` scope that a Super Admin has to have granted, and the rule
    that a custom object may only be named by ``objectTypeId``.
    """

    vendor = "hubspot"
    # Only the async job. The research documents one path to a file -
    # "To start an export, make a POST request to
    # /crm/exports/2026-09/export/async" - and paging the file it produces is
    # part of reading that job, not a second strategy. So a small HubSpot export
    # is still an export, and the volume rule's fall-through says so on the run
    # rather than pretending a synchronous read exists.
    strategies = ("async_job",)
    supports_push = True
    required_scopes = (REQUIRED_SCOPE["scope"],)
    addresses_objects_by_id = True

    def preflight(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> list[str]:
        findings: list[str] = []
        granted = [str(scope) for scope in (connection.get("granted_scopes") or [])]
        for scope in self.required_scopes:
            if scope not in granted:
                findings.append(
                    f"this connection's token has not been granted {scope}; the research states the "
                    "user installing the app must be a Super Admin to grant that scope, and an "
                    "export started without it fails after the fact rather than at the request"
                )
        findings.extend(check_object_addressing(connection, plan))
        return findings

    def start(self, connection: Mapping[str, Any], plan: Mapping[str, Any]) -> VendorPage:
        scope = plan.get("scope") or {}
        total = self._count(connection, scope)
        return VendorPage(
            cursor=self._handle("exp-", connection, plan),
            job_state="IN_PROGRESS",
            total=total,
            processed=0,
            more=True,
            detail=(
                "export requested with format, object and properties; the status endpoint is read "
                "on the next poll and the download URL arrives with COMPLETED"
            ),
        )

    def read_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: str | None,
        poll_count: int,
    ) -> VendorPage:
        export_id = cursor or self._handle("exp-", connection, plan)
        ready_after = int(job.get("ready_after", plan.get("ready_after", 1)))
        total = job.get("total")
        url = f"https://exports.hubspot.example/{export_id}.csv"
        if poll_count < ready_after:
            return VendorPage(
                cursor=export_id,
                job_state="IN_PROGRESS",
                total=total,
                processed=int(job.get("processed") or 0),
                more=True,
                detail=f"export {export_id} is still being produced; no download URL yet",
            )

        page_size = int(plan.get("page_size") or 5000)
        offset = int(job.get("processed") or 0)
        rows = self._rows(connection, plan.get("scope") or {}, offset, page_size)
        processed = offset + len(rows)
        more = total is not None and processed < int(total)
        return VendorPage(
            rows=rows,
            cursor=export_id,
            job_state="IN_PROGRESS" if more else "COMPLETED",
            total=total,
            processed=processed,
            more=more,
            detail=(
                f"export {export_id} is COMPLETED; reading page at offset {offset} from {url}"
                if not more
                else f"export {export_id} ready at {url}; read {len(rows)} row(s) at offset {offset}"
            ),
        )

    def submit_page(
        self,
        connection: Mapping[str, Any],
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        page_number: int,
    ) -> dict[str, Any]:
        return {
            "export_id": self._handle("exp-", connection, plan),
            "submitted": len(rows),
            "page_number": page_number,
            "state": "IN_PROGRESS",
        }


def check_object_addressing(connection: Mapping[str, Any], plan: Mapping[str, Any]) -> list[str]:
    """Whether this vendor's own rules let the requested object be named as asked.

    "For standard objects, you can use the object's name (e.g., ``CONTACT``), but
    for custom objects, you must use the ``objectTypeId`` value." A deployment
    declares which names it considers standard, because which objects exist is a
    property of the account and not of this product.
    """
    object_name = plan.get("object_name")
    object_type_id = plan.get("object_type_id")
    if object_type_id:
        return []
    if not object_name:
        return []
    standard = [str(name) for name in (connection.get("standard_objects") or [])]
    if not standard or object_name in standard:
        return []
    return [
        f"{object_name!r} is not one of this connection's standard objects "
        f"({', '.join(standard) or 'none declared'}), so HubSpot will only accept it as an "
        "objectTypeId; supply object_type_id instead of object_name"
    ]


def require_supported(adapter: VendorAdapter, strategy: str) -> None:
    """Refuse a strategy the adapter does not implement, naming what it does."""
    if adapter.supports(strategy):
        return
    raise UnsupportedStrategy(
        f"{adapter.vendor} does not implement the {strategy} strategy; it implements "
        f"{', '.join(adapter.strategies) or 'none'}"
    )


def require_direction(adapter: VendorAdapter, direction: str) -> None:
    """Refuse a reverse backfill at a vendor that cannot receive one."""
    if direction == "push" and not adapter.supports_push:
        raise DirectionNotSupported(
            f"{adapter.vendor} is read-only in this build; a reverse backfill needs an adapter that "
            "implements submit_page"
        )


def require_no_preflight(findings: Sequence[str]) -> None:
    """Raise every preflight refusal at once, or nothing.

    All of them, not the first: a connection missing two grants should be fixed
    in one pass rather than discovered one rejected run at a time.
    """
    if findings:
        raise MissingScope("; ".join(findings))


def require_registered(vendor: str, registry: Mapping[str, VendorAdapter]) -> VendorAdapter:
    """Resolve a vendor to its adapter, or refuse with what *is* registered.

    "A third party can add a new vendor by implementing only 'create job' and
    'read page'." This is the refusal that sentence implies, and it names the
    way out, because a 409 that only says "unsupported" is a dead end.
    """
    adapter = registry.get(vendor)
    if adapter is None:
        raise UnsupportedVendor(
            f"no adapter is registered for {vendor!r}; registered vendors are "
            f"{', '.join(sorted(registry)) or 'none'}. A third party adds one by implementing only "
            "'create job' and 'read page' - see dsr.crm_backfill.vendors.VendorAdapter."
        )
    return adapter


def default_registry(source: HistorySource | None = None) -> dict[str, VendorAdapter]:
    """The adapters this build ships, keyed by vendor.

    Takes the history source as an argument rather than reaching for a global,
    so a deployment substitutes a real transport here and nowhere else, and so a
    test can hand the registry rows of its own.
    """
    if source is None:
        source = SimulatedHistory()
    adapters: list[VendorAdapter] = [
        SalesforceBulkAdapter(source),
        DataverseDeltaAdapter(source),
        HubSpotExportAdapter(source),
    ]
    return {adapter.vendor: adapter for adapter in adapters}


# --------------------------------------------------------------------------- #
# The default source
# --------------------------------------------------------------------------- #


class SimulatedHistory:
    """A CRM history held in memory, in the shape a real one would arrive.

    The product ships without CRM credentials, so the workflows that integrate
    read a scripted history rather than a socket. This is the same arrangement
    :mod:`dsr.crm` uses for webhook delivery, and it has the same two useful
    consequences: the whole workflow runs in a test, and the demo database is
    reproducible rather than dependent on what a remote system answers today.

    Rows are returned in a stable order - by ``occurred_at`` then by ``id`` -
    because a paged read that changes its order between pages cannot resume, and
    that bug is invisible until a real backfill skips rows.
    """

    def __init__(self, rows: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._rows = sorted(
            (dict(row) for row in (rows or [])),
            key=lambda row: (str(row.get("occurred_at") or ""), str(row.get("id") or "")),
        )

    def add(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = sorted(
            [*self._rows, *(dict(row) for row in rows)],
            key=lambda row: (str(row.get("occurred_at") or ""), str(row.get("id") or "")),
        )

    def scoped(
        self, connection: Mapping[str, Any] | None, scope: Mapping[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Rows inside the scope, with the researched bounds applied.

        ``from`` is inclusive and ``to`` exclusive, which is the convention the
        rest of this product's windowed reads use: two adjacent ranges neither
        skip nor double-count the row on their boundary.

        A connection that names an ``object_name`` gets only that object's rows.
        One history serves all three researched vendors here, and an export of
        ``CONTACT`` that answered with opportunities would be a demo nobody
        could read - so the tag is honoured where it is set and ignored where it
        is not, which is what lets a single-vendor deployment leave it out.
        """
        scope = scope or {}
        start = str(scope.get("from") or "")
        end = str(scope.get("to") or "")
        wanted = str((connection or {}).get("object_name") or "")
        selected = []
        for row in self._rows:
            if wanted and str(row.get("object") or "") != wanted:
                continue
            stamp = str(row.get("occurred_at") or "")
            if start and stamp < start:
                continue
            if end and stamp >= end:
                continue
            selected.append(row)
        return selected

    def count(self, connection: Mapping[str, Any], scope: Mapping[str, Any]) -> int:
        return len(self.scoped(connection, scope))

    def rows(
        self,
        connection: Mapping[str, Any],
        scope: Mapping[str, Any],
        *,
        offset: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return [dict(row) for row in self.scoped(connection, scope)[offset : offset + limit]]
