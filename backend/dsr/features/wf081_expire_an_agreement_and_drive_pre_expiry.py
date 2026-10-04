"""WF-081: expire an agreement and drive pre-expiry reminders.

The researched workflow, in full. A seller sends an agreement and sets a deadline
on it (step 1), the signer reads that deadline in their own timezone beside the
count of fields still to place (steps 2 and 3), the system reminds them 7 and then
3 days out and never twice inside a day (step 4), and at the deadline the
incomplete signers flip to ``expired`` while a signer who signed in time stays
``signed`` (step 5). What expiry does **not** do is delete anything: "All parties
to the signature request will still have access to the document including audit
trail, similar to ``declined`` signature requests. They will not be able to sign or
modify the signature request."

What the contract meant for this build
--------------------------------------

**The prefix is ``/api/wf-081`` and every route is room-scoped.** The
specification's join key is ``expires_at`` on the request, and the room is what a
seller is looking at when they ask whether the agreement in front of them is
still open.

**``source`` comes from the route.** Every write passes
``f"{router.prefix}..."``, so the audit row names the route that served it. A
hardcoded URL string inside a domain method is a defect, which is why
``ExpiryEngine`` takes ``source`` as a required keyword on every writing method.

**One handler for the whole error hierarchy.** ``ExpiryError`` is the base of every
refusal the rules raise, and each carries its own ``status`` and ``code``, so one
handler answers 422 for a malformed deadline and 409 for a request that is closed.
``ExpiryRequestNotFound`` and ``ExpirySignerNotFound`` are claimed separately and
they are this feature's own types, because a feature may only map error types it
raises itself.

**The sweep is a route, not a thread.** The specification describes a scheduler
and does not say what drives it. See ``expiry_inferences`` for why this build has
no background worker, and the short version is that a thread would write audit
rows that name no route.

**Embedded flows publish an event and send nothing.** "Emails are muted in all
embedded signing flows. Integrations using embedded signing must consume the
``signature_request_expired`` event." The reminders and the expiry both publish an
event in the embedded mode and record a channel instead of pretending a message
went out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import expiry_inferences, expiry_vocabulary as vocab
from dsr.security_governance.expiry_engine import ExpiryEngine
from dsr.security_governance.expiry_rules import (
    ExpiryError,
    ExpiryRequestNotFound,
    ExpirySignerNotFound,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-081-expire-an-agreement-and-drive-pre-expiry",
    "ticket": "WF-081",
    "name": "Expire an agreement and drive pre-expiry reminders",
    "description": (
        "Send an agreement with an expires_at between 1 and 90 days out, remind every "
        "unsigned signer 7 and 3 days before the deadline and never twice inside 24 "
        "hours, and at the deadline flip the incomplete signers to expired while a "
        "signer who signed in time stays signed. Expiry closes the request and keeps "
        "the document. An agreement with no expires_at never expires."
    ),
}

router = APIRouter(prefix="/api/wf-081", tags=["wf081"])


def get_engine(store: RecordStore = StoreDep) -> ExpiryEngine:
    """An :class:`ExpiryEngine` over the process-wide audited store.

    Built per request rather than held on ``app.state``, because putting it there
    is the edit to the shared ``dsr/api.py`` this feature host exists to make
    unnecessary. The engine holds a store handle and a clock and nothing else, so
    a test can build one directly and move the clock by hand.
    """
    return ExpiryEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _expiry_error(request: Request, exc: ExpiryError) -> JSONResponse:
    """A domain refusal, answered with the status and code the refusal carries.

    One handler for the hierarchy, because a malformed ``expires_at`` and a closed
    request are both this package's errors and only one of them is about state
    that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": exc.detail, "status": exc.status},
    )


def _request_not_found(request: Request, exc: ExpiryRequestNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "expiry_request_not_found", "detail": str(exc), "status": 404},
    )


def _signer_not_found(request: Request, exc: ExpirySignerNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "unknown_signer", "detail": str(exc), "status": 404},
    )


EXCEPTION_HANDLERS = {
    ExpiryError: _expiry_error,
    ExpiryRequestNotFound: _request_not_found,
    ExpirySignerNotFound: _signer_not_found,
}


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every published term, served as data.

    The cadence, the dedupe window, the day range, the rounding, the signer
    statuses the sweep moves and the ones it keeps, the two delivery modes and the
    sentence saying what expiry is not. A page renders its badges and its copy from
    this rather than from a list compiled into the page, so a rule changed here
    reaches every client at once.
    """
    return vocab.catalogue()


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The specification fixes the numbers and says almost nothing about the
    mechanism: where a reminder is kept, who sweeps, what an integrator receives
    in place of the email the embedded flow mutes. Those are collected here, named
    and served, rather than left as comments in a function body.
    """
    return expiry_inferences.register()


# --------------------------------------------------------------------------- #
# Step 1: send with an expiry
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/requests", status_code=201)
def send_request(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Send an agreement, with an expiry if the seller set one.

    "Sender sets an expiry when sending (``expires_at``, an epoch timestamp)".
    The same field the specification names on send, on update and on an unclaimed
    draft.

    A payload with no ``expires_at`` is a valid request that will never expire.
    That is the specification's own default and it is not answered with a
    substituted value: "By default signature requests do not expire."

    A deadline outside 1 to 90 days is refused with 422 and writes nothing, so a
    rejected send leaves no trace in the audit log.
    """
    return engine.send(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/requests",
    )


@router.get("/rooms/{room_id}/requests")
def list_requests(
    room_id: str,
    status: str | None = Query(default=None, description="filter by request status"),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Every agreement in this room, with the status each is at.

    The filter is the specification's own terminal state, because the Dropbox Sign
    Documents page this workflow models "can filter by expired status".
    """
    rows = engine.requests(room_id)
    if status:
        rows = [row for row in rows if row["status"] == status]
    return {"room_id": room_id, "count": len(rows), "requests": rows}


@router.get("/rooms/{room_id}/requests/{request_id}")
def read_request(
    room_id: str,
    request_id: str,
    tz: str | None = Query(default=None, description="the signer's timezone"),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """One agreement, as the signer sees it.

    Read on a terminal request, deliberately: every party keeps access to the
    document and its audit trail after expiry. ``tz`` renders the deadline in one
    signer's timezone without changing the instant the sweep compares.
    """
    return engine.request_view(request_id, tz_name=tz)


@router.put("/rooms/{room_id}/requests/{request_id}/expiry")
def update_expiry(
    room_id: str,
    request_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Move the deadline, or clear it with an explicit ``null``.

    "Only signature requests that explicitly set an ``expires_at`` will expire", so
    clearing it is a real operation and not an error: the request returns to the
    documented default and stops closing itself.

    Refused with 409 once the request is terminal. "They will not be able to sign
    or modify the signature request", and a deadline a seller could pull back
    after the fact would make the terminal state terminal in name only.
    """
    return engine.update_expiry(
        request_id,
        payload,
        actor=actor,
        source=f"PUT {router.prefix}/rooms/{{room_id}}/requests/{{request_id}}/expiry",
    )


# --------------------------------------------------------------------------- #
# Step 4: the reminder ledger
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/requests/{request_id}/reminders", status_code=201)
def run_reminders(
    room_id: str,
    request_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Evaluate the 3-and-7-day cadence and record every decision it reaches.

    "Signature request reminder emails will be sent to the signer 3 and 7 days
    before the signature request expires" and "If a signer was already reminded
    within 24 hours, we will skip the automated reminder."

    Both outcomes are rows. A skip is as visible as a send, because the skip is
    the rule that is easiest to get wrong and a skip nobody can see is a skip
    nobody can verify.

    An embedded request sends no email and records an event per reminder instead,
    which is what "Emails are muted in all embedded signing flows" requires.
    """
    return engine.remind(
        request_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/requests/{{request_id}}/reminders",
    )


@router.get("/rooms/{room_id}/reminders")
def list_reminders(
    room_id: str,
    request_id: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """The ledger for a room, newest first. Sends and skips alike."""
    rows = engine.reminders(room_id, request_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "sent": sum(1 for row in rows if row.get("outcome") == "sent"),
        "skipped": sum(1 for row in rows if row.get("outcome") == "skipped"),
        "reminders": rows,
    }


# --------------------------------------------------------------------------- #
# Step 5: signing, and the refusal at the deadline
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/requests/{request_id}/sign", status_code=201)
def sign(
    room_id: str,
    request_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """One signer signs their part.

    "If they attempt to sign the signature request past the expiration date, they
    will receive an error stating that the signature request is closed."

    Two refusals, and the response names which one: ``already_signed`` sends the
    signer back to the document, ``request_closed`` tells them the deadline has
    passed. They are different situations and a single message would be wrong for
    one of them.
    """
    return engine.sign(
        request_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/requests/{{request_id}}/sign",
    )


@router.get("/rooms/{room_id}/requests/{request_id}/can-sign")
def can_sign(
    room_id: str,
    request_id: str,
    email: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """May this person sign right now, and why not if not.

    A ``GET`` because it decides nothing and writes nothing. A signer who arrives
    early is not refused, so the answer is a report, and it names the reason the
    refusal would.
    """
    return engine.can_sign(request_id, email)


# --------------------------------------------------------------------------- #
# Step 5: the sweep
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/sweep", status_code=201)
def sweep(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Close every agreement in this room whose deadline has passed.

    "The expiry sweep marks incomplete signatures ``expired``." Four things it
    does not do, each of which is a researched sentence: it deletes nothing, it
    leaves an agreement with no expiry alone, it leaves a signer who signed alone,
    and it leaves a completed agreement alone.

    Every swept agreement publishes a ``signature_request_expired`` event carrying
    the expiration date and the signers who did not sign by it, which is what the
    embedded flow consumes in place of the email it mutes.
    """
    return engine.sweep(
        room_id,
        payload,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/sweep",
    )


@router.get("/rooms/{room_id}/events")
def list_events(
    room_id: str,
    request_id: str | None = Query(default=None),
    event: str | None = Query(default=None),
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """The event stream, newest first.

    A signed integration reads this list rather than receiving a webhook, because
    this product sends no HTTP to a third party. The event name is the vendor's,
    so a consumer written against the vendor's documentation finds the name it
    subscribes to.
    """
    rows = engine.events(room_id, request_id=request_id, event=event)
    return {"room_id": room_id, "count": len(rows), "events": rows}


@router.get("/rooms/{room_id}/summary")
def summary(
    room_id: str,
    engine: ExpiryEngine = EngineDep,
) -> dict[str, Any]:
    """Counts for the room's header, and the invariants beside them.

    Read back from the store rather than accumulated, so the header cannot describe
    a state the store does not hold. The two invariants are here because they are
    the two things a reader looks for and does not find on a page: expiry keeps the
    document, and an agreement with no expiry never closes.
    """
    rows = engine.requests(room_id)
    ledger = engine.reminders(room_id)
    events = engine.events(room_id)
    return {
        "room_id": room_id,
        "requests": len(rows),
        "with_expiry": sum(1 for row in rows if row["has_expiry"]),
        "never_expires": sum(1 for row in rows if not row["has_expiry"]),
        "pending": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_PENDING),
        "expired": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_EXPIRED),
        "completed": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_COMPLETED),
        "embedded": sum(1 for row in rows if row.get("email_muted")),
        "reminders_sent": sum(1 for row in ledger if row.get("outcome") == "sent"),
        "reminders_skipped": sum(1 for row in ledger if row.get("outcome") == "skipped"),
        "expired_events": sum(1 for row in events if row.get("event") == vocab.EVENT_EXPIRED),
        "invariants": {
            "document_survives": vocab.DOCUMENT_SURVIVES,
            "link_survives": vocab.LINK_SURVIVES,
            "absent_expiry_never_expires": (
                "Only signature requests that explicitly set an expires_at will expire."
            ),
        },
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, written out a second time on purpose. The demo data's
#: audit rows have to name the same routes the router serves, and a change to the
#: prefix has to be made deliberately in both places.
PREFIX = "/api/wf-081"

SEND_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/requests"
REMIND_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/requests/{{request_id}}/reminders"
SIGN_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/requests/{{request_id}}/sign"
SWEEP_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/sweep"

DAY = 86400
HOUR = 3600


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Four agreements, and the states that are not all successes.

    The rows are produced by running the real :class:`ExpiryEngine`, so the demo
    cannot show a shape this workflow would not produce. It is deliberately mixed,
    because a demo of only green teaches a reviewer nothing:

    * **one expired agreement** whose last signer missed the deadline, so the
      incomplete signers are ``expired`` and the audit event names them;
    * **one expired agreement** where one signer signed in time, so "Completed
      signers stay ``signed``" is a row rather than a claim;
    * **one open agreement** inside the 7-day window, so the reminder ledger shows
      a real send;
    * **one open agreement with no expiry at all**, so the sweep leaves it alone
      and "By default signature requests do not expire" is visible;
    * **one embedded agreement** that expires, so the event stream shows the
      ``signature_request_expired`` event standing in for the email it mutes;
    * a **reminder skipped by the 24-hour dedupe**, because the dedupe is the rule
      a reviewer most wants to see.

    Every character in the returned string is encodable by cp1252. The seeder
    prints it on a Windows console, and a single RIGHTWARDS ARROW in one recovered
    feature broke the whole seed.
    """
    store = RecordStore(db)
    clock = context.get("now")
    if clock is None:
        from datetime import datetime, timezone

        clock = datetime.now(timezone.utc)
    engine = ExpiryEngine(store, now=lambda: clock)

    given: list[str] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:3]
    names = [
        "Northwind Traders - agreement expiry",
        "Contoso Health - agreement expiry",
        "Fabrikam Logistics - agreement expiry",
    ]
    created = 0
    for index in range(3):
        if index < len(room_ids):
            continue
        name = names[index]
        record = store.create("room", {"name": name, "account": name.split(" - ")[0]}, actor="dana")
        room_ids.append(record["id"])
        created += 1
    room_id = room_ids[0]

    def at(days: float) -> int:
        return engine_epoch(clock) + int(days * DAY)

    def in_(seconds: int) -> int:
        """A deadline ``seconds`` from now, left to the engine to round down."""
        return engine_epoch(clock) + seconds

    # -- one past its deadline, every signer missed it ----------------------- #
    # The expiry rules refuse a deadline less than one day out, so these cannot be
    # sent already expired. They are sent open with the shortest legal deadline
    # and closed later by the sweep, on a clock that has moved past it. That is the
    # shape a real deadline takes as well.
    missed = engine.send(
        room_id,
        {
            "subject": "Master services agreement - Northwind",
            "document": "northwind-msa.pdf",
            "requester_email": "dana@northwind.example",
            vocab.EXPIRES_AT: at(1),
            "signatures": [
                {
                    "email": "buyer@northwind.example",
                    "name": "Ada Byron",
                    "preferred_timezone": "Europe/London",
                },
                {
                    "email": "legal@contoso.example",
                    "name": "Luis Ortega",
                    "preferred_timezone": "America/New_York",
                },
            ],
        },
        actor="dana",
        source=SEND_SOURCE,
    )

    # -- one past its deadline, one signer made it in time ------------------- #
    partial = engine.send(
        room_id,
        {
            "subject": "Order form - Contoso renewal",
            "document": "contoso-order-form.pdf",
            "requester_email": "dana@northwind.example",
            vocab.EXPIRES_AT: at(1),
            "signatures": [
                {
                    "email": "buyer@contoso.example",
                    "name": "Ada Byron",
                    "preferred_timezone": "Asia/Kolkata",
                },
                {
                    "email": "legal@contoso.example",
                    "name": "Luis Ortega",
                    "preferred_timezone": "America/New_York",
                },
            ],
        },
        actor="dana",
        source=SEND_SOURCE,
    )
    engine.sign(
        partial["id"],
        {"email": "legal@contoso.example"},
        actor="legal@contoso.example",
        source=SIGN_SOURCE,
    )

    # -- one open, sitting in the 3-day window ------------------------------- #
    # Two and a half days out is inside the 3-day window and outside the 7-day
    # one, so the first pass sends the 3-day reminder and nothing else.
    open_request = engine.send(
        room_id,
        {
            "subject": "Mutual action plan - Fabrikam pilot",
            "document": "fabrikam-map.pdf",
            "requester_email": "dana@northwind.example",
            vocab.EXPIRES_AT: in_(int(2.5 * DAY)),
            "signatures": [
                {
                    "email": "buyer@fabrikam.example",
                    "name": "Ada Byron",
                    "preferred_timezone": "America/Los_Angeles",
                }
            ],
        },
        actor="dana",
        source=SEND_SOURCE,
    )
    first_pass = engine.remind(open_request["id"], actor="scheduler", source=REMIND_SOURCE)

    # -- one open, with no expiry at all ------------------------------------- #
    never = engine.send(
        room_id,
        {
            "subject": "Data processing addendum - Fabrikam",
            "document": "fabrikam-dpa.pdf",
            "requester_email": "dana@northwind.example",
            "signatures": [{"email": "ops@fabrikam.example", "name": "Ops"}],
        },
        actor="dana",
        source=SEND_SOURCE,
    )

    # -- one embedded agreement, closing on the same clock ------------------- #
    embedded = engine.send(
        room_id,
        {
            "subject": "Enterprise renewal - embedded signing",
            "document": "northwind-renewal.pdf",
            "requester_email": "dana@northwind.example",
            "flow": "embedded",
            vocab.EXPIRES_AT: at(1),
            "signatures": [{"email": "buyer@northwind.example", "name": "Ada Byron"}],
        },
        actor="dana",
        source=SEND_SOURCE,
    )

    # Two hours later, inside the 24-hour dedupe. The open agreement is read
    # again and the second pass is a skip: "If a signer was already reminded
    # within 24 hours, we will skip the automated reminder."
    shortly_after = clock_from(engine_epoch(clock) + 2 * HOUR)
    again = ExpiryEngine(store, now=lambda: shortly_after)
    second_pass = again.remind(open_request["id"], actor="scheduler", source=REMIND_SOURCE)

    # A day and an hour later: the three short-deadline agreements are past theirs
    # and the open one is not. A second engine on that clock runs the sweep, which
    # is also how a test shows the sweep is driven by a caller and not by a thread.
    later = clock_from(engine_epoch(clock) + int(DAY) + HOUR)
    sweeper = ExpiryEngine(store, now=lambda: later)
    swept = sweeper.sweep(room_id, actor="scheduler", source=SWEEP_SOURCE)
    room = summary_payload(sweeper, room_id)

    missed_row = next((row for row in swept["swept"] if row["id"] == missed["id"]), None)
    partial_row = next((row for row in swept["swept"] if row["id"] == partial["id"]), None)
    # Both rows above are read back rather than assumed, so the string the seeder
    # prints reports what the store holds rather than what this function meant to
    # do. The two agreements the sweep must NOT touch are checked the same way.
    never_still_open = sweeper.request_view(never["id"])["status"] == vocab.REQUEST_STATUS_PENDING
    embedded_event = any(row.get("request_id") == embedded["id"] for row in sweeper.events(room_id))
    return (
        f"{room['requests']} agreement(s) in one room: "
        f"{room['expired']} expired and {room['pending']} still open, "
        f"1 with no expires_at {'still open' if never_still_open else 'NOT still open'}, "
        f"1 embedded whose expiry "
        f"{'published' if embedded_event else 'did NOT publish'} "
        f"signature_request_expired with no email sent; "
        f"the sweep flipped {len(missed_row['swept']) if missed_row else 0} unsigned "
        f"signature(s) to expired on one agreement and kept "
        f"{len(partial_row['kept_signed']) if partial_row else 0} signer(s) signed on another, "
        f"and left {swept['skipped_count']} request(s) alone; "
        f"{first_pass['sent']} reminder(s) sent at the 3-day lead and "
        f"{second_pass['skipped']} skipped by the 24-hour dedupe; "
        f"{room['expired_events']} expiry event(s) published"
        + (f"; {created} room(s) created for the per-room states" if created else "")
        + f"; {len(given)} room(s) from the seeder"
    )


def engine_epoch(clock: Any) -> int:
    from dsr.security_governance.expiry_rules import epoch_seconds

    return epoch_seconds(clock)


def clock_from(seconds: int):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def summary_payload(engine: ExpiryEngine, room_id: str) -> dict[str, Any]:
    """The same counts the summary route serves, read directly from the engine."""
    rows = engine.requests(room_id)
    ledger = engine.reminders(room_id)
    events = engine.events(room_id)
    return {
        "requests": len(rows),
        "expired": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_EXPIRED),
        "pending": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_PENDING),
        "completed": sum(1 for row in rows if row["status"] == vocab.REQUEST_STATUS_COMPLETED),
        "expired_events": sum(1 for row in events if row.get("event") == vocab.EVENT_EXPIRED),
        "ledger": ledger,
    }
