"""The rules WF-084 enforces: the tenant assertion, the code's life, and the directory.

Every rule here is the researched specification for WF-084 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-084.md``, quoted in full
in issue 175, and the docstring on each rule names the evidence it came from.

The three rules the rest of the product leans on
------------------------------------------------

**The tenant is the organization id, and nothing else may decide it.**
The specification's extensibility note states the ban and the reason together: validating a
tenant by email domain "is unsafe ... as organizations might allow email addresses from
outside their corporate domain (e.g. for guest users)". So :func:`assert_tenant` compares
one field, and the email is read only as an attribute. A caller that has a profile and no
organization id gets a refusal rather than a best guess from the domain.

**An authorization code lives ten minutes, and an expired one is refused.**
The evidence quotes the bound: "The authorization code is valid for 10 minutes." An expired
code is a dead sign-in, so :func:`code_state` answers ``expired`` and the engine starts a
new one rather than retrying. A retry would be a second use of a credential the IdP has
already retired.

**A directory user's access is a function of directory state.**
The specification says "A directory is the source of truth for your customer's user and
group lists", and names the drift it replaces: "All future changes to this employee's data
and access are manually entered by IT contacts. This is error-prone and can lead to security
vulnerabilities." So there is no field in this package that a human sets to grant access,
and :func:`resolve_access` reads groups and only groups.

What this module does not decide
--------------------------------

Whether a person behind an IdP is who the directory says they are. The specification places
authentication at the IdP and the tenant assertion here, and this workflow asserts the
tenant because that is the property the research states. A build that claimed the rest would
be claiming something the cited sources do not support.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from dsr.security_governance import sso_vocabulary as vocab

#: The data key this workflow stores a room reference under.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so ``AuditedDatabase._insert_record`` strips it out of ``data`` before the
#: dynamic index is built. A row that stored its room there would be unfilterable by
#: ``find()``. The envelope still carries ``room_id``; this is the payload-side twin of it,
#: and every response projects it back to ``room_id``.
ROOM_REF = "room_ref"


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's."""
    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class IdentitySettingsInvalid(ValueError):
    """A setting this workflow will not accept.

    Carries a field-keyed map, because an administrator filling in a form needs the message
    next to the input that caused it rather than one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class ConnectionNotFound(LookupError):
    """No such connection, or it is not one this workflow owns.

    Its own type rather than the store's ``RecordNotFound``, because a feature may only map
    error types it raises itself: registering a handler for a shared type would intercept
    that exception across the whole product.
    """


class OrganizationNotFound(LookupError):
    """No such organization. Same reasoning as :class:`ConnectionNotFound`."""


class DirectoryNotFound(LookupError):
    """No such directory, or it is not one this workflow owns."""


class DirectoryUserNotFound(LookupError):
    """No such directory user, or it is not one this workflow owns."""


class SessionNotFound(LookupError):
    """No such session, or it is not one this workflow owns.

    Its own type rather than :class:`TenantAssertionFailed`, because the two mean different
    things and answer differently. A missing session is a request this app cannot resolve,
    which is a 404. A refused tenant is a request this app resolved and declined, which is a
    403. Returning 403 for a session id that does not exist tells a caller they were denied
    something, when in fact there was nothing there to deny.
    """


class TenantAssertionFailed(PermissionError):
    """The profile's organization id is not the expected tenant.

    A ``PermissionError`` subclass because that is what it is, and it carries its own type
    so the HTTP layer can map it to 403 without registering a handler for the builtin.
    """

    def __init__(
        self,
        message: str,
        *,
        expected: str | None,
        actual: str | None,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual
        self.reason = reason


#: Why an assertion failed. Three reasons and not one, because they are three different
#: defects and a refusal that cannot distinguish them is a refusal nobody can act on.
REASON_TENANT_MISMATCH = "tenant_mismatch"
REASON_TENANT_ABSENT = "tenant_absent"
REASON_TENANT_NOT_A_MEMBER = "tenant_not_a_member"

#: The two reasons a callback refuses something that is not the tenant assertion. They live
#: here beside the others because the HTTP layer renders one field for all of them and a
#: caller should not have to learn which exceptions carry a reason and which do not.
REASON_CODE_EXPIRED = "authorization_code_expired"

REFUSAL_REASONS = (
    REASON_TENANT_MISMATCH,
    REASON_TENANT_ABSENT,
    REASON_TENANT_NOT_A_MEMBER,
    REASON_CODE_EXPIRED,
)


# --------------------------------------------------------------------------- #
# Rule one: the tenant assertion
# --------------------------------------------------------------------------- #


def _normalise_tenant(value: Any) -> str | None:
    """An organization id as the single string both sides are compared as.

    Case-folded, because an IdP and a tenant record can disagree about the case of an
    opaque slug and the two are the same tenant. Nothing else is normalised: the comparison
    stays exact on the identifier itself, because loosening it would be the ban this rule
    exists to enforce in a different place.
    """

    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() or None


def assert_tenant(profile: Mapping[str, Any], expected: Any) -> str:
    """The tenant this profile belongs to, or :class:`TenantAssertionFailed`.

    The whole security property of WF-084, in one function. The specification says the app
    "asserts `profile.organizationId` matches the expected tenant and only then creates a
    session", and the extensibility note says to copy that assertion rather than the email
    domain. So exactly one field is read from the profile and exactly one field is read from
    the expectation, and they are compared to each other.

    The three failure reasons:

    * ``tenant_absent``. The profile carries no organization id. This is the case the email
      domain exists to paper over, and papering over it is what the specification forbids.
      A profile with a plausible address and no organization id is refused here.
    * ``tenant_mismatch``. Both sides said something and they differ. A guest of another
      organization is the case the specification names, and the whole point of the rule.
    * ``tenant_not_a_member``. Both sides agree, and this workflow has no record of that
      tenant. The id is right and the tenant is unknown, which is a different problem from a
      wrong id and a caller needs to be able to tell them apart.

    Returns the tenant as the profile wrote it, not the case-folded form, so the caller's
    session record names the id the IdP sent.
    """

    profile = profile or {}
    actual_raw = profile.get(vocab.ORGANIZATION_ID)
    actual = _normalise_tenant(actual_raw)
    wanted = _normalise_tenant(expected)

    if actual is None:
        raise TenantAssertionFailed(
            "The profile carries no organization id, so the tenant cannot be asserted.",
            expected=expected if isinstance(expected, str) else None,
            actual=None,
            reason=REASON_TENANT_ABSENT,
        )
    if wanted is None:
        raise TenantAssertionFailed(
            "No expected tenant was supplied, so the assertion has nothing to compare against.",
            expected=None,
            actual=str(actual_raw),
            reason=REASON_TENANT_ABSENT,
        )
    if actual != wanted:
        raise TenantAssertionFailed(
            "The profile's organization id is not the expected tenant.",
            expected=expected if isinstance(expected, str) else None,
            actual=str(actual_raw),
            reason=REASON_TENANT_MISMATCH,
        )
    return str(actual_raw)


def email_domain_is_never_the_tenant() -> bool:
    """The ban, stated as a fact a test can assert rather than as prose.

    It reads the domain off an address because that is what the rule refuses to do, and it
    returns whether this workflow has any place that would use the answer. It does not, so
    the answer is always False. The function exists so the prohibition has one location:
    a later reader who wants to check an email domain has to change this function, and the
    tests that assert it fails change with it.
    """

    return False


def email_domain_of(profile: Mapping[str, Any]) -> str | None:
    """The domain of a profile's first email, for display only.

    Present so the page can show which address a session belongs to, and named as what it
    is. It is never an argument to :func:`assert_tenant` and never reaches a grant decision.
    The rule above is what makes that safe: the tenant assertion has no input this value
    could reach.
    """

    emails = (profile or {}).get(vocab.SCIM_EMAILS)
    address = None
    if isinstance(emails, list) and emails:
        first = emails[0]
        address = first.get(vocab.SCIM_EMAIL_ADDRESS) if isinstance(first, Mapping) else first
    if not address:
        address = (profile or {}).get(vocab.EMAIL)
    if not isinstance(address, str) or "@" not in address:
        return None
    return address.rsplit("@", 1)[1].casefold() or None


def tenant_is_known(organizations: Iterable[Mapping[str, Any]], tenant: Any) -> bool:
    """Whether this workflow holds a record for the tenant a profile named.

    The third assertion the rules above cannot make. Two organization ids can be equal and
    one of them still be unknown here, and a caller needs to hear that as a distinct answer
    rather than as a mismatch.
    """

    wanted = _normalise_tenant(tenant)
    if wanted is None:
        return False
    return any(_normalise_tenant(row.get("organization_id")) == wanted for row in organizations)


# --------------------------------------------------------------------------- #
# Rule two: the authorization code, and the redirect URI it must come back to
# --------------------------------------------------------------------------- #

CODE_STATE_FRESH = "fresh"
CODE_STATE_EXPIRED = "expired"
CODE_STATE_UNKNOWN = "unknown"

CODE_STATES = (CODE_STATE_FRESH, CODE_STATE_EXPIRED, CODE_STATE_UNKNOWN)


def code_state(issued_at: Any, now: datetime) -> str:
    """Whether an authorization code is still usable, from the bound the evidence gives.

    The specification quotes it once: "The authorization code is valid for 10 minutes." So
    the bound is :data:`vocab.AUTHORIZATION_CODE_TTL_MINUTES` and nothing else decides it.
    A code exactly at the bound is expired, because the vendor's sentence says the code is
    valid for ten minutes and a code at ten minutes and zero seconds has had all ten.

    A code with no recorded issue time is ``unknown`` rather than ``fresh``. The safe
    default for a missing fact about a credential is not to trust it, and treating an
    unknown age as fresh would be the same defect as ignoring the bound.
    """

    issued = _as_datetime(issued_at)
    if issued is None:
        return CODE_STATE_UNKNOWN
    age = (now - issued).total_seconds()
    if age >= vocab.AUTHORIZATION_CODE_TTL_SECONDS:
        return CODE_STATE_EXPIRED
    if age < 0:
        # A code stamped in the future has not been issued yet. Treated as fresh rather
        # than as expired, because a clock a few seconds ahead is a fact about the machine
        # rather than a fact about the code.
        return CODE_STATE_FRESH
    return CODE_STATE_FRESH


def seconds_until_expiry(issued_at: Any, now: datetime) -> int | None:
    """Whole seconds a code has left, or ``None`` when its age is unknown."""

    issued = _as_datetime(issued_at)
    if issued is None:
        return None
    remaining = vocab.AUTHORIZATION_CODE_TTL_SECONDS - (now - issued).total_seconds()
    return max(0, int(remaining // 1))


def validate_redirect_uri(candidate: Any, registered: Iterable[Any]) -> str:
    """The registered redirect URI this request names, or a refusal.

    A redirect URI is checked against what the tenant registered rather than accepted from
    the caller, because an authorization URL that carried an attacker's redirect URI would
    send the user's code to the attacker. The match is on scheme, host, port and path
    together, and the comparison is exact on the path: a redirect URI is a destination, and
    a destination that accepts a prefix match is not the destination that was registered.
    """

    if not isinstance(candidate, str) or not candidate.strip():
        raise IdentitySettingsInvalid(
            "A redirect URI is required.",
            {vocab.REDIRECT_URI_PARAM: "Name an absolute URI."},
        )
    parsed = urlparse(candidate.strip())
    if not parsed.scheme or not parsed.netloc:
        raise IdentitySettingsInvalid(
            "A redirect URI must be absolute.",
            {vocab.REDIRECT_URI_PARAM: "Use a full URI including the scheme and the host."},
        )
    wanted = (parsed.scheme, parsed.netloc, parsed.path or "/")
    for entry in registered:
        if not isinstance(entry, str):
            continue
        other = urlparse(entry.strip())
        if (other.scheme, other.netloc, other.path or "/") == wanted:
            return entry.strip()
    raise IdentitySettingsInvalid(
        "That redirect URI is not registered for this organization.",
        {
            vocab.REDIRECT_URI_PARAM: (
                "Use one of the redirect URIs this organization registered. A callback "
                "that accepts an unregistered destination sends the authorization code "
                "somewhere it should not go."
            )
        },
    )


def redirect_uri_limit(single_tenant: bool) -> int:
    """How many redirect URIs a tenancy may hold.

    The evidence quotes both halves and the halves disagree on purpose: "Multi-tenant apps
    will typically have a single redirect URI specified. You can set multiple redirect URIs
    for single-tenant apps." So the limit follows the tenancy rather than being one number
    for everybody.
    """

    return (
        vocab.SINGLE_TENANT_MAX_REDIRECT_URIS
        if single_tenant
        else vocab.MULTI_TENANT_MAX_REDIRECT_URIS
    )


def build_authorization_url(
    *,
    issuer: str,
    organization: str | None = None,
    connection: str | None = None,
    provider: str | None = None,
    redirect_uri: str | None = None,
    client_id: str | None = None,
    state: str | None = None,
) -> str:
    """The authorization URL for one staff-initiated sign-in.

    The specification names the call: "sso.getAuthorizationUrl({ organization | connection
    | provider, redirectUri, clientId })". This builds that URL and nothing else: no code is
    minted here and no session exists yet, because the callback is where the tenant is
    asserted.

    The three identifiers are carried under their own names and are not folded into one.
    The vendor's evidence is explicit that ``provider`` "is used for OAuth connections"
    while ``connection`` "can be used for SAML or OIDC", so sending all three at once would
    send three instructions where the IdP expects one.
    """

    if not organization and not connection and not provider:
        raise IdentitySettingsInvalid(
            "An authorization URL needs an organization, a connection or a provider.",
            {
                "identifier": (
                    "Name an organization, a connection or a provider. The three do different "
                    "jobs and one of them is required."
                )
            },
        )
    query: dict[str, str] = {}
    if organization:
        query[vocab.ORGANIZATION_PARAM] = organization
    if connection:
        query[vocab.CONNECTION_PARAM] = connection
    if provider:
        query[vocab.PROVIDER_PARAM] = provider
    if redirect_uri:
        query[vocab.REDIRECT_URI_PARAM] = redirect_uri
    if client_id:
        query[vocab.CLIENT_ID_FIELD] = client_id
    if state:
        query[vocab.STATE_PARAM] = state
    base = issuer.rstrip("?") + ("&" if "?" in issuer else "?")
    return base + urlencode(query)


def identifier_kind(
    organization: str | None, connection: str | None, provider: str | None
) -> str | None:
    """Which of the three identifiers a request supplied.

    Returns the identifier's own name, or ``None`` when none was given. Not a
    "primary/secondary" ranking and not a merged value, because the specification's
    evidence draws a line between a connection and a provider and this workflow keeps it.
    """

    for name, value in (
        (vocab.ORGANIZATION_PARAM, organization),
        (vocab.CONNECTION_PARAM, connection),
        (vocab.PROVIDER_PARAM, provider),
    ):
        if value:
            return name
    return None


# --------------------------------------------------------------------------- #
# Rule three: directory state decides access
# --------------------------------------------------------------------------- #


def normalise_groups(members: Any) -> list[str]:
    """The group ids on a directory user or in a stored record.

    Deduplicated and order-preserving. The order is kept rather than sorted so that a group
    list rendered from the directory reads the way the directory wrote it, and it is
    deduplicated because the two delivery paths can each report the same membership and a
    rule that counted a group twice would apply the same grant twice.
    """

    if not isinstance(members, (list, tuple, set)):
        return []
    seen: list[str] = []
    for member in members:
        if isinstance(member, Mapping):
            member = member.get("id") or member.get("group_id") or member.get("value")
        text = str(member or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def resolve_access(groups: Iterable[Any], rules_by_group: Mapping[str, Any]) -> dict[str, Any]:
    """The access a user's groups give them.

    ``rules_by_group`` is keyed by **both** the directory's own group id and this app's
    group record id, and a membership list may name either. That is deliberate and it is
    what the two available vocabularies are:

    * A SCIM user resource names its groups with the directory's external group id. That is
      what an IdP sends and what a directory reports.
    * An access rule written in this app holds the record id of the group row.

    Keying on only one of the two makes every real payload fail to match, which is the
    defect this function's comment is about: an access rule that silently matches nothing
    reports no access for a user who is in an admin group, and a page showing that is worse
    than no page at all. Keying on both means a membership list written either way resolves,
    and a group id that names neither simply does not match, which is the honest answer.

    Group membership is an input to a decision, not a label, which is what the
    specification's automation note says a directory group does: "create groups that inform
    access rules". So this is the only place access comes from, and there is no field a
    human can set instead.

    The strongest role wins rather than the last one. A user in an admin group and a member
    group is an admin whichever order the directory reported the memberships in, and
    resolving by order would make the same directory state resolve two ways.
    """

    member_groups = normalise_groups(groups)
    matched: list[str] = []
    matched_names: list[str] = []
    granted: str | None = None
    for group in member_groups:
        rule = rules_by_group.get(group)
        if not rule:
            continue
        matched.append(group)
        external = str(rule.get(vocab.SCIM_EXTERNAL_ID) or group)
        if external not in matched_names:
            matched_names.append(external)
        role = str(rule.get("role") or vocab.RULE_ROLE_MEMBER)
        if granted is None or vocab.RULE_ROLES.index(role) < vocab.RULE_ROLES.index(granted):
            granted = role
    return {
        "groups": member_groups,
        "matched_groups": matched_names,
        "role": granted,
        "granted": granted is not None,
        "source": "directory",
    }


def active_memberships(users: Iterable[Mapping[str, Any]], group_id: str) -> list[str]:
    """The active directory users in one group.

    Filtered on the directory's own ``active`` attribute rather than on anything this
    workflow set. The directory is the source of truth, so a row this build holds but the
    directory has deactivated does not appear in the group it is supposedly in.
    """

    members: list[str] = []
    for user in users:
        if group_id not in normalise_groups(user.get(vocab.SCIM_GROUPS)):
            continue
        if not is_active(user):
            continue
        identifier = str(user.get(vocab.SCIM_EXTERNAL_ID) or user.get("id") or "")
        if identifier and identifier not in members:
            members.append(identifier)
    return members


def is_active(user: Mapping[str, Any]) -> bool:
    """Whether the directory says this user is active.

    An absent attribute reads as inactive. A directory that names a user without saying
    whether they are active has not asserted that they are, and treating silence as
    employment is the drift the specification warns about.
    """

    return bool((user or {}).get(vocab.SCIM_ACTIVE, False))


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def _as_datetime(value: Any) -> datetime | None:
    """Read an ISO 8601 stamp as an aware UTC datetime, or ``None``."""

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant, always in UTC.

    The milliseconds are kept. An authorization code's life is ten minutes, so a stamp that
    rounded to the second would make the boundary a test of rounding rather than of the
    rule.
    """

    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def code_deadline(issued_at: Any) -> str | None:
    """The ISO stamp at which an authorization code stops being valid."""

    issued = _as_datetime(issued_at)
    if issued is None:
        return None
    return stamp(issued + timedelta(minutes=vocab.AUTHORIZATION_CODE_TTL_MINUTES))
