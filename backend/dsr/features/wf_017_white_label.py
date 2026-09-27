"""WF-017: white-label rooms on a custom domain.

Ported from ``feature/WF-017-white-label-rooms-on-a-custom-domain`` onto the
plugin host. The file name follows the contract's ``<ticket>_<slug>.py``
template and the other thirteen feature modules; an earlier cut of this port
called it ``wf_017-white-label.py``, which the host loads perfectly well but
which cannot be reached with an ordinary import statement, because a hyphen is
not an identifier character. See ``backend/tests/test_wf017.py``, which reaches
this module the supported way through the host's own loader.

The researched rules live in :mod:`dsr.domains` (pure: normalise,
validate, build strings, never touch storage) and :mod:`dsr.domain_service` (the
only writer, going through :class:`dsr.store.RecordStore`), both of which the
port took over unchanged. This module is the three things on the branch that were
edits to shared files:

* the route table, which was ``@app.<verb>`` decorators on the single FastAPI
  ``app`` in ``dsr/api.py`` and is now a router this feature owns and the host
  mounts;
* the error mapping, which was two ``@app.exception_handler`` blocks in
  ``dsr/api.py`` and is now an ``EXCEPTION_HANDLERS`` export the host attaches;
* the ``DomainService`` instance, which the branch held on ``app.state`` from a
  ``lifespan`` hook in ``dsr/api.py`` and which is now a dependency declared
  here. See :func:`get_domain_service` for why that costs something and what it
  buys.

Two more things the branch did outside the plugin shape, both replaced here: it
registered the buyer-facing page by editing ``App.jsx``, and it shipped no demo
data at all, which left the feature's page empty in a fresh database. The first
is a shared-file change a feature cannot make, so it is a finding and is written
up in ``frontend/src/features/wf-017-white-label/index.jsx``; the second is a
gap, so see :func:`seed`.

Six deliberate departures from the branch, each forced by the contract:

* **The prefix is ``/api/wf-017-white-label``.** The branch served
  ``/api/white-label/*`` and ``/api/rooms/{room_id}/white-label*``. Both shapes
  are core vocabulary several other workflows want -- ``/api/rooms`` is room
  CRUD -- so a feature that claims them is either a collision or dead code. The
  branch was never merged, so nothing external depended on the old paths.
* **Every write is handed the path this router actually serves.** The branch
  hard-coded ``source=f"claim custom domain {domain} for {room_id}"`` and
  friends inside the service, which is the defect hard rule 4 of the port brief
  calls out: the audit row stops naming a route anyone can call, and the same bug
  has already shipped once in this project. ``source`` is now a required keyword
  on every writing method of :class:`dsr.domain_service.DomainService` and is
  built from ``router.prefix`` by :func:`_source`, so the two cannot drift.
* **The service is a dependency, not ``app.state``.** See above.
* **The ``rooms/{room_id}/white-label`` resource is a *room-scoped* subresource
  of this prefix**, i.e. ``/api/wf-017-white-label/rooms/{room_id}/white-label``.
  It reads as redundant, and the redundancy is the point: it keeps the room id in
  the path the reviewer expects while making the owning feature unambiguous.
* **The ``link_secret`` guard is exported, not wired.** See
  :func:`guard_room_payload`.
* **Demo data lives here.** See :func:`seed`.

What this feature cannot do from a plugin, and is a finding rather than an
omission
--------------------------------------------------------------------
The research states the share-link secret is a "unique, non-removable identifier
... for security purposes", so the generic ``PATCH /api/records/room/{id}`` route
must not be a back door for clearing it. On the branch that guarantee was
implemented by editing ``update_record`` in ``dsr/api.py`` to filter
``domain``, ``link_secret`` and ``collaborator_token`` out of any room patch.
``api.py`` is shared and a feature must not edit it, so on this branch the
guarantee holds for this feature's own routes and for
:meth:`DomainService.strip_reserved` itself, but a caller can still clear the
secret through the generic record surface. :func:`guard_room_payload` is exported
for exactly the one-line change that closes it, and
``test_the_generic_record_route_can_still_clear_the_link_secret`` records the gap
as a strict xfail so that the day someone closes it, the suite says so.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.domain_service import DomainConflict, DomainService, HostNotServed
from dsr.domains import (
    DomainError,
    cname_target,
    default_base_url,
    link_secret_from_path,
    resolver_from_env,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-017-white-label",
    "ticket": "WF-017",
    "name": "White-label rooms on a custom domain",
    "description": (
        "Serve a room from the customer's own domain: point a CNAME at this "
        "deployment, verify it, and every share link switches host while the "
        "non-removable link secret keeps every link already shared working."
    ),
    "nav": [{"id": "white-label", "label": "White-label"}],
}

router = APIRouter(prefix="/api/wf-017-white-label", tags=["WF-017"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Both types are this workflow's own, raised by :mod:`dsr.domain_service` and by
# nothing else in the product. That is what makes it safe to map them here: the
# host refuses two features mapping the same type, and a handler for
# ``ValueError`` -- which ``DomainError`` subclasses -- would intercept
# exceptions from the whole product.


def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
    # 422: the request was well-formed HTTP but the domain or brand token inside
    # it is not one we accept.
    return JSONResponse(status_code=422, content={"error": "invalid_domain", "detail": str(exc)})


def _host_not_served(request: Request, exc: HostNotServed) -> JSONResponse:
    # 404 rather than 421, deliberately: the secret really does exist, and 421
    # would say so to anyone probing. Routing is not identity.
    return JSONResponse(
        status_code=404,
        content={"error": "host_not_served", "detail": f"{exc.host} is not served by this deployment"},
    )


EXCEPTION_HANDLERS = {DomainError: _domain_error, HostNotServed: _host_not_served}


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
#
# The branch added `get_domain_service` to `dsr/api.py` and held the instance on
# `app.state.domain_service`, built once in the `lifespan` hook, with the
# explicit reason that "constructing it reads the deployment's DNS configuration,
# and a new one per request would re-read configuration for every call".
#
# `api.py` is shared, so the seam has to live here. `StoreDep` is the one
# dependency the host already offers a feature, and building the service on top
# of it is a one-line dependency. The cost is the resolver, which is memoised
# below on the exact environment it was built from rather than on "the first
# call", so a deployment that changes `DSR_CNAME_TARGET` or `DSR_CNAME_FIXTURES`
# still gets a fresh resolver and a test that points the fixtures somewhere else
# still sees the change. That keeps the branch's intent and stays isolated.

_RESOLVER_CACHE: dict[tuple[str, str], Any] = {}


def _resolver() -> Any:
    key = (os.environ.get("DSR_CNAME_FIXTURES", ""), os.environ.get("DSR_EDGE_ADDRESSES", ""))
    cached = _RESOLVER_CACHE.get(key)
    if cached is None:
        cached = resolver_from_env()
        _RESOLVER_CACHE[key] = cached
    return cached


def get_domain_service(store: RecordStore = StoreDep) -> DomainService:
    """The store plus a configured resolver, for one request."""
    return DomainService(store, resolver=_resolver())


DomainDep = Depends(get_domain_service)


def _source(verb: str, suffix: str = "") -> str:
    """The audit ``source`` for a write: the path this router actually serves.

    Built from ``router.prefix`` rather than written out, because hard rule 4 of
    the port brief is that a write's audit row must name the route that served
    it, and because a feature that later renames its prefix would otherwise have
    nine stale strings to fix.
    """
    return f"{verb} {router.prefix}{suffix}"


def guard_room_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Strip the product's own room fields out of a caller-supplied patch.

    Returns the payload without ``domain``, ``link_secret`` or
    ``collaborator_token``. Raises nothing.

    This is the "no bypass" guard from the research's non-removable-secret rule,
    exported for the same reason :func:`dsr.features.wf013_rules.guard_rule_payload`
    is: on the branch it was called from ``dsr/api.py``, and the file that needs
    it is one a feature may not edit. Whichever change closes the gap needs this
    function, and it is here for it.
    """
    return DomainService.strip_reserved(payload)


# --------------------------------------------------------------------------- #
# Deployment configuration (read-only)
# --------------------------------------------------------------------------- #


@router.get("/config", summary="What this deployment asks a customer to point at")
def white_label_config() -> dict[str, Any]:
    """The facts the setup screen needs, read from the server.

    The CNAME target is the deployment's own edge hostname. Surfacing it from
    here rather than hard-coding it in the UI is what stops a customer being told
    to point DNS at the vendor's host.
    """
    return {
        "cname_target": cname_target(),
        "base_url": default_base_url(),
        "record_type": "CNAME",
        "propagation_note": (
            "A new CNAME can take up to 24 hours to propagate. You can keep sharing the default-host "
            "links while you wait; they will continue to work."
        ),
        "cloudflare_note": (
            "If this domain is proxied by Cloudflare, set the CNAME to DNS only (proxy off). "
            "A proxied record will not verify and slows page loads."
        ),
        "format_note": "The domain must be in subdomain format, for example proposals.acme.com.",
    }


# --------------------------------------------------------------------------- #
# The room-scoped resource
# --------------------------------------------------------------------------- #


def _describe(service: DomainService, room_id: str) -> dict[str, Any]:
    """A room's public identity, or a 404 naming the room that was not found."""
    try:
        room = service.get_room(room_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return service.describe(room)


@router.get("/rooms/{room_id}/white-label", summary="A room's public identity")
def room_white_label(room_id: str, service: DomainService = DomainDep) -> dict[str, Any]:
    """Domain state, share links and brand tokens for one room."""
    return _describe(service, room_id)


@router.post("/rooms/{room_id}/white-label/link-secret", summary="Mint the share-link secret")
def room_link_secret(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Mint the room's share-link secret, if it has none.

    Idempotent by design: the secret is non-removable, so a second call returns
    the same link rather than invalidating the first one.
    """
    try:
        service.ensure_link_secret(
            room_id,
            actor=actor,
            source=_source("POST", f"/rooms/{room_id}/white-label/link-secret"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return _describe(service, room_id)


# --------------------------------------------------------------------------- #
# Verification, then claiming: the researched order
# --------------------------------------------------------------------------- #


@router.post("/verify", summary="Check a candidate domain, claiming nothing")
def verify_domain(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Check a candidate domain and report every check with what it saw.

    A separate route from claiming, because the researched flow is verify-then-
    save: the operator has to be able to see what is wrong before committing to a
    change. Writes nothing, so it writes no audit row either.
    """
    domain = payload.get("domain")
    if domain is None:
        raise HTTPException(status_code=400, detail="domain is required")
    return service.verify(domain)


@router.post("/rooms/{room_id}/white-label/domain", summary="Attach a custom domain")
def claim_domain(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    force: bool = Query(default=False, description="Save even if the CNAME has not propagated yet"),
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Attach a verified custom domain to a room.

    ``force`` is the researched escape hatch: propagation "may take up to 24
    hours", so an operator mid-wait has to be able to set the binding and poll
    afterwards. It cannot force past an availability clash -- that would hand one
    customer's domain to another.
    """
    domain = payload.get("domain")
    if domain is None:
        raise HTTPException(status_code=400, detail="domain is required")
    try:
        service.claim(
            room_id,
            domain,
            force=force,
            actor=actor,
            source=_source("POST", f"/rooms/{room_id}/white-label/domain"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    except DomainConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _describe(service, room_id)


@router.delete("/rooms/{room_id}/white-label/domain", summary="Detach the custom domain")
def release_domain(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Detach the custom domain. Share links keep working on the default host."""
    try:
        service.release(
            room_id,
            actor=actor,
            source=_source("DELETE", f"/rooms/{room_id}/white-label/domain"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return _describe(service, room_id)


@router.post("/rooms/{room_id}/white-label/recheck", summary="Re-run DNS verification")
def recheck_domain(
    room_id: str,
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Re-check the domain the room already holds.

    The route an operator polls during the researched propagation wait, so they
    never have to retype the domain to find out whether it has landed.
    """
    try:
        service.recheck(
            room_id,
            actor=actor,
            source=_source("POST", f"/rooms/{room_id}/white-label/recheck"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return _describe(service, room_id)


# --------------------------------------------------------------------------- #
# Brand tokens
# --------------------------------------------------------------------------- #


@router.patch("/rooms/{room_id}/white-label/branding", summary="Merge brand tokens")
def update_branding(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Merge brand tokens into the room's open ``branding`` object.

    Colours and font stacks are validated because they are rendered into inline
    styles. Every other key passes through untouched, so a team can add
    ``branding.email_footer`` without coordinating with anyone.
    """
    try:
        service.update_branding(
            room_id,
            payload,
            actor=actor,
            source=_source("PATCH", f"/rooms/{room_id}/white-label/branding"),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room {room_id} not found") from None
    return _describe(service, room_id)


# --------------------------------------------------------------------------- #
# Resolution: the read side of `secret_is_identity`
# --------------------------------------------------------------------------- #


def _resolved(service: DomainService, secret: str, host: str | None) -> dict[str, Any]:
    """Resolve a secret to a room, or 404. Identity is the secret, not the host."""
    try:
        resolved = service.resolve(secret, host=host)
    except KeyError:
        raise HTTPException(status_code=404, detail="no room matches that link secret") from None
    room = resolved["room"]
    return {
        "room_id": room["id"],
        "name": (room.get("data") or {}).get("name"),
        "served_on_custom_domain": resolved["served_on_custom_domain"],
        "white_label": service.describe(room),
    }


@router.get("/links/{secret}", summary="Resolve a share-link secret to its room")
def resolve_link(
    secret: str,
    host: str | None = Query(default=None, description="The Host header the request arrived on"),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """Resolve a share-link secret to the room it points at.

    The same secret resolves on the default host and on every verified custom
    domain, which is what stops a domain change from breaking links a customer
    has already sent. ``host`` is checked only to confirm the request arrived on
    a host this deployment serves, and a host it does not is a 404 rather than a
    421 so the answer does not confirm the secret exists.
    """
    return _resolved(service, secret, host)


@router.get("/resolve", summary="Resolve a full room path to its secret and room")
def resolve_link_path(
    path: str = Query(description="The request path, e.g. /r/Proposal-Name-aB3xY9zK1q"),
    host: str | None = Query(default=None),
    service: DomainService = DomainDep,
) -> dict[str, Any]:
    """The inverse of link construction.

    Reads the trailing secret out of the path so an edge proxy can route a
    white-labelled request without reimplementing the slug rules, which are in
    :mod:`dsr.domains` precisely so there is only one copy of them.
    """
    secret = link_secret_from_path(path)
    if not secret:
        raise HTTPException(status_code=404, detail="path carries no link secret")
    return _resolved(service, secret, host)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# The branch added nothing here, so its feature page was empty in the demo: every
# room had no domain, no secret and no brand tokens, which is exactly the state in
# which a reviewer learns nothing. A feature whose page is empty in the demo is a
# feature nobody can review, so this seeds the states the research describes:
#
#   * a room live on a verified custom domain, with brand tokens and a retired
#     domain in its history, so the whole of the page is populated;
#   * a room with a link secret on the default host, which is the state during
#     the researched "up to 24 hours" propagation window;
#   * a room mid-propagation, with a domain recorded but unverified, so the
#     "Awaiting DNS" state and the re-check control are visible without clicking;
#   * a room with nothing white-labelled at all, so the empty state is honest.
#
# The CNAME target is whatever this deployment is configured for, so a reviewer
# sees the real value. No DNS is consulted: the seeded rooms carry the status
# directly, which is the honest way to present a state that took 24 hours to
# reach.

#: Fixed, obviously-fake link secrets, one per seeded state. They are not minted
#: because a secret is a capability token: a value that is legible in a
#: screenshot and obviously not real is the right thing to put in demo data.
#:
#: Every character is drawn from the production alphabet in :mod:`dsr.domains`
#: and every token is exactly ten characters, because
#: :func:`dsr.domains.link_secret_from_path` is what recovers a room from a
#: share link -- and it returns None for a suffix containing a character the
#: alphabet excludes. That is not a hypothetical: an earlier cut of this seed
#: used tokens ending ``01``, ``02``, ``03`` and ``04``, and every seeded share
#: link 404'd for a buyer because ``0`` and ``1`` are excluded on purpose (they
#: are the glyphs people mistype when re-reading a link from a PDF).
#: ``test_every_seeded_secret_is_recoverable_from_its_own_path`` is the test that
#: caught it.
SEED_SECRETS = ("demoLnkN2d", "demoLnkC3e", "demoLnkF4f", "demoLnkA5g")
#: The collaborator token is *not* constrained the way the buyer secret is: it is
#: never parsed back out of a URL, because this feature has no `/collab/{token}`
#: route. Kept in the same style as the secrets so the two do not read as two
#: different conventions.
SEED_COLLABORATORS = ("demoCollabNw2", "demoCollabCh3", "demoCollabFk4", "demoCollabAw5")

#: The four states, in the order they are applied. ``.example`` is RFC 2606
#: reserved, so a seeded domain can never resolve and can never be a real
#: customer's domain.
SEED_STATES = ("verified", "default_host", "awaiting_dns", "bare")


def _seed_patch(state: str, now: str, room_id: str) -> dict[str, Any]:
    """The room fields for one seeded state.

    Built here rather than written inline so each state is a named thing with a
    comment, which is the difference between demo data a reviewer can read and
    demo data they have to reverse-engineer.
    """
    if state == "verified":
        # Live on its own domain, with brand tokens and a retired predecessor, so
        # every panel of the page has something in it.
        return {
            "link_secret": SEED_SECRETS[0],
            "collaborator_token": SEED_COLLABORATORS[0],
            "domain": "proposals.northwind.example",
            "domain_status": "verified",
            "domain_cname_target": cname_target(),
            "domain_observed": [cname_target()],
            "domain_last_checked_at": now,
            "domain_activated_at": now,
            # The real room id, not a placeholder. `DomainService.claim` records
            # the room it wrote for, and a history entry that says "seed" is a
            # history entry that cannot be traced back to anything.
            "domain_history": [
                {"domain": "pilot.northwind.example", "retired_at": now, "room_id": room_id}
            ],
            "branding": {
                "primary": "#0f172a",
                "accent": "#22c55e",
                "heading_font": "Fira Code, monospace",
                "body_font": "'Fira Sans', sans-serif",
                "logo_url": "https://cdn.northwind.example/logo.svg",
            },
        }

    if state == "default_host":
        # Brand tokens but no domain: the researched state where a customer has
        # set the room up and not yet touched DNS. The link is real and it is on
        # the default host, which is the researched propagation-window behaviour.
        return {
            "link_secret": SEED_SECRETS[1],
            "collaborator_token": SEED_COLLABORATORS[1],
            "branding": {"accent": "#0ea5e9"},
        }

    if state == "awaiting_dns":
        # Mid-propagation: the operator used `force`, so the domain is recorded
        # but has not verified. This is the state the "Awaiting DNS" pill and the
        # Re-check control exist for, and neither is visible without it.
        return {
            "link_secret": SEED_SECRETS[2],
            "collaborator_token": SEED_COLLABORATORS[2],
            "domain": "renewals.fabrikam.example",
            "domain_status": "unverified",
            "domain_cname_target": cname_target(),
            "domain_observed": [],
            "domain_last_checked_at": now,
        }

    # "bare": a secret and nothing else, so the room has a working link, no
    # domain, and the page shows the honest "no custom domain set" state.
    return {"link_secret": SEED_SECRETS[3]}


def seed(db, context: dict[str, Any]) -> str:
    """Put the four researched white-label states into the demo rooms.

    Written through the audited database, so running the seed twice produces a
    second complete set of audit rows rather than overwriting the first.

    The fields are written directly rather than through
    :meth:`DomainService.claim`, and that is deliberate: ``claim`` verifies
    against DNS, and a seed has no DNS to verify against. Each patch is therefore
    exactly the shape ``claim`` and ``recheck`` leave behind, and the last thing
    this function does is read the result back through ``service.describe``, so a
    demo that disagreed with the product fails here rather than in a screenshot.

    The audit ``source`` is ``"seed"``, the same value every other feature's
    ``seed()`` uses, and deliberately *not* a route path. An earlier cut wrote
    ``POST {prefix}/rooms/{id}/white-label/seed-demo``, which reads well and is
    exactly the defect hard rule 4 of the port brief names: the audit log then
    claimed a route the app has never served. A demo write is served by no route
    at all, so it names no route.

    One state per room, and the count reported is the number of rooms actually
    written. Taking ``room_ids[index % len(room_ids)]`` with four states and only
    two demo rooms would silently overwrite the ``verified`` room with
    ``awaiting_dns``, and the seeder would still print four -- which is the worst
    possible combination: a demo that looks complete and is not.
    """
    room_ids: list[tuple[str, str]] = context["room_ids"]
    if not room_ids:
        return "no rooms to attach to"

    now = context["now"].isoformat()
    service = DomainService(RecordStore(db), resolver=_resolver())
    pairs = list(zip(SEED_STATES, room_ids))
    written: list[dict[str, Any]] = []

    for state, (room_id, account) in pairs:
        db.update(
            room_id,
            _seed_patch(state, now, room_id),
            actor=account or "system",
            source="seed",
        )
        written.append(service.describe(service.get_room(room_id)))

    described = (
        f"{sum(1 for s in written if s['domain_status'] == 'verified')} verified, "
        f"{sum(1 for s in written if s['domain_status'] == 'unverified' and s['domain'])} awaiting DNS, "
        f"{sum(1 for s in written if not s['domain'])} on the default host"
    )
    suffix = "" if len(pairs) == len(SEED_STATES) else f", {len(SEED_STATES) - len(pairs)} state(s) skipped"
    return f"{len(pairs)} rooms white-labelled ({described}){suffix}"
