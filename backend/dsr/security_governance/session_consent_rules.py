"""The rules WF-083 enforces: consent, masking, IP exclusion, labels, links, retention.

Every rule here is the researched specification for WF-083 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-083.md``, quoted in
full in issue 191, and the docstring on each rule names the evidence it came from.

The six rules the rest of this workflow leans on
-----------------------------------------------

**Consent is two axes and not one flag.** The evidence gives the call verbatim:
``window.clarity('consentv2', {ad_Storage: 'granted'|'denied', analytics_Storage:
'granted'|'denied'})``. So :func:`parse_consent_call` reads both axes, all four
combinations are legal, and no function anywhere in this workflow reduces them to a single
boolean. A room that allows analytics while refusing ad storage is an ordinary and legal
setting, and collapsing it to "consent denied" would stop recording a visit the evidence
says may be recorded.

**A signal is not a decision.** :data:`~dsr.security_governance.session_consent.CONSENT_SIGNAL`
says a choice was requested and :data:`~dsr.security_governance.session_consent.GRANTED`
says a choice was made. They are different facts, so they are different values and a signal
is never stored as a decision.

**Denial destroys the session rather than skipping it.** The evidence is explicit: "Clarity
deletes any existing cookie for the website, ends the current session, and restarts
tracking in no-consent mode." So :func:`identity_for` hands out a fresh identifier per page
view once either axis is denied, and the engine turns a revocation into a delete.

**Masking happens before the write and default suppression is total.** The evidence asks
"Is masked data uploaded to Clarity? No." and says "By default, Clarity suppresses the
client's entire content." So :func:`mask_payload` runs before anything is stored,
:func:`residual_values` is the check the engine refuses to store on, and
:data:`~dsr.security_governance.session_consent.DEFAULT_MASKING_MODE` is
``suppress_all``.

**IP exclusion is evaluated at ingest and it is IPv4 only.** The evidence says "No
sessions from visitors on the list are recorded", so :func:`blocked_by` runs before a
recording row is written and a blocked visitor produces no row at all. It also says "Clarity
only supports IPv4 addresses. We do not support IPv6 or dynamic IP addresses (for example,
VPN)", so :func:`cidr_block` refuses an IPv6 range and returns the reason.

**Deletion is project-granularity and that is a sourced limit.** The evidence says "you
can't delete or download specific recordings" and "You need to delete the entire project to
delete user's data." So :func:`require_project_granularity` refuses a single-recording
delete rather than quietly deleting the project, and the refusal is the feature.

What this module does not decide
--------------------------------

Whether a room is compliant with a law. Every value here is an axis, a mask, a range, a
count or a window. No function returns "compliant", no route serves a certification, and
the enforcement date is read as configuration rather than as a legal deadline.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from dsr.security_governance import session_consent as vocab

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All three types are declared here and raised by nothing else in the product. That is
# what makes it safe for the feature module to map them: the host refuses a second feature
# registering a handler for the same type, and a handler for a shared type such as
# ValueError would intercept that exception across the whole product.


class SessionConsentRefusal(ValueError):
    """A value, a bound or a window this workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input that
    caused it rather than in one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class SessionConsentNotFound(LookupError):
    """No such project, visit, recording, label, share link or blocked range.

    Its own type rather than the store's ``RecordNotFound``, for the reason above.
    """


class SessionConsentAdministratorRequired(PermissionError):
    """A blocking change arrived from a role below the administrator tier.

    The evidence names the role check: "To set up IP exclusion, you need to be an
    *administrator* for your project." The role name is not invented here: the feature
    module reads the repository's role surface and hands the tier in, so there is one role
    vocabulary in this product and one place that reads it.
    """

    code = "administrator_required"

    def __init__(
        self,
        action: str,
        *,
        administrator: str,
        presented_role: Any = None,
    ) -> None:
        super().__init__(f"{action} is an administrator-only change. Pass role={administrator!r}.")
        self.action = action
        self.administrator = str(administrator)
        self.presented_role = _token(presented_role) or "no_role"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": str(self),
            "action": self.action,
            "presented_role": self.presented_role,
            "required_role": self.administrator,
            "remediation": f"Pass role={self.administrator!r}.",
        }


def _token(value: Any) -> str:
    """A role or a tier reduced to one comparable spelling.

    Case, surrounding whitespace, and a hyphen or a space where the id has an underscore
    all collapse, so "Admin", "admin" and "Instance Admin" compare as one role. A role this
    product does not know reduces to itself and therefore never compares equal to the
    administrator tier, which is what makes :func:`require_administrator` fail closed
    without this module knowing any vocabulary of its own.
    """

    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def token(value: Any) -> str:
    """The comparable spelling of any value.

    Public so the engine can reduce a stored value the same way this module compares one.
    Two spellings of the same thing must never reach two different comparisons, because a
    region stored as "United Kingdom" and read as "uk" is a gate that silently stopped
    enforcing.
    """

    return _token(value)


def require_administrator(role: Any, action: str, administrator: str) -> str:
    """Return the role, or refuse. The check the evidence names, and nothing softer.

    There is no read-only tier here. The specification says administrator for IP
    exclusion and says nothing about a lesser role, so a missing role is a denial rather
    than a default, and the error names both the role presented and the role that would
    have worked.
    """

    wanted = _token(administrator)
    if not wanted:
        raise SessionConsentRefusal(
            "This workflow needs an administrator role to compare against.",
            {"administrator": "Name the role tier that may make a blocking change."},
        )
    if _token(role) != wanted:
        raise SessionConsentAdministratorRequired(
            action, administrator=administrator, presented_role=role
        )
    return str(role)


# --------------------------------------------------------------------------- #
# Instants: two encodings, one parser
# --------------------------------------------------------------------------- #
#
# The engagement collections this workflow sits beside store Unix milliseconds and this
# workflow's own rows store an ISO 8601 UTC instant. Both are read here and compared as
# aware datetimes, so the retention clock never compares a millisecond number with an ISO
# string.
#
# A value it cannot read is a validation failure rather than a silent zero: treating an
# unreadable instant as the epoch would purge a recording the moment it was written.


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept.

    The milliseconds are kept because two page views inside one second must be two rows,
    and because the vendor's console message must be reported at the moment it happened.
    """

    moment = moment or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def coerce_instant(value: Any, field: str) -> datetime | None:
    """Read an ISO 8601 string, a Unix millisecond number, or a datetime.

    Returns ``None`` for an absent value and raises :class:`SessionConsentRefusal` for a
    value it cannot read. The field name is required so the message lands beside the input
    that caused it.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise SessionConsentRefusal(
                f"{field} is not a readable instant.", {field: str(exc)}
            ) from exc
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionConsentRefusal(
            f"{field} is not an ISO 8601 instant.", {field: f"Read {text!r} as an instant."}
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The consent call
# --------------------------------------------------------------------------- #
#
# Two axes, four combinations, and no single boolean anywhere. This is the part of the
# specification an implementer is most likely to get wrong, so the shape is enforced here
# rather than left to each caller.


def consent_call(
    ad_storage: Any,
    analytics_storage: Any,
    *,
    call: str = vocab.CONSENT_CALL,
) -> dict[str, Any]:
    """The vendor's own call, rebuilt from two validated axes.

    The API name and the two axis names are the vendor's, quoted from the "APIs touched"
    section, so a caller can tell a recorded consent call from one this product invented.
    The keys are the vendor's casing because they are the JSON keys of the vendor's call,
    and :func:`parse_consent_call` reads both spellings.
    """

    return {
        "api": str(call or vocab.CONSENT_CALL),
        "ad_Storage": normalise_axis(ad_storage, vocab.AD_STORAGE),
        "analytics_Storage": normalise_axis(analytics_storage, vocab.ANALYTICS_STORAGE),
    }


def normalise_axis(value: Any, axis: str) -> str:
    """Read one axis as ``granted`` or ``denied``, and refuse anything else.

    A signal is accepted here and mapped to a denial, because a requested-but-unanswered
    choice is not a grant. Anything else is a refusal rather than a coercion: the evidence
    gives two words, so a third word is a client bug and silently reading it as a denial
    would hide that bug behind a working-looking gate.
    """

    token = _token(value)
    if token == vocab.GRANTED:
        return vocab.GRANTED
    if token == vocab.DENIED:
        return vocab.DENIED
    if token == vocab.CONSENT_SIGNAL:
        return vocab.DENIED
    raise SessionConsentRefusal(
        f"{axis} must be 'granted' or 'denied'.",
        {
            axis: (
                f"Read {value!r}. A signal means a choice was requested, not made, "
                "so it is a denial."
            )
        },
    )


#: The two spellings a caller may use for each axis. The vendor's own casing is the
#: canonical one; the lower snake spelling is this product's internal name, so a client
#: built against ``records.data`` is not forced to know the vendor's capitalisation.
AXIS_KEYS: dict[str, tuple[str, ...]] = {
    vocab.AD_STORAGE: ("ad_Storage", "ad_storage"),
    vocab.ANALYTICS_STORAGE: ("analytics_Storage", "analytics_storage"),
}


def parse_consent_call(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read a consent call into both axes, the API used, and what it carries.

    The legacy call is accepted because the evidence names it: "``consentv2`` is the latest
    and recommended method... It replaces the [older Consent API] which is planned for
    deprecation." So a payload that names the old API is read, reported as legacy, and not
    refused. A payload that names an API this workflow does not know is refused, because
    reading an unknown call would invent a decision nobody made.

    A payload that carries an axis value of :data:`~dsr.security_governance.session_consent.SIGNAL`
    is read as a signal rather than refused. The CMP and the recording call are two
    messages, and a client that reports the prompt on the axis it was asked about is
    reporting "not answered yet", which is a denial, not a bug.
    """

    body = dict(payload or {})
    call = _token(body.get("api") or body.get("call") or vocab.CONSENT_CALL)
    if call not in (vocab.CONSENT_CALL, vocab.LEGACY_CONSENT_CALL):
        raise SessionConsentRefusal(
            "This workflow reads the consentv2 and legacy consent APIs only.",
            {"api": f"Read {body.get('api') or body.get('call')!r}."},
        )

    signal_only = is_signal_only(body)
    reported_signal = signal_only or any(
        _token(body[key]) == vocab.CONSENT_SIGNAL
        for keys in AXIS_KEYS.values()
        for key in keys
        if key in body
    )
    axes: dict[str, str] = {}
    for axis, keys in AXIS_KEYS.items():
        found = next((body[key] for key in keys if key in body), None)
        if reported_signal:
            axes[axis] = vocab.DENIED
        else:
            axes[axis] = normalise_axis(found, axis)

    return {
        "api": call,
        "legacy": call == vocab.LEGACY_CONSENT_CALL,
        "axes": axes,
        "granted": granted_axes(axes),
        "signal_only": signal_only,
        "signal_reported": reported_signal,
        "before_cookies": before_cookies(body),
    }


def granted_axes(axes: Mapping[str, Any]) -> list[str]:
    """The axes that were granted. Empty when neither was, which is the common deny.

    Returned as a list in the declared axis order, so the value is stable between runs and
    a test can compare it without sorting.
    """

    return [axis for axis in vocab.CONSENT_AXES if _token(axes.get(axis)) == vocab.GRANTED]


def is_signal_only(payload: Mapping[str, Any]) -> bool:
    """True when the room announced a consent prompt without reporting a decision.

    The user flow separates the two: the CMP fires, and only then does the page call
    ``clarity('consentv2', ...)`` with granted or denied. So a signal carries no axes and
    must never be stored as a decision, and a room that emitted one recorded nothing.
    """

    body = dict(payload or {})
    return _token(body.get("type") or body.get("signal")) == vocab.CONSENT_SIGNAL


def before_cookies(payload: Mapping[str, Any] | None = None) -> bool:
    """True unless the caller says the decision came after cookies were set.

    The user flow is explicit: the call is made "with `granted` or `denied` before cookies
    are set". A payload may therefore assert ``before_cookies=False``, and this workflow
    then refuses the record rather than storing a late decision as though it had governed
    the cookie write. A payload that says nothing is taken at its word, because the flow
    describes the order as a property of the integration rather than as a field.
    """

    if payload is None:
        return True
    return bool(dict(payload).get("before_cookies", True))


def requires_consent(region: Any, *, enforced_regions: Sequence[str] | None = None) -> bool:
    """True when this region is one the vendor enforces a consent signal for.

    The evidence is scoped: "page visits originating from the European Economic Area
    (EEA), United Kingdom (UK), and Switzerland (CH)". The list is passed in rather than
    read from a clock here, because the start date is a vendor deadline and this workflow
    enforces on its own schedule; the derivation is recorded in
    :mod:`dsr.security_governance.session_consent_inferences`.

    Both sides are reduced through :func:`_region`, so "EEA", "eea" and " European
    Economic Area " are one region. A gate that silently stopped enforcing because a
    caller spelled a region differently is the failure this reduction exists to prevent.
    """

    regions = tuple(enforced_regions or vocab.CONSENT_ENFORCED_REGIONS)
    return _region(region) in {_region(name) for name in regions}


#: The long names the evidence itself uses, mapped onto the three codes it also uses. The
#: vendor geolocates to a country name, so a caller will send "Switzerland" at least as
#: often as "CH", and the two must not be two different regions.
REGION_ALIASES: dict[str, str] = {
    "european_economic_area": "eea",
    "european economic area": "eea",
    "eu": "eea",
    "europe": "eea",
    "united_kingdom": "uk",
    "great_britain": "uk",
    "gb": "uk",
    "england": "uk",
    "scotland": "uk",
    "wales": "uk",
    "northern_ireland": "uk",
    "switzerland": "ch",
    "swiss": "ch",
    "confoederatio_helvetica": "ch",
}


def _region(value: Any) -> str:
    """One comparable spelling for a region name or code."""

    text = str(value or "").strip().lower()
    return REGION_ALIASES.get(text, text.replace("-", "_").replace(" ", "_"))


def enforcement_active(now: datetime | None = None) -> bool:
    """True on and after the vendor's enforcement start date.

    Kept as one function so there is exactly one place that reads the date, and so a
    boundary test has something to call. The date is a vendor deadline; this workflow uses
    it as its own default because the specification ties the requirement to it.
    """

    start = coerce_instant(vocab.CONSENT_ENFORCEMENT_START, "enforcement_start")
    if start is None:
        raise SessionConsentRefusal(
            "The enforcement start date is unreadable.",
            {"enforcement_start": "Expected an ISO date."},
        )
    return (now or utcnow()) >= start


# --------------------------------------------------------------------------- #
# Identity under denial
# --------------------------------------------------------------------------- #
#
# The evidence is unambiguous: "If consent is not granted, Clarity assigns a **unique ID
# per page view** and does not use cookies to persist session data." So an identifier is a
# function of the page view once a denial is in force, and two page views of the same
# visitor then share nothing.


def identity_kind(axes: Mapping[str, Any]) -> str:
    """The evidence's own two names: a persistent identity, or one per page view.

    :data:`~dsr.security_governance.session_consent.PER_PAGE_VIEW` unless **every** axis is
    granted, because a denial on either axis ends the session: "Clarity deletes any
    existing cookie for the website, ends the current session, and restarts tracking in
    no-consent mode." A cookie cannot be scoped to one axis, so a grant on the other one
    is not a persistent identity.

    This is the derivation ``DERIVED_DENIAL_ON_ONE_AXIS_ENDS_THE_SESSION``. The axes stay
    independent everywhere else, which is what the two-axis contract asks for; what a
    denial shares is the browser cookie, and that is the one resource a per-axis grant
    cannot preserve.
    """

    granted = granted_axes(axes)
    if len(granted) == len(vocab.CONSENT_AXES):
        return vocab.PERSISTENT
    return vocab.PER_PAGE_VIEW


def identity_for(
    axes: Mapping[str, Any],
    *,
    visitor: Any = None,
    page_view: Any = None,
) -> dict[str, Any]:
    """The identifier this visit may carry, and whether cookies may persist it.

    Under a grant the identity is the visitor, so two page views share one identifier.
    Under a denial it is derived from the page view, so two page views get two identifiers,
    and a caller who passes the same page view twice gets the same identifier only because
    it is the same page view. This is the test the specification asks for: "Test that two
    page views under denial get two identifiers."
    """

    kind = identity_kind(axes)
    if kind == vocab.PERSISTENT:
        token = str(visitor or page_view or "unknown-visitor")
        return {
            "kind": kind,
            "visitor_id": token,
            "page_view_id": str(page_view) if page_view else None,
            "cookies_persist": True,
            "cross_session_tracking": True,
            "anonymous": False,
        }
    token = str(page_view or "no-page-view")
    return {
        "kind": kind,
        "visitor_id": None,
        "page_view_id": token,
        "cookies_persist": False,
        "cross_session_tracking": False,
        "anonymous": True,
    }


def revoke_call() -> dict[str, Any]:
    """The documented revoke call, as the evidence writes it.

    "``window.clarity('consent', false)`` - erase cookies and start a new session." Denial
    is destructive, so the workflow that answers a denial names the call the integration
    must make rather than describing it.
    """

    return {"api": vocab.LEGACY_CONSENT_CALL, "value": False, "effect": vocab.DESTROYED_ON_DENIAL}


# --------------------------------------------------------------------------- #
# Masking, before the write
# --------------------------------------------------------------------------- #
#
# Two evidence sentences decide this whole section. "Is masked data uploaded to Clarity? No."
# says the mask is applied before the value leaves. "By default, Clarity suppresses the
# client's entire content." says the default is total suppression, so an unmasked value is
# the exception a project has to configure.


def masking_mode(value: Any) -> str:
    """Read a masking mode, and default to total suppression.

    The default is the documented one rather than a house choice, so a project that never
    configures masking stores no content at all. An unknown mode is refused rather than
    treated as the default: a misspelt selector must not silently suppress nothing.
    """

    if value is None or value == "":
        return vocab.DEFAULT_MASKING_MODE
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in set(vocab.MASKING_MODES):
        return text
    raise SessionConsentRefusal(
        "This masking mode is not one this workflow implements.",
        {"masking_mode": f"Read {value!r}. Choose one of {', '.join(vocab.MASKING_MODES)}."},
    )


def mask_value(value: Any, mode: Any = None, *, selectors: Iterable[str] | None = None) -> Any:
    """Mask one value for the chosen mode.

    ``suppress_all`` returns the mask for every scalar, because the evidence says the
    client's entire content is suppressed by default. ``element_selector`` masks a value
    when its key is one of the configured selectors. ``select_text`` masks it when it is
    text. A structure is walked, so a nested payload is masked as deeply as it is nested
    rather than only at the top level.
    """

    active = masking_mode(mode)
    chosen = {_token(item) for item in (selectors or ())}
    return _mask(value, active, chosen, key=None)


def _mask(value: Any, mode: str, selectors: set[str], *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {name: _mask(item, mode, selectors, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(item, mode, selectors, key=key) for item in value]
    if value is None:
        return None
    if mode == vocab.SUPPRESS_ALL:
        return vocab.MASK
    if mode == vocab.ELEMENT_SELECTOR:
        if key is not None and _token(key) in selectors:
            return vocab.MASK
        return value
    return vocab.MASK if isinstance(value, str) else value


def mask_payload(
    payload: Mapping[str, Any],
    mode: Any = None,
    *,
    selectors: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Mask a frame before it is stored, and record the fact that it was masked.

    The evidence says masked data is not uploaded, so this runs before the write rather
    than after it, and the record carries the mode so a reviewer can tell a masked value
    from a value that was never captured.

    ``suppressed_values`` is the count of values the mask replaced. It is what lets a
    reviewer see that masking happened, without the page having to diff two payloads.
    """

    active = masking_mode(mode)
    body = dict(payload or {})
    masked = _mask(body, active, {_token(item) for item in (selectors or ())})
    masked["masking_mode"] = active
    masked["masked"] = True
    masked["suppressed_values"] = _suppressed(body, masked)
    return masked


def _suppressed(original: Mapping[str, Any], masked: Mapping[str, Any]) -> int:
    """How many leaves the mask replaced. Counted, never estimated."""

    total = 0
    for _, value in _leaves(dict(original or {})):
        if isinstance(value, str) and value and value != vocab.MASK:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            continue
        if value is None:
            continue
        if str(value) == vocab.MASK:
            total += 1
    masked_masks = sum(1 for _, value in _leaves(dict(masked or {})) if value == vocab.MASK)
    return max(0, masked_masks - total)


def residual_values(original: Mapping[str, Any], masked: Mapping[str, Any]) -> list[str]:
    """Values present in the original and still readable in the masked payload.

    This is the check the engine refuses to store on. "Is masked data uploaded to Clarity?
    No." is a claim this workflow can only honour by comparing what it holds with what it
    was given, so the comparison is a function rather than a comment. It reports dotted
    paths, so the response names the field that leaked rather than saying "something did".
    """

    masked_text = repr(masked)
    found: list[str] = []
    for path, value in _leaves(dict(original or {})):
        if value in (None, True, False):
            continue
        if vocab.MASK in str(value):
            continue
        if repr(value) not in masked_text:
            continue
        if not _masked_at(dict(masked or {}), path, value):
            found.append(path)
    return found


def _leaves(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for name, item in value.items():
            child = f"{prefix}.{name}" if prefix else str(name)
            yield from _leaves(item, child)
        return
    yield prefix, value


def _masked_at(payload: Mapping[str, Any], path: str, value: Any) -> bool:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return True
        node = node[part]
    if node == vocab.MASK:
        return True
    return isinstance(node, (list, tuple)) and all(
        item == vocab.MASK or item == value for item in node
    )


#: The fields this workflow adds to a frame after masking. They are metadata about the
#: mask rather than captured content, so the residual check skips them by name.
MASK_CONTROL_FIELDS = ("masking_mode", "masked", "suppressed_values")


def contains_unmasked_content(payload: Mapping[str, Any] | None) -> bool:
    """True when a payload carries a value that was never masked.

    Checked against the document's own two facts: the default is total suppression, so an
    unmasked scalar is the exception, and the mask is the placeholder
    :data:`~dsr.security_governance.session_consent.MASK`. A frame carrying text alongside
    the mask is refused rather than stored.

    The control fields :data:`MASK_CONTROL_FIELDS` are skipped by name. They are written
    after masking and describe the mask, so counting them as captured content would make
    every masked frame look unmasked and the check would reject its own output.
    """

    if not payload:
        return False
    if not payload.get("masked"):
        return True
    for path, value in _leaves(dict(payload)):
        if path in MASK_CONTROL_FIELDS:
            continue
        if isinstance(value, str) and value and value != vocab.MASK:
            return True
    return False


# --------------------------------------------------------------------------- #
# IP exclusion, evaluated at ingest
# --------------------------------------------------------------------------- #
#
# "No sessions from visitors on the list are recorded." So the check runs before the row is
# written, and a blocked visitor produces no recording row at all rather than a row marked
# blocked. A blocked row would still be a stored session, which is the thing the evidence
# refuses.
#
# "Clarity only supports IPv4 addresses. We do not support IPv6 or dynamic IP addresses (for
# example, VPN)." So an IPv6 range is refused and the refusal is recorded rather than
# quietly dropped, because an operator who added a range expects it to do something.


def normalise_ip(value: Any) -> str:
    """Read an IPv4 address and return it in its canonical dotted form.

    An IPv6 address is refused with its own reason rather than as malformed input, because
    the evidence names IPv6 as unsupported and the operator deserves to be told which of the
    two reasons applies.
    """

    text = str(value or "").strip()
    if not text:
        raise SessionConsentRefusal("An IP address is required.", {"ip": "Read an IPv4 address."})
    if ":" in text:
        raise SessionConsentRefusal(
            "This workflow supports IPv4 addresses only.",
            {
                "ip": (
                    f"Read {text!r}. The evidence states IPv6 is not supported, and a dynamic "
                    "address such as a VPN address cannot be blocked."
                )
            },
        )
    try:
        parsed = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError as exc:
        raise SessionConsentRefusal(
            "That is not an IPv4 address.", {"ip": f"Read {text!r}. {exc}"}
        ) from exc
    return str(parsed)


def cidr_block(value: Any) -> dict[str, Any]:
    """Read a blocklist entry, which may be a bare address or a CIDR range.

    A bare address becomes a single-host range of ``/32``, so the storage shape is one kind
    of thing and the page has one control rather than two. An IPv6 range is refused, and an
    entry wider than a classless range is refused for the reason the evidence gives.
    """

    text = str(value or "").strip()
    if not text:
        raise SessionConsentRefusal(
            "An address or a CIDR range is required.", {"cidr": "Read an IPv4 address or range."}
        )
    if ":" in text:
        raise SessionConsentRefusal(
            "This workflow supports IPv4 ranges only.",
            {
                "cidr": (
                    f"Read {text!r}. The evidence states IPv6 is not supported, so the range "
                    "was recorded as rejected rather than stored."
                )
            },
        )
    try:
        network = ipaddress.IPv4Network(text, strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise SessionConsentRefusal(
            "That is not an IPv4 address or range.", {"cidr": f"Read {text!r}. {exc}"}
        ) from exc
    return {
        "cidr": str(network),
        "network": str(network.network_address),
        "prefix_length": network.prefixlen,
        "is_single_host": network.prefixlen == 32,
    }


def ip_in_block(address: Any, blocks: Iterable[Mapping[str, Any]]) -> str | None:
    """The block that excludes this address, or ``None``.

    Returns the matched entry rather than a boolean, because the evidence gives the
    operator a console message to look for and the page has to name which range excluded
    the visitor. The console message is
    :data:`~dsr.security_governance.session_consent.BLOCKED_SIGNAL`.
    """

    if address is None:
        return None
    try:
        parsed = ipaddress.IPv4Address(str(address).strip())
    except (ipaddress.AddressValueError, ValueError):
        return None
    for block in blocks or ():
        raw = str(block.get("cidr") or "").strip()
        if not raw or ":" in raw:
            continue
        try:
            network = ipaddress.IPv4Network(raw, strict=False)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            continue
        if parsed in network:
            return raw
    return None


def blocked_by(address: Any, blocks: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The ingest-time refusal for a blocked address, or ``None``.

    This is what the engine consults before it writes anything. Returning the refusal rather
    than a boolean is deliberate: the response has to carry the console message the vendor
    documents, and a bare ``False`` would leave the page with nothing to show.
    """

    matched = ip_in_block(address, blocks)
    if matched is None:
        return None
    return {
        "blocked": True,
        "matched_range": matched,
        "console_signal": vocab.BLOCKED_SIGNAL,
        "propagation_minutes": vocab.IP_BLOCKLIST_PROPAGATION_MINUTES,
        "recordings_excluded": True,
        "heatmaps_excluded": True,
    }


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
#
# "labels (max 5 per recording)". The cap is on the recording, so it is checked against the
# labels that recording already holds and not against a per-room total.


def label_names(value: Any) -> list[str]:
    """Read a label list, trimmed and de-duplicated in first-seen order.

    Two submissions of the same label are one label, so a caller cannot be refused for a
    duplicate of a label it already holds. The evidence counts labels, not submissions.
    """

    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    seen: list[str] = []
    for item in items:
        name = str(item or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def require_label_capacity(existing: Iterable[str] | None, incoming: Any) -> list[str]:
    """The recording's full label list, or a refusal naming the sixth label.

    The refusal names the label and the cap rather than saying "too many", because the
    evidence gives the number and an operator who sees the number does not have to find it.
    """

    held = label_names(existing)
    requested = label_names(incoming)
    merged = list(held)
    for name in requested:
        if name not in merged:
            merged.append(name)
    if len(merged) <= vocab.MAX_LABELS_PER_RECORDING:
        return merged
    refused = merged[vocab.MAX_LABELS_PER_RECORDING]
    raise SessionConsentRefusal(
        f"A recording takes at most {vocab.MAX_LABELS_PER_RECORDING} labels.",
        {
            "labels": (
                f"{refused!r} is label {len(merged)} of {vocab.MAX_LABELS_PER_RECORDING}. "
                "Remove a label before adding another."
            )
        },
    )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
#
# Two windows and no third. "Clarity retains recordings for 30 days from the time of
# recording." and "Favorite recordings and randomly selected sample of recordings are
# retained for up to 9 months." So the window is a function of two inputs, and a favourite
# outlives the ordinary window.


def retention_days(recording: Mapping[str, Any] | None = None) -> int:
    """The window that applies to this recording: the ordinary one or the favourite one.

    A recording marked favourite gets the longer window, because the evidence extends
    retention to "Favorite recordings and randomly selected sample of recordings". The
    random sample is not modelled, because the specification does not say how the sample is
    drawn, and inventing a draw would be a house rule presented as a sourced one.
    """

    row = dict(recording or {})
    return (
        vocab.FAVOURITE_RETENTION_DAYS
        if _flag(row.get("favourite"))
        else (vocab.ORDINARY_RETENTION_DAYS)
    )


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = _token(value)
    return token in {"true", "1", "yes", "on", "favourite", "favorite"}


def is_true(value: Any) -> bool:
    """Read a stored flag.

    Public because the engine reads flags off records it did not write, and a caller that
    reached into ``_flag`` would be depending on a private name. The spellings accepted are
    the ones this workflow writes plus the spellings a JSON client is likely to send.
    """

    return _flag(value)


def expires_at(recorded: datetime, favourite: bool = False) -> datetime:
    """When this recording ages out, from its own recording instant."""

    days = vocab.FAVOURITE_RETENTION_DAYS if _flag(favourite) else vocab.ORDINARY_RETENTION_DAYS
    return recorded + timedelta(days=days)


def is_expired(
    recorded: datetime | None, *, favourite: bool = False, now: datetime | None = None
) -> bool | None:
    """True when the window has passed, or ``None`` when the instant is unreadable.

    ``None`` rather than ``False`` for an unreadable instant, because answering "not
    expired" for a recording whose instant cannot be read would keep it alive forever
    without saying so.
    """

    if recorded is None:
        return None
    moment = now or utcnow()
    return moment >= expires_at(recorded, favourite)


# --------------------------------------------------------------------------- #
# Share links
# --------------------------------------------------------------------------- #
#
# "sharing (guest links expire, team links don't)". Two link kinds, so two rules, and the
# difference is not a flag on one kind.


def link_kind(value: Any) -> str:
    """Read a link kind, defaulting to the kind that expires.

    The default is the safer of the two because a link nobody labelled is a link someone
    will send to a buyer, and the evidence attaches the expiry to that kind.
    """

    token = _token(value)
    if token in set(vocab.LINK_KINDS):
        return token
    if token == "":
        return vocab.GUEST
    raise SessionConsentRefusal(
        "A share link is either a guest link or a team link.",
        {"kind": f"Read {value!r}. Choose one of {', '.join(vocab.LINK_KINDS)}."},
    )


def link_expiry(
    kind: Any, *, created: datetime, expires_in_days: int | None = None
) -> datetime | None:
    """When a link stops working, or ``None`` for a team link.

    A team link never expires, because the evidence says "team links don't". A guest link
    needs a window, so a guest link with no window is a refusal rather than an open-ended
    guest link: the whole point of the kind is that it closes.
    """

    resolved = link_kind(kind)
    if resolved == vocab.TEAM:
        return None
    days = expires_in_days if expires_in_days is not None else vocab.DEFAULT_GUEST_LINK_DAYS
    if days <= 0:
        raise SessionConsentRefusal(
            "A guest link needs an expiry window greater than zero days.",
            {"expires_in_days": "Read a whole number of days above zero."},
        )
    return created + timedelta(days=int(days))


def link_is_live(
    kind: Any, *, created: datetime, expires_in_days: int | None = None, now: datetime | None = None
) -> bool:
    """True when the link still resolves. A team link always does."""

    expires = link_expiry(kind, created=created, expires_in_days=expires_in_days)
    if expires is None:
        return True
    return (now or utcnow()) < expires


# --------------------------------------------------------------------------- #
# Deletion granularity
# --------------------------------------------------------------------------- #
#
# Two quotes, one rule. "you can't delete or download specific recordings" and "You need to
# delete the entire project to delete user's data." So the only delete this workflow offers
# is the project purge, and a single-recording request is a refusal naming the alternative.


def require_project_granularity(action: str, recording_id: str | None = None) -> dict[str, Any]:
    """Refuse a per-recording delete or download, and name what is supported.

    Returns the refusal payload rather than raising, because the caller turns it into a
    response and the response has to carry the two evidence sentences. The recording id is
    included so a caller can show which recording was asked about.
    """

    supported = (
        action == vocab.PER_RECORDING_DELETE and vocab.SINGLE_RECORDING_DELETE_SUPPORTED
    ) or (action == vocab.PER_RECORDING_DOWNLOAD and vocab.SINGLE_RECORDING_DOWNLOAD_SUPPORTED)
    if supported:  # pragma: no cover - the evidence fixes both flags to False
        return {"supported": True, "action": action, "recording_id": recording_id}
    return {
        "supported": False,
        "action": action,
        "recording_id": recording_id,
        "error": "project_granularity_required",
        "detail": (
            "A single recording cannot be deleted or downloaded. Deletion is at project "
            "granularity."
        ),
        "remediation": "Delete the whole project to remove a visitor's data.",
        "granularity": vocab.PROJECT_GRANULARITY,
        "evidence": (
            "you can't delete or download specific recordings; You need to delete the entire "
            "project to delete user's data."
        ),
    }


# --------------------------------------------------------------------------- #
# Segments and the governance ceiling
# --------------------------------------------------------------------------- #
#
# The Recordings tab can "filter to a segment" and the specification does not enumerate the
# segments, so the dimensions are named here and the derivation recorded in the inferences
# register.


def segment_value(recording: Mapping[str, Any], dimension: str) -> Any:
    """The value of one segment dimension on a recording, or ``None``.

    An unknown dimension is refused rather than ignored, because a filter the product does
    not implement is a filter that would silently return everything.
    """

    token = _token(dimension)
    if token not in set(vocab.SEGMENT_DIMENSIONS):
        raise SessionConsentRefusal(
            "This workflow does not filter on that dimension.",
            {
                "dimension": (
                    f"Read {dimension!r}. This workflow filters on "
                    f"{', '.join(vocab.SEGMENT_DIMENSIONS)}."
                )
            },
        )
    row = dict(recording or {})
    if token == "date":
        recorded = coerce_instant(row.get("recorded_at"), "recorded_at")
        return recorded.date().isoformat() if recorded else None
    if token == "region":
        return row.get("region")
    return row.get(token)


def matches_segment(recording: Mapping[str, Any], dimension: Any, value: Any) -> bool:
    """True when the recording falls in this segment.

    An empty filter matches everything. That is a segment UI's own behaviour rather than a
    governance decision, and it is recorded here so the page and the rules agree.
    """

    if dimension in (None, "") and value in (None, ""):
        return True
    actual = segment_value(recording, str(dimension))
    return _token(actual) == _token(value)


def within_daily_ceiling(count: int, ceiling: int = vocab.MAX_SESSIONS_PER_PROJECT_PER_DAY) -> bool:
    """True while the room is inside the governance ceiling the specification names.

    The ceiling is a vendor limit, so it is reported rather than enforced as a hard stop:
    :func:`ceiling_state` names the state a reviewer needs to see.
    """

    return int(count) < int(ceiling)


def ceiling_state(
    count: int, ceiling: int = vocab.MAX_SESSIONS_PER_PROJECT_PER_DAY
) -> dict[str, Any]:
    """How close this project is to the researched ceiling, as a word plus the numbers.

    No colour carries this state and no tick appears: the ceiling is a vendor limit quoted
    from the specification, so the page says "within the ceiling" or "over the ceiling" and
    prints both numbers.
    """

    used = int(count)
    limit = int(ceiling)
    return {
        "count": used,
        "ceiling": limit,
        "remaining": max(0, limit - used),
        "within_ceiling": used <= limit,
        "label": "within the ceiling" if used <= limit else "over the ceiling",
    }


_CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$")


def looks_like_cidr(value: Any) -> bool:
    """True when a string is shaped like an IPv4 address or CIDR range.

    Used by the page to validate before it submits, so the error appears beside the input
    rather than in a banner at the top of the board.
    """

    return bool(_CIDR_RE.match(str(value or "").strip()))
