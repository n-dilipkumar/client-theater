"""WF-082: verify and IP-allowlist inbound provider webhooks.

A build from a researched specification, not a port. There was no source branch. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-082.md``, quoted in full
in issue 129, and it says of itself that an implementer who needs a flow the evidence does
not contain "must derive it and record the derivation, not assume it". The derivation is
recorded in :mod:`dsr.security_governance.webhook_inferences` and served at
``GET /decisions``.

The rules live in :mod:`dsr.security_governance.webhook_rules` and
:mod:`dsr.security_governance.webhook_signing`. They are not restated here. This module is
the three things a feature contributes and the three things it must never contribute.

What this module contributes
----------------------------

* The route table, under the prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and this
  feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``, ``dsr/db/audited.py``,
  ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``, ``lib/features.js``,
  ``components/ui.jsx``, ``vite.config.js``. Twelve workflow branches each editing those
  files is why none of the original twelve merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from :data:`router`, so
  the audit row names the route that actually served the write.
* No edit to ``dsr/security_governance/__init__.py``. That package is shared with WF-073,
  WF-075, WF-080 and WF-081, and two workflow branches appending to one package initializer
  collide on the same lines for no benefit. Python imports a submodule without the package
  listing it.

The shape the specification pins, and where each half lives
-----------------------------------------------------------

**The request is ``multipart/form-data`` with the payload in a part named ``json``.** It is
not a raw JSON body, and :func:`receive_event` reads the part by name rather than calling
``request.json()``. The evidence is explicit: "Provider POSTs events as
``multipart/form-data`` with the payload in a field named ``json``".

**The acknowledgement is a magic string.** :func:`receive_event` answers
``200`` with :data:`~dsr.security_governance.webhook_vocabulary.ACKNOWLEDGEMENT_BODY` on every
accepted delivery including a duplicate, because the evidence says the body "contains the
string ``Hello API Event Received``" and a 200 with an empty body is still a failed callback.
That is the difference between a handler that works and one that burns a twenty hour retry
ladder, and after ten consecutive failures the provider clears the callback URL outright.

**Three checks, and only then does the handler act.** The engine runs them in
:data:`~dsr.security_governance.webhook_vocabulary.CHECK_ORDER` and the response carries all
three, so a caller is never told which check ran when it only wanted to know one thing.

**The two digests are not interchangeable.** ``Content-Sha256`` is base64 over the whole JSON
payload; ``event_hash`` is the hex HMAC of ``event_time`` concatenated with ``event_type``
with no separator. Conflating them authenticates a payload that was never read, and the
no-separator rule rejects every real delivery, so both are pinned by a test.

**The retry contract is served, not described.** The specification's stated reason for a
machine-readable catalogue is retry logic built "instead of scraping the human-readable page",
so ``GET /retry-policy`` answers the ladder, the thirty second timeout and the self-disable
threshold as data.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import webhook_vocabulary as vocab
from dsr.security_governance.webhook_engine import (
    HONESTY,
    WebhookRegistrationInvalid,
    WebhookRegistrationNotFound,
    WebhookVerifier,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-082-verify-and-ip-allowlist-inbound-provid",
    "ticket": "WF-082",
    "name": "Verify and IP-allowlist inbound provider webhooks",
    "description": (
        "Accept an inbound provider callback only after its source IP, its Content-Sha256 "
        "payload digest and its event_hash HMAC have all verified. A delivery that passes "
        "all three is recorded once and answered with the magic string the provider checks, "
        "including when the provider retries it."
    ),
    "nav": [{"id": "wf-082-inbound-verification", "label": "Inbound verification"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/library``). The issue names
#: ``/api/wf082`` and that is the form used here. The domain package publishes the same
#: string as ``vocab.ROUTER_PREFIX`` so its docstrings cannot drift from what is served.
router = APIRouter(prefix=vocab.ROUTER_PREFIX, tags=["WF-082"])

#: The domain package and this router must name the same prefix. A drift here is the same
#: defect as an audit source naming a stale route, so it fails at import rather than in a
#: deployment. The host records a feature that raises on import as failed and keeps the rest
#: of the product running, so this check costs a line of startup and saves an outage.
assert router.prefix == vocab.ROUTER_PREFIX


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf082_http.py`` asserts every source this router can record
    matches a concrete ``(method, path)`` the host mounted.
    """
    return f"{method} {router.prefix}{path}"


def _honesty() -> dict[str, Any]:
    """The four fields every JSON response carries.

    A copy of :data:`dsr.security_governance.webhook_engine.HONESTY`, not a second definition.
    The engine already puts the four fields into every projection it returns, so the routes
    spread the same dict rather than restating the sentences: a caveat written twice can
    drift, and a caveat that appears in four responses out of five is a caveat a reader
    learns to skip.
    """
    return dict(HONESTY)


def get_engine(store: RecordStore = StoreDep) -> WebhookVerifier:
    """A :class:`WebhookVerifier` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves both
    overridable in a test instead of hanging a long-lived object off ``app.state``, which is a
    shared file this feature may not edit.
    """
    return WebhookVerifier(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Both types are declared in dsr.security_governance.webhook_engine and raised by nothing else
# in the product. That is what makes it safe to map them here: the host refuses a second
# feature registering a handler for the same type, and a handler for ValueError or
# LookupError would intercept those exceptions across the whole application.


def _registration_invalid(request: Request, exc: WebhookRegistrationInvalid) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "errors": exc.errors,
            **_honesty(),
        },
    )


def _registration_not_found(request: Request, exc: WebhookRegistrationNotFound) -> JSONResponse:
    """404, and it names what to register rather than only what is missing.

    The remedy names the route to point the provider at, built from :data:`router`, so the
    answer an operator reads is a route the app actually serves.
    """
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "remedy": (
                f"Register a callback_url for this room at "
                f"{_source('POST', '/rooms/{room_id}/callbacks')}, then have the provider "
                f"POST to {_source('POST', '/rooms/{room_id}/events')}."
            ),
            **_honesty(),
        },
    )


EXCEPTION_HANDLERS = {
    WebhookRegistrationInvalid: _registration_invalid,
    WebhookRegistrationNotFound: _registration_not_found,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None),
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """The board's headline numbers. Reads only, so it writes no audit row."""
    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary(engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """Every published constant, served as data.

    The headers, the part name, the magic string, the three checks in order, the two digests
    and how they differ, the two event types and the warning between them, the retry ladder,
    the thirty second timeout, the self-disable threshold, the error catalogue and the
    staleness window. A client renders its labels from this rather than from a list compiled
    into the page, so the editor can never disagree with the validator.
    """
    return engine.vocabulary()


@router.get("/retry-policy")
def retry_policy(engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """The contract this product owes its consumers, as data.

    The specification's stated reason for a machine-readable catalogue is retry logic built
    "instead of scraping the human-readable page", so the ladder, the timeout and the
    self-disable threshold are served rather than described. A consumer can schedule its
    retries from this without reading a sentence.
    """
    vocabulary_payload = engine.vocabulary()
    return {
        "provider_timeout_seconds": vocab.PROVIDER_TIMEOUT_SECONDS,
        "retry_multiplier": vocab.RETRY_MULTIPLIER,
        "retry_ladder": vocabulary_payload["retry_ladder"],
        "consecutive_failure_limit": vocab.CONSECUTIVE_FAILURE_LIMIT,
        "self_disable": vocabulary_payload["self_disable"],
        "acknowledgement": vocabulary_payload["acknowledgement"],
        "note": (
            "The ladder is the provider's, not this room's. A handler that answers inside "
            f"{vocab.PROVIDER_TIMEOUT_SECONDS}s never spends a retry."
        ),
        **_honesty(),
    }


@router.get("/decisions")
def decisions(engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow rests on, and the option each one rejected.

    The specification instructs an implementer who needs a flow the evidence does not contain
    to "derive it and record the derivation, not assume it". This is that record.
    """
    return engine.inferences()


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str, engine: WebhookVerifier = EngineDep) -> Any:
    """One recorded decision, or a 404 naming the ids that do exist."""
    found = engine.read_inference(decision_id)
    if not found:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "detail": f"No recorded decision called {decision_id}.",
                "known": engine.inferences()["decisions"],
                **_honesty(),
            },
        )
    return {**found, **_honesty()}


@router.get("/whoami")
def webhook_contract() -> dict[str, Any]:
    """What the provider needs to configure, in one place.

    The two headers, the part name, the path to aim at, the acknowledgement it checks for and
    the two event types with the warning between them. Someone configuring a callback needs
    these and should not have to read them out of this module's source.
    """
    return {
        "path_template": _source("POST", "/rooms/{room_id}/events"),
        "user_agent": vocab.USER_AGENT,
        "content_type": "multipart/form-data",
        "json_part": vocab.JSON_PART,
        "headers": {vocab.CONTENT_SHA256_HEADER: "base64 digest of the JSON payload"},
        "acknowledgement": {
            "status": vocab.ACKNOWLEDGEMENT_STATUS,
            "body": vocab.ACKNOWLEDGEMENT_BODY,
        },
        "event_types": [
            vocab.SIGNATURE_REQUEST_ALL_SIGNED,
            vocab.SIGNATURE_REQUEST_DOWNLOADABLE,
        ],
        "callback_scopes": list(vocab.CALLBACK_SCOPES),
        "required_scheme": vocab.REQUIRED_SCHEME,
        "tls_enforcement_date": vocab.TLS_ENFORCEMENT_DATE,
        **_honesty(),
    }


@router.get("/error-codes")
def error_codes(engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """The machine-readable error catalogue, keyed by ``error_name``.

    Every entry carries the five fields the specification names: ``http_status``, ``cause``,
    ``remediation``, ``retryable`` and ``backoff``. A consumer writes its retry logic against
    this rather than against prose.
    """
    payload = engine.vocabulary()
    catalogue = payload["error_catalogue"]
    return {
        "codes_name": catalogue["codes_name"],
        "events_name": catalogue["events_name"],
        "fields": catalogue["fields"],
        "count": len(catalogue["codes"]),
        "codes": catalogue["codes"],
        **_honesty(),
    }


# --------------------------------------------------------------------------- #
# Registrations
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/callbacks")
def list_callbacks(room_id: str, engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """Every registration this room holds, never the sealed key."""
    rows = engine.callbacks(room_id)
    return {
        "room_id": room_id,
        "count": len(rows),
        "https_only": sum(1 for row in rows if str(row["callback_url"]).startswith("https://")),
        "callbacks": rows,
        **_honesty(),
    }


@router.post("/rooms/{room_id}/callbacks", status_code=201)
def create_callback(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """Register a callback URL and seal the account API key against it.

    ``callback_url`` must be HTTPS and ``api_key`` must be present. Both refusals come from
    the specification: plain HTTP callbacks "will stop receiving Sign callback events" from
    December 1, 2024, and a registration with no key cannot authenticate anything.
    """
    return engine.register_callback(
        room_id,
        payload,
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/callbacks"),
    )


@router.get("/callbacks/{callback_id}")
def read_callback(callback_id: str, engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """One registration with the range snapshot it verifies against."""
    return engine.read_callback(callback_id)


@router.patch("/callbacks/{callback_id}")
def update_callback(
    callback_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """Change the URL, the scope or the staleness window. Never the sealed key.

    Replacing a sealed secret is a new value rather than a patch, so the route refuses one and
    says which route stores it instead.
    """
    return engine.update_callback(
        callback_id,
        payload,
        actor=actor,
        source=_source("PATCH", "/callbacks/{callback_id}"),
    )


# --------------------------------------------------------------------------- #
# The allowlist
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/ranges")
def list_ranges(room_id: str, engine: WebhookVerifier = EngineDep) -> dict[str, Any]:
    """The stored range snapshot, with the age that decides whether it can be trusted.

    The evidence says to check the list "periodically", so an answer that cannot say how old
    its own allowlist is cannot answer the only question an operator asks during an incident.
    """
    return {**engine.ranges(room_id), **_honesty()}


@router.post("/rooms/{room_id}/ranges", status_code=201)
def refresh_ranges(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """Store a new range snapshot from the published file.

    The fetch runs **here**, on an operator's call, and never on the delivery path. That is
    ``INFERRED_BOUNDED_ALLOWLIST_REFRESH``: a verification handler that reached for the network
    while a signature waited to be checked would put a third party's uptime inside a thirty
    second budget on every delivery.

    ``ranges`` may carry a range list directly, which is how a test and a disconnected
    deployment supply one. With no body the route stores nothing and says so, because this
    build opens no socket on its own: the published file is fetched by a deployment that has a
    client for it.
    """
    document = payload.get("ranges")
    if document is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": "ranges_required",
                "detail": (
                    "This route does not fetch on its own. POST the published file's contents "
                    "as ranges, or point a deployment that has an HTTP client at "
                    f"{vocab.IP_RANGES_URL}."
                ),
                "source_url": vocab.IP_RANGES_URL,
                **_honesty(),
            },
        )

    def fetcher(_url: str) -> Any:
        return document

    return engine.refresh_ranges(
        room_id,
        fetcher=fetcher,
        source_url=str(payload.get("source_url") or vocab.IP_RANGES_URL),
        actor=actor,
        source=_source("POST", "/rooms/{room_id}/ranges"),
    )


# --------------------------------------------------------------------------- #
# The callback itself
# --------------------------------------------------------------------------- #


@router.post("/rooms/{room_id}/events")
async def receive_event(
    room_id: str,
    request: Request,
    callback_id: str | None = Query(default=None),
    callback_url: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: WebhookVerifier = EngineDep,
) -> Any:
    """The provider's POST. Three checks, then the magic string.

    Reads the ``json`` part by name rather than calling ``request.json()``, because the
    specification says the request is ``multipart/form-data`` with the payload in a part named
    ``json``. A handler that parsed the body as JSON would reject every real delivery, and it
    would reject them with a framework error naming neither the part nor the provider.

    The bytes of that part are what the ``Content-Sha256`` check covers, exactly as they
    arrived. A handler that parsed the payload and re-serialised it would compute a different
    digest and refuse every delivery, so the part is read once and the same bytes are handed
    to both the digest and the parse.

    The source address is the socket peer. This build reads no forwarded header: a declared
    address is chosen by whoever sent the request, so trusting one alone would make the
    allowlist check nothing at all. Mounting this route behind a proxy or a public load
    balancer therefore refuses every delivery, which ``GET /vocabulary`` states outright.
    """
    part, part_error = await _json_part(request)
    if part is None:
        catalogue = vocab.error_code(part_error)
        return JSONResponse(
            status_code=catalogue["http_status"],
            content={
                "error": part_error,
                "detail": catalogue["cause"],
                "remediation": catalogue["remediation"],
                "retryable": catalogue["retryable"],
                "part": vocab.JSON_PART,
                "content_type": "multipart/form-data",
                **_honesty(),
            },
        )

    payload, parse_error = _parse_payload(part)
    if parse_error is not None:
        catalogue = vocab.error_code(parse_error)
        return JSONResponse(
            status_code=catalogue["http_status"],
            content={
                "error": parse_error,
                "detail": catalogue["cause"],
                "remediation": catalogue["remediation"],
                "retryable": catalogue["retryable"],
                "part": vocab.JSON_PART,
                **_honesty(),
            },
        )

    report = engine.inspect(
        room_id,
        source_ip=request.client.host if request.client else "",
        content_sha256=request.headers.get(vocab.CONTENT_SHA256_HEADER),
        payload_bytes=part,
        payload=payload,
        callback_id=callback_id,
        callback_url=callback_url,
        actor=actor or "provider",
        source=_source("POST", "/rooms/{room_id}/events"),
    )

    if report["state"] == vocab.REJECTED:
        return JSONResponse(status_code=int(report["http_status"]), content=report)

    # A magic string, not a status code, and the same on a duplicate. The provider reads
    # anything else as a failed callback, and a failed callback walks the ladder.
    return JSONResponse(status_code=vocab.ACKNOWLEDGEMENT_STATUS, content=report)


async def _json_part(request: Request) -> tuple[bytes | None, str | None]:
    """The bytes of the ``json`` part, as they arrived, or the ``error_name`` saying why not.

    Three refusals and they are three different mistakes for whoever has to fix the sender: the
    body was not multipart at all, the body was multipart with no ``json`` part, and the part
    was named but could not be read. Each gets its own ``remediation``.

    A text part arrives from the form parser as a ``str`` and a file part as an ``UploadFile``.
    Both are read here so the digest covers the same bytes the parser produced, rather than a
    second parse of the body that could disagree with the first.
    """
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - any parse failure is one refusal
        return None, "payload_part_missing"

    part = form.get(vocab.JSON_PART)
    try:
        if isinstance(part, str):
            return part.encode("utf-8"), None
        if hasattr(part, "read"):
            return bytes(await part.read()), None
    except Exception:  # noqa: BLE001 - an unreadable part is one refusal, not a crash
        return None, "payload_part_missing"
    return None, "payload_part_missing"


def _parse_payload(part: bytes) -> tuple[dict[str, Any], str | None]:
    """The ``json`` part as an object, or the ``error_name`` explaining why not.

    A part that does not parse and a part that parses to an array are one mistake to the person
    fixing it, and two to a consumer reading the catalogue, so both map to the single name
    ``payload_not_json``.
    """
    try:
        payload = json.loads(part.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, "payload_not_json"
    if not isinstance(payload, dict):
        return {}, "payload_not_json"
    return payload, None


# --------------------------------------------------------------------------- #
# The delivery log
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/deliveries")
def list_deliveries(
    room_id: str,
    state: str | None = Query(default=None, description="verified, rejected or duplicate"),
    limit: int = Query(default=100, ge=1, le=500),
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """Every recorded delivery, with all three checks intact.

    The checks are carried whole rather than collapsed to a boolean, because a delivery log
    that recorded only ``passed`` cannot answer a question a month later: which range matched,
    which digest was expected, and whether the event id had already been seen are three
    different facts and all three outlive the response.
    """
    rows = engine.deliveries(room_id, state=state, limit=limit)
    by_error: dict[str, int] = {}
    for row in rows:
        if row.get("error_name"):
            by_error[row["error_name"]] = by_error.get(row["error_name"], 0) + 1
    return {
        "room_id": room_id,
        "count": len(rows),
        "verified": sum(1 for row in rows if row["state"] == vocab.VERIFIED),
        "rejected": sum(1 for row in rows if row["state"] == vocab.REJECTED),
        "duplicates": sum(1 for row in rows if row["state"] == vocab.DUPLICATE),
        "by_error_name": by_error,
        "deliveries": rows,
        **_honesty(),
    }


@router.get("/rooms/{room_id}/deliveries/{delivery_id}")
def read_delivery(
    room_id: str,
    delivery_id: str,
    engine: WebhookVerifier = EngineDep,
) -> dict[str, Any]:
    """One delivery with its checks. A read, so it writes nothing."""
    for row in engine.deliveries(room_id, limit=500):
        if row["id"] == delivery_id:
            return {**row, **_honesty()}
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": f"No delivery with id {delivery_id} is recorded for this room.",
            **_honesty(),
        },
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The feature's own prefix, duplicated here so the demo data's audit sources name the same
#: routes the router serves. A change to the prefix has to be made deliberately in both
#: places, which is the point of writing it twice.
PREFIX = vocab.ROUTER_PREFIX

#: The demo key. **Not a secret.** It exists so a fresh checkout's demo verifies against a
#: key it can also compute, and the demo says so in the string it returns.
DEMO_API_KEY = vocab.DEMO_API_KEY

#: The published range file's shape, in the form :func:`classify_ranges` reads. The real file
#: is not fetched: ``INFERRED_BOUNDED_ALLOWLIST_REFRESH`` records why this build never reaches
#: for the network while a delivery is waiting.
DEMO_RANGES: list[dict[str, str]] = [
    {"ip": "203.0.113.0/24", "description": "Callback delivery, region one"},
    {"ip": "198.51.100.0/24", "description": "Callback delivery, region two"},
    {"ip": "192.0.2.0/24", "description": "Documentation range, used by the demo"},
]

DEMO_EVENT_TIME = "2026-03-04T18:22:31Z"
DEMO_EVENT_IDS = {
    "signed": "evt_northwind_all_signed_001",
    "downloadable": "evt_northwind_downloadable_002",
}


def _room(store: RecordStore, name: str, account: str) -> str:
    return store.create("room", {"name": name, "account": account}, actor="dana")["id"]


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Three rooms, and the states that are not all successes.

    The rows are produced by running the real :class:`WebhookVerifier`, so the demo cannot
    show a shape this workflow would not produce, and seeding never opens a socket. It is
    deliberately mixed, because a demo of only green teaches a reviewer nothing:

    * **room one** - an allowlist that admits the demo address, and three deliveries through it:
      an ``all_signed`` event that verifies, the provider's **retry of that same event**,
      recorded as a duplicate and acknowledged rather than refused, and a ``downloadable``
      event that verifies;
    * **the same room** - a delivery from an address **outside** the allowlist, refused at the
      first check, and one with a **mangled payload digest**, refused at the second check, so
      both of the first two refusals are visible with their own catalogue entry;
    * **room two** - a registration whose ``event_hash`` does not verify, refused at the third
      check, which is the one refusal the first two rooms do not show;
    * **room three** - a registration with **no range snapshot at all**, so its delivery is
      refused because an empty allowlist admits nothing, which is the failure mode a stale or
      missing refresh produces.

    The seeder hands over ``[(room_id, account), ...]``. It is topped up with rooms this seed
    creates, because the states above are per-room. The return string says how many rooms came
    from the seeder, and every character in it is encodable by cp1252: ASCII only, no arrow,
    no curly quote.
    """
    from dsr.security_governance import webhook_signing as signing

    store = RecordStore(db)
    engine = WebhookVerifier(store, now=lambda: context["now"])
    given: list[tuple[str, str]] = [
        entry if isinstance(entry, (tuple, list)) else (entry, "")
        for entry in (context.get("room_ids") or [])
    ]

    names = [
        ("Northwind Traders - inbound verification", "Northwind Traders"),
        ("Contoso Health - refused at the event digest", "Contoso Health"),
        ("Fabrikam - no allowlist snapshot", "Fabrikam"),
    ]
    room_ids = [room_id for room_id, _ in given if store.get(room_id) is not None][:3]
    created = 0
    for index in range(3):
        if index < len(room_ids):
            continue
        name, account = names[index]
        room_ids.append(_room(store, name, account))
        created += 1
    working_room, digest_room, bare_room = room_ids

    callback_source = f"POST {PREFIX}/rooms/{{room_id}}/callbacks"
    range_source = f"POST {PREFIX}/rooms/{{room_id}}/ranges"
    event_source = f"POST {PREFIX}/rooms/{{room_id}}/events"

    def register(room_ref: str, url: str) -> str:
        return engine.register_callback(
            room_ref,
            {"callback_url": url, "api_key": DEMO_API_KEY, "scope": "account"},
            actor="dana",
            source=callback_source,
        )["id"]

    def refresh(room_ref: str) -> None:
        engine.refresh_ranges(
            room_ref,
            fetcher=lambda _url: DEMO_RANGES,
            actor="dana",
            source=range_source,
        )

    def deliver(
        room_ref: str,
        *,
        source_ip: str,
        event_id: str,
        event_type: str,
        mangled_digest: bool = False,
        wrong_event_hash: bool = False,
    ) -> dict[str, Any]:
        event = {
            "event_time": DEMO_EVENT_TIME,
            "event_type": event_type,
            "event_id": event_id,
            "event_hash": signing.event_hash(DEMO_API_KEY, DEMO_EVENT_TIME, event_type),
            "event_metadata": {
                "related_signature_id": "sr_northwind_001",
                "reported_for_account_id": "acct_northwind",
                "reported_for_app_id": "app_northwind",
            },
        }
        if wrong_event_hash:
            # The digest a sender that guessed the HMAC input produces: the same two fields,
            # joined with a separator. It is valid hex and the wrong value, which is exactly
            # the mistake the specification's `echo -n $event_time$event_type` rules out.
            event["event_hash"] = signing.digest_over(
                DEMO_API_KEY, f"{DEMO_EVENT_TIME}:{event_type}"
            )
        body = json.dumps({"event": event, "signature_request": {"id": "sr_northwind_001"}})
        raw = body.encode("utf-8")
        header = signing.content_sha256(DEMO_API_KEY, raw)
        if mangled_digest:
            # A proxy that re-encoded the body between the provider and this handler. The
            # digest is valid base64 and wrong, which is the third of the three refusals the
            # content check can produce.
            header = signing.content_sha256(DEMO_API_KEY, raw + b" ")
        return engine.inspect(
            room_ref,
            source_ip=source_ip,
            content_sha256=header,
            payload_bytes=raw,
            payload=json.loads(body),
            actor="provider",
            source=event_source,
        )

    parts: list[str] = []

    # -- room one: allowed, verified, retried, and both early refusals ------ #
    working = register(working_room, "https://hooks.northwind.example/wf082")
    refresh(working_room)

    signed = deliver(
        working_room,
        source_ip="203.0.113.24",
        event_id=DEMO_EVENT_IDS["signed"],
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
    )
    retried = deliver(
        working_room,
        source_ip="203.0.113.24",
        event_id=DEMO_EVENT_IDS["signed"],
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
    )
    downloadable = deliver(
        working_room,
        source_ip="198.51.100.7",
        event_id=DEMO_EVENT_IDS["downloadable"],
        event_type=vocab.SIGNATURE_REQUEST_DOWNLOADABLE,
    )
    off_allowlist = deliver(
        working_room,
        source_ip="198.18.0.9",
        event_id="evt_northwind_off_allowlist_003",
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
    )
    mangled = deliver(
        working_room,
        source_ip="203.0.113.24",
        event_id="evt_northwind_mangled_004",
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
        mangled_digest=True,
    )

    # -- room two: refused at the third check ------------------------------- #
    register(digest_room, "https://hooks.contoso.example/wf082")
    refresh(digest_room)
    bad_event_hash = deliver(
        digest_room,
        source_ip="203.0.113.31",
        event_id="evt_contoso_bad_hash_005",
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
        wrong_event_hash=True,
    )

    # -- room three: no snapshot, so nothing can be admitted ----------------- #
    register(bare_room, "https://hooks.fabrikam.example/wf082")
    no_snapshot = deliver(
        bare_room,
        source_ip="203.0.113.88",
        event_id="evt_fabrikam_no_snapshot_006",
        event_type=vocab.SIGNATURE_REQUEST_ALL_SIGNED,
    )

    summary = engine.summary(working_room)
    parts.append(
        f"room one: {summary['deliveries']} delivery recorded, {summary['verified']} verified, "
        f"{summary['duplicates']} duplicate retry acknowledged rather than refused, "
        f"{summary['rejected']} refused"
    )
    parts.append(
        f"the retry of {DEMO_EVENT_IDS['signed']} was recorded as {retried['state']} and "
        f"answered with the magic string, not a refusal"
    )
    parts.append(
        f"an address outside the published ranges was refused at the first check as "
        f"{off_allowlist.get('error_name')}"
    )
    parts.append(
        f"a payload whose digest was re-encoded in transit was refused at the second check as "
        f"{mangled.get('error_name')}"
    )
    parts.append(
        f"room two: a payload whose event_hash does not verify was refused at the third check "
        f"as {bad_event_hash.get('error_name')}"
    )
    parts.append(
        f"room three: with no range snapshot the delivery was refused as "
        f"{no_snapshot.get('error_name')}, because an empty allowlist admits nothing"
    )
    if created:
        parts.append(f"{created} room(s) created for the per-room states")
    parts.append(f"{len(given)} room(s) from the seeder")
    if signed["state"] != vocab.VERIFIED:
        parts.append(f"FIRST DELIVERY NOT VERIFIED: {signed['state']}")
    if retried["state"] != vocab.DUPLICATE:
        parts.append(f"RETRY NOT DEDUPED: {retried['state']}")
    if downloadable["state"] != vocab.VERIFIED:
        parts.append(f"DOWNLOADABLE EVENT NOT VERIFIED: {downloadable['state']}")
    if not summary["rejected"]:
        parts.append("NO REFUSAL RECORDED")
    if bad_event_hash.get("error_name") != "event_hash_mismatch":
        parts.append(f"THIRD CHECK DID NOT REFUSE: {bad_event_hash.get('error_name')}")
    if not engine.read_callback(working).get("sealed"):
        parts.append("API KEY NOT SEALED")
    return "; ".join(parts)
