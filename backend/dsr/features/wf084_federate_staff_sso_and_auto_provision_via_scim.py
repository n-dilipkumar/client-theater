"""WF-084: federate staff SSO and auto-provision via SCIM.

A build from a researched specification, not a port. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-084.md``, quoted in full in issue 175.
The rules live in :mod:`dsr.security_governance` under the ``sso_`` prefix and are not
restated here. This module is the three things a feature contributes and the three things it
must never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and this
  feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The one place the callback is strict, and why
----------------------------------------------

:meth:`FederationEngine.complete_sign_in` asserts the profile's organization id and refuses
anything else. That is the security property the specification states twice, once in bold,
and the refused cases are named separately so a caller can act on them: a wrong tenant, a
profile carrying no tenant, a tenant this app has no record of, and an expired code. The
email domain is never consulted, and the response carries the reason in words beside the
decision.

The status codes here are this product's own
--------------------------------------------

The specification documents no HTTP status codes, so every status below follows the product's
conventions rather than inventing a codebook: 400 for a setting this workflow will not
accept, 401 for an authorization code that has expired, 403 for a profile this tenant may
not sign in as, and 404 for a tenant, connection, directory or user that does not exist. The
403 and the 404 are different on purpose: a profile from another tenant is a well-formed
request this app refuses, and a tenant that was never configured is a request this app
cannot interpret.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.security_governance import (
    sso_inferences as inferences,
    sso_rules as rules,
    sso_vocabulary as vocab,
)
from dsr.security_governance.sso_engine import FederationEngine
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-084-federate-staff-sso-and-auto-provision-via-scim",
    "ticket": "WF-084",
    "name": "Federate staff SSO and auto-provision via SCIM",
    "description": (
        "Connect a company's identity provider over SAML or OIDC, then assert the returned "
        "profile's organization id against the expected tenant before any session exists. "
        "Turn on Directory Sync and the directory provider becomes the source of truth for "
        "staff: joiners are provisioned, role changes update the account, leavers are "
        "deprovisioned and lose their sessions, and directory groups map to access rules. "
        "An email domain never decides the tenant."
    ),
    "nav": [{"id": "wf-084-staff-sso", "label": "Staff SSO"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-084", tags=["WF-084"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf084_http.py`` asserts every source this router can
    record matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> FederationEngine:
    """A :class:`FederationEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per
    request: the engine holds nothing beyond the store and a clock, so building it here
    leaves both overridable in a test instead of hanging a long-lived object off
    ``app.state``, which is a shared file this feature may not edit.
    """

    return FederationEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All five types are declared in dsr.security_governance.sso_rules and raised by nothing
# else in the product. That is what makes it safe to map them here: the host refuses a
# second feature registering a handler for the same type, and a handler for ValueError or
# PermissionError would intercept those exceptions across the whole product.


def _settings_invalid(request: Request, exc: rules.IdentitySettingsInvalid) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(
        status_code=400,
        content={
            "error": "identity_settings_invalid",
            "detail": str(exc),
            "errors": exc.errors,
        },
    )


def _organization_not_found(request: Request, exc: rules.OrganizationNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "not_found", "detail": "No such organization."}
    )


def _connection_not_found(request: Request, exc: rules.ConnectionNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "not_found", "detail": "No such connection."}
    )


def _directory_not_found(request: Request, exc: rules.DirectoryNotFound) -> JSONResponse:
    """404 for a directory, and for a webhook post that fails the token check.

    The same type answers both, deliberately. A post with the wrong token is indistinguishable
    from a post naming a directory that does not exist, and telling the two apart would let a
    caller probe for which directory ids are real. ``detail`` says which it was in words; the
    status does not.
    """

    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "detail": str(exc) or "No such directory.",
            vocab.EMAIL_DOMAIN_UNSAFE_FIELD: vocab.EMAIL_DOMAIN_UNSAFE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        },
    )


def _directory_user_not_found(request: Request, exc: rules.DirectoryUserNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such directory user or group."},
    )


def _session_not_found(request: Request, exc: rules.SessionNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"error": "not_found", "detail": "No such session."}
    )


def _assertion_failed(request: Request, exc: rules.TenantAssertionFailed) -> JSONResponse:
    """403 for a profile that may not sign in as the expected tenant, 401 for a dead code.

    Two statuses because two different things are wrong, and a caller cannot act on either
    without being told which:

    * An expired authorization code is 401. The user has to authenticate again, and the code
      the IdP issued is spent.
    * Anything else is 403. The code was fine and the profile may not sign in as this tenant.

    The body always names the reason and always carries the limitation sentence, because a
    refusal is exactly where a reader is most likely to over-read what the assertion
    checked.
    """

    expired = exc.reason == rules.REASON_CODE_EXPIRED
    return JSONResponse(
        status_code=401 if expired else 403,
        content={
            "error": "authorization_code_expired" if expired else "tenant_assertion_failed",
            "detail": str(exc),
            "reason": exc.reason,
            "expected_organization_id": exc.expected,
            "actual_organization_id": exc.actual,
            vocab.ASSERTION_POLICY_FIELD: vocab.ASSERTION_POLICY,
            vocab.EMAIL_DOMAIN_UNSAFE_FIELD: vocab.EMAIL_DOMAIN_UNSAFE,
            vocab.EXPIRED_CODE_POLICY_FIELD: vocab.EXPIRED_CODE_POLICY,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        },
    )


EXCEPTION_HANDLERS = {
    rules.IdentitySettingsInvalid: _settings_invalid,
    rules.OrganizationNotFound: _organization_not_found,
    rules.ConnectionNotFound: _connection_not_found,
    rules.DirectoryNotFound: _directory_not_found,
    rules.DirectoryUserNotFound: _directory_user_not_found,
    rules.SessionNotFound: _session_not_found,
    rules.TenantAssertionFailed: _assertion_failed,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every researched term this workflow validates against.

    Served rather than duplicated in the frontend so the page cannot drift from the rules
    that validate it: the two protocols, the three identifiers and the job each does, both
    flows, the ten-minute code bound, the three SCIM operations, the four supported
    providers and both delivery methods all come from the same tables the validator reads.
    """

    return {
        **vocab.vocabulary_payload(),
        "decisions": {"count": inferences.count(), "ids": list(inferences.DECISIONS)},
    }


@router.get("/decisions")
def list_decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with what it rejected.

    The specification says an implementer "must derive it and record the derivation, not
    assume it". This route is that record, and it is served rather than buried in a docstring
    so a reviewer reads the decision instead of the code.
    """

    return {"count": inferences.count(), "decisions": inferences.describe()}


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id, or a 404."""

    decision = inferences.describe_one(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="No such recorded decision.")
    return decision


# --------------------------------------------------------------------------- #
# Tenants and their connections
# --------------------------------------------------------------------------- #


@router.get("/organizations")
def list_organizations(
    room_id: str | None = Query(None), engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """Every tenant staff may sign in to."""

    rows = engine.organizations(room_id)
    return {
        "count": len(rows),
        "organizations": rows,
        vocab.ASSERTION_POLICY_FIELD: vocab.ASSERTION_POLICY,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/organizations", status_code=201)
def create_organization(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Record a tenant and the redirect URIs its staff sign in through.

    The specification's first user-flow step: "IT admin connects the company's identity
    provider (SAML or OIDC) to the app; the app is configured with a redirect URI in the
    dashboard's Redirects tab." The redirect URIs are recorded here rather than being taken
    from each sign-in, because a redirect URI that the caller chooses is a redirect URI the
    IdP will send an authorization code to.
    """

    return engine.create_organization(
        payload, actor=actor, source=_source("POST", "/organizations")
    )


@router.get("/organizations/{organization_id}")
def read_organization(organization_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """One tenant, its redirect URIs, and how many connections it has."""

    return engine.read_organization(organization_id)


@router.post("/organizations/{organization_id}/connections", status_code=201)
def create_connection(
    organization_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Connect one identity provider to a tenant."""

    return engine.create_connection(
        organization_id,
        payload,
        actor=actor,
        source=_source("POST", "/organizations/{organization_id}/connections"),
    )


@router.get("/connections")
def list_connections(
    organization_id: str | None = Query(None), engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """Every connection, optionally narrowed to one tenant."""

    rows = engine.connections(organization_id)
    return {"count": len(rows), "connections": rows, vocab.LIMITATION_FIELD: vocab.LIMITATION}


@router.get("/connections/{connection_id}")
def read_connection(connection_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """One connection, with its protocol and the redirect URI it was registered with."""

    return engine.read_connection(connection_id)


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #


@router.post("/sso/authorize", status_code=201)
def authorize(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Build the authorization URL for one staff-initiated sign-in.

    The specification's second user-flow step. No session exists when this returns: the
    callback is where the tenant is asserted, and the response says so by carrying no
    session id.

    ``organization``, ``connection`` and ``provider`` are three different instructions and
    none of them substitutes for another. Naming all three is allowed and each is sent under
    its own parameter, because the vendor's evidence draws the line between a connection and
    a provider and this workflow keeps it.
    """

    return engine.begin_sign_in(payload, actor=actor, source=_source("POST", "/sso/authorize"))


@router.post("/sso/callback", status_code=201)
def callback(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """The callback: assert the tenant, then create the session.

    The specification's third user-flow step, in the order the data flow gives it. Both entry
    points reach this route: a staff-initiated sign-in arrives with an ``authorization_id``,
    and an IdP-initiated one arrives with the tenant and the redirect URI the customer's SAML
    settings put in ``RelayState``. The spec puts both in scope, and they are one callback.

    A refusal here writes nothing at all, so the audit log never carries a row for a session
    that was not granted.
    """

    body = dict(payload or {})
    if body.get(vocab.FLOW_KEY) == vocab.FLOW_IDP_INITIATED:
        # Recorded rather than inferred, because the two entry points differ in where the
        # redirect URI comes from and a reader of the session has to know which one happened.
        body.setdefault("relay_state_from", vocab.FLOW_IDP_INITIATED)
    return engine.complete_sign_in(body, actor=actor, source=_source("POST", "/sso/callback"))


@router.get("/sessions")
def list_sessions(
    organization_id: str | None = Query(None),
    include_revoked: bool = Query(True),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Every session this workflow granted, each with whether it still grants access.

    ``live`` is resolved on every read rather than stored, so a deprovision that revokes a
    session is visible on the next read rather than whenever something remembers to
    recompute it.
    """

    rows = engine.sessions(organization_id, include_revoked=include_revoked)
    return {
        "count": len(rows),
        "live_sessions": sum(1 for row in rows if row["live"]),
        "revoked_sessions": sum(1 for row in rows if row["revoked"]),
        "sessions": rows,
        vocab.ASSERT_ORDER_FIELD: vocab.ASSERT_ORDER_VALUE,
        vocab.DEPROVISION_POLICY_FIELD: vocab.DEPROVISION_POLICY,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.get("/sessions/{session_id}")
def read_session(session_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """One session, and whether it is live right now."""

    return engine.read_session(session_id)


# --------------------------------------------------------------------------- #
# Directory Sync
# --------------------------------------------------------------------------- #


@router.get("/directories")
def list_directories(
    organization_id: str | None = Query(None), engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """Every connected directory provider."""

    rows = engine.directories(organization_id)
    return {
        "count": len(rows),
        "directories": rows,
        vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/organizations/{organization_id}/directories", status_code=201)
def create_directory(
    organization_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Turn on Directory Sync and return the webhook token.

    The token is returned here and never again, because a webhook that anybody can post to
    would let a stranger deprovision every user in the tenant. Reading the directory back
    reports which delivery method it uses and how many users it holds, and carries no token.
    """

    return engine.create_directory(
        organization_id,
        payload,
        actor=actor,
        source=_source("POST", "/organizations/{organization_id}/directories"),
    )


@router.get("/directories/{directory_id}")
def read_directory(directory_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """One directory provider and its counts. Never carries the webhook token."""

    return engine.read_directory(directory_id)


@router.get("/directories/{directory_id}/users")
def list_directory_users(
    directory_id: str,
    include_deprovisioned: bool = Query(False),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """The directory's users. Deprovisioned users are soft-deleted and can be asked for."""

    engine.read_directory(directory_id)
    rows = engine.directory_users(directory_id, include_deprovisioned=include_deprovisioned)
    return {
        "directory_id": directory_id,
        "count": len(rows),
        "users": rows,
        vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.get("/directories/{directory_id}/groups")
def list_directory_groups(
    directory_id: str, engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """The directory's groups, each with the active users the directory placed in it."""

    engine.read_directory(directory_id)
    rows = engine.directory_groups(directory_id)
    return {
        "directory_id": directory_id,
        "count": len(rows),
        "groups": rows,
        "group_role": vocab.GROUP_POLICY,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


@router.post("/directories/{directory_id}/events", status_code=201)
def directory_events(
    directory_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    x_wf084_webhook_token: str | None = Header(None, alias="X-WF084-Webhook-Token"),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """The SCIM webhook. Reconciles one Users or Groups change into this app.

    The specification's fourth and fifth user-flow steps in one route, because the three SCIM
    operations it quotes - create, update and delete - are the same reconciliation with three
    payloads. A deprovision is a delete here, and a delete removes access: it soft-deletes
    the user, revokes every session the user holds, and says how many it revoked.

    The token travels in a header. A credential in a body is one an access log captures, and
    this endpoint is the one place a stranger could otherwise post. A header beats a body
    field for the same reason, so a caller that sends both has the header believed. This is
    this build's design and not something the research describes; the reasoning is recorded as
    ``DERIVED_WEBHOOK_AUTHENTICATION``.
    """

    token = x_wf084_webhook_token or str(payload.get("webhook_token") or "").strip()
    return engine.apply_directory_event(
        directory_id,
        payload,
        webhook_token=token,
        actor=actor,
        source=_source("POST", "/directories/{directory_id}/events"),
    )


@router.get("/directories/{directory_id}/event-log")
def read_event_log(directory_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """The Events API's view of the same changes, for a tenant that pulls rather than posts.

    The specification offers both and the choice is recorded as
    ``DERIVED_DELIVERY_METHOD``. This route reconciles the events it reads through the same
    code the webhook uses, so a polling tenant reaches the same records rather than a lesser
    copy of them.

    Idempotent by event id rather than by timestamp alone, so re-reading a window applies
    nothing twice. The skipped count is reported, because a reader that cannot tell a skip
    from a no-op has to assume the worst.
    """

    return engine.poll_events(
        directory_id,
        source=_source("GET", "/directories/{directory_id}/event-log"),
    )


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@router.get("/directories/{directory_id}/access")
def read_access(directory_id: str, engine: FederationEngine = EngineDep) -> dict[str, Any]:
    """What each directory user is granted, as a function of the groups they are in.

    The only route that answers an access question, and it reads groups and nothing else. The
    response names the groups no rule maps, so a tenant can see a user who is in the
    directory and in no rule rather than wondering why they have nothing.
    """

    engine.read_directory(directory_id)
    return engine.effective_access(directory_id)


@router.put("/groups/{group_id}/access", status_code=201)
def set_access(
    group_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: FederationEngine = EngineDep,
) -> dict[str, Any]:
    """Map one directory group onto one app role.

    The specification's automation sentence made writable: directory groups "create groups
    that inform access rules". Mapping a group rather than a person is what makes access a
    function of directory state, and there is deliberately no route that grants a person
    anything by hand.
    """

    body = dict(payload or {})
    return engine.set_access_rule(
        group_id,
        str(body.get("role") or vocab.RULE_ROLE_MEMBER),
        actor=actor,
        source=_source("PUT", "/groups/{group_id}/access"),
    )


@router.get("/access-rules")
def list_access_rules(
    directory_id: str | None = Query(None), engine: FederationEngine = EngineDep
) -> dict[str, Any]:
    """Every group-to-role mapping this workflow holds."""

    rows = engine.access_rules(directory_id)
    return {
        "count": len(rows),
        "rules": rows,
        "roles": list(vocab.RULE_ROLES),
        vocab.OVERRIDE_REFUSAL_FIELD: vocab.OVERRIDE_REFUSAL_VALUE,
        vocab.LIMITATION_FIELD: vocab.LIMITATION,
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the states the specification says matter, not just the happy path.

    Every row is produced by calling the real :class:`FederationEngine`, so the demo cannot
    show a shape, an audit row or an access answer the HTTP routes would not produce.
    ``source="seed"`` rather than a route string: no route served this, and claiming one would
    be the lie hard rule 4 of the brief exists to prevent.

    The states seeded, and why each is here:

    * a tenant with a SAML connection and a registered redirect URI, because the sign-in
      flow starts there;
    * three directory users across **two** groups, one of them in both, because a group that
      maps to a rule and a user in no group are both states a reviewer needs to see;
    * a **group-to-rule mapping**, with the admin group outranking the member group, because
      "create groups that inform access rules" is only visible as an access answer;
    * a **deprovisioned** user whose session was **revoked**, because the specification calls
      deprovisioning "a process of removing a user from an app" and a demo that never removes
      one cannot show a removal.

    The return string is ASCII and is asserted encodable by cp1252 in
    ``tests/test_wf084.py``: the seeder prints it to a Windows console, and one RIGHTWARDS
    ARROW in a recovered feature's return string broke the whole seeder.
    """

    store = RecordStore(db)
    now = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    engine = FederationEngine(store, now=lambda: now)

    organization = engine.create_organization(
        {
            vocab.ORGANIZATION_ID: "org_northwind",
            "name": "Northwind Traders",
            "redirect_uris": ["https://app.example/wf-084/callback"],
            vocab.CLIENT_ID_FIELD: "client_northwind",
            "single_tenant": False,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    engine.create_connection(
        organization[vocab.ORGANIZATION_ID],
        {
            "name": "Northwind Okta",
            "protocol": vocab.PROTOCOL_SAML,
            vocab.REDIRECT_URI_PARAM: "https://app.example/wf-084/callback",
        },
        actor="dana",
        source="seed",
    )

    directory = engine.create_directory(
        organization[vocab.ORGANIZATION_ID],
        {"name": "Northwind Okta directory", vocab.PROVIDER_PARAM: vocab.PROVIDER_OKTA},
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    token = directory["webhook_token"]

    for external_id, given, family, title, groups in (
        ("okta_ada", "Ada", "Vance", "Account executive", ["grp_sales"]),
        ("okta_robin", "Robin", "Hale", "Sales operations", ["grp_sales", "grp_admin"]),
        ("okta_kai", "Kai", "Mensah", "Security analyst", ["grp_security"]),
    ):
        engine.apply_directory_event(
            directory["id"],
            {
                "operation": vocab.SCIM_OP_CREATE,
                "user": {
                    vocab.SCIM_EXTERNAL_ID: external_id,
                    vocab.SCIM_USER_NAME: f"{given.lower()}@northwind.example",
                    vocab.SCIM_GIVEN_NAME: given,
                    vocab.SCIM_FAMILY_NAME: family,
                    vocab.SCIM_TITLE: title,
                    vocab.SCIM_EMAILS: [f"{given.lower()}@northwind.example"],
                    vocab.SCIM_GROUPS: groups,
                    vocab.SCIM_ACTIVE: True,
                },
            },
            webhook_token=token,
            actor="dana",
            source="seed",
        )

    for external_id, name in (
        ("grp_sales", "Sales"),
        ("grp_admin", "Workspace administrators"),
        ("grp_security", "Security review"),
    ):
        engine.apply_directory_event(
            directory["id"],
            {
                "operation": vocab.SCIM_OP_UPDATE,
                "group": {vocab.SCIM_EXTERNAL_ID: external_id, "name": name},
            },
            webhook_token=token,
            actor="dana",
            source="seed",
        )

    groups = {row[vocab.SCIM_EXTERNAL_ID]: row for row in engine.directory_groups(directory["id"])}
    engine.set_access_rule(
        groups["grp_sales"]["id"], vocab.RULE_ROLE_MEMBER, actor="dana", source="seed"
    )
    engine.set_access_rule(
        groups["grp_admin"]["id"], vocab.RULE_ROLE_ADMIN, actor="dana", source="seed"
    )

    # A session for the user who is about to leave, so the deprovision below has a live
    # session to revoke. Revoking a session nobody holds would report zero and would make
    # the headline automation invisible in the demo.
    engine.complete_sign_in(
        {
            vocab.ORGANIZATION_PARAM: organization[vocab.ORGANIZATION_ID],
            "issued_at": rules.stamp(now),
            "profile": {
                vocab.ORGANIZATION_ID: "org_northwind",
                vocab.SCIM_EMAILS: [{"address": "kai@northwind.example"}],
            },
        },
        actor="dana",
        source="seed",
    )

    removed = engine.apply_directory_event(
        directory["id"],
        {
            "operation": vocab.SCIM_OP_DELETE,
            "user": {vocab.SCIM_EXTERNAL_ID: "okta_kai"},
        },
        webhook_token=token,
        actor="dana",
        source="seed",
    )

    board = engine.summary()
    access = engine.effective_access(directory["id"])
    granted = sum(1 for row in access["users"] if row["access"]["granted"])

    return (
        f"{board['organizations']} tenant with {board['connections']} SAML connection; "
        f"{board['directories']} Okta directory by webhook, "
        f"{board['directory_groups']} groups and {board['access_rules']} group-to-role "
        f"rules, granting {granted} of {access['count']} remaining directory users access; "
        f"1 leaver deprovisioned and {removed['sessions_revoked']} of their sessions "
        f"revoked, leaving {board['live_sessions']} of {board['sessions']} session(s) live"
    )
