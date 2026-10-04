"""Building the room's read query, per vendor, within that vendor's limits.

User-flow step three: "Room issues a **read-only, field-scoped** CRM query: only
the mapped columns, paged."

Three words carry the whole rule. *Read-only* is why nothing here can produce a
write: the endpoint names are fixed and none of them is a vendor write. *Field
scoped* is why the column list comes from the field map and nowhere else.
*Paged* is why every plan carries a limit and a continuation cursor, and why the
limit is the vendor's number rather than the room's.

The plan is data, not a call. :func:`build_query` returns a :class:`ReadQuery`
that names the method, the path, the query parameters, the headers and the body
a vendor would receive. :mod:`dsr.crm_integration.sources` answers a plan against
the room's own copy of the vendor's tables. Nothing here opens a socket, which is
what makes the whole path testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from dsr.crm_integration import fieldmap, vocabulary
from dsr.crm_integration.errors import (
    BatchTooLarge,
    PageTooLarge,
    QueryTooLong,
    TooManyConditions,
)

#: The Salesforce API version this build writes into its query paths. The
#: research writes ``vXX.X`` throughout, so the concrete version is this build's
#: choice and is reported by :func:`describe`.
SALESFORCE_API_VERSION = "v61.0"

#: The Dataverse API version. The research quotes ``/api/data/v9.2/accounts`` for
#: its example, so that is the version this build addresses.
DATAVERSE_API_VERSION = "v9.2"

#: The endpoint names this module can produce. Every one of them is a read. The
#: list is closed on purpose: a plan that needed a name outside it would be a
#: write, and the room's read path must not contain one.
READ_ONLY_ENDPOINTS: tuple[str, ...] = (
    "query",
    "query_more",
    "entity_set",
    "batch_read",
    "get_by_email",
    "search",
)

#: The header the research names for the Salesforce batch size: "Query Options
#: Header Specifies options used in a query, such as the query results batch
#: size. Use this request header with the Query resource."
SALESFORCE_QUERY_OPTIONS_HEADER = "Sforce-Query-Options"


@dataclass(frozen=True)
class ReadQuery:
    """One field-scoped, paged, read-only request a vendor would receive."""

    system: str
    object_name: str
    endpoint: str
    method: str
    path: str
    select: tuple[str, ...]
    limit: int
    order_by: str
    conditions: tuple[str, ...]
    target_id: str = ""
    id_property: str = ""
    owner_id: str = ""
    match_column: str = ""
    continuation: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None

    @property
    def paging_mode(self) -> str:
        """Which continuation shape this vendor pages in."""
        return vocabulary.paging_mode(self.system)

    def as_dict(self) -> dict[str, Any]:
        """The plan as the query log stores it. JSON, so it round-trips."""
        return {
            "system": self.system,
            "object": self.object_name,
            "endpoint": self.endpoint,
            "method": self.method,
            "path": self.path,
            "select": list(self.select),
            "limit": self.limit,
            "order_by": self.order_by,
            "conditions": list(self.conditions),
            "target_id": self.target_id,
            "id_property": self.id_property,
            "owner_id": self.owner_id,
            "match_column": self.match_column,
            "continuation": self.continuation,
            "headers": dict(self.headers),
            "query": dict(self.query),
            "body": self.body,
            "paging_mode": self.paging_mode,
            "read_only": True,
        }


def build_query(
    system: str,
    object_name: str,
    *,
    field_map: dict[str, dict[str, str]],
    target_id: str = "",
    id_property: str = "id",
    id_column: str = "",
    owner_id: str = "",
    limit: int | None = None,
    order_by: str = "",
    conditions: list[str] | None = None,
    continuation: str = "",
    display_labels: bool = True,
    elastic: bool = False,
) -> ReadQuery:
    """Build the read plan for one object on one vendor.

    ``target_id`` is the record the room already resolved. Empty means "list
    what matches", which is the only case that reaches HubSpot's search endpoint
    and therefore the only case the 3,000-character and 10,000-result limits
    apply to.

    ``id_property`` names what ``target_id`` *is*. Anything other than ``"id"``
    means it is not a record id, and ``id_column`` then says which column to
    filter on. Without that, a Salesforce contact resolved by email would be read
    with ``Id = 'dana@northwind.example'``, which matches nothing and looks like
    a contact who does not exist.

    ``limit`` is clamped down to the vendor's page ceiling rather than refused,
    because a caller asking for more than a vendor returns has asked for the
    largest page available, not for an impossible one. A limit of zero or less
    *is* refused: that is a caller who has not decided yet, and defaulting it
    would hide that.
    """
    vendor = vocabulary.require_system(system)
    target = vocabulary.require_object(object_name)
    selected = fieldmap.read_set(field_map, vendor, target)
    ceiling = _page_ceiling(vendor, elastic)
    page = _resolve_limit(limit, ceiling)
    stated = [str(item).strip() for item in (conditions or []) if str(item).strip()]
    sort = str(order_by or _default_order(vendor, target)).strip() or _default_order(vendor, target)

    # The condition count is checked against the clauses the plan will actually
    # send, not against what the caller passed. The id clause and the owner
    # clause are added below, so a caller who sends exactly 500 conditions has
    # asked the vendor for 501 and would meet the vendor's own error rather than
    # this refusal. Counting first and adding afterwards would let that through.
    _check_conditions(vendor, len(stated) + (1 if target_id else 0) + (1 if owner_id else 0))

    # The column a non-record-id target is matched on. Empty for a record id,
    # because every vendor's record id lives in the same place in that case.
    match_column = ""
    if target_id and id_property and id_property != "id":
        match_column = id_column or _email_column(field_map, target) or id_property

    if vendor == "salesforce":
        return _salesforce_query(
            target,
            selected=selected,
            target_id=target_id,
            match_column=match_column,
            owner_id=owner_id,
            page=page,
            sort=sort,
            stated=stated,
            continuation=continuation,
        )
    if vendor == "dataverse":
        return _dataverse_query(
            target,
            selected=selected,
            target_id=target_id,
            match_column=match_column,
            owner_id=owner_id,
            page=page,
            sort=sort,
            stated=stated,
            continuation=continuation,
            display_labels=display_labels,
        )
    return _hubspot_query(
        target,
        selected=selected,
        target_id=target_id,
        id_property=id_property,
        owner_id=owner_id,
        page=page,
        sort=sort,
        stated=stated,
        continuation=continuation,
    )


def _email_column(field_map: dict[str, dict[str, str]], object_name: str) -> str:
    """The CRM column behind the object's email room field, if the map has one.

    Read from the field map rather than compiled in, for the same reason the read
    set is: a deployment that maps its contact email to a column this build has
    never heard of still gets a working email lookup.
    """
    for column, room_field in (field_map or {}).get(object_name, {}).items():
        if room_field == "contact_email":
            return column
    return ""


# --------------------------------------------------------------------------- #
# The vendors
# --------------------------------------------------------------------------- #


def _salesforce_query(
    object_name: str,
    *,
    selected: list[str],
    target_id: str,
    match_column: str,
    owner_id: str,
    page: int,
    sort: str,
    stated: list[str],
    continuation: str,
) -> ReadQuery:
    """SOQL, and the query locator that continues it.

    The research quotes the response fields and the continuation resource by
    name: "the response contains the first batch of records, a ``false`` value
    for ``done``, and a query locator. You can use the query locator with the
    Query More Results resource to retrieve the next batch of records."
    """
    entity = vocabulary.object_name("salesforce", object_name)
    if continuation:
        # The locator carries the whole query, so the plan stops building one.
        # Rebuilding it here would be a second answer to the same read, and the
        # two could disagree about which fields were selected.
        return ReadQuery(
            system="salesforce",
            object_name=object_name,
            endpoint="query_more",
            method="GET",
            path=f"/services/data/{SALESFORCE_API_VERSION}/query-all/{continuation}",
            select=tuple(selected),
            limit=page,
            order_by=sort,
            conditions=tuple(stated),
            target_id=target_id,
            id_property=match_column or "id",
            match_column=match_column,
            owner_id=owner_id,
            continuation=continuation,
            headers={SALESFORCE_QUERY_OPTIONS_HEADER: f"batchSize={page}"},
        )

    where = list(stated)
    if target_id:
        column = match_column or vocabulary.ID_FIELDS["salesforce"]
        where.insert(0, f"{column} = '{target_id}'")
    if owner_id:
        where.append(f"OwnerId = '{owner_id}'")
    soql = f"SELECT {', '.join(selected)} FROM {entity}"
    if where:
        soql += f" WHERE {' AND '.join(where)}"
    soql += f" ORDER BY {sort} LIMIT {page}"
    return ReadQuery(
        system="salesforce",
        object_name=object_name,
        endpoint="query",
        method="GET",
        path=f"/services/data/{SALESFORCE_API_VERSION}/query",
        select=tuple(selected),
        limit=page,
        order_by=sort,
        conditions=tuple(where),
        target_id=target_id,
        id_property=match_column or "id",
        owner_id=owner_id,
        match_column=match_column,
        headers={SALESFORCE_QUERY_OPTIONS_HEADER: f"batchSize={page}"},
        query={"q": soql},
    )


def _dataverse_query(
    object_name: str,
    *,
    selected: list[str],
    target_id: str,
    match_column: str,
    owner_id: str,
    page: int,
    sort: str,
    stated: list[str],
    continuation: str,
    display_labels: bool,
) -> ReadQuery:
    """``$select`` / ``$filter`` / ``$orderby`` / ``$top``, and the Prefer header.

    The research names all four options and the header, and says what the header
    buys: ``"statecode@OData.Community.Display.V1.FormattedValue": "Active"``.
    The header is sent when the row says the vendor can annotate, and omitted
    when it cannot - which is the researched capability check, not an
    optimisation.
    """
    entity_set = vocabulary.object_name("dataverse", object_name)
    clauses = list(stated)
    if target_id:
        column = match_column or vocabulary.ID_FIELDS["dataverse"]
        clauses.insert(0, f"{column} eq '{target_id}'")
    if owner_id:
        clauses.append(f"ownerid eq '{owner_id}'")

    params = {
        "$select": ",".join(selected),
        "$orderby": sort,
        "$top": str(page),
        "$count": "true",
    }
    if clauses:
        params["$filter"] = " and ".join(clauses)

    headers: dict[str, str] = {}
    if display_labels and vocabulary.supports_display_labels("dataverse"):
        headers["Prefer"] = vocabulary.DATAVERSE_DISPLAY_PREFERENCE

    if continuation:
        # "@odata.nextLink" carries its own $skiptoken, so the cursor replaces
        # the filter rather than adding to it.
        path = continuation
        params.pop("$filter", None)
        return ReadQuery(
            system="dataverse",
            object_name=object_name,
            endpoint="entity_set",
            method="GET",
            path=path,
            select=tuple(selected),
            limit=page,
            order_by=sort,
            conditions=tuple(clauses),
            target_id=target_id,
            id_property=match_column or "id",
            match_column=match_column,
            owner_id=owner_id,
            continuation=continuation,
            headers=headers,
            query=dict(params),
        )

    return ReadQuery(
        system="dataverse",
        object_name=object_name,
        endpoint="entity_set",
        method="GET",
        path=f"/api/data/{DATAVERSE_API_VERSION}/{entity_set}",
        select=tuple(selected),
        limit=page,
        order_by=sort,
        conditions=tuple(clauses),
        target_id=target_id,
        id_property=match_column or "id",
        match_column=match_column,
        owner_id=owner_id,
        headers=headers,
        query=params,
    )


def _hubspot_query(
    object_name: str,
    *,
    selected: list[str],
    target_id: str,
    id_property: str,
    owner_id: str,
    page: int,
    sort: str,
    stated: list[str],
    continuation: str,
) -> ReadQuery:
    """One of the three read endpoints the research names, chosen by what is known.

    With a resolved record id it is a batch read. With an email and nothing else
    it is the single-record lookup, because ``idProperty`` is what makes that
    work. With neither it is search, and only search carries the 3,000-character
    and 10,000-result limits.

    "By default the ``id`` values in the request refer to the Record ID, so the
    ``idProperty`` parameter is not required when retrieving by Record ID, but
    always required when retrieving by email or a custom unique ID property."
    """
    object_type = vocabulary.object_name("hubspot", object_name)
    base = f"/crm/v3/objects/{object_type}"
    properties = [name for name in selected if name != vocabulary.ID_FIELDS["hubspot"]]

    if target_id and id_property == "id":
        inputs = [{"id": target_id}]
        _check_batch_size(inputs)
        body: dict[str, Any] = {"properties": properties, "inputs": inputs}
        if owner_id:
            body["properties"] = [*properties, "hubspot_owner_id"]
        filters = _hubspot_filters(owner_id, stated)
        if filters:
            body["filterGroups"] = [{"filters": filters}]
        _check_query_length(body)
        return ReadQuery(
            system="hubspot",
            object_name=object_name,
            endpoint="batch_read",
            method="POST",
            path=f"{base}/batch/read",
            select=tuple(selected),
            limit=page,
            order_by=sort,
            conditions=tuple(stated),
            target_id=target_id,
            id_property=id_property,
            owner_id=owner_id,
            headers={},
            body=body,
        )

    if target_id and id_property != "id":
        path = f"{base}/{target_id}"
        params = {"properties": ",".join(properties), "idProperty": id_property}
        if continuation:
            params["after"] = continuation
        return ReadQuery(
            system="hubspot",
            object_name=object_name,
            endpoint="get_by_email",
            method="GET",
            path=path,
            select=tuple(selected),
            limit=1,
            order_by=sort,
            conditions=tuple(stated),
            target_id=target_id,
            id_property=id_property,
            owner_id=owner_id,
            continuation=continuation,
            query=params,
        )

    filters = _hubspot_filters(owner_id, stated)
    body = {
        "filterGroups": [{"filters": filters}] if filters else [],
        "properties": properties,
        "limit": page,
        "sorts": [{"propertyName": sort, "direction": "DESCENDING"}],
    }
    if continuation:
        body["after"] = continuation
    _check_query_length(body)
    return ReadQuery(
        system="hubspot",
        object_name=object_name,
        endpoint="search",
        method="POST",
        path=f"{base}/search",
        select=tuple(selected),
        limit=page,
        order_by=sort,
        conditions=tuple(stated),
        id_property=id_property,
        owner_id=owner_id,
        continuation=continuation,
        body=body,
    )


def _hubspot_filters(owner_id: str, stated: list[str]) -> list[dict[str, str]]:
    """Turn the room's ``property operator value`` conditions into HubSpot filters.

    A condition the room did not write in that shape is dropped rather than
    guessed at, and the caller is told how many were dropped by
    :func:`findings` on the query. HubSpot's filter grammar is not SOQL's and
    not OData's, and a condition translated into the wrong one would return the
    wrong rows while looking correct.
    """
    filters: list[dict[str, str]] = []
    for condition in stated:
        parts = condition.split(None, 2)
        if len(parts) != 3:
            continue
        filters.append({"propertyName": parts[0], "operator": parts[1], "value": parts[2]})
    if owner_id:
        filters.append({"propertyName": "hubspot_owner_id", "operator": "EQ", "value": owner_id})
    return filters


def dropped_conditions(query: ReadQuery) -> list[str]:
    """Conditions this vendor's grammar could not carry, named.

    Only HubSpot needs one, and only because its filter grammar differs from the
    other two. Reporting the drop is what keeps a read from being quietly
    narrower than the room asked for.
    """
    if query.system != "hubspot":
        return []
    carried = {
        filter_["propertyName"]
        for group in (query.body or {}).get("filterGroups", [])
        for filter_ in group.get("filters", [])
    }
    dropped = []
    for condition in query.conditions:
        parts = condition.split(None, 2)
        if len(parts) == 3 and parts[0] in carried:
            continue
        dropped.append(condition)
    return dropped


# --------------------------------------------------------------------------- #
# The limits
# --------------------------------------------------------------------------- #


def _page_ceiling(system: str, elastic: bool) -> int:
    """The largest page one vendor returns, with the elastic table's own number.

    "Without this limit, Dataverse returns up to 5,000 standard table rows and
    500 elastic table rows." A room reading an elastic table and asking for 5,000
    would be asking for a page that does not exist.
    """
    if system == "dataverse" and elastic:
        return vocabulary.DATAVERSE_ELASTIC_ROW_LIMIT
    return vocabulary.page_limit(system)


def _resolve_limit(limit: int | None, ceiling: int) -> int:
    """The page size, bounded above by the vendor and below by one.

    No limit named means the room's default page, not the vendor's ceiling. A
    ceiling is the largest answer a vendor can give, and a panel read that asks
    for 2,000 rows to show one deal pays the vendor's biggest-page cost for the
    smallest answer. See :data:`dsr.crm_integration.inferences.DEFAULT_PAGE`.
    """
    if limit is None:
        return min(vocabulary.DEFAULT_PAGE, ceiling)
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        return ceiling
    if wanted <= 0:
        raise PageTooLarge(
            f"limit must be a positive number of rows; {limit!r} is not. A read of zero "
            "rows is a caller that has not decided yet."
        )
    return min(wanted, ceiling)


def _check_conditions(system: str, count: int) -> None:
    """Refuse a filter longer than the vendor accepts, in the vendor's words."""
    ceiling = vocabulary.condition_limit(system)
    if ceiling is None or count <= ceiling:
        return
    raise TooManyConditions(
        f"{count} conditions exceed the {ceiling} this vendor accepts. "
        f"{vocabulary.MAX_CONDITIONS_MESSAGE}"
    )


def _check_batch_size(inputs: list[dict[str, str]]) -> None:
    """Refuse a batch read larger than the vendor's, quoting it."""
    if len(inputs) <= vocabulary.HUBSPOT_BATCH_READ_LIMIT:
        return
    raise BatchTooLarge(
        f"{len(inputs)} ids in one batch read exceeds the "
        f"{vocabulary.HUBSPOT_BATCH_READ_LIMIT} this vendor accepts. Read the ids in "
        f"batches of {vocabulary.HUBSPOT_BATCH_READ_LIMIT} or fewer."
    )


def _check_query_length(body: dict[str, Any]) -> None:
    """Refuse a query longer than the vendor accepts, quoting it."""
    text = json.dumps(body, separators=(",", ":"), sort_keys=True)
    if len(text) <= vocabulary.HUBSPOT_QUERY_CHARACTER_LIMIT:
        return
    raise QueryTooLong(
        f"the assembled query is {len(text)} characters, over the "
        f"{vocabulary.HUBSPOT_QUERY_CHARACTER_LIMIT} this vendor accepts. Narrow the "
        "field map or filter on the record id rather than on a property list."
    )


def _default_order(system: str, object_name: str) -> str:
    """The order a read uses when the caller names none.

    Salesforce's ``LastModifiedDate`` and HubSpot's ``hs_lastmodifieddate`` are
    the columns the vendors maintain for exactly this. Dataverse has no such
    column on every table, so it orders by the entity's primary name column,
    which the research shows is how it selects one row (``$top=1`` with an
    ``$orderby``).
    """
    if system == "salesforce":
        return "LastModifiedDate"
    if system == "hubspot":
        return "hs_lastmodifieddate"
    return "name"


def describe() -> dict[str, Any]:
    """Every vendor's endpoints, limits and capability, as data."""
    return {
        "api_versions": {
            "salesforce": SALESFORCE_API_VERSION,
            "dataverse": DATAVERSE_API_VERSION,
        },
        "query_options_header": {
            "salesforce": SALESFORCE_QUERY_OPTIONS_HEADER,
        },
        "display_label_preference": vocabulary.DATAVERSE_DISPLAY_PREFERENCE,
        "display_label_annotation": vocabulary.DATAVERSE_DISPLAY_ANNOTATION,
        "read_only_endpoints": list(READ_ONLY_ENDPOINTS),
        "objects": {
            system: dict(vocabulary.OBJECT_NAMES[system]) for system in vocabulary.CRM_SYSTEMS
        },
        "paging": {
            "modes": vocabulary.PAGING_MODES,
            "fields": vocabulary.PAGING_FIELDS,
            "record_arrays": vocabulary.RECORD_ARRAY_FIELDS,
        },
        "limits": {
            "salesforce": {
                "synchronous_records_per_request": vocabulary.SALESFORCE_SYNCHRONOUS_RECORD_LIMIT,
            },
            "dataverse": {
                "standard_rows_per_request": vocabulary.DATAVERSE_STANDARD_ROW_LIMIT,
                "elastic_rows_per_request": vocabulary.DATAVERSE_ELASTIC_ROW_LIMIT,
                "max_conditions": vocabulary.DATAVERSE_MAX_CONDITIONS,
                "max_conditions_message": vocabulary.MAX_CONDITIONS_MESSAGE,
            },
            "hubspot": {
                "batch_read_ids": vocabulary.HUBSPOT_BATCH_READ_LIMIT,
                "objects_per_page": vocabulary.HUBSPOT_PAGE_LIMIT,
                "query_characters": vocabulary.HUBSPOT_QUERY_CHARACTER_LIMIT,
                "search_results": vocabulary.HUBSPOT_SEARCH_RESULT_LIMIT,
                "search_requests_per_second": vocabulary.HUBSPOT_SEARCH_REQUESTS_PER_SECOND,
            },
        },
    }


__all__ = [
    "DATAVERSE_API_VERSION",
    "READ_ONLY_ENDPOINTS",
    "SALESFORCE_API_VERSION",
    "SALESFORCE_QUERY_OPTIONS_HEADER",
    "ReadQuery",
    "build_query",
    "describe",
    "dropped_conditions",
]
