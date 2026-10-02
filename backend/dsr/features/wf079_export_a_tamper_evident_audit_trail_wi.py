"""WF-079: export a tamper-evident audit trail with IP and verification outcomes.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-079.md`` (section 12
of ``docs/research/raw/security-governance.md``). The domain is in
:mod:`dsr.audit_export`; this module is the three things a workflow needs in order
to exist in this product: the HTTP surface under a prefix of its own, the mapping
from its refusals to responses, and the demo data.

What the research specifies, and where each piece of it landed
--------------------------------------------------------------

*The paginated export keyed by document.* ``GET /rooms/{room_id}/audit-trail`` is
the page a compliance lead reads, and ``GET /rooms/{room_id}/documents/
{record_id}/audit-trail`` is the researched route
``GET /public/v2/documents/{document_id}/audit-trail`` - "Retrieves the full audit
trail for a **specified document**". Both carry ``limit``/``offset`` and the
researched entry tuple ``{id, user, action, reason, date_created, ip_address}``,
with the host's own columns alongside so a row can be chased back to the record it
describes.

*The administrator gate.* "This endpoint is accessible to authorized workspace
administrators only." Every route that returns evidence goes through
:func:`dsr.audit_export.access.require_administrator`, which fails closed. The two
routes that publish *the rules* - ``/vocabulary`` and ``/inferences`` - are not
gated, and that is a deliberate reading: the gate the source states is on the
audit-trail endpoint, and a page that cannot read the rules cannot explain its own
refusal. They describe no evidence.

*The stable integer action enum.* "The action enum is the critical extension
point - a sales room that adds its own events (NDA accepted, link revoked,
watermark toggled) needs a stable, additive integer vocabulary rather than
free-text strings, because compliance reviews query by code."
:mod:`dsr.audit_export.vocabulary` publishes the codes the research names, the
bands it names without naming their members, the integers left reserved so a team
cannot mint a code the vendor later publishes, and a floor above every sourced
band for this application's own codes. A record that declares an ``event_code``
of its own is exported under that integer and described as ``undeclared`` rather
than dropped or rewritten to something similar.

*IPs.* ``audit_log`` has no address column and ``dsr.db.audited`` is a shared
file this workflow may not edit, so an address is attributed to an audit row by
``POST /observations`` and stored as an ordinary audited record. A row with no
address is reported ``ip_status: "not_captured"`` - never an empty string, never
dropped. A sandbox key masks it to the literal ``"hidden"``, verbatim from the
source's field documentation.

*Verification outcomes.* Read, not written. ``47-54`` and ``69-70`` are resolved
from the pass/fail fact another workflow stamps on the record it writes, and the
export refuses to lose a rejected attempt: "Both success and failure produce audit
actions, so a rejected attempt is as visible as a successful one." An attempt
whose method has no slot in the band is reported as
``verification_unallocable``, which says why it has no code instead of leaving a
reviewer to guess.

*Tamper-evidence.* Every export carries an ``integrity`` block with the chain head,
the **exact** recipe - the six hashed fields, the canonical serialisation, the
seed, the step function, the order - and the honest statement of what a hash over
a list cannot prove. ``POST .../anchors`` pins a head so that a later read is
falsifiable rather than self-consistent, and ``GET .../verify`` reports the first
entry that diverged.

*The CSV report.* ``POST /reports`` with ``report_type`` in the researched enum,
``MM/DD/YYYY`` dates, a range of at most twelve months and a start no more than ten
years back; one report - and so one notification - per requested type, each
carrying a download link rather than the bytes.

Three things this module is careful about
-----------------------------------------

**``source`` is built from ``router.prefix`` and passed in from every route.** No
domain function hardcodes a path. A feature whose audit log names a route the app
stopped serving has shipped in this branch's history, and the guard is that
``source`` has no default in the domain layer, so it cannot regress.

**Dependencies come from ``dsr.deps``.** This module never imports ``dsr.api``; a
feature that reaches for the app reintroduces exactly the coupling the plugin host
exists to remove.

**Reads go through ``store.audit()`` and ``store.db.audit_count()``.** The two
read paths the host already serves at ``/api/audit`` and ``/api/audit/{seq}``. It
does not open the SQLite file, and it keeps no second copy of the events: a
parallel export log is a copy that can drift from the thing it claims to describe,
which would make this feature actively harmful rather than merely redundant.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from dsr.audit_export import (
    anchors,
    entries as domain_entries,
    integrity,
    reports as domain_reports,
    vocabulary,
)
from dsr.audit_export.access import (
    AccessDenied,
    gate_payload,
    require_administrator,
    sandbox_enabled,
)
from dsr.audit_export.entries import ObservationRefused
from dsr.audit_export.reports import ReportError
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-079-export-a-tamper-evident-audit-trail-wi",
    "ticket": "WF-079",
    "name": "Export a tamper-evident audit trail",
    "description": (
        "The audit trail as evidence: the researched integer action codes, the "
        "acting user, the client address, the pass/fail of every identity check, "
        "a SHA-256 chain a third party can recompute, and a date-ranged CSV "
        "report delivered by link."
    ),
    "nav": [{"id": "audit-trail", "label": "Audit trail"}],
}

router = APIRouter(prefix="/api/wf-079", tags=["wf-079"])


def _now() -> str:
    """UTC, millisecond precision - the same shape the audit log writes."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# The administrator gate
# --------------------------------------------------------------------------- #


def require_reader(
    role: str | None = Query(
        default=None, description="Caller role. The export is administrator-only."
    ),
    sandbox: bool | None = Query(
        default=None,
        description=(
            "True when the caller presented a sandbox key, which masks every "
            "address to the literal 'hidden'. Defaults to the "
            "DSR_AUDIT_EXPORT_SANDBOX environment variable."
        ),
    ),
) -> dict[str, Any]:
    """Every evidence route goes through here, and it fails closed.

    A dependency rather than a call inside each handler so a route added later
    cannot forget it. The one deliberate exception is ``/vocabulary``: the
    researched gate is on the audit-trail endpoint, and a client that cannot read
    the rules cannot render the refusal it just received.
    """
    return {"role": require_administrator(role), "sandbox": sandbox_enabled(sandbox)}


ReaderDep = Depends(require_reader)


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

#: Which researched rule each report refusal breaks, and therefore what status it
#: is. 400 for a malformed request, 404 for one naming something that is not
#: there, 409 for one that is well-formed but not in a state to answer.
_REPORT_STATUS = {
    domain_reports.ERROR_REPORT_NOT_FOUND: 404,
    domain_reports.ERROR_REPORT_NOT_READY: 409,
    domain_reports.ERROR_BAD_TOKEN: 403,
}


def _report_error(request: Request, exc: ReportError) -> JSONResponse:
    return JSONResponse(
        status_code=_REPORT_STATUS.get(exc.code, 400),
        content=exc.to_dict(),
    )


def _access_denied(request: Request, exc: AccessDenied) -> JSONResponse:
    """403 with the role that would have worked.

    A bare 403 here would be a support ticket: the caller cannot tell whether
    they sent the wrong role or the feature is misconfigured.
    """
    return JSONResponse(status_code=403, content=exc.to_dict())


def _observation_refused(request: Request, exc: ObservationRefused) -> JSONResponse:
    """409: the request was well formed, but the address it wants is already taken.

    First writer wins on an address, deliberately. An export whose address can be
    rewritten after the fact is not evidence of anything, so a second attribution
    for the same audit row conflicts with the first rather than replacing it.
    """
    return JSONResponse(status_code=409, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    AccessDenied: _access_denied,
    ReportError: _report_error,
    ObservationRefused: _observation_refused,
}


# --------------------------------------------------------------------------- #
# The rules, ungated
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every name, code and rule this workflow enforces")
def audit_vocabulary() -> dict[str, Any]:
    """The vocabulary, the report rules and the gate, as data.

    Served so a client renders its column headers, its code filter and its role
    switcher from the same table the exporter validates against, rather than from
    a list copied into a component that can drift. No evidence is in here, which
    is why this route is not behind the administrator gate.
    """
    return {
        "vocabulary": vocabulary.vocabulary_payload(),
        "reports": domain_reports.payload(),
        "access": gate_payload(),
        "ip_status": {
            domain_entries.IP_OBSERVED: "an address was attributed to this audit row",
            domain_entries.IP_HIDDEN: "a sandbox key was presented, so the address is masked",
            domain_entries.IP_NOT_CAPTURED: (
                "no address was ever attributed to this row. Reported rather than "
                "blank, because 'we did not record it' and 'we recorded nothing' "
                "are different facts."
            ),
        },
    }


@router.get("/inferences", summary="Every reading this build had to choose")
def audit_inferences() -> dict[str, Any]:
    """The judgement calls, named and bounded.

    The research quotes its codes, its mask and its date rules and then is silent
    on several things this build had to decide. This is the list of everything that
    follows from those silences, so a reviewer can disagree with a named entry
    instead of hunting through a diff. No evidence is in here either.
    """
    return {
        "action_codes": vocabulary.INFERENCES,
        "export": [
            {
                "claim": "An address is attributed to an audit row by a separate write.",
                "basis": (
                    "audit_log has no address column and dsr/db/audited.py is a "
                    "shared file this workflow may not edit, so the write path "
                    "cannot attach one. The attribution is a record holding an "
                    "address and the audit seq it belongs to. It holds no events, "
                    "no actors and no before/after state, so it is not a second "
                    "audit log and cannot drift from one."
                ),
            },
            {
                "claim": "A masked address is what the digest hashes.",
                "basis": (
                    "The export must be verifiable from the export alone. Hashing "
                    "the real address and masking it only on the way out produces "
                    "a digest nobody holding the file can recompute, which "
                    "defeats the point of shipping one."
                ),
            },
            {
                "claim": "Only the socket peer is trusted, never X-Forwarded-For.",
                "basis": (
                    "A forwarded header is set by the caller. An export that let a "
                    "caller choose its own recorded address would be worthless as "
                    "evidence. A deployment behind a proxy needs a trusted-proxy "
                    "list, which belongs in the shared write path."
                ),
            },
            {
                "claim": "The room trail walks the whole audit log.",
                "basis": (
                    "AuditedDatabase.audit has no room_id predicate and that "
                    "method is shared. The per-document trail avoids the walk by "
                    "pushing record_id down into the indexed column. The fix for "
                    "the room case belongs in db.audit, as platform work."
                ),
            },
            {
                "claim": "The evidence pack is a digest, not a PDF.",
                "basis": (
                    "The vendor merges the audit trail into the signed document's "
                    "bytes. This product stores documents as records, so there is "
                    "no byte stream to merge pages into. What lands is the part "
                    "that is falsifiable here: one digest over the trail, the "
                    "document hash and every signer interaction, which changes if "
                    "any of them is altered."
                ),
            },
        ],
        "reports": [
            {
                "claim": "Report generation is a route rather than a job.",
                "basis": (
                    "The source makes generation asynchronous and delivers it by "
                    "email. This product has no scheduler and no mail transport, "
                    "so a request returns before the file exists and generation is "
                    "a separate, idempotent step. The researched notification - "
                    "one per report type, carrying a link rather than the bytes - "
                    "is written to the report record as delivery fields. The link "
                    "it carries really does serve the CSV."
                ),
            },
            {
                "claim": "sms_activity and fax_usage export empty rather than fail.",
                "basis": (
                    "They are part of the researched enum, and the source offers "
                    "no second data source for them. Refusing a value the source "
                    "calls valid, or inventing rows to fill the file, are both "
                    "worse than an empty one with a header row and a note."
                ),
            },
            {
                "claim": "end_date is inclusive.",
                "basis": (
                    "The source speaks in dates and never in timestamps. A lead "
                    "asking for 'up to the 30th' means the 30th."
                ),
            },
        ],
    }


# --------------------------------------------------------------------------- #
# The export
# --------------------------------------------------------------------------- #


def _trail_response(
    entries: list[dict[str, Any]],
    total: int,
    *,
    room_id: str,
    record_id: str | None,
    reader: dict[str, Any],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """One envelope for both trails, with the integrity block on it.

    The block is on every export rather than behind a separate route because the
    recipe is what makes the digest worth having: a page that shows entries and a
    page that shows how to check them must come from the same call, or a client
    can render an export it cannot verify.
    """
    page = entries[offset : offset + limit]
    check = integrity.verify(page)
    head = str(entries[-1]["chain"]) if entries else integrity.chain_seed()
    return {
        "room_id": room_id,
        "record_id": record_id,
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "sandbox": reader["sandbox"],
        "entries": page,
        "integrity": integrity.integrity_payload(
            entries=entries,
            room_id=room_id,
            head=head,
            verified=check["verified"],
            first_mismatch=check["first_mismatch"],
        ),
        "note": (
            "The integrity block's head and its recipe cover every entry matching "
            "this filter, not just this page: the chain is folded over the whole "
            "filtered trail so that a page can be checked on its own."
        ),
    }


@router.get("/rooms/{room_id}/audit-trail", summary="The room's audit trail, newest first")
def room_audit_trail(
    room_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: int | None = Query(
        default=None, description="One researched integer action code, not a name."
    ),
    actor: str | None = Query(default=None),
    collection: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO instant, inclusive"),
    until: str | None = Query(default=None, description="ISO instant, inclusive"),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """The paginated export, in the researched entry shape.

    ``action`` is the integer code, because "compliance reviews query by code" and
    because a filter that took a name would break the first time a label was
    reworded. An entry whose code this workflow does not recognise is still
    returned, under its own integer, described as ``undeclared``.
    """
    all_entries, total = domain_entries.build_trail(
        store,
        room_id,
        sandbox=reader["sandbox"],
        since=since,
        until=until,
        action=action,
        actor=actor,
        collection=collection,
    )
    return _trail_response(
        all_entries,
        total,
        room_id=room_id,
        record_id=None,
        reader=reader,
        limit=limit,
        offset=offset,
    )


@router.get("/rooms/{room_id}/audit-trail/summary", summary="Counts a compliance lead reads first")
def room_audit_summary(
    room_id: str,
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """Who acted, from where, how it went, and against how big a log.

    The last number matters most: ``audit_log_rows`` is the whole log and
    ``scoped_rows`` is this room's window into it. A reviewer who sees a trail of
    four rows for a room and a log of four thousand can tell the trail is scoped
    rather than broken.
    """
    all_entries, total = domain_entries.build_trail(
        store, room_id, sandbox=reader["sandbox"], since=since, until=until
    )
    summary = domain_entries.summarise(all_entries)
    summary["room_id"] = room_id
    summary["scoped_rows"] = total
    summary["audit_log_rows"] = domain_entries.audit_total(store)
    summary["sandbox"] = reader["sandbox"]
    summary["read_role"] = reader["role"]
    return summary


@router.get("/rooms/{room_id}/audit-trail/verify", summary="Check this trail against its anchors")
def verify_trail(
    room_id: str,
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    (
        """Recompute each pinned scope from the current database and compare.

    Reports the first entry whose chain diverged, not just that something did.
    The honest limit is carried in every anchor's ``meaning``: an anchor lives in
    the same database as the trail, so it detects divergence - a corrupted or
    partially restored database, a row rewritten without the chain - and it is not
    a defence against a writer who can recompute a digest.
    """
        """
    **Declared above ``/audit-trail/{seq}`` on purpose.** Starlette matches routes
    in registration order, so a literal path registered after a parameterised
    sibling is dead code: ``/audit-trail/verify`` is captured by ``{seq}`` and
    answered 422, because ``verify`` is not an integer. The feature registry
    reports both paths either way, so nothing else in the product would notice.
    """
    )
    pinned = anchors.listing(store, room_id=room_id, limit=100)
    checks: list[dict[str, Any]] = []
    for anchor in pinned:
        scope = dict((anchor.get("data") or {}).get("scope") or {})
        rebuilt, _ = domain_entries.build_trail(
            store,
            str(scope.get("room_id") or room_id),
            sandbox=reader["sandbox"],
            since=scope.get("since"),
            until=scope.get("until"),
            action=scope.get("action"),
            actor=scope.get("actor"),
            collection=scope.get("collection"),
            record_id=scope.get("record_id"),
            # Truncate at the anchor’s own upto_seq. Without it the rebuild
            # includes rows written since the pin, and every anchor would
            # report divergence from the moment it was taken.
            upto_seq=(anchor.get("data") or {}).get("upto_seq"),
        )
        checks.append(anchors.check(anchor, rebuilt))
    return {
        "room_id": room_id,
        "anchors": len(checks),
        "intact": all(check["intact"] for check in checks),
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# Attributing a client address
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/audit-trail/{seq}", summary="One audit entry, by its seq")
def room_audit_entry(
    room_id: str,
    seq: int,
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> Any:
    """One entry, addressed the way the host already addresses one at ``/api/audit/{seq}``.

    Looked up in the projected trail rather than by walking the raw log, so the
    entry a caller reads here is byte-for-byte the entry they would have read off
    the list - including its chain. A second projection for a single-entry read
    would be a second thing to keep in step.
    """
    all_entries, _ = domain_entries.build_trail(store, room_id, sandbox=reader["sandbox"])
    for entry in all_entries:
        if entry.get("seq") == seq:
            return entry
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"no audit entry {seq} in room {room_id}"},
    )


@router.get(
    "/rooms/{room_id}/documents/{record_id}/audit-trail",
    summary="One document's trail (the researched per-document route)",
)
def document_audit_trail(
    room_id: str,
    record_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """``GET /public/v2/documents/{document_id}/audit-trail``, keyed by document.

    "Retrieves the full audit trail for a specified document." Read through the
    indexed ``record_id`` rather than the room walk, and scoped to ``room_id`` as a
    check rather than a filter: the row carries the room the change belonged to,
    so this is what stops one room's lead reading another's trail by guessing an
    id. A record with no audited rows yields an empty trail rather than a 404 -
    a document nobody has touched is a real answer, not a missing one.
    """
    all_entries, total = domain_entries.document_trail(
        store, record_id, room_id=room_id, sandbox=reader["sandbox"], since=since, until=until
    )
    return _trail_response(
        all_entries,
        total,
        room_id=room_id,
        record_id=record_id,
        reader=reader,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/rooms/{room_id}/documents/{record_id}/evidence-pack",
    summary="The e-signature evidence for one document, with one digest over all of it",
)
def document_evidence_pack(
    room_id: str,
    record_id: str,
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """The trail, the document hash, and every signer interaction, under one digest.

    The researched flow puts this evidence "in the final PDF as an audit-trail
    section, so it travels with the document". This build produces a digest rather
    than a file - see ``inferences`` - and says so in ``basis`` on every response
    rather than letting the word "tamper-evident" imply a guarantee it cannot make.
    """
    pack = domain_entries.evidence_pack(
        store, record_id, room_id=room_id, sandbox=reader["sandbox"], since=since, until=until
    )
    pack["integrity"] = integrity.integrity_payload(
        entries=pack["entries"],
        room_id=room_id,
        head=str(pack["entries"][-1]["chain"]) if pack["entries"] else integrity.chain_seed(),
        verified=integrity.verify(pack["entries"])["verified"],
        first_mismatch=integrity.verify(pack["entries"])["first_mismatch"],
    )
    return pack


# --------------------------------------------------------------------------- #
# Tamper-evidence: pinning a head, and falsifying it later
# --------------------------------------------------------------------------- #


@router.post(
    "/rooms/{room_id}/audit-trail/anchors", status_code=201, summary="Pin this trail's chain head"
)
def pin_anchor(
    room_id: str,
    action: int | None = Query(default=None),
    actor: str | None = Query(default=None),
    collection: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """Record the head digest that exists now, with the scope it was taken over.

    A fresh export's chain always agrees with itself, because it is sealed as it
    is built - so recomputing it proves internal consistency and nothing more. An
    anchor is what a later read compares against.

    Written through the audited store with the route's own path as ``source``, so
    the anchor is itself a row in the trail it describes.
    """
    scope = anchors.scope_of(
        room_id,
        action=action,
        actor=actor,
        collection=collection,
        since=since,
        until=until,
    )
    all_entries, total = domain_entries.build_trail(
        store,
        room_id,
        sandbox=reader["sandbox"],
        since=since,
        until=until,
        action=action,
        actor=actor,
        collection=collection,
    )
    record = anchors.pin(
        store,
        all_entries,
        scope=scope,
        pinned_at=_now(),
        actor=reader["role"],
        source=f"POST {router.prefix}/rooms/{room_id}/audit-trail/anchors",
        room_id=room_id,
    )
    return {"pinned": True, "entries": total, "anchor": record}


@router.post(
    "/observations", status_code=201, summary="Attribute a client address to an audited change"
)
def create_observation(
    payload: dict[str, Any] = Body(default_factory=dict),
    seq: int | None = Query(default=None, description="The audit row this address belongs to"),
    room_id: str | None = Query(default=None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Attach an observed address to one audit row.

    First writer wins. A second attribution for the same row is a 409 rather than
    an overwrite, because an export whose address can be rewritten after the fact
    is not evidence of anything.

    Deliberately **not** behind the administrator gate: this is the write that
    makes the gated export worth reading, so gating it would mean a sandbox could
    never populate its own trail. It carries no evidence and discloses none - it
    stores what the caller already knows.
    """
    # ``seq`` is accepted in the body as well as the query, because every other
    # attribute of an observation is in the body. Reading it only from the query
    # meant a caller who put it in the body - the obvious place - was told to
    # supply a seq or request_id it had already supplied.
    raw_seq = payload.get("seq")
    if raw_seq is not None:
        try:
            seq = int(raw_seq)
        except (TypeError, ValueError):
            raise ReportError(
                "invalid_seq",
                f"seq must be an integer, got {raw_seq!r}",
                remediation="Pass the audit entry seq as an integer.",
            ) from None
    address = payload.get("ip_address") or payload.get("address")
    record = domain_entries.observe_ip(
        store,
        ip_address=address,
        seq=seq,
        request_id=payload.get("request_id"),
        user_agent=payload.get("user_agent"),
        room_id=room_id,
        attributed_by=payload.get("actor"),
        source=f"POST {router.prefix}/observations",
    )
    return {
        "observed": True,
        "seq": seq,
        "observation": record.get("data"),
        "ip_status": domain_entries.IP_OBSERVED,
    }


# --------------------------------------------------------------------------- #
# The date-ranged CSV report
# --------------------------------------------------------------------------- #


@router.post("/reports", status_code=202, summary="Request CSV reports over a date range")
def request_reports(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """Accept the researched request and record one report per requested type.

    ``report_type`` may be one value or a list, because the source's delivery rule
    is per type: "you will receive an email (one per requested report type)". A
    request that could only carry one type would make that rule unobservable in
    the API, and the rule is the reason a list exists.

    ``202`` rather than ``201``: the file does not exist yet. Generation is a
    separate, idempotent step - see ``/reports/{report_id}/generate`` and the
    ``reports`` inferences, which say why there is no scheduler here.
    """
    validated = domain_reports.validate_request(
        payload.get("report_type") or payload.get("report_types"),
        payload.get("start_date"),
        payload.get("end_date"),
        payload.get("email"),
    )
    records = domain_reports.request(
        store,
        validated,
        requested_at=_now(),
        actor=actor or reader["role"],
        source=f"POST {router.prefix}/reports",
        room_id=room_id,
    )
    return {
        "accepted": True,
        "requested": len(records),
        "one_notification_per_report_type": True,
        # The id is carried alongside the payload, not buried out of reach: without
        # it a client cannot take the next step. ``/reports/{report_id}`` is
        # generation and ``/reports/{report_id}/download`` is the link the delivery
        # record issues, so a response that omitted the id would make the researched
        # request-then-notify-then-download flow unfollowable through the API.
        "reports": [{"id": record["id"], **(record.get("data") or {})} for record in records],
    }


@router.get("/reports", summary="Report requests, newest first")
def list_reports(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """Every report request this workspace has made.

    Does not generate anything. A read that mutated state would make ``GET`` mean
    two different things, and the asynchronous property the source describes is
    preserved by ``POST /reports`` returning before the file exists rather than by
    a read quietly finishing the job.
    """
    records = domain_reports.listing(store, limit=limit, offset=offset)
    return {
        "count": len(records),
        # Ids for the same reason ``POST /reports`` carries them: a listing a client
        # cannot act on is a report it cannot generate or download.
        "reports": [{"id": record["id"], **(record.get("data") or {})} for record in records],
    }


@router.get("/reports/{report_id}", summary="One report, its rows and its delivery")
def read_report(
    report_id: str,
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """The report's state, its row count, its digest, and the notification issued.

    A pending report reports ``pending`` with no digest rather than a 404, because
    "asked for, not yet generated" is a state a lead needs to see.
    """
    record = domain_reports.read(store, report_id)
    return {"report": record.get("data"), "id": record.get("id")}


@router.post("/reports/{report_id}/generate", summary="Generate the CSV and record its delivery")
def generate_report(
    report_id: str,
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> dict[str, Any]:
    """Build the file and write down the notification the source describes.

    Idempotent: a report that is already ``ready`` is returned with its original
    digest, so regenerating cannot quietly produce a different file for a link
    that has already gone out.

    The delivery is **recorded, not sent**. This product has no mail transport;
    what lands is the researched shape - one notification per report type, each
    carrying a link rather than the bytes - written to the report record so it is
    auditable, and the link it carries really does serve the CSV.
    """
    record = domain_reports.build(
        store, report_id, source=f"POST {router.prefix}/reports/{report_id}/generate", at=_now()
    )
    return {"report": record.get("data"), "id": record.get("id")}


@router.get(
    "/reports/{report_id}/download",
    summary="The CSV the notification linked to",
    response_class=PlainTextResponse,
)
def download_report(
    report_id: str,
    token: str | None = Query(default=None, description="From the delivery record's link"),
    store: RecordStore = StoreDep,
    reader: dict[str, Any] = ReaderDep,
) -> PlainTextResponse:
    """The CSV itself, for the link the delivery record issued.

    Served as ``text/csv`` with a ``Content-Disposition`` filename, because the
    recipient is a person opening a browser rather than a client that reads a
    content type. The token must match the one in the delivery record, and the
    report must be ``ready``; a report that is not is a 409, not an empty file,
    so a link that is followed too early says why.
    """
    text = domain_reports.csv_for(store, report_id, token)
    record = domain_reports.read(store, report_id)
    report_type = str((record.get("data") or {}).get("report_type") or "audit")
    filename = f"audit-export-{report_type}-{report_id}.csv"
    return PlainTextResponse(
        text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audit-Rows": str((record.get("data") or {}).get("row_count", 0)),
            "X-Audit-Sha256": str((record.get("data") or {}).get("sha256", "")),
        },
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo seeds one document per demo room, in the states worth reviewing rather
#: than only the happy path:
#:
#:   rooms[0]  Declined, with identity checks. Two verification records - one KBA
#:              pass and one KBA *failure* - plus an email-OTP pass, each with an
#:              attributed address. The failure is the row a compliance reviewer
#:              most needs to see, and the one an implementation is most likely to
#:              drop, so it is here.
#:   rooms[1]  Expired. A status update, so the trail shows a lifecycle code
#:              derived from a status rather than declared.
#:   rooms[2]  Completed manually, then forwarded. Two status updates, and a
#:              ``verification`` record with no slot in the 47-54 band, so the
#:              page shows what "unallocable" looks like rather than describing it.
#:   rooms[3]  Untouched. One insert and no addresses at all, which is what
#:              ``ip_status: "not_captured"`` looks like.
#:
#: The addresses come from RFC 5737 documentation space (``192.0.2.0/24``), so a
#: demo row is never a real address and never resolves to a real host.
SEED_VERIFICATION: tuple[dict[str, Any], ...] = (
    {
        "room": 0,
        "method": "kba",
        "outcome": "pass",
        "ip": "192.0.2.10",
        "agent": "Mozilla/5.0 (seed)",
    },
    {
        "room": 0,
        "method": "kba",
        "outcome": "fail",
        "ip": "192.0.2.11",
        "agent": "Mozilla/5.0 (seed)",
    },
    {"room": 0, "method": "email_otp", "outcome": "pass", "ip": "192.0.2.12", "agent": None},
    {"room": 1, "method": "sms", "outcome": "pass", "ip": "192.0.2.13", "agent": None},
    {
        "room": 2,
        "method": "face",
        "outcome": "fail",
        "ip": "192.0.2.14",
        "agent": None,
    },
)

#: ``(room index, status)`` pairs applied to the seeded documents, in order.
SEED_STATUSES: tuple[dict[str, Any], ...] = (
    {"room": 0, "status": "declined", "reason": "Procurement declined; terms not agreed."},
    {"room": 1, "status": "expired", "reason": "Reached its expiry without every signer."},
    {"room": 2, "status": "completed_manually", "reason": "Completed by the rep."},
    {"room": 2, "status": "forwarded", "reason": "Forwarded to the signatory at Contoso."},
)


def _latest_seq(db: AuditedDatabase, record_id: str) -> int | None:
    """The newest audit ``seq`` for a record, read through the audited wrapper.

    Used by the seed to attribute an address to the row that was just written. It
    reads ``db.audit`` - the same read path the host serves at ``/api/audit`` -
    rather than querying SQLite, so the demo cannot demonstrate a capability the
    product does not actually expose.
    """
    rows = db.audit(record_id=record_id, limit=1)
    return int(rows[0]["seq"]) if rows else None


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the four researched states, and the trap that keeps the export honest.

    Every write goes through the audited database, so each seeded row is a real
    row in the trail the page exports - the demo cannot show a shape the workflow
    would not produce.

    Returns a short description, which the seeder prints. A database with no demo
    rooms still gets nothing, and says so rather than inventing rooms to hang
    documents on.
    """
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 audit rows (no rooms to scope them to)"

    documents: dict[int, str] = {}
    for index, (room_id, account) in enumerate(rooms[:4]):
        record = db.create(
            "document",
            {
                "title": f"Mutual NDA - {account}",
                "status": "draft",
                "folder": "documents",
                "uploaded_by": "dana",
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        documents[index] = record["id"]

    for plan in SEED_STATUSES:
        record_id = documents.get(int(plan["room"]))
        if record_id is None:
            continue
        db.update(
            record_id,
            {"status": plan["status"], "reason": plan["reason"]},
            actor="sam",
            source="seed",
        )

    # A room that declares its own code, to show the additive extension point the
    # research asks for: an integer nothing here claims is exported verbatim and
    # described as undeclared, rather than dropped or snapped to a similar code.
    if 3 in documents:
        db.update(
            documents[3],
            {"event_code": 4242, "reason": "Watermark toggled by the room owner."},
            actor="dana",
            source="seed",
        )

    observed = 0
    for plan in SEED_VERIFICATION:
        room_index = int(plan["room"])
        record_id = documents.get(room_index)
        if record_id is None:
            continue
        room_id = rooms[room_index][0]
        # A recipient verification record, written the way the workflow that owns
        # verification writes one: the pass/fail fact on the payload. The export
        # reads it; it does not own it.
        record = db.create(
            "recipient_verification",
            {
                "method": plan["method"],
                "outcome": plan["outcome"],
                "gate": "before_open",
                "email": f"signer@{rooms[room_index][1].split()[0].lower()}.example",
            },
            room_id=room_id,
            actor=rooms[room_index][1].split()[0].lower(),
            source="seed",
        )
        seq = _latest_seq(db, record["id"])
        if seq is None or not plan.get("ip"):
            continue
        domain_entries.observe_ip(
            RecordStore(db),
            ip_address=plan["ip"],
            seq=seq,
            user_agent=plan.get("agent"),
            room_id=room_id,
            attributed_by="seed",
            source="seed",
        )
        observed += 1

    # One report request, generated so the demo shows a delivered link, and one
    # left pending so the page shows both states without interaction.
    started, finished = _seed_report_range(context)
    store = RecordStore(db)
    stamp = context["now"].isoformat(timespec="milliseconds")
    for report in domain_reports.request(
        store,
        domain_reports.validate_request(
            ["user_activity", "document_status"],
            started.strftime(domain_reports.DATE_FORMAT),
            finished.strftime(domain_reports.DATE_FORMAT),
            "compliance@northwind.example",
        ),
        requested_at=stamp,
        actor="dana",
        source="seed",
        room_id=rooms[0][0],
    ):
        domain_reports.build(store, report["id"], source="seed", at=stamp)
    domain_reports.request(
        store,
        domain_reports.validate_request(
            ["user_activity"],
            started.strftime(domain_reports.DATE_FORMAT),
            finished.strftime(domain_reports.DATE_FORMAT),
            "compliance@northwind.example",
        ),
        requested_at=stamp,
        actor="dana",
        source="seed",
        room_id=rooms[0][0],
    )

    return (
        f"{len(documents)} seeded documents across {len(documents)} rooms: "
        "declined with 3 identity checks (one failed), expired, completed-then-"
        f"forwarded, and one untouched. {observed} client addresses attributed, "
        f"{len(SEED_STATUSES)} status transitions, 1 unclaimed event_code. "
        "3 CSV reports (2 ready, 1 pending)."
    )


def _seed_report_range(context: dict[str, Any]) -> tuple[date, date]:
    """A ``MM/DD/YYYY`` range inside the rules, derived from the seeder's clock.

    Thirty days back to now, so the range is short enough for the twelve-month
    rule and recent enough for the ten-year one however long after the code was
    written the demo is seeded.
    """
    now: datetime = context["now"]
    today = now.date()
    return today - timedelta(days=30), today
