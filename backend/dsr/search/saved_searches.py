"""Saved searches.

The researched flow has a step this app has to answer for itself: a client
details header exists "so the search activity data can be available to customers
for reporting and insight analytics purposes". That needs somewhere durable for
the activity to land.

Recording every query would mean a write per search, and a write means an audit
row -- which would drown the trail in read traffic and break the promise that
the audit log reads as a history of *changes*. So the durable surface here is an
explicit one: a person saves a search, that is one audited write, and the
library's own usage is reported from the audit trail and from the content that
was assembled.

A saved search is a record like any other, so the query body is stored verbatim
and can carry fields this code has never seen.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.search.contract import LibrarySchema, SearchQuery


class SavedSearches:
    """Named, re-runnable queries stored in an ordinary collection."""

    def __init__(
        self,
        store: Any,
        *,
        schema: LibrarySchema,
        collection: str = "library_search",
    ) -> None:
        self.store = store
        self.schema = schema
        self.collection = collection

    def save(
        self,
        name: str,
        body: Mapping[str, Any] | None,
        *,
        source: str,
        actor: str | None = None,
        client_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and store a query under a name.

        The query is validated before it is saved, not at run time, so a saved
        search is never something that will fail when someone re-runs it.

        ``source`` is required, and the route passes the path it is serving. The
        branch defaulted it to a literal ``"POST /api/library/searches"``; a
        domain function must not name a URL, because the audit row then records a
        route the app may have stopped serving.
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ValueError("a saved search needs a name")
        query = SearchQuery.parse(body, self.schema)

        payload: dict[str, Any] = {"name": cleaned, "query": query.as_dict()}
        # Anything else the caller sends rides along untouched: a team can
        # attach its own ticket id or owner without a migration.
        for key, value in (body or {}).items():
            if key not in ("term", "options", "filter", "sort", "repository"):
                payload.setdefault(key, value)
        if client_details:
            payload["client_application"] = client_details.get("application")
        return self.store.create(self.collection, payload, actor=actor, source=source)

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Saved searches, most recently updated first."""
        return self.store.list(self.collection, limit=limit)

    def get(self, record_id: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record["collection"] != self.collection:
            raise KeyError(record_id)
        return record

    def delete(self, record_id: str, *, source: str, actor: str | None = None) -> dict[str, Any]:
        """Remove a saved search. Soft delete, so the trail keeps the history.

        ``source`` is required for the same reason as :meth:`save`: the branch
        built the URL here, inside the domain, out of a string literal.
        """
        return self.store.delete(record_id, actor=actor, source=source)
