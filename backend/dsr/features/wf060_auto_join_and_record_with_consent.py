"""WF-060: auto-join and record the meeting, gated by recording consent.

A build from a researched specification, not a port. There was no source branch.
The specification is ``docs/research/digital-sales-room-workflows/wf/WF-060.md``,
quoted in full in issue 194, and it says of itself: "This spec does not state the
flow as separate fields. The evidence below is the specification. An implementer
who needs a flow the evidence does not contain must derive it and record the
derivation, not assume it."

The rules live in :mod:`dsr.recording_consent` and are not restated here. This
module is the three things a feature contributes and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
  Twelve workflow branches each editing those files is why none of the original
  twelve merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces
  it.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names a route the host actually mounted. The
  branch history is full of features whose audit log named a path the app had
  stopped serving.

The status codes are the researched ones where the research gives one
---------------------------------------------------------------------

The research documents two failures of ``POST /v2/meetings`` with their codes:
``409 Conflict, e.g. consent page is not enabled in your company`` and
``404 No Gong user found corresponding to the provided organizer email``. Both are
read from the profile and the directory *before* any link is issued, so the
refusals carry the vendor's own status rather than an invented one. A profile
whose consent page is off is a configuration the administrator can fix, which is
why it is 409 rather than 400, and an organiser with no user behind them is 404
because that is what the vendor returns and because a caller's retry would not
help.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.recording_consent import (
    ConsentEngine,
    decisions,
    directory,
    inferences,
    links,
    precall,
    profiles,
    vocabulary as vocab,
)
from dsr.recording_consent.errors import (
    BookingNotFound,
    ConsentError,
    ConsentPageDisabled,
    IllegalTransition,
    JoinWithoutConsentRefused,
    LinkSuperseded,
    OrganizerUnmapped,
    ProfileInvalid,
    ProfileNotFound,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-060-auto-join-and-record-with-consent",
    "ticket": "WF-060",
    "name": "Auto-join and record the meeting, gated by recording consent",
    "description": (
        "Resolve a consent profile by the organiser, issue a consent-enabled "
        "meeting link, and let a recording bot auto-join. The recording is "
        "blocked until a participant grants consent, and a decline cancels it. "
        "Every step the research leaves open is a named, served derivation."
    ),
    "nav": [{"id": "wf-060-consent-recording", "label": "Consent recording"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-060", tags=["WF-060"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal. The defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving. ``tests/test_wf060_http.py`` asserts every
    source this router can record matches a concrete ``(method, path)`` the host
    mounted.
    """
    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> ConsentEngine:
    """A :class:`ConsentEngine` over the process-wide audited store.

    Per request, for the same reason WF-041 builds its engine per request: the
    engine holds nothing beyond the store and a clock, so building it here leaves
    both overridable in a test instead of hanging a long-lived object off
    ``app.state`` - which is a shared file this feature may not edit.
    """
    return ConsentEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All seven types are declared in dsr.recording_consent.errors and raised by
# nothing else in the product. That is what makes it safe to map them here: the
# host refuses a second feature registering a handler for the same type, and a
# handler for ValueError or PermissionError would intercept those exceptions
# everywhere.
#
# `RecordNotFound` is deliberately not claimed. The core app already maps it to
# 404, and two handlers for one type is a collision the host refuses.


def _consent_error(request: Request, exc: ConsentError) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=400,
        content={"error": exc.code, "detail": str(exc), "errors": exc.errors},
    )


def _profile_invalid(request: Request, exc: ProfileInvalid) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": exc.code, "detail": str(exc), "errors": exc.errors},
    )


def _profile_not_found(request: Request, exc: ProfileNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": exc.code, "detail": "No such consent profile."}
    )


def _booking_not_found(request: Request, exc: BookingNotFound) -> JSONResponse:
    """404 for a booking nobody opened. Distinct from the 409 a superseded link gets: one
    is a booking that does not exist and the other is an invite that is stale, and a
    caller can act on the second by issuing a new link."""
    return JSONResponse(
        status_code=404, content={"error": exc.code, "detail": str(exc), **exc.errors}
    )


def _consent_page_disabled(request: Request, exc: ConsentPageDisabled) -> JSONResponse:
    """409, the vendor's own code for exactly this state.

    The research quotes it: "409 Conflict, e.g. consent page is not enabled in your
    company". Reproducing the vendor's code means a caller comparing our refusal
    to Gong's does not have to learn two codebooks, and 409 rather than 400 because
    the request was well formed and the organisation's configuration is what
    conflicts with it.
    """
    return JSONResponse(
        status_code=409, content={"error": exc.code, "detail": str(exc), **exc.errors}
    )


def _organizer_unmapped(request: Request, exc: OrganizerUnmapped) -> JSONResponse:
    """404, the vendor's own code, for the same reason as above."""
    return JSONResponse(
        status_code=404, content={"error": exc.code, "detail": str(exc), **exc.errors}
    )


def _join_refused(request: Request, exc: JoinWithoutConsentRefused) -> JSONResponse:
    """403: the participant is not admitted. Not a 400 - the participant did nothing wrong."""
    return JSONResponse(
        status_code=403, content={"error": exc.code, "detail": str(exc), **exc.errors}
    )


def _link_superseded(request: Request, exc: LinkSuperseded) -> JSONResponse:
    """409: the link exists and no longer joins the call."""
    return JSONResponse(
        status_code=409, content={"error": exc.code, "detail": str(exc), **exc.errors}
    )


def _illegal_transition(request: Request, exc: IllegalTransition) -> JSONResponse:
    """409 for a step the machine does not have from this state.

    The body carries ``state``, ``step`` and ``allowed`` so a client can tell the
    user which step is available rather than only that this one is not.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": exc.code,
            "detail": str(exc),
            "state": exc.current,
            "step": exc.step,
            "allowed": list(exc.allowed),
        },
    )


EXCEPTION_HANDLERS = {
    ProfileInvalid: _profile_invalid,
    ProfileNotFound: _profile_not_found,
    BookingNotFound: _booking_not_found,
    ConsentPageDisabled: _consent_page_disabled,
    OrganizerUnmapped: _organizer_unmapped,
    JoinWithoutConsentRefused: _join_refused,
    LinkSuperseded: _link_superseded,
    IllegalTransition: _illegal_transition,
    # Registered last and mapped to the same 400: every subclass above is more
    # specific, so this only catches a type added to the hierarchy without a
    # handler of its own. Without it such a type would be a 500.
    ConsentError: _consent_error,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """Counts for the page header, and the states that need attention."""
    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so a form cannot drift from the
    rules: the provider list, the link kinds, the state sets and the switches all
    come from the same tables the validator uses.
    """
    return {
        "providers": dict(vocab.PROVIDERS),
        "link_kinds": list(vocab.LINK_KINDS),
        "link_states": list(vocab.LINK_STATES),
        "states": list(vocab.STATES),
        "consent_states": list(vocab.CONSENT_STATES),
        "recording_states": list(vocab.RECORDING_STATES),
        "decisions": list(vocab.DECISIONS),
        "steps": list(decisions.STEPS),
        "terminal_states": list(vocab.TERMINAL_STATES),
        "prompt_modes": list(vocab.PROMPT_MODES),
        "default_prompt_mode": vocab.DEFAULT_PROMPT_MODE,
        "default_prompt_text": vocab.DEFAULT_PROMPT_TEXT,
        "locales": list(vocab.CONSENT_PAGE_LOCALES),
        "switches": {
            "consent_page_enabled": vocab.CONSENT_PAGE_SWITCH,
            "enforce_consent_page": vocab.ENFORCEMENT_SWITCH,
            "allow_join_without_consent": vocab.JOIN_WITHOUT_CONSENT_SWITCH,
            "suppress_prompt_when_consent_page_used": vocab.SUPPRESS_PROMPT_WHEN_CONSENT_PAGE_USED,
            "precall_email_enabled": profiles.PRECALL_EMAIL_SWITCH,
            "audio_prompt_enabled": profiles.AUDIO_PROMPT_SWITCH,
        },
        "precall_window_minutes": list(vocab.PRECALL_WINDOW_MINUTES),
        "precall_email_variables": dict(vocab.PRECALL_EMAIL_VARIABLES),
        "precall_disclosure": precall.RECORDING_DISCLOSURE,
        "recording_bot_email": vocab.RECORDING_BOT_EMAIL,
        "profile_resolution_key": vocab.PROFILE_RESOLUTION_KEY,
        "meeting_create_scope": vocab.MEETING_CREATE_SCOPE,
        "new_meeting_request_fields": list(vocab.NEW_MEETING_REQUEST_FIELDS),
        "new_meeting_response_fields": list(vocab.NEW_MEETING_RESPONSE_FIELDS),
        "documented_errors": {str(code): text for code, text in vocab.DOCUMENTED_ERRORS.items()},
        "collections": list(vocab.ALL_COLLECTIONS),
        "directory_defaults": dict(directory.DIRECTORY_DEFAULTS),
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The research says an implementer "must derive it and record the derivation,
    not assume it". This route is that record, and it is served rather than buried
    in a docstring so a reviewer reads the decision instead of the code.
    """
    items = inferences.describe()
    return {"count": len(items), "decisions": items}


@router.get("/decisions/{inference_id}")
def read_decision(inference_id: str) -> dict[str, Any]:
    """One judgement call by id."""
    return inferences.describe_one(inference_id)


@router.get("/integration/status")
def integration_status(engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """The researched ``GET /v2/meetings/integration/status`` answer, from local state.

    The endpoint exists to "validate Gong meeting integration", and the three
    things that can make it fail are all knowable without a call: is there a
    profile with the consent page on, is there a directory user an organiser
    resolves through, and is the recording bot address the one the invite carries.
    A caller reads this before booking rather than after a failed call.
    """
    profile_rows = engine.profiles()
    users = engine.users()
    ready = [p for p in profile_rows if p["data"].get(vocab.CONSENT_PAGE_SWITCH)]
    default = engine.default_profile_id()
    return {
        "endpoint": vocab.INTEGRATION_STATUS_ENDPOINT,
        "ready": bool(ready and users),
        "consent_page_enabled_on": len(ready),
        "profiles": len(profile_rows),
        "directory_users": len(users),
        "default_profile_id": default,
        "recordable_users": sum(1 for u in users if u["data"].get("record_by_gong", True)),
        "recording_bot_email": vocab.RECORDING_BOT_EMAIL,
        "resolution_key": vocab.PROFILE_RESOLUTION_KEY,
        "checks": [
            {
                "name": "consent_page_enabled",
                "ok": bool(ready),
                "detail": (
                    f"{len(ready)} consent profile(s) have the consent page on"
                    if ready
                    else vocab.DOCUMENTED_ERRORS[409]
                ),
            },
            {
                "name": "users_mapped",
                "ok": bool(users),
                "detail": (
                    f"{len(users)} directory user(s) a booking can resolve through"
                    if users
                    else vocab.DOCUMENTED_ERRORS[404]
                ),
            },
            {
                "name": "default_profile",
                "ok": bool(default),
                "detail": (
                    "a profile is the default for unassigned users"
                    if default
                    else "no profile is the default, so an unassigned user has no consent rule"
                ),
            },
        ],
    }


# --------------------------------------------------------------------------- #
# Step 1 to 5: the consent profile
# --------------------------------------------------------------------------- #


@router.get("/profiles")
def list_profiles(
    room_id: str | None = Query(None), engine: ConsentEngine = EngineDep
) -> dict[str, Any]:
    rows = engine.profiles(room_id)
    return {"count": len(rows), "profiles": rows}


@router.post("/profiles", status_code=201)
def create_profile(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Admin centre > Data capture > Recording consent > Add Profile."""
    return engine.create_profile(
        payload,
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/profiles"),
    )


@router.get("/profiles/{profile_id}")
def read_profile(profile_id: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    return engine.profile(profile_id)


@router.patch("/profiles/{profile_id}")
def update_profile(
    profile_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Patch a profile. The whole profile is revalidated, not only the fields sent.

    Turning the consent page on makes a profile invalid without a provider, and
    the request that turns it on is rarely the request that adds one.
    """
    return engine.update_profile(
        profile_id, payload, actor=actor, source=_source("PATCH", "/profiles/{profile_id}")
    )


@router.post("/profiles/{profile_id}/default")
def make_default_profile(
    profile_id: str,
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Set one profile as the default for team members with no assignment.

    Exactly one profile carries the flag afterwards. The engine owns that, rather
    than this route, because it is a property of the data: an invariant a second
    caller can bypass is not an invariant, and the caller that would bypass it here
    is a seed or a script rather than a browser.
    """
    return engine.set_default_profile(
        profile_id, actor=actor, source=_source("POST", "/profiles/{profile_id}/default")
    )


@router.get("/profiles/{profile_id}/consent-page")
def consent_page(
    profile_id: str, base_url: str = Query(""), engine: ConsentEngine = EngineDep
) -> dict[str, Any]:
    """Step 3's consent page preview, as the buyer would receive it."""
    return engine.preview_consent_page(profile_id, base_url)


# --------------------------------------------------------------------------- #
# Step 6: the directory
# --------------------------------------------------------------------------- #


@router.get("/users")
def list_users(
    room_id: str | None = Query(None), engine: ConsentEngine = EngineDep
) -> dict[str, Any]:
    rows = engine.users(room_id)
    return {"count": len(rows), "users": rows}


@router.post("/users", status_code=201)
def add_user(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """One directory entry, carrying the three per-user flags the research names."""
    return engine.add_user(payload, room_id=room_id, actor=actor, source=_source("POST", "/users"))


@router.get("/users/resolve")
def resolve_user(
    organizer_email: str = Query(...),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The profile that applies to an organiser, and how it was found.

    Resolved on ``organizerEmail`` because the research says so: "The Gong consent
    page link will be used according to the settings of this user." Never on the
    room - a shared booking page is booked by any rep, so a room-scoped lookup would
    apply one seller's consent settings to another seller's meeting.
    """
    return engine.resolve_profile(organizer_email)


# --------------------------------------------------------------------------- #
# Step 7: the booking
# --------------------------------------------------------------------------- #


@router.get("/bookings")
def list_bookings(
    room_id: str | None = Query(None), engine: ConsentEngine = EngineDep
) -> dict[str, Any]:
    rows = engine.recordings(room_id)
    return {"count": len(rows), "bookings": rows}


@router.post("/bookings", status_code=201)
def open_booking(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(None),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Issue a consent-enabled link and record the state the profile implies."""
    return engine.open(payload, room_id=room_id, actor=actor, source=_source("POST", "/bookings"))


@router.get("/bookings/{booking_id}")
def read_booking(booking_id: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """One booking's consent and recording state, with both axes."""
    return engine.recording(booking_id)


@router.post("/bookings/{booking_id}/consent")
def record_consent(
    booking_id: str,
    decision: str = Query(...),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The participant's answer, or the bot joining without one.

    ``decision`` is one of the researched values the vocabulary serves:
    ``granted``, ``declined`` or ``joined_without_consent``. The third is only
    admitted where the profile allows it, and where it is, the recording is
    cancelled.
    """
    return engine.decide(
        booking_id, decision, actor=actor, source=_source("POST", "/bookings/{booking_id}/consent")
    )


@router.post("/bookings/{booking_id}/recording/start")
def start_recording(
    booking_id: str,
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The bot joined and the call is recording.

    Refused from a ``blocked`` recording, which is what makes "recorded without
    consent" a state this machine cannot produce.
    """
    return engine.start_recording(
        booking_id, actor=actor, source=_source("POST", "/bookings/{booking_id}/recording/start")
    )


@router.post("/bookings/{booking_id}/recording/finish")
def finish_recording(
    booking_id: str,
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The call ended and a recording exists."""
    return engine.finish_recording(
        booking_id, actor=actor, source=_source("POST", "/bookings/{booking_id}/recording/finish")
    )


@router.post("/bookings/{booking_id}/recording/cancel")
def cancel_recording(
    booking_id: str,
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The call ended with no recording."""
    return engine.cancel_recording(
        booking_id, actor=actor, source=_source("POST", "/bookings/{booking_id}/recording/cancel")
    )


@router.get("/bookings/{booking_id}/link")
def read_link(
    booking_id: str,
    meeting_id: str | None = Query(None, description="Ask whether this invite is still current"),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """The link that currently joins the call.

    409 when the booking has no link, when its only link was superseded, or when
    ``meeting_id`` names an invite this booking has already replaced. Handing back
    a different link for a superseded id would put a new URL into an invite that has
    already been sent.
    """
    return engine.current_link(booking_id, meeting_id)


@router.post("/bookings/{booking_id}/link")
def reissue_link(
    booking_id: str,
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Change the link, disabling the previous one as a state rather than a delete."""
    return engine.reissue_link(
        booking_id, actor=actor, source=_source("POST", "/bookings/{booking_id}/link")
    )


@router.get("/bookings/{booking_id}/runs")
def list_runs(booking_id: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """The recording lifecycle for one booking, oldest first."""
    rows = engine.runs(booking_id)
    return {"count": len(rows), "runs": rows}


@router.post("/bookings/{booking_id}/precall-email")
def plan_precall_email(
    booking_id: str,
    sender_name: str = Query(""),
    sender_company: str = Query(""),
    meeting_hour: str = Query(""),
    actor: str | None = Query(None),
    engine: ConsentEngine = EngineDep,
) -> dict[str, Any]:
    """Send the pre-call email if the window is open, and say why not if it is not.

    Always 200. A window that has not opened is not a failure: refusing it would
    put a normal wait in the same place as a real fault, and an operator reading a
    run of refusals would see a product that is broken rather than one that is
    early.
    """
    return engine.plan_precall_email(
        booking_id,
        sender_name=sender_name,
        sender_company=sender_company,
        meeting_hour=meeting_hour,
        actor=actor,
        source=_source("POST", "/bookings/{booking_id}/precall-email"),
    )


@router.get("/bookings/{booking_id}/emails")
def list_emails(booking_id: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """The pre-call emails one booking produced."""
    rows = engine.emails(booking_id)
    return {"count": len(rows), "emails": rows}


@router.get("/organizers/{organizer_email}/can-record")
def can_record(organizer_email: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """Whether an organiser's own settings permit recording this booking.

    Checked before the booking rather than after a missing recording, so the
    seller learns of it while there is still something to do about it.
    """
    users = [row["data"] for row in engine.users()]
    ok, why = directory.can_record(users, organizer_email)
    blockers = directory.recording_blocked_by_invitee(users, [])
    return {
        "organizer_email": directory.normalise_email(organizer_email),
        "can_record": ok,
        "reason": why,
        "invitee_blockers": blockers,
    }


@router.get("/consent/{booking_id}")
def participant_consent_page(booking_id: str, engine: ConsentEngine = EngineDep) -> dict[str, Any]:
    """What the participant is shown before they answer.

    Never 403s. A closed booking answers with the page and a reason, because the
    participant did nothing wrong and a refusal here would read as one.

    ``allowed_decisions`` is computed from the machine *and* the profile together, so
    the page cannot offer a choice the API would refuse. A consent page that offered
    a button answering 403 asks the participant to do something impossible.
    """
    record = engine.recording(booking_id)
    data = record["data"]
    profile = engine.profile(data["profile_id"])["data"]
    machine = decisions.machine_from_data(data)
    return {
        "booking_id": booking_id,
        "profile": links.consent_page_preview(profile),
        "state": machine.state,
        "consent_state": machine.consent_state,
        "enforced": machine.enforced,
        "consent_required": decisions.consent_required(profile),
        "allow_join_without_consent": bool(profile.get(vocab.JOIN_WITHOUT_CONSENT_SWITCH)),
        "allowed_decisions": list(decisions.available_steps(machine.state, profile)),
        "recording_state": machine.recording_state,
        "open": not machine.terminal,
    }


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the consent and recording states the research says matter.

    Every row is produced by calling the real :class:`ConsentEngine`, so the demo
    cannot show a shape, an event or an audit row the HTTP routes would not
    produce. ``source="seed"`` rather than a route string: no route served this,
    and claiming one would be the lie hard rule 4 exists to prevent.

    The states seeded, and why each is here:

    * a booking **awaiting consent** with its recording ``blocked``. That is the
      gate the whole workflow exists for, and a board that only shows finished
      calls never shows it.
    * a booking whose consent was **granted** and whose recording **completed**.
    * a booking whose consent was **declined**, so the recording was **cancelled**.
      The brief names this as the state the seed must contain: "The seed must
      print at least one state that is not a success."
    * a booking where a participant **joined without consent** and the recording
      was cancelled, which is the other researched non-success.
    * a booking on a profile where enforcement is **off** and the participant
      declined, so the recording still ran. That is the derived open point, and a
      demo that did not contain it would leave the most contestable decision this
      workflow made unviewable.
    * a booking whose link was **superseded**, so "the previous link is disabled"
      is visible as a state.
    * a **profile with the consent page off**, so the researched 409 has a shape on
      the page.

    The clock is the seeder's, moved by hand per booking, because the pre-call
    window is a statement about an instant and a demo row that fired the email at
    whatever time the seed happened to run would prove nothing about it.
    """
    store = RecordStore(db)
    now: datetime = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]

    # The seeder's clock, moved by hand. The engine reads it for the open stamp,
    # the pre-call window and the supersede stamp, so one clock drives all three
    # and the demo's timestamps read as a sequence rather than as a pile.
    #
    # It has to be hand-driven because the pre-call window is a statement about an
    # instant. A seed that ran at whatever time the seeder happened to run would
    # produce a booking whose email never fires, which is the one state the
    # workflow's most quoted timing rule is about.
    cursor = {"at": now.replace(microsecond=0)}

    def clock() -> datetime:
        return cursor["at"]

    def set_at(moment: datetime) -> datetime:
        cursor["at"] = moment.replace(microsecond=0)
        return cursor["at"]

    engine = ConsentEngine(store, now=clock)

    # -- profiles ---------------------------------------------------------- #
    #
    # Two: one enforcing, one not. The difference is the workflow's most
    # contestable decision, and a demo holding only the enforcing profile would
    # make that decision unreviewable.

    enforcing = engine.create_profile(
        {
            "name": "Standard recording consent",
            "description": "This call will be recorded for note taking and follow-up.",
            "consent_page_enabled": True,
            "enforce_consent_page": True,
            "allow_join_without_consent": True,
            "providers": {"zoom": "dynamic_link", "google_meet": "dynamic_link"},
            "default_provider": "zoom",
            "locales": ["en", "en-gb"],
            "precall_email_enabled": True,
            "precall_email": {
                "subject": "{{meeting_title}} at {{meeting_hour}} with {{sender_name}}",
                "body": (
                    "{{sender_name}} from {{sender_company}} has invited you to "
                    "{{meeting_title}}. This call will be recorded. You will be asked "
                    "for consent before it starts."
                ),
                "signature": "{{sender_name}}\n{{sender_company}}",
                "legal_footer": "Recording consent profile: Standard recording consent.",
            },
            "audio_prompt_enabled": True,
            "audio_prompt": {"mode": vocab.DEFAULT_PROMPT_MODE},
            "is_default": True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    advisory = engine.create_profile(
        {
            "name": "Advisory recording consent",
            "description": "The consent page is shown but not enforced.",
            "consent_page_enabled": True,
            "enforce_consent_page": False,
            "providers": {"microsoft_teams": "host_decides"},
            "default_provider": "microsoft_teams",
            "precall_email_enabled": False,
            "audio_prompt_enabled": True,
            "audio_prompt": {"mode": "every_guest", "suppress_when_consent_page_used": False},
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # A profile with the consent page off, so the researched 409 has a shape.
    engine.create_profile(
        {
            "name": "Recording off",
            "description": "No consent page, so no consent-enabled meeting link.",
            "consent_page_enabled": False,
            "providers": {"webex": "static_link"},
            "default_provider": "webex",
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # -- the directory ------------------------------------------------------ #
    #
    # Three users, one per researched per-user flag that changes an answer, plus
    # one whose own setting prevents an invitation from recording.

    engine.add_user(
        {
            "email": "dana@northwind.example",
            "name": "Dana Okafor",
            "profile_id": enforcing["id"],
            "record_by_gong": True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    engine.add_user(
        {
            "email": "sam@northwind.example",
            "name": "Sam Rivera",
            "profile_id": enforcing["id"],
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    engine.add_user(
        {
            "email": "noor@northwind.example",
            "name": "Noor Haddad",
            "profile_id": advisory["id"],
            "is_default_for_new_members": True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    # The researched third flag: "if the invitation of this user to a web
    # conference will prevent its recording". A booking including this address is
    # refused before the invite goes out.
    engine.add_user(
        {
            "email": "guest@blocked-recording.example",
            "name": "External guest who prevents recording",
            "record_by_gong": False,
            "blocks_recording": True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    invitees = [
        {"email": "buyer@northwind.example", "name": "Buyer"},
        {"email": "analyst@contoso.example", "name": "Analyst"},
    ]

    def booking(
        booking_id: str,
        organizer: str,
        title: str,
        days: int = 1,
        to: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Open one booking starting ``days`` after the seed clock.

        Leaves the clock at the meeting's start, which is the instant every later
        step is measured from: the pre-call window opens 20 minutes before it and
        the recording starts at it.
        """
        start = set_at(clock() + timedelta(days=days))
        return engine.open(
            {
                "booking_id": booking_id,
                "organizer_email": organizer,
                "title": title,
                "start_time": start.isoformat(timespec="milliseconds"),
                "end_time": (start + timedelta(minutes=30)).isoformat(timespec="milliseconds"),
                "invitees": list(to if to is not None else invitees),
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    # -- the booking states ------------------------------------------------- #

    # 1. Awaiting consent. The recording is blocked, which is the gate the whole
    # workflow exists for. A board that only shows finished calls never shows it.
    booking("seed-wf060-awaiting", "dana@northwind.example", "Awaiting consent")

    # 2. Granted, then recorded. The happy path, so the board is not all warnings,
    # and the one booking that also produced a pre-call email.
    granted = booking("seed-wf060-granted", "sam@northwind.example", "Consent granted")
    granted_id = granted["data"]["booking_id"]
    granted_start = clock()
    engine.decide(granted_id, "granted", actor="buyer", source="seed")
    # Step back into the researched window: 10 to 20 minutes before the call.
    set_at(granted_start - timedelta(minutes=15))
    engine.plan_precall_email(
        granted_id,
        sender_name="Sam Rivera",
        sender_company="Northwind",
        actor="sam",
        source="seed",
    )
    set_at(granted_start)
    engine.start_recording(granted_id, actor="seed", source="seed")
    engine.finish_recording(granted_id, actor="seed", source="seed")

    # 3. Declined, so the recording was cancelled. The brief names this as the
    # non-success the seed must contain.
    declined = booking("seed-wf060-declined", "dana@northwind.example", "Consent declined")
    declined_id = declined["data"]["booking_id"]
    engine.decide(declined_id, "declined", actor="buyer", source="seed")
    engine.cancel_recording(declined_id, actor="seed", source="seed")

    # 4. Joined without consent. The other researched non-success, and the only one
    # reachable because this profile allows the join.
    joined = booking("seed-wf060-joined", "dana@northwind.example", "Joined without consent")
    joined_id = joined["data"]["booking_id"]
    engine.decide(joined_id, "joined_without_consent", actor="bot", source="seed")
    engine.cancel_recording(joined_id, actor="seed", source="seed")

    # 5. Declined on a profile where enforcement is off, so the recording ran
    # anyway. This is the derivation the research does not state, and it is the row
    # a reviewer most needs to see.
    advisory = booking("seed-wf060-advisory-declined", "noor@northwind.example", "Advisory decline")
    advisory_id = advisory["data"]["booking_id"]
    engine.decide(advisory_id, "declined", actor="buyer", source="seed")
    engine.start_recording(advisory_id, actor="seed", source="seed")
    engine.finish_recording(advisory_id, actor="seed", source="seed")

    # 6. A link that was changed, so "the previous link is disabled" is visible as a
    # state rather than as a gap.
    reissued = booking("seed-wf060-reissued", "sam@northwind.example", "Link reissued")
    reissued_id = reissued["data"]["booking_id"]
    engine.decide(reissued_id, "granted", actor="buyer", source="seed")
    engine.reissue_link(reissued_id, actor="sam", source="seed")

    # 7. A booking whose invitee prevents its own recording. The engine refuses it
    # before the invite goes out, so what the demo holds is the refusal rather than
    # a row that should not exist. This is the researched third directory flag:
    # "if the invitation of this user to a web conference will prevent its
    # recording".
    refused = False
    try:
        booking(
            "seed-wf060-blocked-invitee",
            "dana@northwind.example",
            "Blocked invitee",
            to=[
                {"email": "buyer@northwind.example", "name": "Buyer"},
                {"email": "guest@blocked-recording.example", "name": "Blocking guest"},
            ],
        )
    except ProfileInvalid:
        refused = True

    # -- the summary line --------------------------------------------------- #
    #
    # Every number is read back from the store rather than written by hand, so the
    # line cannot describe a state the seed did not produce.
    #
    # ASCII only, and that is a hard requirement rather than a style preference:
    # the seeder prints this to a Windows console, and one RIGHTWARDS ARROW in a
    # recovered feature's return string broke the whole seeder. Every character
    # below is asserted encodable by cp1252 in tests/test_wf060.py.
    counts = engine.summary(room_id)
    states = counts["by_state"]
    parts = [
        f"{counts['profiles']} consent profiles, {counts['profiles_enforcing']} enforcing",
        f"{counts['users']} directory users",
        f"{counts['bookings']} bookings: "
        f"{states.get('awaiting_consent', 0)} awaiting consent, "
        f"{states.get('consented', 0)} consented, "
        f"{states.get('recorded', 0)} recorded, "
        f"{states.get('cancelled', 0)} recording cancelled",
        f"{counts['recordings_cancelled']} cancelled by a decline or by joining without consent",
        f"{counts['recordings_complete']} recordings complete",
    ]
    if refused:
        parts.append("1 booking refused because an invitee prevents recording")
    return "; ".join(parts)
