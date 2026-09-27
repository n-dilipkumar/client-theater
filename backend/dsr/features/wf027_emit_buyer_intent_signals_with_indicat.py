"""WF-027: emit buyer intent signals with indicators, urgency, and attribution.

The researched workflow, in full. A signal type is registered once per
integration, carrying a name, a ``data_shape``, a localized ICU description, at
least one indicator with a ``key`` and a ``metadata_shape``, and an attribution
list. Each qualifying DSR interaction then emits a live signal with ``type``,
``data``, ``indicators[]``, ``urgency``, ``occurred_at``, a UUID4
``idempotency_key``, an attribution object and ``broadcast_notification``, and
Salesloft publishes the rendered description to the seller's Live Feed.

The domain logic is in :mod:`dsr.signals`, which this module does not own and
which no other feature could have written into its own path. What lives here is
the three things a workflow has to take out of shared files: the HTTP surface, the
mapping from domain errors to responses, and the demo data.

What the contract meant for this build
---------------------------------------

**The prefix is ``/api/wf-027``, and room scoping is real.** The research
describes signals for a buyer's activity *inside a room*, so anything scoped to
one is served under ``/rooms/{room_id}/...``. The registry itself is not
room-scoped, because "per integration, the signal type can only be registered
once" makes a registration an integration-level object, and putting a room in
that key would make the same type registrable twice.

**``source=`` comes from the route.** Every write below passes
``f"{router.prefix}..."`` so the audit row names the route that actually served
it. A hardcoded string inside a domain method is a defect, and the same class of
bug has shipped in this codebase before: a feature's audit log kept naming a path
the app had stopped serving. ``source`` is a *required* keyword on every writing
method of :class:`~dsr.signals.engine.SignalEngine`, so omitting it is a
``TypeError`` at the call site rather than an untraceable row in production.

**One handler for the whole error hierarchy.** ``SignalError`` is the base of
every refusal in :mod:`dsr.signals`, and each carries its own ``status`` and
``code`` on the exception, so one handler can answer 400 for a malformed signal
and 409 for one that conflicts with the registry without being told which. It is
a domain type, so registering it globally cannot intercept anything unrelated
elsewhere in the product. ``RecordNotFound`` is deliberately *not* claimed: the
core app already maps it to 404, and two handlers for one type is a collision the
host refuses.

**Two routes for one decision, on purpose.** ``/signals`` is the strict researched
API and refuses anything it cannot vouch for. ``/interactions`` is the classifier
the data flow describes - "DSR interaction ... matched to a registered indicator"
- and reports its decision instead of raising, because a buyer who watched 40% of
a video is a fact rather than a caller error. Both report the same reasons, so
the two cannot disagree.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.signals import SignalEngine, SignalError
from dsr.signals.errors import IndicatorNotQualified
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-027-emit-buyer-intent-signals-with-indicat",
    "ticket": "WF-027",
    "name": "Emit buyer intent signals with indicators, urgency, and attribution",
    "description": (
        "Register a signal type once per integration, emit a live signal for each qualifying "
        "buyer interaction, and publish the rendered description to the seller's Live Feed. "
        "Indicators are held to the bound their own key states, urgency drives priority, and "
        "every signal says plainly that it makes nobody do anything."
    ),
    "nav": [{"id": "intent-signals", "label": "Intent signals"}],
}

router = APIRouter(prefix="/api/wf-027", tags=["wf027"])


def get_engine(store: RecordStore = StoreDep) -> SignalEngine:
    """A :class:`SignalEngine` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, and an ``app.state`` entry is exactly the edit to the shared
    ``dsr/api.py`` that the feature host exists to make unnecessary. Building it
    here also leaves the engine a plain object, which is what a test constructs.
    """
    return SignalEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _signal_error(request: Request, exc: SignalError) -> JSONResponse:
    """A domain refusal, answered with the status and code the error carries.

    One handler for the whole hierarchy. ``SignalError`` is the base of every
    refusal in :mod:`dsr.signals` - a malformed signal, an unregistered type, a
    type registered twice, an amendment that would break the contract - and all of
    them are the caller's to fix. The status rides on the exception rather than
    being decided here, because a duplicate registration and a missing required
    field are both this package's errors and only one of them conflicts with
    state that already exists.
    """
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "detail": str(exc), "status": exc.status},
    )


EXCEPTION_HANDLERS = {SignalError: _signal_error}


# --------------------------------------------------------------------------- #
# Vocabulary and the inference register
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary(engine: SignalEngine = EngineDep) -> dict[str, Any]:
    """Every published vocabulary, served as data.

    The urgencies, the attribution values and their precedence, the indicator
    claim grammar, the JSON-Schema keywords the validator honours, and the
    research's own specific-versus-vague indicator pair. A client renders its
    pickers from this rather than from a list compiled into the page, so a value
    added here reaches every client at once.
    """
    return engine.vocabulary()


@router.get("/inferences")
def inferences(engine: SignalEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and how to change each one.

    The research names the fields, the three urgencies, the idempotency rule and
    the indicator example. It does not say how a sender behaves on the edges of
    that, so the edges are collected here - named, traceable, and served - rather
    than left as comments in function bodies. The sourced half comes back beside
    the inferred half, because the point of the endpoint is to see where the line
    falls.
    """
    return engine.inferences()


# --------------------------------------------------------------------------- #
# Signal registrations
# --------------------------------------------------------------------------- #


@router.get("/registrations")
def list_registrations(
    integration_id: str | None = Query(default=None),
    type: str | None = Query(default=None, description="the machine-readable signal type"),
    include_withdrawn: bool = Query(default=False),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """The registry.

    Every row carries its lint findings. A registration nobody re-reads is where a
    vague indicator goes to live, and the research's own correction - an indicator
    should be very specific - is only actionable if the registrant can see which
    of theirs is not.
    """
    listed = engine.registrations(
        integration_id=integration_id, type_name=type, include_withdrawn=include_withdrawn
    )
    flagged = sum(
        1
        for row in listed
        if any(entry.get("severity") == "warning" for entry in row.get("warnings") or [])
    )
    return {"count": len(listed), "flagged": flagged, "registrations": listed}


@router.post("/registrations", status_code=201)
def register_signal_type(
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """Register a signal type, once per integration.

    A second registration of the same type on the same integration is refused with
    409 rather than stored: two registrations of one type would give that type two
    incompatible shapes and no way to tell which one a signal follows. To change
    what a registered type means, amend it - and only additively.

    An ``idempotency_key`` is accepted, and a repeat of one returns the
    registration that already exists with ``outcome: already_registered`` and a
    200, the same first-one-wins rule the signal route applies. 201 would tell the
    caller a new contract was created when nothing was.
    """
    result = engine.register(payload, actor=actor, source=f"POST {router.prefix}/registrations")
    if result["outcome"] == "already_registered" and response is not None:
        response.status_code = 200
    return result


@router.get("/registrations/{registration_id}")
def read_registration(registration_id: str, engine: SignalEngine = EngineDep) -> dict[str, Any]:
    """One registration, with the shape and the indicators it declares.

    A withdrawn registration is a 404 here. It is still listed by
    ``/registrations?include_withdrawn=true``, because signals already delivered
    under it still name it and the audit trail must not point at nothing.
    """
    record = engine.registration(registration_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"registration {registration_id} not found")
    return engine.present(record)


@router.patch("/registrations/{registration_id}")
def amend_registration(
    registration_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """Add to a registration. Never change it.

    "Globally installed signals should be considered an immutable API contract
    with Salesloft and only additive changes will be allowed", turned into a test
    this package can apply: an amendment is accepted only if it cannot invalidate
    anything that was already valid, and it may not rewrite a string a seller has
    already read. Every offending path comes back at once, so correcting a
    rejected amendment is one attempt rather than one per field.
    """
    return engine.amend(
        registration_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/registrations/{registration_id}",
    )


@router.delete("/registrations/{registration_id}")
def withdraw_registration(
    registration_id: str,
    actor: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """Withdraw a registration that has not delivered a signal yet.

    One that has is refused with 409, naming the count. Every stored signal names
    the registration it followed, so hiding that registration would leave the
    audit trail pointing at a path the API no longer serves - the exact defect the
    audit-source rule exists to prevent. Register a new signal type instead.
    """
    return engine.withdraw(
        registration_id,
        actor=actor,
        source=f"DELETE {router.prefix}/registrations/{registration_id}",
    )


# --------------------------------------------------------------------------- #
# Signals, scoped to a room
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/signals")
def list_signals(
    room_id: str,
    type: str | None = Query(default=None),
    urgency: str | None = Query(default=None, description="high | medium | low"),
    broadcast: bool | None = Query(default=None),
    seller: str | None = Query(default=None),
    locale: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """The signals in one room, newest first.

    ``broadcast=false`` returns the signals that were delivered but are not shown
    in the Live Feed, which is the distinction ``broadcast_notification`` draws.
    Every filter is a JSON path in the signal's own payload, resolved through the
    dynamic index, so a field a team added later is queryable without a change to
    this route.
    """
    listed = engine.signals(
        room_id=room_id,
        type_name=type,
        urgency=urgency,
        broadcast=broadcast,
        seller=seller,
        locale=locale,
        limit=limit,
    )
    return {"room_id": room_id, "count": len(listed), "signals": listed}


@router.post("/rooms/{room_id}/signals", status_code=201)
def emit_signal(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    response: Response = None,  # type: ignore[assignment] - FastAPI injects the real object
    actor: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """Emit one live signal against a registered type.

    The strict researched API. ``type`` must be registered, ``data`` must satisfy
    the registration's ``data_shape``, every indicator must be declared, its
    metadata must satisfy that indicator's ``metadata_shape``, and that metadata
    must satisfy the bound the indicator's own key states. The last one is a
    reading rather than a quotation, and it is what makes a specific indicator mean
    anything: a claim its own evidence contradicts is a sentence a seller would
    read and believe.

    A repeated ``idempotency_key`` answers 200 with ``outcome: "dropped"`` and the
    signal that was kept. The research says the first one wins; a dropped signal is
    not a failed request, and telling a sender its signal was rejected when the
    first one succeeded would be worse than useless.
    """
    result = engine.emit(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/signals",
    )
    if result["outcome"] == "dropped" and response is not None:
        response.status_code = 200
    return result


@router.post("/rooms/{room_id}/interactions")
def record_interaction(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """A raw DSR interaction: match it against the registered indicators, then emit.

    The researched flow is "On each qualifying DSR interaction, emit a live
    signal", which is two decisions, and this route makes both visible. It
    evaluates *every* declared indicator and reports each one's reason, so an
    interaction that fired nothing can say which bound it failed rather than
    returning an empty answer. Nothing is raised for a non-qualifying interaction
    and nothing is stored for one.

    Qualifying on trust is reported as such. An indicator whose key states no bound
    cannot be checked, so it qualifies - and the signal it produces carries
    ``checked: false``, so the trust is visible rather than silent.
    """
    return engine.interact(
        payload,
        room_id=room_id,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{{room_id}}/interactions",
    )


@router.get("/rooms/{room_id}/signals/{signal_id}")
def read_signal(
    room_id: str,
    signal_id: str,
    locale: str | None = Query(default=None),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """One signal: the sentence a seller would read, and the evidence under it."""
    signal = engine.signal(signal_id, locale=locale)
    if signal is None or signal["room_id"] != room_id:
        raise HTTPException(
            status_code=404, detail=f"signal {signal_id} not found in room {room_id}"
        )
    return signal


@router.get("/rooms/{room_id}/live-feed")
def live_feed(
    room_id: str,
    seller: str | None = Query(default=None, description="the receiving seller; omit for every seller"),
    locale: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    engine: SignalEngine = EngineDep,
) -> dict[str, Any]:
    """The seller's Live Feed for one room: highest urgency first, then newest.

    Only signals with ``broadcast_notification`` set. That flag "controls Live
    Feed display", so a signal carrying it false is still stored and still
    delivered - it is simply not shown here, which is what ``/signals`` is for.

    Every row repeats that it is not actionable, because "users may choose to not
    take action on a signal" and a seller who believes an intent signal is a task
    will stop acting on the ones that are.
    """
    return engine.live_feed(room_id=room_id, seller=seller, locale=locale, limit=limit)


@router.get("/rooms/{room_id}/summary")
def room_summary(room_id: str, engine: SignalEngine = EngineDep) -> dict[str, Any]:
    """Counts for the room, and the actionability note beside them.

    Counted over this room's signals rather than the whole collection, so a
    room's header says what happened in that room.
    """
    return engine.summary(room_id=room_id)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The shape the good registration declares. Kept beside the seed so the two
#: cannot drift: a demo whose own data does not satisfy its registration teaches a
#: reviewer nothing, and this is the failure mode a hand-written demo has.
ENGAGEMENT_DATA_SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_name": {"type": "string", "minLength": 1},
        "document_kind": {"type": "string", "enum": ["pdf", "video", "deck", "link", "order_form"]},
        "action": {"type": "string", "enum": ["viewed", "downloaded", "opened", "watched"]},
        "buyer_first_name": {"type": "string", "minLength": 1},
        "room_name": {"type": "string", "minLength": 1},
        "page": {"type": "integer", "minimum": 1},
    },
    "required": ["document_name", "action", "buyer_first_name", "room_name"],
}

#: The three registrations the demo registers - one per interaction type.
#:
#: One per type, not one for the whole engagement stream, and the reason is worth
#: stating because the first draft of this seed got it wrong. A registration
#: carries *one* localized description, so a registration whose indicators observe
#: different measurements has a description that names fields some of its signals
#: do not carry, and those signals render with a visible hole in the sentence. The
#: obvious fix - declare both measurements on both indicators' shapes - is worse,
#: because a shape naming two numeric fields is ambiguous, so the bound can no
#: longer be checked and the indicator silently qualifies on trust. One type, one
#: description, one measurement is the shape that has no hole in it.
#:
#: The third registration is the research's poor indicator, verbatim, and it is
#: registered on purpose. "Indicators should be very specific" is only actionable
#: if a registrant can see which of their own is not, so the demo carries one that
#: the lint objects to and the page shows the objection.
DEMO_REGISTRATIONS: tuple[dict[str, Any], ...] = (
    {
        "signal_name": "Deep engagement with shared content",
        "type": "document_engagement",
        "integration_id": "dsr",
        "description": {
            "en": "{buyer_first_name} spent {time_in_seconds, plural, =1 {# second} other {# seconds}} "
            "on {document_name} in {room_name}.",
            "fr": "{buyer_first_name} a passe {time_in_seconds, plural, =1 {# seconde} other {# secondes}} "
            "sur {document_name} dans {room_name}.",
        },
        "data_shape": ENGAGEMENT_DATA_SHAPE,
        "indicators": [
            {
                # The research's own good indicator, with its bound in the key and
                # the evidence in the metadata.
                "key": "spent_more_than_30s_on_site",
                "metadata_shape": {
                    "type": "object",
                    "properties": {"time_in_seconds": {"type": "integer", "minimum": 0}},
                    "required": ["time_in_seconds"],
                },
                "description": {
                    "en": "Spent {time_in_seconds} seconds on {document_name}, past the 30 second threshold.",
                    "fr": "A passe {time_in_seconds} secondes sur {document_name}, au-dela du seuil de 30 secondes.",
                },
            }
        ],
        "attribution": [
            "person_id",
            "account_id",
            "opportunity_id",
            "user_guid",
            "email_tracked_content_id",
        ],
        "broadcast_notification": True,
    },
    {
        "signal_name": "Video watched past three quarters",
        "type": "video_engagement",
        "integration_id": "dsr",
        "description": {
            "en": "{buyer_first_name} watched {watched_percent}% of {document_name}.",
            "fr": "{buyer_first_name} a regarde {watched_percent} % de {document_name}.",
        },
        "data_shape": {
            "type": "object",
            "properties": {
                "document_name": {"type": "string", "minLength": 1},
                "document_kind": {"type": "string", "enum": ["video"]},
                "action": {"type": "string", "enum": ["watched"]},
                "buyer_first_name": {"type": "string", "minLength": 1},
                "room_name": {"type": "string", "minLength": 1},
            },
            "required": ["document_name", "action", "buyer_first_name", "room_name"],
        },
        "indicators": [
            {
                # The second half of the research's data flow: "video watched >75%".
                "key": "watched_more_than_75_percent",
                "metadata_shape": {
                    "type": "object",
                    "properties": {"watched_percent": {"type": "integer", "minimum": 0, "maximum": 100}},
                    "required": ["watched_percent"],
                },
                "description": {
                    "en": "Watched {watched_percent}% of {document_name}, past the 75% threshold.",
                    "fr": "A regarde {watched_percent} % de {document_name}, au-dela du seuil de 75 %.",
                },
            }
        ],
        "attribution": ["person_id", "account_id", "user_guid"],
        "broadcast_notification": True,
    },
    {
        "signal_name": "Content opened, no threshold",
        "type": "content_opened",
        "integration_id": "dsr",
        "description": {"en": "{buyer_first_name} opened {document_name}."},
        "data_shape": {
            "type": "object",
            "properties": {
                "document_name": {"type": "string", "minLength": 1},
                "document_kind": {"type": "string"},
                "buyer_first_name": {"type": "string", "minLength": 1},
                "room_name": {"type": "string", "minLength": 1},
            },
            "required": ["document_name", "buyer_first_name", "room_name"],
        },
        "indicators": [
            {
                # The research's poor example, verbatim: the same measurement with
                # the claim removed. It states no bound, so it qualifies on trust
                # and the signal it produces says so.
                "key": "time_spent_on_site",
                "metadata_shape": {
                    "type": "object",
                    "properties": {"time_in_seconds": {"type": "integer", "minimum": 0}},
                },
                "description": {
                    "en": "Spent {time_in_seconds} seconds on {document_name}.",
                    "fr": "A passe {time_in_seconds} secondes sur {document_name}.",
                },
            }
        ],
        "attribution": ["person_id", "user_guid"],
        "broadcast_notification": True,
    },
)

#: The two seeded signals that are about the *rule* rather than about a buyer.
#: Named as constants because nothing stores them, so a reviewer looking for them
#: in the database will not find them, and the seeder's return string is the only
#: place they appear.
NOT_QUALIFYING_SECONDS = 6
REFUSED_SECONDS = 12


def _uuid4(rng: random.Random) -> str:
    """A reproducible version 4 UUID.

    The research specifies ``idempotency_key`` as a UUID4, so the demo's keys have
    to be real ones or the demo would be exercising a path the API refuses. Drawn
    from the seeder's own seeded generator rather than ``uuid4()``, so two runs of
    the seeder produce the same demo and a reviewer comparing two databases is not
    looking at noise.
    """
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three registrations, seven signals, and the states that are not all successes.

    The rows are produced by running the real :class:`SignalEngine`, so the demo
    cannot show a shape this workflow would not produce, and seeding never opens a
    socket. It is deliberately mixed, because a demo of only green teaches a
    reviewer nothing:

    * a ``high`` urgency signal and a ``low`` one, so the feed's priority rule is
      something you can look at rather than take on trust;
    * one signal with ``broadcast_notification`` off, so "stored and delivered but
      not shown" is a row rather than a claim;
    * one ``idempotency_key`` emitted twice, so first-one-wins leaves a visible
      ``duplicate_attempts`` count;
    * one signal on the vague-indicator registration, so "qualified on trust" has
      somewhere to appear;
    * one signal rendered in French against the French locale, and one rendered in
      ``de-AT`` against a registration with no German, so the locale fallback is a
      row rather than a claim;
    * one emission refused because its evidence failed the bound its own key
      states, and one interaction that did not qualify. Neither stores anything -
      which is the point, and the reason this function's return string names them.
    """
    store = RecordStore(db)
    engine = SignalEngine(store)
    rng: random.Random = context.get("rng") or random.Random("wf027")
    # ``backend/seed.py`` passes ``[(room_id, account), ...]``. A bare id is
    # accepted too, because a caller assembling a context by hand should not have
    # to know the tuple shape to seed a feature.
    rooms: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]
    base: datetime = context.get("now") or datetime.now(timezone.utc)
    source = "seed"

    registered = [
        engine.register(spec, actor="dana", source=source)["registration"]
        for spec in DEMO_REGISTRATIONS
    ]
    flagged = sum(
        1
        for row in registered
        if any(entry.get("severity") == "warning" for entry in row.get("warnings") or [])
    )
    if not rooms:
        return (
            f"{len(registered)} signal registrations ({flagged} flagged for a non-specific "
            "indicator), 0 signals (no rooms to scope them to)"
        )

    def at(minutes_ago: int) -> str:
        return (base - timedelta(minutes=minutes_ago)).isoformat()

    room_id, account = rooms[0]
    other_room = rooms[1][0] if len(rooms) > 1 else rooms[0][0]
    room_name = "Northwind Traders — Enterprise Evaluation"
    other_room_name = "Contoso Health — Security Review"

    def send(
        target_room: str,
        signal_type: str,
        indicator_key: str,
        observations: dict[str, Any],
        **overrides: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": signal_type,
            "data": {
                "document_name": overrides.pop("document_name", "Security & Compliance Pack"),
                "document_kind": overrides.pop("document_kind", "pdf"),
                "action": overrides.pop("action", "viewed"),
                "buyer_first_name": overrides.pop("buyer_first_name", "Priya"),
                "room_name": overrides.pop("room_name", room_name),
            },
            "indicators": [{"key": indicator_key, "metadata": observations}],
            "urgency": overrides.pop("urgency", "medium"),
            "occurred_at": overrides.pop("occurred_at", at(5)),
            "idempotency_key": overrides.pop("idempotency_key", _uuid4(rng)),
            "attribution": overrides.pop(
                "attribution", {"person_id": f"per_{rng.randrange(10**6):06d}"}
            ),
            "locale": overrides.pop("locale", "en"),
        }
        payload.update(overrides)
        return engine.emit(payload, room_id=target_room, actor="dana", source=source)

    emitted: list[dict[str, Any]] = []

    # High urgency on the video, so the feed's priority rule has something to order.
    # A separate signal type, because a registration carries one description and a
    # video signal does not carry the seconds a document signal does.
    emitted.append(
        send(
            room_id,
            "video_engagement",
            "watched_more_than_75_percent",
            {"watched_percent": 92},
            urgency="high",
            document_name="Customer Reference — Northwind",
            document_kind="video",
            action="watched",
            occurred_at=at(3),
        )
    )
    # The researched good indicator, with the evidence that satisfies its bound.
    emitted.append(
        send(
            room_id,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": 214},
            occurred_at=at(18),
        )
    )
    # Low urgency, and not broadcast: stored and delivered, absent from the feed.
    emitted.append(
        send(
            room_id,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": 47},
            urgency="low",
            occurred_at=at(41),
            broadcast_notification=False,
        )
    )
    # The vague indicator: qualifies on trust, and the signal carries checked=false.
    emitted.append(
        send(
            room_id,
            "content_opened",
            "time_spent_on_site",
            {"time_in_seconds": 30},
            urgency="low",
            document_name="Pricing One-Pager",
            action="opened",
            occurred_at=at(55),
        )
    )
    # First-one-wins, made visible: one key, sent twice, second one dropped.
    repeated_key = _uuid4(rng)
    emitted.append(
        send(
            room_id,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": 88},
            urgency="high",
            occurred_at=at(62),
            idempotency_key=repeated_key,
            document_name="Contract Draft",
        )
    )
    duplicate = send(
        room_id,
        "document_engagement",
        "spent_more_than_30s_on_site",
        {"time_in_seconds": 999},
        urgency="high",
        occurred_at=at(63),
        idempotency_key=repeated_key,
        document_name="Contract Draft",
    )

    # A second room, so the room scoping is a row rather than a claim. Rendered in
    # French, which the first registration declares, and attributed to a named
    # user rather than a person, so the precedence has something to resolve.
    emitted.append(
        send(
            other_room,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": 71},
            occurred_at=at(9),
            document_name="Pack Securite et Conformite",
            action="opened",
            room_name=other_room_name,
            buyer_first_name="Camille",
            locale="fr",
            attribution={"user_guid": "usr_1042", "account_id": f"acc_{account}"},
        )
    )
    # And one in a locale nobody declared, so the fallback chain is a row.
    emitted.append(
        send(
            other_room,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": 33},
            urgency="low",
            occurred_at=at(74),
            document_name="Implementation Roadmap",
            room_name="Fabrikam Logistics — Renewal",
            buyer_first_name="Jonas",
            locale="de-AT",
            attribution={"account_id": f"acc_{account}"},
        )
    )

    # An emission whose evidence fails the bound its own indicator states. Refused,
    # and nothing stored: a specific claim contradicted by its own metadata is a
    # sentence a seller would read and believe.
    try:
        send(
            room_id,
            "document_engagement",
            "spent_more_than_30s_on_site",
            {"time_in_seconds": REFUSED_SECONDS},
            occurred_at=at(20),
        )
        refused = False
    except IndicatorNotQualified:
        refused = True

    # An interaction that does not qualify. Nothing stored, and the reason named:
    # the decision is reported and the history is not polluted.
    not_qualifying = engine.interact(
        {
            "type": "document_engagement",
            "data": {
                "document_name": "API Integration Guide",
                "document_kind": "pdf",
                "action": "viewed",
                "buyer_first_name": "Priya",
                "room_name": room_name,
            },
            "observations": {"time_in_seconds": NOT_QUALIFYING_SECONDS, "watched_percent": 20},
            "occurred_at": at(80),
            "idempotency_key": _uuid4(rng),
            "attribution": {"person_id": "per_000042"},
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    return (
        f"{len(registered)} signal registrations ({flagged} flagged for a non-specific indicator), "
        f"{len(emitted)} signals across {len({room_id, other_room})} rooms "
        f"(1 withheld from the feed, 1 qualified on an unbounded indicator, "
        f"{duplicate['duplicate_attempts']} duplicate dropped, 1 rendered in French, "
        f"1 rendered via locale fallback), "
        f"{1 if refused else 0} emission refused for a bound its own evidence failed, "
        f"1 interaction did not qualify ({not_qualifying['indicators'][0]['reason']})"
    )
