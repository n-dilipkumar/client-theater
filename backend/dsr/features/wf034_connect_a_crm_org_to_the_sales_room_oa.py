"""WF-034: connect a CRM org to the sales room over OAuth 2.0.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-034.md``, which is
the specification. The researched decisions are the product: the four-interface
connector shape a third party plugs into, the HubSpot authorize URL and its three
parameters, ``Authorization: Bearer token`` on every later call, the credential
vault keyed by the org/account id and sealed at rest, refresh driven by the
``expires_in`` the token carried, health polling the room drives itself, and the
sentence the whole token half is built around - **"`Unauthorized (401)` requests
are not a valid indicator that a new access token must be retrieved."**

This module is the three things the contract requires of a feature and nothing
else: the HTTP surface, the mapping from domain errors to responses, and the demo
data. The domain lives in :mod:`dsr.crm_oauth`.

Why the prefix is ``/api/wf-034``
---------------------------------
A spec does not declare its routes, so a ticket-derived prefix cannot collide
with a feature-shaped one by construction. Every room-scoped path is room-scoped,
and the host's loader would report a ``(method, path)`` clash as a failed feature
rather than shadowing it.

``source=`` comes from the route
--------------------------------
Every write route below passes the route that actually served it, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain method that writes, so it cannot silently
regress. A test asserts that every source recorded in the audit log matches a
route the host actually mounted. The sweep is where this matters most: a
connection-health pass writes a dozen audit rows through one route, and every one
of them names that route rather than a path the app used to serve.

Error mapping
-------------
Eight handlers, one per distinct answer, and all eight types are this feature's
own. ``RecordNotFound`` and ``AuditError`` are deliberately not claimed: the core
app already maps them, and two handlers for one type is a collision the host
refuses. The split between ``400`` (the request asks for something this layer
will not do), ``404`` (no such connection or room), ``428`` (well formed, this
installation is not set up to answer it yet) and ``502`` (the vendor's own
endpoint failed in a way this layer cannot interpret) is deliberate, and
``apiRequest`` in the frontend carries the status so a page can tell them apart.

There is no route in this feature for the credential vault. Not a redacted one, not
a masked one: none. A sealed row is opaque to every reader except the code that
holds the key, and the health of a connection is reported without it. The core
app's generic records API does list the ``crm_credential`` collection, which is
safe because the payload is the sealed string plus the field *names* inside it -
and there is a test asserting that, so the safety is checked rather than claimed.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from dsr.crm_oauth import (
    CrmOAuthConnections,
    describe_inferences,
    describe_statuses,
    describe_vocabulary,
)
from dsr.crm_oauth.connectors import describe_connector, registered_vendors
from dsr.crm_oauth.errors import (
    ConnectionDisabledError,
    ConnectionNotFoundError,
    CrmOAuthError,
    NotConnectedError,
    TokenExchangeError,
    UnknownRoomError,
    VaultSealedError,
    VendorRequestError,
)
from dsr.crm_oauth.transport import HttpResult
from dsr.crm_oauth.vocabulary import VENDOR_IDS
from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-034-connect-a-crm-org-to-the-sales-room-oa",
    "ticket": "WF-034",
    "name": "Connect a CRM org to the sales room (OAuth 2.0 authorization code)",
    "description": (
        "Authorize a Salesforce, HubSpot or Dynamics org, exchange the code for a "
        "bearer token, seal the refresh token in a vault keyed by the org id, "
        "refresh on the stored TTL, and verify the token with a low-cost "
        "authenticated call."
    ),
    "nav": [{"id": "crm-connections", "label": "CRM connections"}],
}

router = APIRouter(prefix="/api/wf-034", tags=["wf034"])


def get_connections(store: RecordStore = StoreDep) -> CrmOAuthConnections:
    """A :class:`CrmOAuthConnections` over the process-wide audited store.

    Per request rather than stored on ``app.state``: the engine holds nothing but
    the store handle, the vault and the transport, and ``app.state`` is the one
    place a feature would have to edit a shared file. Building it here also
    leaves the transport an overridable dependency, so the suite drives the whole
    token lifecycle without a socket.
    """
    return CrmOAuthConnections(store)


ConnectionsDep = Depends(get_connections)


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _crm_oauth_error(request: Request, exc: CrmOAuthError) -> JSONResponse:
    """Well-formed JSON asking for something this layer will not do. 400.

    The base of the hierarchy, and the fallback for the refusals that are the
    caller's to fix: an unknown vendor, a connection missing something the
    authorize URL needs, a callback that matches no pending authorization. Their
    own handlers below exist only where the answer is *different*.
    """
    return JSONResponse(status_code=400, content={"error": "crm_oauth_error", "detail": str(exc)})


def _token_exchange_error(request: Request, exc: TokenExchangeError) -> JSONResponse:
    """The vendor refused a code or a refresh token. 400, with their answer.

    Its own handler because the vendor's own status and body excerpt travel with
    it, and a caller debugging a refused code needs both. Never the request: the
    request body holds the client secret.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": "token_exchange_failed",
            "detail": str(exc),
            "vendor_status": exc.vendor_status,
            "vendor_body": exc.vendor_body,
        },
    )


def _vendor_request_error(request: Request, exc: VendorRequestError) -> JSONResponse:
    """The vendor's own endpoint failed. 502.

    Distinct from 400 because the caller can retry it: nothing about the request
    was wrong, and a sweep that treats an unreachable vendor as a caller error
    would stop checking connections it could still have checked.
    """
    return JSONResponse(
        status_code=502, content={"error": "vendor_unreachable", "detail": str(exc)}
    )


def _not_connected(request: Request, exc: NotConnectedError) -> JSONResponse:
    """Well formed, but this installation is not set up to answer it yet. 428.

    No sealed credential for this org. The distinction from 400 is the point: the
    caller's request was right, and the work is "finish the setup".
    """
    return JSONResponse(status_code=428, content={"error": "not_connected", "detail": str(exc)})


def _connection_disabled(request: Request, exc: ConnectionDisabledError) -> JSONResponse:
    """The connection exists and is switched off. 428, distinct body.

    Starlette picks the most specific registered handler by MRO, so this wins
    over ``_not_connected`` for the same exception and a page can say "turn it
    on" rather than "finish setting it up".
    """
    return JSONResponse(
        status_code=428, content={"error": "connection_disabled", "detail": str(exc)}
    )


def _vault_sealed(request: Request, exc: VaultSealedError) -> JSONResponse:
    """A sealed row this process cannot open. 428.

    Also "not set up to answer it yet", and also the caller's fault in no way:
    the row is intact and the remedy is to re-authorize, which re-seals it under
    the current key. Raising rather than returning an empty credential, because an
    empty one would fail later and further away.
    """
    return JSONResponse(status_code=428, content={"error": "vault_sealed", "detail": str(exc)})


def _connection_not_found(request: Request, exc: ConnectionNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": str(exc), "id": str(exc).split()[-1]},
    )


def _unknown_room(request: Request, exc: UnknownRoomError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": f"room {exc} not found", "id": str(exc)},
    )


EXCEPTION_HANDLERS = {
    # The base of the domain's hierarchy, and the fallback for the refusals that
    # are the caller's to fix: an unknown vendor, a connection missing something
    # the authorize URL needs, a callback that matches no pending authorization.
    # The six below exist only where the answer is *different*.
    CrmOAuthError: _crm_oauth_error,
    TokenExchangeError: _token_exchange_error,
    VendorRequestError: _vendor_request_error,
    NotConnectedError: _not_connected,
    ConnectionDisabledError: _connection_disabled,
    VaultSealedError: _vault_sealed,
    ConnectionNotFoundError: _connection_not_found,
    UnknownRoomError: _unknown_room,
}


# --------------------------------------------------------------------------- #
# The researched contract, served as data
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """The researched contract: the six steps, the quoted evidence, the gaps.

    Served as data so a client renders its pickers from the same source the
    validator enforces against, and so a reviewer can read the researched facts
    without opening a Python file. Includes the state vocabulary and the two
    research gaps, because a missing Dataverse authentication quote is something
    a reader should be told about rather than left to assume.
    """
    payload = describe_vocabulary()
    payload["states"] = describe_statuses()
    return payload


@router.get("/inferences")
def inferences() -> dict[str, Any]:
    """Every design inference this workflow rests on, and how to change each one.

    The research is specific about the authorize URL, the bearer header, the four
    interfaces and the 401 rule, and silent about almost everything around them.
    The parts that are therefore judgement calls are collected in
    :mod:`dsr.crm_oauth.inferences` and served here, next to the sourced facts
    they are measured against.

    A read with no side effect, so it needs no store.
    """
    return describe_inferences()


@router.get("/connectors")
def connectors() -> dict[str, Any]:
    """Every registered connector, its four methods, and what is sourced.

    The researched extensibility claim - "a connector is a plug-in, not a fork" -
    is only worth something if a reader can see the surface a fourth one would
    implement. This is that surface, for the vendors this installation has.
    """
    names = registered_vendors()
    return {
        "count": len(names),
        "vendors": list(VENDOR_IDS),
        "registered": list(names),
        "connectors": {name: describe_connector(name) for name in names},
        "note": (
            "Any object with authorize_url, exchange_code, refresh and execute "
            "can be registered. Nothing else in the package needs to change, which "
            "is the researched claim, and the suite proves it by registering a "
            "fourth vendor and running the whole flow on it."
        ),
    }


@router.get("/summary")
def summary(connections: CrmOAuthConnections = ConnectionsDep) -> dict[str, Any]:
    """Counts across every connection, for the page header.

    Includes the vault key's origin. A connection count that reads as healthy
    while the credentials are sealed with a published key would be the kind of
    quiet wrongness this feature exists to avoid.
    """
    return connections.overview()


# --------------------------------------------------------------------------- #
# Step 1: register the connection
# --------------------------------------------------------------------------- #


@router.get("/connections")
def list_connections(
    room_id: str | None = Query(default=None),
    vendor: str | None = Query(default=None, description=" | ".join(VENDOR_IDS)),
    status: str | None = Query(
        default=None, description="pending_authorization | authorized | expired"
    ),
    tenant: str | None = Query(default=None),
    scope: str = Query(default="all", description="all | room | tenant"),
    limit: int = Query(default=200, ge=1, le=1000),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Every connection this installation has, as summaries.

    ``room_id`` means "the connections that serve this room": the ones attached
    to it plus the tenant-wide ones, because a tenant-scoped credential serves
    every room in the tenant. Each row says which it is.

    A summary never carries a credential. ``has_client_secret`` is a boolean,
    ``credential`` is a sealed-row description, and the expiry is reported from
    inside the sealed payload because a connection that cannot say when its token
    expires cannot tell an operator whether to wait.
    """
    if room_id:
        connections.require_room(room_id)
    rows = connections.list_connections(
        room_id=room_id, vendor=vendor, status=status, tenant=tenant, scope=scope, limit=limit
    )
    return {
        "count": len(rows),
        "room_id": room_id,
        "vault_key_origin": connections.vault.key_origin,
        "vault_key_warning": connections.vault.key_warning(),
        "connections": rows,
    }


@router.post("/connections", status_code=201)
def create_connection(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Register a connection. The researched step 1, recorded.

    The ``client_id`` and the ``client_secret`` come from the vendor's own
    screen - the research names Salesforce's Setup → External Client Apps and
    HubSpot's Developer Platform → app Auth page. The secret is sealed in the
    vault rather than kept in the settings row.

    Nothing is enforced here beyond a known vendor and a real room: a
    half-configured connection is a genuine state, and ``/rooms/<room_id>/readiness``
    reports every field that is missing rather than this route refusing the
    create and leaving the admin with a form and no list.
    """
    return connections.create_connection(
        payload, actor=actor, source=f"POST {router.prefix}/connections"
    )


@router.get("/connections/{connection_id}")
def read_connection(
    connection_id: str, connections: CrmOAuthConnections = ConnectionsDep
) -> dict[str, Any]:
    return connections.connection(connection_id)


@router.patch("/connections/{connection_id}")
def update_connection(
    connection_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Patch a connection, re-sealing the client secret when one is supplied.

    ``{"enabled": false}`` switches the connection off: the sweep skips it, and
    every route that would use it answers 428 rather than spending a token.
    """
    return connections.update_connection(
        connection_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/connections/{connection_id}",
    )


@router.delete("/connections/{connection_id}", status_code=204)
def disconnect_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> Response:
    """Disconnect. 204.

    A soft delete, so the token events and the audit trail outlive it: a
    connection that was authorized and then removed is the row an operator needs
    when a room stops syncing, and the audit log is the guarantee the product is
    built on. The sealed credential and the app secret go with it, so a
    disconnected connection leaves no credential behind.
    """
    connections.disconnect(
        connection_id, actor=actor, source=f"DELETE {router.prefix}/connections/{connection_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Steps 2 to 5: authorize, consent, callback, exchange
# --------------------------------------------------------------------------- #


@router.get("/connections/{connection_id}/authorize-url")
def authorize_url(
    connection_id: str,
    scope: str | None = Query(
        default=None, description="space-separated, overrides the connection's scopes"
    ),
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Step 2: build the vendor's authorization URL and open a pending grant.

    [sourced] "Admin clicks **Authorize**; the app sends the browser to the
    vendor's authorization URL with `client_id`, `scope`, `redirect_uri`."

    A ``GET`` because a browser can follow it, and a ``state`` rides along so the
    callback knows which authorization it is answering. Refused - with the whole
    list of blockers, not just the first - for a connection that cannot produce a
    URL the vendor will accept.
    """
    scopes = [part for part in (scope or "").replace(",", " ").split() if part] or None
    return connections.begin_authorization(
        connection_id,
        scopes=scopes,
        actor=actor,
        source=f"GET {router.prefix}/connections/{connection_id}/authorize-url",
    )


@router.post("/connections/{connection_id}/callback")
def callback(
    connection_id: str,
    code: str = Query(default="", description="the vendor's redirect parameter"),
    state: str = Query(default="", description="the pending authorization this answers"),
    org_hint: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Steps 4 and 5: the redirect, the server-side exchange, the sealed vault.

    [sourced] "Vendor redirects back to the sales room's `redirect_uri` with a
    `code` query parameter." … "Sales room exchanges the code server-side for an
    access token (+ refresh token) and stores the refresh token in the
    integration's credential vault, keyed by the org/account id."

    The response says the credential is sealed and nothing else about it. The
    code is never stored, and the refresh token never leaves the vault.
    """
    if org_hint:
        # Some consent screens hand the org back as a parameter rather than
        # putting it in the token response. It is recorded on the connection so
        # the vault has a key, and never used as one on its own.
        connections.update_connection(
            connection_id,
            {"org_id": org_hint},
            actor=actor,
            source=f"POST {router.prefix}/connections/{connection_id}/callback",
        )
    return connections.exchange_callback(
        connection_id,
        code=code,
        state=state,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/callback",
    )


@router.get("/grants")
def list_grants(
    connection_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="pending | exchanged | failed | cancelled"),
    limit: int = Query(default=100, ge=1, le=1000),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Pending and closed authorizations, newest first.

    A pending one is an admin who is on the vendor's consent screen right now, so
    the page shows how long it is still good for: an authorization expires in ten
    minutes and a code can only be exchanged once.
    """
    rows = connections.list_grants(connection_id=connection_id, state_name=state, limit=limit)
    return {"count": len(rows), "grants": rows}


@router.delete("/grants/{grant_id}", status_code=204)
def cancel_grant(
    grant_id: str,
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> Response:
    """Abandon a pending authorization. 204.

    The researched flow has no cancel step, but a tab the admin closed leaves one
    lying around, and a grant that is still live can be completed long after the
    admin stopped caring.
    """
    connections.cancel_grant(
        grant_id, actor=actor, source=f"DELETE {router.prefix}/grants/{grant_id}"
    )
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# The automation, and step 6
# --------------------------------------------------------------------------- #


@router.post("/connections/{connection_id}/refresh")
def refresh_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Refresh now, through the same path the TTL takes.

    [sourced] "Token refresh before expiry." Exposed because an operator watching
    a connection about to expire should not have to wait for the rule to fire, and
    because it is deliberately the *only* way to force one: there is no flag a 401
    could set.
    """
    return connections.refresh_now(
        connection_id,
        actor=actor,
        source=f"POST {router.prefix}/connections/{connection_id}/refresh",
    )


@router.post("/connections/{connection_id}/test")
def test_connection(
    connection_id: str,
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """Step 6: "Test connection". A low-cost authenticated call, and what it said.

    [sourced] "Admin clicks **Test connection**; the sales room calls a low-cost
    authenticated endpoint to verify the token."

    Refreshes first **if the stored TTL says so**, then makes one bearer call.
    Answers ``200`` even when the vendor rejects the token, with ``ok: false`` and
    ``outcome: "unauthorized"``: the call succeeded, and the answer was no.

    A 401 changes the health, the last-unauthorized timestamp and the token-event
    log. It does not clear the sealed token, move the expiry, set anything the
    refresh path reads, or bring the next health check forward. See
    ``a_401_changes_nothing_but_health`` in :mod:`dsr.crm_oauth.inferences`.
    """
    return connections.test_connection(
        connection_id, actor=actor, source=f"POST {router.prefix}/connections/{connection_id}/test"
    )


@router.get("/connections/{connection_id}/health")
def connection_health(
    connection_id: str, connections: CrmOAuthConnections = ConnectionsDep
) -> dict[str, Any]:
    """One connection's credential state, last probe, and next poll.

    ``status`` is the credential lifecycle and ``health`` is the last probe's
    outcome. They are separate because the researched rule only means something
    if a 401 and an expiry are recorded as different facts.
    """
    entry = connections.connection(connection_id)
    return {
        "connection": entry,
        "status": entry["status"],
        "health": entry["health"],
        "last_checked_at": entry["last_checked_at"],
        "next_check_at": entry["next_check_at"],
        "next_check_in": entry["next_check_in"],
        "health_interval_seconds": entry["health_interval_seconds"],
        "expires_at": entry["expires_at"],
        "expires_in": entry["expires_in"],
        "refresh_due_at": entry["refresh_due_at"],
        "ttl_known": entry["ttl_known"],
        "last_unauthorized_at": entry["last_unauthorized_at"],
        "needs_action": entry["needs_action"],
        "blockers": entry["blockers"],
        "credential": entry["credential"],
    }


@router.get("/connections/{connection_id}/token-events")
def token_events(
    connection_id: str,
    kind: str | None = Query(
        default=None, description="authorize | issued | refreshed | tested | …"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """The token lifecycle for one connection, newest first.

    Every attempt is here rather than in the fields of one call: which trigger
    refreshed a token, what the vendor answered and when, and - the one this
    workflow is measured on - that a 401 changed nothing but the health. A record
    rather than memory, because the reason a second attempt happened is the first
    attempt.
    """
    connections.require_connection(connection_id)
    rows = connections.token_events(connection_id=connection_id, kind=kind, limit=limit)
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[str(row.get("kind"))] = kinds.get(str(row.get("kind")), 0) + 1
    return {"count": len(rows), "summary": kinds, "events": rows}


# --------------------------------------------------------------------------- #
# Room-scoped views
# --------------------------------------------------------------------------- #


@router.get("/rooms/{room_id}/connections")
def room_connections(
    room_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """The connections that serve this room, each marked room or tenant scope."""
    connections.require_room(room_id)
    rows = connections.list_connections(room_id=room_id, limit=limit)
    return {
        "room_id": room_id,
        "count": len(rows),
        "by_scope": {
            "room": sum(1 for row in rows if row["scope"] == "room"),
            "tenant": sum(1 for row in rows if row["scope"] == "tenant"),
        },
        "connections": rows,
    }


@router.get("/rooms/{room_id}/readiness")
def readiness(room_id: str, connections: CrmOAuthConnections = ConnectionsDep) -> dict[str, Any]:
    """What is missing before this room's connections can do anything.

    The researched flow has six steps and each one has a precondition, so a
    connection that cannot finish is a state this reports precisely: the blocker
    code, why it blocks, and the researched grant requirement it cannot check
    itself (HubSpot's installer has to be a Super Admin, and this room cannot
    know the operator's role, so that one is advisory and never a blocker).
    """
    return connections.readiness(room_id)


@router.post("/rooms/{room_id}/health-check")
def health_check(
    room_id: str,
    force: bool = Query(
        default=False, description="check every connection, not only the ones that are due"
    ),
    actor: str | None = Query(default=None),
    connections: CrmOAuthConnections = ConnectionsDep,
) -> dict[str, Any]:
    """The sweep the room's own scheduler calls. Nothing pushes at us.

    [sourced] "Connection-health polling is driven by the sales room's own
    scheduler (not vendor-side)."

    Covers the connections attached to the room *and* the tenant-wide ones. A
    connection that is not due is skipped and says so, which is what makes the
    interval mean something; ``force`` checks them anyway. One connection that
    cannot be probed is reported and does not take the sweep down, because a
    scheduler that stops because one tenant is misconfigured stops polling every
    other tenant too.
    """
    return connections.health_check(
        room_id,
        force=force,
        actor=actor,
        source=f"POST {router.prefix}/rooms/{room_id}/health-check",
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The base URL the demo's ``redirect_uri`` points at. It resolves to nothing,
#: because the demo never opens a socket and never needs a callback to arrive.
DEMO_REDIRECT_BASE = "https://rooms.example/api/wf-034/callback"

#: What the scripted transport answers for each demo org.
#:
#: Keyed by the org slug, which appears in the *token* the transport handed out
#: - the right key, because a vendor's answer about a token should be a function
#: of that token. Nothing is keyed on the connection id, so a reader can see that
#: two connections to the same vendor with different orgs are told different
#: things.
#:
#: The four states are the ones the research makes this workflow responsible for:
#: a healthy bearer, a token whose TTL has passed and whose refresh the vendor
#: refuses, a bearer the vendor answers 401 for, and a vendor endpoint that is
#: down. The first two are the researched happy paths; the second two are the
#: ones a demo of only green would never exercise.
DEMO_ORGS: Mapping[str, Mapping[str, Any]] = {
    "northwind": {
        "vendor": "hubspot",
        "host": "northwind.hubspot.com",
        "expires_in": 1800,
        "probe_status": 200,
        "refresh": "ok",
        "note": "authorized 10 minutes ago, half an hour of TTL left, probe answered 200",
    },
    "fabrikam": {
        "vendor": "hubspot",
        "host": "fabrikam.hubspot.com",
        "expires_in": 1800,
        "probe_status": 200,
        "refresh": "invalid_grant",
        "note": (
            "the TTL passed two hours ago and the vendor refuses the refresh with "
            "invalid_grant: the row that shows what a refused refresh does, which is "
            "not 'try again on a timer'"
        ),
    },
    "taylorswitch": {
        "vendor": "hubspot",
        "host": "taylorswitch.hubspot.com",
        "expires_in": 1800,
        "probe_status": 401,
        "refresh": "ok",
        "note": (
            "the researched case: a valid, unexpired token the vendor answers 401 "
            "for. The health says unauthorized, the status stays authorized, the "
            "expiry does not move, and the next action is the consent screen"
        ),
    },
    "acmecorp": {
        "vendor": "salesforce",
        "host": "acmecorp.my.salesforce.com",
        "expires_in": 3600,
        "probe_status": 200,
        "refresh": "ok",
        "note": "an external client app, the kind Salesforce recommends, with an hour of TTL",
    },
    "contoso": {
        "vendor": "dataverse",
        "host": "contoso.api.crm.dynamics.com",
        "expires_in": 3600,
        "probe_status": 503,
        "refresh": "ok",
        "note": (
            "the Dataverse vendor's endpoint is down: health is error, the sweep "
            "comes back in five minutes rather than an hour, and the token is "
            "untouched"
        ),
    },
}

#: How far back the clock is wound for each seeded credential, so the demo lands
#: in a spread of real states rather than six identical fresh ones.
DEMO_AGE_HOURS: Mapping[str, float] = {
    "northwind": 0.17,
    "fabrikam": 2.0,
    "taylorswitch": 0.33,
    "acmecorp": 0.67,
    "contoso": 0.33,
}


class DemoTransport:
    """A scripted transport, so seeding the demo never opens a socket.

    A real :class:`~dsr.crm_oauth.transport.UrllibTransport` would try to reach
    ``api.hubapi.com`` and ``login.salesforce.com`` from ``backend/seed.py``. This
    one answers from a fixed script keyed by the org slug inside the token, which
    also makes the demo's rows deterministic rather than dependent on what a
    hostname happens to answer today.

    Every refusal it produces is a **real** refusal through the real code path: a
    401 goes through the real probe, an ``invalid_grant`` through the real refresh
    handler. Nothing here writes a status onto a record; the engine has to reach
    that conclusion itself, which is the only way the demo proves the rules.
    """

    def __init__(self, orgs: Mapping[str, Mapping[str, Any]] = DEMO_ORGS) -> None:
        self.orgs = dict(orgs)
        self.calls: list[dict[str, Any]] = []

    # -- helpers ---------------------------------------------------------- #

    @staticmethod
    def _fields(body: bytes | None) -> dict[str, str]:
        from urllib.parse import parse_qs

        if not body:
            return {}
        return {key: value[0] for key, value in parse_qs(body.decode("utf-8")).items()}

    def _org_for(self, blob: str) -> tuple[str, Mapping[str, Any]]:
        """The demo org a token or a code belongs to.

        The slug is embedded in the value the transport itself handed out, so the
        key is the credential and not the connection - which is how a vendor
        behaves, and how two connections to one vendor end up independent.
        """
        for slug in self.orgs:
            if slug in blob:
                return slug, self.orgs[slug]
        return "", {}

    def _token(self, slug: str, index: int, org: Mapping[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "access_token": f"at-{slug}-{index}",
            "expires_in": int(org.get("expires_in") or 1800),
            "token_type": "bearer",
        }
        if slug == "acmecorp":
            payload["instance_url"] = f"https://{org.get('host')}"
        return payload

    # -- interface -------------------------------------------------------- #

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        fields = self._fields(body)
        grant_type = fields.get("grant_type", "")

        if grant_type == "authorization_code":
            slug, org = self._org_for(fields.get("code", ""))
            if not org:
                return HttpResult(ok=False, status=400, body='{"error":"unknown_code"}')
            payload = self._token(slug, 1, org)
            payload["refresh_token"] = f"rt-{slug}-1"
            if slug == "northwind":
                payload["hub_domain"] = org.get("host")
            return HttpResult(ok=True, status=200, body=json.dumps(payload), duration_ms=9.0)

        if grant_type == "refresh_token":
            slug, org = self._org_for(fields.get("refresh_token", ""))
            if not org:
                return HttpResult(ok=False, status=400, body='{"error":"unknown_token"}')
            if org.get("refresh") == "invalid_grant":
                # The researched shape of a dead refresh token: the vendor says
                # which it is, and the engine has to decide what that means.
                return HttpResult(
                    ok=False,
                    status=400,
                    body=json.dumps(
                        {
                            "error": "invalid_grant",
                            "error_description": "refresh token is not valid",
                        }
                    ),
                    duration_ms=11.0,
                )
            payload = self._token(slug, 2, org)
            # Taylor Switch's refresh response returns **no** new refresh token,
            # which is the other half of the rule the engine has to get right: a
            # refresh that omits one keeps the stored one rather than clearing it.
            payload["refresh_token"] = "" if slug == "taylorswitch" else f"rt-{slug}-2"
            if slug == "acmecorp":
                payload["instance_url"] = f"https://{org.get('host')}"
            return HttpResult(ok=True, status=200, body=json.dumps(payload), duration_ms=8.0)

        # A bearer-authenticated read: the researched step 6.
        authorization = str((headers or {}).get("Authorization") or "")
        slug, org = self._org_for(authorization)
        status = int((org or {}).get("probe_status") or 404)
        if status == 200:
            return HttpResult(ok=True, status=200, body='{"results": []}', duration_ms=12.0)
        if status == 401:
            return HttpResult(
                ok=False, status=401, body='{"message": "expired access token"}', duration_ms=7.0
            )
        return HttpResult(
            ok=False, status=status, body='{"error": "upstream unavailable"}', duration_ms=31.0
        )


#: The connections the demo registers, one per org plus one that is deliberately
#: half-configured so readiness has something to say.
#:
#: ``room`` is an index into ``context["room_ids"]``, and ``None`` means the
#: connection is tenant-wide - the researched settings table is a tenant row, and
#: a tenant-wide credential serves every room.
DEMO_CONNECTIONS: tuple[Mapping[str, Any], ...] = (
    {
        "org": "northwind",
        "room": 0,
        "label": "Northwind Traders — HubSpot",
        "tenant": "acme",
        "scopes": ["crm.objects.contacts.read", "offline_access"],
    },
    {
        "org": "fabrikam",
        "room": 1,
        "label": "Fabrikam Logistics — HubSpot",
        "tenant": "acme",
        "scopes": ["crm.objects.contacts.read", "offline_access"],
    },
    {
        "org": "taylorswitch",
        "room": 0,
        "label": "Taylor Switch Co — HubSpot (token rejected)",
        "tenant": "acme",
        "scopes": ["crm.objects.contacts.read", "offline_access"],
    },
    {
        "org": "acmecorp",
        "room": 0,
        "label": "Acme Corp — Salesforce (external client app)",
        "tenant": "acme",
        "policy": "external_client_app",
        "org_id": "acmecorp.my.salesforce.com",
        "scopes": ["api", "refresh_token", "offline_access"],
    },
    {
        "org": "contoso",
        "room": None,
        "label": "Contoso — Dynamics tenant-wide",
        "tenant": "acme",
        "environment_url": "https://contoso.api.crm.dynamics.com",
        "org_id": "contoso",
        "scopes": ["https://contoso.crm.dynamics.com/.default", "offline_access"],
    },
    {
        "org": "",
        "room": 2,
        "label": "Half-configured — no client secret, no scopes",
        "tenant": "contoso",
        "vendor": "salesforce",
        "policy": "connected_app",
        "org_id": "legacy.my.salesforce.com",
        "scopes": [],
        "no_secret": True,
        "note": "readiness has to name the two missing fields, or an admin cannot fix the form",
    },
)


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed six connections across the states this workflow is responsible for.

    Every credential is produced by running the real
    :class:`~dsr.crm_oauth.engine.CrmOAuthConnections` over :class:`DemoTransport`
    - the real code exchange, the real vault, the real TTL arithmetic, the real
    refresh handler and the real probe - so the demo cannot show a state the
    workflow would not actually reach, and seeding never opens a socket.

    The clock is wound back per connection so the six land in a spread of real
    states rather than six identical fresh ones:

    * **authorized and healthy** - Northwind on HubSpot, and Acme on Salesforce
      over an external client app, both probed successfully;
    * **expired, and the vendor refuses the refresh** - Fabrikam. The stored TTL
      has passed, the refresh is answered ``invalid_grant``, and the next action
      becomes the consent screen rather than another attempt on a timer;
    * **unauthorized, with the token untouched** - Taylor Switch, the researched
      case: a valid unexpired token the vendor answers 401 for;
    * **error** - Contoso on Dataverse, whose endpoint is answering 503, so the
      sweep comes back in five minutes;
    * **blocked** - a Salesforce connection with no client secret and no scopes,
      so readiness has something real to report;
    * and one **pending authorization** left in flight, because an admin on the
      consent screen right now is a state a reviewer should see.

    Returns a short description of what was added, which the seeder prints.
    """
    from datetime import datetime, timedelta, timezone

    from dsr.crm_oauth.vault import CredentialVault

    store = RecordStore(db)
    source = "seed"
    actor = "dana"
    real_now = datetime.now(timezone.utc)
    clock: dict[str, datetime] = {"now": real_now}
    transport = DemoTransport()
    engine = CrmOAuthConnections(
        store,
        transport=transport,
        vault=CredentialVault(store),
        now=lambda: clock["now"],
    )

    rooms: list[tuple[str, str]] = [
        (str(room_id), str(account))
        for room_id, account in list(context.get("room_ids") or [])
        if store.get(str(room_id)) is not None
    ]
    if not rooms:
        # No demo rooms to attach to. The seeder prints what was skipped rather
        # than aborting a whole feature over one stale room id, which would leave
        # a page nobody can review.
        return "0 connections (no demo rooms to scope them to)"

    def room_at(index: Any) -> str | None:
        if index is None or index >= len(rooms):
            return None
        return rooms[index][0]

    counts = {"authorized": 0, "refresh_refused": 0, "unauthorized": 0, "error": 0, "blocked": 0}
    pending_grant = 0

    for spec in DEMO_CONNECTIONS:
        slug = str(spec.get("org") or "")
        org = DEMO_ORGS.get(slug, {})
        vendor = str(spec.get("vendor") or org.get("vendor") or "salesforce")
        room_id = room_at(spec.get("room"))
        payload: dict[str, Any] = {
            "vendor": vendor,
            "label": spec.get("label"),
            "tenant": spec.get("tenant", "acme"),
            "room_id": room_id,
            "client_id": f"demo-client-{slug or 'legacy'}",
            "client_secret": None if spec.get("no_secret") else f"demo-secret-{slug or 'legacy'}",
            "redirect_uri": f"{DEMO_REDIRECT_BASE}/{slug or 'legacy'}",
            "scopes": list(spec.get("scopes") or []),
            "org_id": spec.get("org_id") or org.get("host") or "",
            "environment": "production",
            "policy": spec.get("policy")
            or ("external_client_app" if vendor == "salesforce" else ""),
            "environment_url": spec.get("environment_url") or "",
            "notes": spec.get("note") or org.get("note") or "",
        }
        if payload["client_secret"] is None:
            payload.pop("client_secret")
        connection = engine.create_connection(payload, actor=actor, source=source)

        if not org:
            counts["blocked"] += 1
            continue

        # Wind the clock back so the credential lands in the state this org is
        # meant to demonstrate, then exchange it for real.
        clock["now"] = real_now - timedelta(hours=float(DEMO_AGE_HOURS.get(slug, 0)))
        grant = engine.begin_authorization(connection["id"], actor=actor, source=source)
        engine.exchange_callback(
            connection["id"],
            code=f"code-{slug}",
            state=grant["state"],
            actor=actor,
            source=source,
        )
        clock["now"] = real_now
        counts["authorized"] += 1

        # The real probe, so the health column and the token-event log are what
        # this workflow actually produces. An org whose TTL has already passed is
        # met by the real refresh handler on the way to the probe, and its refusal
        # is the state being demonstrated - so the refusal is counted here rather
        # than in a hand-written row afterwards.
        try:
            probe = engine.test_connection(connection["id"], actor=actor, source=source)
        except TokenExchangeError:
            counts["refresh_refused"] += 1
            continue
        except Exception:  # noqa: BLE001 - a demo must not abort a whole seed
            continue
        if probe["outcome"] == "unauthorized":
            counts["unauthorized"] += 1
        elif probe["outcome"] == "error":
            counts["error"] += 1

    # One authorization left in flight, so the page shows an admin who is on the
    # vendor's consent screen right now.
    healthy = engine.list_connections(
        room_id=rooms[0][0], vendor="hubspot", status="authorized", limit=10
    )
    if healthy:
        engine.begin_authorization(healthy[0]["id"], actor=actor, source=source)
        pending_grant = 1

    # Finally the real sweep, so the demo's next-poll times are the engine's own
    # arithmetic and not something written by hand.
    engine.health_check(rooms[0][0], force=True, actor=actor, source=source)

    return (
        f"{len(DEMO_CONNECTIONS)} connections, {counts['authorized']} authorized, "
        f"{counts['unauthorized']} whose token the vendor answered 401 for, "
        f"{counts['error']} with the vendor endpoint down, "
        f"{counts['refresh_refused']} with a refused refresh, "
        f"{counts['blocked']} half-configured, {pending_grant} authorization pending"
    )
