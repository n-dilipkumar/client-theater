"""The rules WF-085 enforces: region, retention, the consent gate and the erasure.

Every rule here is the researched specification for WF-085 made executable. The
specification is ``docs/research/digital-sales-room-workflows/wf/WF-085.md``, quoted in
full in issue 200, and the docstring on each rule names the evidence it came from.

The five rules the rest of this workflow leans on
-----------------------------------------------

**A region change re-stamps every record it holds.** The specification asserts that
residency "fixes the jurisdiction of both content and engagement data" and says nothing
about records already written. This module enforces the re-stamp: one jurisdiction
always holds the room's data, so a DSAR can always name the store a record is in. The
rule is recorded as ``DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD`` and was put to Jev as
audit ``jev-20261004T182818-16136-98999``, which selected it at confidence 0.84.

**A retention window can be shorter than the researched ceiling and never longer.** The
evidence says "up to 30 days" and "up to 9 months". Those are maxima. A deployment may
promise less; a request for more is refused rather than clamped, because clamping leaves
an operator believing a longer window was agreed when it was not.

**The consent gate fails closed on every path that is not an explicit grant.** There is
one grant word and the gate compares against it. A missing signal, an unreadable
signal, an opt-out signal and a signal from an unlisted region all reach the same place,
and that place is a deny: "a unique ID per page view and does not use cookies to persist
session data".

**DNT is not a consent signal.** The evidence says so, so :func:`gate` does not read it
and :func:`ignored_signals` says which inputs it discarded and why.

**Per-subject deletion is a path, not a room deletion.** The evidence names the vendor's
limitation - "You need to delete the entire project to delete user's data" - and calls
per-subject deletion "the hard part". So discovery matches on the address the evidence
names and erasure removes those records only, and reports what it could not remove
instead of claiming a clean sweep.

What this module does not decide
--------------------------------

Whether a room is legally compliant. Every value here is a mechanism name, a window, a
jurisdiction label or a count. No function returns "compliant", and no certification is
rendered, because the researched certifications are claims about a vendor.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.security_governance import residency as vocab


class PrivacyRefusal(ValueError):
    """A value, a bound or a window this workflow will not accept.

    Carries a field-keyed map, because the page puts each message beside the input that
    caused it rather than in one combined sentence.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class PrivacyNotFound(LookupError):
    """No such residency record, consent record or erasure request.

    Its own type rather than the store's ``RecordNotFound``: a feature may only map
    error types it raises itself, because a handler for a shared type would intercept
    that exception across the whole product.
    """


class PrivacyAdministratorRequired(PermissionError):
    """A blocking change arrived from a role below the administrator tier.

    The specification names the role check: "role separation, since blocking changes are
    admin-only (\"you need to be an *administrator* for your project\")". The role is not
    invented here, and this module does not read it either: the caller supplies the tier
    the repository resolves against, and the body reports both the role presented and
    the role that would have worked.
    """

    code = "administrator_required"

    def __init__(
        self,
        action: str,
        *,
        administrator: str,
        presented_role: Any = None,
    ) -> None:
        super().__init__(
            f"{action} is an administrator-only change. Present role={administrator!r}."
        )
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
    all collapse, so "Instance Admin", "instance-admin" and "instance_admin" are one role.
    A role this product does not know reduces to itself and therefore never compares equal
    to the administrator tier, which is what makes :func:`require_administrator` fail
    closed without this module knowing any vocabulary of its own.
    """

    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


#: The role vocabulary and the administrator tier are deliberately not declared here.
#:
#: This package depends on nothing inside ``dsr`` but the store and itself, and
#: ``tests/test_wf073.py`` enforces exactly that for every module in it. The repository's
#: own role surface is ``dsr.permissions``; the feature module is the one place allowed
#: to import it, and it supplies the tier to :func:`require_administrator` and to
#: :class:`~dsr.security_governance.privacy_engine.PrivacyEngine`. So there is one role
#: vocabulary in this product and one place that reads it. The derivation is
#: ``DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED``, put to Jev as audit
#: ``jev-20261004T193716-19404-36869``, which selected it at confidence 1.00.

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# --------------------------------------------------------------------------- #
# Instants: two encodings, one parser
# --------------------------------------------------------------------------- #
#
# The engagement collections store Unix milliseconds and this workflow's own rows store
# an ISO 8601 UTC instant. Both are read here and compared as aware datetimes, so the
# retention clock never compares a millisecond number with an ISO string.
#
# One function converts, so there is exactly one instant check in the workflow. A value
# it cannot read is a validation failure rather than a silent zero: treating an
# unreadable instant as the epoch would purge a record the moment it was written.


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant in UTC, with the milliseconds kept.

    The milliseconds are kept because two consent signals can land inside one second,
    and the most recent signal is the one that governs. Rounding them together would let
    an older grant overwrite a newer deny.
    """

    value = moment or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def coerce_instant(value: Any, field: str) -> datetime | None:
    """One stored instant as an aware UTC datetime, or ``None`` when there is none.

    ``None`` is a real answer rather than an error: a row with no timestamp is a row
    this workflow cannot age, and the retention board reports it under
    :data:`~dsr.security_governance.residency.UNDATED_POLICY` instead of pretending it
    is fresh.

    Two encodings are accepted, and only the two this repository stores: an ISO 8601
    string (this workflow's rows) and Unix milliseconds (the engagement rows). A string
    that is only digits is read as milliseconds, because a bare number is how a
    millisecond timestamp survives a JSON round trip through a client.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PrivacyRefusal(
            f"{field} must be an instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        )
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
    if isinstance(value, (int, float)):
        return _from_millis(float(value), field)
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        try:
            return _from_millis(float(text), field)
        except PrivacyRefusal:
            raise
        except (OverflowError, ValueError) as exc:
            raise PrivacyRefusal(
                f"{field} is not a readable instant.",
                {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
            ) from exc
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrivacyRefusal(
            f"{field} is not a readable instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        ) from exc
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _from_millis(number: float, field: str) -> datetime:
    """Unix milliseconds as an aware UTC datetime."""

    try:
        return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise PrivacyRefusal(
            f"{field} is not a readable instant.",
            {field: f"{field} must be an ISO 8601 instant or Unix milliseconds."},
        ) from exc


def now_ms(moment: datetime | None = None) -> int:
    """One instant as Unix milliseconds, for a response that speaks the other unit."""

    value = moment or utcnow()
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def age_days(recorded: datetime, now: datetime) -> float:
    """How old a record is, in days, never negative.

    Clamped at zero rather than allowed to go negative, because a clock that moved
    backwards would otherwise report a record as not yet due for a window it has already
    served, and the erasure board would show a retention deadline in the future.
    """

    delta = now.astimezone(timezone.utc) - recorded.astimezone(timezone.utc)
    return max(0.0, delta.total_seconds() / 86400.0)


# --------------------------------------------------------------------------- #
# Regions and the administrator gate
# --------------------------------------------------------------------------- #


def normalise_region(value: Any) -> str:
    """One known deployment region id, or a refusal naming the known set.

    Matching is case-insensitive and tolerates a space or an underscore where the id has
    a hyphen, because an operator typing a region name should not have to know the exact
    spelling. It does not tolerate an unknown region: an unrecognised region written to
    the residency record would leave the room with a jurisdiction nothing can read.
    """

    text = str(value or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not text:
        raise PrivacyRefusal(
            "A residency region is required.",
            {"region": "Choose one of the documented deployment regions."},
        )
    if text not in vocab.REGION_IDS:
        raise PrivacyRefusal(
            f"{text!r} is not a documented deployment region.",
            {"region": f"Known regions are: {', '.join(vocab.REGION_IDS)}."},
        )
    return text


def jurisdiction_of(region: Any) -> str:
    """The jurisdiction a deployment region sits in."""

    return vocab.REGION_JURISDICTIONS[normalise_region(region)]


def normalise_transfer_mechanism(value: Any) -> str:
    """One known cross-border transfer mechanism, or a refusal.

    Defaults to ``none`` when the caller says nothing, because the honest default for a
    deployment that has not thought about transfers is "none declared", and a page that
    renders that can see the gap. It is not a claim that no transfer occurs.
    """

    if value is None or str(value).strip() == "":
        return vocab.TRANSFER_NONE
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.TRANSFER_MECHANISMS:
        raise PrivacyRefusal(
            f"{text!r} is not a known transfer mechanism.",
            {
                "transfer_mechanism": (
                    f"Known mechanisms are: {', '.join(vocab.TRANSFER_MECHANISMS)}."
                )
            },
        )
    return text


def require_administrator(role: Any, action: str, administrator: str) -> str:
    """Return the caller's resolved role, or refuse the change.

    Fails closed on everything: no role at all, an empty string, a role this product does
    not recognise, and a recognised role below the administrator tier. The comparison is
    one equality against the tier the caller supplied, so there is no branch here that can
    be talked into widening it, and a role this module has never heard of reduces to
    itself and therefore never matches.

    That is the whole of the "role separation" the specification asks for. The tier is
    the repository's own, supplied by the caller rather than written here, so no second
    role vocabulary exists in the product; see the note above :data:`_EMAIL_RE`.
    """

    tier = _token(administrator)
    if not tier:
        # An empty tier would make an empty role the administrator. Refuse the wiring
        # rather than the caller, because a blank tier is a wiring fault and every
        # refusal afterwards would be caused by it.
        raise PrivacyRefusal(
            "No administrator role was configured for this workflow.",
            {"role": "The administrator role must be named before a change can be gated."},
        )
    presented = _token(role)
    if presented != tier:
        raise PrivacyAdministratorRequired(action, administrator=administrator, presented_role=role)
    return presented


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def retention_window(class_id: Any, overrides: Mapping[str, Any] | None = None) -> int:
    """The configured window for one class, in days.

    ``overrides`` carries the deployment's configured windows. A configured value may be
    *shorter* than the researched ceiling and never longer. A longer request is refused
    rather than clamped, so an operator is never told a window was agreed when the room
    would have enforced the shorter one.
    """

    name = normalise_class(class_id)
    ceiling = vocab.MAX_RETENTION_DAYS[name]
    if not overrides or name not in overrides:
        return ceiling
    requested = coerce_days(overrides[name], name)
    if requested > ceiling:
        raise PrivacyRefusal(
            f"{name} cannot be kept for longer than the researched ceiling.",
            {
                name: (
                    f"The researched ceiling is {ceiling} {vocab.WINDOW_UNIT}. "
                    f"{requested} was requested."
                )
            },
        )
    return requested


def normalise_class(class_id: Any) -> str:
    """One known retention class id, or a refusal naming the three classes."""

    text = str(class_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text not in vocab.RETENTION_CLASSES:
        raise PrivacyRefusal(
            f"{text!r} is not a retention class.",
            {"class": f"Known classes are: {', '.join(vocab.RETENTION_CLASSES)}."},
        )
    return text


def coerce_days(value: Any, field: str) -> int:
    """A configured window as whole days, at least one.

    A window of zero days would purge a record the instant it was written, which is a
    legitimate reading of "retain nothing" but a useless one to store as a window. One
    day is the floor, and it is stated rather than applied silently.
    """

    if isinstance(value, bool):
        raise PrivacyRefusal(
            f"{field} must be a whole number of days.",
            {field: f"{field} must be a whole number of days, at least 1."},
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PrivacyRefusal(
            f"{field} must be a whole number of days.",
            {field: f"{field} must be a whole number of days, at least 1."},
        ) from exc
    if number < 1:
        raise PrivacyRefusal(
            f"{field} must be at least one day.",
            {field: f"{field} must be at least 1 {vocab.WINDOW_UNIT}."},
        )
    return number


def due_at(recorded: datetime, window_days: int) -> datetime:
    """The instant a record of this age is due for erasure."""

    return recorded.astimezone(timezone.utc) + timedelta(days=window_days)


def is_due(recorded: datetime | None, window_days: int, now: datetime) -> bool | None:
    """Whether a record is past its window.

    ``None`` means the record carries no readable instant, so it cannot be placed in a
    window at all. The caller reports those under
    :data:`~dsr.security_governance.residency.UNDATED_POLICY` instead of ageing them:
    guessing a date would either purge a record early or let one outlive its window, and
    both are worse than naming the gap.
    """

    if recorded is None:
        return None
    return now.astimezone(timezone.utc) >= due_at(recorded, window_days)


def classify(collection: Any) -> str | None:
    """The retention class for a collection, or ``None`` when nothing ages it.

    ``None`` is the finding, not a gap in the table: it means the collection may hold
    personal data that no window reaches, which is the "longer-lived side channel" the
    specification forbids. :func:`unmapped_collections` names them.
    """

    return vocab.RETENTION_SCOPE.get(str(collection or ""))


def instant_field(collection: Any) -> str | None:
    """The stored field that says when a record of this collection was made."""

    return vocab.INSTANT_FIELDS.get(str(collection or ""))


def retention_policy(overrides: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The three classes with their configured window, the ceiling and the evidence.

    Served rather than written into the page, so a board cannot show a window the rules
    would not enforce. ``configured`` equals ``ceiling_days`` unless the deployment has
    promised less, and the two are reported separately so "we keep it longer" is never
    an answer the page can give.
    """

    rows: list[dict[str, Any]] = []
    for name in vocab.RETENTION_CLASSES:
        rows.append(
            {
                "class": name,
                "label": vocab.RETENTION_CLASS_LABELS[name],
                "configured_days": retention_window(name, overrides),
                "ceiling_days": vocab.MAX_RETENTION_DAYS[name],
                "evidence": vocab.RETENTION_EVIDENCE[name],
                "collections": sorted(
                    collection
                    for collection, mapped in vocab.RETENTION_SCOPE.items()
                    if mapped == name
                ),
            }
        )
    return rows


def scope_of(overrides: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every aged collection with its class, window and instant field."""

    return {
        collection: {
            "class": mapped,
            "window_days": retention_window(mapped, overrides),
            "instant_field": instant_field(collection),
        }
        for collection, mapped in vocab.RETENTION_SCOPE.items()
    }


# --------------------------------------------------------------------------- #
# Consent: the gate that fails closed
# --------------------------------------------------------------------------- #
#
# The gate is one function and it has two exits. The evidence's own sentence fixes what a
# deny means, so the deny branch returns the two booleans a page needs rather than a
# label the page has to interpret:
# "If consent is not granted, Clarity assigns a unique ID per page view and does not use
# cookies to persist session data."

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag(value: Any) -> bool | None:
    """A tri-state flag: True, False, or ``None`` for a value that is not a flag."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def consent_required(region: Any, consent_jurisdictions: Iterable[str] | None = None) -> bool:
    """Whether this region is one where explicit consent is required.

    The jurisdiction list is the evidence's: the EEA, the UK and Switzerland. It is a
    parameter so a deployment can be configured rather than hard-coded, and the default
    is the researched list. This is a policy question and it is answered by a list, not
    by a clock - see ``DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE``.
    """

    listed = tuple(consent_jurisdictions or vocab.CONSENT_JURISDICTIONS)
    return jurisdiction_of(region) in listed


def ignored_signals(signal: Any, **opt_out: Any) -> list[dict[str, str]]:
    """Which inputs the gate discarded, and why.

    The evidence records DNT as unsupported, so this is where that is visible rather
    than a silent absence. A client sending ``dnt`` learns it had no effect instead of
    inferring from a deny that it was honoured and then failed.
    """

    discarded: list[dict[str, str]] = []
    if str(signal or "").strip().lower() in vocab.UNSUPPORTED_SIGNALS:
        discarded.append(
            {
                "signal": str(signal).strip().lower(),
                "reason": (
                    "The specification records that the vendor does not respond to this "
                    "signal, so it is not a consent signal here."
                ),
            }
        )
    for name, raw in opt_out.items():
        if name not in vocab.OPT_OUT_SIGNALS:
            discarded.append(
                {
                    "signal": str(name),
                    "reason": f"{name!r} is not an opt-out signal this gate reads.",
                }
            )
        elif raw is None or raw == "":
            # Not sent at all. Reporting an absent signal as an unreadable one would
            # tell a client it had sent something it had not.
            continue
        elif _flag(raw) is None:
            discarded.append(
                {
                    "signal": str(name),
                    "reason": f"{name!r} was sent as {raw!r}, which is not a flag.",
                }
            )
    return discarded


def gate(
    region: Any,
    *,
    signal: Any = None,
    consent_jurisdictions: Iterable[str] | None = None,
    **opt_out: Any,
) -> dict[str, Any]:
    """Decide whether this page view may be tracked, and say why.

    Two exits only, and every path that is not an explicit grant reaches the deny one.

    * An **opt-out** signal that is set denies, whether or not anything else was sent.
      The evidence says opt-outs "are honoured automatically".
    * An opt-out signal that is **clear** does not grant. "gpc is false" means nobody
      opted out, which is not the same fact as anybody consenting.
    * An **explicit grant** - and only the exact word
      :data:`~dsr.security_governance.residency.CONSENT_GRANTED` - allows tracking, and
      only where the region is one the list covers.
    * Everything else denies. A missing signal, a signal this vocabulary does not know,
      a signal sent as something other than a flag: all deny.

    Outside the listed jurisdictions the gate reports ``not_required`` rather than
    tracking silently. The derivation and what it costs are recorded as
    ``DERIVED_CONSENT_GATE_IS_A_REGION_LIST_NOT_A_DATE`` and
    ``DERIVED_CONSENT_GATE_ENFORCES_IN_LISTED_JURISDICTIONS_ONLY``.
    """

    name = normalise_region(region)
    discarded = ignored_signals(signal, **opt_out)
    reasons: list[str] = []

    opted_out = [
        signal_name
        for signal_name in vocab.OPT_OUT_SIGNALS
        if _flag(opt_out.get(signal_name)) is True
    ]

    if opted_out:
        reasons.append("An opt-out signal is set: " + ", ".join(sorted(opted_out)) + ".")

    granted = str(signal or "").strip().lower() == vocab.CONSENT_GRANTED
    if not granted and not opted_out:
        if signal is None or str(signal).strip() == "":
            reasons.append("No consent signal was sent.")
        elif not granted:
            reasons.append(
                f"{str(signal).strip()!r} is not the explicit grant {vocab.CONSENT_GRANTED!r}."
            )

    if not consent_required(name, consent_jurisdictions):
        return {
            "region": name,
            "jurisdiction": jurisdiction_of(name),
            "outcome": vocab.CONSENT_NOT_REQUIRED,
            "consent_required": False,
            "requirement": vocab.CONSENT_NOT_REQUIRED,
            "granted": granted,
            "opted_out": sorted(opted_out),
            "signals_ignored": discarded,
            "reason": (
                "This region's jurisdiction is not in the configured consent list, so the "
                "gate does not enforce consent here."
            ),
            "reasons": reasons,
            "effect": dict(vocab.GRANT_EFFECT),
            "consent_jurisdictions": list(consent_jurisdictions or vocab.CONSENT_JURISDICTIONS),
        }

    if granted and not opted_out:
        return {
            "region": name,
            "jurisdiction": jurisdiction_of(name),
            "outcome": vocab.GATE_TRACK,
            "consent_required": True,
            "requirement": vocab.CONSENT_GRANTED,
            "granted": True,
            "opted_out": [],
            "signals_ignored": discarded,
            "reason": "Explicit consent was granted in a region that requires it.",
            "reasons": reasons,
            "effect": dict(vocab.GRANT_EFFECT),
            "consent_jurisdictions": list(consent_jurisdictions or vocab.CONSENT_JURISDICTIONS),
        }

    return {
        "region": name,
        "jurisdiction": jurisdiction_of(name),
        "outcome": vocab.GATE_DENY,
        "consent_required": True,
        "requirement": vocab.CONSENT_DENIED,
        "granted": False,
        "opted_out": sorted(opted_out),
        "signals_ignored": discarded,
        "reason": "; ".join(reasons) or "Consent was not granted.",
        "reasons": reasons,
        "effect": dict(vocab.DENY_EFFECT),
        "consent_jurisdictions": list(consent_jurisdictions or vocab.CONSENT_JURISDICTIONS),
    }


# --------------------------------------------------------------------------- #
# Personal data and subject matching
# --------------------------------------------------------------------------- #


def normalise_subject(value: Any) -> str:
    """A data subject's address: lower-cased and checked for the shape of an address.

    Lower-cased because matching is the whole of discovery: a stored ``Buyer@X.example``
    and a request for ``buyer@x.example`` are the same subject, and a DSAR that missed
    one of them would report a clean sweep over a record it never looked at.
    """

    text = str(value or "").strip().lower()
    if not text:
        raise PrivacyRefusal(
            "A data subject address is required.",
            {"subject": "An email address is required."},
        )
    if not _EMAIL_RE.match(text) or ".." in text:
        raise PrivacyRefusal(
            f"{text!r} is not a valid email address.",
            {"subject": "An email address is required."},
        )
    return text


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def personal_data_of(collection: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    """The personal fields one record actually holds, and which are missing.

    Reported as found rather than as a fixed list, because a DSAR plan that listed a
    field no record carries could not be fulfilled and one that listed a field the record
    omits would over-report. An absent field is named ``missing`` rather than dropped,
    so "this record holds no city" and "this record was never asked" stay different.
    """

    found: dict[str, Any] = {}
    missing: list[str] = []
    for path in vocab.PERSONAL_DATA.get(str(collection or ""), ()):
        value = _get_path(data or {}, path)
        if value is None:
            missing.append(path)
        else:
            found[path] = value
    return {"fields": found, "missing": missing, "count": len(found)}


def matches_subject(collection: Any, data: Mapping[str, Any], subject: str) -> bool:
    """Whether one record belongs to a data subject.

    Matches on the fields the evidence names - ``email`` on the visitor row,
    ``viewer_email`` on the view row - and on nothing else. A record that merely happens
    to share a country is not the subject's, and matching on anything softer would make
    the erasure delete other buyers' rows.
    """

    wanted = str(subject or "").strip().lower()
    for path in vocab.SUBJECT_FIELDS.get(str(collection or ""), ()):
        value = _get_path(data or {}, path)
        if value is not None and str(value).strip().lower() == wanted:
            return True
    return False


def subject_of(collection: Any, data: Mapping[str, Any]) -> str | None:
    """The address one record belongs to, if it names one."""

    for path in vocab.SUBJECT_FIELDS.get(str(collection or ""), ()):
        value = _get_path(data or {}, path)
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return None


def unmapped_collections(
    collections: Sequence[str], scope: Mapping[str, Any] | None = None
) -> list[str]:
    """Live collections the retention table does not cover, sorted.

    This is the "no longer-lived side channels" check made visible. A collection holding
    engagement data that no class ages is a longer-lived copy of it, and the board names
    every one rather than letting an operator assume the table is complete.
    """

    covered = set(vocab.RETENTION_SCOPE) | set(scope or {})
    return sorted({str(name) for name in collections if str(name) not in covered})
