"""WF-022: triage the pipeline with saved workspace views.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-022.md`` (source:
``docs/research/raw/analytics-intent.md`` section 7), which is the
specification. The domain logic lives in :mod:`dsr.triage`, so the researched
rules are testable without a framework; this module is the three things a feature
has to add to the product and nothing else:

* the route table, on a prefix this feature owns, mounted by discovery;
* the mapping from this workflow's own error type to a response, exported for the
  host to attach because FastAPI accepts exception handlers on the app only;
* the demo rows, as a ``seed(db, context)`` hook rather than an edit to the
  shared ``backend/seed.py``.

The researched workflow
-----------------------
"Open the Dock **Workspaces** dashboard. Click **Add view** and start from a
default view: **All Workspaces**, **My Workspaces**, **Active Pipeline**, **Deal
Desk**, or **Implementations**. **Clone** the view, then filter/sort by owner,
creation date, recent client activity, CRM stage, workspace type. Edit and
rearrange **columns**. Save as a **private view** (personal) or **public view**
(team); Dock remembers the views you had open."

The one rule in there that has to be got right is **Active Pipeline**: "Any
workspace that has a 'Sales' workspace type, **or** an opportunity or deal
connected from your CRM." Two independent arms, and the second is itself a
disjunction. Both are implemented, both are tested on their own, and
:mod:`dsr.triage.inferences` names the decision. A view that only found the Sales
workspaces would be a rule that does not fall through, which is the failure mode
this programme's build brief singles out.

What is sourced and what is not
-------------------------------
The research is explicit that this workflow is "Documented as a UI capability",
and it is more specific in some places than others. Sourced: the two
``PATCH /v1/workspaces/{id}``-style writes, the ``properties`` parameter and its
``id``/``object``/``url`` fallback, the ``workspaceFilters`` /
``workspaceDomainFilters`` pair, the column vocabulary per CRM, the two default
views that have a quoted definition, private versus public, clone, and the
remembered open set. Not sourced: the operator set, the matching semantics, what
an unreadable filter does, whether a template change re-categorises an existing
workspace, the other three default views, the dynamic-section condition
vocabulary, and who may edit a public view. All of that is inference, and all of
it is served at ``GET /api/wf-022/inferences`` so a reviewer can disagree with a
named entry rather than hunt through a diff.

Deliberate structural choices
-----------------------------
**Every ``source`` comes from the route.** Built from ``router.prefix`` by
:func:`_source`, never written out. Hard rule 4 of the build brief is that a
write's audit row must name the route that served it, and the same defect has
shipped in this repository before: a feature's audit log kept recording a path
the app had stopped serving.

**The board is built per request.** :class:`~dsr.triage.views.TriageBoard` holds
nothing but the store handle, so keeping it on ``app.state`` would mean editing
the shared app for no gain - the reason the original twelve workflow branches
could not merge.

**``TriageError`` is a domain type, not an ``HTTPException``.** It propagates and
the host-installed handler turns it into the response, which is what
``EXCEPTION_HANDLERS`` exists for. ``RecordNotFound`` is deliberately not claimed:
the core app already maps it to 404, and two handlers for one type is a collision
the host refuses.

**Room-scoped paths are room-scoped.** ``/rooms/{room_id}/...`` for anything
about one workspace, and the view routes stay unscoped because a view is not a
room's.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.triage import TriageBoard, TriageError, parse_properties
from dsr.triage import inferences as inference_registry
from dsr.triage import vocabulary as vocab
from dsr.triage.rows import (
    CRM_LINK_COLLECTION,
    ORDER_FORM_COLLECTION,
    ROOM_COLLECTION,
    TEMPLATE_COLLECTION,
)

FEATURE = {
    "id": "wf-022-triage-the-pipeline-with-saved-workspa",
    "ticket": "WF-022",
    "name": "Triage the pipeline with saved workspace views",
    "description": (
        "The Workspaces dashboard as a set of saved views: add one from a default, clone it, "
        "filter and sort the joined rows, rearrange the columns, keep it private or share it "
        "with the team, and reopen the same views next time."
    ),
    "nav": [{"id": "pipeline-triage", "label": "Pipeline triage"}],
}

router = APIRouter(prefix="/api/wf-022", tags=["wf022"])


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def get_board(store: RecordStore = StoreDep) -> TriageBoard:
    """A :class:`TriageBoard` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and ``app.state`` is a shared file.
    """
    return TriageBoard(store)


BoardDep = Depends(get_board)


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the build brief is that a write's audit row must name the route that served
    it, and writing the string by hand is how the two drift apart.
    """
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _triage_error(request: Request, exc: TriageError) -> JSONResponse:
    """A view, filter, sort or rule this layer will not accept. 422.

    One handler for every refusal in :mod:`dsr.triage`. ``TriageError`` is raised
    by this workflow and by nothing else in the product, so a global registration
    for it cannot intercept an unrelated error anywhere in the product - which is
    what makes it safe to map here at all.
    """
    return JSONResponse(status_code=422, content={"error": "invalid_view", "detail": str(exc)})


EXCEPTION_HANDLERS = {TriageError: _triage_error}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Columns, operators, and how the two filter params differ")
def vocabulary() -> dict[str, Any]:
    """Everything the view editor needs, in one payload.

    One call rather than four, because a client that fetched the column list and
    the operator list separately could render a picker offering a combination the
    server refuses.
    """
    return vocab.catalog()


@router.get("/default-views", summary="The five default views")
def default_views() -> dict[str, Any]:
    """**All Workspaces**, **My Workspaces**, **Active Pipeline**, **Deal Desk**,
    **Implementations** - the definitions ``Add view`` starts from.

    Each carries the research's own definition where it has one, and is marked
    ``inferred`` where it does not, so a client can label the difference rather
    than presenting a guess with the same confidence as a quote.
    """
    listed = vocab.catalog()["default_views"]
    return {"count": len(listed), "default_views": listed}


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences() -> dict[str, Any]:
    """The sourced half of the workflow next to the inferred half.

    A read with no side effect, so it needs no store. Publishing it is the
    difference between "we inferred this, see the comment" and "we inferred this,
    here it is, here is what would change it".
    """
    return inference_registry.describe()


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #


@router.get("/dashboard", summary="Step one: what this account sees on opening")
def dashboard(actor: str | None = Query(default=None), board: TriageBoard = BoardDep) -> dict[str, Any]:
    """The remembered open views, the defaults to add from, and the views you own.

    "The views you had open are unique to your user account. We'll remember which
    views you had open the next time you open the Workspaces dashboard." A view
    deleted since is reported in ``missing_view_ids`` rather than dropped, because
    a remembered set that quietly forgets one is worse than one that says so.
    """
    return board.dashboard(actor)


# --------------------------------------------------------------------------- #
# Saved views
# --------------------------------------------------------------------------- #


@router.get("/views", summary="The views you may see")
def list_views(
    actor: str | None = Query(default=None),
    visibility: str | None = Query(default=None, description="private | public"),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """Your own private views plus every public one.

    A private view of another user is absent rather than marked inaccessible: it
    should not be confirmable from a list.
    """
    views = board.list_views(actor=actor, visibility=visibility)
    return {
        "actor": actor,
        "count": len(views),
        "views": views,
        "private": sum(1 for view in views if view["visibility"] == vocab.PRIVATE),
        "public": sum(1 for view in views if view["visibility"] == vocab.PUBLIC),
    }


@router.post("/views", status_code=201, summary="Add a view")
def create_view(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """**Add view**, optionally from one of the five defaults via ``base``.

    A filter set or column list sent alongside a base overrides the base's, which
    is the flow in one request: add the view, adjust it, save it.
    """
    return board.create_view(payload, actor=actor, source=_source("POST", "/views"))


@router.get("/views/{view_id}", summary="One view")
def read_view(view_id: str, actor: str | None = Query(default=None), board: TriageBoard = BoardDep) -> dict[str, Any]:
    """The view's full definition, plus any problem its own filters would report."""
    return board.get_view(view_id, actor)


@router.patch("/views/{view_id}", summary="Edit a view")
def update_view(
    view_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """Edit the name, the columns, the filters, the sort, or the visibility.

    ``columns`` is replaced wholesale, because the list is ordered and "edit and
    rearrange columns" is a statement about the whole list rather than an
    insertion at a position.
    """
    return board.update_view(view_id, payload, actor=actor, source=_source("PATCH", f"/views/{view_id}"))


@router.delete("/views/{view_id}", status_code=204, summary="Remove a view")
def delete_view(
    view_id: str,
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> Response:
    """Soft-delete, so the definition and its history stay auditable.

    A remembered open set that included this view keeps naming it, and the
    dashboard reports it as missing rather than breaking.
    """
    board.delete_view(view_id, actor=actor, source=_source("DELETE", f"/views/{view_id}"))
    return Response(status_code=204)


@router.post("/views/{view_id}/clone", status_code=201, summary="Clone a view")
def clone_view(
    view_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """**Clone** a view into a private copy of your own.

    Always private, even when the source was a public team view: "clone existing
    views to make your own customized copy", and a custom copy is one of the
    "private views for yourself".
    """
    return board.clone_view(view_id, payload, actor=actor, source=_source("POST", f"/views/{view_id}/clone"))


@router.get("/views/{view_id}/rows", summary="The joined, filtered, sorted slice")
def view_rows(
    view_id: str,
    actor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """The triage table: workspace metadata joined to engagement, CRM and order
    form fields, filtered and sorted by the view.

    ``problems`` carries any filter the engine could not read. An unreadable
    condition is dropped rather than fatal - dropping one can only widen the
    result set, so the row count shows it - and it is reported here rather than
    left to be discovered.
    """
    return board.table(view_id, actor=actor, limit=limit, offset=offset)


# --------------------------------------------------------------------------- #
# One workspace's row
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/row", summary="One workspace's joined row")
def room_row(
    room_id: str,
    properties: str | None = Query(
        default=None,
        description="Comma-separated column keys. Omit for id/object/url only.",
    ),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """A single row, honouring the researched ``properties`` parameter.

    "Endpoints that return a resource accept a ``properties`` query parameter that
    controls which fields are included in the response. If you omit it, the
    response contains only the resource's ``id``, ``object``, and ``url``." So an
    omitted parameter really does return those three and nothing else; a property
    this build has no value for is reported in ``unknown`` rather than refused,
    which is what lets a team ask for a CRM field before it starts syncing.
    """
    return board.row_for_room(room_id, parse_properties(properties))


@router.get("/rooms/{room_id}/type", summary="A workspace's effective type")
def room_type(room_id: str, board: TriageBoard = BoardDep) -> dict[str, Any]:
    """The type, and whether it was typed on the workspace or inherited.

    ``source`` is the field worth reading: it separates "a rep chose Sales" from
    "nobody has chosen anything yet".
    """
    return board.room_type(room_id)


@router.patch("/rooms/{room_id}/type", summary="Write a workspace's own type")
def set_room_type(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """The researched write: "``PATCH /v1/workspaces/{id}`` can write custom field values".

    ``{"type": null}`` clears the override and returns the workspace to its
    template's type, so being able to make a per-workspace decision includes being
    able to undo it.
    """
    return board.set_room_type(
        room_id, payload, actor=actor, source=_source("PATCH", f"/rooms/{room_id}/type")
    )


# --------------------------------------------------------------------------- #
# Templates: where the type comes from
# --------------------------------------------------------------------------- #


@router.get("/templates", summary="Templates and the type each one imposes")
def list_templates(board: TriageBoard = BoardDep) -> dict[str, Any]:
    """Template **Settings** as a list: each template's type, and how many
    workspaces are currently inheriting it.

    ``inheriting`` counts the workspaces that point at the template and have no
    type of their own, which is the population the vendor's "any future workspaces
    created from that template will be automatically categorized" is about.
    """
    listed = board.list_templates()
    return {"count": len(listed), "templates": listed}


@router.patch("/templates/{template_id}", summary="Set the type a template imposes")
def set_template_type(
    template_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """Set or clear a template's type. This is the automation's other half.

    A workspace that already carries its own type keeps it: the inheritance is a
    default, not a re-categorisation. The reasoning, and the alternative reading
    it was chosen over, are in ``type-inheritance-is-live-not-copied``.
    """
    return board.set_template_type(
        template_id, payload, actor=actor, source=_source("PATCH", f"/templates/{template_id}")
    )


# --------------------------------------------------------------------------- #
# Dynamic sections
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/sections", summary="Which sections are shown, and why")
def room_sections(room_id: str, board: TriageBoard = BoardDep) -> dict[str, Any]:
    """"**Dynamic workspaces:** Show or hide specific workspace sections based on
    what a customer has done in your product."

    A section with no rule is visible. One whose rule does not match is hidden,
    with the reason given. A rule that cannot be read leaves the section visible
    and is reported in ``problems`` - a broken rule must not take a workspace's
    pages down with it.
    """
    return board.sections(room_id)


@router.put("/rooms/{room_id}/sections", summary="Replace a workspace's section rules")
def set_room_sections(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """Set the whole ruleset: ``{"sections": [{"section": ..., "visible_when": ...}]}``.

    ``visible_when`` is an ordinary filter group, so a section is shown when the
    same conditions a view filters on would match - one operator set for the whole
    feature rather than a second vocabulary invented for sections.
    """
    return board.set_section_rules(
        room_id, payload, actor=actor, source=_source("PUT", f"/rooms/{room_id}/sections")
    )


# --------------------------------------------------------------------------- #
# The remembered open set
# --------------------------------------------------------------------------- #


@router.get("/open-views", summary="The views this account had open")
def open_views(actor: str | None = Query(default=None), board: TriageBoard = BoardDep) -> dict[str, Any]:
    """"We'll remember which views you had open the next time you open the
    Workspaces dashboard."

    An account that has never opened the dashboard gets an empty set and
    ``remembered: false``, which is a real answer rather than a missing one, so
    the client falls through to the defaults.
    """
    return board.open_views(actor)


@router.put("/open-views", summary="Record the views this account has open")
def remember_open_views(
    payload: dict[str, Any] = Body(default_factory=dict),
    board: TriageBoard = BoardDep,
) -> dict[str, Any]:
    """Replace the remembered set. Every id is checked against what the actor may
    read, so the set cannot become a way to confirm a private view's id.
    """
    return board.remember_open_views(payload, source=_source("PUT", "/open-views"))


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# ``backend/seed.py`` is a shared file and ten of the first twelve features
# rewrote it purely to add their own rows. The seeder calls the hook below
# instead, so the demo travels with the feature that needs it.
#
# Everything is written through :class:`~dsr.triage.views.TriageBoard` rather than
# by hand, so a seeded view cannot disagree with what the API would produce for
# the same request - the same reason WF-016 runs its demo events through the real
# engine over a scripted transport.

#: The templates the demo workspaces are made from. One of the two has a type, so
#: inheritance is visible on a fresh database rather than only in a test.
DEMO_TEMPLATES: tuple[dict[str, Any], ...] = (
    {"id_key": "sales-template", "name": "Sales room", "type": vocab.SOURCED_TYPE},
    {"id_key": "onboarding-template", "name": "Customer onboarding", "type": vocab.INFERRED_IMPLEMENTATION_TYPE},
    # A third template with no type at all, so "inherits nothing" is a state the
    # demo shows rather than a case only the tests cover.
    {"id_key": "blank-template", "name": "Uncategorised room", "type": None},
)

#: The extra room this feature seeds, and why.
#:
#: The shared demo dataset scatters buyer activity across all four of its rooms,
#: so "a workspace nobody has ever viewed" - a null **Last Client View**, which is
#: the state the ``is_empty`` operator and the nulls-last sort both exist for - is
#: not reachable by editing the existing rows. One extra room is the honest way to
#: put it on screen, and it is an ordinary audited room record like any other.
#: ``index`` is a sentinel meaning "a room of my own" rather than ``room_ids[n]``.
OWN_ROOM = -1
DEMO_SILENT_ROOM = {
    "id_key": "wf022-silent-room",
    "name": "Alder Freight — Invitation Sent",
    "account": "Alder Freight",
    "stage": "outreach",
    "owner": "sam",
    "seats": 5,
    "integrations": ["hubspot"],
    "note": "Seeded by WF-022. No buyer activity, so Last Client View is empty.",
}

#: The CRM links, keyed by which demo workspace they hang off.
#:
#: Deliberately spread, because the interesting part of Active Pipeline is the
#: disjunction and the three states that exercise it have to all be on screen:
#: ``room_at(0)`` satisfies both arms, ``room_at(1)`` satisfies only the Sales
#: type, ``room_at(2)`` and the extra room satisfy only the CRM arm, and
#: ``room_at(3)`` satisfies neither and is therefore out of the view.
#:
#: The extra room's link also carries Salesforce field names alongside HubSpot
#: ones, on purpose: the provider gate is a correctness rule and the demo is the
#: only place a reviewer can see it refuse to show a Closed Lost stage the
#: integration never wrote. See ``crm-columns-are-provider-gated``.
DEMO_CRM_LINKS: tuple[tuple[int, dict[str, Any]], ...] = (
    (
        0,
        {
            "provider": "salesforce",
            "opportunity_id": "006NS00000A1b2c",
            "opportunity_stage": "Negotiation/Review",
            "opportunity_created_date": "2026-06-14",
            "opp_amount": 184000,
            "opportunity_type": "New Business",
            "account_id": "001NS00000A1",
        },
    ),
    (
        2,
        {
            "provider": "salesforce",
            "opportunity_id": "006NS00000F4d5e",
            "opportunity_stage": "Negotiation",
            "opportunity_created_date": "2026-03-09",
            "opp_amount": 42000,
            "opportunity_type": "Renewal",
            "account_id": "001NS00000F4",
        },
    ),
    (
        OWN_ROOM,
        {
            "provider": "hubspot",
            "deal_id": "hs-9001",
            "deal_stage": "Contact Created",
            "deal_type": "Outbound",
            "deal_amount": 8000,
            "account_id": "hs-acct-77",
            # Salesforce field names on a HubSpot link. The salesforce.* columns
            # must stay null: a stage from a system this workspace is not
            # connected to is a wrong number on a live pipeline.
            "opportunity_stage": "Closed Lost",
            "opp_amount": 999999,
        },
    ),
)

#: Order forms. Two of the three states a deal desk actually holds: signed, still
#: out, and voided. A voided form is the row that most needs a rep to look at it,
#: and it is why Deal Desk keys on presence rather than on status
#: (``order-form-presence-not-status``). ``fabrikam``'s voided form is also the
#: section rule below.
DEMO_ORDER_FORMS: tuple[tuple[int, dict[str, Any]], ...] = (
    (0, {"status": "completed", "deal_type": "New Business"}),
    (1, {"status": "sent", "deal_type": "New Business"}),
    (2, {"status": "voided", "deal_type": "Renewal"}),
)

#: The sections each demo workspace declares. Declared rather than published as an
#: enum, because the research names the show/hide pattern and no section list.
DEMO_SECTIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("overview", "documents", "pricing", "order-form", "timeline")),
    (1, ("overview", "security", "timeline")),
    (2, ("overview", "implementation-plan", "order-form", "timeline")),
)

#: The workspace types, so the inheritance, the override, and "no type at all"
#: are all on screen.
#:
#: ``room_at(0)`` is typed only by its template, ``room_at(1)`` is typed by hand
#: and has no CRM at all - the Active Pipeline arm that is easy to forget -
#: ``room_at(2)`` is typed by its template as an onboarding workspace, so it is in
#: Implementations and out of the first arm, ``room_at(3)`` sits on a template
#: that categorises nothing and has no CRM link either, which is the workspace
#: the disjunction excludes, and the extra room is typed by hand with a type that
#: is not Sales.
DEMO_TYPES: tuple[tuple[int, str, Any], ...] = (
    (0, "template_id", "sales-template"),
    (1, "type", vocab.SOURCED_TYPE),
    (2, "template_id", "onboarding-template"),
    (3, "template_id", "blank-template"),
    (OWN_ROOM, "type", "Outbound"),
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed templates, a silent room, CRM links, order forms, views, and an open set.

    The states are chosen so each researched rule is visible on a fresh database
    without any interaction:

    * **both arms of Active Pipeline separately** - one workspace typed Sales with
      no CRM, one workspace with a CRM and no Sales type - plus one with both, and
      one with neither, which is out;
    * **both providers**, plus a HubSpot record carrying Salesforce field names,
      so the provider gate is visible rather than merely asserted in a test;
    * **a voided order form**, so Deal Desk has a row a rep would chase, and a
      workspace with no order form at all, so the view's exclusion is visible too;
    * **a workspace nobody has ever viewed**, so a null Last Client View exists in
      the demo;
    * **a public team view, three private views, one of them a clone** of the
      public one, which is all three visibilities the research describes;
    * **a remembered open set** for two accounts, so step one of the flow is not an
      empty screen;
    * **a section hidden by a rule that does not match**, so the dynamic-section
      pattern is on screen.

    Returns a short description of what was added, which the seeder prints.
    """
    from dsr.store import RecordStore

    store = RecordStore(db)
    board = TriageBoard(store)
    source = "seed"
    actor = "dana"

    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])

    template_ids: dict[str, str] = {}
    for spec in DEMO_TEMPLATES:
        record = db.create(
            TEMPLATE_COLLECTION,
            {"name": spec["name"], "type": spec["type"]},
            record_id=spec["id_key"],
            actor=actor,
            source=source,
        )
        template_ids[spec["id_key"]] = record["id"]
    summary: list[str] = [f"{len(DEMO_TEMPLATES)} templates"]

    if not rooms:
        # No demo rooms to point at. The templates are still worth having, and the
        # seeder prints what was skipped rather than pretending otherwise.
        return ", ".join(summary) + ", 0 CRM links, 0 views (no rooms to attach to)"

    own_room = db.create(
        ROOM_COLLECTION,
        {key: value for key, value in DEMO_SILENT_ROOM.items() if key != "id_key"},
        record_id=DEMO_SILENT_ROOM["id_key"],
        actor="sam",
        source=source,
    )["id"]

    def room_at(index: int) -> str:
        return own_room if index == OWN_ROOM else rooms[index % len(rooms)][0]

    for index, link in DEMO_CRM_LINKS:
        db.create(CRM_LINK_COLLECTION, link, room_id=room_at(index), actor=actor, source=source)
    summary.append(f"{len(DEMO_CRM_LINKS)} CRM links (2 Salesforce, 1 HubSpot)")

    for index, form in DEMO_ORDER_FORMS:
        db.create(ORDER_FORM_COLLECTION, form, room_id=room_at(index), actor=actor, source=source)
    summary.append(f"{len(DEMO_ORDER_FORMS)} order forms (1 completed, 1 sent, 1 voided)")

    for index, key, value in DEMO_TYPES:
        resolved = template_ids.get(value, value) if key == "template_id" else value
        _retype(db, room_at(index), key, resolved, actor, source)
    summary.append(f"{len(DEMO_TYPES)} workspace types (3 inherited, 2 typed by hand)")

    for index, sections in DEMO_SECTIONS:
        _retype(
            db,
            room_at(index),
            "sections",
            [{"key": key, "label": key.replace("-", " ").title()} for key in sections],
            actor,
            source,
        )
    summary.append("1 extra room with no buyer activity")

    views: list[str] = []

    # A public team view: the Active Pipeline definition, shared.
    team = board.create_view(
        {"base": "active-pipeline", "name": "Team pipeline", "visibility": vocab.PUBLIC},
        actor=actor,
        source=source,
    )
    views.append(team["id"])

    # A private view of the owner's own workspaces. ``$me`` resolves to dana on
    # creation, so the stored filter is a plain equality and stays readable
    # through the dynamic index.
    mine = board.create_view({"base": "my", "name": "My pipeline"}, actor=actor, source=source)
    views.append(mine["id"])

    # A private clone of the public view, made by somebody else and then
    # rearranged. The clone is the researched "your own customized copy", and its
    # being private is what puts both visibilities on screen at once.
    sam_copy = board.clone_view(team["id"], {"name": "Renewals I am chasing"}, actor="sam", source=source)
    views.append(sam_copy["id"])
    board.update_view(
        sam_copy["id"],
        {
            "columns": [
                "dock.name",
                "dock.owner",
                "hubspot.deal_stage",
                "hubspot.deal_amount",
                "engagement.last_client_view",
            ],
            "sort": {"field": "hubspot.deal_amount", "direction": "desc"},
        },
        actor="sam",
        source=source,
    )

    # The deal desk narrowed to signed order forms. A view the team *edited*
    # rather than only added, which is the point of being able to edit one.
    signed = board.create_view(
        {
            "base": "deal-desk",
            "name": "Signed only",
            "workspace_domain_filters": {
                "join": "and",
                "conditions": [{"field": "order_form.status", "op": "is", "value": "completed"}],
            },
        },
        actor=actor,
        source=source,
    )
    views.append(signed["id"])

    # The remembered open set, so step one of the flow is not an empty screen.
    board.remember_open_views(
        {"actor": actor, "view_ids": [team["id"], signed["id"]], "active_view_id": team["id"]},
        source=source,
    )
    board.remember_open_views({"actor": "sam", "view_ids": [sam_copy["id"]]}, source=source)
    summary.append(f"{len(views)} views (1 public, 3 private, 1 of them a clone)")

    # A section hidden by a rule that does not match: the order form on the
    # workspace whose form was voided.
    board.set_section_rules(
        room_at(2),
        {
            "sections": [
                {
                    "section": "order-form",
                    "visible_when": {
                        "join": "and",
                        "conditions": [{"field": "order_form.status", "op": "is", "value": "completed"}],
                    },
                }
            ]
        },
        actor=actor,
        source=source,
    )
    summary.append("1 section rule (order form hidden while it is not completed)")

    return ", ".join(summary)


def _retype(
    db: AuditedDatabase, room_id: str, key: str, value: Any, actor: str, source: str
) -> None:
    """Write one field onto an existing workspace through the audited store.

    Deliberately not a route: this is demo shaping of records the shared seeder
    already created, and the audit rows say ``seed`` because that is what they
    are. The researched *write* path - ``PATCH /api/wf-022/rooms/{id}/type`` - is
    exercised by the tests instead.
    """
    from dsr.store import RecordStore

    RecordStore(db).update(room_id, {key: value}, actor=actor, source=source)
