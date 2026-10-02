"""Least-privilege integration scopes: the machine half of WF-077.

This is where the research's sharpest rule lives, so it is implemented literally
and tested from both sides.

**There is no implicit hierarchy.** ``documents.write`` does **not** imply
``documents.read``. "Each scope is independent ... it lets you mint write-only
tokens for systems that push data in but shouldn't be able to read it back out
(e.g., an ingestion worker)." The consequence that matters is that a token
holding *every* scope except one read scope still cannot read. That is why
:func:`token_has_scope` is set membership and nothing else - no prefix expansion,
no ``*`` handling, no "write implies read" special case, and no fallback from a
coarse grant to the fine-grained ones.

**Wildcards are refused.** "Don't request ``*`` or wildcards; they're not supported.
The token endpoint will reject unknown scopes." So ``*`` and ``documents.*`` are
refused, and so is anything else that is not in the catalogue. Both are
:class:`~dsr.workspace_roles.errors.ScopeError`, and both are refused *at
creation*, which is where the research puts the rejection.

**Coarse grants are named, not wildcards.** ``apis.read`` and ``apis.all`` are
"Forward-compatible coarse grants" on the same page that forbids wildcards. They
grant exactly what :data:`vocabulary.COARSE_COVERAGE` says and nothing more, so
``apis.all`` does not unlock ``documents.write``.

**A scope is necessary but not sufficient.** Seismic states the complementary
rule: "Scopes are not intended to override a users defined permissions. For
example, a business user cannot upload content to content manager when using an
auth token with ``seismic.library.manage`` scope." So when a request presents
both a token and a member, the member's own permissions are checked *as well*.
A token never widens what a person may do; :func:`authorise` returns both
answers so a caller can be refused for the reason that actually applies.

**The 403 has one message for two causes.** "The token is valid, but doesn't have
the scope the endpoint requires *or* isn't authorized to act on the team you're
addressing." Papermark cannot tell them apart either, so this module does not
pretend to; it reports which one it found in a structured field while the human
message stays the researched sentence.

**Throttling answers with a reset signal.** ``429`` with ``X-RateLimit-Reset`` and
the message "Your token has exceeded its per-minute budget". See
:class:`RateLimiter`.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.workspace_roles import vocabulary as vocab
from dsr.workspace_roles.errors import PlanFeatureError, RateLimited, ScopeError, WorkspaceRoleError

#: The canonical spelling of every scope this application issues, plus the two
#: coarse grants. A set, because the membership test is the whole contract.
KNOWN_SCOPES: frozenset[str] = frozenset(
    [str(entry["scope"]) for entry in vocab.SCOPES] + list(vocab.COARSE_SCOPES)
)

#: Anything containing ``*`` is a wildcard. Matched on the raw string rather than
#: parsed, because the rule is "don't request ``*`` or wildcards" - a request for
#: ``documents.*`` is a wildcard request whether or not it parses.
WILDCARD = re.compile(r"[*]")

#: ``<object>.<permission>``. The research cites this format twice: Papermark's
#: a-la-carte examples (``links.write``, ``documents.read``, ``analytics.read``)
#: and Seismic's "``seismic.object.permission``".
SCOPE_SHAPE = re.compile(r"^[a-z0-9_]+(?:-[a-z0-9_]+)*\.[a-z]+$")

#: Seismic publishes permission levels ``view`` and ``manage``; Papermark publishes
#: ``read`` and ``write``. Both are accepted on input and canonicalised to the
#: ``<object>.<verb>`` form this application stores.
_ALIAS_VERBS: dict[str, str] = {
    "read": vocab.VERB_READ,
    "view": vocab.VERB_READ,
    "write": vocab.VERB_WRITE,
    "manage": vocab.VERB_WRITE,
}


def looks_like_wildcard(scope: Any) -> bool:
    return bool(WILDCARD.search(str(scope or "")))


def normalise_scope(raw: Any) -> str:
    """Canonicalise one scope string, or refuse it.

    The canonical form is ``<object>.<verb>`` with a lower-case verb drawn from
    :data:`vocabulary.VERBS`. Seismic's ``seismic.library.manage`` canonicalises to
    ``seismic.library.write``, because "``manage`` for read/write access" and
    ``write`` are the same level and one spelling per level is what keeps the
    membership test unambiguous.

    Refuses, in this order, because each refusal names a different mistake:
    a wildcard, a malformed string, an unknown scope.
    """
    text = str(raw or "").strip()
    if not text:
        raise ScopeError("a scope name is required", scope=raw)
    if looks_like_wildcard(text):
        raise ScopeError(
            "wildcards are not supported: scopes are chosen a la carte and '*' is never a "
            f"valid scope (asked for {text!r})",
            scope=text,
            rule=vocab.WILDCARD_QUOTE,
        )

    lowered = text.lower()
    if "." not in lowered:
        raise ScopeError(
            f"scope {text!r} must be <object>.<permission>, for example documents.read",
            scope=text,
            rule=vocab.SEISMIC_FORMAT_QUOTE,
        )

    obj, _, verb = lowered.rpartition(".")

    # A coarse grant is a *named* grant, not a permission level, so its last
    # segment is the grant's own name and is returned verbatim. This has to come
    # before the verb check, because `all` is not a permission level and routing
    # it through `_ALIAS_VERBS` would refuse a scope the catalogue carries and
    # `coverage_for` already knows how to expand.
    #
    # It stays a literal string all the same, which is the whole point: accepting
    # it here grants exactly `apis.all` and nothing it was not individually given.
    if lowered in vocab.COARSE_SCOPES:
        return lowered

    canonical_verb = _ALIAS_VERBS.get(verb)
    if canonical_verb is None:
        raise ScopeError(
            f"scope {text!r} has permission {verb!r}; use one of "
            + ", ".join(sorted(set(_ALIAS_VERBS))),
            scope=text,
            rule=vocab.SEISMIC_FORMAT_QUOTE,
        )
    candidate = f"{obj}.{canonical_verb}"
    if candidate not in KNOWN_SCOPES:
        raise ScopeError(
            f"unknown scope {candidate!r}; the token endpoint rejects unknown scopes",
            scope=candidate,
            known=sorted(KNOWN_SCOPES),
            rule=vocab.WILDCARD_QUOTE,
        )
    return candidate


def normalise_scopes(requested: Iterable[Any]) -> tuple[str, ...]:
    """Canonicalise a requested scope list, preserving order and dropping duplicates.

    A duplicate is dropped rather than refused: asking twice for a scope you
    already hold is harmless, and refusing it would make a client's retry loop
    fail on the second attempt for no security gain. The set of scopes is what
    matters and it is unchanged.
    """
    seen: dict[str, None] = {}
    for raw in requested or ():
        seen.setdefault(normalise_scope(raw), None)
    return tuple(seen)


def token_has_scope(granted: Iterable[str] | None, required: str) -> bool:
    """Whether a token's granted set contains ``required``. Nothing else.

    This is the no-implicit-hierarchy rule in its entirety, and it is
    deliberately the smallest correct function in the module:

    * **No hierarchy.** ``documents.write`` is not in ``{"documents.write"}``'s
      expansion, because there is no expansion. A token granted write-only cannot
      read. This is the sharpest rule in the research and the only way to satisfy
      it is to have no expansion mechanism at all: nothing here derives a grant
      from the *shape* of a scope name.
    * **No wildcards.** A stored scope containing ``*`` grants nothing at all,
      because creation refuses it, and a scope that reached the store some other
      way must not become a grant by being present.
    * **A coarse grant means exactly what its catalogue entry says, and no more.**
      ``apis.all`` stands for ``tokens.read`` and ``tokens.write``, because that is
      the mapping the catalogue publishes, and it stands for nothing else. This is
      not a hierarchy and not a wildcard: it is a fixed, enumerated, reviewable
      table (:data:`vocabulary.COARSE_COVERAGE`), it can only ever *add* scopes the
      catalogue already names, and no member of it is a prefix or a parent of
      anything.

      The earlier version of this function deliberately refused that mapping, on the
      reasoning that letting it widen a check is implicit hierarchy. That reasoning
      was wrong in a way worth recording: it made ``apis.all`` mintable and inert.
      A grant that unlocks nothing is not least privilege, it is a no-op that looks
      like a grant, and the honest options were to drop the scope from the catalogue
      or to honour its published coverage. The catalogue is the vendor's, so the
      scope stays and the coverage is honoured - and it stays non-wildcard, which is
      the property the research actually names.

    Normalisation happens at the boundary (:func:`normalise_scope`), so a granted
    set read back out of the store is already canonical and this stays a set
    lookup.
    """
    if not granted:
        return False
    held = {str(scope) for scope in granted if not looks_like_wildcard(scope)}
    wanted = str(required)
    if wanted in held:
        return True
    return any(wanted in vocab.COARSE_COVERAGE.get(scope, ()) for scope in held)


def missing_scopes(granted: Iterable[str] | None, required: Iterable[str]) -> tuple[str, ...]:
    """Which of an endpoint's required scopes the token is missing.

    Several, not one: an endpoint may declare that it needs ``documents.read``
    *and* ``analytics.read``, and a caller asking "what do I need" deserves the
    whole list rather than the first miss.
    """
    return tuple(scope for scope in required if not token_has_scope(granted, scope))


def coverage_for(scopes: Iterable[str] | None) -> frozenset[str]:
    """The scopes a coarse grant stands for.

    This is the whole of the coarse-grant mechanism: one table,
    :data:`vocabulary.COARSE_COVERAGE`, mapping each named coarse grant to the
    scopes it covers. It is read both by :func:`token_has_scope` (to decide) and by
    the descriptive surfaces (to explain), from the same source, so the list a
    developer reads while choosing scopes is the list that is enforced.

    ``apis.all`` here means the two token-management scopes and nothing else. It is
    not every scope, and it is not a prefix of anything.
    """
    covered: set[str] = set()
    for scope in scopes or ():
        name = str(scope)
        covered |= set(vocab.COARSE_COVERAGE.get(name, ()))
    return frozenset(covered)


def scopes_that_unlock(
    endpoint: Mapping[str, Any], known: Iterable[str] | None = None
) -> list[str]:
    """Every single scope, and every coarse grant, that reaches one endpoint.

    A coarse grant reaches an endpoint when the scopes it stands for satisfy that
    endpoint's requirements. So ``apis.all`` reaches the token-management endpoints
    and not the document ones - which is the claim
    ``inferences.coarse-grants-are-not-wildcards`` makes, expressed as code rather
    than as prose.
    """
    required = tuple(endpoint.get("scopes") or ())
    if not required:
        return []
    candidates = list(known if known is not None else sorted(KNOWN_SCOPES))
    reached: list[str] = []
    for candidate in candidates:
        if candidate in vocab.COARSE_COVERAGE:
            if not missing_scopes(coverage_for([candidate]), required):
                reached.append(candidate)
        elif not missing_scopes([candidate], required):
            reached.append(candidate)
    return reached


def granted_surfaces(scopes: Iterable[str] | None) -> dict[str, list[str]]:
    """What a scope set reaches in the product, keyed by scope.

    This is the half of "which endpoints each scope unlocks" that a scope catalogue
    can answer about the whole product rather than about one feature's own routes.
    A coarse grant contributes nothing here, because it stands for other scopes
    rather than for a surface of its own - which is exactly why ``apis.all`` can be
    granted without appearing to unlock the document library.
    """
    return {
        str(scope): list(vocab.SCOPE_TARGETS[str(scope)])
        for scope in (scopes or ())
        if str(scope) in vocab.SCOPE_TARGETS
    }


def unlocked_endpoints(
    scopes: Iterable[str] | None, endpoints: Sequence[Mapping[str, Any]]
) -> list[dict]:
    """The endpoints a scope set reaches, for the token-creation surface.

    Purely descriptive. It reads the same per-endpoint declarations the request
    path enforces, so the list a developer reads while choosing scopes is the list
    that will actually be enforced, and the two cannot drift.

    A coarse grant is expanded through the same :data:`vocabulary.COARSE_COVERAGE`
    table the request path uses, so this description and the enforcement cannot
    drift: if the list here said an endpoint was reachable and the request path
    disagreed, one of the two would be lying, and they read the same table.
    """
    granted = sorted(coverage_for(tuple(scopes or ())) | set(scopes or ()))
    reached: list[dict] = []
    for endpoint in endpoints:
        required = tuple(endpoint.get("scopes") or ())
        if required and not missing_scopes(granted, required):
            reached.append(
                {
                    "method": endpoint.get("method"),
                    "path": endpoint.get("path"),
                    "name": endpoint.get("name", ""),
                    "scopes": list(required),
                }
            )
    return reached


# --------------------------------------------------------------------------- #
# The 403
# --------------------------------------------------------------------------- #

FORBIDDEN_DETAIL = (
    "The token is valid, but doesn't have the scope the endpoint requires *or* isn't "
    "authorized to act on the team you're addressing."
)


class Forbidden(WorkspaceRoleError):
    """A scope-gated refusal, and which of the research's two causes it was.

    An exception like every other refusal here, so the host can map it without
    special-casing it. It carries the vendor's message *and* the cause this build
    actually found, because the two are different things: the message is what a
    client written against the vendor's documentation matches on, and the cause is
    what makes the refusal debuggable.

    The status and the code are the researched ones - ``403`` and ``forbidden`` -
    and this is one of the three codes the research quotes verbatim, so a client
    written against Papermark's documentation switches on exactly this string.
    """

    status_code = 403
    code = "forbidden"

    def __init__(
        self,
        *,
        scope_missing: Iterable[str] = (),
        member_forbidden: str = "",
        detail: str = FORBIDDEN_DETAIL,
        **context: Any,
    ) -> None:
        super().__init__(detail, **context)
        #: The researched sentence, unchanged.
        self.detail = detail
        #: Which required scopes the token was missing. Empty when the refusal was
        #: the other half of the sentence.
        self.scope_missing = tuple(scope_missing)
        #: Why the member's own permissions did not allow it, or "".
        self.member_forbidden = str(member_forbidden or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "code": self.code,
            "status": self.status_code,
            "detail": self.detail,
            "missing_scopes": list(self.scope_missing),
            "member_forbidden": self.member_forbidden,
        }


def authorise(
    *,
    granted: Sequence[str] | None,
    required: Sequence[str],
    member_permission: str = "",
    member_allowed: bool | None = None,
) -> None:
    """Refuse unless both the token's scopes *and* the member's permissions allow it.

    ``member_allowed`` is ``None`` for a pure machine call, where no member is
    present and there is nothing to override - the Seismic rule is about a *user's*
    permissions, and a token with no user behind it has none to override. It is
    ``True``/``False`` when a member is present, and then a scope grant cannot
    rescue a member who lacks the permission: "Scopes are not intended to override
    a users defined permissions."

    Raises :class:`Forbidden` unless both the token's scopes *and* the member's
    permissions allow it.

    ``granted=None`` means **no token was presented**, which is a different
    situation from a token that was presented and lacks a scope. The research is
    explicit that the two are separate subjects - "Machine access is separate and
    strictly scope-gated" - so a request carrying only a member's role is held to
    that role and no scope check runs at all. Applying the scope check to a
    human-only request would mean every person in the product needs an integration
    token to use their own permissions, which is the opposite of the sentence.

    ``member_allowed`` is ``None`` for a pure machine call, where no member is
    present and there is nothing to override - the Seismic rule is about a
    *user's* permissions, and a token with no user behind it has none to override.
    It is ``True``/``False`` when a member is present, and then a scope grant
    cannot rescue a member who lacks the permission: "Scopes are not intended to
    override a users defined permissions."
    """
    # Only a presented token is scope-checked. `None` is "no token", not
    # "a token with no scopes".
    miss = () if granted is None else missing_scopes(granted, required)
    if granted is None and member_allowed is None:
        # Neither credential was presented at all. This is its own case and it is
        # the case that must never fall through: a request with no token and no
        # member has satisfied neither check, and treating "nothing presented" as
        # "nothing to check" is how an endpoint ends up open to the world.
        raise Forbidden(member_forbidden="no member or token was presented")
    member_forbidden = ""
    if member_allowed is False:
        member_forbidden = member_permission or "the member's own permissions"
    if miss or member_forbidden:
        raise Forbidden(scope_missing=miss, member_forbidden=member_forbidden)


# --------------------------------------------------------------------------- #
# Plan gating
# --------------------------------------------------------------------------- #


def plan_allows(plan: str, feature: str) -> bool:
    """Whether ``plan`` entitles ``feature``.

    Only ever called from a create or update path. The research is explicit that
    entitlement is "enforced independently of scopes" and that after a downgrade
    "existing links keep working" - so this function is not consulted when an
    already-granted thing is *used*, and a token minted before the downgrade
    keeps working. That asymmetry is the rule, and :func:`require_plan` is the
    only caller.
    """
    required = vocab.GATED_FEATURES.get(feature)
    if required is None:
        return True
    return vocab.PLAN_RANK.get(str(plan or ""), -1) >= vocab.PLAN_RANK[required]


def require_plan(plan: str, feature: str) -> None:
    if plan_allows(plan, feature):
        return
    raise PlanFeatureError(
        f"plan {plan!r} does not include {feature!r}; it requires the "
        f"{vocab.GATED_FEATURES[feature]} plan or above",
        plan=plan,
        feature=feature,
        rule=vocab.PLAN_GATE_QUOTE,
    )


# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #


class RateLimiter:
    """A per-token per-minute request budget, with the researched reset signal.

    In memory, per process, keyed by token id. Two properties are deliberate:

    * **It is not written to the store.** A throttled request is a request that
      was *refused*, so recording it would put an audit row on a read, and the
      audit log's granularity is supposed to describe changes. The limiter is
      governance state, not product data.
    * **It has an injected clock.** The reset arithmetic is the part worth
      testing and it is the part a real clock would make untestable, so
      :meth:`check` takes ``now`` and the router passes one in.

    :meth:`check` returns the ``X-RateLimit-Reset`` value for the request that was
    *allowed*, so a well-behaved client can pace itself without ever hitting the
    429.
    """

    def __init__(
        self, limit: int = vocab.DEFAULT_RATE_LIMIT_PER_MINUTE, *, capacity: int = 4096
    ) -> None:
        self.limit = max(0, int(limit))
        self._hits: dict[str, deque[float]] = {}
        self._capacity = max(1, int(capacity))

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(str(key), None)

    def _window(self, key: str, now: float) -> deque[float]:
        window = self._hits.get(key)
        if window is None:
            window = deque()
            self._hits[key] = window
        # Drop everything at or before the window's start. This is also the
        # eviction: a key whose window empties stops costing anything, and a long
        # run of distinct tokens cannot grow the map without bound because the
        # oldest keys are trimmed once the map exceeds capacity.
        while window and window[0] <= now - 60.0:
            window.popleft()
        if len(self._hits) > self._capacity:
            for stale in [k for k, v in self._hits.items() if not v][
                : max(1, len(self._hits) // 4)
            ]:
                self._hits.pop(stale, None)
        return window

    def check(self, key: str, now: float) -> str:
        """Record a request against ``key``'s budget, or refuse it.

        Returns the ISO-8601 UTC instant the current window ends. Raises
        :class:`~dsr.workspace_roles.errors.RateLimited` when the budget is spent,
        carrying that same instant for the ``X-RateLimit-Reset`` header.
        """
        window = self._window(str(key), float(now))
        if self.limit and len(window) >= self.limit:
            reset_at = _iso(window[0] + 60.0)
            raise RateLimited(
                vocab.RATE_LIMIT_QUOTE,
                reset_at=reset_at,
                limit=self.limit,
                key=str(key),
            )
        window.append(float(now))
        return _iso(window[0] + 60.0) if window else _iso(float(now) + 60.0)

    def remaining(self, key: str, now: float) -> int:
        if not self.limit:
            return -1
        return max(0, self.limit - len(self._window(str(key), float(now))))


def _iso(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def reset_seconds(now: float, reset_at: str) -> int:
    """Whole seconds until ``reset_at``, floored at zero.

    A header value in the past would invite a client to spin on an already-open
    window, so the arithmetic floors rather than rounding.
    """
    try:
        parsed = datetime.fromisoformat(str(reset_at).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed - datetime.fromtimestamp(float(now), tz=timezone.utc)
    return max(0, int(delta.total_seconds()))


def rate_limit_headers(reset_at: str, *, limit: int, now: float, remaining: int) -> dict[str, str]:
    """The headers a throttled or paced response carries."""
    headers = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Reset": str(reset_at)}
    if remaining >= 0:
        headers["X-RateLimit-Remaining"] = str(remaining)
    headers["Retry-After"] = str(reset_seconds(now, reset_at))
    return headers


def window_opens(now: float) -> str:
    """The next whole-minute boundary, as ISO-8601 UTC."""
    base = datetime.fromtimestamp(float(now), tz=timezone.utc).replace(second=0, microsecond=0)
    return _iso((base + timedelta(minutes=1)).timestamp())
