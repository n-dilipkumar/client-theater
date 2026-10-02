"""The content-search query contract.

Everything here is a value object or a pure function: no database, no HTTP, no
clock. That is deliberate. The researched search operation has a small set of
limits that the product depends on (a bounded term, a bounded page size, a
bounded filter depth, expiring cursors), and limits that live in a route handler
drift the moment a second caller appears. Here they are constants, the
validation is a function, and both are testable without a store.

The contract is shaped after the researched ``POST /search/v1/content/query``
operation: ``term``, ``options.searchFields``, ``options.returnFields``,
``filter``, ``sort``, ``options.pageSize``, ``options.enableSuggestedQueryResults``,
paged with a ``continuationToken``, answered with ``totalCount`` / ``documents`` /
``continuationToken`` / ``actualSearchTerm`` and the 400 messages the vendor
documents. What is *not* reproduced is the vendor identity: the store declares
no schema, so every logical field resolves through :class:`LibrarySchema` to a
JSON path a deployment chooses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

# -- documented limits ------------------------------------------------------- #

MAX_TERM_LENGTH = 150
"""Longest accepted search term."""

MIN_PAGE_SIZE = 0
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 40
"""The researched default is 40 and the documented range is 0-100 inclusive."""

MAX_FILTER_DEPTH = 2
"""Boolean groups may nest this deep. A condition at the limit is fine; a group
that would need a third level is not."""

# -- documented vocabulary --------------------------------------------------- #

DEFAULT_SEARCH_FIELDS: tuple[str, ...] = ("name", "description", "body", "properties")
"""Fields the search index covers: the title, the summary, the full text (which
the research describes as text "transformed from image, audio or video"), and
custom properties."""

DEFAULT_RETURN_FIELDS: tuple[str, ...] = (
    "repository",
    "name",
    "teamsiteId",
    "id",
    "versionId",
    "type",
    "applicationUrls",
    "format",
)
"""The fields returned when ``returnFields`` is not supplied.

The research describes these as "9 returned by default" but enumerates only
eight names. The enumerated list is reproduced here because it is the
actionable half: a caller can rely on these names, and a caller that omits
``returnFields`` gets exactly the fields the research names. The count
discrepancy is a documentation gap in the source, not a ninth field we failed
to find.
"""

OPT_IN_RETURN_FIELDS: tuple[str, ...] = (
    "properties",
    "thumbnailUrl",
    "pageThumbnailUrls",
    "downloadUrl",
    "publishDate",
    "majorVersion",
    "minorVersion",
    "latestVersion",
    "latestApprovedVersion",
)
"""The fields a caller has to ask for, including the pre-signed asset URLs.

The research describes these as "9 more opt-in"; nine names are listed here.
The pre-signed URLs are the reason they are opt-in and the reason the response
carries an expiry: the research records that the tokens "will expire in 1 day",
so they must never be treated as durable storage. :func:`asset_url_expiry` is
where that fact is expressed rather than left as a comment.
"""

SEARCH_OPERATORS: tuple[str, ...] = (
    "in",
    "equal",
    "greaterThan",
    "greaterThanOrEqual",
    "lessThan",
    "lessThanOrEqual",
)
FILTER_OPERATORS: tuple[str, ...] = ("and", "or")
REPOSITORIES: tuple[str, ...] = ("library", "WorkSpace")
"""Where content lives: the library (content manager, doc centre, news centre)
or a workspace."""

ASSET_URL_TTL_DAYS = 1
"""Lifetime of a pre-signed asset URL, in days."""


# -- errors ------------------------------------------------------------------ #


class SearchError(ValueError):
    """A query the caller must fix. Surfaces as HTTP 400 with this message."""


TERM_TOO_LONG = f"Search term should be less than {MAX_TERM_LENGTH} characters"
FILTER_TOO_COMPLEX = f"Filter is too complex. Currently the max filter depth is {MAX_FILTER_DEPTH}."
TOKEN_INVALID = "continuationToken is invalid or expired. Please regenerate it."


def page_size_error(value: Any) -> str:
    """The documented 400 body for an out-of-range page size."""
    return (
        f"PageSize {value} is incorrect. Please set a value between {MIN_PAGE_SIZE}-{MAX_PAGE_SIZE}"
    )


def asset_url_expiry() -> dict[str, Any]:
    """When the asset URLs in a result set stop working.

    Returned as part of every search response so a client cannot silently cache
    a thumbnail URL and serve a broken image tomorrow.
    """
    expires = datetime.now(timezone.utc) + timedelta(days=ASSET_URL_TTL_DAYS)
    return {
        "assetUrlsExpireAt": expires.isoformat(timespec="seconds"),
        "assetUrlTtlDays": ASSET_URL_TTL_DAYS,
    }


# -- JSON path access ------------------------------------------------------- #


def get_path(payload: Any, path: str) -> Any:
    """Read a dotted path out of a JSON payload, or ``None`` if absent.

    Written against the same flattening the dynamic index uses, so a path that
    is filterable is also readable here.
    """
    current = payload
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif isinstance(current, (list, tuple)) and segment.lstrip("-").isdigit():
            position = int(segment)
            if not -len(current) <= position < len(current):
                return None
            current = current[position]
        else:
            return None
    return current


# -- the logical field vocabulary -------------------------------------------- #


@dataclass(frozen=True)
class LibrarySchema:
    """Which JSON path in ``data`` backs each logical content field.

    The store declares no schema, and that is the point: a deployment says once,
    here, that its document title lives at ``data.title``. Anything not listed
    resolves to itself, so a team that adds ``data.region`` can filter, sort and
    return ``region`` on the day they add it, with no migration and no edit
    here.
    """

    fields: Mapping[str, str] = field(default_factory=dict)
    properties_root: str = "properties"
    """Root that ``custom.<Name>`` filter fields resolve under, mirroring the
    researched ``custom.<CustomPropertyName>`` filter field."""

    primary_field: str = "name"
    """Field whose exact and prefix matches score highest. Treated as the title."""

    def resolve(self, name: str) -> str:
        """Dotted JSON path for a logical field name.

        ``custom.Region`` resolves under the properties root so custom
        properties are filterable by name, which is the extension contract the
        research describes.
        """
        if name.startswith("custom."):
            suffix = name[len("custom.") :]
            if not suffix:
                raise SearchError("custom filter field needs a property name, e.g. custom.Region")
            return f"{self.properties_root}.{suffix}"
        return self.fields.get(name, name)


# -- filter expressions ------------------------------------------------------ #


@dataclass(frozen=True)
class Condition:
    """A leaf comparison: one logical field, one operator, one value."""

    field: str
    operator: str
    value: Any
    path: str
    depth: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
            "path": self.path,
            "depth": self.depth,
        }


@dataclass(frozen=True)
class Group:
    """An ``and`` / ``or`` of conditions and further groups."""

    operator: str
    children: tuple[Any, ...]
    depth: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "depth": self.depth,
            "children": [child.as_dict() for child in self.children],
        }


FilterNode = Any  # Condition | Group | None


def parse_filter(node: Any, schema: LibrarySchema, depth: int = 1) -> FilterNode:
    """Parse a filter expression into a depth-checked tree.

    Depth is counted over boolean groups: a top-level group is depth 1, the
    conditions inside it are depth 2, and anything that would need a third
    level is rejected with the documented message. The limit is enforced here
    rather than in the SQL because a filter can be arbitrarily nested JSON, and
    "max filter depth is 2" is a property of the contract, not of one backend.
    """
    if node is None:
        return None
    if not isinstance(node, Mapping):
        raise SearchError("filter must be an object with 'and', 'or', or a 'field' condition")

    present = [name for name in FILTER_OPERATORS if name in node]
    if present:
        if len(present) > 1 or "field" in node:
            raise SearchError(
                "a filter node is either a boolean group ('and'/'or') or a condition ('field'), not both"
            )
        operator = present[0]
        if depth > MAX_FILTER_DEPTH:
            raise SearchError(FILTER_TOO_COMPLEX)
        children = node[operator]
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)) or not children:
            raise SearchError(f"filter '{operator}' needs a non-empty list of conditions")
        if depth + 1 > MAX_FILTER_DEPTH:
            # Children may be conditions at the limit, but not further groups.
            for child in children:
                if isinstance(child, Mapping) and any(name in child for name in FILTER_OPERATORS):
                    raise SearchError(FILTER_TOO_COMPLEX)
        return Group(
            operator=operator,
            children=tuple(parse_filter(c, schema, depth + 1) for c in children),
            depth=depth,
        )

    if "field" not in node:
        raise SearchError("filter condition needs a 'field'; groups use 'and' or 'or'")

    field_name = node["field"]
    if not isinstance(field_name, str) or not field_name.strip():
        raise SearchError("filter condition 'field' must be a non-empty string")
    operator = node.get("operator", "equal")
    if operator not in SEARCH_OPERATORS:
        raise SearchError(
            f"unknown filter operator {operator!r}; use one of {', '.join(SEARCH_OPERATORS)}"
        )
    if "value" not in node:
        raise SearchError(f"filter condition on {field_name!r} needs a 'value'")
    if operator == "in" and not isinstance(node["value"], (list, tuple, set)):
        raise SearchError("the 'in' operator needs a list of values")
    return Condition(
        field=field_name,
        operator=operator,
        value=node["value"],
        path=schema.resolve(field_name),
        depth=depth,
    )


# -- sort -------------------------------------------------------------------- #


@dataclass(frozen=True)
class SortKey:
    """One sort term. ``field`` is a logical field, so it resolves like a filter."""

    field: str
    descending: bool = True
    path: str = ""

    def __post_init__(self) -> None:
        if not self.path:
            object.__setattr__(self, "path", self.field)


RELEVANCE = "__relevance__"
"""Sort key meaning "score first". Chosen because it cannot collide with a
logical field name, which resolves to itself when unmapped."""


def parse_sort(raw: Any, schema: LibrarySchema) -> tuple[SortKey, ...]:
    """Parse a sort expression. Absent or empty means relevance, then id."""
    if raw is None or raw == []:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise SearchError("sort must be a list of {field, direction} objects")
    keys: list[SortKey] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise SearchError("sort entries must be objects with a 'field'")
        field_name = entry.get("field")
        if not isinstance(field_name, str) or not field_name.strip():
            raise SearchError("sort entries need a non-empty 'field'")
        direction = str(entry.get("direction", "desc")).lower()
        if direction not in ("asc", "desc"):
            raise SearchError(f"sort direction must be 'asc' or 'desc', got {direction!r}")
        if field_name == RELEVANCE:
            keys.append(SortKey(field=RELEVANCE, descending=True, path=RELEVANCE))
            continue
        keys.append(
            SortKey(
                field=field_name, descending=direction == "desc", path=schema.resolve(field_name)
            )
        )
    return tuple(keys)


# -- the query --------------------------------------------------------------- #


@dataclass(frozen=True)
class SearchQuery:
    """A validated search request. Construction goes through :meth:`parse`."""

    term: str = ""
    search_fields: tuple[str, ...] = DEFAULT_SEARCH_FIELDS
    return_fields: tuple[str, ...] = DEFAULT_RETURN_FIELDS
    filter: FilterNode = None
    sort: tuple[SortKey, ...] = ()
    page_size: int = DEFAULT_PAGE_SIZE
    enable_suggested_query_results: bool = False
    repository: str = REPOSITORIES[0]
    schema: LibrarySchema = field(default_factory=LibrarySchema, repr=False, compare=False)

    @classmethod
    def parse(cls, body: Mapping[str, Any] | None, schema: LibrarySchema) -> "SearchQuery":
        """Validate a request body into a query, or raise :class:`SearchError`.

        An absent or empty body is a valid query that matches everything, which
        is the documented behaviour of the researched operation ("a completely
        empty query body, such as empty or {} queries all content").
        """
        body = body or {}
        if not isinstance(body, Mapping):
            raise SearchError("search body must be a JSON object")
        options = body.get("options") or {}
        if not isinstance(options, Mapping):
            raise SearchError("'options' must be an object")

        term = body.get("term", "")
        if term is None:
            term = ""
        if not isinstance(term, str):
            raise SearchError("'term' must be a string")
        term = term.strip()
        if len(term) > MAX_TERM_LENGTH:
            raise SearchError(TERM_TOO_LONG)

        search_fields = cls._resolve_fields(
            options.get("searchFields"), schema, DEFAULT_SEARCH_FIELDS, "searchFields"
        )
        return_fields = cls._resolve_fields(
            options.get("returnFields"), schema, DEFAULT_RETURN_FIELDS, "returnFields"
        )

        page_size = options.get("pageSize", DEFAULT_PAGE_SIZE)
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise SearchError(page_size_error(page_size))
        if not MIN_PAGE_SIZE <= page_size <= MAX_PAGE_SIZE:
            raise SearchError(page_size_error(page_size))

        suggested = options.get("enableSuggestedQueryResults", False)
        if not isinstance(suggested, bool):
            raise SearchError("'options.enableSuggestedQueryResults' must be a boolean")

        repository = body.get("repository", REPOSITORIES[0])
        if repository not in REPOSITORIES:
            raise SearchError(
                f"unknown repository {repository!r}; use one of {', '.join(REPOSITORIES)}"
            )

        return cls(
            term=term,
            search_fields=search_fields,
            return_fields=return_fields,
            filter=parse_filter(body.get("filter"), schema),
            sort=parse_sort(body.get("sort"), schema),
            page_size=page_size,
            enable_suggested_query_results=suggested,
            repository=repository,
            schema=schema,
        )

    @staticmethod
    def _resolve_fields(
        raw: Any, schema: LibrarySchema, default: tuple[str, ...], label: str
    ) -> tuple[str, ...]:
        """Validate a list of logical field names, falling back to the default.

        An unmapped name is accepted on purpose: it resolves to itself, so a team
        that just added ``data.region`` can search it today. What is rejected is
        a malformed ``custom.`` field, which cannot resolve to anything.
        """
        if raw is None:
            return default
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise SearchError(f"'options.{label}' must be a list of field names")
        if not raw:
            return ()
        names: list[str] = []
        for entry in raw:
            if not isinstance(entry, str) or not entry.strip():
                raise SearchError(f"'options.{label}' entries must be non-empty strings")
            name = entry.strip()
            if name.startswith("custom."):
                # Raises if the property name is missing.
                schema.resolve(name)
            names.append(name)
        return tuple(dict.fromkeys(names))

    def as_dict(self) -> dict[str, Any]:
        """Round-trippable body. Handy for saving a search verbatim."""
        body: dict[str, Any] = {"options": {}}
        if self.term:
            body["term"] = self.term
        options: dict[str, Any] = {}
        if self.search_fields != DEFAULT_SEARCH_FIELDS:
            options["searchFields"] = list(self.search_fields)
        if self.return_fields != DEFAULT_RETURN_FIELDS:
            options["returnFields"] = list(self.return_fields)
        if self.page_size != DEFAULT_PAGE_SIZE:
            options["pageSize"] = self.page_size
        if self.enable_suggested_query_results:
            options["enableSuggestedQueryResults"] = True
        if options:
            body["options"] = options
        if self.filter is not None:
            body["filter"] = _filter_to_body(self.filter)
        if self.sort:
            body["sort"] = [
                {"field": key.field, "direction": "desc" if key.descending else "asc"}
                for key in self.sort
            ]
        if self.repository != REPOSITORIES[0]:
            body["repository"] = self.repository
        return body

    def fingerprint(self) -> str:
        """Stable digest of everything that shapes a result page but not paging.

        Bound into the continuation token so that resuming a page with a
        different term, filter or sort is refused rather than silently returning
        a slice of the wrong result set.
        """
        payload = json.dumps(
            {
                "term": self.term,
                "search_fields": list(self.search_fields),
                "return_fields": list(self.return_fields),
                "filter": _filter_to_body(self.filter) if self.filter else None,
                "sort": [[k.field, k.descending] for k in self.sort],
                "repository": self.repository,
                "suggested": self.enable_suggested_query_results,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _filter_to_body(node: FilterNode) -> Any:
    """Serialise a parsed filter back to the request shape."""
    if node is None:
        return None
    if isinstance(node, Group):
        return {node.operator: [_filter_to_body(child) for child in node.children]}
    return {"field": node.field, "operator": node.operator, "value": node.value}


# -- continuation tokens ----------------------------------------------------- #

DEFAULT_TOKEN_TTL_SECONDS = 900


@dataclass(frozen=True)
class Cursor:
    """A paging position, bound to one query and one expiry."""

    offset: int
    expires_at: float
    fingerprint: str = ""


class CursorCodec:
    """Signs and verifies continuation tokens.

    A cursor is time-bounded on purpose. The research is explicit that
    "continuationToken is invalid or expired. Please regenerate it" and that
    consumers "must re-issue the search rather than resume indefinitely": the
    underlying content can change under a long-lived cursor, so an old one is
    not a promise of a stable page, it is a guess. Expiring it turns a silent
    wrong answer into an error the caller can act on.

    The token is opaque and tamper-evident (HMAC), not encrypted. It carries an
    offset, an expiry and the query fingerprint, and nothing else.
    """

    def __init__(self, secret: bytes | str, ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS) -> None:
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not secret:
            raise ValueError("a signing secret is required for continuation tokens")
        self._secret = secret
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, offset: int, fingerprint: str, *, now: float) -> str:
        """Mint a token for the next page."""
        payload = {
            "o": int(offset),
            "e": int(now + self.ttl_seconds),
            "f": fingerprint,
        }
        body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{_sign(self._secret, body)}"

    def redeem(self, token: str, *, now: float) -> Cursor:
        """Verify a token and return the position it points at.

        Every failure mode raises the same error, because from the caller's side
        they are the same problem: this token is no good, run the search again.
        """
        if not isinstance(token, str) or "." not in token:
            raise SearchError(TOKEN_INVALID)
        body, _, signature = token.partition(".")
        if not body or not hmac.compare_digest(_sign(self._secret, body), signature):
            raise SearchError(TOKEN_INVALID)
        try:
            payload = json.loads(_b64url_decode(body))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SearchError(TOKEN_INVALID) from exc
        if not isinstance(payload, dict) or "o" not in payload or "e" not in payload:
            raise SearchError(TOKEN_INVALID)
        try:
            offset, expires_at = int(payload["o"]), float(payload["e"])
        except (TypeError, ValueError) as exc:
            raise SearchError(TOKEN_INVALID) from exc
        if now >= expires_at or offset < 0:
            raise SearchError(TOKEN_INVALID)
        return Cursor(offset=offset, expires_at=expires_at, fingerprint=str(payload.get("f", "")))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: bytes, body: str) -> str:
    return _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())


# -- the discoverable contract ----------------------------------------------- #


def contract(
    schema: LibrarySchema, *, token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS
) -> dict[str, Any]:
    """Everything a client needs to build a valid query, in one response.

    Served so the UI can render the limits, the field pickers and the operator
    list from the server instead of hard-coding a second copy that drifts.
    """
    return {
        "limits": {
            "maxTermLength": MAX_TERM_LENGTH,
            "minPageSize": MIN_PAGE_SIZE,
            "maxPageSize": MAX_PAGE_SIZE,
            "defaultPageSize": DEFAULT_PAGE_SIZE,
            "maxFilterDepth": MAX_FILTER_DEPTH,
            "continuationTokenTtlSeconds": token_ttl_seconds,
        },
        "searchFields": list(DEFAULT_SEARCH_FIELDS),
        "returnFields": {
            "default": list(DEFAULT_RETURN_FIELDS),
            "optIn": list(OPT_IN_RETURN_FIELDS),
        },
        "operators": {"condition": list(SEARCH_OPERATORS), "group": list(FILTER_OPERATORS)},
        "repositories": list(REPOSITORIES),
        "customPropertyPrefix": "custom.",
        "customPropertyRoot": schema.properties_root,
        "primaryField": schema.primary_field,
        "sortRelevanceKey": RELEVANCE,
        "assetUrls": asset_url_expiry(),
    }
