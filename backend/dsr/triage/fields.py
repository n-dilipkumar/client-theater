"""Scalar and field extraction, with no opinions about anyone's schema.

The product rule is that a record payload is arbitrary JSON in ``data`` and a
team adding a field must not need coordination. That rule has a consequence
this module exists to handle: *this* workflow cannot assume which key holds the
workspace's owner, or which key holds a CRM amount. So every concept is
resolved through a list of synonym paths, taken first-non-empty, and a value
that is not recognised is ``None`` rather than an error.

Two things are deliberately not tolerant:

* A **timestamp** is parsed strictly, because a recency filter that silently
  treats an unparseable date as "long ago" would move a workspace to the wrong
  end of a triage table. Unparseable means ``None``, and ``None`` is a value the
  filters and the sort both handle explicitly.
* A **number** is never coerced out of a string that does not read as a number.
  ``"not set"`` is a value, not a zero, and coercing it would invent a pipeline
  amount.

Nothing here reads or writes storage. It is pure, so the rules it encodes are
testable without a database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------- #
# Scalars
# --------------------------------------------------------------------------- #


def as_text(value: Any, default: str = "") -> str:
    """A trimmed string, or ``default`` for anything that is not one."""
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def as_number(value: Any) -> float | None:
    """A number, or ``None`` when the value is not one.

    ``None`` rather than ``0.0`` is the point. A missing opp amount and a
    zero-value opp amount are different facts about a deal, and collapsing them
    would make "Opp Amount is 0" match a workspace nobody has quoted yet.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on")
    return False


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #

#: Timestamp keys a record may use, in preference order. The seeded activity
#: records use ``occurred_at``; the store's own envelope uses ``created_at``; a
#: team may write any of the others.
TIME_KEYS: tuple[str, ...] = (
    "occurred_at",
    "at",
    "happened_at",
    "timestamp",
    "last_viewed_at",
    "last_seen_at",
    "updated_at",
    "created_at",
)


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO-8601 date or datetime into an aware UTC ``datetime``.

    A naive value is read as UTC rather than as local time. The store writes
    UTC, and guessing a local zone would make "in the last 7 days" depend on
    which machine ran the query - which is exactly the sort of quiet
    platform-dependence a triage table must not have.

    Returns ``None`` for anything unparseable, including a number, a dict and a
    malformed string. Callers treat ``None`` as "no time", never as "epoch".
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = as_text(value)
    if not text:
        return None
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: Any) -> str | None:
    """Normalise any timestamp-ish value to an ISO-8601 UTC string, or ``None``."""
    parsed = parse_time(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="seconds")


def now() -> datetime:
    return datetime.now(timezone.utc)


def days_ago(days: float, reference: datetime | None = None) -> datetime:
    """The instant ``days`` before ``reference`` (default: now)."""
    base = reference or now()
    return base - timedelta(days=float(days))


def utcnow_iso() -> str:
    return now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Field resolution
# --------------------------------------------------------------------------- #


def dig(data: Mapping[str, Any] | None, path: str) -> Any:
    """Read a dotted JSON path out of a payload, or ``None``.

    A path segment that is not a dict, or a key that is absent, yields ``None``
    rather than raising, because these payloads are open and a half-populated
    CRM object is normal.
    """
    current: Any = data or {}
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        if segment not in current:
            return None
        current = current[segment]
    return current


def first_text(data: Mapping[str, Any] | None, paths: Iterable[str], default: str = "") -> str:
    """The first non-empty string among ``paths``."""
    for path in paths:
        value = as_text(dig(data, path))
        if value:
            return value
    return default


def first_time(data: Mapping[str, Any] | None, paths: Iterable[str]) -> datetime | None:
    """The first parseable timestamp among ``paths``."""
    for path in paths:
        parsed = parse_time(dig(data, path))
        if parsed is not None:
            return parsed
    return None


def first_number(data: Mapping[str, Any] | None, paths: Iterable[str]) -> float | None:
    """The first numeric value among ``paths``."""
    for path in paths:
        value = as_number(dig(data, path))
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------- #
# Synonym tables
# --------------------------------------------------------------------------- #

#: Where a workspace's own properties may live. A team that calls the owner
#: ``sponsor`` is not wrong, it is just not the spelling this build reads first.
DOCK_NAME_PATHS = ("name", "title", "workspace_name")
DOCK_ACCOUNT_PATHS = ("account", "company", "organisation", "organization", "customer")
DOCK_OWNER_PATHS = ("owner", "owner_id", "assigned_to", "sponsor", "rep")
DOCK_TEAM_PATHS = ("team", "team_name", "group")
DOCK_STAGE_PATHS = ("stage", "status", "phase", "pipeline_stage")
DOCK_TEMPLATE_PATHS = ("template_id", "template", "workspace_template", "template_ref")

#: Where the CRM link's values may live. The provider is what gates a column, so
#: it has to be found before any of these are read.
CRM_PROVIDER_PATHS = ("provider", "crm", "source", "integration")
CRM_OPPORTUNITY_ID_PATHS = ("opportunity_id", "opp_id", "sf_opportunity_id", "opportunity")
CRM_DEAL_ID_PATHS = ("deal_id", "hs_deal_id")
CRM_ACCOUNT_ID_PATHS = ("account_id", "crm_account_id")

#: ``Deal Type`` appears under both HubSpot and Order forms in the research, so
#: the two must not share a synonym list. HubSpot's lives on the CRM record and
#: the order form's on the order form record; they are separate objects.
CRM_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "salesforce": {
        "opportunity_stage": ("opportunity_stage", "opp_stage", "stage_name", "opportunityStage"),
        "opportunity_created_date": (
            "opportunity_created_date",
            "opportunity_created_at",
            "opp_created_date",
            "opportunityCreatedDate",
        ),
        "opp_amount": ("opp_amount", "opportunity_amount", "amount", "oppAmount"),
        "opportunity_type": ("opportunity_type", "opp_type", "type", "opportunityType"),
    },
    "hubspot": {
        "deal_stage": ("deal_stage", "stage", "dealstage", "pipeline_stage"),
        "deal_type": ("deal_type", "dealtype", "type"),
        "deal_closed_date": ("deal_closed_date", "closed_date", "closedate", "close_date"),
        "deal_amount": ("deal_amount", "amount", "dealamount"),
    },
}

#: The provider strings this build recognises. Compared case-insensitively and
#: with the ``.`` of ``hubspot.com`` tolerated, because that is how a team writes
#: it when they paste the integration's own name.
CRM_PROVIDERS: tuple[str, ...] = ("salesforce", "hubspot")

ORDER_FORM_STATUS_PATHS = ("status", "order_form_status", "state", "stage")
ORDER_FORM_DEAL_TYPE_PATHS = ("deal_type", "order_form_deal_type", "type", "order_type")

#: The activity action names counted as a *view* for the "Views" column and for
#: "Last Client View". The research's own event vocabulary for this domain names
#: ``workspace.viewed``, ``workspace.page.viewed`` and ``workspace.file.viewed``,
#: and the core seed uses ``viewed``, so both spellings are read.
VIEW_ACTIONS: tuple[str, ...] = (
    "viewed",
    "view",
    "page_viewed",
    "file_viewed",
    "workspace.viewed",
    "workspace.page.viewed",
    "workspace.file.viewed",
)

ACTIVITY_ACTION_PATHS = ("action", "event", "activity", "kind", "type")


def normalise_provider(value: Any) -> str:
    """``"Salesforce"``, ``"hubspot.com"`` and ``" salesforce "`` all give ``salesforce``."""
    text = as_text(value).lower()
    if not text:
        return ""
    for provider in CRM_PROVIDERS:
        if text == provider or text.startswith(f"{provider}.") or text.startswith(f"{provider}_"):
            return provider
    return text


def is_view_action(action: Any) -> bool:
    return as_text(action).strip().lower() in VIEW_ACTIONS


def to_list(value: Any) -> list[Any]:
    """Coerce a scalar-or-list into a list, treating ``None`` as empty."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def as_sequence(value: Any) -> Sequence[Any]:
    return to_list(value)
