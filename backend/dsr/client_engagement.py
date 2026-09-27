"""WF-024: roll up client engagement and multi-threading portfolio-wide.

The domain behind Dock's **Reports -> Client Engagement** report, plus the two
reports the research names in the same family (Team Usage, Implementations).
The research document is the specification:
``docs/research/digital-sales-room-workflows/wf/WF-024.md``
(source: ``docs/research/raw/analytics-intent.md`` section 9).

The researched flow, as the spec states it
------------------------------------------
1. Open the **Reports** tab, then **Client Engagement**.
2. Read the tiles: **Total client views**, **Total client actions**,
   **Average unique clients per workspace**, **Client views over time**,
   **Most engaged clients**.
3. Click a metric tile to expand the full list of accounts and their
   engagement; sort by any column.
4. Filter by date range, owners, teams.
5. Read out loud: is the account being multi-threaded, and who is the champion?

Sourced behaviour this module implements
----------------------------------------
* **The report spans every workspace, automatically.** "The Client Engagement
  report analyzes external activity across ALL workspaces in your Dock
  instance." / "Reports in Dock automatically show holistic data across all
  workspaces." There is no workspace selector on this report; the one place a
  single workspace is in scope is
  :func:`workspace_engagement`, which is the same rollup narrowed to it.
* **Client activity only.** The report analyses *external* activity, and the
  data source is the "external vs internal user distinction". Every metric here
  counts events whose audience classifies as external; internal reps' own
  activity is counted separately and reported as such rather than being silently
  folded into client numbers.
* **Total client actions.** "Counts how many times a client has interacted with
  a space. We think of this as clicking into pages, embedded content, etc."
* **A view is a kind of action.** That same sentence puts page clicks inside the
  action count, so ``actions`` is every external interaction and ``views`` is the
  subset of actions that were a view. The report exposes both plus the
  difference, so the relationship is visible rather than assumed. (Jev chose
  this reading over disjoint counters at 0.69, audit
  ``jev-20260927T061954-25528-94194``.)
* **Total client actions expands to the individuals.** "Click into the cell to
  expand upon who these individuals are!" The account list carries the people
  behind every number, and :func:`account_detail` is that expansion on its own.
* **Average unique clients per workspace.** Read literally, over *every*
  workspace in scope: a workspace nobody ever opened contributes a zero and
  drags the average down, which is what a portfolio coverage report is for.
  (Jev chose this over an engaged-only denominator at 0.99, audit
  ``jev-20260927T061954-25528-94901``.)
* **Multi-threading is buyer-side.** "This report tracks how well you're
  multithreading accounts, helps you identify champions." The tile that exists
  counts unique *clients* per workspace and the expandable cell lists *client*
  individuals, so the thread count is distinct client people and the champion is
  the most engaged of them. (Jev chose this over a rep-side reading at 0.99,
  audit ``jev-20260927T061954-25528-94530``.)
* **The three filters.** "You can filter the report down by date range, owners,
  and/or teams." Owners and teams are read off the workspace record; a team
  that no record carries cannot be filtered on, so :func:`filters` publishes
  the owners and teams actually in scope for a client to render pickers from.
* **Sort by any column.** Every column of the account list is a sort key, and an
  unknown key is refused rather than silently ignored.
* **Team Usage.** "How actively is your team using Dock?" - the internal mirror
  of the same external/internal distinction.
* **Implementations Report.** Total/Active/Completed implementations, time to
  completion average, % completed on time, implementations by owner, customer
  views/actions, most engaged customers.

Design inferences
-----------------
The research fixes the report's vocabulary and its scope; it does not fix the
arithmetic. These are deliberate, documented decisions, all overridable in the
``client_engagement_config`` record so no code change is needed:

``audience classification``
    Nothing in this product's data declares who is a client. The order is
    explicit-field first (``user_type``, ``external``, ...), then a configured
    list of internal identities and domains, then a configurable default. An
    event with no identifiable person is ``unknown`` and is counted in
    ``coverage``, never quietly counted as a client action.
``multi_thread_floor``
    Two distinct engaged clients make an account multi-threaded. One client
    means the whole deal rests on a single person, which is the risk the report
    exists to surface, so the default is 2.
``champion``
    The client with the most actions in the account, ties broken by views, then
    recency, then name. The research promises a champion, not an algorithm.
``chart window``
    "Client views over time" needs an axis. With no explicit ``from`` the series
    starts at the earliest in-scope event, so the chart shows the activity that
    exists rather than a fixed window that is mostly empty.
``account key``
    Account names are display strings, and an account is addressed through a
    path segment, so each name gets a deterministic path-safe key. Two accounts
    whose names collapse to the same key are disambiguated in name order.
``at-risk implementations``
    The spec calls the Implementations Report a tool "for at-risk delivery
    tracking" without defining at-risk. Derived here as: active and past due,
    active with no committed date, or completed after its due date. Urgency that
    is not risk - an implementation due next week - is reported as
    ``days_to_due`` rather than folded in, so the at-risk list stays a list a
    person has to act on.
``stale accounts``
    An account with client activity in scope but none for ``stale_after_days``
    reads as dormant. This is the portfolio-wide form of the "is this account
    being multi-threaded" question.

Schema flexibility
------------------
Field *locations* are discovered, not declared, in the house style of
:mod:`dsr.analytics`: every concept lists its synonyms in the config and a team
may override any of them without a migration. A field nobody has heard of is
still carried through untouched in the ``data`` of the events returned. Nothing
here writes a metric row - the report is computed on read, so two hundred
features adding a field to an event needs no coordination with anyone.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

COLLECTION_ROOM = "room"
COLLECTION_ACTIVITY = "activity"
COLLECTION_CONFIG = "client_engagement_config"
CONFIG_KEY = "default"

#: The five tiles, in the order the research lists them. ``expand`` names the
#: account-list sort key a click on the tile should carry, which is how
#: "click a metric tile to expand the full list of accounts" is served as data
#: rather than left to the client to guess.
TILES = (
    ("client_views", "Total client views", "views"),
    ("client_actions", "Total client actions", "actions"),
    ("avg_unique_clients", "Average unique clients per workspace", "unique_clients"),
    ("client_views_over_time", "Client views over time", "views"),
    ("most_engaged_clients", "Most engaged clients", "actions"),
)

AUDIENCE_EXTERNAL = "external"
AUDIENCE_INTERNAL = "internal"
AUDIENCE_UNKNOWN = "unknown"

#: ``AuditedDatabase.list`` pages with LIMIT/OFFSET, so scanning in pages of
#: this size keeps aggregates correct well past the per-call cap.
_PAGE = 1000

#: Dense time series stop being readable past this many points, so the grain
#: widens rather than the response becoming a ten-thousand-element array.
MAX_CHART_POINTS = 400

DEFAULT_CONFIG: dict[str, Any] = {
    "key": CONFIG_KEY,
    "thresholds": {
        "chart_days": 30,
        "multi_thread_floor": 2,
        "stale_after_days": 30,
        "client_limit": 10,
        "account_limit": 0,
        "max_chart_points": MAX_CHART_POINTS,
    },
    "audience": {
        # Sourced: the report "analyzes external activity", and the data source is
        # the external vs internal user distinction. The field names are ours.
        "user_type_fields": ["user_type", "audience", "member_type", "user_class", "kind"],
        "external_values": ["external", "client", "buyer", "guest", "contact", "prospect"],
        "internal_values": ["internal", "rep", "member", "employee", "staff", "team", "seller"],
        "bool_fields": ["external", "is_external", "internal", "is_internal"],
        # An explicit list of internal identities, for a dataset that marks
        # nothing. Empty by default: nothing is assumed to be internal.
        "internal_people": [],
        # Suffixes on the address domain, e.g. "ourco.example".
        "internal_domains": [],
        # Whether a person the data does not classify is counted as a client.
        # True by default: the report is about clients, and a person whose role
        # is unknown is far more often a buyer than a seller.
        "unknown_is_external": True,
    },
    "taxonomy": {
        # A view is a kind of action, so this list selects which actions are also
        # counted as views. An action nobody listed is still counted as an
        # action, so a new kind needs no configuration change to appear.
        "view_actions": [
            "viewed",
            "view",
            "page_viewed",
            "workspace_viewed",
            "previewed",
            "opened",
            "opened_link",
        ],
    },
    "room": {
        "name_fields": ["name", "title", "account"],
        "account_fields": ["account", "company", "organisation", "organization", "customer"],
        "owner_fields": ["owner", "sponsor", "assigned_to", "rep"],
        "team_fields": ["team", "teams", "group", "segment", "pod", "region"],
    },
    "event": {
        "person_fields": ["person", "user", "visitor", "email", "user_email", "actor", "by"],
        "action_fields": ["action", "event", "activity", "kind", "type"],
        "target_fields": ["target", "document", "page", "page_title"],
        "timestamp_fields": ["occurred_at", "at", "happened_at", "timestamp"],
    },
    "implementation": {
        "collection": "implementation",
        "account_fields": ["account", "customer", "company"],
        "name_fields": ["name", "title"],
        "status_fields": ["status", "state"],
        "owner_fields": ["owner", "assigned_to", "lead"],
        "started_fields": ["started_at", "start_date", "kickoff", "began_at"],
        "completed_fields": ["completed_at", "finished_at", "done_at", "end_date"],
        "due_fields": ["due_at", "due_date", "target_date", "go_live", "go_live_date"],
        "active_states": ["active", "in_progress", "in progress", "onboarding", "started", "at_risk"],
        "completed_states": ["completed", "complete", "done", "delivered", "live"],
    },
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class EngagementReportError(ValueError):
    """A well-formed request asking for something this report will not do.

    A ``ValueError`` subclass so it is a domain type rather than a builtin: the
    feature maps it once in ``EXCEPTION_HANDLERS`` and a handler registered for a
    builtin would let this feature intercept exceptions raised anywhere in the
    product.
    """


class InvalidWindow(EngagementReportError):
    """The requested date range cannot mean anything."""


class InvalidSortColumn(EngagementReportError):
    """A sort key that is not a column of the account list."""


class UnknownWorkspace(LookupError):
    """Raised when a workspace id does not resolve to a live room record."""


class UnknownAccount(LookupError):
    """Raised when an account key does not resolve to an account in scope."""


# --------------------------------------------------------------------------- #
# Scalar helpers
#
# Deliberately local rather than imported from dsr.analytics. Two workflows
# that both need to read a timestamp out of an arbitrary JSON payload should not
# share one module's private coercion functions: the moment either needs a
# different reading of a field name, the other changes underneath it. Thirty
# lines of duplication is the cheaper of the two failures.
# --------------------------------------------------------------------------- #


def _as_text(value: Any, default: str = "") -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, bool):
        return default
    return str(value)


def _as_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "y", "on")
    return False


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _pick(data: Mapping[str, Any] | None, keys: Sequence[str], default: Any = None) -> Any:
    """First non-empty value among ``keys`` inside a JSON payload."""
    if not isinstance(data, Mapping):
        return default
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _pick_text(data: Mapping[str, Any] | None, keys: Sequence[str], default: str = "") -> str:
    return _as_text(_pick(data, keys), default)


def _pick_list(data: Mapping[str, Any] | None, keys: Sequence[str]) -> list[str]:
    """A field that may hold one value or several, always returned as a list."""
    value = _pick(data, keys)
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [text for text in (_as_text(item) for item in value) if text]
    text = _as_text(value)
    return [text] if text else []


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; date-only values are treated as UTC midnight."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_date_only(value: Any) -> bool:
    """True when a filter value names a day rather than an instant."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text.endswith(("Z", "z")) or "+" in text[10:] or text.count(":") > 0:
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _whole_days(delta: timedelta) -> int:
    """Whole days elapsed, so a just-now event is 0 rather than -1."""
    return max(0, int(delta.total_seconds() // 86400))


def _lower_set(values: Iterable[Any]) -> set[str]:
    return {text for text in (_as_text(value).lower() for value in values) if text}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge, so a partial config patch cannot drop sibling keys."""
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _config_id(key: str = CONFIG_KEY) -> str:
    return f"{COLLECTION_CONFIG}_{key}"


def load_config(store: RecordStore) -> dict[str, Any]:
    """Effective configuration: stored overrides layered onto the defaults.

    A missing config record is not an error; the defaults are the documented
    behaviour and are returned verbatim so a client can show them.
    """
    record = store.get(_config_id())
    if record is None:
        matches = store.find(COLLECTION_CONFIG, {"key": CONFIG_KEY}, limit=1)
        record = matches[0] if matches else None
    stored = (record or {}).get("data") or {}
    return _deep_merge(DEFAULT_CONFIG, stored if isinstance(stored, Mapping) else {})


def save_config(
    store: RecordStore,
    patch: Mapping[str, Any],
    *,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Merge a partial config patch and write it through the audited store.

    ``source`` is required rather than defaulted: only the HTTP layer knows its
    own path, and an audit row that cannot be traced back to the request that
    caused it is not an audit trail.
    """
    existing = store.get(_config_id())
    base = (existing or {}).get("data") or {}
    merged = _deep_merge(base if isinstance(base, Mapping) else {}, patch)
    merged["key"] = CONFIG_KEY
    if existing is None:
        return store.create(
            COLLECTION_CONFIG, merged, record_id=_config_id(), actor=actor, source=source
        )
    return store.update(_config_id(), merged, actor=actor, source=source)


def resolve_now(value: str | None) -> datetime:
    return _parse_dt(value) or datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


def _scan(store: RecordStore, collection: str, room_id: str | None = None) -> list[dict[str, Any]]:
    """Every live record in a collection, paged so nothing is truncated."""
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(
            collection,
            room_id=room_id,
            limit=_PAGE,
            offset=offset,
            order_by="id",
            descending=False,
        )
        records.extend(page)
        if len(page) < _PAGE:
            return records
        offset += _PAGE


@dataclass(frozen=True)
class Workspace:
    """One workspace, as the portfolio report reads it."""

    id: str
    name: str
    account: str
    owner: str
    teams: tuple[str, ...]
    created: datetime | None
    data: Mapping[str, Any]

    @property
    def has_owner(self) -> bool:
        return bool(self.owner)

    @property
    def has_teams(self) -> bool:
        return bool(self.teams)


def scan_workspaces(store: RecordStore, config: Mapping[str, Any]) -> list[Workspace]:
    """Every live workspace, with the account, owner and teams read by discovery."""
    room_config = config.get("room") or {}
    names = list(room_config.get("name_fields") or [])
    accounts = list(room_config.get("account_fields") or [])
    owners = list(room_config.get("owner_fields") or [])
    teams = list(room_config.get("team_fields") or [])

    found: list[Workspace] = []
    for record in _scan(store, COLLECTION_ROOM):
        data = record.get("data") or {}
        found.append(
            Workspace(
                id=str(record.get("id")),
                name=_pick_text(data, names) or str(record.get("id")),
                account=_pick_text(data, accounts),
                owner=_pick_text(data, owners),
                teams=tuple(_pick_list(data, teams)),
                created=_parse_dt(record.get("created_at")),
                data=data,
            )
        )
    return found


def require_workspace(store: RecordStore, room_id: str) -> dict[str, Any]:
    record = store.get(room_id)
    if record is None or record.get("collection") != COLLECTION_ROOM:
        raise UnknownWorkspace(room_id)
    return record


# --------------------------------------------------------------------------- #
# Audience: who counts as a client
# --------------------------------------------------------------------------- #


def classify_audience(
    person: str, data: Mapping[str, Any] | None, config: Mapping[str, Any]
) -> str:
    """External, internal, or unknown - the report's central distinction.

    Sourced: the report "analyzes external activity" and its data source is the
    "external vs internal user distinction". The resolution order is ours:

    1. an explicit field on the event, because that is a decision somebody made;
    2. a configured internal identity or address domain, because a dataset that
       marks nothing still has a known seller team;
    3. the configured default for a person the data does not classify.

    An event with no person at all is ``unknown`` whatever the default says: an
    anonymous action cannot be attributed to a client, and counting it as one
    would inflate every client metric with un-attributable traffic.
    """
    audience = config.get("audience") or {}
    payload = data if isinstance(data, Mapping) else {}
    person = _as_text(person)

    if not person:
        return AUDIENCE_UNKNOWN

    raw_type = _pick(payload, list(audience.get("user_type_fields") or []))
    text = _as_text(raw_type).lower()
    if text:
        if text in _lower_set(audience.get("external_values") or []):
            return AUDIENCE_EXTERNAL
        if text in _lower_set(audience.get("internal_values") or []):
            return AUDIENCE_INTERNAL

    for flag in audience.get("bool_fields") or []:
        if flag not in payload or not isinstance(payload[flag], bool):
            continue
        if flag in ("external", "is_external"):
            return AUDIENCE_EXTERNAL if payload[flag] else AUDIENCE_INTERNAL
        return AUDIENCE_INTERNAL if payload[flag] else AUDIENCE_EXTERNAL

    lowered = person.lower()
    if lowered in _lower_set(audience.get("internal_people") or []):
        return AUDIENCE_INTERNAL
    domain = lowered.rpartition("@")[2]
    if domain:
        for suffix in _lower_set(audience.get("internal_domains") or []):
            if domain == suffix or domain.endswith(f".{suffix}"):
                return AUDIENCE_INTERNAL

    return AUDIENCE_EXTERNAL if _as_bool(audience.get("unknown_is_external", True)) else AUDIENCE_INTERNAL


def collect_events(
    store: RecordStore,
    config: Mapping[str, Any],
    *,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    """Normalise activity records into one event shape, whatever they call fields."""
    event_config = config.get("event") or {}
    people = list(event_config.get("person_fields") or [])
    actions = list(event_config.get("action_fields") or [])
    targets = list(event_config.get("target_fields") or [])
    stamps = list(event_config.get("timestamp_fields") or [])
    account_fields = list((config.get("room") or {}).get("account_fields") or [])
    view_actions = _lower_set((config.get("taxonomy") or {}).get("view_actions") or [])

    events: list[dict[str, Any]] = []
    for record in _scan(store, COLLECTION_ACTIVITY, room_id=room_id):
        data = record.get("data") or {}
        person = _pick_text(data, people)
        action = _pick_text(data, actions) or "unknown"
        at = _parse_dt(_pick(data, stamps)) or _parse_dt(record.get("created_at"))
        events.append(
            {
                "id": record.get("id"),
                "room_id": record.get("room_id"),
                # The event's own account wins over the workspace's, so an
                # activity row that names an account is not misfiled.
                "account": _pick_text(data, account_fields),
                "person": person,
                "action": action,
                "target": _pick_text(data, targets),
                "is_view": action.lower() in view_actions,
                "audience": classify_audience(person, data, config),
                "at": at,
                "occurred_at": _iso(at),
                "data": data,
            }
        )
    return events


# --------------------------------------------------------------------------- #
# The filter set
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Filters:
    """The three sourced filters, resolved: date range, owners, teams.

    ``owners`` and ``teams`` hold lowercased values and match
    case-insensitively; the caller's spelling is kept in ``owner_query`` so the
    response can echo what was asked for.
    """

    start: datetime | None = None
    end: datetime | None = None
    owners: frozenset[str] = frozenset()
    teams: frozenset[str] = frozenset()
    owner_query: tuple[str, ...] = ()
    team_query: tuple[str, ...] = ()

    @property
    def unfiltered(self) -> bool:
        return not (self.owners or self.teams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": _iso(self.start),
            "to": _iso(self.end),
            "owners": sorted(self.owners),
            "teams": sorted(self.teams),
        }


def _split_values(values: Sequence[str] | None) -> list[str]:
    """A filter may arrive repeated or comma-separated; accept both."""
    out: list[str] = []
    for value in values or ():
        for piece in str(value).split(","):
            text = piece.strip()
            if text:
                out.append(text)
    return out


def resolve_filters(
    *,
    date_from: Any = None,
    date_to: Any = None,
    owners: Sequence[str] | None = None,
    teams: Sequence[str] | None = None,
) -> Filters:
    """Build the filter set, refusing a range that cannot mean anything.

    A date-only ``to`` bound means the end of that day. Parsed as an instant it
    would be midnight at the *start* of the day, so filtering "through the 27th"
    would silently drop the 27th - the single most likely way for a report filter
    to be quietly wrong.
    """
    owner_query = _split_values(owners)
    team_query = _split_values(teams)

    start = _parse_dt(date_from) if date_from not in (None, "") else None
    end = _parse_dt(date_to) if date_to not in (None, "") else None
    if date_from not in (None, "") and start is None:
        raise InvalidWindow(f"from is not a date or timestamp: {date_from!r}")
    if date_to not in (None, "") and end is None:
        raise InvalidWindow(f"to is not a date or timestamp: {date_to!r}")
    if _is_date_only(date_to) and end is not None:
        end = end + timedelta(days=1) - timedelta(microseconds=1)
    if start is not None and end is not None and start > end:
        raise InvalidWindow(f"from {date_from!r} is after to {date_to!r}")

    return Filters(
        start=start,
        end=end,
        owners=frozenset(item.lower() for item in owner_query),
        teams=frozenset(item.lower() for item in team_query),
        owner_query=tuple(owner_query),
        team_query=tuple(team_query),
    )


def _in_window(event: Mapping[str, Any], window: Filters) -> bool:
    at = event.get("at")
    if at is None:
        # An event with no resolvable timestamp cannot be shown to be inside a
        # date range, so a range filter excludes it. With no range it is kept
        # and shows up in the coverage block as undated.
        return window.start is None and window.end is None
    if window.start is not None and at < window.start:
        return False
    if window.end is not None and at > window.end:
        return False
    return True


def workspace_in_scope(workspace: Workspace, window: Filters) -> bool:
    """Owner and team filters, applied to the workspace rather than the event.

    A workspace with no owner is excluded once an owner filter is on: it is not
    known to belong to that owner, and quietly keeping it would make a filtered
    report disagree with the list of owners it was filtered by.
    """
    if window.owners and workspace.owner.lower() not in window.owners:
        return False
    if window.teams:
        teams = _lower_set(workspace.teams)
        if not (teams & window.teams):
            return False
    return True


# --------------------------------------------------------------------------- #
# Account keys
# --------------------------------------------------------------------------- #

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: The account name used for a workspace that carries no account value. It is a
#: real bucket, not a null: an unattributed workspace is still a workspace the
#: portfolio opened.
UNATTRIBUTED_ACCOUNT = "(unattributed)"


def account_key(name: str) -> str:
    """A deterministic, path-safe key for an account name.

    The account list is sorted by name and the drill-down is a path segment, so
    an account named ``Northwind / EMEA`` could not be addressed at all. Collapses
    to a slug, and the disambiguation below guarantees uniqueness.
    """
    slug = _UNSAFE.sub("-", name).strip("-.")
    return slug[:80] or "account"


def assign_keys(names: Iterable[str]) -> dict[str, str]:
    """Map every account name to a unique key, in name order.

    Two names that collapse to the same slug are told apart by a numeric
    suffix, assigned in sorted order so the keys are stable between calls.
    """
    ordered = sorted({_as_text(name, UNATTRIBUTED_ACCOUNT) or UNATTRIBUTED_ACCOUNT for name in names})
    keys: dict[str, str] = {}
    taken: set[str] = set()
    for name in ordered:
        base = account_key(name)
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{suffix}"
            suffix += 1
        taken.add(candidate)
        keys[name] = candidate
    return keys


# --------------------------------------------------------------------------- #
# The rollup
# --------------------------------------------------------------------------- #


def _client_rank_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Rank a client by actions, then views, then recency, then name.

    Descending on the first three and ascending on the last, which is why the
    name is negated: a deterministic order needs a total one, and two clients
    with identical engagement must not swap places between two reads.
    """
    last = row.get("_last")
    return (
        -int(row.get("actions") or 0),
        -int(row.get("views") or 0),
        -(last.timestamp() if last else 0.0),
        str(row.get("person") or "").lower(),
    )


def _blank_client(person: str) -> dict[str, Any]:
    return {
        "person": person,
        "actions": 0,
        "views": 0,
        "workspaces": set(),
        "targets": set(),
        "_last": None,
    }


def _finalise_client(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    last = row.get("_last")
    return {
        "person": row["person"],
        "actions": row["actions"],
        "views": row["views"],
        "workspaces": sorted(row["workspaces"]),
        "workspace_count": len(row["workspaces"]),
        "targets": sorted(row["targets"]),
        "last_seen_at": _iso(last),
        "days_since_seen": _whole_days(now - last) if last else None,
    }


def bucket_start(value: datetime, grain: str) -> datetime:
    day = value.astimezone(timezone.utc)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain == "week":
        start -= timedelta(days=start.weekday())
    return start


def _step(grain: str) -> timedelta:
    return timedelta(days=7 if grain == "week" else 1)


def _chart_window(
    events: Sequence[Mapping[str, Any]], window: Filters, now: datetime, chart_days: Any
) -> tuple[datetime, datetime]:
    """The axis for "Client views over time".

    Sourced as a widget with no stated axis. With no ``from``, the series starts
    at the earliest in-scope event rather than a fixed 30 days back, because a
    report whose chart is empty while its tiles are full reads as a bug.
    """
    end = window.end or now
    start = window.start
    if start is None:
        stamps = [event["at"] for event in events if event.get("at") is not None]
        if stamps:
            start = bucket_start(min(stamps), "day")
        else:
            start = bucket_start(now - timedelta(days=_as_int(chart_days, 30, minimum=1)), "day")
    if start > end:
        start = end
    return start, end


def _resolve_grain(
    start: datetime, end: datetime, requested: str, max_points: int
) -> tuple[str, bool]:
    """Widen the chart grain rather than return an unreadable series.

    A two-year range at day grain is seven hundred points of noise. The response
    says which grain it used and that it widened, so a reader is never shown a
    coarser chart without being told.
    """
    if requested not in ("day", "week"):
        raise InvalidWindow(f"grain must be 'day' or 'week', got {requested!r}")
    cap = max(2, _as_int(max_points, MAX_CHART_POINTS, minimum=2))
    span_days = max(1, int((end - start).total_seconds() // 86400) + 1)
    if requested == "day" and span_days > cap:
        return "week", True
    return requested, False


def views_over_time(
    events: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
    grain: str,
) -> list[dict[str, Any]]:
    """Sourced tile: "Client views over time".

    Dense and zero-filled, so a day with no client activity reads as a gap rather
    than being absent from the series.
    """
    buckets: dict[str, dict[str, Any]] = {}

    def point(key: str) -> dict[str, Any]:
        return buckets.setdefault(key, {"views": 0, "actions": 0, "clients": set()})

    for event in events:
        at = event.get("at")
        if at is None or at < start or at > end:
            continue
        row = point(bucket_start(at, grain).date().isoformat())
        row["actions"] += 1
        if event.get("is_view"):
            row["views"] += 1
        row["clients"].add(str(event.get("person") or ""))

    points: list[dict[str, Any]] = []
    cursor = bucket_start(start, grain)
    limit = bucket_start(end, grain)
    while cursor <= limit:
        key = cursor.date().isoformat()
        row = buckets.get(key) or {}
        points.append(
            {
                "bucket_start": key,
                "views": int(row.get("views") or 0),
                "actions": int(row.get("actions") or 0),
                "clients": len(row.get("clients") or ()),
            }
        )
        cursor += _step(grain)
    return points


def build_rollup(
    *,
    workspaces: Sequence[Workspace],
    events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    now: datetime,
    window: Filters,
) -> dict[str, Any]:
    """Every account-level number the report and the list are made of.

    Computed on read from the records a team already keeps. Nothing here is a
    stored metric, so a team adding a field to an event changes the report
    without a migration, a redeploy, or a word of coordination.
    """
    thresholds = config.get("thresholds") or {}
    floor = _as_int(thresholds.get("multi_thread_floor"), 2, minimum=1)
    stale_after = _as_int(thresholds.get("stale_after_days"), 30, minimum=1)
    client_limit = _as_int(thresholds.get("client_limit"), 10, minimum=1)

    in_window = [event for event in events if _in_window(event, window)]
    scoped = [workspace for workspace in workspaces if workspace_in_scope(workspace, window)]
    by_id = {workspace.id: workspace for workspace in scoped}

    external = [event for event in in_window if event.get("audience") == AUDIENCE_EXTERNAL]
    internal = [event for event in in_window if event.get("audience") == AUDIENCE_INTERNAL]
    unknown = [event for event in in_window if event.get("audience") == AUDIENCE_UNKNOWN]
    undated = [event for event in in_window if event.get("at") is None]

    # An event on a workspace that is not in scope is left out: filtering by
    # owner must not leave that owner's activity behind in the totals. It is
    # counted rather than dropped silently, because an event that vanishes from
    # every number with no trace is the failure mode a report cannot have.
    out_of_scope = [event for event in in_window if event.get("room_id") not in by_id]
    external = [event for event in external if event.get("room_id") in by_id]
    internal = [event for event in internal if event.get("room_id") in by_id]
    unknown = [event for event in unknown if event.get("room_id") in by_id]
    undated = [event for event in undated if event.get("room_id") in by_id]

    def account_of(event: Mapping[str, Any]) -> str:
        named = _as_text(event.get("account"))
        if named:
            return named
        workspace = by_id.get(str(event.get("room_id")))
        # An event on no workspace at all cannot be attributed to one, so it
        # belongs in the unattributed bucket rather than nowhere. It is still
        # excluded from the workspace-scoped totals below.
        return (workspace.account if workspace else "") or UNATTRIBUTED_ACCOUNT

    # ---- accounts ---------------------------------------------------------- #
    grouped: dict[str, dict[str, Any]] = {}
    for workspace in scoped:
        name = workspace.account or UNATTRIBUTED_ACCOUNT
        row = grouped.setdefault(
            name,
            {
                "account": name,
                "workspaces": {},
                "owners": set(),
                "teams": set(),
                "clients": {},
                "actions": 0,
                "views": 0,
                "_first": None,
                "_last": None,
            },
        )
        row["workspaces"][workspace.id] = workspace
        if workspace.owner:
            row["owners"].add(workspace.owner)
        row["teams"].update(workspace.teams)

    for event in external:
        name = account_of(event)
        row = grouped.setdefault(
            name,
            {
                "account": name,
                "workspaces": {},
                "owners": set(),
                "teams": set(),
                "clients": {},
                "actions": 0,
                "views": 0,
                "_first": None,
                "_last": None,
            },
        )
        room_id = str(event.get("room_id"))
        workspace = by_id.get(room_id)
        if workspace is not None:
            row["workspaces"].setdefault(room_id, workspace)
            if workspace.owner:
                row["owners"].add(workspace.owner)
            row["teams"].update(workspace.teams)

        person = str(event.get("person") or "")
        client = row["clients"].setdefault(person, _blank_client(person))
        client["actions"] += 1
        if event.get("is_view"):
            client["views"] += 1
            row["views"] += 1
        client["workspaces"].add(room_id)
        if event.get("target"):
            client["targets"].add(str(event["target"]))
        at = event.get("at")
        if at is not None:
            if client["_last"] is None or at > client["_last"]:
                client["_last"] = at
            if row["_first"] is None or at < row["_first"]:
                row["_first"] = at
            if row["_last"] is None or at > row["_last"]:
                row["_last"] = at
        row["actions"] += 1

    keys = assign_keys(row["account"] for row in grouped.values())
    accounts: list[dict[str, Any]] = []
    for row in grouped.values():
        clients = sorted(row["clients"].values(), key=_client_rank_key)
        finalised = [_finalise_client(client, now) for client in clients]
        last = row["_last"]
        days_since = _whole_days(now - last) if last else None
        unique_clients = len(finalised)
        champion = finalised[0] if finalised else None
        accounts.append(
            {
                "account": row["account"],
                "account_key": keys[row["account"]],
                "views": row["views"],
                "actions": row["actions"],
                "other_actions": row["actions"] - row["views"],
                "unique_clients": unique_clients,
                "workspace_count": len(row["workspaces"]),
                "workspaces": [
                    {
                        "id": workspace.id,
                        "name": workspace.name,
                        "owner": workspace.owner,
                        "teams": list(workspace.teams),
                    }
                    for workspace in sorted(
                        row["workspaces"].values(), key=lambda item: item.name.lower()
                    )
                ],
                "owners": sorted(row["owners"]),
                "teams": sorted(row["teams"]),
                "first_activity_at": _iso(row["_first"]),
                "last_activity_at": _iso(last),
                "days_since_activity": days_since,
                "stale": bool(days_since is not None and days_since > stale_after),
                "multi_threaded": unique_clients >= floor,
                "thread_floor": floor,
                "thread_depth": unique_clients,
                "champion": champion,
                "champion_only_thread": bool(champion is not None and unique_clients == 1),
                "no_client_activity": unique_clients == 0,
                "clients": finalised,
            }
        )

    # ---- portfolio totals -------------------------------------------------- #
    per_workspace_clients: dict[str, set[str]] = defaultdict(set)
    for event in external:
        per_workspace_clients[str(event.get("room_id"))].add(str(event.get("person") or ""))

    workspace_count = len(scoped)
    unique_clients_total = sum(row["unique_clients"] for row in accounts)
    workspace_client_total = sum(len(people) for people in per_workspace_clients.values())
    average = (workspace_client_total / workspace_count) if workspace_count else 0.0

    ranked_clients: list[dict[str, Any]] = []
    by_client: dict[str, dict[str, Any]] = {}
    for row in accounts:
        for client in row["clients"]:
            entry = by_client.setdefault(
                client["person"],
                {
                    "person": client["person"],
                    "actions": 0,
                    "views": 0,
                    "accounts": set(),
                    "workspaces": set(),
                    "_last": None,
                    "_last_raw": client["last_seen_at"],
                },
            )
            entry["actions"] += client["actions"]
            entry["views"] += client["views"]
            entry["accounts"].add(row["account"])
            entry["workspaces"].update(client["workspaces"])
            stamp = _parse_dt(client["last_seen_at"])
            if stamp is not None and (entry["_last"] is None or stamp > entry["_last"]):
                entry["_last"] = stamp
    for entry in by_client.values():
        ranked_clients.append(
            {
                "person": entry["person"],
                "actions": entry["actions"],
                "views": entry["views"],
                "accounts": sorted(entry["accounts"]),
                "account_count": len(entry["accounts"]),
                "workspaces": sorted(entry["workspaces"]),
                "workspace_count": len(entry["workspaces"]),
                "last_seen_at": _iso(entry["_last"]),
                "days_since_seen": _whole_days(now - entry["_last"]) if entry["_last"] else None,
            }
        )
    ranked_clients.sort(key=lambda item: (-item["actions"], -item["views"], item["person"].lower()))

    return {
        "now": now,
        "window": window,
        "workspaces": scoped,
        "accounts": accounts,
        "events": external,
        "internal_events": internal,
        "unknown_events": unknown,
        "undated_events": undated,
        "per_workspace_clients": per_workspace_clients,
        "ranked_clients": ranked_clients,
        "client_limit": client_limit,
        "totals": {
            "workspaces": workspace_count,
            "workspaces_with_client_activity": len(per_workspace_clients),
            "accounts": len(accounts),
            "client_views": sum(row["views"] for row in accounts),
            "client_actions": sum(row["actions"] for row in accounts),
            "client_actions_other_than_views": sum(row["other_actions"] for row in accounts),
            "unique_clients": unique_clients_total,
            "unique_clients_portfolio": len(by_client),
            "avg_unique_clients_per_workspace": round(average, 3),
            "multi_threaded_accounts": sum(1 for row in accounts if row["multi_threaded"]),
            "single_threaded_accounts": sum(
                1 for row in accounts if row["champion_only_thread"]
            ),
            "accounts_without_client_activity": sum(1 for row in accounts if row["no_client_activity"]),
            "stale_accounts": sum(1 for row in accounts if row["stale"]),
            "internal_actions": len(internal),
        },
        "coverage": {
            "external_events": len(external),
            "internal_events_excluded": len(internal),
            "unattributed_events_excluded": len(unknown),
            "events_out_of_scope": len(out_of_scope),
            "undated_events": len(undated),
            "workspaces_dropped_by_filter": len(workspaces) - workspace_count,
        },
    }


# --------------------------------------------------------------------------- #
# Sorting the account list
# --------------------------------------------------------------------------- #

_SORT_NUMERIC = {
    "views": lambda row: row["views"],
    "actions": lambda row: row["actions"],
    "other_actions": lambda row: row["other_actions"],
    "unique_clients": lambda row: row["unique_clients"],
    "thread_depth": lambda row: row["thread_depth"],
    "workspace_count": lambda row: row["workspace_count"],
    "days_since_activity": lambda row: (
        row["days_since_activity"] if row["days_since_activity"] is not None else -1
    ),
    "multi_threaded": lambda row: 1 if row["multi_threaded"] else 0,
    "champion_only_thread": lambda row: 1 if row["champion_only_thread"] else 0,
    "stale": lambda row: 1 if row["stale"] else 0,
    "no_client_activity": lambda row: 1 if row["no_client_activity"] else 0,
    "owner_count": lambda row: len(row["owners"]),
    "team_count": lambda row: len(row["teams"]),
}

_SORT_TEXT = {
    "account": lambda row: row["account"].lower(),
    "champion": lambda row: str((row["champion"] or {}).get("person") or "").lower(),
    "first_activity_at": lambda row: row["first_activity_at"] or "",
    "last_activity_at": lambda row: row["last_activity_at"] or "",
}

#: Sourced as "sort by any column", so every column of the account list is a
#: sort key. An unknown key raises :class:`InvalidSortColumn` rather than being
#: ignored: a silently dropped ``sort`` is a report that looks sorted by
#: something it is not.
SORTABLE_COLUMNS: dict[str, str] = {
    **{name: "number" for name in _SORT_NUMERIC},
    **{name: "text" for name in _SORT_TEXT},
}

DEFAULT_SORT = "actions"


def sort_accounts(
    accounts: Sequence[Mapping[str, Any]], sort: str | None, descending: bool
) -> list[dict[str, Any]]:
    """Order the account list by any of its columns.

    Ties break on the account name, so two accounts with the same views and
    actions do not swap places between two reads of the same data.
    """
    key = (sort or DEFAULT_SORT).strip() or DEFAULT_SORT
    if key not in SORTABLE_COLUMNS:
        raise InvalidSortColumn(
            f"cannot sort by {key!r}; sortable columns are {', '.join(sorted(SORTABLE_COLUMNS))}"
        )
    if key in _SORT_NUMERIC:
        primary = _SORT_NUMERIC[key]
    else:
        primary = _SORT_TEXT[key]
    sign = -1 if descending else 1
    return sorted(
        (dict(row) for row in accounts),
        key=lambda row: (sign * primary(row), row["account"].lower()),
    )


# --------------------------------------------------------------------------- #
# Public views
# --------------------------------------------------------------------------- #


def _tile_values(rollup: Mapping[str, Any], series: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The five sourced tiles.

    ``expand`` is the account-list sort key a click carries. The route builds the
    URL from it; this module stays free of URL knowledge, the same way the audit
    ``source`` comes from the HTTP layer.
    """
    totals = rollup["totals"]
    average = totals["avg_unique_clients_per_workspace"]
    chart_days = [point for point in series if point["views"] or point["actions"]]
    return [
        {
            "key": "client_views",
            "label": "Total client views",
            "value": totals["client_views"],
            "hint": "External views of a workspace, page or embedded content.",
            "expand": "views",
        },
        {
            "key": "client_actions",
            "label": "Total client actions",
            "value": totals["client_actions"],
            "hint": (
                "Every client interaction. Click into the cell to expand upon who "
                "these individuals are."
            ),
            "expand": "actions",
        },
        {
            "key": "avg_unique_clients",
            "label": "Average unique clients per workspace",
            "value": average,
            "display": f"{average:.2f}",
            "hint": (
                f"Over all {totals['workspaces']} workspace(s) in scope, including any "
                "nobody has opened."
            ),
            "expand": "unique_clients",
        },
        {
            "key": "client_views_over_time",
            "label": "Client views over time",
            "value": len(chart_days),
            "display": f"{totals['client_views']} views",
            "hint": f"Across {len(series)} bucket(s).",
            "expand": "views",
            "series": list(series),
        },
        {
            "key": "most_engaged_clients",
            "label": "Most engaged clients",
            "value": totals["unique_clients_portfolio"],
            "display": f"{totals['unique_clients_portfolio']} client(s)",
            "hint": "Ranked by actions across the portfolio.",
            "expand": "actions",
            "clients": rollup["ranked_clients"][: rollup["client_limit"]],
        },
    ]


def _context(
    *,
    rollup: Mapping[str, Any],
    config: Mapping[str, Any],
    now: datetime,
    grain: str,
    widened: bool,
    series: Sequence[Mapping[str, Any]],
    scope_label: str,
    room_id: str | None = None,
) -> dict[str, Any]:
    window: Filters = rollup["window"]  # type: ignore[assignment]
    return {
        "as_of": _iso(now),
        "grain": grain,
        "grain_widened": widened,
        "scope": {
            "label": scope_label,
            "room_id": room_id,
            "all_workspaces": room_id is None,
        },
        "filters": window.to_dict(),
        "config": {
            "thresholds": config.get("thresholds"),
            "taxonomy": config.get("taxonomy"),
            "audience": {
                key: value
                for key, value in (config.get("audience") or {}).items()
                if key not in ("internal_people",)
            },
        },
        "totals": rollup["totals"],
        "coverage": rollup["coverage"],
        "series_grain": grain,
        "series_points": len(series),
    }


def client_engagement(
    store: RecordStore,
    *,
    filters: Filters,
    grain: str = "day",
    as_of: str | None = None,
) -> dict[str, Any]:
    """The Client Engagement report: the five tiles, over every workspace.

    Sourced scope: "The Client Engagement report analyzes external activity
    across ALL workspaces in your Dock instance." There is no workspace parameter
    here and adding one would make this the WF-006 Analytics view wearing this
    report's name; :func:`workspace_engagement` is where a single workspace is
    in scope, and it runs the same rollup.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    workspaces = scan_workspaces(store, config)
    events = collect_events(store, config)

    rollup = build_rollup(workspaces=workspaces, events=events, config=config, now=now, window=filters)
    thresholds = config.get("thresholds") or {}
    start, end = _chart_window(rollup["events"], filters, now, thresholds.get("chart_days"))
    resolved_grain, widened = _resolve_grain(
        start, end, grain, thresholds.get("max_chart_points")
    )
    series = views_over_time(rollup["events"], start=start, end=end, grain=resolved_grain)

    payload = _context(
        rollup=rollup,
        config=config,
        now=now,
        grain=resolved_grain,
        widened=widened,
        series=series,
        scope_label="All Workspaces",
    )
    payload["report"] = "client_engagement"
    payload["tiles"] = _tile_values(rollup, series)
    payload["sortable_columns"] = dict(SORTABLE_COLUMNS)
    payload["thread_floor"] = thresholds.get("multi_thread_floor", 2)
    return payload


def workspace_engagement(
    store: RecordStore, room_id: str, *, filters: Filters, grain: str = "day", as_of: str | None = None
) -> dict[str, Any]:
    """The same report narrowed to one workspace, with that workspace's clients.

    The portfolio report has no workspace selector, so this is the only
    workspace-scoped read. It runs the same rollup over one workspace rather than
    filtering the portfolio down, so a workspace nobody has opened still reports
    zeros instead of disappearing.

    Owner and team filters are dropped here: the scope is already the workspace
    itself, and an owner filter on a single workspace could only ever empty it.
    The date range is kept, because "is this workspace still being looked at"
    over a quarter is the question a date filter on this page is for.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    require_workspace(store, room_id)
    target = next(
        (item for item in scan_workspaces(store, config) if item.id == room_id), None
    )
    scoped = [target] if target is not None else []
    window = Filters(start=filters.start, end=filters.end)
    events = collect_events(store, config, room_id=room_id)
    rollup = build_rollup(
        workspaces=scoped, events=events, config=config, now=now, window=window
    )

    thresholds = config.get("thresholds") or {}
    start, end = _chart_window(rollup["events"], window, now, thresholds.get("chart_days"))
    resolved_grain, widened = _resolve_grain(start, end, grain, thresholds.get("max_chart_points"))
    series = views_over_time(rollup["events"], start=start, end=end, grain=resolved_grain)

    payload = _context(
        rollup=rollup,
        config=config,
        now=now,
        grain=resolved_grain,
        widened=widened,
        series=series,
        scope_label=target.name if target is not None else room_id,
        room_id=room_id,
    )
    payload["report"] = "workspace_engagement"
    payload["tiles"] = _tile_values(rollup, series)
    payload["sortable_columns"] = dict(SORTABLE_COLUMNS)
    payload["thread_floor"] = thresholds.get("multi_thread_floor", 2)
    payload["accounts"] = rollup["accounts"]
    payload["workspace"] = {
        "id": room_id,
        "name": target.name if target is not None else room_id,
        "account": target.account if target is not None else "",
        "owner": target.owner if target is not None else "",
        "teams": list(target.teams) if target is not None else [],
        "created_at": _iso(target.created) if target is not None else None,
        "clients": rollup["ranked_clients"],
    }
    return payload


def account_list(
    store: RecordStore,
    *,
    filters: Filters,
    sort: str | None = None,
    descending: bool = True,
    limit: int | None = None,
    offset: int = 0,
    as_of: str | None = None,
) -> dict[str, Any]:
    """The expanded tile: every account and its engagement, sorted by any column."""
    now = resolve_now(as_of)
    config = load_config(store)
    workspaces = scan_workspaces(store, config)
    events = collect_events(store, config)
    rollup = build_rollup(workspaces=workspaces, events=events, config=config, now=now, window=filters)
    ordered = sort_accounts(rollup["accounts"], sort, descending)

    # The config cap applies only when the caller asked for no limit of its own.
    # Applying it before the offset would make `limit` mean "the first N rows",
    # so the second page of a list would be empty however high the offset went.
    cap = _as_int((config.get("thresholds") or {}).get("account_limit"), 0, minimum=0)
    total = len(ordered)
    if cap and limit is None:
        ordered = ordered[:cap]
    page = ordered[max(0, offset) :]
    if limit is not None:
        page = page[: max(1, limit)]

    key = (sort or DEFAULT_SORT).strip() or DEFAULT_SORT
    return {
        "report": "client_engagement_accounts",
        "as_of": _iso(now),
        "sort": key,
        "sort_kind": SORTABLE_COLUMNS.get(key),
        "descending": descending,
        "sortable_columns": dict(SORTABLE_COLUMNS),
        "filters": filters.to_dict(),
        "thread_floor": (config.get("thresholds") or {}).get("multi_thread_floor", 2),
        "count": len(page),
        "total": total,
        "offset": max(0, offset),
        "totals": rollup["totals"],
        "accounts": page,
    }


def account_detail(
    store: RecordStore, key: str, *, filters: Filters, as_of: str | None = None
) -> dict[str, Any]:
    """Click into the cell: the individuals behind one account's numbers.

    Sourced: "Click into the cell to expand upon who these individuals are!"
    """
    listing = account_list(store, filters=filters, as_of=as_of)
    for account in listing["accounts"]:
        if account["account_key"] == key:
            payload = dict(account)
            payload["report"] = "client_engagement_account"
            payload["filters"] = filters.to_dict()
            payload["as_of"] = listing["as_of"]
            payload["thread_floor"] = listing["thread_floor"]
            return payload
    raise UnknownAccount(key)


def available_filters(store: RecordStore, *, as_of: str | None = None) -> dict[str, Any]:
    """What the report can be filtered by, discovered rather than declared.

    Sourced: "You can filter the report down by date range, owners, and/or
    teams." A team no record carries cannot be filtered on, so the values in
    scope are published here for a client to build its pickers from.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    workspaces = scan_workspaces(store, config)
    events = collect_events(store, config)

    owners = sorted({workspace.owner for workspace in workspaces if workspace.owner}, key=str.lower)
    teams = sorted(
        {team for workspace in workspaces for team in workspace.teams if team}, key=str.lower
    )
    stamps = [event["at"] for event in events if event.get("at") is not None]
    audiences = defaultdict(int)
    for event in events:
        audiences[str(event.get("audience"))] += 1

    accounts = {
        workspace.account or UNATTRIBUTED_ACCOUNT
        for workspace in workspaces
    } | {
        _as_text(event.get("account")) or UNATTRIBUTED_ACCOUNT
        for event in events
        if _as_text(event.get("account"))
    }
    keys = assign_keys(accounts)

    return {
        "report": "client_engagement_filters",
        "as_of": _iso(now),
        "owners": owners,
        "teams": teams,
        "accounts": [{"account": name, "account_key": keys[name]} for name in sorted(keys)],
        "date_range": {
            "earliest": _iso(min(stamps)) if stamps else None,
            "latest": _iso(max(stamps)) if stamps else None,
            "as_of": _iso(now),
        },
        "grains": ["day", "week"],
        "sortable_columns": dict(SORTABLE_COLUMNS),
        "default_sort": DEFAULT_SORT,
        "tiles": [
            {"key": key, "label": label, "expand": expand} for key, label, expand in TILES
        ],
        "audience_split": dict(sorted(audiences.items())),
        "collections": {
            "workspaces": COLLECTION_ROOM,
            "events": COLLECTION_ACTIVITY,
            "implementations": (config.get("implementation") or {}).get("collection"),
        },
    }


# --------------------------------------------------------------------------- #
# Team Usage: the internal mirror
# --------------------------------------------------------------------------- #


def team_usage(
    store: RecordStore, *, filters: Filters, as_of: str | None = None
) -> dict[str, Any]:
    """Team Usage: "How actively is your team using Dock?"

    Sourced as one sentence, with no widget vocabulary of its own, so every
    number below is an inference and is named as one: what a rep's own activity
    says, and what the client response to the accounts they own says. The
    external/internal distinction is the same one the Client Engagement report
    uses, run over the internal side of it.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    workspaces = scan_workspaces(store, config)
    events = collect_events(store, config)
    rollup = build_rollup(workspaces=workspaces, events=events, config=config, now=now, window=filters)

    owned: dict[str, dict[str, Any]] = {}
    for workspace in rollup["workspaces"]:
        if not workspace.owner:
            continue
        row = owned.setdefault(
            workspace.owner,
            {
                "owner": workspace.owner,
                "workspaces": set(),
                "accounts": set(),
                "_client_actions": 0,
                "_client_views": 0,
                "_client_people": set(),
            },
        )
        row["workspaces"].add(workspace.id)
        row["accounts"].add(workspace.account or UNATTRIBUTED_ACCOUNT)

    for account in rollup["accounts"]:
        for owner in account["owners"]:
            row = owned.get(owner)
            if row is None:
                continue
            row["_client_actions"] += account["actions"]
            row["_client_views"] += account["views"]
            row["_client_people"].update(client["person"] for client in account["clients"])

    # A rep's own activity is attributed to them by name, which only resolves
    # when the data says who they are; the config's internal_people list is how a
    # dataset makes that true.
    internal_by_person: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"actions": 0, "views": 0, "_last": None}
    )
    for event in rollup["internal_events"]:
        person = str(event.get("person") or "")
        if not person:
            continue
        entry = internal_by_person[person]
        entry["actions"] += 1
        if event.get("is_view"):
            entry["views"] += 1
        at = event.get("at")
        if at is not None and (entry["_last"] is None or at > entry["_last"]):
            entry["_last"] = at

    people_to_owner = {owner.lower(): owner for owner in owned}
    for person, entry in internal_by_person.items():
        owner = people_to_owner.get(person.lower())
        if owner is None:
            continue
        row = owned[owner]
        row.setdefault("_internal_actions", 0)
        row.setdefault("_internal_views", 0)
        row["_internal_actions"] += entry["actions"]
        row["_internal_views"] += entry["views"]

    totals = rollup["totals"]
    multi_threaded_by_owner: dict[str, int] = defaultdict(int)
    quiet_by_owner: dict[str, int] = defaultdict(int)
    for account in rollup["accounts"]:
        for owner in account["owners"]:
            if account["multi_threaded"]:
                multi_threaded_by_owner[owner] += 1
            if account["no_client_activity"]:
                quiet_by_owner[owner] += 1

    rows: list[dict[str, Any]] = []
    for owner, row in owned.items():
        accounts_owned = len(row["accounts"])
        rows.append(
            {
                "owner": owner,
                "workspaces": len(row["workspaces"]),
                "accounts": accounts_owned,
                "internal_actions": row.get("_internal_actions", 0),
                "internal_views": row.get("_internal_views", 0),
                "client_actions": row["_client_actions"],
                "client_views": row["_client_views"],
                "unique_client_people": len(row["_client_people"]),
                "multi_threaded_accounts": multi_threaded_by_owner.get(owner, 0),
                "accounts_without_client_activity": quiet_by_owner.get(owner, 0),
                "client_actions_per_account": round(
                    row["_client_actions"] / accounts_owned, 3
                )
                if accounts_owned
                else 0.0,
            }
        )
    rows.sort(key=lambda item: (-item["client_actions"], item["owner"].lower()))

    unowned = [workspace for workspace in rollup["workspaces"] if not workspace.owner]
    return {
        "report": "team_usage",
        "as_of": _iso(now),
        "filters": filters.to_dict(),
        "totals": {
            "owners": len(rows),
            "workspaces": totals["workspaces"],
            "internal_actions": totals["internal_actions"],
            "client_actions": totals["client_actions"],
            "client_views": totals["client_views"],
            "workspaces_without_an_owner": len(unowned),
        },
        "coverage": rollup["coverage"],
        "count": len(rows),
        "owners_detail": rows,
        "unattributed_internal_people": sorted(
            person
            for person in internal_by_person
            if person.lower() not in people_to_owner
        ),
        "workspaces_without_an_owner": [
            {"id": workspace.id, "name": workspace.name, "account": workspace.account}
            for workspace in sorted(unowned, key=lambda item: item.name.lower())
        ],
    }


# --------------------------------------------------------------------------- #
# Implementations: delivery status
# --------------------------------------------------------------------------- #


def _implementation_state(data: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    impl = config.get("implementation") or {}
    raw = _pick_text(data, list(impl.get("status_fields") or [])).lower()
    if raw in _lower_set(impl.get("completed_states") or []):
        return "completed"
    if raw in _lower_set(impl.get("active_states") or []):
        return "active"
    return "unknown"


def implementations(
    store: RecordStore, *, filters: Filters, as_of: str | None = None
) -> dict[str, Any]:
    """The Implementations Report: "How long are customer implementations taking?"

    Sourced widget vocabulary, all six: Total/Active/Completed implementations,
    time to completion average, % completed on time, implementations by owner,
    customer views/actions, most engaged customers. The customer figures are the
    same client rollup the Client Engagement report uses, which is why this
    report can answer "is the customer actually engaging during onboarding" with
    the same numbers that report shows.

    "At-risk delivery tracking" is the research's stated purpose for this report
    and it does not define at-risk, so it is derived here as: active and past
    due, active with no committed date, or completed after its due date. Every
    reason is named on the row, and urgency that is not risk is reported as
    ``days_to_due`` rather than folded in.
    """
    now = resolve_now(as_of)
    config = load_config(store)
    impl = config.get("implementation") or {}
    collection = _as_text(impl.get("collection"), "implementation")

    rows: list[dict[str, Any]] = []
    for record in _scan(store, collection):
        data = record.get("data") or {}
        started = _parse_dt(_pick(data, list(impl.get("started_fields") or [])))
        completed = _parse_dt(_pick(data, list(impl.get("completed_fields") or [])))
        due = _parse_dt(_pick(data, list(impl.get("due_fields") or [])))
        state = _implementation_state(data, config)
        duration = (completed - started) if (completed and started) else None
        on_time = None
        if completed is not None and due is not None:
            on_time = completed <= due

        reasons: list[str] = []
        if state == "active" and due is not None and due < now:
            reasons.append(f"active and past its {due.date().isoformat()} due date")
        if on_time is False:
            reasons.append(f"completed after its {due.date().isoformat()} due date")
        if state == "active" and due is None:
            # No committed date at all. That is a delivery risk, and it is
            # invisible anywhere else on this report.
            reasons.append("active with no due date")
        if state == "active" and completed is not None:
            reasons.append("marked active but carries a completion date")

        rows.append(
            {
                "id": record.get("id"),
                "name": _pick_text(data, list(impl.get("name_fields") or []))
                or str(record.get("id")),
                "account": _pick_text(data, list(impl.get("account_fields") or []))
                or UNATTRIBUTED_ACCOUNT,
                "room_id": record.get("room_id"),
                "owner": _pick_text(data, list(impl.get("owner_fields") or [])),
                "state": state,
                "status": _pick_text(data, list(impl.get("status_fields") or [])),
                "started_at": _iso(started),
                "completed_at": _iso(completed),
                "due_at": _iso(due),
                "duration_days": round(duration.total_seconds() / 86400, 2) if duration else None,
                "days_to_due": int((due - now).total_seconds() // 86400) if due else None,
                "completed_on_time": on_time,
                "at_risk": bool(reasons),
                "at_risk_reasons": reasons,
            }
        )

    # Owner and team filters apply to the accounts in scope, so a filtered
    # portfolio report and a filtered implementation report agree. An
    # implementation whose account matches no workspace in scope is left out
    # rather than counted against a portfolio the caller filtered away.
    scoped_workspaces = [
        workspace
        for workspace in scan_workspaces(store, config)
        if workspace_in_scope(workspace, filters)
    ]
    in_scope = {workspace.account or UNATTRIBUTED_ACCOUNT for workspace in scoped_workspaces}
    rows = [row for row in rows if row["account"] in in_scope]

    completed = [row for row in rows if row["state"] == "completed"]
    active = [row for row in rows if row["state"] == "active"]
    timed = [row for row in completed if row["duration_days"] is not None]
    dated_completions = [row for row in completed if row["completed_on_time"] is not None]

    by_owner: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["owner"] or "(unassigned)"
        entry = by_owner.setdefault(key, {"owner": key, "total": 0, "active": 0, "completed": 0, "at_risk": 0})
        entry["total"] += 1
        if row["state"] == "active":
            entry["active"] += 1
        if row["state"] == "completed":
            entry["completed"] += 1
        if row["at_risk"]:
            entry["at_risk"] += 1
    owner_rows = sorted(
        by_owner.values(), key=lambda item: (-item["total"], str(item["owner"]).lower())
    )

    rollup = build_rollup(
        workspaces=scoped_workspaces,
        events=collect_events(store, config),
        config=config,
        now=now,
        window=filters,
    )
    totals = rollup["totals"]
    average_days = (
        round(sum(row["duration_days"] for row in timed) / len(timed), 2) if timed else None
    )
    on_time_rate = (
        round(100.0 * sum(1 for row in dated_completions if row["completed_on_time"]) / len(dated_completions), 2)
        if dated_completions
        else None
    )

    return {
        "report": "implementations",
        "as_of": _iso(now),
        "filters": filters.to_dict(),
        "collection": collection,
        "missing_dates": {
            "completed_without_a_start": sum(
                1 for row in rows if row["state"] == "completed" and row["duration_days"] is None
            ),
            "no_due_date": sum(1 for row in rows if row["due_at"] is None),
            "no_completion_date": sum(1 for row in rows if row["completed_at"] is None),
            "unknown_state": sum(1 for row in rows if row["state"] == "unknown"),
        },
        "totals": {
            "implementations": len(rows),
            "active": len(active),
            "completed": len(completed),
            "at_risk": sum(1 for row in rows if row["at_risk"]),
            "time_to_completion_days": average_days,
            "completed_on_time_percent": on_time_rate,
            "customer_views": totals["client_views"],
            "customer_actions": totals["client_actions"],
            "unique_customers": totals["unique_clients_portfolio"],
        },
        "by_owner": owner_rows,
        "most_engaged_customers": rollup["ranked_clients"][
            : _as_int((config.get("thresholds") or {}).get("client_limit"), 10, minimum=1)
        ],
        "at_risk": [row for row in rows if row["at_risk"]],
        "count": len(rows),
        "implementations_detail": sorted(
            rows, key=lambda row: (row["at_risk"] is False, str(row["account"]).lower(), row["name"].lower())
        ),
    }


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def record_event(
    store: RecordStore,
    payload: Mapping[str, Any],
    *,
    room_id: str | None = None,
    actor: str | None = None,
    source: str,
) -> dict[str, Any]:
    """Record one client interaction: the entry point of the data flow.

    Sourced: "underlying event capture is continuous", and the data reaches the
    product either through the activity log or through "the full ``workspace.*``
    webhook stream". Either way it arrives here as one interaction.

    The payload is stored verbatim, so a team that sends a field nobody declared
    still gets it indexed and queryable with ``?where=``. What this adds is the
    audience stamp: when the payload classifies nobody, the event is recorded as
    external, because this is the client-engagement stream and the report it
    feeds is a report about clients. An internal rep's own page view is posted
    with ``user_type: "internal"`` and is counted by the Team Usage report
    instead.

    The classification is returned rather than applied silently, so a caller can
    see which side of the report its event landed on.
    """
    if not isinstance(payload, Mapping) or not payload:
        raise EngagementReportError("an event needs at least one field")
    if room_id is not None:
        require_workspace(store, room_id)

    config = load_config(store)
    person = _pick_text(payload, list((config.get("event") or {}).get("person_fields") or []))
    audience = classify_audience(person, payload, config)

    data = dict(payload)
    if not any(
        field in data for field in ((config.get("audience") or {}).get("user_type_fields") or [])
    ) and not any(
        field in data for field in ((config.get("audience") or {}).get("bool_fields") or [])
    ):
        data["user_type"] = audience if audience != AUDIENCE_UNKNOWN else AUDIENCE_EXTERNAL

    record = store.create(
        COLLECTION_ACTIVITY, data, room_id=room_id, actor=actor, source=source
    )
    return {
        "event": record,
        "counted_as": audience,
        "in_client_engagement": audience == AUDIENCE_EXTERNAL,
        "note": (
            "Recorded as an external client interaction."
            if audience == AUDIENCE_EXTERNAL
            else "Recorded, but this report counts client activity only."
        ),
    }
