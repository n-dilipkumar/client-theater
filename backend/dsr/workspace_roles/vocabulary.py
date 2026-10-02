"""The researched terms of WF-077, word for word where the research is exact.

Two vocabularies live here and they must not be mixed:

* **Internal roles** - the people side. ``Admin`` / ``Manager`` / ``Member`` /
  ``Collaborator`` plus workspace-defined custom roles, from PandaDoc.
* **Integration scopes** - the machine side. ``<object>.<permission>`` strings
  from Papermark and Seismic, chosen a la carte and with no hierarchy between
  them.

They are separate on purpose and the research is explicit that they are: "Machine
access is separate and strictly scope-gated." A person who is an ``Admin`` in a
workspace holds no integration scope by that fact, and a token holding
``documents.write`` confers nothing on any person. Conflating them is the single
easiest way to build this workflow wrong.

Every ``*_QUOTE`` below is copied from
``docs/research/digital-sales-room-workflows/wf/WF-077.md``. They are served over
HTTP by the feature's ``/vocabulary`` route so a reviewer reads the same sentences
the code was written from, and a test asserts each quote still appears in the
research document, so a quote cannot drift away from its source without a
failure.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# People: internal workspace roles
# --------------------------------------------------------------------------- #

ADMIN = "Admin"
MANAGER = "Manager"
MEMBER = "Member"
COLLABORATOR = "Collaborator"

#: The four built-in role names, in the vendor's own spelling and in the order
#: the research lists them. These are the names a client sends; the store keeps
#: them verbatim so a role read back out of the audit log is the name that was
#: written, not a normalised variant of it.
BUILT_IN_ROLES: tuple[str, ...] = (ADMIN, MANAGER, MEMBER, COLLABORATOR)

ROLE_OWNER = "Owner"
ROLES_INCLUDING_OWNER: tuple[str, ...] = (ROLE_OWNER, *BUILT_IN_ROLES)

#: Seats. The research distinguishes them only in the promotion rule - a Guest
#: promoted to a non-Collaborator role becomes a Full seat - and nowhere else, so
#: there is no other seat behaviour to infer.
SEAT_FULL = "full"
SEAT_GUEST = "guest"
SEATS: tuple[str, ...] = (SEAT_FULL, SEAT_GUEST)

#: Permissions a role can carry. These are this application's names, not a
#: vendor's: the research states that a caller needs to be "an organization
#: admin, a workspace admin, or hold a role with permission to edit member roles"
#: without enumerating the permissions. The set below is therefore the minimum
#: that makes that sentence expressible, and every role's grants are published
#: over HTTP rather than buried in a ``if`` statement.
PERM_MEMBERS_READ = "members.read"
PERM_MEMBERS_WRITE = "members.write"
PERM_ROLES_WRITE = "roles.write"
PERM_TOKENS_READ = "tokens.read"
PERM_TOKENS_WRITE = "tokens.write"
PERM_SSO_READ = "sso.read"
PERM_SSO_WRITE = "sso.write"
PERM_PART11_READ = "part11.read"

PERMISSIONS: tuple[str, ...] = (
    PERM_MEMBERS_READ,
    PERM_MEMBERS_WRITE,
    PERM_ROLES_WRITE,
    PERM_TOKENS_READ,
    PERM_TOKENS_WRITE,
    PERM_SSO_READ,
    PERM_SSO_WRITE,
    PERM_PART11_READ,
)

#: What each built-in role grants. ``Admin`` is not special-cased anywhere in
#: the rules: it is simply the role that carries every permission, which is what
#: makes "the last admin's role cannot be changed" a statement about the
#: permission set rather than about a name.
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ADMIN: PERMISSIONS,
    MANAGER: (
        PERM_MEMBERS_READ,
        PERM_MEMBERS_WRITE,
        PERM_ROLES_WRITE,
        PERM_TOKENS_READ,
        PERM_SSO_READ,
        # Deliberately NOT part11.read. A Manager can change roles but may not read
        # who changed them: being able to do the thing and being able to audit the
        # thing are different powers, and the vendor draws that line for its audit
        # endpoint. Only Admin - and a custom role granting every permission -
        # holds it.
    ),
    MEMBER: (PERM_MEMBERS_READ,),
    COLLABORATOR: (PERM_MEMBERS_READ,),
}

# --------------------------------------------------------------------------- #
# Machine: integration scopes
# --------------------------------------------------------------------------- #

#: The two verbs. Seismic: "The permission level is either **view** for read-only
#: access, or **manage** for read/write access." Papermark uses ``.read`` and
#: ``.write``. Both spellings are accepted on the way in and the canonical
#: ``<object>.<verb>`` form is what is stored.
VERB_READ = "read"
VERB_WRITE = "write"
VERBS: tuple[str, ...] = (VERB_READ, VERB_WRITE)

#: The permission-level synonyms each vendor publishes, mapped onto this
#: application's two verbs. ``view`` is read-only and ``manage`` is read/write,
#: which is the mapping Seismic states outright.
SEISMIC_PERMISSION_LEVELS: dict[str, str] = {"view": VERB_READ, "manage": VERB_WRITE}

#: The a-la-carte scope catalogue. Each entry names the endpoints it unlocks,
#: because the research records that the vendor's own surface does:
#: "The dashboard's token-creation UI shows which endpoints each scope unlocks".
#: An integrator choosing a token is choosing a set of endpoints, and a catalogue
#: that cannot answer that question is not the catalogue the vendor describes.
SCOPES: tuple[dict[str, Any], ...] = (
    {
        "scope": "documents.read",
        "label": "Read documents",
        "unlocks": [
            "list and read documents in a workspace",
            "read a document's metadata",
        ],
    },
    {
        "scope": "documents.write",
        "label": "Write documents",
        "unlocks": [
            "upload, rename, move and delete documents",
        ],
    },
    {
        "scope": "links.write",
        "label": "Write links",
        "unlocks": [
            "create and revoke tracked links",
        ],
    },
    {
        "scope": "links.read",
        "label": "Read links",
        "unlocks": [
            "list tracked links and their click counts",
        ],
    },
    {
        "scope": "analytics.read",
        "label": "Read analytics",
        "unlocks": [
            "read view counts, dwell time and drop-off",
        ],
    },
    {
        "scope": "members.read",
        "label": "Read members",
        "unlocks": [
            "list workspace members and their roles",
        ],
    },
    {
        "scope": "members.write",
        "label": "Write members",
        "unlocks": [
            "change a member's internal role",
            "change a member's seat",
        ],
    },
    {
        "scope": "tokens.read",
        "label": "Read tokens",
        "unlocks": [
            "list API tokens and their scopes",
        ],
    },
    {
        "scope": "tokens.write",
        "label": "Write tokens",
        "unlocks": [
            "create and revoke API tokens",
            "exchange an authorization code for a token",
        ],
    },
    {
        "scope": "sso.read",
        "label": "Read SSO configuration",
        "unlocks": ["read the directory SSO connection"],
    },
    {
        "scope": "sso.write",
        "label": "Write SSO configuration",
        "unlocks": ["create, update and disable the directory SSO connection"],
    },
)

#: The vendor's two forward-compatible coarse grants. Papermark: "``apis.read`` /
#: ``apis.all`` - Forward-compatible coarse grants."
#:
#: These are *named* scopes, not wildcards, and the difference is the whole point:
#: the same page says "Don't request ``*`` or wildcards; they're not supported."
#: So ``apis.all`` grants the API-management endpoints it is catalogued against and
#: nothing else. It is not a synonym for every scope, and
#: :func:`dsr.workspace_roles.scopes.token_has_scope` treats it as the literal
#: string it is.
COARSE_SCOPES: tuple[str, ...] = ("apis.read", "apis.all")

#: What each scope grants *in this product*, as data.
#:
#: This is deliberately separate from the catalogue's ``unlocks`` prose and from
#: :data:`dsr.workspace_roles.engine.ENDPOINTS`, because it answers a different
#: question. The engine's table is **this feature's own routes** and the scope each
#: requires. This table is the **product surfaces** a scope grants: the document
#: library, the tracked links, the analytics reads.
#:
#: Those surfaces belong to other features and their routes are not this feature's
#: to declare - but the research requires the token-creation surface to answer
#: "which endpoints each scope unlocks", and the research's own examples are
#: ``links.write`` and ``documents.read`` + ``analytics.read``, none of which this
#: feature serves. Inventing routes for another feature's API would be a claim this
#: branch cannot keep, so the grant is described in the vocabulary instead and the
#: page shows both: the surfaces a scope reaches, and the routes of *this* API it
#: unlocks.
SCOPE_TARGETS: dict[str, tuple[str, ...]] = {
    "documents.read": ("document library: list and read", "document metadata: read"),
    "documents.write": ("document library: upload, rename, move, delete",),
    "links.write": ("tracked links: create and revoke",),
    "links.read": ("tracked links: list and click counts",),
    "analytics.read": ("view counts, dwell time, drop-off",),
    "members.read": ("workspace members: list and read",),
    "members.write": ("workspace members: change role and seat",),
    "tokens.read": ("API tokens: list and inspect",),
    "tokens.write": ("API tokens: mint and revoke", "OAuth: consent screen and code exchange"),
    "sso.read": ("directory SSO connection: read",),
    "sso.write": ("directory SSO connection: wire, update, disable",),
}

#: What the coarse grants actually cover, which is what makes them coarse rather
#: than universal. The values are *scope* names, so a coarse grant is understood as
#: standing for a named set of scopes rather than for the permissions behind them.
#:
#: Note that permission names and scope names are spelled the same way
#: (``members.read`` is both). That is a convenience, not a shared grant: they are
#: two namespaces in two different records, and nothing here lets one satisfy a
#: check that asks for the other. See ``inferences.role-spelling-is-preserved`` and
#: ``WorkspaceAccess.authorise``, which deliberately calls a *different* function
#: for each of the two.
COARSE_COVERAGE: dict[str, tuple[str, ...]] = {
    "apis.read": ("tokens.read",),
    "apis.all": ("tokens.read", "tokens.write"),
}

#: Seismic's scope spelling, carried because the research cites it and because
#: this application accepts it: ``seismic.<object>.<view|manage>``.
SEISMIC_SCOPE_EXAMPLES: tuple[str, ...] = (
    "seismic.library.view",
    "seismic.library.manage",
    "seismic.delivery",
)

# --------------------------------------------------------------------------- #
# Plans and gating
# --------------------------------------------------------------------------- #

PLAN_STARTER = "starter"
PLAN_BUSINESS = "business"
PLAN_ENTERPRISE = "enterprise"
PLANS: tuple[str, ...] = (PLAN_STARTER, PLAN_BUSINESS, PLAN_ENTERPRISE)

#: What each plan entitles. Gating is enforced on create and update only, because
#: the research is explicit that it is not enforced on use: "enabling a gated
#: feature returns ``403 forbidden_plan_feature`` on create/update, while existing
#: links keep working after a downgrade."
GATED_FEATURES: dict[str, str] = {
    "tokens": PLAN_BUSINESS,
    "sso": PLAN_ENTERPRISE,
    "part11": PLAN_ENTERPRISE,
}

PLAN_RANK: dict[str, int] = {name: index for index, name in enumerate(PLANS)}

# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #

#: A token's per-minute request budget. The research documents the response, not
#: the number: "``429 rate_limit_exceeded`` with ``X-RateLimit-Reset``" and "Your
#: token has exceeded its per-minute budget". 600/minute is this build's choice.
DEFAULT_RATE_LIMIT_PER_MINUTE = 600

# --------------------------------------------------------------------------- #
# Quoted evidence
# --------------------------------------------------------------------------- #

BUILT_IN_ROLE_QUOTE = (
    "The `role` field accepts either a built-in role name "
    "(`Admin`, `Manager`, `Member`, `Collaborator`) or the name of a custom role"
)

CALLER_QUOTE = (
    "You must be an organization admin, a workspace admin, or hold a role with "
    "permission to edit member roles"
)

OWNER_IMMUTABLE_QUOTE = "The role of the workspace owner cannot be changed."

LAST_ADMIN_QUOTE = (
    "The role of the last member with admin privileges in the workspace cannot be changed."
)

SEAT_UPGRADE_QUOTE = (
    "a `Guest` promoted to a non-`Collaborator` role is auto-upgraded to a Full seat"
)

NO_HIERARCHY_QUOTE = (
    "`documents.write` does **not** imply `documents.read`. Each scope is independent\u2026 "
    "it lets you mint write-only tokens for systems that push data in but shouldn't "
    "be able to read it back out (e.g., an ingestion worker)."
)

FORBIDDEN_QUOTE = (
    "**HTTP 403.** The token is valid, but doesn't have the scope the endpoint "
    "requires *or* isn't authorized to act on the team you're addressing."
)

WILDCARD_QUOTE = (
    "Don't request `*` or wildcards; they're not supported. The token endpoint will "
    "reject unknown scopes."
)

COARSE_QUOTE = "`apis.read` / `apis.all` \u2014 Forward-compatible coarse grants."

SCOPES_UNLOCK_QUOTE = "The dashboard's token-creation UI shows which endpoints each scope unlocks"

SCOPES_DO_NOT_OVERRIDE_QUOTE = (
    "Scopes are not intended to override a users defined permissions. For example, a "
    "business user cannot upload content to content manager when using an auth token "
    "with `seismic.library.manage` scope."
)

SEISMIC_FORMAT_QUOTE = "Scopes are defined in the following format: `seismic.object.permission`"

PART11_QUOTE = "This endpoint is accessible to authorized workspace administrators only."

PLAN_GATE_QUOTE = (
    "Plan entitlement is enforced independently of scopes: enabling a gated feature "
    "returns `403 forbidden_plan_feature` on create/update, while existing links keep "
    "working after a downgrade."
)

RATE_LIMIT_QUOTE = "Your token has exceeded its per-minute budget"

NO_CACHING_QUOTE = "Role/scope checks are enforced on every request (no caching window documented)."

ADDITIONAL_PROPERTIES_QUOTE = "generated SDKs should already reject unknown fields client-side"

ERROR_CODE_STABLE_QUOTE = (
    'an error envelope whose `code` is byte-stable \u2014 "Safe to switch on."'
)

QUOTES: tuple[dict[str, str], ...] = (
    {"id": "built_in_roles", "source": "pandadoc", "quote": BUILT_IN_ROLE_QUOTE},
    {"id": "caller_may_edit_roles", "source": "pandadoc", "quote": CALLER_QUOTE},
    {"id": "owner_role_immutable", "source": "pandadoc", "quote": OWNER_IMMUTABLE_QUOTE},
    {"id": "last_admin_role_immutable", "source": "pandadoc", "quote": LAST_ADMIN_QUOTE},
    {"id": "seat_upgrade", "source": "pandadoc", "quote": SEAT_UPGRADE_QUOTE},
    {"id": "no_implicit_hierarchy", "source": "papermark", "quote": NO_HIERARCHY_QUOTE},
    {"id": "forbidden_scope_miss", "source": "papermark", "quote": FORBIDDEN_QUOTE},
    {"id": "no_wildcards", "source": "papermark", "quote": WILDCARD_QUOTE},
    {"id": "coarse_grants", "source": "papermark", "quote": COARSE_QUOTE},
    {"id": "scopes_unlock_endpoints", "source": "papermark", "quote": SCOPES_UNLOCK_QUOTE},
    {"id": "error_code_stable", "source": "papermark", "quote": ERROR_CODE_STABLE_QUOTE},
    {
        "id": "additional_properties_false",
        "source": "papermark",
        "quote": ADDITIONAL_PROPERTIES_QUOTE,
    },
    {
        "id": "scopes_do_not_override_user",
        "source": "seismic",
        "quote": SCOPES_DO_NOT_OVERRIDE_QUOTE,
    },
    {"id": "seismic_scope_format", "source": "seismic", "quote": SEISMIC_FORMAT_QUOTE},
    {"id": "part11_role_gate", "source": "pandadoc", "quote": PART11_QUOTE},
    {"id": "plan_gate_independent_of_scopes", "source": "papermark", "quote": PLAN_GATE_QUOTE},
    {"id": "rate_limit_reset_header", "source": "papermark", "quote": RATE_LIMIT_QUOTE},
    {"id": "no_caching_window", "source": "papermark", "quote": NO_CACHING_QUOTE},
)


def published_vocabulary() -> dict[str, Any]:
    """Everything this module knows, as one JSON-serialisable payload."""
    return {
        "internal_roles": {
            "built_in": list(BUILT_IN_ROLES),
            "including_owner": list(ROLES_INCLUDING_OWNER),
            "owner": ROLE_OWNER,
            "permissions": list(PERMISSIONS),
            "role_permissions": {role: list(perms) for role, perms in ROLE_PERMISSIONS.items()},
            "seats": list(SEATS),
            "seat_upgrade_rule": SEAT_UPGRADE_QUOTE,
        },
        "integration_scopes": {
            "catalogue": [dict(entry) for entry in SCOPES],
            "verbs": list(VERBS),
            "coarse": list(COARSE_SCOPES),
            "coarse_coverage": {name: list(items) for name, items in COARSE_COVERAGE.items()},
            "seismic_examples": list(SEISMIC_SCOPE_EXAMPLES),
            "seismic_permission_levels": dict(SEISMIC_PERMISSION_LEVELS),
            "no_implicit_hierarchy": NO_HIERARCHY_QUOTE,
            "no_wildcards": WILDCARD_QUOTE,
            "unlock_rule": SCOPES_UNLOCK_QUOTE,
        },
        "plans": {
            "levels": list(PLANS),
            "gated_features": dict(GATED_FEATURES),
            "gate_rule": PLAN_GATE_QUOTE,
        },
        "throttling": {
            "per_minute_default": DEFAULT_RATE_LIMIT_PER_MINUTE,
            "exceeded_message": RATE_LIMIT_QUOTE,
            "reset_header": "X-RateLimit-Reset",
        },
        "sourced_quotes": [dict(entry) for entry in QUOTES],
    }
