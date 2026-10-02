"""The domain errors of WF-077, and why each one is a distinct type.

Two features may not map the same error type to a handler, so the split here is
load-bearing rather than decorative. Each type below carries the status code and
the byte-stable ``code`` its handler emits, and the two are the same string in
every response because the research says an error envelope's ``code`` is
"byte-stable" and "Safe to switch on."

The split between the types follows what the *caller* has to do next:

===============================  ======  ==========================================
Error                           Status  What the caller does about it
===============================  ======  ==========================================
``RoleError``                   422     The request contradicts a role rule. Re-read
                                         the member and try a different role.
``RoleForbidden``               403     The actor's role does not permit this. Ask
                                         someone who holds the permission.
``ScopeError``                  422     The scope list is wrong: a wildcard, an
                                         unknown scope, a duplicate. Fix the list.
``TokenError``                  401     The token is absent, unknown, expired or
                                         revoked. Mint a new one.
``RateLimited``                 429     Wait for ``X-RateLimit-Reset``. Do not retry
                                         sooner.
``PlanFeatureError``            403     The plan does not entitle this feature, on
                                         create or update. Upgrade the plan.
===============================  ======  ==========================================

A scope *miss* on a valid token is deliberately **not** an error type here. It is
a ``403 forbidden`` and the research gives its own message: "The token is valid,
but doesn't have the scope the endpoint requires *or* isn't authorized to act on
the team you're addressing." The handler that produces it lives with the scope
enforcement code, because the same 403 covers both halves of that sentence and a
caller cannot tell them apart from the response alone.
"""

from __future__ import annotations


class WorkspaceRoleError(Exception):
    """Base of every refusal in :mod:`dsr.workspace_roles`."""

    #: The HTTP status this refusal becomes.
    status_code = 422
    #: The byte-stable machine-readable code. Never localised, never reworded.
    code = "workspace_role_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message
        self.context: dict[str, object] = dict(context)


class RoleError(WorkspaceRoleError):
    """A role change breaks a rule the research states. 422."""

    status_code = 422
    code = "role_transition_refused"


class RoleForbidden(WorkspaceRoleError):
    """The actor's role does not permit this action. 403."""

    status_code = 403
    code = "role_forbidden"


class ScopeError(WorkspaceRoleError):
    """A requested scope is a wildcard, unknown, or malformed. 422."""

    status_code = 422
    code = "scope_invalid"


class TokenError(WorkspaceRoleError):
    """The token is absent, unknown, expired or revoked. 401."""

    status_code = 401
    code = "token_invalid"


class RateLimited(WorkspaceRoleError):
    """The token's per-minute budget is spent. 429."""

    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(self, message: str = "", *, reset_at: str = "", **context: object) -> None:
        super().__init__(message, **context)
        #: ISO-8601 UTC instant at which the budget refills. The handler copies
        #: it into the ``X-RateLimit-Reset`` header the research names.
        self.reset_at = reset_at


class PlanFeatureError(WorkspaceRoleError):
    """The plan does not entitle this feature on create or update. 403.

    Not on use. The research is explicit: "enabling a gated feature returns ``403
    forbidden_plan_feature`` on create/update, while existing links keep working
    after a downgrade." So this type is raised by create and update paths only.
    """

    status_code = 403
    code = "forbidden_plan_feature"


class MembershipNotFound(WorkspaceRoleError):
    """The membership record does not exist. 404."""

    status_code = 404
    code = "membership_not_found"


class ConsentError(WorkspaceRoleError):
    """An authorization code is unknown, spent, expired, or for another client. 400.

    A 400 rather than a 401 because the code is not the caller's credential: it
    is a one-time artifact of a consent the workspace already granted, so the
    request is malformed rather than unauthenticated. The research does not
    describe the code's lifetime or its reuse rules at all, so both are this
    build's choices - see ``dsr.workspace_roles.inferences``.
    """

    status_code = 400
    code = "authorization_code_invalid"


class SsoError(WorkspaceRoleError):
    """A directory SSO connection is missing, malformed, or contradicts a member. 422."""

    status_code = 422
    code = "sso_configuration_invalid"
