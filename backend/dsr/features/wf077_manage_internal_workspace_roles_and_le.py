"""WF-077: manage internal workspace roles and least-privilege integration scopes.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-077.md``, which is the
specification. This module is the three things the contract requires of a feature
and nothing else: the HTTP surface, the mapping from domain errors to responses, and
the demo data. The domain lives in :mod:`dsr.workspace_roles`.

What the research fixes, and where each rule is implemented
----------------------------------------------------------
* Four built-in roles plus workspace-defined custom roles, and the caller rule -
  ``Admin``/``Manager`/custom role with ``members.write``. ``rules.require_permission``.
* **The workspace owner's role cannot be changed.** ``rules.decide_role_change``.
* **The last member with admin privileges cannot be changed.** ``rules.decide_role_change``,
  with "admin" read as a permission set rather than a name.
* **A Guest promoted to a non-Collaborator role is auto-upgraded to a Full seat.**
  ``rules.decide_role_change``, visible as ``seat_upgraded`` in the response.
* **No implicit hierarchy.** ``scopes.token_has_scope`` is set membership.
* **403 on a scope miss**, with the vendor's own sentence. ``scopes.Forbidden``.
* **Wildcards refused at the token endpoint.** ``scopes.normalise_scope``.
* **Coarse grants** ``apis.read`` / ``apis.all``, which are named and not wildcards.
* **Plan entitlement independent of scopes**, refused on create/update and never on
  use, so a downgrade does not break a legitimate grant.
* **429 with ``X-RateLimit-Reset``** on a spent per-minute budget.
* **Scopes do not override a user's permissions** - the Seismic rule, enforced by
  checking the member *and* the token rather than either.
* **The audit read is role-gated** to administrators, per the vendor's 21 CFR
  Part 11 delegation note.

``source=`` comes from the route
--------------------------------
Every write below passes the path this router actually serves, built from
``router.prefix`` so it cannot drift when the prefix changes, and ``source`` is a
*required* keyword on every domain write, so it cannot silently regress. A test
asserts every recorded source names a route the host actually mounted - the defect
the contract calls out by name, where a feature's audit log keeps naming a path the
app stopped serving.

Who the caller is
-----------------
Two ways in, and the research describes both halves of the workflow with two
different subjects:

* **A person.** ``X-Workspace-Member: <member_id>``. The role rules apply.
* **A machine.** ``Authorization: Bearer <token>``. The scope rules apply.

Both together is the interesting case and it is checked with *both*: Seismic's rule
is that a scope does not override the user's own permissions, so a request carrying
a token and a member is refused if either is insufficient. Neither credential can
rescue the other. Header names are this build's choice - the research describes
per-endpoint ``security: [{ bearerAuth: [<scope>] }]`` declarations, which is the
mechanism, not the header spelling.

Error mapping
-------------
One handler per distinct answer. ``RecordNotFound`` and ``AuditError`` are
deliberately not claimed: the core app already maps them, and two handlers for one
type is a collision the host refuses.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from dsr.deps import StoreDep
from dsr.store import RecordStore
from dsr.workspace_roles import (
    ENDPOINTS,
    MEMBERSHIPS,
    AccessContext,
    ConsentError,
    Forbidden,
    MembershipNotFound,
    PlanFeatureError,
    RateLimited,
    RoleError,
    RoleForbidden,
    ScopeError,
    SsoError,
    TokenError,
    WorkspaceAccess,
    describe_inferences,
    outcome_table,
    published_oauth,
    published_sso,
    published_vocabulary,
    scopes as scope_rules,
    sso as sso_rules,
    vocabulary as vocab,
)

FEATURE = {
    "id": "wf-077-manage-internal-workspace-roles-and-le",
    "ticket": "WF-077",
    "name": "Manage internal workspace roles and least-privilege integration scopes",
    "description": (
        "Change an internal workspace member's role under the researched guard rails - the "
        "owner's role is fixed, the last admin's role is fixed, and a Guest promoted to a "
        "non-Collaborator role is auto-upgraded to a Full seat - and mint least-privilege API "
        "tokens chosen a la carte. Scopes have no implicit hierarchy, so documents.write does "
        "not imply documents.read and a write-only token is genuinely write-only. Wildcards are "
        "refused, a scope miss is a 403, plan entitlement is enforced on create and update "
        "rather than on use, and directory SSO decides whether a member may sign in locally at "
        "all."
    ),
    "nav": [{"id": "wf-077-manage-internal-workspace-roles-and-le", "label": "Roles and scopes"}],
}

router = APIRouter(prefix="/api/wf-077", tags=["wf077"])

#: The header naming the calling member. This build's choice; see the module
#: docstring.
MEMBER_HEADER = "X-Workspace-Member"


def get_access(store: RecordStore = StoreDep) -> WorkspaceAccess:
    """A :class:`WorkspaceAccess` over the process-wide audited store.

    Per request rather than cached on ``app.state``, because it holds the rate
    limiter and the limiter's state is per-process; building it here also leaves
    the engine a seam a test can replace with one carrying a tiny budget.
    """
    return WorkspaceAccess(store)


AccessDep = Depends(get_access)


# --------------------------------------------------------------------------- #
# Audit sources
# --------------------------------------------------------------------------- #


def _source(suffix: str) -> str:
    """The audit source for a write, built from the route this router serves.

    Never a literal. The branch history is full of features whose audit log named
    a URL the app had stopped serving, and that is exactly the defect this helper
    makes structurally impossible: the prefix is read off the router.
    """
    return f"{router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


def _envelope(status: int, code: str, detail: str, **extra: Any) -> dict[str, Any]:
    """The error body. ``error`` and ``code`` carry the same value.

    The shared frontend client reads ``body.error``; the research's stable-code
    promise names ``code``. One value under two keys means neither reader has to
    know about the other, and a client may switch on either.
    """
    return {"error": code, "code": code, "status": status, "detail": detail, **extra}


def _workspace_role_error(request: Request, exc: RoleError) -> JSONResponse:
    """A role change that breaks a researched rule. 422."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _role_forbidden(request: Request, exc: RoleForbidden) -> JSONResponse:
    """The actor's role does not permit this. 403."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _parse_instant(value: Any) -> datetime | None:
    """Read one ISO-8601 UTC instant, or return ``None`` if it is not one.

    ``None`` rather than an exception, because every caller has a correct answer
    for an unreadable value and none of them is "give up". The alternative -
    letting ``fromisoformat`` raise - turns a cosmetic problem in a throttling
    signal into a 500 on the request that was already being refused, which
    replaces a useful answer with a useless one.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _scope_error(request: Request, exc: ScopeError) -> JSONResponse:
    """A wildcard or an unknown scope. 422."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _token_error(request: Request, exc: TokenError) -> JSONResponse:
    """Absent, unknown, expired or revoked token. 401."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
    """A spent per-minute budget. 429, with the reset signal the research names.

    ``X-RateLimit-Reset`` is an HTTP-date-shaped value in the vendor's world; here
    it is ISO-8601 UTC, which is unambiguous and is what the rest of this API emits
    for instants. Both are sent: the header the research names, and ``Retry-After``
    in whole seconds for a client that only speaks that.

    The reset instant is normalised before it is published rather than trusted. A
    limiter hands over whatever it was constructed with, and this handler is the
    only place standing between that value and an HTTP header, so a value that is
    absent, unparseable, or not a string at all would otherwise either drop the one
    header the research names or raise past the handler and turn a 429 into a 500.
    A rate limiter that fails under load has removed the limit rather than enforced
    it, so the fallback is the next whole minute - the same window the limiter
    itself uses - and the client is told to come back then.
    """
    reset = _parse_instant(exc.reset_at)
    if reset is None:
        reset = _parse_instant(scope_rules.window_opens(time.time()))
    if reset is None:  # pragma: no cover - only if the clock itself is unusable
        reset = datetime.now(timezone.utc) + timedelta(seconds=scope_rules.WINDOW_SECONDS)

    headers = {
        "X-RateLimit-Reset": reset.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "X-RateLimit-Limit": str(exc.context.get("limit", "")),
        "Retry-After": str(max(0, int((reset - datetime.now(timezone.utc)).total_seconds()))),
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
        headers=headers,
    )


def _plan_feature_error(request: Request, exc: PlanFeatureError) -> JSONResponse:
    """Not entitled, on create or update. 403 ``forbidden_plan_feature``."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _membership_not_found(request: Request, exc: MembershipNotFound) -> JSONResponse:
    """No such member in this workspace. 404."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _consent_error(request: Request, exc: ConsentError) -> JSONResponse:
    """An authorization code is unknown, spent, expired, or another client's. 400."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _sso_error(request: Request, exc: SsoError) -> JSONResponse:
    """An SSO connection is missing or malformed. 422."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.status_code, exc.code, str(exc), **exc.context),
    )


def _forbidden(request: Request, exc: Forbidden) -> JSONResponse:
    """The researched 403, word for word.

    "The token is valid, but doesn't have the scope the endpoint requires *or* isn't
    authorized to act on the team you're addressing." The vendor cannot tell those
    two apart, so the message does not either; which one this build found is
    reported additively in the body.
    """
    return JSONResponse(status_code=403, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    RoleError: _workspace_role_error,
    RoleForbidden: _role_forbidden,
    ScopeError: _scope_error,
    TokenError: _token_error,
    RateLimited: _rate_limited,
    PlanFeatureError: _plan_feature_error,
    MembershipNotFound: _membership_not_found,
    ConsentError: _consent_error,
    SsoError: _sso_error,
    Forbidden: _forbidden,
}


# --------------------------------------------------------------------------- #
# Request bodies
#
# `extra="forbid"` on every model is the researched half of "machine-readable
# request schemas with `additionalProperties: false` on every *Request component".
# The vendor publishes that so "generated SDKs should already reject unknown fields
# client-side"; refusing here is what makes the guarantee true for a client that is
# not a generated SDK. It stops at the request boundary on purpose: records.data
# stays free-form, so a team can still add whatever its own rows need.
# --------------------------------------------------------------------------- #


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleChangeBody(StrictModel):
    role: str = Field(min_length=1, description="A built-in role name, or a custom role's name.")
    reason: str = ""


class SeatChangeBody(StrictModel):
    seat: str = Field(description="full or guest.")
    reason: str = ""


class CustomRoleBody(StrictModel):
    name: str = Field(min_length=1)
    permissions: list[str] = Field(min_length=1)
    description: str = ""


class TokenBody(StrictModel):
    name: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    expires_at: str = ""


class AuthorizeBody(StrictModel):
    client_id: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    client_name: str = ""
    redirect_uri: str = ""


class AccessTokenBody(StrictModel):
    code: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    scopes: list[str] | None = None


class SsoBody(BaseModel):
    """Deliberately *not* ``extra="forbid"``.

    SAML and OIDC metadata differ between every corporate IdP, and this workflow's
    record is schema-flexible JSON for exactly that reason. Refusing an unknown
    field here would mean a team cannot wire their IdP without changing this
    feature, which is the outcome the contract forbids. The three fields the
    decision needs are validated by :func:`dsr.workspace_roles.sso
    .validate_connection`; everything else is stored as given.
    """

    model_config = ConfigDict(extra="allow")

    protocol: str = ""
    idp_entity_id: str = ""
    issuer: str = ""
    sso_url: str = ""
    domains: list[str] | str = Field(default_factory=list)
    enabled: bool = True


# --------------------------------------------------------------------------- #
# Shared plumbing
# --------------------------------------------------------------------------- #


def _context(
    access: WorkspaceAccess,
    workspace_id: str,
    *,
    authorization: str,
    member: str,
    response: Response | None,
) -> AccessContext:
    """Resolve the caller and publish the throttling headers on the way out.

    The reset signal is sent on **successful** responses too. A client that can
    only learn its budget by being refused will spend the whole budget learning it,
    and the research's point about a reset signal is that a well-behaved client can
    pace itself without ever reaching the 429.
    """
    bearer = ""
    if authorization:
        scheme, _, value = str(authorization).partition(" ")
        if scheme.lower() != "bearer":
            raise TokenError("the Authorization header must use the Bearer scheme")
        bearer = value.strip()

    ctx = access.context(workspace_id, bearer=bearer, member_id=member or "")
    if ctx.reset_at and response is not None:
        # Normalised rather than passed through: this is an HTTP header, and the
        # value comes from the limiter, so it is read here with the same tolerance
        # the 429 path uses. A header is a worse failure than a missing one - it
        # fails the response itself - so the fallback is the next whole minute.
        reset = _parse_instant(ctx.reset_at) or _parse_instant(
            scope_rules.window_opens(time.time())
        )
        if reset is not None:
            response.headers.update(
                scope_rules.rate_limit_headers(
                    reset.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    limit=access.limiter.limit,
                    now=time.time(),
                    remaining=ctx.rate_limit_remaining,
                )
            )
    return ctx


def _caller(ctx: AccessContext) -> str:
    """Who to name in an audit row's ``actor``.

    A token's id rather than its name: the name is a human label a workspace can
    change, and an audit trail that names a mutable label cannot be trusted later.
    """
    if ctx.token is not None:
        return f"token:{ctx.token.id}"
    if ctx.member is not None:
        return str(ctx.member.get("email") or ctx.member.get("id") or "member")
    return "anonymous"


# --------------------------------------------------------------------------- #
# The catalogue: vocabulary, rules, scopes, endpoints, inferences
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="What this workflow governs")
def summary(access: WorkspaceAccess = AccessDep) -> dict[str, Any]:
    workspaces = access.workspaces()
    return {
        "workspaces": len(workspaces),
        "workspace_ids": workspaces,
        "built_in_roles": list(vocab.BUILT_IN_ROLES),
        "scopes": len(scope_rules.KNOWN_SCOPES),
        "coarse_scopes": list(vocab.COARSE_SCOPES),
        "endpoints": len(access.endpoints()),
        "guard_rails": [entry["outcome"] for entry in outcome_table()],
        "no_implicit_hierarchy": vocab.NO_HIERARCHY_QUOTE,
    }


@router.get("/vocabulary", summary="The researched terms and the quotes behind them")
def vocabulary() -> dict[str, Any]:
    return published_vocabulary()


@router.get("/rules", summary="The role-change refusal table")
def rules_table() -> dict[str, Any]:
    return {
        "outcomes": outcome_table(),
        "caller_rule": vocab.CALLER_QUOTE,
        "built_in_roles": list(vocab.BUILT_IN_ROLES),
    }


@router.get("/endpoints", summary="Every endpoint and the scopes it requires")
def endpoints() -> dict[str, Any]:
    """The per-endpoint declaration the research calls portable.

    "the pattern is directly portable: a per-endpoint scope declaration plus a
    closed error catalogue". So the declaration is served, not merely applied: an
    integrator can read exactly what each scope unlocks before minting a token.

    ``unlocked_by`` is computed through
    :func:`dsr.workspace_roles.scopes.scopes_that_unlock`, which is the same
    membership test the request path uses - so the answer here cannot be more
    generous than what will actually be allowed.
    """
    declared = [dict(entry) for entry in ENDPOINTS]
    return {
        "endpoints": [
            {**entry, "unlocked_by": scope_rules.scopes_that_unlock(entry)} for entry in declared
        ],
        "scopes": [dict(entry) for entry in vocab.SCOPES],
        "coarse": list(vocab.COARSE_SCOPES),
        "coarse_coverage": {name: list(items) for name, items in vocab.COARSE_COVERAGE.items()},
    }


@router.get("/scopes", summary="The scope catalogue, and what a scope set would unlock")
def scopes(
    scopes: str = Query(default="", description="Comma-separated scope set to preview."),
) -> dict[str, Any]:
    """The token-creation surface, which the research says shows what each unlocks.

    Passing ``?scopes=links.write`` returns the endpoints that scope set reaches.
    The answer is derived from the same declarations the request path enforces, so
    the list shown here is the list that will actually apply - and a wildcard or an
    unknown scope comes back as a warning rather than as a token, because
    "the token endpoint will reject unknown scopes".
    """
    requested = [part.strip() for part in str(scopes or "").split(",") if part.strip()]
    preview: dict[str, Any] = {"requested": [], "unlocks": [], "warning": ""}
    if requested:
        try:
            canonical = scope_rules.normalise_scopes(requested)
            preview = {
                "requested": list(canonical),
                "unlocks": scope_rules.unlocked_endpoints(canonical, [dict(e) for e in ENDPOINTS]),
                "surfaces": scope_rules.granted_surfaces(canonical),
                "coverage": sorted(scope_rules.coverage_for(canonical)),
                "warning": "",
            }
        except ScopeError as exc:
            preview = {
                "requested": requested,
                "unlocks": [],
                "surfaces": {},
                "coverage": [],
                "warning": str(exc),
            }
    return {
        "catalogue": [
            {**dict(entry), "surfaces": list(vocab.SCOPE_TARGETS.get(str(entry["scope"]), ()))}
            for entry in vocab.SCOPES
        ],
        "known": sorted(scope_rules.KNOWN_SCOPES),
        "coarse": list(vocab.COARSE_SCOPES),
        "no_wildcards": vocab.WILDCARD_QUOTE,
        "no_implicit_hierarchy": vocab.NO_HIERARCHY_QUOTE,
        "unlock_note": vocab.SCOPES_UNLOCK_QUOTE,
        "preview": preview,
    }


@router.get("/inferences", summary="What this build inferred, and why")
def inferences() -> dict[str, Any]:
    return describe_inferences()


@router.get("/oauth-flow", summary="The consent screen and token endpoint this models")
def oauth_flow() -> dict[str, Any]:
    return published_oauth()


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


@router.get("/workspaces", summary="Every workspace that has members")
def list_workspaces(access: WorkspaceAccess = AccessDep) -> dict[str, Any]:
    rows = []
    for workspace_id in access.workspaces():
        summary_row = access.summary(workspace_id)
        rows.append(
            {
                "workspace_id": workspace_id,
                "plan": summary_row["plan"],
                "members": summary_row["members"],
                "admins": summary_row["admins"],
                "tokens": summary_row["tokens"],
                "sso_configured": summary_row["sso"]["configured"],
            }
        )
    return {"workspaces": rows, "count": len(rows)}


@router.get("/workspaces/{workspace_id}", summary="One workspace at a glance")
def workspace_summary(workspace_id: str, access: WorkspaceAccess = AccessDep) -> dict[str, Any]:
    return access.summary(workspace_id)


# --------------------------------------------------------------------------- #
# Members and roles
# --------------------------------------------------------------------------- #


@router.get("/workspaces/{workspace_id}/members", summary="The workspace member list")
def list_members(
    workspace_id: str,
    response: Response,
    role: str = Query(default=""),
    include_inactive: bool = Query(default=True),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(
        access, workspace_id, authorization=authorization, member=member, response=response
    )
    access.authorise(ctx, ["members.read"])
    rows = access.list_members(workspace_id, role=role, include_inactive=include_inactive)
    return {
        "workspace_id": workspace_id,
        "members": rows,
        "count": len(rows),
        "built_in_roles": list(vocab.BUILT_IN_ROLES),
        "custom_roles": [row["name"] for row in access.list_custom_roles(workspace_id)],
        "seats": list(vocab.SEATS),
        "caller": ctx.to_dict(),
    }


@router.post(
    "/workspaces/{workspace_id}/members/{member_id}/preview-role",
    summary="What a role change would do. Writes nothing.",
)
def preview_role_change(
    workspace_id: str,
    member_id: str,
    body: RoleChangeBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    """The dry run.

    Same pure function the write path uses, so a preview that reports a refusal is
    a refusal that will happen, and a preview that reports an auto-upgraded seat is
    the seat that will be written.
    """
    decision = access.preview_role_change(workspace_id, member_id, body.role)
    return decision.to_dict()


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}/role",
    summary="Change a member's role",
)
def change_role(
    workspace_id: str,
    member_id: str,
    response: Response,
    body: RoleChangeBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(
        access, workspace_id, authorization=authorization, member=member, response=response
    )
    access.authorise(ctx, ["members.write"])
    result = access.change_role(
        workspace_id,
        member_id,
        body.role,
        source=_source(f"/workspaces/{workspace_id}/members/{member_id}/role"),
        actor=_caller(ctx),
        reason=body.reason,
    )
    return {**result, "seat_rule": vocab.SEAT_UPGRADE_QUOTE}


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}/seat",
    summary="Change a member's seat",
)
def change_seat(
    workspace_id: str,
    member_id: str,
    body: SeatChangeBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["members.write"])
    return access.change_seat(
        workspace_id,
        member_id,
        body.seat,
        source=_source(f"/workspaces/{workspace_id}/members/{member_id}/seat"),
        actor=_caller(ctx),
    )


@router.get(
    "/workspaces/{workspace_id}/members/{member_id}/local-login",
    summary="May this member sign in with a local password?",
)
def local_login(
    workspace_id: str,
    member_id: str,
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    """The observable consequence of wiring directory SSO.

    "so staff authenticate through the company IdP rather than local credentials"
    has to be *checkable*, or wiring SSO is only storage. This route is where that
    sentence becomes a fact a test can assert on.
    """
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["members.read"])
    return access.local_login(workspace_id, member_id)


@router.get("/workspaces/{workspace_id}/role-definitions", summary="Workspace-defined custom roles")
def list_role_definitions(
    workspace_id: str,
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["members.read"])
    rows = access.list_custom_roles(workspace_id)
    return {
        "workspace_id": workspace_id,
        "roles": rows,
        "count": len(rows),
        "permissions": list(vocab.PERMISSIONS),
        "built_in": list(vocab.BUILT_IN_ROLES),
    }


@router.post("/workspaces/{workspace_id}/role-definitions", summary="Define a custom role")
def create_role_definition(
    workspace_id: str,
    body: CustomRoleBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["members.write"])
    role = access.create_custom_role(
        workspace_id,
        body.name,
        body.permissions,
        source=_source(f"/workspaces/{workspace_id}/role-definitions"),
        actor=_caller(ctx),
        description=body.description,
    )
    return {"role": role, "quote": vocab.BUILT_IN_ROLE_QUOTE}


# --------------------------------------------------------------------------- #
# Integration tokens
# --------------------------------------------------------------------------- #


@router.get("/workspaces/{workspace_id}/tokens", summary="List API tokens")
def list_tokens(
    workspace_id: str,
    response: Response,
    include_revoked: bool = Query(default=False),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(
        access, workspace_id, authorization=authorization, member=member, response=response
    )
    access.authorise(ctx, ["tokens.read"])
    rows = access.list_tokens(workspace_id, include_revoked=include_revoked)
    return {
        "workspace_id": workspace_id,
        "tokens": rows,
        "count": len(rows),
        "no_implicit_hierarchy": vocab.NO_HIERARCHY_QUOTE,
    }


@router.post("/workspaces/{workspace_id}/tokens", summary="Mint a least-privilege token")
def mint_token(
    workspace_id: str,
    body: TokenBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["tokens.write"])
    minted = access.mint_token(
        workspace_id,
        name=body.name,
        scopes=body.scopes,
        source=_source(f"/workspaces/{workspace_id}/tokens"),
        actor=_caller(ctx),
        minted_role=str((ctx.member or {}).get("role") or ""),
        expires_at=body.expires_at,
    )
    return {
        "token": minted,
        "secret": minted["secret"],
        "shown_once": True,
        "scopes_chosen": list(minted["scopes"]),
        "unlocks": minted["unlocks"],
        "independent_scopes": vocab.NO_HIERARCHY_QUOTE,
        "unlocks_note": vocab.SCOPES_UNLOCK_QUOTE,
    }


@router.delete("/workspaces/{workspace_id}/tokens/{token_id}", summary="Revoke an API token")
def revoke_token(
    workspace_id: str,
    token_id: str,
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["tokens.write"])
    return {
        "token": access.revoke_token(
            workspace_id,
            token_id,
            source=_source(f"/workspaces/{workspace_id}/tokens/{token_id}"),
            actor=_caller(ctx),
        )
    }


# --------------------------------------------------------------------------- #
# OAuth: consent screen and code exchange
# --------------------------------------------------------------------------- #


@router.post(
    "/workspaces/{workspace_id}/oauth/authorize",
    summary="The 'Authorize Application' consent screen",
)
def authorize(
    workspace_id: str,
    body: AuthorizeBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["tokens.read"])
    return access.authorize(
        workspace_id,
        client_id=body.client_id,
        requested_scopes=body.scopes,
        source=_source(f"/workspaces/{workspace_id}/oauth/authorize"),
        actor=_caller(ctx),
        client_name=body.client_name,
        redirect_uri=body.redirect_uri,
    )


@router.post(
    "/workspaces/{workspace_id}/oauth/access-token",
    summary="Exchange an authorization code for a token",
)
def access_token(
    workspace_id: str,
    body: AccessTokenBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["tokens.read"])
    minted = access.exchange_code(
        workspace_id,
        code=body.code,
        client_id=body.client_id,
        source=_source(f"/workspaces/{workspace_id}/oauth/access-token"),
        actor=_caller(ctx),
        scopes=body.scopes,
    )
    return {"token": minted, "secret": minted["secret"], "shown_once": True}


# --------------------------------------------------------------------------- #
# Directory SSO
# --------------------------------------------------------------------------- #


@router.get("/workspaces/{workspace_id}/sso", summary="The directory SSO connection")
def read_sso(
    workspace_id: str,
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    """Read is never plan-gated.

    "existing links keep working after a downgrade" - so a workspace that wired SSO
    and then downgraded still sees its configuration. Refusing to *describe* what is
    already configured would be the gate leaking into a read path, which is the
    mistake the research's asymmetry is written to prevent.
    """
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["sso.read"])
    return access.read_sso(workspace_id)


@router.put("/workspaces/{workspace_id}/sso", summary="Wire the directory SSO connection")
def write_sso(
    workspace_id: str,
    body: SsoBody = Body(...),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    access.authorise(ctx, ["sso.write"])
    payload = body.model_dump(exclude_none=True)
    return {
        "sso": access.write_sso(
            workspace_id,
            payload,
            source=_source(f"/workspaces/{workspace_id}/sso"),
            actor=_caller(ctx),
        ),
        "published": published_sso(),
    }


# --------------------------------------------------------------------------- #
# The role-gated audit read
# --------------------------------------------------------------------------- #


@router.get(
    "/workspaces/{workspace_id}/audit",
    summary="Who changed whose role, and when. Administrators only.",
)
def audit_read(
    workspace_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    authorization: str = Header(default=""),
    member: str = Header(default="", alias=MEMBER_HEADER),
    access: WorkspaceAccess = AccessDep,
) -> dict[str, Any]:
    """The vendor's role gate, implemented.

    "This endpoint is accessible to authorized workspace administrators only" -
    quoted by the research from PandaDoc's audit-trail endpoint. The permission
    checked is ``part11.read``, which only ``Admin`` carries among the built-ins, so
    a ``Manager`` - who can change roles - still cannot read who changed them.

    That separation is the point and it is why the gate is a permission rather
    than "can edit roles": being able to do the thing and being able to audit the
    thing are different powers, and the vendor draws the line between them.
    """
    ctx = _context(access, workspace_id, authorization=authorization, member=member, response=None)
    # The token scope and the human permission differ here, deliberately. A token
    # that can read members is a plausible auditor; a *person* needs
    # `part11.read`, which only Admin carries. See WorkspaceAccess.authorise.
    access.authorise(ctx, ["members.read"], member_permission=vocab.PERM_PART11_READ)

    scanned = access.store.audit(limit=limit * 8)
    member_ids = {row["id"] for row in access.list_members(workspace_id)}
    rows = [
        {
            "ts": entry["ts"],
            "action": entry["action"],
            "record_id": entry["record_id"],
            "actor": entry["actor"],
            "source": entry["source"],
            "summary": entry["summary"],
            "diff": entry.get("diff"),
        }
        for entry in scanned
        if entry.get("collection") == MEMBERSHIPS or str(entry.get("record_id") or "") in member_ids
    ]
    page = rows[:limit]
    return {
        "workspace_id": workspace_id,
        "gate": vocab.PART11_QUOTE,
        "required_permission": "part11.read",
        "entries": page,
        "count": len(page),
        "scanned": len(scanned),
    }


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #

#: The demo workspace. Named after the demo dataset's first account so the page
#: and the rooms a reviewer is already looking at agree with each other.
SEED_WORKSPACE = "northwind"


def seed(db, context: dict[str, Any]) -> str:
    """Seed the states the research says matter, not just the happy path.

    What is on screen when the page loads:

    * an **owner** whose role cannot be changed, and a **second admin** so the
      last-admin guard rail has something to bite on that is *not* the owner;
    * a **Guest on their way up**, so the auto-upgrade to a Full seat is visible
      the moment somebody promotes them;
    * a **custom role** that grants only ``members.read``, so a token minted for
      it and a role change attempted through it fail differently and legibly;
    * a **write-only ingestion token** carrying ``documents.write`` and nothing
      else - the sharpest rule in the research, made visible;
    * a **revoked token**, because a token list with nothing revoked teaches a
      reviewer nothing;
    * a **workspace on a plan that does not entitle SSO**, so the plan gate is
      demonstrable rather than theoretical;
    * a **staff member on a `starter` plan** whose SSO connection is already wired,
      so "existing links keep working after a downgrade" is visible too.
    """
    now = context["now"]
    stamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    room_ids: list[tuple[str, str]] = context.get("room_ids") or []
    room_id = room_ids[0][0] if room_ids else None

    def _create(collection: str, payload: dict[str, Any], actor: str = "dana") -> dict[str, Any]:
        return db.create(collection, payload, room_id=room_id, actor=actor, source="seed")

    members = [
        (
            "owner@northwind.example",
            "Priya Raman",
            vocab.ROLE_OWNER,
            vocab.SEAT_FULL,
            sso_rules.AUTH_LOCAL,
            True,
        ),
        (
            "dana@northwind.example",
            "Dana Okafor",
            vocab.ADMIN,
            vocab.SEAT_FULL,
            sso_rules.AUTH_SSO,
            False,
        ),
        (
            "sam@northwind.example",
            "Sam Whitfield",
            vocab.MANAGER,
            vocab.SEAT_FULL,
            sso_rules.AUTH_LOCAL,
            False,
        ),
        (
            "ines@northwind.example",
            "Ines Ferreira",
            vocab.MEMBER,
            vocab.SEAT_FULL,
            sso_rules.AUTH_LOCAL,
            False,
        ),
        (
            "noor@northwind.example",
            "Noor Haddad",
            vocab.COLLABORATOR,
            vocab.SEAT_GUEST,
            sso_rules.AUTH_SSO,
            False,
        ),
    ]
    member_ids: list[str] = []
    for email, name, role, seat, auth, is_owner in members:
        record = _create(
            "workspace_membership",
            {
                "workspace_id": SEED_WORKSPACE,
                "email": email,
                "name": name,
                "role": role,
                "seat": seat,
                "auth": auth,
                "active": True,
                "is_owner": is_owner,
                "plan": vocab.PLAN_BUSINESS,
                "sso_subject": email if auth == sso_rules.AUTH_SSO else "",
            },
        )
        member_ids.append(record["id"])

    # A custom role that is deliberately *not* an admin: it carries one
    # permission. If it counted as an admin the last-admin rail would behave
    # differently, so a reviewer can see that "admin" means a permission set.
    _create(
        "workspace_role_definition",
        {
            "workspace_id": SEED_WORKSPACE,
            "name": "Deal Desk",
            "permissions": ["members.read"],
            "description": "Reads the member list. Cannot change a role, cannot mint a token.",
            "created_by": "dana",
        },
    )
    _create(
        "workspace_role_definition",
        {
            "workspace_id": SEED_WORKSPACE,
            "name": "Integration Owner",
            "permissions": ["members.read", "tokens.read", "tokens.write"],
            "description": "Runs the integrations and nothing else.",
            "created_by": "dana",
        },
    )
    # Somebody actually holding the custom role, so the member list shows a
    # workspace-defined role beside the built-in ones.
    _create(
        "workspace_membership",
        {
            "workspace_id": SEED_WORKSPACE,
            "email": "kai@northwind.example",
            "name": "Kai Lindqvist",
            "role": "Deal Desk",
            "seat": vocab.SEAT_FULL,
            "auth": sso_rules.AUTH_LOCAL,
            "active": True,
            "is_owner": False,
            "plan": vocab.PLAN_BUSINESS,
        },
    )

    # The write-only ingestion token. `documents.write` and nothing else: this is
    # the researched use case verbatim ("an ingestion worker"), and it cannot read.
    _create(
        "workspace_token",
        {
            "workspace_id": SEED_WORKSPACE,
            "name": "Ingestion worker (write-only)",
            "scopes": ["documents.write"],
            "prefix": "dsr_demo_ing",
            # Not a real secret, and not a usable hash either: a demo credential
            # that would authenticate is worse than one that says it cannot.
            "secret_hash": "seeded-demo-not-a-usable-hash",
            "minted_plan": vocab.PLAN_BUSINESS,
            "minted_role": vocab.MANAGER,
            "minted_by": "dana",
            "minted_at": stamp,
            "revoked": False,
        },
        actor="dana",
    )
    # A read-only analytics token, and a revoked one, so the list is not all
    # success.
    _create(
        "workspace_token",
        {
            "workspace_id": SEED_WORKSPACE,
            "name": "Dashboards (read-only)",
            "scopes": ["analytics.read", "documents.read"],
            "prefix": "dsr_demo_dash",
            "secret_hash": "seeded-demo-not-a-usable-hash-2",
            "minted_plan": vocab.PLAN_BUSINESS,
            "minted_role": vocab.MEMBER,
            "minted_by": "sam",
            "minted_at": stamp,
            "revoked": False,
        },
        actor="sam",
    )
    _create(
        "workspace_token",
        {
            "workspace_id": SEED_WORKSPACE,
            "name": "Retired nightly export",
            "scopes": ["links.write", "links.read"],
            "prefix": "dsr_demo_old",
            "secret_hash": "seeded-demo-not-a-usable-hash-3",
            "minted_plan": vocab.PLAN_BUSINESS,
            "minted_role": vocab.ADMIN,
            "minted_by": "dana",
            "minted_at": stamp,
            "revoked": True,
            "revoked_at": stamp,
            "revoked_by": "dana",
        },
        actor="dana",
    )

    # A second workspace on a plan that does not entitle SSO, so the plan gate is
    # demonstrable: this one cannot wire directory SSO, and minting a token is
    # refused with forbidden_plan_feature.
    contoso = "contoso"
    _create(
        "workspace_membership",
        {
            "workspace_id": contoso,
            "email": "alex@contoso.example",
            "name": "Alex Duarte",
            "role": vocab.ROLE_OWNER,
            "seat": vocab.SEAT_FULL,
            "auth": sso_rules.AUTH_LOCAL,
            "active": True,
            "is_owner": True,
            "plan": vocab.PLAN_STARTER,
        },
    )
    _create(
        "workspace_membership",
        {
            "workspace_id": contoso,
            "email": "ruth@contoso.example",
            "name": "Ruth Bello",
            "role": vocab.ADMIN,
            "seat": vocab.SEAT_FULL,
            "auth": sso_rules.AUTH_SSO,
            "active": True,
            "is_owner": False,
            "plan": vocab.PLAN_STARTER,
        },
        actor="alex",
    )
    # SSO already wired on the downgraded workspace: the research's asymmetry, made
    # visible. Reading this connection keeps working; creating another one would
    # not.
    _create(
        "workspace_sso",
        {
            "workspace_id": contoso,
            "protocol": sso_rules.PROTOCOL_OIDC,
            "idp_entity_id": "https://idp.contoso.example",
            "sso_url": "https://idp.contoso.example/sso",
            "domains": ["contoso.example"],
            "enabled": True,
            "note": "Wired before the downgrade. Reads keep working; new writes are refused.",
        },
        actor="alex",
    )

    return (
        f"{len(member_ids) + 2} members across 2 workspaces, 2 custom roles, "
        f"3 tokens (1 revoked, 1 write-only), 1 SSO connection on a downgraded plan"
    )
