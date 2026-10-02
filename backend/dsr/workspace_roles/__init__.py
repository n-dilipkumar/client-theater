"""WF-077: manage internal workspace roles and least-privilege integration scopes.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-077.md``, which is the
specification. Two vocabularies, kept apart on purpose because the research keeps
them apart: **internal roles** are about people (``Admin`` / ``Manager`` / ``Member``
/ ``Collaborator``, plus workspace-defined custom roles), and **integration scopes**
are about machines (``links.write`` only, or ``documents.read`` + ``analytics.read``).
A person who is an ``Admin`` holds no integration scope by that fact, and a token
holding ``documents.write`` confers nothing on any person.

Module map, in dependency order:

``vocabulary``
    The researched terms, the role and scope catalogues, and every sourced quote.
``errors``
    Seven domain error types, one per distinct answer, each carrying its status
    and its byte-stable code.
``rules``
    The role guard rails as pure functions: owner, last admin, seat auto-upgrade,
    and the caller rule.
``scopes``
    The machine half. No implicit hierarchy, no wildcards, coarse grants that are
    not wildcards, the researched 403, the plan gate, and the rate limiter.
``oauth``
    The consent screen and the code-to-token exchange.
``sso``
    Directory-driven SSO, and the one rule it changes about signing in.
``engine``
    The flow over the audited store. The only module that reads or writes.
``inferences``
    Every judgement call the research left open, named and served over HTTP.

The rule most likely to be implemented backwards
------------------------------------------------
There is **no implicit hierarchy**. ``documents.write`` does not imply
``documents.read``. That is not a detail of the vocabulary; it is the reason the
scope catalogue exists - "it lets you mint write-only tokens for systems that push
data in but shouldn't be able to read it back out (e.g., an ingestion worker)". An
implementation that expands a scope into its siblings produces tokens that look
least-privilege on the creation screen and are not, which is worse than no
scoping at all because it is trusted. So
:func:`dsr.workspace_roles.scopes.token_has_scope` is set membership and nothing
else, and :mod:`dsr.workspace_roles.rules` has no equivalent shortcut because a
human role *is* a set of permissions while a scope is not.

The module name
---------------
``workspace_roles``, not ``roles``. ``dsr.roles`` and ``dsr.roles_api`` belong to
WF-004, which is live on ``main``; they are about *granting a buyer access to a
room*. This package is about *staff authority inside a workspace*, and
``dsr.permissions`` belongs to WF-003. Three modules in this repository now hold
role and permission vocabulary for three different features, and the names keep
them apart. See ``backend/tests/test_wf004.py`` for the collision that produced
the first of those three.
"""

from __future__ import annotations

from dsr.workspace_roles import engine, errors, inferences, oauth, rules, scopes, sso, vocabulary
from dsr.workspace_roles.engine import (
    COLLECTIONS,
    DEFAULT_PLAN,
    ENDPOINTS,
    MEMBERSHIPS,
    OAUTH_CODES,
    ROLE_DEFINITIONS,
    SSO_CONNECTIONS,
    TOKENS,
    AccessContext,
    TokenContext,
    WorkspaceAccess,
    WorkspaceIdentity,
)
from dsr.workspace_roles.errors import (
    ConsentError,
    MembershipNotFound,
    PlanFeatureError,
    RateLimited,
    RoleError,
    RoleForbidden,
    ScopeError,
    SsoError,
    TokenError,
    WorkspaceRoleError,
)
from dsr.workspace_roles.inferences import INFERENCES, describe_inferences
from dsr.workspace_roles.oauth import ConsentRequest, published_oauth
from dsr.workspace_roles.rules import (
    ACCEPTED,
    REFUSALS,
    RoleDecision,
    decide_role_change,
    display_role,
    has_permission,
    is_admin,
    is_built_in,
    normalise_seat,
    outcome_table,
    permissions_for,
    require_permission,
    role_key,
)
from dsr.workspace_roles.scopes import (
    FORBIDDEN_DETAIL,
    KNOWN_SCOPES,
    Forbidden,
    RateLimiter,
    authorise,
    coverage_for,
    granted_surfaces,
    looks_like_wildcard,
    missing_scopes,
    normalise_scope,
    normalise_scopes,
    plan_allows,
    rate_limit_headers,
    require_plan,
    scopes_that_unlock,
    token_has_scope,
    unlocked_endpoints,
)
from dsr.workspace_roles.sso import published_sso
from dsr.workspace_roles.vocabulary import published_vocabulary

__all__ = [
    "ACCEPTED",
    "AccessContext",
    "COLLECTIONS",
    "ConsentError",
    "ConsentRequest",
    "DEFAULT_PLAN",
    "ENDPOINTS",
    "FORBIDDEN_DETAIL",
    "INFERENCES",
    "KNOWN_SCOPES",
    "MEMBERSHIPS",
    "MembershipNotFound",
    "OAUTH_CODES",
    "REFUSALS",
    "ROLE_DEFINITIONS",
    "RateLimited",
    "RateLimiter",
    "RoleDecision",
    "RoleError",
    "RoleForbidden",
    "SSO_CONNECTIONS",
    "ScopeError",
    "SsoError",
    "TOKENS",
    "TokenContext",
    "TokenError",
    "WorkspaceAccess",
    "WorkspaceIdentity",
    "WorkspaceRoleError",
    "authorise",
    "coverage_for",
    "decide_role_change",
    "describe_inferences",
    "display_role",
    "engine",
    "errors",
    "Forbidden",
    "granted_surfaces",
    "has_permission",
    "inferences",
    "is_admin",
    "is_built_in",
    "looks_like_wildcard",
    "missing_scopes",
    "normalise_scope",
    "normalise_scopes",
    "normalise_seat",
    "oauth",
    "outcome_table",
    "permissions_for",
    "PlanFeatureError",
    "plan_allows",
    "published_oauth",
    "published_sso",
    "published_vocabulary",
    "rate_limit_headers",
    "require_permission",
    "require_plan",
    "role_key",
    "rules",
    "scopes",
    "scopes_that_unlock",
    "sso",
    "token_has_scope",
    "unlocked_endpoints",
    "vocabulary",
]
