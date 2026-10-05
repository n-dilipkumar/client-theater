"""WF-083: record buyer sessions behind a consent gate.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-083.md``, quoted in full in issue 191.
The rules live in :mod:`dsr.security_governance.session_consent_rules` and are not restated
here. This module is the three things a feature contributes and the three things it must
never contribute.

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
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The four controls this workflow ships
-------------------------------------

The specification names them, and each one is a route below.

**Two-axis consent.** ``window.clarity('consentv2', {ad_Storage, analytics_Storage})``, with
``granted`` or ``denied`` per axis, and a replay recorded only when both are granted.

**Masking before upload.** "Is masked data uploaded to Clarity? No." So the frame is masked
before the write, and a frame that still carries content is refused.

**IP exclusion at ingest.** "No sessions from visitors on the list are recorded." So a
blocked visitor produces no recording row at all, and the response carries the vendor's own
console message.

**Two retention windows and a coarse delete.** Thirty days, or nine months for a favourite,
and deletion at project granularity because "you can't delete or download specific
recordings".

The wording this workflow is not allowed to use
-----------------------------------------------

**No authentication path.** "Microsoft Clarity doesn't support authentication via your
company's AAD instance." So no route offers SSO, and every response that reports a role
carries :data:`~dsr.security_governance.session_consent.AUTHENTICATION`.

**No compliance claim.** Nothing here returns "compliant", and no route serves a
certification. The enforcement date is a vendor deadline and is served as configuration.

The status codes here are the product's own
-------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses: 400 for a value or a bound this workflow will
not accept, 403 for a blocking change from a role below the administrator tier, 404 for a
recording or a link that does not exist. Reads of an empty store answer 200 with empty
states, because a board that 500s on a fresh room is a broken feature.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.permissions import INSTANCE_ADMIN, ROLE_LABELS, ROLES
from dsr.security_governance import (
    session_consent as vocab,
    session_consent_inferences as inferences,
    session_consent_rules as rules,
)
from dsr.security_governance.session_consent_engine import SessionConsentEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-083-record-buyer-sessions-behind-a-consent-gate",
    "ticket": "WF-083",
    "name": "Record buyer sessions behind a consent gate",
    "description": (
        "Record a buyer's session only after both consent axes are granted, mask "
        "confidential content before the write, exclude internal IPv4 ranges at ingest so "
        "a blocked visitor produces no record at all, and age recordings out on the 30-day "
        "window or the nine-month favourite window."
    ),
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-083", tags=["WF-083"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf083_http.py`` asserts every source this router can
    record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> SessionConsentEngine:
    """A :class:`SessionConsentEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store, a clock and a role tier, so
    building it here leaves every seam overridable in a test instead of hanging a
    long-lived object off ``app.state``, which is a shared file this feature may not edit.

    This is also the one place in the workflow that reads the repository's role surface.
    ``dsr/security_governance`` depends on nothing inside ``dsr`` but the store and itself,
    so the tier arrives here and is handed to the engine. One import site, one role
    vocabulary, and no second set of role names anywhere in the product.
    """

    return SessionConsentEngine(store, administrator=INSTANCE_ADMIN)


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
# All three types are declared in dsr.security_governance.session_consent_rules and raised
# by nothing else in the product. That is what makes it safe to map them here: the host
# refuses a second feature registering a handler for the same type, and a handler for
# ValueError would intercept that exception across the whole product.


def _refused(request: Request, exc: rules.SessionConsentRefusal) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_session_consent_request",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _not_found(request: Request, exc: rules.SessionConsentNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})


def _administrator_required(
    request: Request, exc: rules.SessionConsentAdministratorRequired
) -> JSONResponse:
    """403 naming the role presented and the role that would have worked.

    A 403 with no remediation is a support ticket, so the body carries both roles.
    """

    return JSONResponse(status_code=403, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    rules.SessionConsentRefusal: _refused,
    rules.SessionConsentNotFound: _not_found,
    rules.SessionConsentAdministratorRequired: _administrator_required,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only.

    A read records no audit row: resolving what the room holds changes nothing, and a route
    that logged every read would fill this product's own guarantee with entries describing
    no change.
    """

    return engine.summary(project_id=project_id, room_id=room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the board cannot drift from the rules
    that compute it: the two axes, the three masking modes, the blocklist limits, the two
    retention windows and the two link kinds all come from the same table the engine reads.
    """

    return {
        "axes": list(vocab.CONSENT_AXES),
        "axis_keys": {axis: list(keys) for axis, keys in rules.AXIS_KEYS.items()},
        "consent_values": list(vocab.CONSENT_VALUES),
        "consent_call": vocab.CONSENT_CALL,
        "legacy_consent_call": vocab.LEGACY_CONSENT_CALL,
        "signal": vocab.CONSENT_SIGNAL,
        "consent_gate_field": vocab.CONSENT_GATE,
        "identity_kinds": list(vocab.IDENTITY_KINDS),
        "destroyed_on_denial": vocab.DESTROYED_ON_DENIAL,
        "masking_modes": list(vocab.MASKING_MODES),
        "default_masking_mode": vocab.DEFAULT_MASKING_MODE,
        "mask": vocab.MASK,
        "recording_states": list(vocab.RECORDING_STATES),
        "enforced_regions": list(vocab.CONSENT_ENFORCED_REGIONS),
        "enforcement_start": vocab.CONSENT_ENFORCEMENT_START,
        "enforcement_is_date_scoped": vocab.ENFORCEMENT_IS_DATE_SCOPED,
        "geography_source": vocab.GEOGRAPHY_SOURCE,
        "ordinary_retention_days": vocab.ORDINARY_RETENTION_DAYS,
        "favourite_retention_days": vocab.FAVOURITE_RETENTION_DAYS,
        "max_labels_per_recording": vocab.MAX_LABELS_PER_RECORDING,
        "link_kinds": list(vocab.LINK_KINDS),
        "default_guest_link_days": vocab.DEFAULT_GUEST_LINK_DAYS,
        "segment_dimensions": list(vocab.SEGMENT_DIMENSIONS),
        "blocked_signal": vocab.BLOCKED_SIGNAL,
        "blocklist_propagation_minutes": vocab.IP_BLOCKLIST_PROPAGATION_MINUTES,
        "ip_blocking_role": vocab.IP_BLOCKING_ROLE,
        "ipv4_only": vocab.IPV4_ONLY,
        "authentication": vocab.AUTHENTICATION,
        "max_sessions_per_project_per_day": vocab.MAX_SESSIONS_PER_PROJECT_PER_DAY,
        "collections": list(vocab.ALL_COLLECTIONS),
        "admin_role": INSTANCE_ADMIN,
        "roles": [dict(entry) for entry in ROLE_VOCABULARY],
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification asks an implementer to "Decide what the room masks by default and
    record it". This route is that record, served rather than buried in a docstring so a
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
# The project and its gate
# --------------------------------------------------------------------------- #


@router.get("/project")
def read_project(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The recording project: its masking mode, its consent gate and its enforcement.

    Answers 200 with a null project on a fresh room rather than a 404, so a board for a
    project nobody has created yet renders an empty state instead of an error.
    """

    return engine.project(project_id) or {}


@router.post("/project", status_code=201)
def create_project(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Create a recording project with the consent gate on and masking at its default.

    Masking defaults to total suppression because the evidence documents that default, and
    the gate defaults on because a project that records without one is the shape the
    specification forbids.
    """

    return engine.create_project(
        name=payload.get("name"),
        masking_mode=payload.get("masking_mode"),
        consent_gate=payload.get(vocab.CONSENT_GATE, True),
        enforced_regions=payload.get("enforced_regions"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/project"),
    )


@router.put("/project/masking")
def set_masking(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Change the masking mode, which decides what leaves the room.

    Administrator-only. It is a masking configuration and not a consent decision, so it does
    not need the consent gate itself to be on.
    """

    return engine.set_masking_mode(
        project_id or "",
        payload.get("masking_mode"),
        selectors=payload.get("masking_selectors"),
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("PUT", "/project/masking"),
    )


# --------------------------------------------------------------------------- #
# IP exclusion
# --------------------------------------------------------------------------- #


@router.get("/ip-blocks")
def list_ip_blocks(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Every blocked range on the project."""

    return {"blocks": engine.blocklist(project_id=project_id, room_id=room_id)}


@router.post("/ip-blocks", status_code=201)
def add_ip_block(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Add an IPv4 address or range to the blocklist.

    Administrator-only, because the evidence says "To set up IP exclusion, you need to be an
    administrator for your project." An IPv6 range is a 400 that names the reason, rather
    than a silent no-op an operator would read as a working block.
    """

    return engine.block_ip(
        project_id or "",
        payload.get("cidr") or payload.get("ip"),
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/ip-blocks"),
    )


@router.delete("/ip-blocks/{range_id}")
def remove_ip_block(
    range_id: str,
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Remove one range from the blocklist. Administrator-only, like adding one."""

    return engine.unblock_ip(
        range_id,
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("DELETE", "/ip-blocks/{range_id}"),
    )


# --------------------------------------------------------------------------- #
# Consent and ingest
# --------------------------------------------------------------------------- #


@router.post("/consent", status_code=201)
def record_consent(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Record one ``consentv2`` call and act on what it decided.

    Four answers, and they are different facts:

    * a **signal** says a prompt fired, and stores no decision;
    * a **grant on both axes** stores a persistent identity;
    * a **grant on one axis** is recorded as a partial grant, because the axes are
      independent, but it ends the session: the cookie cannot be scoped to one axis;
    * a **denial** is destructive. It hard-deletes the visitor's stored sessions and
      returns the vendor's own revoke call.

    The response carries the identity kind and whether cookies persist, because "denied"
    alone does not tell a reader that the visitor is now anonymous.
    """

    return engine.record_consent(
        project_id,
        payload,
        visitor=payload.get("visitor_id"),
        page_view=payload.get("page_view_id"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/consent"),
    )


@router.post("/ingest", status_code=201)
def ingest_visit(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Decide what happens to one visit, then act on that decision.

    The checks run in the order the evidence gives them: the blocklist first, so a blocked
    visitor produces no recording row at all; then consent, where a replay needs both axes;
    then masking, before the write. Every answer carries ``recorded`` and the ``state``, and
    a refusal names its reason, because a visit that was not recorded for no stated reason
    is indistinguishable from a bug.
    """

    call = rules.parse_consent_call(payload.get("consent_call") or {})
    return engine.ingest_visit(
        project_id,
        visitor=payload.get("visitor_id"),
        page_view=payload.get("page_view_id"),
        ip_address=payload.get("ip_address"),
        region=payload.get("region"),
        axes=call["axes"],
        frame=payload.get("frame") or {},
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/ingest"),
    )


@router.get("/visits")
def list_visits(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Every visit the project has seen, including the ones it refused to record.

    The blocked visits are in this list on purpose: they hold no session, only the matched
    range and the vendor's console message, and they are the only trace that an internal
    viewer was excluded.
    """

    return {"visits": engine.visits(project_id=project_id, room_id=room_id)}


# --------------------------------------------------------------------------- #
# Recordings
# --------------------------------------------------------------------------- #


@router.get("/recordings")
def list_recordings(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    dimension: str | None = Query(None),
    value: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The recordings list, optionally filtered to a segment.

    An unknown dimension is a 400 rather than an unfiltered list, because a filter that
    quietly returned everything would be read as a filter that matched everything.
    """

    return {
        "recordings": engine.recordings(
            project_id=project_id, room_id=room_id, dimension=dimension, value=value
        )
    }


@router.get("/recordings/{recording_id}")
def read_recording(recording_id: str, engine: SessionConsentEngine = EngineDep) -> dict[str, Any]:
    """One recording, including its masked frame, its labels and its retention window."""

    return engine.recording(recording_id)


@router.post("/recordings/{recording_id}/favourite")
def mark_favourite(
    recording_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Mark a recording a favourite, which moves it onto the nine-month window.

    The evidence extends retention to "Favorite recordings", so the flag is a retention
    control rather than a bookmark, and the response reports both windows.
    """

    return engine.set_favourite(
        recording_id,
        payload.get("favourite", True),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/recordings/{recording_id}/favourite"),
    )


@router.post("/recordings/{recording_id}/labels", status_code=201)
def add_labels(
    recording_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Add labels to a recording. The sixth is a 400 naming the label and the cap."""

    return engine.label_recording(
        recording_id,
        payload.get("labels"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/recordings/{recording_id}/labels"),
    )


@router.delete("/recordings/{recording_id}")
def delete_recording(recording_id: str, engine: SessionConsentEngine = EngineDep) -> JSONResponse:
    """Refuse a single-recording delete, and write nothing.

    The refusal is the feature rather than a gap in it. The evidence says "you can't delete
    or download specific recordings" and "You need to delete the entire project to delete
    user's data", so the response names both and the supported alternative. A 200 here
    would be a lie about a product limit that is the reason a buyer-facing workflow needs
    this control.
    """

    refusal = engine.delete_recording(recording_id)
    return JSONResponse(status_code=409, content=refusal)


@router.post("/project/purge")
def purge_project(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Delete every recording, visit, label and link on the project.

    Administrator-only, and a hard delete, because this is the only delete the evidence
    supports and a soft delete would leave the session rows behind.
    """

    return engine.purge_project(
        project_id,
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/project/purge"),
    )


# --------------------------------------------------------------------------- #
# Share links
# --------------------------------------------------------------------------- #


@router.get("/share-links")
def list_share_links(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Every share link, each marked live or expired."""

    return {"links": engine.share_links(project_id=project_id, room_id=room_id)}


@router.post("/share-links", status_code=201)
def create_share_link(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Share one recording.

    A guest link gets a window and a team link never expires, because the evidence says
    "guest links expire, team links don't". A window supplied for a team link is a 400
    rather than a discarded value, so the record cannot say one thing and behave like
    another.
    """

    return engine.create_share_link(
        project_id,
        str(payload.get("recording_id") or ""),
        kind=payload.get("kind"),
        expires_in_days=payload.get("expires_in_days"),
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/share-links"),
    )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


@router.get("/retention")
def retention_schedule(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Every recording with the window that applies to it and whether it has passed.

    Both windows appear in the response, because the favourite window outliving the
    ordinary one is the fact a reviewer needs to see.
    """

    return engine.retention_schedule(project_id=project_id, room_id=room_id)


@router.post("/retention/run")
def run_retention(
    room_id: str | None = Query(None),
    project_id: str | None = Query(None),
    actor: str | None = Query(None),
    role: str | None = Query(None),
    engine: SessionConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Age out every recording whose window has passed. Administrator-only, and reported.

    The response says how many rows it removed rather than returning a boolean, because a
    sweep that removed nothing and a sweep that was never run should not look the same.
    """

    return engine.run_retention(
        project_id,
        room_id=room_id,
        actor=actor,
        role=role,
        source=_source("POST", "/retention/run"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification's user flow describes, not just the happy path.

    Every row is produced by calling the real :class:`SessionConsentEngine`, so the demo
    cannot show a state, a count or an audit row the HTTP routes would not produce.

    The states seeded, and why each is here:

    * a project with the gate on and the default masking, because that is the documented
      starting point;
    * an **internal IPv4 range** blocked, so the blocklist and the ingest refusal are both
      rendered rather than described;
    * a **grant on both axes** and a recording, so the recordings list is not empty;
    * a **partial grant** (analytics allowed, ad storage refused), because the issue asks
      for all four combinations and a partial grant is the one a reviewer most needs to see
      named;
    * a **denial** and the **teardown** it causes, because the evidence calls denial
      destructive and a demo that only shows a skip hides the part that matters;
    * a **blocked visitor**, whose visit leaves no recording row at all, so the refusal is
      visible while the absence is also visible;
    * a recording labelled with the maximum of five labels, so the cap is one away from
      being refused on the page;
    * a **guest share link** with a window and a **team share link** without one.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf083.py``: the seeder prints it to a Windows console, and one RIGHTWARDS
    ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    admin = INSTANCE_ADMIN
    engine = SessionConsentEngine(store, administrator=admin, now=lambda: now)
    room_id = room_ids[0][0]

    project = engine.create_project(
        name="Q4 enterprise rooms",
        room_id=room_id,
        actor="dana",
        source="wf-083 seed",
    )
    engine.block_ip(
        project["id"], "10.0.0.0/8", room_id=room_id, actor="dana", role=admin, source="wf-083 seed"
    )

    # A grant on both axes, then a recorded visit from outside the blocklist.
    engine.record_consent(
        project["id"],
        {
            "api": vocab.CONSENT_CALL,
            "ad_Storage": vocab.GRANTED,
            "analytics_Storage": vocab.GRANTED,
        },
        visitor="visitor_northwind",
        page_view="pv_seed_1",
        room_id=room_id,
        source="wf-083 seed",
    )
    recorded = engine.ingest_visit(
        project["id"],
        visitor="visitor_northwind",
        page_view="pv_seed_1",
        ip_address="203.0.113.24",
        region="EEA",
        axes={vocab.AD_STORAGE: vocab.GRANTED, vocab.ANALYTICS_STORAGE: vocab.GRANTED},
        frame={"page_path": "/pricing", "title": "FY27 pricing", "sections": 4},
        room_id=room_id,
        source="wf-083 seed",
    )
    recording_id = str((recorded.get("recording") or {}).get("id") or "")

    # Five labels, so the sixth is one away on the page.
    if recording_id:
        engine.label_recording(
            recording_id,
            ["enterprise", "pricing", "q4", "renewal", "board"],
            room_id=room_id,
            actor="dana",
            source="wf-083 seed",
        )

    # A partial grant: analytics allowed, ad storage refused. The axes are independent and
    # the session still ends, which is the derivation the issue asks a reviewer to see.
    engine.record_consent(
        project["id"],
        {"api": vocab.CONSENT_CALL, "ad_Storage": vocab.DENIED, "analytics_Storage": vocab.GRANTED},
        visitor="visitor_vantage",
        page_view="pv_seed_2",
        room_id=room_id,
        source="wf-083 seed",
    )

    # A denial, which tears the stored session down rather than only skipping it.
    engine.record_consent(
        project["id"],
        {"api": vocab.CONSENT_CALL, "ad_Storage": vocab.DENIED, "analytics_Storage": vocab.DENIED},
        visitor="visitor_meridian",
        page_view="pv_seed_3",
        room_id=room_id,
        source="wf-083 seed",
    )

    # A blocked visitor, from inside the range, producing no recording row at all.
    engine.ingest_visit(
        project["id"],
        visitor="visitor_internal",
        page_view="pv_seed_4",
        ip_address="10.4.2.9",
        region="EEA",
        axes={vocab.AD_STORAGE: vocab.GRANTED, vocab.ANALYTICS_STORAGE: vocab.GRANTED},
        frame={"page_path": "/pricing", "title": "Internal review"},
        room_id=room_id,
        source="wf-083 seed",
    )

    if recording_id:
        engine.create_share_link(
            project["id"],
            recording_id,
            kind=vocab.GUEST,
            expires_in_days=vocab.DEFAULT_GUEST_LINK_DAYS,
            room_id=room_id,
            actor="dana",
            source="wf-083 seed",
        )
        engine.create_share_link(
            project["id"],
            recording_id,
            kind=vocab.TEAM,
            room_id=room_id,
            actor="dana",
            source="wf-083 seed",
        )

    return (
        "1 recording project, 1 blocked IPv4 range, 1 recorded session, 5 labels, "
        "1 partial grant, 1 denial with teardown, 1 blocked visitor with no recording, "
        "1 guest share link and 1 team share link"
    )
