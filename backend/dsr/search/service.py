"""Library search and room assembly over the audited store.

This is the only module that knows both the query contract and the store. Reads
go through :class:`~dsr.store.RecordStore`, which owns the single audited
connection, so a search cannot reach past the wrapper; the one write this module
performs (assembling content into a room) goes through ``bulk_create`` and lands
as a single transaction with a single audit row.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from dsr.search.contract import (
    DEFAULT_RETURN_FIELDS,
    DEFAULT_SEARCH_FIELDS,
    OPT_IN_RETURN_FIELDS,
    RELEVANCE,
    TOKEN_INVALID,
    CursorCodec,
    FilterNode,
    Group,
    LibrarySchema,
    SearchError,
    SearchQuery,
    asset_url_expiry,
    contract,
    get_path,
)
from dsr.search.matching import Match, Scorer, suggest, tokenize, vocabulary
from dsr.search.reads import scan

DEFAULT_SUGGESTION_LIMIT = 3
"""How many related terms to try before giving up on a zero-hit query."""


def _sort_key(value: Any) -> tuple[int, tuple[int, Any]]:
    """A comparison key that cannot raise, whatever a record holds.

    Three parts: missing values last, then a type rank so numbers sort ahead of
    text, then the value itself. The rank matters because a sort field is
    schema-flexible -- a team can sort on a path where one record stored a
    number and another stored a label, and comparing ``3.0`` to ``"abc"`` would
    turn a sort into a 500.
    """
    if value is None:
        return (1, (2, ""))
    if isinstance(value, bool):
        return (0, (0, float(value)))
    if isinstance(value, (int, float)):
        return (0, (0, float(value)))
    return (0, (1, str(value)))


@dataclass(frozen=True)
class LibraryHit:
    """A ranked record before it is projected onto the requested return fields."""

    record: Mapping[str, Any]
    score: float


class LibrarySearch:
    """Runs a :class:`SearchQuery` against the library collection.

    ``collection`` names the records that make up the library. It is
    configuration, not a schema: pointing a deployment at a different collection
    is a constructor argument, and the fields inside each record are resolved
    through :class:`~dsr.search.contract.LibrarySchema`.
    """

    def __init__(
        self,
        store: Any,
        *,
        schema: LibrarySchema | None = None,
        codec: CursorCodec | None = None,
        collection: str = "document",
        weights: Mapping[str, float] | None = None,
        suggestion_limit: int = DEFAULT_SUGGESTION_LIMIT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.schema = schema or LibrarySchema()
        self.codec = codec or CursorCodec(b"dsr-library-search")
        self.collection = collection
        self.scorer = Scorer(self.schema, weights)
        self.suggestion_limit = suggestion_limit
        self.clock = clock

    # -- discovery ---------------------------------------------------------- #

    def contract(self) -> dict[str, Any]:
        """The operational contract, for clients that build queries."""
        return contract(self.schema, token_ttl_seconds=self.codec.ttl_seconds)

    def fields_in_use(self) -> list[dict[str, Any]]:
        """Fields that actually carry a value in the library collection.

        The counterpart to the store's schema discovery: a client asks what it
        can filter on and gets the answer from the data, so a field added
        yesterday shows up without a code change.

        Built from the dynamic index, which already flattens nested JSON into
        dotted paths -- that is how ``properties.Region`` appears at all, since
        the logical name ``properties`` only resolves to the object as a whole.
        The logical mappings are unioned in on top so a caller who knows a
        library calls its title ``name`` can still see where that resolves to.
        """
        counts: dict[str, int] = {
            entry["path"]: int(entry["records"]) for entry in self.store.fields(self.collection)
        }

        records = scan(self.store, self.collection)
        for record in records:
            data = record.get("data") or {}
            for name in _logical_names(self.schema):
                path = self.schema.resolve(name)
                if get_path(data, path) is not None:
                    counts[path] = counts.get(path, 0) + 1

        return [{"path": path, "records": count} for path, count in sorted(counts.items())]

    # -- the search itself -------------------------------------------------- #

    def run(
        self,
        query: SearchQuery,
        *,
        continuation_token: str | None = None,
        client_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a query and return one page of results.

        Reads do not audit, so a search leaves no trace in the trail; the
        durable record of *why* content was added to a room is the assembly, not
        the query.
        """
        started = time.perf_counter()
        fingerprint = query.fingerprint()

        offset = 0
        if continuation_token:
            cursor = self.codec.redeem(continuation_token, now=self.clock())
            if cursor.fingerprint and cursor.fingerprint != fingerprint:
                # The token is valid but points into a different result set.
                # Refuse rather than return a page of the wrong documents.
                raise SearchError(TOKEN_INVALID)
            offset = cursor.offset

        records = scan(self.store, self.collection)
        candidates = self._apply_filter(records, query.filter)
        matches, actual_term = self._match(candidates, query)

        ordered = self._sort(matches, query)
        total = len(ordered)
        page = ordered[offset : offset + query.page_size] if query.page_size else []

        token = None
        if ordered and offset + len(page) < total:
            token = self.codec.issue(offset + len(page), fingerprint, now=self.clock())

        elapsed = (time.perf_counter() - started) * 1000
        result: dict[str, Any] = {
            "totalCount": total,
            "documents": [self._project(hit.record, query) for hit in page],
            "continuationToken": token,
            "actualSearchTerm": actual_term,
            "queryTimeInMs": round(elapsed, 3),
            "serviceTimeInMs": round(elapsed, 3),
            "pageSize": query.page_size,
            "repository": query.repository,
            "searchedCollection": self.collection,
            "returnFields": list(query.return_fields),
            "scanned": len(records),
            "clientDetails": dict(client_details) if client_details else None,
            **asset_url_expiry(),
        }
        return result

    # -- internals ---------------------------------------------------------- #

    def _apply_filter(
        self, records: Sequence[Mapping[str, Any]], node: FilterNode
    ) -> list[Mapping[str, Any]]:
        """Evaluate a filter expression against already-retrieved records.

        One code path, on purpose. An earlier version short-circuited ``equal``
        and ``in`` through the dynamic index, which is the obvious thing to reach
        for -- and it was wrong twice over. The records are already in hand by the
        time the filter runs, so the index could not save any work; and because
        the index stores numbers numerically and text as text, a filter that
        compared a numeric field against a string value would find nothing while
        the in-memory comparison found a match. Two evaluation paths that can
        disagree are worse than one that is merely slower, and this one is
        neither.
        """
        if node is None:
            return list(records)

        if isinstance(node, Group):
            branches = [self._apply_filter(records, child) for child in node.children]
            if node.operator == "or":
                keep = {record["id"] for branch in branches for record in branch}
                return [record for record in records if record["id"] in keep]
            return branches[0] if len(branches) == 1 else _intersect(branches)

        return [
            record
            for record in records
            if self._matches_condition(record, node)
        ]

    @staticmethod
    def _matches_condition(record: Mapping[str, Any], node: Any) -> bool:
        data = record.get("data") or {}
        actual = get_path(data, node.path)
        return _compare(node.operator, actual, node.value)

    def _match(
        self, records: Sequence[Mapping[str, Any]], query: SearchQuery
    ) -> tuple[list[LibraryHit], str | None]:
        """Score every candidate against the term, broadening if enabled.

        The researched operation's zero-hit broadening is reproduced: when
        ``enableSuggestedQueryResults`` is set and the term finds nothing, the
        search is retried with related terms, and the term that actually matched
        is reported as ``actualSearchTerm`` so the caller can tell the user the
        results are not for the words they typed.
        """
        tokens = tokenize(query.term)
        hits = self._score(records, tokens, query.search_fields)
        if hits or not tokens or not query.enable_suggested_query_results:
            return hits, None

        counts = vocabulary(records, self.scorer, query.search_fields)
        for alternative in suggest(query.term, counts, limit=self.suggestion_limit):
            retry_tokens = tokenize(alternative)
            if not retry_tokens:
                continue
            retry = self._score(records, retry_tokens, query.search_fields)
            if retry:
                return retry, alternative
        return hits, None

    def _score(
        self, records: Sequence[Mapping[str, Any]], tokens: Sequence[str], search_fields: Sequence[str]
    ) -> list[LibraryHit]:
        hits: list[LibraryHit] = []
        for record in records:
            match: Match | None = self.scorer.score(record, tokens, search_fields)
            if match is not None:
                hits.append(LibraryHit(record=record, score=match.score))
        return hits

    def _sort(self, hits: list[LibraryHit], query: SearchQuery) -> list[LibraryHit]:
        """Order the full result set before slicing a page out of it.

        Id is the final tie-break, always ascending: the researched API does not
        promise a stable order for equal scores, and a paging cursor over an
        unstable order is a cursor that drops results.
        """
        if not query.sort:
            return sorted(hits, key=lambda hit: (-hit.score, hit.record["id"]))

        ordered = list(hits)
        for key in reversed(query.sort):
            if key.field == RELEVANCE:
                ordered.sort(key=lambda hit: (-hit.score, hit.record["id"]))
                continue
            reverse = key.descending
            ordered.sort(
                key=lambda hit, path=key.path: _sort_key(
                    get_path(hit.record.get("data") or {}, path)
                ),
                reverse=reverse,
            )
        return ordered

    def _project(self, record: Mapping[str, Any], query: SearchQuery) -> dict[str, Any]:
        """Reduce a record to exactly the requested return fields.

        ``returnFields`` is honoured literally, because that is the point of it:
        a caller that omits ``properties`` is avoiding a payload it does not
        need. ``repository`` is the one synthesised value, since the repository
        being searched is a property of the query rather than of the record.
        """
        data = record.get("data") or {}
        document: dict[str, Any] = {}
        for name in query.return_fields:
            if name == "repository":
                document["repository"] = query.repository
            elif name == "id":
                document["id"] = record["id"]
            elif name == "versionId":
                document["versionId"] = get_path(data, self.schema.resolve("versionId"))
            else:
                document[name] = get_path(data, self.schema.resolve(name))
        document["_matchedFields"] = list(self._matched_fields(record, query))
        return document

    def _matched_fields(self, record: Mapping[str, Any], query: SearchQuery) -> tuple[str, ...]:
        """Which searched fields this record matched on. Diagnostic, not contract."""
        tokens = tokenize(query.term)
        if not tokens or not query.search_fields:
            return ()
        found: list[str] = []
        for field_name in query.search_fields:
            text = self.scorer.read(record, field_name)
            if set(tokens) & text.tokens:
                found.append(field_name)
        return tuple(found)


def _intersect(branches: Sequence[Sequence[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """Intersect record-id sets, preserving the order of the first branch."""
    if not branches:
        return []
    ids = [{record["id"] for record in branch} for branch in branches]
    combined = set.intersection(*ids)
    return [record for record in branches[0] if record["id"] in combined]


def _compare(operator: str, actual: Any, expected: Any) -> bool:
    """Apply one conditional operator.

    Comparison is type-tolerant on purpose: the store holds whatever JSON a team
    stored, so ``equal`` compares text for text and numbers for numbers, and a
    number compared against text is simply not a match rather than an exception.
    """
    if operator == "in":
        return any(_compare("equal", actual, candidate) for candidate in expected)
    if operator == "equal":
        if isinstance(actual, bool) or isinstance(expected, bool):
            return actual is expected or actual == expected
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return float(actual) == float(expected)
        if actual is None or expected is None:
            return actual is expected
        return str(actual) == str(expected)
    if operator in ("greaterThan", "greaterThanOrEqual", "lessThan", "lessThanOrEqual"):
        return _compare_range(operator, actual, expected)
    raise SearchError(f"unknown filter operator {operator!r}")


def _compare_range(operator: str, actual: Any, expected: Any) -> bool:
    """Ordered comparison across numbers, dates-as-text, and plain text."""
    if actual is None or expected is None:
        return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    left: Any
    right: Any
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        left, right = float(actual), float(expected)
    else:
        # RFC 3339 timestamps compare correctly as strings, which is why the
        # research can specify date-typed custom properties without a date type
        # in the filter language.
        left, right = str(actual), str(expected)
    if operator == "greaterThan":
        return left > right
    if operator == "greaterThanOrEqual":
        return left >= right
    if operator == "lessThan":
        return left < right
    return left <= right


class LibraryAssembler:
    """Adds library documents to a room.

    One call is one transaction and one audit row, whichever way it goes: a
    half-assembled room is worse than a rejected one, because the seller cannot
    tell from the room which documents actually made it in.
    """

    def __init__(
        self,
        store: Any,
        *,
        room_collection: str = "room",
        content_collection: str = "room_content",
    ) -> None:
        self.store = store
        self.room_collection = room_collection
        self.content_collection = content_collection

    def assemble(
        self,
        room_id: str,
        items: Iterable[Mapping[str, Any]],
        *,
        source: str,
        actor: str | None = None,
        request_id: str | None = None,
        search_id: str | None = None,
        client_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Attach documents to a room, skipping ones already attached.

        Each item needs at least an ``id``: the library record it came from. Any
        other key is copied into the stored payload, so the fields a caller wants
        to keep alongside the content are the caller's business, not a schema
        decision made here.

        ``source`` is required and the caller passes the path it is serving. The
        branch defaulted it to the literal ``"POST /api/library/assemble"``, which
        is the defect the port brief calls out: a domain function that hard-codes
        a URL records an audit row naming a route the app might have stopped
        serving. There is no default to fall back on, so it cannot rot.
        """
        room = self.store.get(room_id)
        if room is None or room["collection"] != self.room_collection:
            raise KeyError(room_id)

        existing = {
            record["data"].get("content_id")
            for record in scan(self.store, self.content_collection, room_id=room_id)
        }

        added: list[Mapping[str, Any]] = []
        skipped: list[str] = []
        for item in items:
            content_id = str(item.get("id") or item.get("content_id") or "").strip()
            if not content_id:
                raise SearchError("each item needs an 'id': the library record it came from")
            if content_id in existing:
                skipped.append(content_id)
                continue
            existing.add(content_id)
            payload = {key: value for key, value in item.items() if key != "id"}
            payload["content_id"] = content_id
            payload.setdefault("source", "library")
            if search_id:
                payload["search_id"] = search_id
            if client_details:
                payload["client_application"] = client_details.get("application")
            added.append(payload)

        created = []
        if added:
            # One transaction, one audit row, whatever the payload looks like.
            #
            # The branch also passed ``context={content_ids, search_id,
            # client_application}`` so the provenance would land in the audit row's
            # ``after_state`` rather than only on the records. That keyword only
            # exists because the branch edited ``dsr/db/audited.py``, which is a
            # shared file, so the port cannot pass it. The provenance is
            # therefore recorded on the content records themselves, which is where
            # the branch read it from anyway. The one-audit-row guarantee - the
            # part the product actually rests on - is unchanged. Promoting the
            # keyword is raised in this port's report for an integrator.
            created = self.store.bulk_create(
                self.content_collection,
                added,
                room_id=room_id,
                actor=actor,
                source=source,
                request_id=request_id,
            )

        return {
            "room_id": room_id,
            "collection": self.content_collection,
            "added": [record["id"] for record in created],
            "added_count": len(created),
            "skipped": skipped,
            "skipped_count": len(skipped),
        }


def _logical_names(schema: LibrarySchema) -> list[str]:
    """Field names worth probing for the discovery endpoint."""
    names = list(DEFAULT_RETURN_FIELDS) + list(OPT_IN_RETURN_FIELDS) + list(DEFAULT_SEARCH_FIELDS)
    names.extend(schema.fields)
    return list(dict.fromkeys(names))
