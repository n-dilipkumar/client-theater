"""WF-073: apply confidential view and block screenshot shortcuts.

A build from a researched specification, not a port. There was no source branch. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-073.md``, quoted in
full in issue 176. The rules live in :mod:`dsr.security_governance` and are not
restated here. This module is the three things a feature contributes and the three
things it must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only
  and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
  Twelve workflow branches each editing those files is why none of the original twelve
  merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The wording this workflow is not allowed to use
-----------------------------------------------

The specification says of itself: "Screenshot blocking is largely unenforceable from a
browser; treat as deterrence, and do not sell it as protection."

That sentence constrains the copy, not only the code. So this module exports
:data:`EXCEPTION_HANDLERS` and never calls a control a protection, the seed says
"deterrence" rather than "blocked", and every response carries ``effect:
"deterrent"`` beside the two controls. A test greps this file and the domain package
for the words the specification forbids in a claim, and fails if one appears in a
sentence that asserts protection.

The status codes here are the researched ones
---------------------------------------------

There are none, because the specification documents no errors for either flag. So
every status here follows the product's own conventions rather than inventing a
codebook: 400 for a setting this workflow will not accept, 404 for a link or a
baseline that does not exist, 422 for a capture attempt naming a shortcut outside the
derived list. The distinction between the last two matters and is recorded: an
unrecognised shortcut is a request this build cannot interpret, not a bad setting, so
it is 422 rather than 400. Guessing a vendor's code for a vendor behaviour nobody
documented would be worse than being plainly conventional.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import inferences, rules, vocabulary as vocab
from dsr.security_governance.engine import ConfidentialEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-073-apply-confidential-view-and-block-screenshot",
    "ticket": "WF-073",
    "name": "Apply confidential view and block screenshot shortcuts",
    "description": (
        "Keep two booleans on a link. Confidential view renders one narrow band of each "
        "page sharp and delivers the rest blurred, so a single screenshot cannot capture "
        "a whole page. Screenshot protection intercepts the capture and screen-recording "
        "shortcuts a page can intercept and records what the viewer's browser saw. Both "
        "are deterrence, not protection, and every response says so."
    ),
    "nav": [{"id": "wf-073-confidential-view", "label": "Confidential view"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-073", tags=["WF-073"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a
    domain function hardcoding a URL string, which leaves the audit log naming a route
    the app stopped serving. ``tests/test_wf073_http.py`` asserts every source this
    router can record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> ConfidentialEngine:
    """A :class:`ConfidentialEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and a clock, so building it here
    leaves both overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return ConfidentialEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.security_governance.rules and raised by nothing
# else in the product. That is what makes it safe to map them here: the host refuses a
# second feature registering a handler for the same type, and a handler for ValueError
# or PermissionError would intercept those exceptions across the whole product.


def _confidential_error(request: Request, exc: rules.ConfidentialError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={
            "error": "confidential_settings_invalid",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _link_not_found(request: Request, exc: rules.LinkNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": "No such governed link.",
            vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        },
    )


def _preset_not_found(request: Request, exc: rules.PresetNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such governance baseline."},
    )


def _unknown_shortcut(request: Request, exc: rules.UnknownShortcut) -> JSONResponse:
    """422, not 400.

    The distinction is the point. A setting this workflow will not accept is a bad
    request and belongs in the same class as every other one. A capture attempt naming a
    shortcut outside the derived list is not a bad setting: the keys are real, this
    build simply does not have a record for them, and the most likely cause is that a
    platform changed its bindings. That is an unprocessable request rather than a
    malformed one, so it gets its own code and its own message.

    Every response below it, and every one in this router, states what the control is
    worth, because the specification forbids selling it as protection and the cheapest
    way to keep that true is to make the caveat part of the data.
    """

    return JSONResponse(
        status_code=422,
        content={
            "error": "unknown_shortcut",
            "detail": str(exc),
            "known_shortcuts": [shortcut["name"] for shortcut in vocab.SHORTCUT_KEYS],
            vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
        },
    )


EXCEPTION_HANDLERS = {
    rules.ConfidentialError: _confidential_error,
    rules.LinkNotFound: _link_not_found,
    rules.PresetNotFound: _preset_not_found,
    rules.UnknownShortcut: _unknown_shortcut,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: ConfidentialEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""
    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the access-controls panel cannot
    drift from the rules that validate it: the two flags, their defaults, the CLI
    spelling, the capture shortcuts with a blockable flag on each, and the focus-band
    arithmetic all come from the same tables the validator uses.
    """

    return {
        "flags": list(vocab.FLAGS),
        "defaults": dict(vocab.DEFAULTS),
        "panel": {
            "title": vocab.PANEL_TITLE,
            "controls": [dict(control) for control in vocab.PANEL_CONTROLS],
        },
        "descriptions": dict(vocab.DESCRIPTIONS),
        "cli_flags": {control["field"]: control["cli_flag"] for control in vocab.PANEL_CONTROLS},
        "tri_state_values": ["on", "off", None],
        "render": {
            "chosen_shape": vocab.CHOSEN_RENDER_SHAPE,
            "shapes": list(vocab.RENDER_SHAPES),
            "band_fraction": vocab.BAND_FRACTION,
            "band_overlap": round(vocab.BAND_OVERLAP, 4),
            "small_page_pixels": vocab.SMALL_PAGE_PIXELS,
        },
        "shortcuts": [dict(shortcut) for shortcut in vocab.SHORTCUT_KEYS],
        "blockable_shortcuts": [shortcut["name"] for shortcut in rules.blockable_shortcuts()],
        "unblockable_shortcuts": [shortcut["name"] for shortcut in rules.unblockable_shortcuts()],
        "capture_actions": list(vocab.CAPTURE_ACTIONS),
        "attempt_states": list(vocab.ATTEMPT_STATES),
        "preset_covered_fields": list(vocab.PRESET_COVERED_FIELDS),
        "collections": list(vocab.ALL_COLLECTIONS),
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
        "not_protection": vocab.NOT_PROTECTION,
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification says an implementer "must derive it and record the derivation,
    not assume it". This route is that record, and it is served rather than buried in a
    docstring so a reviewer reads the decision instead of the code.
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
# Governance baselines
# --------------------------------------------------------------------------- #


@router.get("/presets")
def list_presets(engine: ConfidentialEngine = EngineDep) -> dict[str, Any]:
    """Every governance baseline a link can be seeded from."""
    rows = engine.presets()
    return {
        "count": len(rows),
        "presets": rows,
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


@router.post("/presets", status_code=201)
def create_preset(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Define a governance baseline. "Both flags are ``preset_id``-coverable, so they
    can be governance baselines." Only the two flags are interpreted; anything else in
    ``fields`` is stored and carried, because those fields belong to other workflows."""
    return engine.create_preset(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/presets")
    )


@router.get("/presets/{preset_id}")
def read_preset(preset_id: str, engine: ConfidentialEngine = EngineDep) -> dict[str, Any]:
    return engine.read_preset(preset_id)


# --------------------------------------------------------------------------- #
# The access-controls panel
# --------------------------------------------------------------------------- #


@router.get("/links")
def list_links(
    room_id: str | None = Query(None), engine: ConfidentialEngine = EngineDep
) -> dict[str, Any]:
    """Every governed link, and where each one's two controls stand."""
    rows = engine.links(room_id)
    return {
        "count": len(rows),
        "links": rows,
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


@router.post("/rooms/{room_id}/links", status_code=201)
def create_link(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Create a governed link. Mirrors ``POST /v1/links``.

    Both flags default to the documented ``false``, and a ``preset_id`` seeds them
    first so a governance baseline is what a link starts from rather than something
    the seller has to remember to apply.
    """
    return engine.create_link(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/links")
    )


@router.get("/links/{link_id}")
def read_link(link_id: str, engine: ConfidentialEngine = EngineDep) -> dict[str, Any]:
    """One link's two controls, with the effect they are worth."""
    return engine.read_link(link_id)


@router.patch("/links/{link_id}")
def update_link(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Toggle either flag in place. Mirrors ``PATCH /v1/links/{id}``.

    Tri-state, so this endpoint can both rotate a control and remove one: a field the
    body omits is left alone, ``true``/``false`` (or the CLI's ``on``/``off``) sets it,
    and an explicit ``null`` returns it to the documented default.

    The id and the room are untouched, which is the specification's "the URL and
    existing viewers are unaffected". A viewer already looking at the link sees the new
    setting on their next request, because both flags are read per request and nothing
    caches them.
    """
    return engine.update_link(
        link_id, payload, actor=actor, source=_source("PATCH", "/links/{link_id}")
    )


# --------------------------------------------------------------------------- #
# The buyer's side: a page against a viewport
# --------------------------------------------------------------------------- #


@router.post("/links/{link_id}/render")
def render_page(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Resolve which band of one page is sharp, for one viewer's viewport.

    The specification's third user-flow step: "Buyer scrolls the document; content only
    resolves inside the focus band, so a single screenshot cannot capture a whole page."

    It never refuses anyone. Confidential view is a rendering transformation and not a
    gate, and gating a link is WF-069's domain. It also never returns the page: it
    returns the geometry and the one sharp band, which is the property the
    specification states as the reason this works - "the full-resolution page never
    reaches the client for out-of-band regions".
    """
    return engine.render_page(link_id, payload)


@router.get("/links/{link_id}/render")
def read_render(
    link_id: str,
    page_height: int = Query(0),
    viewport_height: int = Query(0),
    viewport_top: int = Query(0),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """The same resolution, read-only and addressable.

    Exists so a viewer can put the current band in a URL and reload into it, which is
    what "a single change applies to every future view of that link" needs: the link's
    settings are a property of the link, so a viewer who reloads resolves against them
    without asking a seller for anything.
    """
    return engine.render_page(
        link_id,
        {
            "page_height": page_height,
            "viewport_height": viewport_height,
            "viewport_top": viewport_top,
        },
    )


# --------------------------------------------------------------------------- #
# The buyer's side: a reported capture attempt
# --------------------------------------------------------------------------- #


@router.post("/links/{link_id}/attempts", status_code=201)
def report_attempt(
    link_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Record one capture attempt the viewer's browser reported.

    This is a report and the response says so in ``state``. The specification's
    criticality note is that screenshot blocking "is largely unenforceable from a
    browser", so a row claiming a capture was prevented would be a claim the product
    cannot support.

    ``outcome`` is derived from the shortcut list rather than accepted from the caller.
    Letting the caller choose would let a browser that wanted to look compliant report
    itself as blocked, which would make the whole log a self-assessment.
    """
    return engine.report_attempt(
        link_id, payload, actor=actor, source=_source("POST", "/links/{link_id}/attempts")
    )


@router.get("/links/{link_id}/attempts")
def list_link_attempts(link_id: str, engine: ConfidentialEngine = EngineDep) -> dict[str, Any]:
    """Every reported attempt on one link, newest first."""
    rows = engine.attempts(link_id=link_id)
    return {
        "link_id": link_id,
        "count": len(rows),
        "attempts": rows,
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


@router.get("/attempts")
def list_attempts(
    room_id: str | None = Query(None),
    link_id: str | None = Query(None),
    engine: ConfidentialEngine = EngineDep,
) -> dict[str, Any]:
    """Every reported attempt, optionally narrowed to a room or a link."""
    rows = engine.attempts(room_id, link_id)
    return {
        "count": len(rows),
        "attempts": rows,
        vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the control states the specification says matter, not just the happy path.

    Every row is produced by calling the real :class:`ConfidentialEngine`, so the demo
    cannot show a shape, an audit row or an attempt the HTTP routes would not produce.
    ``source="seed"`` rather than a route string: no route served this, and claiming one
    would be the lie hard rule 4 of the brief exists to prevent.

    The states seeded, and why each is here:

    * a **governance baseline** with both controls on, so "they are inherited by every
      link created from a preset" is visible as a link rather than as a claim;
    * a link **seeded from that baseline**, both on;
    * a link with **confidential view on alone** and a link with **screenshot protection
      on alone**, because the evidence gives two independent booleans and a board holding
      only the both-on case would hide that;
    * a link with **neither**, so the documented default is on the board as a state;
    * a **reported capture attempt** on a link a page can intercept, which records as
      ``blocked``;
    * a **reported attempt on Print Screen**, which records as ``reported`` and not as
      blocked, because no web page can intercept it. That row is the workflow's own
      honesty rule made visible, and a demo that showed only blocked attempts would
      misrepresent the control it is demonstrating;
    * a link whose **confidential view was toggled off after being on**, so the in-place
      update is visible as a change rather than as a gap.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf073.py``: the seeder prints it to a Windows console, and one
    RIGHTWARDS ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    room_two = room_ids[1][0] if len(room_ids) > 1 else room_id

    engine = ConfidentialEngine(store, now=lambda: now)

    # 1. A governance baseline with both controls on.
    baseline = engine.create_preset(
        {
            "name": "Confidential deal baseline",
            "description": (
                "Both controls on for a deal where the document must not leave the room "
                "in one piece."
            ),
            "fields": {
                vocab.CONFIDENTIAL_VIEW: True,
                vocab.SCREENSHOT_PROTECTION: True,
            },
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # 2. Seeded from the baseline, so inheritance is visible.
    seeded = engine.create_link(
        room_id,
        {"title": "Northwind - seeded from the confidential baseline", "preset_id": baseline["id"]},
        actor="dana",
        source="seed",
    )

    # 3. One control at a time, because the evidence gives two independent booleans.
    engine.create_link(
        room_id,
        {
            "title": "Halcyon - confidential view only",
            vocab.CONFIDENTIAL_VIEW: True,
        },
        actor="dana",
        source="seed",
    )
    engine.create_link(
        room_two,
        {
            "title": "Vantage - screenshot protection only",
            vocab.SCREENSHOT_PROTECTION: True,
        },
        actor="dana",
        source="seed",
    )

    # 4. Neither, so the documented default is a state on the board.
    engine.create_link(
        room_two,
        {"title": "Kestrel - open, no controls"},
        actor="dana",
        source="seed",
    )

    # 5. A link whose confidential view was turned on and then toggled off in place, so
    #    "toggle either flag in place, and the URL is unaffected" is visible as a change
    #    rather than as a gap.
    rotated = engine.create_link(
        room_two,
        {"title": "Meridian - confidential view rotated off", vocab.CONFIDENTIAL_VIEW: True},
        actor="dana",
        source="seed",
    )
    engine.update_link(
        rotated["id"],
        {vocab.CONFIDENTIAL_VIEW: "off"},
        actor="dana",
        source="seed",
    )

    # 6. Two reported attempts: one a page can intercept, and one it cannot. Both are
    #    recorded through the engine, so both land in the audit log the way a real
    #    report would.
    engine.report_attempt(
        seeded["id"],
        {"keys": ["meta", "shift", "5"], "reported_by": "viewer"},
        actor="viewer",
        source="seed",
    )
    engine.report_attempt(
        seeded["id"],
        {"keys": ["print_screen"], "reported_by": "viewer"},
        actor="viewer",
        source="seed",
    )

    # Counts are read back from the engine rather than written out here, so the line
    # the seeder prints cannot describe a state the seed did not produce. The summary
    # is taken with no room filter, which is what makes it a count of everything this
    # seed created across both rooms rather than of one room.
    counts = engine.summary()
    attempts = engine.attempts()
    blocked = sum(1 for row in attempts if row.get("outcome") == vocab.ATTEMPT_BLOCKED)
    reported = sum(1 for row in attempts if row.get("outcome") == vocab.ATTEMPT_REPORTED)
    # The rotated link is read back rather than counted, so this line reports what
    # actually happened to it. If a future edit broke the toggle the number would
    # read 0 and the seed would be visibly wrong, rather than quietly claiming a
    # change it did not make.
    rotated_off = int(not engine.read_link(rotated["id"])["flags"][vocab.CONFIDENTIAL_VIEW])

    return (
        f"{counts['links']} governed links across the control states "
        f"({counts['both_controls']} with both controls, {counts['confidential_view']} with "
        f"confidential view, {counts['screenshot_protection']} with screenshot protection, "
        f"{counts['no_controls']} with neither, {rotated_off} rotated off in place); "
        f"{counts['presets']} governance baseline; "
        f"{len(attempts)} reported capture attempt(s), {blocked} a page can intercept and "
        f"{reported} a page cannot - deterrence, not protection"
    )
