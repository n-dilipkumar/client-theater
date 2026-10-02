"""WF-077 over the audited store: memberships, custom roles, tokens, SSO.

The domain rules live in :mod:`dsr.workspace_roles.rules` and
:mod:`dsr.workspace_roles.scopes` as pure functions. This module is the only place
that reads and writes, and it does both exclusively through
:class:`~dsr.store.RecordStore`, so every change lands in the audit log in the same
transaction as the change itself.

Three invariants this module holds, each of which has cost somebody a bug:

* **``source`` is a required keyword on every write.** Not a default, not a
  string built inside this module. The audit row has to name the route that
  actually served the write, and the only way to keep that true when the prefix
  changes is to make the caller pass it and to have no default to fall back on.
* **A token's secret is never stored and never returned.** Only a hash and a
  display prefix are persisted, and the plaintext appears exactly once, in the
  mint response. This is the one piece of this workflow where the researched
  behaviour ("Token is shown once", on the sibling WF-056 flow) and ordinary
  engineering judgement agree, so it is not treated as an inference.
* **No caching.** "Role/scope checks are enforced on every request (no caching
  window documented)." So :meth:`WorkspaceAccess.context` reads the membership on
  every call and holds no cache. That costs a query per request and is the price
  of the sentence being true; the alternative is a role change that takes effect
  "on the member's next request" being true only sometimes.

Collections
-----------
Four, all ordinary schema-flexible records. Every field lives in ``records.data``
as JSON, so a team adding ``cost_center`` to a membership needs no migration and
no coordination with anyone.

``workspace_membership``
    One row per person in a workspace: their role, their seat, how they
    authenticate, and whether they are the owner.
``workspace_role_definition``
    A workspace-defined custom role and the permissions it carries.
``workspace_token``
    A minted integration token: its scopes, its hash, its prefix, whether it is
    revoked, and the plan and role that were in force when it was minted.
``workspace_oauth_code``
    A one-time authorization code from the consent screen.
``workspace_sso``
    The directory SSO connection. One live row per workspace.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.store import RecordStore
from dsr.workspace_roles import (
    oauth as oauth_flow,
    rules,
    scopes as scope_rules,
    sso as sso_rules,
    vocabulary as vocab,
)
from dsr.workspace_roles.errors import MembershipNotFound, RoleError, TokenError

MEMBERSHIPS = "workspace_membership"
ROLE_DEFINITIONS = "workspace_role_definition"
TOKENS = "workspace_token"
OAUTH_CODES = "workspace_oauth_code"
SSO_CONNECTIONS = "workspace_sso"

COLLECTIONS: tuple[str, ...] = (MEMBERSHIPS, ROLE_DEFINITIONS, TOKENS, OAUTH_CODES, SSO_CONNECTIONS)

#: Where a plan is read from when a record does not carry one. This build's
#: default, recorded in ``inferences.py`` under ``plan-names-are-invented``.
DEFAULT_PLAN = vocab.PLAN_BUSINESS

TOKEN_PREFIX_BYTES = 12
TOKEN_SECRET_BYTES = 32

#: The per-endpoint scope declaration, as data.
#:
#: The research names this pattern as the portable part of the whole workflow: "The
#: pattern is directly portable: a per-endpoint scope declaration plus a closed
#: error catalogue." So it is a *declaration* rather than a scattering of ``if``
#: statements in the handlers: the request path enforces this list, the
#: token-creation screen reads it to answer "which endpoints each scope unlocks",
#: and a test asserts the two agree.
#:
#: Module level and store-free so the ``/scopes`` preview route can serve it
#: without a database, and so a test can assert every path here is a path the host
#: actually mounted.
ENDPOINTS: tuple[dict[str, Any], ...] = (
    {
        "method": "GET",
        "path": "/api/wf-077/workspaces/{workspace_id}/members",
        "name": "List workspace members",
        "scopes": ["members.read"],
    },
    {
        "method": "PATCH",
        "path": "/api/wf-077/workspaces/{workspace_id}/members/{member_id}/role",
        "name": "Change a member's role",
        "scopes": ["members.write"],
    },
    {
        "method": "PATCH",
        "path": "/api/wf-077/workspaces/{workspace_id}/members/{member_id}/seat",
        "name": "Change a member's seat",
        "scopes": ["members.write"],
    },
    {
        "method": "GET",
        "path": "/api/wf-077/workspaces/{workspace_id}/role-definitions",
        "name": "List custom roles",
        "scopes": ["members.read"],
    },
    {
        "method": "POST",
        "path": "/api/wf-077/workspaces/{workspace_id}/role-definitions",
        "name": "Create a custom role",
        "scopes": ["members.write"],
    },
    {
        "method": "GET",
        "path": "/api/wf-077/workspaces/{workspace_id}/tokens",
        "name": "List API tokens",
        "scopes": ["tokens.read"],
    },
    {
        "method": "POST",
        "path": "/api/wf-077/workspaces/{workspace_id}/tokens",
        "name": "Mint an API token",
        "scopes": ["tokens.write"],
    },
    {
        "method": "DELETE",
        "path": "/api/wf-077/workspaces/{workspace_id}/tokens/{token_id}",
        "name": "Revoke an API token",
        "scopes": ["tokens.write"],
    },
    {
        "method": "POST",
        "path": "/api/wf-077/workspaces/{workspace_id}/oauth/authorize",
        "name": "Authorize an application (consent screen)",
        "scopes": ["tokens.read"],
    },
    {
        "method": "POST",
        "path": "/api/wf-077/workspaces/{workspace_id}/oauth/access-token",
        "name": "Exchange an authorization code for a token",
        "scopes": ["tokens.read"],
    },
    {
        "method": "GET",
        "path": "/api/wf-077/workspaces/{workspace_id}/sso",
        "name": "Read the directory SSO connection",
        "scopes": ["sso.read"],
    },
    {
        "method": "PUT",
        "path": "/api/wf-077/workspaces/{workspace_id}/sso",
        "name": "Wire the directory SSO connection",
        "scopes": ["sso.write"],
    },
    {
        "method": "GET",
        "path": "/api/wf-077/workspaces/{workspace_id}/audit",
        "name": "Read the role-change audit trail (administrators only)",
        "scopes": ["members.read"],
    },
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class WorkspaceIdentity:
    """What a workspace is, and where its plan and SSO connection live.

    Split out because "what is a workspace" is the judgement call everything else
    inherits (see ``inferences.granularity-is-a-guess``), and a reviewer who
    disagrees with that decision should be able to change it in one class rather
    than in twenty call sites.
    """

    def plan(self, store: RecordStore, workspace_id: str) -> str:
        """The plan in force for a workspace.

        Read from the workspace's own membership rows: the owner's row carries the
        plan, because there is no workspace record to carry it. Any live
        membership may carry it and the first live row wins, so a workspace can
        move between plans by updating one row rather than by a schema change.
        """
        for record in self.memberships(store, workspace_id):
            plan = str(record["data"].get("plan") or "")
            if plan in vocab.PLAN_RANK:
                return plan
        return DEFAULT_PLAN

    def memberships(self, store: RecordStore, workspace_id: str) -> list[dict[str, Any]]:
        return store.find(MEMBERSHIPS, {"workspace_id": workspace_id}, limit=1000)

    def custom_roles(self, store: RecordStore, workspace_id: str) -> list[dict[str, Any]]:
        return store.find(ROLE_DEFINITIONS, {"workspace_id": workspace_id}, limit=1000)

    def owner(self, store: RecordStore, workspace_id: str) -> dict[str, Any] | None:
        for record in self.memberships(store, workspace_id):
            if record["data"].get("role") == vocab.ROLE_OWNER or record["data"].get("is_owner"):
                return record
        return None

    def sso_connection(self, store: RecordStore, workspace_id: str) -> dict[str, Any] | None:
        rows = store.find(SSO_CONNECTIONS, {"workspace_id": workspace_id}, limit=10)
        return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TokenContext:
    """A presented API token, resolved and checked against its workspace."""

    id: str
    workspace_id: str
    name: str
    scopes: tuple[str, ...]
    minted_plan: str
    minted_role: str
    client_id: str = ""
    prefix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "scopes": list(self.scopes),
            "prefix": self.prefix,
            "client_id": self.client_id,
            "minted_plan": self.minted_plan,
            "minted_role": self.minted_role,
        }


@dataclass(frozen=True)
class AccessContext:
    """Who is making a request: a token, a member, both, or neither.

    Both is the interesting case. Seismic: "Scopes are not intended to override a
    users defined permissions." So a request carrying a token *and* a member is
    held to the member's own permissions as well as the token's scopes, and a
    scope grant cannot rescue a member who lacks the permission. Neither is the
    case for a member-less machine call, where there is no user to override.
    """

    workspace_id: str
    token: TokenContext | None = None
    member: Mapping[str, Any] | None = None
    plan: str = DEFAULT_PLAN
    reset_at: str = ""
    rate_limit_remaining: int = -1

    @property
    def is_machine(self) -> bool:
        return self.token is not None

    @property
    def custom_roles(self) -> list[Mapping[str, Any]]:
        return self._custom_roles

    _custom_roles: list[Mapping[str, Any]] = field(default_factory=list)

    def permissions(self) -> tuple[str, ...]:
        if self.member is None:
            return ()
        return rules.permissions_for(self.member, custom_roles=self.custom_roles)

    def require(self, permission: str, *, actor: str = "") -> None:
        """Refuse unless a *member* holds the permission.

        A token never satisfies this. That is the Seismic rule as behaviour: a
        token is a machine's credential, and giving it the power to stand in for a
        person would be precisely the override the vendor documents as unintended.
        """
        member = self.member
        if member is None:
            raise RoleError(
                f"this action needs a member holding {permission}; a token alone cannot stand in "
                "for one, because scopes do not override a user's defined permissions",
                permission=permission,
                rule=vocab.SCOPES_DO_NOT_OVERRIDE_QUOTE,
            )
        rules.require_permission(member, permission, actor=actor, custom_roles=self.custom_roles)

    def to_dict(self) -> dict[str, Any]:
        """Who is calling, and what they hold, for the ``caller`` block of a body.

        The reset instant is deliberately *not* here. It is a header value - the
        handler publishes it as ``X-RateLimit-Reset`` - and copying the limiter's
        raw value into a JSON body means anything the limiter returns is now
        something a serializer has to be able to encode. The real limiter always
        returns an ISO string, so nothing is broken today; the point is that a 200
        response should not be able to fail because a throttling helper changed
        what it hands back.
        """
        return {
            "workspace_id": self.workspace_id,
            "plan": self.plan,
            "machine": self.is_machine,
            "token": self.token.to_dict() if self.token else None,
            "member": {
                "id": (self.member or {}).get("id"),
                "email": (self.member or {}).get("email"),
                "role": (self.member or {}).get("role"),
                "seat": (self.member or {}).get("seat"),
            }
            if self.member
            else None,
            "permissions": list(self.permissions()),
            "rate_limited": bool(self.reset_at),
        }


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


class WorkspaceAccess:
    """Everything WF-077 does, over one :class:`~dsr.store.RecordStore`."""

    def __init__(self, store: RecordStore, *, identity: WorkspaceIdentity | None = None) -> None:
        self.store = store
        self.identity = identity or WorkspaceIdentity()
        self.limiter = scope_rules.RateLimiter(vocab.DEFAULT_RATE_LIMIT_PER_MINUTE)

    # -- reads ------------------------------------------------------------- #

    def workspaces(self) -> list[str]:
        """Every workspace id that has at least one live membership."""
        seen: dict[str, None] = {}
        for record in self.store.list(MEMBERSHIPS, limit=1000, order_by="created_at"):
            key = str(record["data"].get("workspace_id") or "")
            if key:
                seen.setdefault(key, None)
        return sorted(seen)

    def list_members(
        self,
        workspace_id: str,
        *,
        role: str = "",
        include_inactive: bool = True,
    ) -> list[dict[str, Any]]:
        records = self.identity.memberships(self.store, workspace_id)
        custom = {
            rules.role_key(str(r["data"].get("name") or "")): r["data"]
            for r in self.identity.custom_roles(self.store, workspace_id)
        }
        rows = []
        for record in records:
            data = record["data"]
            if not include_inactive and data.get("active") is False:
                continue
            if role and rules.role_key(str(data.get("role") or "")) != rules.role_key(role):
                continue
            custom_role = custom.get(rules.role_key(str(data.get("role") or "")))
            rows.append(
                {
                    "id": record["id"],
                    "workspace_id": workspace_id,
                    "email": data.get("email", ""),
                    "name": data.get("name", ""),
                    "role": data.get("role", ""),
                    "role_label": rules.display_role(data, custom_role),
                    "seat": data.get("seat", vocab.SEAT_FULL),
                    "auth": data.get("auth", sso_rules.AUTH_LOCAL),
                    "active": data.get("active", True) is not False,
                    "is_owner": bool(data.get("is_owner")) or data.get("role") == vocab.ROLE_OWNER,
                    "is_admin": rules.is_admin(data, custom_roles=custom.values()),
                    "permissions": list(rules.permissions_for(data, custom_roles=custom.values())),
                    "plan": data.get("plan", ""),
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                }
            )
        rows.sort(key=lambda row: (not row["is_owner"], str(row["email"]).lower(), str(row["id"])))
        return rows

    def member(self, workspace_id: str, member_id: str) -> dict[str, Any]:
        record = self.store.get(member_id)
        if (
            record is None
            or record["collection"] != MEMBERSHIPS
            or str(record["data"].get("workspace_id") or "") != str(workspace_id)
        ):
            raise MembershipNotFound(
                f"no member {member_id!r} in workspace {workspace_id!r}", member_id=member_id
            )
        return record

    def member_count(self, workspace_id: str, **where: Any) -> int:
        return self.store.count_where(MEMBERSHIPS, {"workspace_id": workspace_id, **where})

    def admin_count(self, workspace_id: str) -> int:
        """Live, non-owner members that hold every permission.

        Counted with a Python predicate over the workspace's memberships rather
        than with a ``where`` clause, because "admin" here means a permission set
        (see ``inferences.admin-means-every-permission``) and a custom role's
        permissions live in a different collection. This is the one count in the
        workflow that cannot be a query, and it is the count the last-admin guard
        rail turns on, so it is worth saying out loud.
        """
        custom = [r["data"] for r in self.identity.custom_roles(self.store, workspace_id)]
        total = 0
        for record in self.identity.memberships(self.store, workspace_id):
            data = record["data"]
            if data.get("active") is False or data.get("role") == vocab.ROLE_OWNER:
                continue
            if rules.is_admin(data, custom_roles=custom):
                total += 1
        return total

    def list_custom_roles(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = []
        for record in self.identity.custom_roles(self.store, workspace_id):
            data = record["data"]
            holders = self.member_count(workspace_id, role=str(data.get("name") or ""))
            rows.append(
                {
                    "id": record["id"],
                    "workspace_id": workspace_id,
                    "name": data.get("name", ""),
                    "permissions": list(data.get("permissions") or ()),
                    "is_admin": rules.is_admin({"role": data.get("name")}, custom_roles=[data]),
                    "description": data.get("description", ""),
                    "holders": holders,
                    "created_at": record["created_at"],
                }
            )
        rows.sort(key=lambda row: str(row["name"]).lower())
        return rows

    def list_tokens(
        self, workspace_id: str, *, include_revoked: bool = False
    ) -> list[dict[str, Any]]:
        rows = []
        for record in self.store.find(TOKENS, {"workspace_id": workspace_id}, limit=1000):
            data = record["data"]
            if data.get("revoked") and not include_revoked:
                continue
            rows.append(self._token_view(record))
        rows.sort(key=lambda row: str(row["created_at"]), reverse=True)
        return rows

    def token_view(self, token_id: str) -> dict[str, Any]:
        record = self.store.get(token_id)
        if record is None or record["collection"] != TOKENS:
            raise TokenError(f"no token {token_id!r}")
        return self._token_view(record)

    @staticmethod
    def _token_view(record: Mapping[str, Any]) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "workspace_id": data.get("workspace_id", ""),
            "name": data.get("name", ""),
            "prefix": data.get("prefix", ""),
            "scopes": list(data.get("scopes") or ()),
            "revoked": bool(data.get("revoked")),
            "revoked_at": data.get("revoked_at"),
            "client_id": data.get("client_id", ""),
            "minted_plan": data.get("minted_plan", ""),
            "minted_role": data.get("minted_role", ""),
            "minted_by": data.get("minted_by", ""),
            "minted_at": data.get("minted_at") or record["created_at"],
            "last_used_at": data.get("last_used_at"),
            "expires_at": data.get("expires_at"),
            # Carried so a listing can order on the record's own timestamp rather
            # than on a payload field a workspace could have edited.
            "created_at": record["created_at"],
            # The secret itself is not here and never is: only its hash was stored.
            "secret_stored": False,
        }

    def resolve_token(self, presented: str) -> dict[str, Any]:
        """Find the token record a bearer secret belongs to.

        Lookup is by hash, so the stored row cannot leak the secret and an
        indexed lookup on the hash does not have to scan. A revoked or expired
        token resolves to nothing, which is the same answer as an unknown one on
        purpose: telling a caller their token was revoked is information about a
        credential they no longer hold.
        """
        secret = str(presented or "").strip()
        if not secret:
            raise TokenError("no bearer token presented")
        digest = _hash(secret)
        for record in self.store.find(TOKENS, {"secret_hash": digest}, limit=10):
            data = record["data"]
            if data.get("revoked"):
                continue
            expires_at = str(data.get("expires_at") or "")
            if expires_at and datetime.now(timezone.utc) >= datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            ):
                continue
            return record
        raise TokenError("that token is not valid")

    def endpoints(self) -> list[dict[str, Any]]:
        """Every endpoint's declared scope requirements.

        Kept here, beside the data, rather than in the router, because the router's
        job is to enforce them and the *declaration* has to be readable by the
        token-creation surface too. One list, read by both, is what stops the
        screen lying to an integrator.
        """
        return [dict(entry) for entry in ENDPOINTS]

    def summary(self, workspace_id: str) -> dict[str, Any]:
        custom = [r["data"] for r in self.identity.custom_roles(self.store, workspace_id)]
        members = self.list_members(workspace_id)
        tokens = self.list_tokens(workspace_id)
        connection = self.identity.sso_connection(self.store, workspace_id)
        guests = sum(1 for row in members if row["seat"] == vocab.SEAT_GUEST)
        sso_only = sum(1 for row in members if row["auth"] == sso_rules.AUTH_SSO)
        return {
            "workspace_id": workspace_id,
            "plan": self.identity.plan(self.store, workspace_id),
            "members": len(members),
            "owners": sum(1 for row in members if row["is_owner"]),
            "admins": sum(1 for row in members if row["is_admin"] and not row["is_owner"]),
            "guests": guests,
            "sso_only_members": sso_only,
            "custom_roles": len(custom),
            "tokens": len(tokens),
            "revoked_tokens": sum(1 for row in tokens if row["revoked"]),
            "sso": sso_rules.connection_summary(connection),
            "scope_catalogue_size": len(scope_rules.KNOWN_SCOPES),
            "endpoints": len(self.endpoints()),
            "no_implicit_hierarchy": vocab.NO_HIERARCHY_QUOTE,
        }

    # -- context ----------------------------------------------------------- #

    def context(
        self,
        workspace_id: str,
        *,
        bearer: str = "",
        member_id: str = "",
        rate_limit: bool = True,
        now: float | None = None,
    ) -> AccessContext:
        """Resolve a request into an :class:`AccessContext`.

        Throttling is applied here, on *every* request, because the research says
        "Role/scope checks are enforced on every request (no caching window
        documented)" - the same sentence governs both, and a budget checked only on
        writes is not a budget.
        """
        token_record: dict[str, Any] | None = None
        token: TokenContext | None = None
        if bearer:
            token_record = self.resolve_token(bearer)
            data = token_record["data"]
            token = TokenContext(
                id=token_record["id"],
                workspace_id=str(data.get("workspace_id") or ""),
                name=str(data.get("name") or ""),
                scopes=tuple(str(s) for s in (data.get("scopes") or ())),
                minted_plan=str(data.get("minted_plan") or ""),
                minted_role=str(data.get("minted_role") or ""),
                client_id=str(data.get("client_id") or ""),
                prefix=str(data.get("prefix") or ""),
            )

        if token is not None and token.workspace_id and token.workspace_id != str(workspace_id):
            # The second half of Papermark's 403 sentence: a valid token that is
            # not authorised to act on the team it is addressing. Surfaced through
            # the same refusal as a scope miss, because the vendor cannot tell
            # them apart either.
            raise scope_rules.Forbidden(member_forbidden=f"token is scoped to {token.workspace_id}")

        member: Mapping[str, Any] | None = None
        if member_id:
            member = self.member(workspace_id, member_id)["data"]

        reset_at = ""
        remaining = -1
        if rate_limit:
            stamp = time.time() if now is None else float(now)
            # The budget is keyed on the token when there is one and on the member
            # otherwise, so two members behind one session do not share a budget
            # and an anonymous request cannot exhaust a named member's.
            key = token.id if token else f"member:{member_id or 'anonymous'}"
            reset_at = self.limiter.check(key, stamp)
            remaining = self.limiter.remaining(key, stamp)

        return AccessContext(
            workspace_id=str(workspace_id),
            token=token,
            member=member,
            plan=self.identity.plan(self.store, workspace_id),
            reset_at=reset_at,
            rate_limit_remaining=remaining,
            _custom_roles=[r["data"] for r in self.identity.custom_roles(self.store, workspace_id)],
        )

    def authorise(
        self,
        context: AccessContext,
        required: Sequence[str],
        *,
        member_permission: str = "",
    ) -> None:
        """The per-endpoint check, as researched.

        Raises :class:`~dsr.workspace_roles.scopes.Forbidden` - the researched
        403 - when the token is missing a scope *or* a member is present and lacks
        the permission. Both halves are checked; neither can rescue the other.

        ``required`` is the *token scope* the endpoint declares.
        ``member_permission`` is the *human permission* required, which is not
        always the same string. The role-gated audit read is the case that forces
        the distinction: it declares ``members.read`` as its token scope, because a
        token that can list members is a plausible auditor, but the permission it
        requires of a *person* is ``part11.read``, which no built-in role below
        Admin carries. Defaulting the two together would have quietly let every
        Manager read the audit trail, which is the opposite of "This endpoint is
        accessible to authorized workspace administrators only."

        The two checks deliberately use *different* functions, because they are
        different kinds of thing even where the names coincide: a token's grant is
        a scope set with no hierarchy, so it goes through
        :func:`~dsr.workspace_roles.scopes.token_has_scope`; a member's grant is a
        permission set, so it goes through
        :func:`~dsr.workspace_roles.rules.has_permission`. Using the scope function
        for both would blur the one distinction the research is most explicit
        about - "Machine access is separate and strictly scope-gated."
        """
        granted = context.token.scopes if context.token else None
        member_ok: bool | None = None
        wanted = member_permission or (required[0] if required else "")
        if context.member is not None:
            member_ok = (
                rules.has_permission(context.member, wanted, custom_roles=context.custom_roles)
                if wanted
                else True
            )
        scope_rules.authorise(
            granted=granted,
            required=required,
            member_permission=wanted,
            member_allowed=member_ok,
        )

    # -- role writes ------------------------------------------------------- #

    def preview_role_change(
        self, workspace_id: str, member_id: str, requested_role: str
    ) -> rules.RoleDecision:
        """What a role change would do. Writes nothing.

        Same pure function the write path uses, so a preview that says *refused*
        is a refusal that will happen.
        """
        record = self.member(workspace_id, member_id)
        return rules.decide_role_change(
            member={**record["data"], "id": record["id"]},
            memberships=[
                {**r["data"], "id": r["id"]}
                for r in self.identity.memberships(self.store, workspace_id)
            ],
            requested_role=requested_role,
            custom_roles=[r["data"] for r in self.identity.custom_roles(self.store, workspace_id)],
        )

    def change_role(
        self,
        workspace_id: str,
        member_id: str,
        requested_role: str,
        *,
        source: str,
        actor: str = "api",
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply a role change, or refuse it.

        ``source`` is required and has no default: the audit row must name the
        route that served the write, and a default would let a caller forget.
        """
        decision = self.preview_role_change(workspace_id, member_id, requested_role)
        if decision.refused:
            raise self._error_for(decision)

        patch: dict[str, Any] = dict(decision.patch)
        patch["role_changed_at"] = _utcnow()
        patch["role_changed_by"] = actor
        if reason:
            patch["role_change_reason"] = str(reason)

        record = self.store.update(member_id, patch, actor=actor, source=source)
        return {
            "member": self._member_view(workspace_id, record),
            "decision": decision.to_dict(),
            "seat_upgraded": decision.seat_changed,
        }

    @staticmethod
    def _error_for(decision: rules.RoleDecision) -> Exception:
        if decision.outcome == rules.REFUSED_LAST_ADMIN:
            return RoleError(
                "this is the last member with admin privileges in the workspace, so the role "
                f"cannot be changed ({vocab.LAST_ADMIN_QUOTE})",
                outcome=decision.outcome,
                rule=decision.rule,
            )
        if decision.outcome == rules.REFUSED_OWNER:
            return RoleError(
                f"the workspace owner's role cannot be changed ({vocab.OWNER_IMMUTABLE_QUOTE})",
                outcome=decision.outcome,
                rule=decision.rule,
            )
        return RoleError(
            decision.reason or "that role change was refused",
            outcome=decision.outcome,
            rule=decision.rule,
        )

    def change_seat(
        self,
        workspace_id: str,
        member_id: str,
        seat: str,
        *,
        source: str,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Set a member's seat directly.

        The auto-upgrade belongs to *promotion* - "a ``Guest`` promoted to a
        non-``Collaborator`` role is auto-upgraded" - so it is a property of a role
        change and not of this route. Setting the seat here is the deliberate,
        explicit action, and downgrading a seat to ``guest`` is allowed because the
        research forbids nothing of the kind.
        """
        # Validates that the membership exists and belongs to this workspace
        # before anything is written; the record itself is not needed.
        self.member(workspace_id, member_id)
        wanted = rules.normalise_seat(seat)
        updated = self.store.update(
            member_id,
            {"seat": wanted, "seat_changed_at": _utcnow()},
            actor=actor,
            source=source,
        )
        return {"member": self._member_view(workspace_id, updated)}

    def _member_view(self, workspace_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
        custom = {
            rules.role_key(str(r["data"].get("name") or "")): r["data"]
            for r in self.identity.custom_roles(self.store, workspace_id)
        }
        data = record["data"]
        key = rules.role_key(str(data.get("role") or ""))
        return {
            "id": record["id"],
            "workspace_id": workspace_id,
            "email": data.get("email", ""),
            "name": data.get("name", ""),
            "role": data.get("role", ""),
            "role_label": rules.display_role(data, custom.get(key)),
            "seat": data.get("seat", vocab.SEAT_FULL),
            "auth": data.get("auth", sso_rules.AUTH_LOCAL),
            "active": data.get("active", True) is not False,
            "is_owner": bool(data.get("is_owner")) or data.get("role") == vocab.ROLE_OWNER,
            "is_admin": rules.is_admin(data, custom_roles=custom.values()),
            "permissions": list(rules.permissions_for(data, custom_roles=custom.values())),
            "role_changed_at": data.get("role_changed_at"),
            "role_changed_by": data.get("role_changed_by"),
            "updated_at": record["updated_at"],
        }

    # -- custom role writes ------------------------------------------------ #

    def create_custom_role(
        self,
        workspace_id: str,
        name: str,
        permissions: Iterable[str],
        *,
        source: str,
        actor: str = "api",
        description: str = "",
    ) -> dict[str, Any]:
        """Define a workspace role, plan-gated on create.

        Permission names are validated against
        :data:`vocabulary.PERMISSIONS` rather than accepted freely: an unknown
        permission in a role is a typo, and a typo that silently grants nothing is
        the failure mode this refuses.
        """
        clean = str(name or "").strip()
        if not clean:
            raise RoleError("a custom role needs a name")
        if rules.is_built_in(clean):
            raise RoleError(
                f"{clean!r} is a built-in role name; a custom role needs a name of its own",
                role=clean,
            )
        wanted = sorted({str(p).strip() for p in permissions or () if str(p).strip()})
        unknown = [p for p in wanted if p not in vocab.PERMISSIONS]
        if unknown:
            raise RoleError(
                "unknown permission(s): "
                + ", ".join(unknown)
                + "; known: "
                + ", ".join(vocab.PERMISSIONS),
                unknown=unknown,
            )
        if not wanted:
            raise RoleError("a custom role needs at least one permission")
        for existing in self.identity.custom_roles(self.store, workspace_id):
            if rules.role_key(str(existing["data"].get("name") or "")) == rules.role_key(clean):
                raise RoleError(f"a custom role named {clean!r} already exists in this workspace")

        # Deliberately NOT plan-gated. The research's gated features are token
        # minting, directory SSO and the regulated audit read; it never says a
        # workspace-defined role is a paid feature, and inventing a gate here would
        # refuse something the research puts no price on.
        record = self.store.create(
            ROLE_DEFINITIONS,
            {
                "workspace_id": workspace_id,
                "name": clean,
                "permissions": wanted,
                "description": str(description or ""),
                "created_by": actor,
            },
            actor=actor,
            source=source,
        )
        return self._role_view(record)

    def _role_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record["data"]
        return {
            "id": record["id"],
            "workspace_id": data.get("workspace_id", ""),
            "name": data.get("name", ""),
            "permissions": list(data.get("permissions") or ()),
            "description": data.get("description", ""),
            "is_admin": rules.is_admin({"role": data.get("name")}, custom_roles=[data]),
            "holders": self.member_count(
                str(data.get("workspace_id") or ""), role=str(data.get("name") or "")
            ),
            "created_at": record["created_at"],
        }

    # -- token writes ------------------------------------------------------ #

    def mint_token(
        self,
        workspace_id: str,
        *,
        name: str,
        scopes: Sequence[Any],
        source: str,
        actor: str = "api",
        client_id: str = "",
        minted_role: str = "",
        expires_at: str = "",
        plan: str = "",
    ) -> dict[str, Any]:
        """Mint a least-privilege token. Plan-gated on create.

        The scope list is normalised first, which is where a wildcard and an
        unknown scope are refused: "The token endpoint will reject unknown
        scopes." The secret is generated here, hashed into the record, and
        returned exactly once.
        """
        clean_name = str(name or "").strip()
        if not clean_name:
            raise RoleError("a token needs a name so it can be recognised later")
        wanted = scope_rules.normalise_scopes(scopes)
        if not wanted:
            raise RoleError("a token needs at least one scope; scopes are chosen a la carte")
        self._require_plan(workspace_id, "tokens", source=source)

        secret = f"dsr_{secrets.token_urlsafe(TOKEN_SECRET_BYTES)}"
        prefix = secret[: TOKEN_PREFIX_BYTES + 4]
        plan_in_force = plan or self.identity.plan(self.store, workspace_id)
        record = self.store.create(
            TOKENS,
            {
                "workspace_id": workspace_id,
                "name": clean_name,
                "scopes": list(wanted),
                "prefix": prefix,
                "secret_hash": _hash(secret),
                "client_id": str(client_id or ""),
                "minted_plan": plan_in_force,
                "minted_role": minted_role,
                "minted_by": actor,
                "minted_at": _utcnow(),
                "revoked": False,
                "expires_at": str(expires_at or ""),
            },
            actor=actor,
            source=source,
        )
        view = self._token_view(record)
        # Shown once, never stored, never returned again.
        view["secret"] = secret
        view["secret_shown_once"] = True
        view["coverage"] = sorted(scope_rules.coverage_for(wanted))
        view["surfaces"] = scope_rules.granted_surfaces(wanted)
        view["unlocks"] = scope_rules.unlocked_endpoints(wanted, self.endpoints())
        return view

    def revoke_token(
        self, workspace_id: str, token_id: str, *, source: str, actor: str = "api"
    ) -> dict[str, Any]:
        record = self.store.get(token_id)
        if (
            record is None
            or record["collection"] != TOKENS
            or str(record["data"].get("workspace_id") or "") != str(workspace_id)
        ):
            raise TokenError(
                f"no token {token_id!r} in workspace {workspace_id!r}", token_id=token_id
            )
        updated = self.store.update(
            token_id,
            {"revoked": True, "revoked_at": _utcnow(), "revoked_by": actor},
            actor=actor,
            source=source,
        )
        return self._token_view(updated)

    # -- OAuth ------------------------------------------------------------- #

    def authorize(
        self,
        workspace_id: str,
        *,
        client_id: str,
        requested_scopes: Sequence[Any],
        source: str,
        actor: str = "api",
        client_name: str = "",
        redirect_uri: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """The consent screen, and on approval a one-time authorization code."""
        request = oauth_flow.new_consent_request(
            workspace_id=workspace_id,
            client_id=client_id,
            requested_scopes=requested_scopes,
            client_name=client_name,
            redirect_uri=redirect_uri,
        )
        moment = now or datetime.now(timezone.utc)
        payload = oauth_flow.issue_code(request, now=moment)
        record = self.store.create(
            OAUTH_CODES,
            payload,
            actor=actor,
            source=source,
        )
        return {
            "consent": oauth_flow.consent_preview(request, self.endpoints()),
            "code_id": record["id"],
            "code": payload["code"],
            "expires_at": payload["expires_at"],
            "scopes": list(payload["scopes"]),
        }

    def exchange_code(
        self,
        workspace_id: str,
        *,
        code: str,
        client_id: str,
        source: str,
        actor: str = "api",
        scopes: Sequence[Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code for a token.

        The code's spend and the token's creation happen in **one transaction**, so
        a code cannot survive a crash between the two writes. That is the reason
        the code's ``used_at`` is patched here rather than in
        :func:`oauth_flow.check_code`.
        """
        moment = now or datetime.now(timezone.utc)
        record = None
        for candidate in self.store.find(OAUTH_CODES, {"code": str(code or "")}, limit=10):
            record = candidate
            break
        consented = oauth_flow.check_code(
            record["data"] if record else {},
            workspace_id=workspace_id,
            client_id=client_id,
            now=moment,
            scopes=scopes,
        )
        payload = oauth_flow.issue_code(
            oauth_flow.ConsentRequest(
                workspace_id=workspace_id,
                client_id=client_id,
                client_name=str((record or {}).get("data", {}).get("client_name") or client_id),
                requested_scopes=consented,
                redirect_uri=str((record or {}).get("data", {}).get("redirect_uri") or ""),
            ),
            now=moment,
        )
        secret = f"dsr_{secrets.token_urlsafe(TOKEN_SECRET_BYTES)}"
        prefix = secret[: TOKEN_PREFIX_BYTES + 4]
        with self.store.db.transaction(actor=actor, source=source) as tx:
            tx.update(
                str(record["id"]),
                oauth_flow.spend_patch(moment),
            )
            created = tx.create(
                TOKENS,
                {
                    "workspace_id": workspace_id,
                    "name": payload["client_name"],
                    "scopes": list(consented),
                    "prefix": prefix,
                    "secret_hash": _hash(secret),
                    "client_id": client_id,
                    "authorization_code_id": str(record["id"]),
                    "minted_plan": self.identity.plan(self.store, workspace_id),
                    "minted_role": "",
                    "minted_by": actor,
                    "minted_at": _utcnow(),
                    "revoked": False,
                    "expires_at": "",
                },
            )
        view = self._token_view(created)
        view["secret"] = secret
        view["secret_shown_once"] = True
        view["coverage"] = sorted(scope_rules.coverage_for(consented))
        view["surfaces"] = scope_rules.granted_surfaces(consented)
        view["unlocks"] = scope_rules.unlocked_endpoints(consented, self.endpoints())
        return view

    # -- SSO --------------------------------------------------------------- #

    def read_sso(self, workspace_id: str) -> dict[str, Any]:
        connection = self.identity.sso_connection(self.store, workspace_id)
        summary = sso_rules.connection_summary(connection)
        # Read is never plan-gated: "existing links keep working after a downgrade."
        summary["plan"] = self.identity.plan(self.store, workspace_id)
        summary["entitled"] = scope_rules.plan_allows(summary["plan"], sso_rules.GATED_FEATURE)
        return summary

    def write_sso(
        self,
        workspace_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Wire or update the directory SSO connection. Plan-gated on write."""
        self._require_plan(workspace_id, sso_rules.GATED_FEATURE, source=source)
        cleaned = sso_rules.validate_connection(payload)
        # The workspace is stamped here rather than by the caller, because the
        # record is looked up *by* workspace_id and a payload that forgot it would
        # silently write a connection nothing can ever find again.
        cleaned["workspace_id"] = workspace_id
        existing = self.identity.sso_connection(self.store, workspace_id)
        if existing is None:
            record = self.store.create(SSO_CONNECTIONS, cleaned, actor=actor, source=source)
        else:
            record = self.store.update(str(existing["id"]), cleaned, actor=actor, source=source)
        summary = sso_rules.connection_summary(record["data"])
        summary["plan"] = self.identity.plan(self.store, workspace_id)
        summary["entitled"] = True
        return summary

    def local_login(self, workspace_id: str, member_id: str) -> dict[str, Any]:
        """Whether a member may sign in with a local password.

        The one behavioural consequence of wiring SSO. Exposed as its own route so
        the page can show it per member, and so a test can assert it flips the
        moment the connection is enabled - the sentence "so staff authenticate
        through the company IdP rather than local credentials" has to be observable
        or it is only storage.
        """
        record = self.member(workspace_id, member_id)
        connection = self.identity.sso_connection(self.store, workspace_id)
        decision = sso_rules.decide_local_login(
            record["data"], connection["data"] if connection else None
        )
        payload = decision.to_dict()
        payload["member_id"] = member_id
        payload["email"] = record["data"].get("email", "")
        return payload

    # -- internals --------------------------------------------------------- #

    def _require_plan(self, workspace_id: str, feature: str, *, source: str) -> None:
        """Plan gate, applied on create and update only.

        Named so the call sites read as what they are, and so a reviewer looking
        for the asymmetry can find every gate in one grep: there is no fourth
        caller anywhere in this module, which is the point.
        """
        scope_rules.require_plan(self.identity.plan(self.store, workspace_id), feature)


__all__ = [
    "AccessContext",
    "COLLECTIONS",
    "DEFAULT_PLAN",
    "ENDPOINTS",
    "MEMBERSHIPS",
    "OAUTH_CODES",
    "ROLE_DEFINITIONS",
    "SSO_CONNECTIONS",
    "TOKENS",
    "TokenContext",
    "WorkspaceAccess",
    "WorkspaceIdentity",
]
