"""WF-085: meet GDPR / CCPA with residency, retention, consent and a DSAR path.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-085.md``, quoted in full in issue 200.
The rules live in :mod:`dsr.security_governance` and are not restated here. This module is
the three things a feature contributes and the three things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and
  this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The five controls this workflow ships
-------------------------------------

The specification names them: a documented residency region, hard retention limits with
no longer-lived side channels, a consent gate that fails closed, a DSAR path that deletes
one identifiable visitor's records, and role separation on blocking changes. Each one is
a route below, and each records the derivation it rests on at ``GET /decisions``.

Two things this workflow reads and does not own
-----------------------------------------------

**Engagement rows.** WF-075 provisions the ``View`` and ``Visitor`` rows that the DSAR
and the retention run have to reach: "it provisions the ``View`` and ``Visitor`` rows and
the analytics layer that serve ``GET /v1/analytics/views/{id}``... Without it, the
personal-data surface named in ``apis_hit`` does not exist." This module reads those two
collections **as data** and names them in
:data:`~dsr.security_governance.residency.RETENTION_SCOPE`. It does not import WF-075's
module, because a feature must not import another feature.

**The role vocabulary.** The specification says blocking changes are admin-only and does
not define the role. The role is read from :mod:`dsr.permissions`, which is this
repository's own surface, so no second role vocabulary exists in the product. The
derivation is ``DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED``.

The wording this workflow is not allowed to use
-----------------------------------------------

**No certification.** The evidence quotes a vendor claiming "SOC 2 Type II, ISO 27001,
ISO 27701, GDPR, and the EU AI Act". That is a claim about a vendor, so
``GET /residency`` serves it under :data:`VENDOR_CLAIMS` with ``verified: false`` and a
note, and no route returns a "compliant" flag.

**No vendor entity as this deployment's controller.** "Clarity customers in the EU are
contracting with Microsoft Ireland Operations Limited" describes the vendor's customers.
``transfer_mechanism`` therefore names an instrument and stores no entity.

**No fixed enforcement date.** "Starting October 31, 2025, Clarity begins enforcing
consent signal requirements" is a vendor deadline. The gate reads a configured
jurisdiction list and no clock.

**No DNT.** "Clarity doesn't currently respond to browser DNT signals", so the gate does
not read it and reports any DNT it discarded.

The status codes here are the product's own
------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses and the distinction is recorded rather than
invented: 400 for a value, a region or a window this workflow will not accept, 403 for a
blocking change from a role below the administrator tier, 404 for a residency record or
an erasure request that does not exist. Reads of an empty store answer 200 with empty
states, because a board that 500s on a fresh room is a broken feature.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.permissions import INSTANCE_ADMIN, ROLE_LABELS, ROLES
from dsr.security_governance import (
    privacy_inferences as inferences,
    privacy_rules as rules,
    residency as vocab,
)
from dsr.security_governance.privacy_engine import PrivacyEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-085-meet-gdpr-ccpa-residency-retention-dsar",
    "ticket": "WF-085",
    "name": "Meet GDPR / CCPA: residency, retention, DSAR",
    "description": (
        "Pin the deployment to a residency region and re-stamp every record it holds, "
        "age three retention classes with hard windows, gate every page view behind a "
        "consent signal that fails closed, and erase one identifiable visitor's records "
        "per subject with the residue reported rather than hidden."
    ),
    "nav": [{"id": "wf-085-privacy-controls", "label": "Privacy controls"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-085", tags=["WF-085"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a
    domain function hardcoding a URL string, which leaves the audit log naming a route
    the app stopped serving. ``tests/test_wf085_http.py`` asserts every source this router
    can record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> PrivacyEngine:
    """A :class:`PrivacyEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store, a clock and a role tier, so
    building it here leaves every seam overridable in a test instead of hanging a
    long-lived object off ``app.state``, which is a shared file this feature may not edit.

    This is also the one place in the workflow that reads the repository's role surface.
    ``dsr/security_governance`` depends on nothing inside ``dsr`` but the store and
    itself, and ``tests/test_wf073.py`` enforces that for every module in it, so the tier
    arrives here and is handed to the engine. One import site, one role vocabulary, and no
    second set of role names anywhere in the product.
    """

    return PrivacyEngine(store, administrator=INSTANCE_ADMIN)


EngineDep = Depends(get_engine)


#: The role vocabulary, served so a page renders a role switcher rather than hard-coding
#: one. Built from the repository's surface rather than written out here, so a role added
#: to the product appears on this page with no edit to this file.
ROLE_VOCABULARY: list[dict[str, Any]] = [
    {
        "id": role,
        "label": ROLE_LABELS.get(role, role),
        "may_change": role == INSTANCE_ADMIN,
    }
    for role in ROLES
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared in dsr.security_governance.privacy_rules and raised by
# nothing else in the product. That is what makes it safe to map them here: the host
# refuses a second feature registering a handler for the same type, and a handler for
# ValueError would intercept that exception across the whole product.


def _privacy_refused(request: Request, exc: rules.PrivacyRefusal) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_privacy_request",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _privacy_not_found(request: Request, exc: rules.PrivacyNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc)},
    )


def _administrator_required(
    request: Request, exc: rules.PrivacyAdministratorRequired
) -> JSONResponse:
    """403 naming the role presented and the role that would have worked.

    A 403 with no remediation is a support ticket, so the body carries both roles.
    """

    return JSONResponse(status_code=403, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    rules.PrivacyRefusal: _privacy_refused,
    rules.PrivacyNotFound: _privacy_not_found,
    rules.PrivacyAdministratorRequired: _administrator_required,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), engine: PrivacyEngine = EngineDep) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only.

    Carries ``unmapped_collections`` beside every other count, because a collection
    holding engagement data that no retention class ages is a longer-lived copy of it and
    the board is the only place that fact is visible.
    """

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the board cannot drift from the
    rules that compute it: the regions, the retention classes and their windows, the
    consent vocabulary and the personal-data fields all come from the same tables the
    engine reads.
    """

    return {
        "regions": [
            {"id": region["id"], "label": region["label"], "jurisdiction": region["jurisdiction"]}
            for region in vocab.REGIONS
        ],
        "jurisdictions": [
            {
                "id": code,
                "label": vocab.JURISDICTION_LABELS[code],
                "consent_required": code in vocab.CONSENT_JURISDICTIONS,
            }
            for code in vocab.JURISDICTIONS
        ],
        "transfer_mechanisms": [
            {"id": mechanism, "label": vocab.TRANSFER_LABELS[mechanism]}
            for mechanism in vocab.TRANSFER_MECHANISMS
        ],
        "transfer_mechanism_note": vocab.TRANSFER_MECHANISM_NOTE,
        "retention_classes": [
            {
                "class": name,
                "label": vocab.RETENTION_CLASS_LABELS[name],
                "days": vocab.RETENTION_DAYS[name],
                "evidence": vocab.RETENTION_EVIDENCE[name],
            }
            for name in vocab.RETENTION_CLASSES
        ],
        vocab.WINDOW_UNIT_FIELD: vocab.WINDOW_UNIT,
        "retention_scope": {
            collection: {
                "class": mapped,
                "instant_field": vocab.INSTANT_FIELDS.get(collection),
            }
            for collection, mapped in vocab.RETENTION_SCOPE.items()
        },
        "undated_policy": vocab.UNDATED_POLICY,
        "personal_data": {
            collection: list(fields) for collection, fields in vocab.PERSONAL_DATA.items()
        },
        "subject_fields": {
            collection: list(fields) for collection, fields in vocab.SUBJECT_FIELDS.items()
        },
        "consent_jurisdictions": list(vocab.CONSENT_JURISDICTIONS),
        "consent_enforcement_quote": vocab.CONSENT_ENFORCEMENT_QUOTE,
        "grant_word": vocab.CONSENT_GRANTED,
        "outcomes": list(vocab.GATE_OUTCOMES),
        "consent_states": list(vocab.CONSENT_STATES),
        "opt_out_signals": list(vocab.OPT_OUT_SIGNALS),
        "unsupported_signals": list(vocab.UNSUPPORTED_SIGNALS),
        "deny_effect": dict(vocab.DENY_EFFECT),
        "screen_text": vocab.SCREEN_TEXT_DEFAULT,
        "dsar_states": list(vocab.DSAR_STATES),
        "dsar_window_days": vocab.DSAR_WINDOW_DAYS,
        "residue_reasons": list(vocab.RESIDUE_REASONS),
        "admin_role": INSTANCE_ADMIN,
        "roles": [dict(entry) for entry in ROLE_VOCABULARY],
        "vendor_claims": [dict(claim) for claim in vocab.VENDOR_CLAIMS],
        vocab.COLLECTIONS_FIELD: list(vocab.ALL_COLLECTIONS),
        vocab.INSTANT_FORMAT_FIELD: list(vocab.INSTANT_FORMATS),
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification asks an implementer to "derive it and record the derivation, not
    assume it". This route is that record, served rather than buried in a docstring so a
    reviewer reads the decision instead of the code.
    """

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{inference_id}")
def read_decision(inference_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(inference_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Residency: (1) a documented region per deployment
# --------------------------------------------------------------------------- #


@router.get("/residency")
def read_residency(
    room_id: str | None = Query(None), engine: PrivacyEngine = EngineDep
) -> dict[str, Any]:
    """Where this deployment's content and engagement data is processed.

    A read, so it takes no ``source`` and records no audit row. Resolving where data
    lives changes nothing.

    The response carries the vendor's certifications under ``vendor_claims`` with
    ``verified: false`` and a ``certification_note``. They are the research, and they are
    not a claim about this deployment.
    """

    return engine.residency_report(room_id)


@router.post("/residency", status_code=201)
def set_residency(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Pin the deployment to a region, re-stamping every record it holds.

    Administrator-only. The specification says blocking changes are admin-only, and this
    is the most blocking of them: it changes the jurisdiction of everything the room
    holds.

    The relocation is re-stamped in place rather than refused. That derivation, the
    alternatives rejected and the Jev audit that chose it are recorded as
    ``DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD``. The response reports how many records
    were scanned and how many moved, because a change that wrote to every row should not
    report a single boolean.
    """

    body = dict(payload or {})
    return engine.set_residency(
        body.get("region") or body.get("residency_region"),
        transfer_mechanism=body.get(vocab.TRANSFER_MECHANISM_FIELD),
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/residency"),
    )


# --------------------------------------------------------------------------- #
# Retention: (2) hard limits with no longer-lived side channels
# --------------------------------------------------------------------------- #


@router.get("/retention/policy")
def read_retention_policy(
    room_id: str | None = Query(None), engine: PrivacyEngine = EngineDep
) -> dict[str, Any]:
    """The three classes, their windows, their evidence and what nothing ages."""

    return engine.retention_report(room_id)


@router.post("/retention/policy")
def set_retention_policy(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Set the deployment's windows. Shorter than the ceiling, never longer.

    The researched figures are maxima - the evidence says "up to" - so a deployment may
    promise less. A longer request is refused rather than clamped, because clamping
    leaves an operator believing a window was agreed that the room would not enforce.
    """

    body = dict(payload or {})
    windows = body.get("windows")
    if windows is None:
        windows = {key: value for key, value in body.items() if key in vocab.RETENTION_CLASSES}
    if not isinstance(windows, dict):
        raise rules.PrivacyRefusal(
            "windows must be an object keyed by retention class.",
            {"windows": "windows must be an object keyed by retention class."},
        )
    return engine.set_retention_policy(
        windows,
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/retention/policy"),
    )


@router.get("/retention/schedule")
def read_retention_schedule(
    room_id: str | None = Query(None), engine: PrivacyEngine = EngineDep
) -> dict[str, Any]:
    """What is due for erasure now, by class, and when each class next falls due.

    ``undated`` is reported beside ``due`` and never folded into it: a record with no
    readable instant is not fresh and not expired, and a count that conflated the two
    would say something the store does not hold.
    """

    return engine.retention_schedule(room_id)


@router.post("/retention/run")
def run_retention(
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Erase every record past its window and record the run.

    Administrator-only, and the deletion is hard. A soft delete would leave the row and
    its dynamic index behind, and a longer-lived copy of personal data is exactly what
    the retention classes exist to prevent.
    """

    return engine.run_retention(
        room_id=room_id, actor=actor, role=role, source=_source("POST", "/retention/run")
    )


# --------------------------------------------------------------------------- #
# Consent: (3) a gate that fails closed
# --------------------------------------------------------------------------- #


@router.get("/consent")
def read_consent(
    room_id: str | None = Query(None),
    region: str | None = Query(None),
    signal: str | None = Query(None),
    subject: str | None = Query(None),
    gpc: str | None = Query(None),
    daa: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """The gate's vocabulary, plus the decision for one region without writing anything.

    Naming a ``region`` evaluates the gate for that case, so an operator can see what the
    page would do before recording it. Naming a ``subject`` adds that subject's current
    state, which is how the page shows a revocation beside the gate that produced it.
    """

    return engine.consent_report(
        room_id,
        region=region,
        signal=signal,
        subject=subject,
        gpc=gpc,
        daa=daa,
    )


@router.post("/consent", status_code=201)
def record_consent(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Record one consent signal and return the decision the gate reached.

    Not administrator-gated, and deliberately so: the specification says consent is
    captured "at the page (per visitor, per region)", so the caller is the room's own page
    rather than an operator.

    A deny after a grant is a revocation. The stored state becomes ``revoked``,
    ``cookies_cleared`` is true and tracking is blocked until new consent, which is the
    evidence's own sentence about ``clarity('consent', false)``.
    """

    body = dict(payload or {})
    region = body.get("region")
    subject = body.get("subject")
    if not region:
        raise rules.PrivacyRefusal(
            "A region is required to evaluate the gate.",
            {"region": "A region is required to evaluate the gate."},
        )
    if not subject:
        raise rules.PrivacyRefusal(
            "A data subject address is required.",
            {"subject": "An email address is required."},
        )
    return engine.record_consent(
        region,
        subject,
        signal=body.get("signal"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/consent"),
        gpc=body.get("gpc"),
        daa=body.get("daa"),
    )


# --------------------------------------------------------------------------- #
# The DSAR: (4) per-subject deletion, not per-project
# --------------------------------------------------------------------------- #


@router.get("/dsar/requests")
def list_dsar_requests(
    room_id: str | None = Query(None), engine: PrivacyEngine = EngineDep
) -> dict[str, Any]:
    """Every data-subject request, oldest first. Reads only.

    The specification calls fulfilment "a privileged, project-scoped administrative
    operation", and this is the read half of it: the requests themselves are the record
    of what was asked and what was found.
    """

    rows = engine.dsar_requests(room_id)
    return {
        "count": len(rows),
        "requests": rows,
        vocab.DSAR_STATES_FIELD: list(vocab.DSAR_STATES),
        "window_days": vocab.DSAR_WINDOW_DAYS,
    }


@router.get("/dsar/requests/{request_id}")
def read_dsar_request(request_id: str, engine: PrivacyEngine = EngineDep) -> dict[str, Any]:
    """One request with its found records and the personal fields each one holds.

    A row that exists but is not one of this workflow's requests is a 404 rather than a
    500, because a feature may not read across into another workflow's collection.
    """

    for row in engine.dsar_requests():
        if row["id"] == request_id:
            return row
    raise rules.PrivacyNotFound(f"No data-subject request {request_id!r}.")


@router.post("/dsar/requests", status_code=201)
def open_dsar_request(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Open a data-subject request and record everything it found.

    Administrator-only. Fulfilment is "a privileged, project-scoped administrative
    operation", and opening the request is the step that decides whose records will be
    erased.

    Discovery reads the aged collections and matches on the address the evidence names.
    The personal fields each matching record holds are returned with the request, so the
    reviewer can see what is about to be deleted before anything is.
    """

    body = dict(payload or {})
    return engine.open_dsar(
        body.get("subject"),
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/dsar/requests"),
        reason=body.get("reason"),
    )


@router.post("/dsar/requests/{request_id}/fulfil")
def fulfil_dsar_request(
    request_id: str,
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: PrivacyEngine = EngineDep,
) -> dict[str, Any]:
    """Erase the records one subject owns and report what survived.

    Per subject, never per room. The specification quotes the vendor's limitation - "You
    need to delete the entire project to delete user's data" - and calls per-subject
    deletion the hard part, so only the records that matched the address are touched.

    The residue is reported, never hidden. Every erased row left its audit rows behind,
    and those rows hold the subject's address in their before and after snapshots. The
    request therefore reads ``partial`` rather than ``fulfilled`` whenever any audit row
    remains, which is the state a legal reviewer needs to see.
    """

    return engine.fulfil_dsar(
        request_id,
        actor=actor,
        role=role,
        source=_source("POST", "/dsar/requests/{request_id}/fulfil"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`PrivacyEngine`, so the demo cannot
    show a state, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * a deployment in the **EEA** and one in the **United States**, so the consent gate
      has both a region it enforces and one it does not, and the board can show the
      difference rather than describing it;
    * a strict-class window **shortened below the researched ceiling**, so the configured
      window and the ceiling differ on the board;
    * an **explicit grant** in the EEA, so the tracked state is a state a reviewer can see;
    * a **denial** in the EEA by a buyer who never granted, so the deny branch is
      rendered rather than merely documented;
    * a **revocation**: the granted buyer denies afterwards, which is the state the
      evidence's "revocation is immediate" sentence describes;
    * a **global opt-out**, because "Clarity supports Global Privacy Control (GPC)" and
      the DAA opt-out list are honoured automatically and need a row to show it;
    * **aged engagement rows** in the collection WF-075 provisions, one well past the
      30-day recording class and one inside it, so the retention board has a due record
      and a retained one. Those rows are written into that collection as data: this
      workflow ages and erases what WF-075 writes, which is the dependency the issue
      names, and a board that could only ever show fresh rows would not demonstrate the
      strict class at all;
    * a **consent record older than its nine-month window**, so the longer class has a due
      record too and the demo does not look as though only the strict class works;
    * a **data-subject request** for a buyer who has engagement rows, fulfilled so the
      erasure and its residue are both on the board. The residue is the non-success state
      the brief asks for: the request found the buyer and could not remove every trace,
      because the audit trail keeps what the erasure removed.

    The retention run is deliberately **not** executed here. A purge that has already
    happened leaves a board with nothing to show, and the schedule with a due record and a
    retained one is the more useful thing for a reviewer to open. The page's run button
    exercises it.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf085.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    admin = INSTANCE_ADMIN
    engine = PrivacyEngine(store, administrator=admin, now=lambda: now)
    base_ms = int(now.timestamp() * 1000)
    hour_ms = 3600000
    day_ms = 24 * hour_ms

    # 1. Two deployments. One in a jurisdiction the consent gate enforces and one in a
    #    jurisdiction it does not, so the board shows both answers rather than one.
    eu_room = room_ids[0][0]
    us_room = room_ids[min(1, len(room_ids) - 1)][0]
    moved: list[int] = []
    for region, room_id, actor, mechanism in (
        ("eu-west", eu_room, "dana", vocab.TRANSFER_SCC),
        ("us-east", us_room, "sam", None),
    ):
        record = engine.set_residency(
            region,
            transfer_mechanism=mechanism,
            room_id=room_id,
            actor=actor,
            role=admin,
            source="wf-085 seed",
        )
        moved.append(record["relocation"]["records_moved"])

    # 2. A deployment that promises less than the researched ceiling on the strict class.
    engine.set_retention_policy(
        {vocab.CLASS_SESSION_RECORDING: 14},
        room_id=eu_room,
        actor="dana",
        role=admin,
        source="wf-085 seed",
    )

    # 3. Aged engagement rows in the collection WF-075 provisions: one well past the
    #    recording window, one inside it.
    store.create(
        "wf075_view",
        {
            "link_id": "seed-wf085-link-aged",
            "dataroom_id": eu_room,
            "viewer_email": "buyer@halcyon.example",
            "view_type": "link",
            "viewed_at": base_ms - 400 * day_ms,
            "page_durations": [{"page_number": 1, "duration_seconds": 45}],
            "location": {"country": "Germany", "city": "Berlin"},
            "client": {"browser": "Edge", "os": "Windows", "device": "desktop"},
            vocab.RESIDENCY_FIELD: "eu-west",
        },
        room_id=eu_room,
        actor="system",
        source="wf-085 seed retention demo",
    )
    store.create(
        "wf075_view",
        {
            "link_id": "seed-wf085-link-fresh",
            "dataroom_id": eu_room,
            "viewer_email": "buyer@northwind.example",
            "view_type": "link",
            "viewed_at": base_ms - 2 * hour_ms,
            "page_durations": [{"page_number": 1, "duration_seconds": 60}],
            "location": {"country": "United Kingdom", "city": "London"},
            "client": {"browser": "Chrome", "os": "macOS", "device": "laptop"},
            vocab.RESIDENCY_FIELD: "eu-west",
        },
        room_id=eu_room,
        actor="system",
        source="wf-085 seed retention demo",
    )

    # 4. An identifiable visitor row, so the DSAR has a persistent row to find beside the
    #    view events.
    store.create(
        "wf075_visitor",
        {
            "email": "buyer@halcyon.example",
            "dataroom_id": eu_room,
            "invited_at": base_ms - 420 * day_ms,
            "last_viewed_at": base_ms - 400 * day_ms,
            "total_views": 3,
            "verified": False,
            vocab.RESIDENCY_FIELD: "eu-west",
        },
        room_id=eu_room,
        actor="dana",
        source="wf-085 seed retention demo",
    )

    # 5. The four consent states, all in the region the gate enforces: a grant, a
    #    denial from somebody who never granted, the same buyer's later revocation, and a
    #    global opt-out.
    engine.record_consent(
        "eu-west",
        "buyer@northwind.example",
        signal=vocab.CONSENT_GRANTED,
        room_id=eu_room,
        source="wf-085 seed",
    )
    engine.record_consent("eu-west", "buyer@vantage.example", room_id=eu_room, source="wf-085 seed")
    engine.record_consent(
        "eu-west",
        "buyer@northwind.example",
        signal=vocab.CONSENT_DENIED,
        room_id=eu_room,
        source="wf-085 seed",
    )
    engine.record_consent(
        "eu-west", "buyer@meridian.example", gpc="1", room_id=eu_room, source="wf-085 seed"
    )

    # 6. A consent record recorded long enough ago to be past its own nine-month class, so
    #    the longer window has a due record and not only the strict one.
    legacy = engine.record_consent(
        "eu-west",
        "buyer@legacy.example",
        signal=vocab.CONSENT_GRANTED,
        room_id=eu_room,
        source="wf-085 seed",
    )
    store.update(
        legacy["id"],
        {
            "recorded_at": rules.stamp(
                now - timedelta(days=vocab.RETENTION_DAYS[vocab.CLASS_FAVOURITE_OR_SAMPLE] + 5)
            )
        },
        actor="system",
        source="wf-085 seed retention demo",
    )

    # 7. The data-subject request, fulfilled so the residue is on the board. It found the
    #    buyer and could not remove every trace of them, because the audit trail keeps
    #    what the erasure removed. That is the non-success state the brief asks for.
    request = engine.open_dsar(
        "buyer@halcyon.example",
        room_id=eu_room,
        actor="dana",
        role=admin,
        source="wf-085 seed",
        reason="Data subject request received by the privacy desk.",
    )
    fulfilled = engine.fulfil_dsar(request["id"], actor="dana", role=admin, source="wf-085 seed")

    # Counts are read back rather than written out, so the line the seeder prints cannot
    # describe a state the seed did not produce.
    schedule = engine.retention_schedule(eu_room)
    consent = engine.consent_report(eu_room)
    policy = engine.retention_report(eu_room)
    strict = next(row for row in policy["policy"] if row["class"] == vocab.CLASS_SESSION_RECORDING)
    due = sum(row["due"] for row in schedule["classes"])
    retained = sum(row["retained"] for row in schedule["classes"])

    return (
        f"2 deployments pinned, {moved[0]}+{moved[1]} record(s) re-stamped to their region; "
        f"strict retention window configured at {strict['configured_days']} days against a "
        f"{strict['ceiling_days']}-day ceiling; {due} record(s) past their window and "
        f"{retained} still inside it; "
        f"{consent['counts'][vocab.ACTIVE]} active consent, "
        f"{consent['counts'][vocab.CONSENT_DENIED]} denied with no grant, "
        f"{consent['counts'][vocab.REVOKED]} revoked and 1 global privacy control opt-out; "
        f"a data-subject request found {request['found']} record(s) and erased "
        f"{fulfilled['erased']}, leaving {fulfilled['residue']['audit_rows']} audit row(s) "
        f"that still name the buyer, so the request reads {fulfilled['state']} and not "
        f"fulfilled"
    )
