"""WF-106: trigger outreach on high-intent page visits.

A prospect browses a seller's upgrade page more than once. The room matches the
page view against the workflow's targeting rules, counts the visits inside the
window, checks the *Show workflow until* mode and the researched session damper,
and shows an in-app block. The buyer's answer is recorded as a ``content_stat``
receipt and the block is hidden for the rest of that session.

The domain logic is in :mod:`dsr.page_outreach`, which this module does not own.
What lives here is the three things a workflow has to take out of shared files:
the HTTP surface, the mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-106``, and every read is room-scoped.** A page view is
interest in one seller's site through one room, and a receipt that cannot say which
room is not one a seller can act on. Room is a query parameter rather than a path
segment, because a client already holds a room list and gets the room it cares
about from a picker.

**``source=`` names the method and the route.** Every write below passes the HTTP
method and ``router.prefix``, so the audit row names the route that actually served
it. A hardcoded string inside a domain method is a defect, and that class of bug has
shipped in this codebase before: a feature's audit log kept naming a path the app
had stopped serving. The method is part of the name because two features may share a
prefix while their concrete paths differ.

**Two routes for one decision, on purpose.** ``POST /views/evaluate`` is the strict
evaluator and reports its decision without writing. ``POST /views`` commits. The
reason is the one WF-027 and WF-133 both give: a buyer who has visited a pricing
page twice is a fact rather than a caller error, so a caller watching a prospect
before deciding anything gets its decisions without filling the store with visits
that were only ever going to be counted.

**One handler for the whole error hierarchy.** ``PageOutreachError`` is the base of
every refusal in :mod:`dsr.page_outreach`, and each carries its own ``status`` and
``code``, so one handler answers 422 for a page view that cannot be read and 409 for
a workflow name that is already taken. ``RecordNotFound`` is deliberately not
claimed: the core app already maps it to 404, and two handlers for one type is a
collision the host refuses.

**One module, at one name.** The brief names this file
``wf106_trigger_outreach_on_high_intent_page_visits.py`` and GitHub issue 137 names
the same file ``wf106_trigger_outreach_on_high_intent_page_v.py``, truncated to the
forty characters every other feature module in this repository is truncated to. The
brief says its names are exact, so this file carries the code and the truncated name
exists nowhere in the tree. Jev was asked which of the three possible readings to take
and selected ``brief_name_only`` at confidence 0.96, audit
``jev-20261005T163752-16556-72939``, on measured evidence: an alias that re-exported
this router made the host report a route collision on all eighteen paths and record
the alias as a failed feature, which put ``failed_count`` at 1 and would break other
agents' tests that assert it is 0.

**Demo data is not all successes.** The seeder prints this feature's return string
on a Windows console, so every character of it is ASCII. The string names the states
it created, and a demo where every workflow is live and every buyer engaged teaches
nothing about the damper, so it seeds a draft, a buyer who never qualified, a buyer
hidden for the session, and a buyer who engaged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.page_outreach import PageOutreach
from dsr.page_outreach.errors import PageOutreachError
from dsr.page_outreach.vocabulary import ENGAGEMENT_INTERACTIONS
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-106-trigger-outreach-on-high-intent-page-visits",
    "ticket": "WF-106",
    "name": "Trigger outreach on high-intent page visits",
    "description": (
        "Match a website page view against a workflow's targeting rules, count the visits "
        "inside the window, apply the Show workflow until mode and the session damper, and "
        "record what would be shown plus the content_stat receipts for what the buyer did. "
        "The repeat count, the repeat window and the dwell threshold are derived, not "
        "sourced, and each says so."
    ),
    "nav": [{"id": "page-outreach", "label": "Page outreach"}],
}

router = APIRouter(prefix="/api/wf-106", tags=["wf106"])


def get_outreach(store: RecordStore = StoreDep) -> PageOutreach:
    """A :class:`PageOutreach` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but the
    store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it here
    also leaves the engine a plain object, which is what a test constructs.
    """
    return PageOutreach(store)


OutreachDep = Depends(get_outreach)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _page_outreach_error(request: Request, exc: PageOutreachError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``PageOutreachError`` is the base of every
    refusal in :mod:`dsr.page_outreach`, and all of them are the caller's to fix. The
    status rides on the exception rather than being decided here, because "this page
    view carries a field no rule reads" and "that workflow name is taken" are both
    this package's errors and only one of them conflicts with state that already
    exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {PageOutreachError: _page_outreach_error}


# --------------------------------------------------------------------------- #
# Vocabulary, decisions and the room at a glance
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(outreach: PageOutreach = OutreachDep) -> dict[str, Any]:
    """Every name, number and threshold this workflow uses, served as data.

    The three frequency modes with the quote beside each one, the five interaction
    kinds, the five trigger panes with what each holds here, the rule kinds, the three
    derived thresholds with the derivation beside each one, and the five limits the
    research left open. A client renders its pickers from this rather than from a list
    compiled into the page, so a threshold changed in the domain module reaches every
    client at once.
    """
    return outreach.vocabulary()


@router.get("/inferences")
def inferences(outreach: PageOutreach = OutreachDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research quotes three frequency modes and one session damper sentence. It names
    a repeat count, a repeat window, a dwell threshold and five pane contents and
    specifies none of them. Those readings are collected here, named, traceable and
    served, rather than left as comments in function bodies. Seventeen entries, and
    each says whether it is sourced.
    """
    return outreach.inferences()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """The room at a glance: workflows, views, blocks shown and receipts.

    Every number here is a count over this feature's own four collections. Nothing is
    read from another feature, so a room whose dependencies have not seeded reads as
    zeros rather than failing.
    """
    return outreach.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


@router.get("/workflows")
def list_workflows(
    room_id: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """The workflows in a room, drafts included."""
    listed = outreach.workflows(room_id=room_id)
    return {
        "count": len(listed),
        "by_state": _tally(str(entry.get("state") or "draft") for entry in listed),
        "workflows": listed,
    }


@router.post("/workflows", status_code=201)
def create_workflow(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Save a workflow: the targeting rules, the five panes, the blocks and the paths.

    A draft unless the caller says ``state: live``, because the researched flow ends
    with the seller setting it live and a workflow that published itself the moment it
    was created would publish half-finished rules to the whole audience.

    A channel other than ``in_app`` is refused rather than stored. The research names
    the Messenger as the surface and ``POST /messages`` with ``message_type: in_app``
    as its API equivalent, and this product has no outbound transport, so a second
    channel would be a stored promise nothing could keep.
    """
    return outreach.create_workflow(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/workflows"
    )


@router.get("/workflows/{workflow_id}")
def read_workflow(workflow_id: str, outreach: PageOutreach = OutreachDep) -> dict[str, Any]:
    """One workflow, with its rules, panes, blocks and paths."""
    return outreach.read_workflow(workflow_id)


@router.put("/workflows/{workflow_id}")
def replace_workflow(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Replace a workflow's rules and panes with what the caller sent.

    A full replacement rather than a merge. A merge of a rule list would leave a rule
    the seller deleted in place forever, and this API has no way to express "remove
    this one rule" against a patch.
    """
    return outreach.update_workflow(
        workflow_id, payload, actor=actor, source=f"PUT {router.prefix}/workflows/{{workflow_id}}"
    )


@router.post("/workflows/{workflow_id}/state", status_code=201)
def set_workflow_state(
    workflow_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Move a workflow between draft and live.

    A separate route because the researched flow's last step is this one and it is the
    only write here that changes what buyers see. A draft never fires, whatever the
    page views say, and says so rather than quietly returning an empty decision.
    """
    return outreach.set_state(
        workflow_id,
        str(payload.get("state") or ""),
        actor=actor,
        source=f"POST {router.prefix}/workflows/{{workflow_id}}/state",
    )


@router.delete("/workflows/{workflow_id}")
def delete_workflow(
    workflow_id: str,
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Retire a workflow.

    A soft delete, so the audit trail keeps the rules that were in force for every view
    recorded while it was live. A hard delete would leave those views pointing at a
    definition nobody can read.
    """
    return outreach.delete_workflow(
        workflow_id, actor=actor, source=f"DELETE {router.prefix}/workflows/{{workflow_id}}"
    )


# --------------------------------------------------------------------------- #
# Page views: the ingest and the decision
# --------------------------------------------------------------------------- #


@router.post("/views/evaluate")
def evaluate_view(
    payload: dict[str, Any] = Body(default_factory=dict),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Say what the rules decide, and write nothing.

    The report and the commit are two routes on purpose. This one never writes a view,
    so a caller watching a prospect before deciding anything gets every decision
    without filling the store with visits that were only ever going to be counted. The
    ``stopped_by`` field names which of the five gates stopped it: rules, the repeat
    threshold, the frequency mode, the session damper, or the workflow not being live.

    One page view per call. The page view is collected by an external JavaScript
    snippet on the seller's site, and the room owns the rules and the receipts rather
    than the collection, so the caller owns the batching and posts one call per view.
    """
    return outreach.evaluate(payload)


@router.post("/views", status_code=201)
def record_view(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Record a page view and commit the decision.

    Two records are written when the block is shown, one when it is not. The view is
    written either way, because the repeat count is made of the visits that did not
    qualify as well as the ones that did, and a page view that was counted and then
    dropped would leave the arithmetic unauditable.

    A 201 here does not mean a block was shown. It means the view was recorded and the
    decision was made. ``show`` and ``stopped_by`` say which happened, and a delivery
    record is present only when the block was shown.
    """
    return outreach.record_view(
        payload, room_id=room_id, actor=actor, source=f"POST {router.prefix}/views"
    )


@router.get("/views")
def list_views(
    room_id: str | None = Query(default=None),
    workflow_id: str = Query(default=""),
    visitor_key: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """The recorded page views, newest first, and how many of them qualified."""
    rows = outreach.views(
        room_id=room_id,
        workflow_id=workflow_id,
        visitor_key=visitor_key,
        limit=limit,
    )
    return {
        "count": len(rows),
        "matched": len([row for row in rows if row.get("matched")]),
        "by_stopped_by": _tally(str(row.get("stopped_by") or "") for row in rows),
        "views": rows,
    }


@router.get("/views/{view_id}")
def read_view(view_id: str, outreach: PageOutreach = OutreachDep) -> dict[str, Any]:
    """One recorded page view, with the counts that were in force when it was seen."""
    return outreach.read_view(view_id)


# --------------------------------------------------------------------------- #
# Deliveries: what would be shown, to whom, in which session
# --------------------------------------------------------------------------- #


@router.get("/deliveries")
def list_deliveries(
    room_id: str | None = Query(default=None),
    visitor_key: str = Query(default=""),
    workflow_id: str = Query(default=""),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """The blocks this room would have shown, newest first, with each one's state."""
    listed = outreach.deliveries(room_id=room_id, visitor_key=visitor_key, workflow_id=workflow_id)
    return {
        "count": len(listed),
        "by_state": _tally(str(entry.get("state") or "shown") for entry in listed),
        "deliveries": listed,
    }


@router.get("/deliveries/{delivery_id}")
def read_delivery(delivery_id: str, outreach: PageOutreach = OutreachDep) -> dict[str, Any]:
    """One delivery: the blocks, the trigger that caused it, and the receipts on it.

    ``state`` is derived from the receipts rather than stored, so a delivery can never
    hold a state its own receipts do not support. It reads ``engaged`` after a path
    selection or a goal, ``hidden_for_session`` after a dismissal or a Messenger open,
    ``interacted`` after a click, and ``shown`` while nothing has been recorded.
    """
    return outreach.read_delivery(delivery_id)


# --------------------------------------------------------------------------- #
# Receipts: the content_stat events
# --------------------------------------------------------------------------- #


@router.get("/receipts")
def list_receipts(
    room_id: str | None = Query(default=None),
    visitor_key: str = Query(default=""),
    workflow_id: str = Query(default=""),
    delivery_id: str = Query(default=""),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """The content_stat receipts recorded, newest first, grouped by kind."""
    listed = outreach.receipts(
        room_id=room_id,
        visitor_key=visitor_key,
        workflow_id=workflow_id,
        delivery_id=delivery_id,
    )
    return {
        "count": len(listed),
        "by_kind": _tally(str(entry.get("kind") or "") for entry in listed),
        "engaged": len([entry for entry in listed if entry.get("engagement")]),
        "hides_for_session": len([entry for entry in listed if entry.get("hides_for_session")]),
        "receipts": listed,
    }


@router.post("/deliveries/{delivery_id}/interactions", status_code=201)
def record_interaction(
    delivery_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Record one buyer interaction as a content_stat receipt.

    Five kinds: a path selection, a Messenger open, a click, a dismissal and a goal
    reached. A dismissal or a Messenger open hides the block for the rest of the
    session, and a path selection or a goal counts as engaging. Both are read back
    from the receipts rather than written as a flag, so the damper has no state that
    can disagree with the events it is derived from.

    A ``path_selected`` receipt has to name a key the workflow declared, because the
    researched flow branches on the buyer's answer and an answer that resolves to no
    branch would leave the receipt pointing at nothing.
    """
    return outreach.record_interaction(
        delivery_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/deliveries/{{delivery_id}}/interactions",
    )


# --------------------------------------------------------------------------- #
# Who browsed and what happened to them
# --------------------------------------------------------------------------- #


@router.get("/prospects")
def prospects(
    room_id: str | None = Query(default=None),
    outreach: PageOutreach = OutreachDep,
) -> dict[str, Any]:
    """Every visitor this room has a recorded page view for, and how far each one got.

    Built from the views rather than from the deliveries, because the interesting rows
    are the buyers who browsed a high-intent page and did not yet qualify. A view built
    from the deliveries alone can only show who was shown the block, so the half of the
    screen a seller most needs would be empty.
    """
    return outreach.prospects(room_id=room_id)


def _tally(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _ago(now: datetime, **back: float) -> str:
    """An ISO 8601 moment ``back`` from ``now``.

    Every string this seed writes carries an offset, and every string this seed returns
    is ASCII, because ``seed.py`` prints the return value on a Windows console and one
    rightwards arrow in one recovered feature broke the whole seeder.
    """
    return (now - timedelta(**back)).isoformat()


#: Four workflows, one per combination a reviewer needs to see: a live Engaged with
#: workflow with two branches, a live Seen workflow that has already fired once, a live
#: Any interaction workflow, and a draft that has never fired at all.
_SEED_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "name": "Upgrade page repeaters",
        "frequency": "engaged_with",
        "state": "live",
        "repeat_visits": 3,
        "repeat_window_days": 7,
        "dwell_seconds": 60,
        "rules": [
            {"kind": "url", "mode": "prefix", "value": "/upgrade"},
            {"kind": "dwell", "value": "60"},
        ],
        "audience": {"company_keys": [], "tags": [], "segments": []},
        "goal_name": "meeting_booked",
        "blocks": [
            {
                "kind": "message",
                "text": (
                    "You have been back to our upgrade page a few times. Here is the "
                    "two-minute version of what changes on the higher plan."
                ),
                "apps": [
                    {
                        "kind": "video",
                        "title": "Two-minute upgrade walkthrough",
                        "url": "https://northwind.example/upgrade-walkthrough",
                    }
                ],
            }
        ],
        "paths": [
            {
                "key": "yes_upgrade",
                "label": "Yes, let's talk about upgrading",
                "next": "book_a_time",
                "note": "The research's booking branch.",
            },
            {
                "key": "not_right_now",
                "label": "Not right now",
                "next": "webinar",
                "closes": True,
                "note": "The research's close branch.",
            },
        ],
        "note": "Live. Two visits short of three shows nothing, and the third shows it.",
    },
    {
        "name": "Pricing page, once only",
        "frequency": "seen",
        "state": "live",
        "repeat_visits": 2,
        "repeat_window_days": 14,
        "dwell_seconds": 45,
        "rules": [{"kind": "url", "mode": "prefix", "value": "/pricing"}],
        "audience": {"company_keys": [], "tags": [], "segments": []},
        "goal_name": "",
        "blocks": [
            {
                "kind": "message",
                "text": "You have looked at pricing twice. Here is the per-seat breakdown.",
                "apps": [
                    {
                        "kind": "article",
                        "title": "Per-seat pricing",
                        "url": "https://tailwind.example/pricing-detail",
                    }
                ],
            }
        ],
        "paths": [],
        "note": "Live. The Seen mode fires once and never again, whatever the buyer does.",
    },
    {
        "name": "Security page, stops on any touch",
        "frequency": "any_interaction",
        "state": "live",
        "repeat_visits": 2,
        "repeat_window_days": 7,
        "dwell_seconds": 90,
        "rules": [
            {"kind": "url", "mode": "prefix", "value": "/security"},
            {"kind": "utm_source", "value": "linkedin*"},
        ],
        "audience": {"company_keys": [], "tags": [], "segments": []},
        "goal_name": "",
        "blocks": [
            {
                "kind": "message",
                "text": "You came back to our security page from LinkedIn. Here is the report.",
                "apps": [
                    {
                        "kind": "article",
                        "title": "Security report 2026",
                        "url": "https://meridian.example/security-report",
                    }
                ],
            }
        ],
        "paths": [],
        "note": "Live. A dismissal counts as an interaction and stops it just as surely.",
    },
    {
        "name": "Case studies, still a draft",
        "frequency": "seen",
        "state": "draft",
        "repeat_visits": 3,
        "repeat_window_days": 7,
        "dwell_seconds": 60,
        "rules": [{"kind": "url", "mode": "prefix", "value": "/case-studies"}],
        "audience": {"company_keys": [], "tags": [], "segments": []},
        "goal_name": "",
        "blocks": [{"kind": "message", "text": "Three customers in your industry.", "apps": []}],
        "paths": [],
        "note": "Draft. Never fires, and says so rather than returning an empty decision.",
    },
)


#: The buyer activity that produces four different states between them. Each entry is
#: one prospect, the visits they made, and what they did with the block.
_SEED_PROSPECTS: tuple[dict[str, Any], ...] = (
    {
        "workflow": "Upgrade page repeaters",
        "visitor_key": "visitor-northwind-9f2",
        "company_key": "northwind-energy",
        "visits": (
            {"hours": 72, "dwell": 71},
            {"hours": 50, "dwell": 66},
            {"hours": 3, "dwell": 84, "session": "sess-northwind-4"},
        ),
        "interactions": (("path_selected", "yes_upgrade", ""),),
        "reads": "Qualified on the third visit, chose a branch, and the mode then stops.",
    },
    {
        "workflow": "Pricing page, once only",
        "visitor_key": "visitor-tailwind-3c1",
        "company_key": "tailwind-and-friends",
        "visits": (
            {"hours": 100, "dwell": 55},
            {"hours": 26, "dwell": 61, "session": "sess-tailwind-2"},
        ),
        "interactions": (("dismissed", "", "Read the pricing page last quarter too."),),
        "reads": "Qualified, then dismissed. The Seen mode keeps it at one show anyway.",
    },
    {
        "workflow": "Security page, stops on any touch",
        "visitor_key": "visitor-meridian-7b8",
        "company_key": "meridian-foods",
        "visits": (
            {"days": 3, "dwell": 104, "utm_source": "linkedin-ads"},
            {"days": 1, "dwell": 95, "session": "sess-meridian-1", "utm_source": "linkedin-ads"},
        ),
        "interactions": (("messenger_opened", "", ""),),
        "reads": (
            "Qualified, then opened the Messenger. Hidden for the rest of that session, and "
            "the Any interaction mode stops it entirely."
        ),
    },
    {
        "workflow": "Upgrade page repeaters",
        "visitor_key": "visitor-halcyon-5d4",
        "company_key": "",
        "visits": (
            {"hours": 30, "dwell": 70},
            {"hours": 12, "dwell": 68},
        ),
        "interactions": (),
        "reads": (
            "Two matching visits and no third. Written to the view record and shown nothing, "
            "which is the row a seller most needs to see."
        ),
    },
    {
        "workflow": "Case studies, still a draft",
        "visitor_key": "visitor-orbis-1a7",
        "company_key": "orbis-labs",
        "visits": (
            {"hours": 6, "dwell": 75},
            {"hours": 2, "dwell": 80, "session": "sess-orbis-1"},
        ),
        "interactions": (),
        "reads": "Two matching visits against a draft workflow. Never fires.",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Demo rows for the page-outreach page.

    Four workflows and five prospects, chosen so the page shows every state the
    workflow can be in rather than only the happy one: a buyer who engaged, a buyer
    who was shown the block and dismissed it, a buyer whose session hid it, a buyer
    who has two matching visits and no third, and a buyer whose views hit a draft.

    The prerequisites are provisioned here rather than borrowed. Nothing is read from
    another feature, so this demo data does not depend on the alphabetical order of the
    seeder, and a demo that appears only when another branch's seed happens to run
    first is not a demo.
    """
    room_ids = context.get("room_ids") or []
    if not room_ids:
        return ""
    room_id = room_ids[0][0]
    now = context["now"]

    outreach = PageOutreach(RecordStore(db))
    existing = outreach.workflows(room_id=room_id)
    if existing:
        return (
            f"{len(existing)} workflows already seeded "
            f"({', '.join(str(entry.get('state')) for entry in existing)}); "
            "no new demo rows written"
        )

    by_name: dict[str, str] = {}
    for payload in _SEED_WORKFLOWS:
        created = outreach.create_workflow(
            payload, room_id=room_id, actor="seed", source="seed", now=now
        )
        by_name[str(created["name"])] = str(created["id"])

    states = {"shown": 0, "engaged": 0, "dismissed": 0, "hidden": 0, "pending": 0}
    for entry in _SEED_PROSPECTS:
        workflow_id = by_name.get(str(entry["workflow"]), "")
        if not workflow_id:
            continue
        last_delivery_id = ""
        for position, visit in enumerate(entry["visits"]):
            result = outreach.record_view(
                {
                    "workflow_id": workflow_id,
                    "visitor_key": entry["visitor_key"],
                    "company_key": entry["company_key"],
                    "session_id": visit.get("session") or f"sess-{entry['visitor_key']}-{position}",
                    "path": _seed_path(str(entry["workflow"])),
                    "dwell_seconds": visit.get("dwell", 0),
                    "utm_source": visit.get("utm_source", ""),
                    "visited_at": _ago(
                        now,
                        days=visit.get("days", 0),
                        hours=visit.get("hours", 0),
                        minutes=visit.get("minutes", 0),
                    ),
                },
                room_id=room_id,
                actor="seed",
                source="seed",
                now=now,
            )
            delivery = result.get("delivery")
            if delivery:
                last_delivery_id = str(delivery["id"])
        if not last_delivery_id:
            states["pending"] += 1
            continue
        kinds: set[str] = set()
        for kind, path_key, note in entry["interactions"]:
            outreach.record_interaction(
                last_delivery_id,
                {"kind": kind, "path_key": path_key, "note": note},
                actor="seed",
                source="seed",
                now=now,
            )
            kinds.add(kind)
        if kinds & set(ENGAGEMENT_INTERACTIONS):
            states["engaged"] += 1
        elif "dismissed" in kinds:
            states["dismissed"] += 1
        elif "messenger_opened" in kinds:
            states["hidden"] += 1
        else:
            states["shown"] += 1

    summary = outreach.summary(room_id=room_id)
    return (
        f"4 workflows (1 live Engaged with, 1 live Seen, 1 live Any interaction, "
        f"1 draft), {summary['views']} page views, {summary['deliveries']} blocks shown, "
        f"{summary['receipts']} receipts ({summary['engaged_receipts']} engaged); "
        f"prospects: {states['engaged']} engaged, {states['dismissed']} dismissed, "
        f"{states['hidden']} hidden for session, {states['pending']} not qualified"
    )


def _seed_path(workflow_name: str) -> str:
    """The page each demo workflow targets, so the views have real paths in them."""
    return {
        "Upgrade page repeaters": "/upgrade/plans",
        "Pricing page, once only": "/pricing",
        "Security page, stops on any touch": "/security/trust",
        "Case studies, still a draft": "/case-studies/logistics",
    }.get(workflow_name, "/overview")


__all__ = ["EXCEPTION_HANDLERS", "FEATURE", "router", "seed"]
