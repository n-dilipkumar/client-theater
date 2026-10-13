"""WF-097: track buyer engagement on a shared quote and drive follow-up.

The rules live in :mod:`dsr.quote_engagement`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary the page renders, served from one place so the panel cannot
  drift from the rules that validate it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Every write passes a ``source`` built from ``router.prefix`` and the route's own
path, rather than a label written once. That is what keeps every audit row naming
a URL the app actually serves.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-097.md``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quote_engagement.engagement import (
    EXPIRING_WITHIN_DAYS,
    NO_ACTIVITY_AFTER_DAYS,
    QuoteEngagementService,
    vocabulary,
)
from dsr.quote_engagement.rules import QuoteEngagementRuleError
from dsr.quote_engagement.vocabulary import EVENT, SETTINGS
from dsr.store import RecordStore, parse_where

FEATURE = {
    "id": "wf-097-track-buyer-engagement-on-a-shared-quote",
    "ticket": "WF-097",
    "name": "Track buyer engagement on a shared quote",
    "description": (
        "Record buyer and seller activity on a shared quote, maintain the four "
        "engagement counters against a configurable threshold, and drive the "
        "recommended follow-ups. A buyer who opened the PDF in the email is on "
        "the timeline and is not a view."
    ),
    "nav": [
        {
            "id": "wf-097-quote-engagement",
            "label": "Quote engagement",
        }
    ],
}

router = APIRouter(prefix="/api/wf-097", tags=["WF-097"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _rule_error(request: Request, exc: QuoteEngagementRuleError) -> JSONResponse:
    """A rule refused the request.

    422 rather than 400: the request was well-formed and the rules are what
    refused it. The core ``AuditError`` handler decides its status by searching
    the message for the words "conflict" or "exists", which would make a status
    code a function of prose. This type is this workflow's own, so the mapping is
    declared here and does not depend on how a message is worded.
    """
    return JSONResponse(
        status_code=422, content={"error": "quote_engagement_refused", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {QuoteEngagementRuleError: _rule_error}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_service(store: RecordStore = StoreDep) -> QuoteEngagementService:
    """The service, built on the shared audited store.

    Composed from the documented dependency seam rather than reaching for
    ``request.app.state``, so a test can construct it and the audit row is still
    written in the same transaction as the change.
    """
    return QuoteEngagementService(store)


ServiceDep = Depends(get_service)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary_route() -> dict[str, Any]:
    """Every label, class and bound the rules use, served from one place.

    The page renders these rather than repeating them, so a panel cannot offer a
    seller an event label the write route will refuse.
    """
    return vocabulary()


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """The headline figures, and the names of any that cannot be computed."""
    return service.summary(room_id=room_id)


@router.get("/settings")
def get_settings(
    room_id: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """The workspace's analysis settings.

    "The activity threshold is configurable" and the research gives no number, so
    an unconfigured key comes back ``None`` and is listed under
    ``unconfigured_keys``. There is no default the caller could mistake for a
    decision the workspace made.
    """
    return service.settings(room_id=room_id)


@router.put("/settings")
def put_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """Write the analysis settings. Every value is validated before anything is.

    The patch merges, so writing the threshold leaves the rest of the row alone,
    and a refused value leaves the whole row exactly as it was.
    """
    return service.save_settings(
        payload,
        room_id=room_id,
        actor=actor,
        source=_source("PUT", "/settings"),
    )


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    activity_class: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    signing_status: str | None = Query(default=None),
    where: str | None = Query(
        default=None, description='JSON object or "k=v,k2=v2" for advanced filters'
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """The quote index, one row per quote with the four researched counters.

    "On the index page, read the **Last Activity Date**, **Activity Count**,
    **Activity Count Since Last Communication** and **View Count** columns; filter
    by **Status**, **Last activity date**, **Owner**, **Signing status**, or
    **Advanced filters**."

    Status, owner and signing status are arguments. ``where`` is the store's own
    JSON-path query, so a team can filter on a field it added itself. The last
    activity date filter is a rule rather than an argument, because it needs a
    window the workspace configures, and it is served on ``/follow-ups`` with the
    windows beside it.
    """
    rows = service.list_quotes(
        room_id=room_id,
        status=status,
        activity_class=activity_class,
        owner=owner,
        signing_status=signing_status,
        limit=limit,
    )
    if where:
        try:
            filters = parse_where(where)
        except ValueError as exc:
            raise QuoteEngagementRuleError(str(exc)) from exc
        wanted = {row["id"] for row in service.advanced(filters, room_id=room_id, limit=1000)}
        rows = [row for row in rows if row["id"] in wanted]
    return {"count": len(rows), "entries": rows, "settings": service.settings(room_id=room_id)}


@router.get("/quotes/{quote_id}/engagement")
def quote_engagement(
    quote_id: str,
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """One quote's counters and the facts around them.

    This is the *Quote activities* card in the researched quote preview, and the
    same answer the sidebar and the full timeline are read from.
    """
    return service.engagement(quote_id)


@router.get("/quotes/{quote_id}/activity")
def quote_activity(
    quote_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """A quote's activity timeline, newest first."""
    entries = service.timeline(quote_id, limit=limit)
    return {"count": len(entries), "entries": entries}


@router.post("/quotes/{quote_id}/activity", status_code=201)
def record_activity(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """Record one activity event and bring the quote's counters up to date.

    "Activity events are written server-side without user action." The body
    carries ``event``, an optional ``surface`` and an optional ``occurred_at``.

    The surface is the whole of the researched distinction: a view on the quote
    link counts, and a buyer opening the PDF attached to the email does not. It is
    a parameter rather than a second event label because the researched vocabulary
    has one label for it, and a second label would make this timeline disagree
    with the platform a seller is reading.
    """
    return service.record_event(
        quote_id,
        event=payload.get("event"),
        surface=payload.get("surface"),
        occurred_at=payload.get("occurred_at"),
        actor=actor or payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/activity"),
    )


@router.get("/chart")
def chart(
    room_id: str | None = Query(default=None),
    include_expired: bool = Query(default=False),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """The expiration-by-engagement bubble chart's points.

    "quotes below the threshold render black and above it green; hover for name,
    expiration date and activity count; tick **Show older expired quotes**."

    ``include_expired`` is that tick box, and the answer reports how many expired
    quotes it hid, because a tick box with no count beside it reads as a control
    that does nothing.
    """
    return service.chart(room_id=room_id, include_expired=include_expired)


@router.get("/overview")
def overview(
    room_id: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """Pipeline overview figures plus the Recent quote activity list.

    "Under **Pipeline overview** read the value of high-activity quotes, quotes
    at risk of expiring, and the quote acceptance rate; under **Recent quote
    activity** see name/last activity/amount/expiration."

    One answer for one panel, because two queries could be taken a second apart
    and disagree. Each figure carries the basis it was computed on, so a zero
    from a figure that could not be computed never reads as a measurement.
    """
    return service.overview(room_id=room_id)


@router.get("/follow-ups")
def follow_ups(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """The Recommended follow-ups panel, with the windows it was measured on.

    "view recommended follow-ups for quotes based on activity on the quote, and
    expiration dates (e.g., quotes with no activity in a long time, or quotes
    that'll expire soon)".

    A window the workspace has not configured is reported as not configured
    rather than defaulted, so a quiet panel says why it is quiet.
    """
    return service.follow_ups(room_id=room_id, limit=limit)


@router.post("/follow-ups/{quote_id}/send", status_code=201)
def send_follow_up(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: QuoteEngagementService = ServiceDep,
) -> dict[str, Any]:
    """Act on a recommendation: record a new **Quote sent** for the quote.

    "clicking **Send a follow up** which opens the **Send quote** dialog" and
    "the seller re-sends the quote email from the recommendation, generating a
    new `Quote sent` activity and (if previously expired/recalled) a new send".

    No message is transmitted. This product has no mail transport, so what the
    answer reports is the recorded send and whether it renewed an expired or
    recalled quote.
    """
    return service.send_follow_up(
        quote_id,
        actor=actor or payload.get("actor"),
        surface=payload.get("surface"),
        source=_source("POST", f"/follow-ups/{quote_id}/send"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Five quotes covering every state this workflow distinguishes.

    A high-activity quote whose buyer opened the hosted link, a low-activity
    quote whose buyer only ever opened the PDF attached to the email, a quote
    that is quiet enough to be recommended and another that is about to expire, a
    quote past its expiration date, and one the buyer has already accepted. The
    first two are what make the view rule visible on the page a reviewer opens: the
    second one has activity and no views, which is the researched distinction and
    the only way to see it without reading the source.

    The settings record carries a configured threshold and both windows, because
    the workflow ships no defaults. A demo whose threshold is unset classifies
    nothing, and a reviewer would see an empty dashboard and learn nothing about
    the feature they are reviewing.

    Every character of the returned string is cp1252-encodable. The seeder prints
    it on a Windows console, and one arrow character in one recovered feature broke
    the whole seed.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return None
    room_id = rooms[0][0]
    now = context["now"]
    seeded_ids: list[str] = []

    def quote(title, **payload):
        record = db.create(
            "quote",
            {"title": title, "status": "PUBLISHED", **payload},
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        seeded_ids.append(record["id"])
        return record

    def event(quote_id, label, days_ago, **payload):
        moment = now - timedelta(days=days_ago)
        side = payload.pop("side", None) or ("buyer" if label in _BUYER_SEED_LABELS else "seller")
        return db.create(
            EVENT,
            {
                "kind": "quote_engagement_event",
                "quote_id": quote_id,
                "event": label,
                "side": side,
                "surface": payload.pop("surface", "quote_link"),
                "occurred_at": moment.isoformat(timespec="seconds"),
                "recorded_by": payload.pop("recorded_by", "buyer"),
                **payload,
            },
            room_id=room_id,
            actor="system",
            source="seed",
        )

    engaged = quote(
        "Northwind enterprise renewal",
        quote_number="Q-2026-021",
        owner="dana",
        hs_quote_amount=28500.0,
        locked_amount=28500.0,
        expires_at=(now + _days(21)).isoformat(timespec="seconds"),
    )
    event(engaged["id"], "Quote published", 30, side="seller")
    event(engaged["id"], "Quote sent", 29, side="buyer", surface="email")
    for offset in (26, 24, 21, 19, 17, 15, 12, 9, 7, 5, 3, 1):
        event(engaged["id"], "Quote viewed", offset, surface="quote_link")
    event(engaged["id"], "Quote downloaded", 8)
    event(engaged["id"], "Quote buyer signed", 2)

    pdf_only = quote(
        "Contoso security review",
        quote_number="Q-2026-022",
        owner="sam",
        hs_quote_amount=8400.0,
        locked_amount=8400.0,
        expires_at=(now + _days(45)).isoformat(timespec="seconds"),
    )
    event(pdf_only["id"], "Quote published", 40, side="seller")
    event(pdf_only["id"], "Quote sent", 39, side="buyer", surface="email")
    for offset in (30, 22, 14):
        # The researched exception: the buyer opened the PDF in the email. The
        # event is on the timeline and the View Count stays at zero.
        event(pdf_only["id"], "Quote viewed", offset, surface="email_pdf_attachment")

    quiet = quote(
        "Fabrikam logistics renewal",
        quote_number="Q-2026-023",
        owner="dana",
        hs_quote_amount=12000.0,
        locked_amount=12000.0,
        expires_at=(now + _days(90)).isoformat(timespec="seconds"),
    )
    event(quiet["id"], "Quote published", 120, side="seller")
    event(quiet["id"], "Quote sent", 119, side="buyer", surface="email")
    event(quiet["id"], "Quote viewed", 96, surface="quote_link")

    expiring = quote(
        "Adventure Works pilot",
        quote_number="Q-2026-024",
        owner="sam",
        hs_quote_amount=15000.0,
        locked_amount=15000.0,
        expires_at=(now + _days(4)).isoformat(timespec="seconds"),
    )
    event(expiring["id"], "Quote published", 40, side="seller")
    event(expiring["id"], "Quote sent", 39, side="buyer", surface="email")
    event(expiring["id"], "Quote viewed", 30, surface="quote_link")
    event(expiring["id"], "Quote viewed", 11, surface="quote_link")

    lapsed = quote(
        "Litware expired proposal",
        quote_number="Q-2026-025",
        status="EXPIRED",
        owner="dana",
        hs_quote_amount=4200.0,
        locked_amount=4200.0,
        expires_at=(now - _days(6)).isoformat(timespec="seconds"),
    )
    event(lapsed["id"], "Quote published", 70, side="seller")
    event(lapsed["id"], "Quote sent", 69, side="buyer", surface="email")
    event(lapsed["id"], "Quote expired", 6, side="seller")

    accepted = quote(
        "Tailspin accepted expansion",
        quote_number="Q-2026-026",
        status="ACCEPTED",
        owner="dana",
        hs_quote_amount=9800.0,
        locked_amount=9800.0,
        expires_at=(now + _days(10)).isoformat(timespec="seconds"),
    )
    event(accepted["id"], "Quote published", 25, side="seller")
    event(accepted["id"], "Quote sent", 24, side="buyer", surface="email")
    event(accepted["id"], "Quote viewed", 20, surface="quote_link")
    event(accepted["id"], "Quote accepted", 12, side="buyer")

    db.create(
        SETTINGS,
        {
            "high_activity_threshold": 10,
            "email_channel_connected": False,
            "no_activity_after_days": 30,
            "expiring_within_days": 14,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    counts = _seed_counter_block(db, room_id, seeded_ids)
    return (
        f"6 quotes (1 engaged above the threshold, 1 with activity but no views "
        f"because the buyer opened the PDF, 1 quiet, 1 expiring in 4 days, 1 "
        f"expired and not accepted, 1 accepted) and 1 analysis setting with a "
        f"threshold of 10 activities, a 30 day quiet window and a 14 day expiry "
        f"window; {counts}"
    )


#: The labels the seeder records as buyer events. It is a local copy rather than
#: an import so the demo rows read the same way the rules read them, and the
#: mismatch this could cause is caught by the test that seeds and then reads the
#: counters back.
_BUYER_SEED_LABELS = frozenset(
    {
        "Quote sent",
        "Quote viewed",
        "Quote downloaded",
        "Quote printed",
        "Quote buyer signed",
        "Quote manually signed",
        "Quote accepted",
    }
)


def _days(count):
    """A day offset from the seeder's frozen ``now``, for a seeded date."""
    return timedelta(days=count)


def _seed_counter_block(db, room_id, quote_ids):
    """Recompute each seeded quote's counters so the demo shows real figures.

    A seed that wrote activity records and no counters would leave the page a
    reviewer opens showing zero activity on a quote with twelve recorded views,
    and the first thing that workflow would look broken is the one number it
    exists to produce. The counters are therefore written from the same rows the
    seeder just created, through the same shape the engine writes.

    Only the ids this call created are touched, so a quote another feature seeded
    into the same room is left exactly as that feature wrote it. The audit source
    is ``seed``, not a route, because no HTTP request is what produced these rows.
    """
    service = QuoteEngagementService(RecordStore(db))
    written = 0
    for quote_id in quote_ids:
        row = db.get(quote_id)
        if row is None:
            continue
        service.refresh_counters(row, actor="seed", source="seed")
        written += 1
    return f"counters recomputed on {written} seeded quotes"


__all__ = ("EXPIRING_WITHIN_DAYS", "FEATURE", "NO_ACTIVITY_AFTER_DAYS", "router", "seed")
