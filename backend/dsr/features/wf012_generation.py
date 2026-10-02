"""WF-012: generate a personalised room programmatically from a template.

Ported from
``feature/WF-012-generate-a-personalised-room-programmatically-from-a-template``.

The domain logic is reused unchanged from :mod:`dsr.generation`. This module is
only the three things the branch had to take out of shared files: the HTTP
surface, the mapping from domain errors to responses, and the demo data.

What the port changed, and why
------------------------------

**The routes became a router.** The branch registered nine handlers with
``@app.<verb>`` on the one shared FastAPI app in ``dsr/api.py``. That single edit
is what made the twelve workflow branches mutually unmergeable. Here they are
``@router.<verb>`` on an ``APIRouter`` the plugin host mounts by discovery.

**The prefix is ``/api/wf-012``.** The branch served ``/api/templates`` and
``/api/generations`` at the top level, which are exactly the paths any other
feature would also reach for. The contract asks for a ticket-derived prefix, and
the branch was never merged, so no client depends on the old paths.

**``source=`` comes from the route.** The branch called
``generator.generate(request, actor=actor)`` and let the domain default
``source="POST /api/generations"`` land in the audit row - naming a path the app
did not serve. Every write route below passes the route that actually served it,
built from ``router.prefix`` so it cannot drift when the prefix changes, and
``source`` is now a *required* keyword on the four domain methods that write, so
this cannot silently regress.

**The generator is built per request from ``StoreDep``.** The branch created one
in the lifespan and hung it on ``app.state.generator``, which is another edit to
``api.py``. :class:`~dsr.generation.TemplateGenerator` holds nothing beyond the
store handle, so constructing it per request is equivalent and keeps this module
importable on its own.

**The error mapping is exported.** FastAPI only accepts exception handlers on
the app object, so the three ``@app.exception_handler`` blocks became
``EXCEPTION_HANDLERS`` for the host to attach. All three types are defined in
``dsr/generation.py`` for this workflow; none is a builtin, so a global
registration cannot intercept an unrelated error anywhere in the product.
``RecordNotFound`` is deliberately *not* claimed here: the core app already maps
it to 404, and two handlers for one type is a collision the host refuses.

**Demo data is a ``seed(db, context)`` export.** The branch rewrote
``backend/seed.py`` to add a template and two generated rooms. The contract
forbids that, so the same rows are produced here instead - and produced by
running the real generator over the real template, so the demo cannot drift from
the code that serves requests.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.generation import (
    GenerationConflict,
    GenerationError,
    GenerationRequest,
    TemplateGenerator,
    UnknownTemplate,
    parse_request,
)
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-012-room-generation",
    "ticket": "WF-012",
    "name": "Generate a personalised room programmatically from a template",
    "description": (
        "Declare a template shell of {{ variable }} blocks, preview a generation without "
        "writing, then produce a personalised, audited room that is a draft unless "
        "publication was asked for."
    ),
    "nav": [{"id": "room-generation", "label": "Template generator"}],
}

router = APIRouter(prefix="/api/wf-012", tags=["wf-012"])


def get_generator(store: RecordStore = StoreDep) -> TemplateGenerator:
    """A :class:`TemplateGenerator` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the generator is a thin
    wrapper with no state of its own, and ``app.state`` is where the branch had
    to put it, which meant editing the shared app.
    """
    return TemplateGenerator(store)


GeneratorDep = Depends(get_generator)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _generation_error(request: Request, exc: GenerationError) -> JSONResponse:
    """A malformed request. 400, because the caller can fix it and retry."""
    return JSONResponse(status_code=400, content={"error": "generation_error", "detail": str(exc)})


def _unknown_template(request: Request, exc: UnknownTemplate) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_template", "detail": str(exc), "id": str(exc)},
    )


def _generation_conflict(request: Request, exc: GenerationConflict) -> JSONResponse:
    """Well-formed, but it collides with current state. 409."""
    return JSONResponse(
        status_code=409, content={"error": "generation_conflict", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {
    GenerationError: _generation_error,
    UnknownTemplate: _unknown_template,
    GenerationConflict: _generation_conflict,
}


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@router.get("/templates")
def list_templates(
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """List room templates, optionally filtered on any JSON path."""
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    records = generator.templates(where=filters, limit=limit)
    return {"collection": "template", "count": len(records), "records": records}


@router.post("/templates", status_code=201)
def declare_template(
    response: Response,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """Declare a template shell: blocks plus the variables they reference.

    Supply ``template_id`` to update an existing template in place (200); omit it
    to create a new one (201). The declared ``variables`` list drives the
    generator UI and is advisory, never a validation gate.
    """
    record = generator.declare(payload, actor=actor, source=f"POST {router.prefix}/templates")
    if payload.get("template_id"):
        response.status_code = 200
    return record


# Declared before "/templates/{template_id}" so the literal path can never be
# read as a template id, whatever Starlette does with a method mismatch.
@router.post("/templates/preview")
def preview_generation(
    payload: dict[str, Any] = Body(default_factory=dict),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """Render a request without writing anything.

    The operator's safety net: the buyer-visible content, the variables that
    were left unresolved, the substitutions the template never referenced, and
    the expiry deadline the room *would* get if published now. Nothing is
    written, so nothing is audited.
    """
    return generator.preview(payload)


@router.get("/templates/{template_id}")
def read_template(template_id: str, generator: TemplateGenerator = GeneratorDep) -> dict[str, Any]:
    return generator.template(template_id)


# --------------------------------------------------------------------------- #
# Generations
# --------------------------------------------------------------------------- #


@router.get("/generations")
def list_generations(
    where: str | None = Query(default=None, description='JSON object or "k=v,k2=v2"'),
    limit: int = Query(default=100, ge=1, le=1000),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """List rooms generated from a template.

    ``where`` resolves dotted JSON paths through the dynamic index, so
    ``{"external_id":"sf-op-0001"}`` or ``{"published":true}`` work without this
    endpoint knowing either field exists.
    """
    try:
        filters = parse_where(where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rooms = generator.generated(where=filters, limit=limit)
    return {"collection": "room", "generated": True, "count": len(rooms), "rooms": rooms}


def _parse_batch_item(item: Any, index: int) -> GenerationRequest:
    """Parse one batch entry, naming the index in any error.

    A batch is all-or-nothing, so ``items[7]: template_id is required`` tells the
    caller exactly which row to fix instead of making them count rows.
    """
    if not isinstance(item, dict):
        raise GenerationError(f"items[{index}]: each item must be an object")
    try:
        return parse_request(item)
    except GenerationError as exc:
        raise GenerationError(f"items[{index}]: {exc}") from exc


@router.post("/generations", status_code=201)
def create_generation(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """Generate one personalised room from a template.

    ``published`` defaults to false: a generation is a draft unless the caller
    explicitly asks otherwise, and an ``expiry`` setting on a draft is retained
    rather than started.
    """
    request = parse_request(payload)
    return generator.generate(request, actor=actor, source=f"POST {router.prefix}/generations")


@router.post("/generations/bulk", status_code=201)
def create_generations(
    items: list[dict[str, Any]] = Body(default_factory=list),
    actor: str | None = Query(default=None),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """Generate a batch of rooms: one transaction, one audit row, all or nothing.

    Capped at ``MAX_BATCH`` items. Every item is rendered and every
    caller-supplied ``external_id`` checked before the single write, so a bad
    item cannot leave the good ones committed.
    """
    if isinstance(items, dict) or not isinstance(items, list):
        raise HTTPException(status_code=400, detail="a batch body must be an array of requests")
    requests = [_parse_batch_item(item, index) for index, item in enumerate(items)]
    return generator.generate_many(
        requests, actor=actor, source=f"POST {router.prefix}/generations/bulk"
    )


@router.get("/generations/{room_id}")
def read_generation(room_id: str, generator: TemplateGenerator = GeneratorDep) -> dict[str, Any]:
    """Read a generated room.

    The record envelope is exactly what the store holds; ``generation`` holds
    only the state derived from it on read (``status``, ``expires_at``), so a
    stored fact and a computed one are never confused.
    """
    return generator.get_generated(room_id)


@router.post("/generations/{room_id}/publish")
def publish_generation(
    room_id: str,
    actor: str | None = Query(default=None),
    generator: TemplateGenerator = GeneratorDep,
) -> dict[str, Any]:
    """Publish a generated room, which is when its expiry clock starts."""
    return generator.publish(
        room_id, actor=actor, source=f"POST {router.prefix}/generations/{room_id}/publish"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

# A template is a shell: a named set of blocks carrying {{ variable }} references
# plus a declaration of the variables it expects. It is never itself a room and
# is never published; rooms are generated from it. `variables` is advisory (it
# drives the generator's form) and the blocks carry the references substitutions
# fill in.
EVALUATION_TEMPLATE = {
    "name": "Enterprise Evaluation",
    "description": (
        "Standard enterprise evaluation room: a cover block, a pinned capacity line, "
        "and a repeating quote."
    ),
    "variables": [
        {"key": "hello_world", "label": "Account name", "type": "text", "required": True},
        {"key": "region", "label": "Region", "type": "text"},
        {"key": "reference", "label": "Opportunity reference", "type": "text"},
        {"key": "line_items", "label": "Quote lines", "type": "repeat"},
    ],
    "blocks": [
        {"id": "hero", "kind": "heading", "text": "Prepared for {{hello_world}}"},
        {
            "id": "intro",
            "kind": "text",
            "text": "{{hello_world}} is evaluating the platform for the {{region}} region.",
        },
        {
            "id": "capacity",
            "kind": "text",
            "text": "Licensed seats: {{seats}}",
            # A block-level pin. Per the source, this overwrites the page-level
            # value for this block only.
            "substitutions": {"seats": "to be confirmed"},
        },
        {
            "id": "quote",
            "kind": "line_items",
            "repeat": "line_items",
            "item": "{{item.description}} x{{item.quantity}} @ {{item.unit_price}}",
        },
        {"id": "footer", "kind": "text", "text": "Confidential. Reference {{reference}}."},
    ],
}

DEMO_LINE_ITEMS = [
    {"description": "Platform licence", "quantity": 40, "unit_price": "1200.00"},
    {"description": "Onboarding", "quantity": 1, "unit_price": "4500.00"},
]

#: ``(account, region, published, days_ago_published)``. Publication is opt-in and
#: the expiry count starts at publication, so the three shapes below are the three
#: states a generated room can read as: a draft that has kept its expiry setting,
#: a live room with a deadline ahead of it, and a room that has run past its
#: deadline and now reads as ``declined``.
DEMO_GENERATIONS = [
    ("Northwind Traders", "EMEA", False, None),
    ("Contoso Health", "APAC", True, 3),
    ("Fabrikam Logistics", "AMER", True, 45),
]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one template and three rooms generated from it.

    The rooms are produced by running the real
    :class:`~dsr.generation.TemplateGenerator` over the real template rather
    than by hand-writing the rendered content, so the demo cannot show a shape
    the workflow would not actually produce. Each write is audited, exactly as
    the HTTP routes would be.
    """
    generator = TemplateGenerator(RecordStore(db))
    template = generator.declare(EVALUATION_TEMPLATE, actor="dana", source="seed")
    template_id = template["id"]

    for index, (account, region, published, days_ago) in enumerate(DEMO_GENERATIONS):
        request = parse_request(
            {
                "template_id": template_id,
                "name": f"{account} — Enterprise Evaluation",
                "account": account,
                "owner_id": "dana",
                "tags": ["enterprise", "seeded"],
                "external_id": f"seed-op-{index + 1}",
                "expiry": {"days": 30},
                "published": published,
                "metadata": {"crm_system": "salesforce", "seeded": True},
                "substitutions": {
                    "hello_world": account,
                    "region": region,
                    "reference": f"NSQ-{8800 + index}",
                    "line_items": DEMO_LINE_ITEMS,
                },
            }
        )
        room = generator.generate(request, actor="dana", source="seed")

        # `published_at` is normally stamped at the instant of publication, so a
        # room that has run past its deadline cannot be produced by the API in
        # one call. Backdate this one so the derived `declined` state is visible
        # in the demo without a background job faking it.
        if days_ago is not None:
            backdated = (context["now"] - timedelta(days=days_ago)).isoformat(
                timespec="milliseconds"
            )
            db.update(room["id"], {"published_at": backdated}, actor="dana", source="seed")

    return f"1 template, {len(DEMO_GENERATIONS)} generated rooms (draft, published, declined)"
