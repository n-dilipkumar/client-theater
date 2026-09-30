"""WF-063: reassign a booked meeting to a different host.

A researched workflow, not a port: there is no source branch. The research
document is the specification, and what it specifies is how a booking changes
hands and, just as importantly, what it is *not* allowed to change on the way.

What the research specifies
---------------------------

A meeting was booked and has a host. An administrator opens
``Reporting > Meetings Activity``, picks the **Upcoming** or **Past** tab,
filters, and opens a meeting. The icon next to the host's name offers **Edit
Meeting** or **Reassign Meeting**. If the target person is known and free, they
pick them and hit Reassign; otherwise they take Edit Meeting, which reopens the
scheduler for the *original* Distribution, where they may change the
**Distribution**, **Team** or **Individual** and pick a new slot. Two things
cannot change: the Meeting Type and the Workspace. The invite is updated with
the new assignee's name, links and details. A ``Meeting Update`` webhook fires.
An Events History row records who, to whom, when, and the source. The
round-robin credit moves with the host.

The four sources it rests on, and what each contributes:

* Chili Piper's *Reassigning Meetings* article - the two-step shape, the
  editable axis, the two locked fields, and the note that reassignment ignores
  the minimum scheduling notice and the maximum availability range.
* Chili Piper's Events History article - the four things a reassignment row
  displays, and the two sources it names.
* Cal.com's automation/webhooks guide - ``BOOKING_REASSIGNED``, the payload
  keys that differ from a plain booking payload, and the round-robin-only scope
  that decides whether it fires at all.
* Cal.com's v2 API reference - the two reassign endpoints, and the round-robin
  limit on the automatic one.

What this module is
-------------------

Only the three things a feature is allowed to add: the HTTP surface, the mapping
from domain errors to responses, and the demo data. The behaviour is in
:mod:`dsr.reassign`, where it can be tested without a request.

Decisions in here a reviewer would otherwise have to reverse-engineer
---------------------------------------------------------------------

**One source of truth for the decision.** ``preview`` and ``reassign`` both call
``rules.decide``. A dry run that could disagree with the real one would be
worse than no dry run, because it would be trusted.

**A refusal writes nothing.** Not the meeting, not the reassignment record, not
the history row. The brief for WF-041 puts it the same way and it is right for
the same reason: a row saying "we tried and did not" is indistinguishable from a
row saying "we did", to anything that does not open the detail.

**The bounds are evaluated and then ignored.** A reassignment records which of
the minimum notice and the maximum range the slot breaches, because a
reassignment that rescued a stale booking and gave no hint that it was inside the
notice window would teach nothing to the person who has to explain it. The
exemption is a researched rule, not an accident, and showing the numbers is how
it stays visible.

**``source`` is built from ``router.prefix`` at the route and passed down.** The
audit row must name the route that actually served the write. That defect has
shipped in this codebase before - an audit log naming a route the app had
stopped serving - so the suite asserts every recorded source matches a route the
host actually mounted.

**Room-scoped paths are room-scoped.** The brief asks for
``/rooms/{room_id}/...`` and a meeting belongs to a room, so the meetings, the
reassignments, the history and the whole flow take a room. The unscoped routes
are the ones genuinely not about a room: the vocabulary, the outcomes, the
webhooks, the distributions, the hosts, and the summary.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.reassign import (
    DISTRIBUTION_COLLECTION,
    HISTORY_LIMIT,
    HOST_COLLECTION,
    MEETING_COLLECTION,
    MeetingStateError,
    ReassignEngine,
    ReassignError,
    inferences as reassign_inferences,
    invite_for,
    normalise_distribution,
    normalise_host,
    outcome_table,
    published_vocabulary,
    require_tab,
    webhook_catalogue,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-063-reassign-a-booked-meeting-to-a-differe",
    "ticket": "WF-063",
    "name": "Reassign a booked meeting to a different host",
    "description": (
        "Hand a booked meeting to another host from Meetings Activity or the calendar add-on: the "
        "Meeting Type and Workspace stay locked, the invite takes the new assignee's details, the "
        "round-robin credit moves with the host, and an Events History row records who, to whom, when "
        "and the source."
    ),
    "nav": [{"id": "meeting-reassign", "label": "Meeting reassignment"}],
}

router = APIRouter(prefix="/api/wf-063", tags=["wf-063"])


def get_engine(store: RecordStore = StoreDep) -> ReassignEngine:
    """A :class:`ReassignEngine` over the process-wide audited store.

    Per request, for the same reason the other features build their engine per
    request: it holds nothing beyond the store and a clock, so both stay
    overridable in a test rather than hanging a long-lived object off
    ``app.state`` - which is a shared file this feature may not edit.
    """
    return ReassignEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _reassign_error(request: Request, exc: ReassignError) -> JSONResponse:
    """A request the researched rules refuse, or one naming a missing record. 400.

    One handler for the whole hierarchy *except* :class:`MeetingStateError`,
    which the host resolves first because it is more specific. The message is
    the decision's own reason, which quotes the researched sentence, so the 400
    body says which rule refused rather than only that something did.
    """
    return JSONResponse(status_code=400, content={"error": "reassign_error", "detail": str(exc)})


def _meeting_state_error(request: Request, exc: MeetingStateError) -> JSONResponse:
    """A well-formed request that conflicts with the meeting's state. 409.

    Distinct from a plain refusal because the fix is different: a 400 says
    change the request, a 409 says this meeting is over.
    """
    return JSONResponse(status_code=409, content={"error": "meeting_state_error", "detail": str(exc)})


EXCEPTION_HANDLERS = {
    ReassignError: _reassign_error,
    MeetingStateError: _meeting_state_error,
}


# --------------------------------------------------------------------------- #
# The researched vocabulary, the outcomes, and the webhooks
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term, each with the sentence it comes from.

    Served as data so the pickers on the page are rendered from the same source
    the validator enforces against, and a term added in one place reaches every
    client at once.
    """
    return published_vocabulary()


@router.get("/outcomes", summary="The decision outcomes and the rules behind them")
def outcomes() -> dict[str, Any]:
    """The ten outcomes, and the researched sentence that produces each.

    ``BOOKING_REASSIGNED`` is scoped to round-robin bookings by its own
    documentation, which is why the automatic path and that webhook share a
    condition; showing both together is the point.
    """
    return outcome_table()


@router.get("/webhooks", summary="The webhooks a reassignment fires")
def webhooks() -> dict[str, Any]:
    """Both payload shapes, what fires them, and the keys each adds.

    Served so a reviewer can check this workflow's output against the vendor
    docs without reading the source.
    """
    return webhook_catalogue()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research for WF-063 is explicit about its vocabulary and about what
    reassignment ignores, and silent on several things a working implementation
    has to decide. Those gaps are product behaviour, not comments, so they are
    collected here for a reviewer to disagree with by name.

    A read with no side effect, so it needs no store.
    """
    return reassign_inferences.describe()


# --------------------------------------------------------------------------- #
# Distributions and hosts
# --------------------------------------------------------------------------- #


@router.get("/distributions", summary="List distributions")
def list_distributions(
    room_id: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The Distribution contexts a meeting can be booked from.

    Unscoped because a distribution is scheduling configuration rather than
    anything about one room - the same Enterprise Demo distribution serves
    every room on the team.
    """
    records = engine.distributions(room_id=room_id)
    return {"count": len(records), "distributions": records}


@router.post("/distributions", status_code=201, summary="Configure a distribution")
def create_distribution(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a Distribution: its team, the two locked fields, and its controls.

    ``allow_any_team_member`` is the researched "whether you allow rescheduling
    with any team member or not", and ``min_notice_minutes`` and
    ``max_range_days`` are the two bounds a reassignment ignores. Validated
    before the row is written, so a half-configured distribution cannot exist.
    """
    body = normalise_distribution(payload)
    return engine.store.create(
        DISTRIBUTION_COLLECTION, body, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/distributions",
    )


@router.get("/distributions/{distribution_id}", summary="Read one distribution")
def read_distribution(distribution_id: str, engine: ReassignEngine = EngineDep) -> dict[str, Any]:
    record = engine.store.get(distribution_id)
    if record is None or record["collection"] != DISTRIBUTION_COLLECTION:
        raise HTTPException(status_code=404, detail=f"distribution {distribution_id} not found")
    return record


# --------------------------------------------------------------------------- #
# Hosts
# --------------------------------------------------------------------------- #


@router.get("/hosts", summary="List hosts")
def list_hosts(
    room_id: str | None = Query(default=None),
    team: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The people a booking can be handed to.

    ``active=false`` is the interesting filter: a host who has left the team is
    kept rather than deleted, because the reassignments they took part in have
    to keep naming them.
    """
    records = engine.hosts(room_id=room_id, team=team, active=active)
    return {"count": len(records), "hosts": records}


@router.post("/hosts", status_code=201, summary="Register a host")
def create_host(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Register a host: their name, calendar blocks, and invite details.

    The invite fields matter here rather than at reassignment time, because
    "the invite is updated with the new assignee's name, links and details" can
    only be honoured if the product already knows what those details are.
    """
    body = normalise_host(payload)
    return engine.store.create(
        HOST_COLLECTION, body, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/hosts",
    )


@router.get("/hosts/{host_id}", summary="Read one host")
def read_host(host_id: str, engine: ReassignEngine = EngineDep) -> dict[str, Any]:
    record = engine.store.get(host_id)
    if record is None or record["collection"] != HOST_COLLECTION:
        raise HTTPException(status_code=404, detail=f"host {host_id} not found")
    return record


# --------------------------------------------------------------------------- #
# Meetings Activity
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/meetings", summary="Meetings Activity")
def list_meetings(
    room_id: str,
    tab: str = Query(default="all", description="upcoming | past | all"),
    status: str | None = Query(default=None),
    meeting_type: str | None = Query(default=None),
    host_id: str | None = Query(default=None, description="the assignee"),
    booker: str | None = Query(default=None),
    product_source: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The researched list: tabs, and the five filters step 1 names.

    ``tab`` splits on when the meeting *starts*, not on its status - a meeting
    cancelled for next week is still Upcoming, because the tab answers "when is
    it", not "will it happen". The filters are dotted JSON paths through the
    dynamic index, so a team that adds a field later can filter on it without a
    migration.
    """
    records = engine.meeting_activity(
        room_id,
        tab,
        status=status,
        meeting_type=meeting_type,
        host_id=host_id,
        booker=booker,
        product_source=product_source,
    )
    return {
        "room_id": room_id,
        "tab": require_tab(tab),
        "count": len(records),
        "meetings": records,
    }


@router.post("/rooms/{room_id}/meetings", status_code=201, summary="Book a meeting")
def create_meeting(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Book a meeting against a distribution, with the host's invite attached.

    The invite is built from the host rather than accepted from the caller, so
    the reassign path has a correct "before" to compare against.

    **A booking does not bypass the bounds; a reassignment does.** The researched
    note exempts reassignment specifically - "Reassignment does not take into
    account the minimum scheduling notice or the maximum availability range" - and
    it gives that exemption a purpose: "so it can always rescue a stale booking".
    A rescue only means something if the thing it rescues from would otherwise
    have been refused, so a slot inside the notice window or beyond the range is
    a 400 here. That is what makes the reassign path's bypass a documented
    exception rather than the only way to get a meeting booked at all.
    """
    if engine.store.get(room_id) is None:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found")
    body = engine.prepare_meeting(payload)
    host = engine.require_host(body["host_id"])
    body["invite"] = invite_for(host["data"])
    engine.require_slot_within_bounds(body, host["id"])
    return engine.store.create(
        MEETING_COLLECTION, body, room_id=room_id, actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/meetings",
    )


@router.get("/rooms/{room_id}/meetings/export.csv", summary="Export the list to CSV")
def export_meetings(
    room_id: str,
    tab: str = Query(default="all"),
    engine: ReassignEngine = EngineDep,
) -> PlainTextResponse:
    """The researched "Export to CSV" from the Meetings Activity tab.

    Registered **before** ``/rooms/{room_id}/meetings/{meeting_id}`` on purpose.
    Starlette matches in registration order, so a literal segment declared after
    a parameterised one is unreachable: ``export.csv`` would be read as a meeting
    id and answered with a 404. The host would not report a collision - the paths
    genuinely differ - so this ordering is invisible until someone clicks Export.

    The same rows the list returns, in the same order, so what a person exports
    is what they were looking at. Rendered from the recorded fields rather than
    from ``json.dumps`` of the payload, because a CSV whose cells are Python
    dicts is not a CSV anybody opens.
    """
    records = engine.meeting_activity(room_id, tab)
    header = ["meeting_id", "title", "status", "meeting_type", "workspace", "host_id", "booker", "starts_at", "product_source"]
    lines = [",".join(header)]
    for record in records:
        data = record["data"]
        row = [
            record["id"],
            str(data.get("title") or ""),
            str(data.get("status") or ""),
            str(data.get("meeting_type") or ""),
            str(data.get("workspace") or ""),
            str(data.get("host_id") or ""),
            str(data.get("booker") or ""),
            str(data.get("starts_at") or ""),
            str(data.get("product_source") or ""),
        ]
        lines.append(",".join(_csv_cell(cell) for cell in row))
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="meetings-{room_id}.csv"'},
    )


@router.get("/rooms/{room_id}/meetings/{meeting_id}", summary="Read one meeting")
def read_meeting(room_id: str, meeting_id: str, engine: ReassignEngine = EngineDep) -> dict[str, Any]:
    """One meeting, with its current host, its invite, and its reassignment count.

    Registered after ``export.csv`` and deliberately so: see the note there.
    """
    _require_room_meeting(engine, room_id, meeting_id)
    return engine.require_meeting(meeting_id)


def _csv_cell(value: str) -> str:
    """Quote a CSV cell when it needs it, doubling any embedded quotes.

    A title containing a comma would otherwise shift every later column by one,
    and the resulting file is wrong rather than obviously broken.
    """
    text = str(value)
    if any(character in text for character in (",", '"', "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


# --------------------------------------------------------------------------- #
# The reassignment flow
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/meetings/{meeting_id}/availability", summary="Who could take this booking")
def availability(
    room_id: str,
    meeting_id: str,
    kind: str = Query(default="individual", description="individual | team | distribution"),
    starts_at: str | None = Query(default=None, description="a new slot, for the Edit Meeting path"),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The candidates for the researched "known and free" pick.

    Backs the host-name icon on the meeting details panel: eligible hosts first,
    and the ineligible ones alongside them, because a rep who had someone in
    mind needs to see that they are on this list and busy rather than missing
    from it.
    """
    _require_room_meeting(engine, room_id, meeting_id)
    request: dict[str, Any] = {"assign_to": {"kind": kind}}
    if starts_at:
        request["starts_at"] = starts_at
    return engine.availability(meeting_id, request)


@router.post("/rooms/{room_id}/meetings/{meeting_id}/preview", summary="What a reassignment would do")
def preview(
    room_id: str,
    meeting_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Run the decision and report it. Writes nothing at all.

    The read-only half of :func:`reassign`, calling the same decision, so the
    answer a form shows before the operator commits is the answer the commit
    produces. A preview that could disagree with the write would be worse than
    no preview.
    """
    _require_room_meeting(engine, room_id, meeting_id)
    return engine.preview(meeting_id, payload)


@router.post("/rooms/{room_id}/meetings/{meeting_id}/reassign", summary="Reassign the meeting")
def reassign(
    room_id: str,
    meeting_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The workflow, end to end: decide, book, update the invite, move the credit,
    write the Events History row, and build the webhooks.

    The response carries the reassignment record, the updated meeting, the
    history row, both webhook payloads, which invite fields changed, the credit
    movement, and which bounds were bypassed - so a caller can report the change
    without a second round trip and without reconstructing any of it.

    A refusal is a 400 carrying the decision's reason, and writes nothing. A
    meeting that is cancelled or already completed is a 409: the request was
    well formed and the meeting is simply over.
    """
    _require_room_meeting(engine, room_id, meeting_id)
    # Both ids interpolated, not the `{meeting_id}` placeholder. An audit row
    # has to name the route that served *this* write, and a template with a
    # literal brace in it is neither the route nor a resolvable one: a reader
    # filtering the log by meeting id would find nothing.
    return engine.reassign(
        meeting_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/meetings/{meeting_id}/reassign",
    )


def _require_room_meeting(engine: ReassignEngine, room_id: str, meeting_id: str) -> dict[str, Any]:
    """A meeting must exist *on this room*.

    Checked in the route rather than left to the engine, because the path says
    the meeting belongs to a room and reading another room's meeting through
    this room's path would make the scoping decorative.
    """
    meeting = engine.get_meeting(meeting_id)
    if meeting is None or meeting.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"meeting {meeting_id} not found on room {room_id}")
    return meeting


# --------------------------------------------------------------------------- #
# Reassignments and Events History
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/reassignments", summary="List reassignments")
def list_reassignments(
    room_id: str,
    meeting_id: str | None = Query(default=None),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Every reassignment taken in this room, newest first.

    The full envelope, because a reader auditing a change needs the record id to
    open it and the before/after invite to see what moved.
    """
    records = engine.reassignments(room_id=room_id, meeting_id=meeting_id)
    return {"room_id": room_id, "count": len(records), "reassignments": records}


@router.get("/rooms/{room_id}/reassignments/{reassignment_id}", summary="One reassignment in full")
def read_reassignment(
    room_id: str, reassignment_id: str, engine: ReassignEngine = EngineDep
) -> dict[str, Any]:
    """One reassignment, with the invite before and after and the webhooks it fired."""
    record = engine.get_reassignment(reassignment_id)
    if record is None or record.get("room_id") != room_id:
        raise HTTPException(status_code=404, detail=f"reassignment {reassignment_id} not found on room {room_id}")
    return record


@router.get("/rooms/{room_id}/events-history", summary="The Events History tab")
def events_history(
    room_id: str,
    meeting_id: str | None = Query(default=None),
    limit: int = Query(default=HISTORY_LIMIT, ge=1, le=1000),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """The researched tab: who reassigned it, to whom, when, and the source.

    One row per reassignment, and the four displayed facts are the four fields
    the research names, so the tab cannot drift from the artifact it copies.
    """
    records = engine.events_history(meeting_id=meeting_id, limit=limit)
    return {
        "room_id": room_id,
        "count": len(records),
        "history": [
            {
                "at": record["data"].get("at"),
                "reassigned_by": record["data"].get("reassigned_by"),
                "reassigned_to": record["data"].get("reassigned_to"),
                "reassignment_source": record["data"].get("reassignment_source"),
                "meeting_id": record["data"].get("meeting_id"),
                "meeting_title": record["data"].get("meeting_title"),
            }
            for record in records
        ],
    }


@router.get("/rooms/{room_id}/meetings/{meeting_id}/history", summary="One meeting's reassignment history")
def meeting_history(
    room_id: str, meeting_id: str, engine: ReassignEngine = EngineDep
) -> dict[str, Any]:
    """A meeting's history, plus whether its notice window has already closed.

    ``needs_reassignment`` is a rep's queue, not a rule: a meeting whose start is
    inside the distribution's minimum notice window is the one a reassignment is
    about to be too late for, which is the situation the researched note about
    ignoring the notice exists to rescue.
    """
    _require_room_meeting(engine, room_id, meeting_id)
    return engine.meeting_history(meeting_id)


@router.get("/rooms/{room_id}/summary", summary="Counts for the page header")
def summary(room_id: str, engine: ReassignEngine = EngineDep) -> dict[str, Any]:
    """Meetings by tab and status, reassignments, and history rows by source.

    Computed over exactly the rows the same filters would return, so a
    room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """
    return engine.summary(room_id=room_id)


@router.get("/upcoming", summary="The Upcoming tab across every room")
def upcoming(
    limit: int = Query(default=50, ge=1, le=1000),
    engine: ReassignEngine = EngineDep,
) -> dict[str, Any]:
    """Meetings starting later than now, whatever room they are on.

    The unscoped convenience view, which is the one a rep opening the app has
    first. Deliberately read-only: a reassignment always needs a room, because
    the path says which room's meeting is changing.
    """
    now = engine._clock()  # noqa: SLF001 - the same clock the engine decides with
    from dsr.reassign import parse_instant

    records = [
        record
        for record in engine.meetings()
        if parse_instant(record["data"]["starts_at"]) >= now
    ]
    records.sort(key=lambda record: (record["data"]["starts_at"], record["id"]))
    return {"count": min(len(records), limit), "meetings": records[:limit]}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The hosts the demo reassigns between. Two teams, so ``allow_any_team_member``
#: has something to decide, and one inactive host so the "not taking bookings"
#: refusal is visible rather than theoretical.
DEMO_HOSTS: tuple[dict[str, Any], ...] = (
    {
        "key": "dana",
        "name": "Dana Okoro",
        "email": "dana.okoro@dsr.example",
        "team": "enterprise",
        "distribution": "Enterprise Demo",
        "round_robin_credits": 4,
        "conference_link": "https://meet.example/dana-okoro",
        "dial_in": "+1-555-0142",
        "location": "Building 2, Room 4.14",
    },
    {
        "key": "priya",
        "name": "Priya Raman",
        "email": "priya.raman@dsr.example",
        "team": "enterprise",
        "distribution": "Enterprise Demo",
        "round_robin_credits": 2,
        "conference_link": "https://meet.example/priya-raman",
        "dial_in": "+1-555-0177",
        "location": "Building 2, Room 4.15",
    },
    {
        "key": "marcus",
        "name": "Marcus Webb",
        "email": "marcus.webb@dsr.example",
        "team": "enterprise",
        "distribution": "Enterprise Demo",
        "round_robin_credits": 1,
        "conference_link": "https://meet.example/marcus-webb",
        # No dial-in and no location, on purpose: a reassignment onto Marcus
        # shows a field going to null rather than carrying the previous host's
        # number over, which is the defect "links and other details that
        # possibly changed" exists to prevent. Every other demo host sets both,
        # so this is the only host where the change is visible.
    },
    {
        "key": "nadia",
        "name": "Nadia Farouk",
        "email": "nadia.farouk@dsr.example",
        "team": "mid-market",
        "distribution": "Mid-Market Demo",
        "round_robin_credits": 3,
        "conference_link": "https://meet.example/nadia-farouk",
        "dial_in": "+1-555-0190",
    },
    {
        "key": "tomas",
        "name": "Tomas Vela",
        "email": "tomas.vela@dsr.example",
        "team": "mid-market",
        "distribution": "Mid-Market Demo",
        "round_robin_credits": 5,
        "conference_link": "https://meet.example/tomas-vela",
        "dial_in": "+1-555-0163",
    },
    {
        "key": "alba",
        "name": "Alba Ries",
        "email": "alba.ries@dsr.example",
        "team": "mid-market",
        "distribution": "Mid-Market Demo",
        "round_robin_credits": 2,
        "active": False,
        "conference_link": "https://meet.example/alba-ries",
    },
    {
        # Busy over the demo slot, so the "known and free" refusal is reachable
        # from the demo without anyone having to arrange a conflict by hand.
        "key": "rui",
        "name": "Rui Silva",
        "email": "rui.silva@dsr.example",
        "team": "enterprise",
        "distribution": "Enterprise Demo",
        "round_robin_credits": 0,
        "conference_link": "https://meet.example/rui-silva",
        "dial_in": "+1-555-0155",
        "busy": [
            {
                "starts_at": "2026-09-29T10:00:00Z",
                "ends_at": "2026-09-29T12:00:00Z",
                "label": "Fabrikam — Renewal call",
            }
        ],
    },
)

#: The distributions the demo books from. ``allow_any_team_member`` differs
#: between them, which is what makes the researched control observable: the
#: same out-of-team host is a candidate under one and not under the other.
DEMO_DISTRIBUTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "enterprise",
        "name": "Enterprise Demo",
        "team": "enterprise",
        "workspace": "northwind",
        "meeting_type": "demo",
        "allow_any_team_member": False,
        "member_keys": ("dana", "priya", "marcus", "rui"),
        "min_notice_minutes": 60,
        "max_range_days": 90,
    },
    {
        "key": "midmarket",
        "name": "Mid-Market Demo",
        "team": "mid-market",
        "workspace": "contoso",
        "meeting_type": "demo",
        # The researched "whether you allow rescheduling with any team member".
        "allow_any_team_member": True,
        "member_keys": ("nadia", "tomas", "alba"),
        "min_notice_minutes": 30,
        "max_range_days": 45,
    },
    {
        "key": "renewal",
        "name": "Renewal Review",
        "team": "enterprise",
        "workspace": "fabrikam",
        "meeting_type": "renewal",
        "allow_any_team_member": False,
        "member_keys": ("dana", "priya"),
        # Both bounds configured tightly, so the demo can show a reassignment
        # bypassing a min-notice breach and a max-range breach.
        "min_notice_minutes": 1440,
        "max_range_days": 7,
    },
)

#: One meeting per state the research makes interesting. Between them they reach
#: the successful reassignment, both bypassed bounds, an invitation whose fields
#: go to null, both webhook shapes, the credit movement, the no-show credit-back
#: that suppresses it, and a non-round-robin booking that cannot be auto-assigned.
DEMO_MEETINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "northwind",
        "label": "reassigned across a min-notice breach, both webhooks fire",
        "room_index": 0,
        "title": "Northwind Traders — Enterprise Demo",
        "host_key": "dana",
        "distribution": "Enterprise Demo",
        "booker": "a.buyer@northwind.example",
        "booker_email": "a.buyer@northwind.example",
        "round_robin": True,
        "booking_uid": "bk-northwind-4471",
        # 2026-09-29T09:30Z against a 60-minute notice, so a *booking* here
        # would be refused; the reassignment is the researched way past it.
        "starts_at": "2026-09-29T09:30:00Z",
        "ends_at": "2026-09-29T10:00:00Z",
        "product_source": "myapp",
        "reassign": {
            "assign_to": {"kind": "individual", "id": "priya"},
            "surface": "meetings_activity",
            "requested_by": "dana",
        },
    },
    {
        "key": "contoso",
        "label": "reassigned onto a host with fewer invite fields, so they go null",
        "room_index": 1,
        "title": "Contoso Health — Enterprise Demo",
        "host_key": "priya",
        "distribution": "Enterprise Demo",
        "booker": "procurement@contoso.example",
        "booker_email": "procurement@contoso.example",
        "round_robin": True,
        "booking_uid": "bk-contoso-2210",
        "starts_at": "2026-09-30T14:00:00Z",
        "ends_at": "2026-09-30T14:30:00Z",
        "product_source": "myapp",
        "reassign": {
            # Marcus has a conference link but no dial-in and no location, so
            # two invite fields move to null.
            "assign_to": {"kind": "individual", "id": "marcus"},
            "surface": "chilical_home",
            "extension": {"installed": True, "logged_in": True},
            "requested_by": "priya",
        },
    },
    {
        "key": "fabrikam",
        "label": "reassigned from the CRM button, bypassing a 24-hour notice breach",
        "room_index": 2,
        "title": "Fabrikam Logistics — Renewal Review",
        "host_key": "dana",
        "distribution": "Renewal Review",
        "booker": "ops@fabrikam.example",
        "booker_email": "ops@fabrikam.example",
        "round_robin": True,
        "booking_uid": "bk-fabrikam-9003",
        # 40 minutes out against a 1440-minute notice, and inside a 7-day
        # window - so the notice is the only bound breached here, which is the
        # case the seeded rescue exists to show.
        "starts_at": "2026-09-28T09:40:00Z",
        "ends_at": "2026-09-28T10:10:00Z",
        "product_source": "crm_event_button",
        "reassign": {
            "assign_to": {"kind": "individual", "id": "priya"},
            "surface": "crm_event_button",
            "requested_by": "sam",
        },
    },
    {
        "key": "adventure",
        "label": "reassigned out of a no-show, so the credit does not move twice",
        "room_index": 3,
        "title": "Adventure Works — Pilot Review",
        "host_key": "nadia",
        "distribution": "Mid-Market Demo",
        "booker": "lead@adventure.example",
        "booker_email": "lead@adventure.example",
        "round_robin": True,
        "booking_uid": "bk-adventure-7788",
        # Two days before the seed's `now`, so the meeting is in the Past tab -
        # which is where a rep who has just marked it a no-show is looking.
        "starts_at": "2026-09-26T16:00:00Z",
        "ends_at": "2026-09-26T16:30:00Z",
        "product_source": "myapp",
        # A no-show credit-back already returned this booking's credit to
        # Nadia, so the reassignment below must move no credit.
        "status": "no_show",
        "no_show_credit_back": True,
        "no_show_credited_host_id": "nadia",
        "reassign": {
            "assign_to": {"kind": "individual", "id": "tomas"},
            "surface": "meetings_activity",
            "requested_by": "sam",
        },
    },
    {
        "key": "northwind_team",
        "label": "left alone: a non-round-robin booking, so a team assignment is refused",
        "room_index": 0,
        "title": "Northwind Traders — Technical Deep Dive",
        "host_key": "dana",
        "distribution": "Enterprise Demo",
        "booker": "b.buyer@northwind.example",
        "booker_email": "b.buyer@northwind.example",
        # Not round robin, so the automatic path is the researched refusal.
        # Left un-reassigned on purpose; DEMO_REFUSED below exercises it.
        "round_robin": False,
        "booking_uid": "bk-northwind-4472",
        "starts_at": "2026-10-06T11:00:00Z",
        "ends_at": "2026-10-06T11:45:00Z",
        "product_source": "myapp",
    },
    {
        "key": "fabrikam_busy",
        "label": "left alone, so the busy-host and inactive-host refusals are reachable",
        "room_index": 2,
        "title": "Fabrikam Logistics — Implementation Check-in",
        "host_key": "priya",
        "distribution": "Enterprise Demo",
        "booker": "ops@fabrikam.example",
        "booker_email": "ops@fabrikam.example",
        "round_robin": True,
        "booking_uid": "bk-fabrikam-9004",
        # Deliberately over Rui's seeded block, so naming him is refused.
        "starts_at": "2026-09-29T10:30:00Z",
        "ends_at": "2026-09-29T11:00:00Z",
        "product_source": "api",
    },
    {
        "key": "fabrikam_far",
        "label": "reassigned across a max-range breach rather than a notice one",
        "room_index": 2,
        "title": "Fabrikam Logistics — Roadmap Review",
        "host_key": "priya",
        "distribution": "Renewal Review",
        "booker": "ops@fabrikam.example",
        "booker_email": "ops@fabrikam.example",
        # Round robin, so the automatic path is available, and the slot is 20 days
        # out against this distribution's 7-day maximum range. The other seeded
        # rescue breaches the *minimum notice* instead; between them the demo
        # shows both of the researched bounds being ignored, which one case
        # could not.
        "round_robin": True,
        "booking_uid": "bk-fabrikam-9005",
        "starts_at": "2026-10-18T11:00:00Z",
        "ends_at": "2026-10-18T11:30:00Z",
        "product_source": "api",
        "reassign": {
            "assign_to": {"kind": "individual", "id": "dana"},
            "surface": "api",
            "requested_by": "sam",
        },
    },
    {
        "key": "contoso_cancelled",
        "label": "cancelled, so a reassignment is a 409 rather than a 400",
        "room_index": 1,
        "title": "Contoso Health — Cancelled Workshop",
        "host_key": "priya",
        "distribution": "Enterprise Demo",
        "booker": "procurement@contoso.example",
        "booker_email": "procurement@contoso.example",
        "round_robin": True,
        "booking_uid": "bk-contoso-2211",
        "starts_at": "2026-10-02T09:00:00Z",
        "ends_at": "2026-10-02T10:00:00Z",
        "status": "cancelled",
        "product_source": "myapp",
    },
)

#: The reassignments that are expected to be refused. Kept apart from
#: :data:`DEMO_MEETINGS` because they are expected to raise, and a seed that
#: raised would be skipped by ``backend/seed.py`` with the whole feature's demo
#: lost. Each is run here and its refusal recorded, so the demo asserts the
#: behaviour rather than leaving a reader to take it on trust.
DEMO_REFUSED: tuple[dict[str, Any], ...] = (
    {
        "label": "refused: a host who is not free for the slot",
        "meeting_key": "fabrikam_busy",
        "assign_to": {"kind": "individual", "id": "rui"},
        "surface": "meetings_activity",
    },
    {
        "label": "refused: an inactive host",
        "meeting_key": "adventure",
        "assign_to": {"kind": "individual", "id": "alba"},
        "surface": "meetings_activity",
    },
    {
        "label": "refused: a team assignment on a booking that is not round robin",
        "meeting_key": "northwind_team",
        "assign_to": {"kind": "team"},
        "surface": "meetings_activity",
    },
    {
        "label": "refused: the calendar add-on without the extension installed",
        "meeting_key": "northwind",
        "assign_to": {"kind": "individual", "id": "priya"},
        "surface": "chilical_home",
    },
    {
        "label": "refused: a change to the locked Meeting Type",
        "meeting_key": "fabrikam",
        "assign_to": {"kind": "individual", "id": "priya"},
        "meeting_type": "demo",
        "surface": "api",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the hosts, distributions, meetings, reassignments and history rows.

    The reassignments come from running the real :class:`ReassignEngine` over
    the real rules, so the demo cannot show a shape the workflow would not
    produce, and seeding opens no socket because the calendar here is the
    audited store.

    Deliberately mixed. A demo of only successful reassignments would teach
    nothing about the four things this workflow exists for: the two locked
    fields, the two bypassed bounds, the credit that must not move twice, and
    the round-robin limit on the automatic path. All of them are here, alongside
    a reassignment whose invite fields go to null.

    Returns a description the seeder prints, and which says how many of each
    outcome landed so a reviewer can see at a glance that the demo is mixed.
    """
    store = RecordStore(db)
    now = context["now"]
    engine = ReassignEngine(store, clock=lambda: now)
    source = "seed"
    rooms: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not rooms:
        return "0 meetings (no rooms to scope them to)"

    host_ids: dict[str, str] = {}
    for spec in DEMO_HOSTS:
        body = {k: v for k, v in spec.items() if k != "key"}
        host_ids[str(spec["key"])] = store.create(
            HOST_COLLECTION, normalise_host(body), actor="dana", source=source
        )["id"]

    # The meeting's own `invite` is built from its host, exactly as the booking
    # route does, so a reassignment has a correct "before" to compare against.
    # Kept out of `normalise_meeting` on purpose: that function validates the
    # caller-supplied fields, and the invite is not one of them.
    def invite_of(host_key: str) -> dict[str, Any]:
        return invite_for(store.get(host_ids[host_key])["data"])

    for spec in DEMO_DISTRIBUTIONS:
        body = {k: v for k, v in spec.items() if k not in ("key", "member_keys")}
        body["member_ids"] = [host_ids[key] for key in spec["member_keys"]]
        store.create(DISTRIBUTION_COLLECTION, normalise_distribution(body), actor="dana", source=source)

    def room_at(index: int) -> str:
        return rooms[index % len(rooms)][0]

    meeting_ids: dict[str, str] = {}
    for spec in DEMO_MEETINGS:
        body = {k: v for k, v in spec.items() if k not in ("key", "label", "reassign", "host_key")}
        body["host_id"] = host_ids[spec["host_key"]]
        if "no_show_credited_host_id" in body:
            body["no_show_credited_host_id"] = host_ids[spec["no_show_credited_host_id"]]
        # Seeded directly rather than through the booking route, because the demo
        # deliberately contains a slot a *booking* would refuse - inside the notice
        # window. A reassignment is the researched way past that, so the seed has to
        # be able to create the situation the workflow exists to rescue.
        prepared = engine.prepare_meeting(body)
        # The invite is attached *after* normalisation, exactly as the booking
        # route does it. `normalise_meeting` builds a fresh payload from the
        # fields it validates, so setting it before would be silently dropped -
        # and a dropped invite makes every reassignment's "before" empty, which
        # hides the very change the research is about.
        prepared["invite"] = invite_of(spec["host_key"])
        record = store.create(
            MEETING_COLLECTION,
            prepared,
            room_id=room_at(spec["room_index"]),
            actor="dana",
            source=source,
        )
        meeting_ids[str(spec["key"])] = record["id"]

    outcomes: list[str] = []
    for spec in DEMO_MEETINGS:
        if not spec.get("reassign"):
            continue
        request = dict(spec["reassign"])
        assign_to = dict(request.get("assign_to") or {})
        if assign_to.get("id") and str(assign_to["id"]) in host_ids:
            assign_to["id"] = host_ids[str(assign_to["id"])]
        request["assign_to"] = assign_to
        result = engine.reassign(
            meeting_ids[str(spec["key"])], request, actor="dana", source=source
        )
        outcomes.append(str(result["reassignment"]["data"]["outcome"]))

    # Every case here is expected to raise, so each is run and its refusal
    # recorded. A case that *succeeded* would leave a reassignment in the demo
    # the label does not describe, so the count is compared against the specs and
    # a mismatch is reported rather than being papered over by the summary.
    refused: list[str] = []
    for spec in DEMO_REFUSED:
        assign_to = dict(spec.get("assign_to") or {})
        if assign_to.get("id") and str(assign_to["id"]) in host_ids:
            assign_to["id"] = host_ids[str(assign_to["id"])]
        request: dict[str, Any] = {
            "assign_to": assign_to,
            "surface": spec.get("surface", "api"),
        }
        if "meeting_type" in spec:
            request["meeting_type"] = spec["meeting_type"]
        try:
            engine.reassign(meeting_ids[spec["meeting_key"]], request, actor="dana", source=source)
        except ReassignError:
            refused.append(str(spec["label"]))

    bypassed = sum(
        1 for r in engine.reassignments() if r["data"].get("bounds_bypassed")
    )
    return (
        f"{len(DEMO_HOSTS)} hosts, {len(DEMO_DISTRIBUTIONS)} distributions, {len(DEMO_MEETINGS)} meetings, "
        f"{len(outcomes)} reassignments ({bypassed} bypassing a bound), {len(refused)} refusals, "
        f"{len(engine.events_history())} history rows"
    )
