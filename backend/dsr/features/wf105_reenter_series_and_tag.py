"""WF-105: re-enter a recurring nurture series and tag the contact on completion.

The researched workflow, in full. A contact matches the starting rule block's
filters again when a recurring event fires; if **Re-enter Series** is enabled the
contact is re-enrolled into the live series; the contact traverses the path; the
tag block writes an existing workspace Tag onto the Contact; and a sticky exit
rule removes a contact for good, so a later qualifying event cannot re-enrol it.

The domain logic is in :mod:`dsr.automation_engagement.series`, which this module
does not own. What lives here is the three things a workflow has to take out of
shared files: the HTTP surface, the mapping from domain errors to responses, and
the demo data.

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf105``.** A ticket-derived prefix cannot collide with a
feature-shaped one by construction, and the host refuses a colliding
``(method, path)`` rather than shadowing it.

**``source=`` comes from the route.** Every write below passes a string built
from ``router.prefix``, so the audit row and the route table cannot drift. A
hardcoded string inside a domain method is a defect, and the same class of bug
has shipped in this codebase before: a feature's audit log kept naming a path the
app had stopped serving. ``source`` is a *required* keyword on every writing
method of :class:`~dsr.automation_engagement.series.engine.SeriesEngine`, so
omitting it is a ``TypeError`` at the call site rather than an untraceable row in
production.

**One handler for the whole error hierarchy.**
:class:`~dsr.automation_engagement.series.errors.SeriesError` is the base of every
refusal in the package, and each subclass carries its own ``status`` and ``code``,
so one handler answers 400 for a malformed payload, 404 for a missing series, and
409 for a series that is already live - which are not the same kind of problem.

**A skip is an outcome, not an error.** The poll records a contact that does not
match, or that has exited, rather than raising. A data-driven automation skips far
more often than it enrols, and turning a skip into an HTTP error would make a
correctly-behaving series look broken.

**The Goal tab is a second filter.** The starting rule block decides entry. The
Goal tab's outcome filter and match window decide whether a completed contact
achieved the outcome, and they are separate keys in the record.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.automation_engagement.series import (
    SeriesEngine,
    SeriesError,
    SeriesRefused,
    inferences as series_inferences,
    published_vocabulary,
)
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-105-reenter-series-and-tag",
    "ticket": "WF-105",
    "name": "Re-enter a recurring nurture series and tag the contact",
    "description": (
        "A live nurture series re-enrols a contact that matches its starting rule block again "
        "when a recurring event fires, provided Re-enter Series is on and the contact has not "
        "exited. The contact traverses the path and a tag block writes an existing workspace tag "
        "onto the contact, which other series, segments and reports can then use as an entry or "
        "exit condition. A sticky exit rule removes a contact permanently."
    ),
    "nav": [{"id": "reenter-series-and-tag", "label": "Nurture series"}],
}

router = APIRouter(prefix="/api/wf105", tags=["wf105"])

PREFIX = router.prefix


def get_engine(store: RecordStore = StoreDep) -> SeriesEngine:
    """A :class:`SeriesEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle and a clock, and an ``app.state`` entry is exactly the edit
    to the shared ``dsr/api.py`` that the feature host exists to make
    unnecessary. Building it here also leaves the clock a plain constructor
    argument, which is what lets a test drive the whole workflow with rows of its
    own and a clock it controls.
    """

    return SeriesEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _series_error(request: Request, exc: SeriesError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. The status rides on the exception rather
    than being decided here, because a malformed request, a missing row and a
    conflict with state that already exists are different problems and answering
    all three 400 would hide two of them.
    """

    to_dict = getattr(exc, "to_dict", None)
    body = to_dict() if callable(to_dict) else {"error": exc.code, "detail": str(exc)}
    return JSONResponse(status_code=getattr(exc, "status", 400), content=body)


EXCEPTION_HANDLERS = {SeriesError: _series_error}


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="The researched vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term: the poll cadence, the re-entry toggle, the tag block,
    the sticky exit, the Goal window, and the tag webhook topics.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a term added in one place reaches every
    client at once.
    """

    return published_vocabulary()


@router.get("/inferences", summary="Every judgement call this build makes")
def inferences() -> dict[str, Any]:
    """What the research leaves open, what this build chose, and how to change it.

    The research is precise about re-entry, the sticky exit, the tag block and the
    Goal tab. It is silent about how the poll runs, how re-entry is stored, and
    several smaller things. Those gaps are product behaviour rather than comments,
    so they are collected here for a reviewer to disagree with by name. A read
    with no side effect, so it needs no store.
    """

    return series_inferences.describe()


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Series, tags, enrollments by state, tag applications and evaluations.

    The summary is computed over exactly the rows the same filters would return,
    so a room-scoped total above an unscoped list cannot be misread as a
    product-wide one.
    """

    return engine.summary(room_id=room_id)


@router.get("/catalog", summary="The series, tags and enrollments the poll reads")
def catalog(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Everything a reviewer needs to understand why a contact was enrolled."""

    return engine.catalog(room_id=room_id, limit=limit)


# --------------------------------------------------------------------------- #
# Tags: the shared workspace vocabulary
# --------------------------------------------------------------------------- #


@router.get("/tags", summary="List the workspace tag vocabulary")
def list_tags(
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """The tags a tag block may select. A tag block cannot create one."""

    records = engine.tags(limit=limit)
    return {"count": len(records), "tags": records}


@router.post("/tags", status_code=201, summary="Register a workspace tag")
def create_tag(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Register an existing tag in the workspace vocabulary.

    The tag block selects from this vocabulary, so a tag has to exist before a
    series can name it. A duplicate name is refused rather than silently merged,
    because two tags that print the same are one tag to every reader.
    """

    return engine.create_tag(payload, actor=actor, source=f"POST {PREFIX}/tags")


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #


@router.get("/series", summary="List nurture series")
def list_series(
    room_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="draft | live | paused"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """The declared series, oldest first, each with its tag blocks resolved."""

    records = engine.series(room_id=room_id, state=state, limit=limit)
    return {"count": len(records), "series": [engine.series_view(r) for r in records]}


@router.post("/series", status_code=201, summary="Declare a series")
def create_series(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Declare a series: starting rule block, exit rules, goal, and path.

    Validated in full before the row exists, so a path that names a tag the
    workspace does not have cannot leave a half-configured series behind. The
    series starts in draft; nothing enrols until it is published.
    """

    return engine.create_series(payload, actor=actor, source=f"POST {PREFIX}/series")


@router.get("/series/{series_id}", summary="Read one series")
def read_series(series_id: str, engine: SeriesEngine = EngineDep) -> dict[str, Any]:
    """One series with its tag blocks, block count and filter counts."""

    return engine.series_view(engine.require_series(series_id))


@router.post("/series/{series_id}/publish", summary="Publish a series so it can enrol")
def publish_series(
    series_id: str,
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Move a series to live.

    Publish is what turns a definition into the automation, so the tag blocks are
    resolved against the workspace again here: a tag that left the vocabulary
    between declare and publish is refused rather than published into a block
    that cannot apply it.
    """

    return engine.publish_series(
        series_id, actor=actor, source=f"POST {PREFIX}/series/{{series_id}}/publish"
    )


# --------------------------------------------------------------------------- #
# The poll: entry and progression, on demand
# --------------------------------------------------------------------------- #


@router.post("/series/{series_id}/poll", status_code=201, summary="Poll a live series")
def poll_series(
    series_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Evaluate entry, and progression when an event asks for it, in one poll.

    The research polls a series every 30 minutes. This build runs the poll when
    it is called and records what it decided; the cadence is served on the
    vocabulary route. ``events`` is a list of the recurring domain events the data
    flow names, each carrying a contact. An event with ``advance`` true also moves
    an enrolling contact one block along the path.
    """

    events = payload.get("events")
    if not isinstance(events, list):
        raise SeriesRefused(
            "a poll needs an events list; each event carries a contact",
            {"events": "required"},
        )
    return engine.poll_series(
        series_id, events, actor=actor, source=f"POST {PREFIX}/series/{{series_id}}/poll"
    )


@router.get("/due", summary="The live series whose next poll is due")
def due_series(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """One read that names the work to do, so a scheduler needs no in-process timer.

    The researched cadence lives in the domain: each series carries
    ``last_polled_at`` and ``poll_interval_minutes``, and this route reports which
    live series have passed the interval.
    """

    records = engine.due_series(room_id=room_id, limit=limit)
    return {"count": len(records), "due": records}


@router.get("/series/{series_id}/due", summary="Whether one series is due for its next poll")
def series_due(series_id: str, engine: SeriesEngine = EngineDep) -> dict[str, Any]:
    """Whether one series is due, and when its next poll falls due."""

    return engine.series_due(series_id)


# --------------------------------------------------------------------------- #
# Progression, exit and the reads
# --------------------------------------------------------------------------- #


@router.post(
    "/series/{series_id}/contacts/{contact_id}/advance",
    status_code=201,
    summary="Move an enrollment one block along the path",
)
def advance(
    series_id: str,
    contact_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Advance an active enrollment one block.

    When the block is a tag block the tag is written onto the contact, which is
    the data flow's "the tag block writes a Tag onto the Contact object". Passing
    the contact's current data as ``contact`` lets the Goal tab be evaluated
    against it when the path completes.
    """

    contact = payload.get("contact")
    return engine.advance(
        series_id,
        contact_id,
        contact=contact if isinstance(contact, dict) else None,
        actor=actor,
        source=f"POST {PREFIX}/series/{{series_id}}/contacts/{{contact_id}}/advance",
    )


@router.post(
    "/series/{series_id}/contacts/{contact_id}/exit",
    summary="Record a sticky exit for a contact",
)
def exit_contact(
    series_id: str,
    contact_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Record the researched sticky exit.

    The contact's data must match the exit rules, or the call is refused: the
    exit is permanent, so writing one by mistake cannot be undone by a later
    qualifying event.
    """

    contact = payload.get("contact")
    if not isinstance(contact, dict):
        raise SeriesRefused(
            "an exit needs the contact's data to evaluate the exit rules",
            {"contact": "required"},
        )
    return engine.record_exit(
        series_id,
        contact_id,
        contact,
        actor=actor,
        source=f"POST {PREFIX}/series/{{series_id}}/contacts/{{contact_id}}/exit",
    )


@router.get("/enrollments", summary="List enrollments")
def list_enrollments(
    series_id: str | None = Query(default=None),
    contact_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="enrolled | completed | exited"),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Every enrollment, newest first, filterable by series, contact and state."""

    records = engine.enrollments(
        series_id=series_id, contact_id=contact_id, state=state, limit=limit
    )
    return {"count": len(records), "enrollments": records}


@router.get("/tag-applications", summary="List tags written onto contacts")
def list_tag_applications(
    series_id: str | None = Query(default=None),
    contact_id: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Every tag a tag block wrote onto a contact, newest first.

    The topic the application would publish on is recorded beside it, so an
    external system subscribing to ``contact.user.tag.created`` can see the
    contract even though this build sends no webhook.
    """

    records = engine.tag_applications(
        series_id=series_id, contact_id=contact_id, tag=tag, limit=limit
    )
    return {"count": len(records), "tag_applications": records}


@router.get("/evaluations", summary="List poll evaluations")
def list_evaluations(
    series_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SeriesEngine = EngineDep,
) -> dict[str, Any]:
    """Every poll's recorded decision, newest first.

    A skip is recorded here rather than raised, so a series that matched nobody
    has a trail rather than silence.
    """

    records = engine.evaluations(series_id=series_id, limit=limit)
    return {"count": len(records), "evaluations": records}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The workspace tags the demo series may select. The research is explicit that a
#: tag block cannot create a tag, so these exist before any series names them.
DEMO_TAGS: tuple[str, ...] = ("Upgraded after Series", "ExitUpgradeSeries")


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed one live series, its tags, and the states the research names.

    Deliberately mixed. A demo of only clean enrollments would teach nothing about
    the four states this workflow exists for: a completed contact whose goal was
    met, a contact that re-entered after completing, a contact that exited by the
    sticky exit rule and was then refused re-entry, and a contact that matched
    nothing at all. All four are run here and expected, so the demo asserts the
    behaviour rather than a reader having to take the seed's word for it.

    Every character of the returned string is ASCII, because the seeder prints it
    on a Windows console whose codec is cp1252. A single RIGHTWARDS ARROW in one
    recovered feature broke the entire seeder.
    """

    store = RecordStore(db)
    engine = SeriesEngine(store, clock=lambda: context["now"])
    source = "seed"

    for name in DEMO_TAGS:
        engine.create_tag({"name": name}, actor="dana", source=source)

    series = engine.create_series(
        {
            "name": "Expansion nurture",
            "reenter": True,
            "start_filters": [{"field": "plan", "value": "trial"}],
            "exit_filters": [
                {"field": "spam", "value": True},
                {"field": "tags", "value": "ExitUpgradeSeries"},
            ],
            "goal": {
                "outcome_filters": [{"field": "plan", "value": "premium"}],
                "match_window_days": 30,
            },
            "path": [
                {"kind": "message", "title": "Welcome to the expansion series"},
                {"kind": "rule", "title": "Wait for a qualifying event"},
                {
                    "kind": "tag",
                    "title": "Tag the upgraded contact",
                    "tag": "Upgraded after Series",
                },
            ],
        },
        actor="dana",
        source=source,
    )
    engine.publish_series(str(series["id"]), actor="dana", source=source)

    # Contact 1: enter, walk the path, and reach the tag block with the outcome
    # already met, so the demo shows both a tag application and a satisfied goal.
    engine.evaluate_entry(
        str(series["id"]),
        {"contact_id": "con-ada", "plan": "trial"},
        event_type="subscription.expiring",
        actor="dana",
        source=source,
    )
    for _ in range(3):
        engine.advance(
            str(series["id"]),
            "con-ada",
            contact={"contact_id": "con-ada", "plan": "premium"},
            actor="dana",
            source=source,
        )

    # Contact 2: complete without the goal, then re-enter on a later event, so the
    # demo shows reentry_count moving and the toggle doing its job.
    engine.evaluate_entry(
        str(series["id"]),
        {"contact_id": "con-ben", "plan": "trial"},
        event_type="purchase.made",
        actor="dana",
        source=source,
    )
    for _ in range(3):
        engine.advance(str(series["id"]), "con-ben", actor="dana", source=source)
    engine.evaluate_entry(
        str(series["id"]),
        {"contact_id": "con-ben", "plan": "trial"},
        event_type="subscription.expiring",
        actor="dana",
        source=source,
    )

    # Contact 3: exit by the sticky rule, then match again and be refused. This is
    # the researched property that gives the rule its name.
    engine.evaluate_entry(
        str(series["id"]),
        {"contact_id": "con-cara", "plan": "trial"},
        event_type="purchase.made",
        actor="dana",
        source=source,
    )
    engine.record_exit(
        str(series["id"]),
        "con-cara",
        {"contact_id": "con-cara", "plan": "trial", "tags": "ExitUpgradeSeries"},
        actor="dana",
        source=source,
    )
    engine.evaluate_entry(
        str(series["id"]),
        {"contact_id": "con-cara", "plan": "trial"},
        event_type="subscription.expiring",
        actor="dana",
        source=source,
    )

    # A poll over a contact that matches nothing, so the demo shows an honest skip
    # as a recorded outcome rather than as an error.
    poll = engine.poll_series(
        str(series["id"]),
        [{"contact": {"contact_id": "con-dee", "plan": "free"}, "event_type": "purchase.made"}],
        actor="dana",
        source=source,
    )

    enrollments = engine.enrollments()
    applications = engine.tag_applications()
    states: dict[str, int] = {}
    for record in enrollments:
        key = str(record["data"].get("state"))
        states[key] = states.get(key, 0) + 1
    state_text = ", ".join(f"{count} {name}" for name, count in sorted(states.items()))
    skipped = sum(poll["outcomes"].values()) - poll["enrolled"] - poll["reentered"]
    return (
        f"1 series, {len(DEMO_TAGS)} tags, {len(enrollments)} enrollments ({state_text}), "
        f"{len(applications)} tag applications, 1 poll ({skipped} skipped)"
    )
