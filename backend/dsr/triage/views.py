"""Saved views: the researched view, its lifecycle, and everything it reads.

The user flow this lands, step by step:

1. "Open the Dock **Workspaces** dashboard." -> :meth:`TriageBoard.dashboard`
2. "Click **Add view** and start from a default view: **All Workspaces**, **My
   Workspaces**, **Active Pipeline**, **Deal Desk**, or **Implementations**." ->
   :meth:`TriageBoard.create_view` with ``base`` naming one of the five.
3. "**Clone** the view, then filter/sort by owner, creation date, recent client
   activity, CRM stage, workspace type." -> :meth:`TriageBoard.clone_view` and
   :meth:`TriageBoard.update_view`; the filter and sort engine is
   :mod:`dsr.triage.filters`.
4. "Edit and rearrange **columns**." -> ``columns`` is an ordered list and
   :meth:`TriageBoard.update_view` replaces it wholesale, which is what
   rearranging is.
5. "Save as a **private view** (personal) or **public view** (team); Dock
   remembers the views you had open." -> ``visibility``, and
   :meth:`TriageBoard.remember_open_views`.

Three researched decisions this module is careful about
---------------------------------------------------
**A clone is private.** "You can also **clone** existing views to make your own
customized copy", and a custom copy is one of the "**private views** for
yourself". So :meth:`TriageBoard.clone_view` always produces a private view
owned by the actor, even when the source was a public team view.

**A private view is not merely unlisted.** It is unreadable, unwritable and
undeletable by anyone but its owner, and it answers 404 rather than 403 - a view
somebody chose to keep private should not be confirmable to another user by the
status code alone.

**The open set survives a deletion.** "We'll remember which views you had open
the next time you open the Workspaces dashboard." A view deleted since then must
not break the dashboard, so :meth:`TriageBoard.open_views` resolves what it can
and reports the ids it could not.

Every write here takes a required ``source``, and the HTTP layer supplies it
from ``router.prefix``. A domain method that hard-coded a URL would put a path
in the audit log that the app may not serve, which is a defect this repository
has shipped once already.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.store import RecordStore
from dsr.triage import filters as engine, vocabulary as vocab
from dsr.triage.errors import TriageError
from dsr.triage.fields import as_text, first_text, utcnow_iso
from dsr.triage.rows import TEMPLATE_COLLECTION, JoinIndex, declared_sections

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: A saved view. Everything the view holds is ordinary JSON in ``data``, so a
#: team can add a column key, a filter, or a sort without a migration - which is
#: the research's own extensibility claim: "the column/filter set is user-defined
#: so new CRM fields flow through automatically".
VIEW_COLLECTION = "saved_view"

#: One record per user holding the views they last had open. Keyed on ``actor``,
#: so it is a per-account record rather than a session: "The views you had open
#: are unique to your user account."
DASHBOARD_STATE_COLLECTION = "dashboard_state"

#: Per-workspace rules for which page sections to show. A separate collection
#: rather than a key on the workspace, so a section rule is audited on its own
#: and a team can query it with ``?where={"section": "pricing"}``.
SECTION_RULE_COLLECTION = "workspace_section_rule"

#: The column set a view gets when it is created with no default and no columns.
#: Deliberately short: a blank view that starts with all twenty columns is a
#: table nobody can read, and every column is one click away.
BLANK_COLUMNS: tuple[str, ...] = (
    "dock.name",
    "dock.account",
    "dock.owner",
    "dock.stage",
    "engagement.last_client_view",
)

#: How many rows a triage table returns before paging. The joined row is small,
#: but a team with a large pipeline should page rather than be refused.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

#: The page size every unbounded list in this module reads with. A user's view
#: list, a template list and a ruleset are all small; 1000 is the store's own
#: per-call ceiling, and asking for it means one call rather than a paging loop
#: whose page count nobody wants to reason about.
LIST_LIMIT = 1000

#: Why a section is visible or not. Named so a client can render a reason rather
#: than infer one from a boolean.
REASON_NO_RULE = "no_rule"
REASON_RULE_MATCHED = "rule_matched"
REASON_RULE_NOT_MATCHED = "rule_not_matched"


def parse_properties(raw: str | None) -> list[str] | None:
    """Read the researched ``properties`` query parameter.

    "Endpoints that return a resource accept a ``properties`` query parameter
    that controls which fields are included in the response. If you omit it, the
    response contains only the resource's ``id``, ``object``, and ``url``."

    ``None`` means omitted, which is a different instruction from an empty
    string and is preserved as such. An empty string happens to select nothing
    and so produces the same three fields, but the two answers stay
    distinguishable in the response's ``requested`` field.
    """
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


class TriageBoard:
    """The saved-view side of the Digital Sales Room.

    Built per request from ``StoreDep``, like every other feature's engine: it
    holds nothing but the store handle, so keeping it on ``app.state`` would mean
    editing the shared app for no gain.

    Every method that writes takes a required ``source`` keyword. That is not
    ceremony: hard rule 4 of the build brief is that a write's audit row must
    name the route that served it, and a default value would let the next
    contributor skip it.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- discovery ---------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        return vocab.catalog()

    def defaults(self) -> list[dict[str, Any]]:
        """The five default views a rep starts from, ready to be added."""
        return [vocab.default_view_summary(view) for view in vocab.DEFAULT_VIEWS]

    def default_view(self, view_id: str) -> dict[str, Any] | None:
        definition = vocab.default_view(view_id)
        return vocab.default_view_summary(definition) if definition is not None else None

    # -- views -------------------------------------------------------------- #

    def list_views(
        self, actor: str | None = None, visibility: str | None = None
    ) -> list[dict[str, Any]]:
        """Every view ``actor`` may see: their own private ones plus all public.

        Filtered in Python rather than with ``find()`` on two separate queries,
        because the rule is a disjunction over a value inside the payload and the
        store's ``where`` is a conjunction. The set is a user's view list, so it
        is bounded by the number of views a team has.
        """
        wanted = as_text(visibility).lower() or None
        if wanted is not None and wanted not in vocab.VISIBILITIES:
            raise TriageError(
                f"visibility must be one of {list(vocab.VISIBILITIES)}, got {visibility!r}"
            )
        rows = []
        for record in self.store.list(VIEW_COLLECTION, limit=LIST_LIMIT, order_by="created_at"):
            data = record.get("data") or {}
            if wanted and as_text(data.get("visibility")) != wanted:
                continue
            if not self.can_read(data, actor):
                continue
            rows.append(self.summarise(record))
        return rows

    def get_view(self, view_id: str, actor: str | None = None) -> dict[str, Any]:
        """One view, or a :class:`TriageError` if it does not resolve for ``actor``.

        Raises rather than returning ``None`` so a route can turn it into a 404
        without repeating the lookup, and so the not-found and not-permitted
        cases give the same answer - which is the point.
        """
        record = self._live_record(view_id)
        if record is None or not self.can_read(record.get("data") or {}, actor):
            raise TriageError(f"view {view_id} not found")
        return self.describe(record)

    def create_view(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """**Add view.** Optionally starting from one of the five defaults.

        ``base`` (or the researched ``from``) names a default view. Its filters,
        columns and sort are copied and ``$me`` is resolved to ``actor``, so the
        sentinel a published definition carries is never written to storage.
        """
        base_id = as_text(payload.get("base")) or as_text(payload.get("from"))
        base = vocab.default_view(base_id) if base_id else None
        if base_id and base is None:
            raise TriageError(
                f"{base_id!r} is not a default view. Default views: "
                f"{', '.join(vocab.DEFAULT_VIEWS_BY_ID)}"
            )

        owner = as_text(payload.get("owner")) or as_text(actor)
        visibility = self._visibility(payload)
        if visibility == vocab.PRIVATE and not owner:
            raise TriageError("a private view needs an owner; pass actor= or owner=")

        name = as_text(payload.get("name")) or (base["label"] if base else "")
        if not name:
            raise TriageError("name is required when the view is not created from a default")

        if base is not None:
            summary = vocab.default_view_summary(base)
            workspace_filters = summary["workspace_filters"]
            domain_filters = summary["workspace_domain_filters"]
            match = base["match"]
            columns = list(base["columns"])
            sort = dict(base["sort"])
        else:
            workspace_filters, domain_filters, match = engine.normalise_filter_set(payload)
            columns = list(BLANK_COLUMNS)
            sort = {"field": "dock.name", "direction": engine.ASCENDING}

        # A filter set supplied alongside a base wins: "Add view", then edit it
        # before saving is the flow, and a client that sends both means the
        # second.
        supplied_workspace, supplied_domain, supplied_match = engine.normalise_filter_set(payload)
        if supplied_workspace is not None:
            workspace_filters = supplied_workspace
        if supplied_domain is not None:
            domain_filters = supplied_domain
        if supplied_match is not None:
            match = supplied_match

        if "columns" in payload and payload.get("columns") is not None:
            columns = [as_text(key) for key in payload["columns"]]
        if payload.get("sort") is not None:
            sort = engine.validate_sort(payload["sort"])

        cleaned, dropped = engine.dedupe_columns([key for key in columns if key])
        if not cleaned:
            raise TriageError("a view needs at least one column")

        data: dict[str, Any] = {
            "name": name,
            "owner": owner,
            "visibility": visibility,
            "base": base_id or None,
            "match": as_text(match, vocab.MATCH_ALL).lower(),
            "workspace_filters": workspace_filters or {"join": "and", "conditions": []},
            "workspace_domain_filters": domain_filters or {"join": "and", "conditions": []},
            "columns": cleaned,
            "sort": sort,
        }
        if dropped:
            data["dropped_duplicate_columns"] = dropped

        # ``$me`` is resolved against the view's owner and written through, so
        # the sentinel a published default carries is never stored. The
        # conditions are stored as written - including any this build cannot
        # read - so the problem keeps being reported instead of being tidied
        # away at save time.
        data["workspace_filters"] = engine.substitute_me_in_raw(data["workspace_filters"], owner)
        data["workspace_domain_filters"] = engine.substitute_me_in_raw(
            data["workspace_domain_filters"], owner
        )

        record = self.store.create(VIEW_COLLECTION, data, actor=actor, source=source)
        return self.describe(record)

    def update_view(
        self, view_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Edit a view: its name, columns, filters, sort, or visibility.

        ``columns`` is replaced wholesale, because the column list is ordered and
        "edit and rearrange columns" is a statement about the whole list rather
        than an insertion at a position.
        """
        record = self._require_writable(view_id, actor)
        data = record.get("data") or {}
        patch: dict[str, Any] = {}

        if "name" in payload:
            name = as_text(payload["name"])
            if not name:
                raise TriageError("name cannot be empty")
            patch["name"] = name

        if "visibility" in payload:
            visibility = self._visibility(payload)
            patch["visibility"] = visibility
            if visibility == vocab.PRIVATE:
                # A private view is "for yourself", so the actor making it
                # private becomes its owner. Otherwise a teammate could hide a
                # team view by flipping one flag, and the original owner would
                # lose sight of it.
                patch["owner"] = (
                    as_text(payload.get("owner")) or as_text(actor) or data.get("owner")
                )
            elif "owner" in payload:
                patch["owner"] = as_text(payload["owner"])

        if "columns" in payload:
            requested = payload["columns"]
            if not isinstance(requested, Sequence) or isinstance(requested, (str, bytes)):
                raise TriageError("columns must be a list of column keys")
            cleaned, dropped = engine.dedupe_columns([as_text(key) for key in requested])
            if not cleaned:
                raise TriageError("a view needs at least one column")
            patch["columns"] = cleaned
            if dropped:
                patch["dropped_duplicate_columns"] = dropped
            else:
                patch["dropped_duplicate_columns"] = None

        workspace, domain, match = engine.normalise_filter_set(payload)
        if workspace is not None:
            patch["workspace_filters"] = workspace
        if domain is not None:
            patch["workspace_domain_filters"] = domain
        if match is not None:
            patch["match"] = as_text(match).lower()

        if "sort" in payload:
            patch["sort"] = engine.validate_sort(payload["sort"])

        if not patch:
            return self.describe(record)

        # Resolve ``$me`` against the view's own owner, so a view whose filters
        # arrive by a generic record write still means "mine".
        owner = as_text(patch.get("owner")) or as_text(data.get("owner"))
        existing_workspace = patch.get("workspace_filters", data.get("workspace_filters"))
        existing_domain = patch.get(
            "workspace_domain_filters", data.get("workspace_domain_filters")
        )
        patch["workspace_filters"] = engine.substitute_me_in_raw(existing_workspace, owner)
        patch["workspace_domain_filters"] = engine.substitute_me_in_raw(existing_domain, owner)
        patch["match"] = as_text(patch.get("match", data.get("match")), vocab.MATCH_ALL).lower()

        return self.describe(self.store.update(view_id, patch, actor=actor, source=source))

    def delete_view(self, view_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Soft-delete a view. The definition and its history stay auditable."""
        self._require_writable(view_id, actor)
        return self.store.delete(view_id, actor=actor, source=source)

    def clone_view(
        self,
        view_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """**Clone** a view into a private copy of your own.

        A clone is always private and always owned by the actor - "make your own
        customized copy" plus "private views for yourself". Any field the caller
        supplies overrides the copy, so "clone, then change the columns" is one
        request and not two.
        """
        payload = payload or {}
        origin = self.get_view(view_id, actor)
        owner = as_text(payload.get("owner")) or as_text(actor)
        if not owner:
            raise TriageError("cloning needs an actor: the copy belongs to whoever made it")

        name = as_text(payload.get("name")) or f"{origin['name']} (copy)"
        if "columns" in payload and payload.get("columns") is not None:
            columns, _dropped = engine.dedupe_columns(
                [as_text(key) for key in payload["columns"] if as_text(key)]
            )
        else:
            columns = list(origin["columns"])
        if not columns:
            raise TriageError("a view needs at least one column")

        workspace, domain, match = engine.normalise_filter_set(payload)
        data: dict[str, Any] = {
            "name": name,
            "owner": owner,
            # Always private: a custom copy is one of the "private views for
            # yourself", even when the thing copied was a public team view.
            "visibility": vocab.PRIVATE,
            "base": origin.get("base"),
            "cloned_from": view_id,
            "match": as_text(match) if match is not None else origin["match"],
            "workspace_filters": workspace
            if workspace is not None
            else origin["workspace_filters"],
            "workspace_domain_filters": (
                domain if domain is not None else origin["workspace_domain_filters"]
            ),
            "columns": columns,
            "sort": engine.validate_sort(payload["sort"])
            if payload.get("sort")
            else origin["sort"],
        }
        data["workspace_filters"] = engine.substitute_me_in_raw(data["workspace_filters"], owner)
        data["workspace_domain_filters"] = engine.substitute_me_in_raw(
            data["workspace_domain_filters"], owner
        )

        return self.describe(self.store.create(VIEW_COLLECTION, data, actor=actor, source=source))

    # -- the dashboard ------------------------------------------------------ #

    def dashboard(self, actor: str | None = None) -> dict[str, Any]:
        """Step one of the flow: what this user sees when they open the dashboard.

        The remembered open views first, then the defaults they can add from, then
        the views they already own. A view deleted since they last looked is
        reported rather than silently dropped, because "we remembered your views"
        that quietly forgets one is worse than saying so.
        """
        remembered = self.open_views(actor)
        return {
            "actor": actor,
            "open_views": remembered["view_ids"],
            "active_view_id": remembered["active_view_id"],
            "missing_view_ids": remembered["missing"],
            "views": remembered["views"],
            "default_views": self.defaults(),
            "your_views": self.summarise_all(self.list_views(actor)),
            "remembered": remembered["remembered"],
        }

    def table(
        self,
        view_id: str,
        *,
        actor: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        reference: Any = None,
    ) -> dict[str, Any]:
        """The joined, filtered, sorted slice a view describes.

        ``columns`` is the view's ordered column list, so the table's shape is
        the view's shape and a client renders it without a second lookup.
        ``problems`` carries any filter the engine could not read: a triage table
        that hid rows for a typo it would not name is the failure this workflow
        cannot ship.
        """
        view = self.get_view(view_id, actor)
        index = JoinIndex(self.store)
        plan = engine.compile_filters(
            view["workspace_filters"], view["workspace_domain_filters"], view["match"]
        )
        context = engine.FilterContext(actor=actor, reference=reference)
        problems = list(plan.problems) + engine.unresolved_me_problems(plan)

        matched = [row for row in index.rows() if plan.matches(row.values, context)]
        ordered = engine.sort_rows(
            matched,
            view["sort"],
            value_of=lambda row: row.values.get(view["sort"]["field"]),
            key_of=lambda row: row.id,
        )
        window = ordered[max(0, offset) : max(0, offset) + max(1, min(limit, MAX_LIMIT))]
        return {
            "view": view,
            "columns": list(view["columns"]),
            "column_meta": [self.describe_column(key) for key in view["columns"]],
            "rows": [row.table_row(view["columns"]) for row in window],
            "count": len(window),
            "total": len(ordered),
            "offset": max(0, offset),
            "limit": max(1, min(limit, MAX_LIMIT)),
            "match": view["match"],
            "sort": view["sort"],
            "workspace_filters": view["workspace_filters"],
            "workspace_domain_filters": view["workspace_domain_filters"],
            "problems": problems,
        }

    def row_for_room(self, room_id: str, properties: Sequence[str] | None = None) -> dict[str, Any]:
        """One workspace's joined row, honouring the researched ``properties``.

        The three envelope fields are always present, so a caller that narrowed
        the response can still act on the row. ``unknown`` lists any property
        this build has no value for, which is how a team discovers that a CRM
        field they asked for has not started syncing.
        """
        index = JoinIndex(self.store)
        workspace = next(
            (record for record in index.workspaces if str(record.get("id")) == room_id), None
        )
        if workspace is None:
            raise TriageError(f"room {room_id} not found")
        row = index.row_for(workspace)
        selected, unknown = row.selected(properties)
        return {
            "row": selected,
            "requested": None if properties is None else list(properties),
            "unknown": unknown,
            "available": list(vocab.ALL_COLUMN_KEYS),
            "meta": dict(row.meta),
        }

    # -- the remembered open set --------------------------------------------- #

    def open_views(self, actor: str | None = None) -> dict[str, Any]:
        """The views this account last had open, resolved.

        An account that has never opened the dashboard gets an empty set and
        ``remembered: false``, which is a real answer rather than a missing one -
        the client then falls through to the defaults.
        """
        actor = as_text(actor)
        if not actor:
            return {
                "actor": None,
                "remembered": False,
                "view_ids": [],
                "active_view_id": None,
                "views": [],
                "missing": [],
            }
        record = self._state_record(actor)
        if record is None:
            return {
                "actor": actor,
                "remembered": False,
                "view_ids": [],
                "active_view_id": None,
                "views": [],
                "missing": [],
            }
        data = record.get("data") or {}
        stored = [as_text(value) for value in (data.get("view_ids") or []) if as_text(value)]
        resolved: list[dict[str, Any]] = []
        missing: list[str] = []
        for view_id in stored:
            found = self._live_record(view_id)
            if found is None or not self.can_read(found.get("data") or {}, actor):
                missing.append(view_id)
                continue
            resolved.append(self.describe(found))
        return {
            "actor": actor,
            "remembered": True,
            "view_ids": stored,
            "active_view_id": as_text(data.get("active_view_id")) or None,
            "views": resolved,
            "missing": missing,
        }

    def remember_open_views(self, payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        """Record the views an account has open. Replaces the set outright.

        Replaces rather than merges, because "the views you had open" is a
        snapshot of the dashboard as it is now, and merging would leave a view
        the user has closed sitting in the remembered set for ever.

        Every id is checked against what the actor may read, so the remembered
        set cannot become a side channel for discovering a private view's id.
        """
        actor = as_text(payload.get("actor"))
        if not actor:
            raise TriageError("actor is required: the open set is remembered per user account")

        requested = payload.get("view_ids")
        if requested is None:
            requested = []
        if not isinstance(requested, Sequence) or isinstance(requested, (str, bytes)):
            raise TriageError("view_ids must be a list of view ids")

        view_ids: list[str] = []
        for raw in requested:
            view_id = as_text(raw)
            if not view_id or view_id in view_ids:
                continue
            record = self._live_record(view_id)
            if record is None or not self.can_read(record.get("data") or {}, actor):
                raise TriageError(f"view {view_id} not found")
            view_ids.append(view_id)

        active = as_text(payload.get("active_view_id")) or None
        if active is not None and active not in view_ids:
            raise TriageError(
                f"active_view_id {active} is not in view_ids, so the dashboard would have "
                "no way to show it"
            )

        data = {
            "actor": actor,
            "view_ids": view_ids,
            "active_view_id": active,
            "remembered_at": utcnow_iso(),
        }
        existing = self._state_record(actor)
        if existing is None:
            self.store.create(DASHBOARD_STATE_COLLECTION, data, actor=actor, source=source)
        else:
            self.store.update(existing["id"], data, actor=actor, source=source)
        return self.open_views(actor)

    # -- workspace type and the template it inherits from -------------------- #

    def room_type(self, room_id: str) -> dict[str, Any]:
        """A workspace's effective type, and where that type came from.

        ``source`` is the interesting field: it says whether the value was typed
        on the workspace or inherited from its template, which is the difference
        between "a rep chose Sales" and "nobody chose anything yet".
        """
        workspace = self.store.get(room_id)
        if (
            workspace is None
            or workspace.get("collection") != "room"
            or workspace.get("deleted_at")
        ):
            raise TriageError(f"room {room_id} not found")
        index = JoinIndex(self.store)
        value, source, template_id = index.effective_type(workspace.get("data") or {})
        data = workspace.get("data") or {}
        return {
            "room_id": room_id,
            "type": value,
            "source": source,
            "own_type": first_text(data, ("type", "workspace_type", "workspaceType")) or None,
            "template_id": template_id,
        }

    def set_room_type(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Write a workspace's own type, or clear it to inherit again.

        This is the researched write: "``PATCH /v1/workspaces/{id}`` can write
        custom field values". Sending ``{"type": null}`` clears the override, so a
        workspace returns to whatever its template says - the way back out of a
        per-workspace decision is part of being able to make one.
        """
        workspace = self.store.get(room_id)
        if (
            workspace is None
            or workspace.get("collection") != "room"
            or workspace.get("deleted_at")
        ):
            raise TriageError(f"room {room_id} not found")
        if "type" not in payload:
            raise TriageError('type is required; send {"type": null} to clear the override')
        raw = payload.get("type")
        value = as_text(raw) or None
        self.store.update(room_id, {"type": value}, actor=actor, source=source)
        return self.room_type(room_id)

    def list_templates(self) -> list[dict[str, Any]]:
        """Templates and the type each one imposes on the workspaces made from it.

        ``inheriting`` is computed from the workspaces that point at the template
        and have no type of their own, which is the population the "Any future
        workspaces created from that template will be automatically categorized"
        quote is about.
        """
        index = JoinIndex(self.store)
        counts: dict[str, int] = {}
        for workspace in index.workspaces:
            data = workspace.get("data") or {}
            if first_text(data, ("type", "workspace_type", "workspaceType")):
                continue
            template_id = first_text(
                data, ("template_id", "template", "workspace_template", "template_ref")
            )
            if template_id:
                counts[template_id] = counts.get(template_id, 0) + 1
        listed = []
        for record in self.store.list(TEMPLATE_COLLECTION, limit=LIST_LIMIT, order_by="created_at"):
            data = record.get("data") or {}
            listed.append(
                {
                    "id": record["id"],
                    "name": first_text(data, ("name", "title", "label")) or record["id"],
                    "type": first_text(data, ("type", "workspace_type", "workspaceType")) or None,
                    "inheriting": counts.get(record["id"], 0),
                    "data": dict(data),
                }
            )
        return listed

    def set_template_type(
        self, template_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Set the type a template imposes. This is template *Settings*.

        A workspace that already carries its own type keeps it: the inheritance is
        a default, not a re-categorisation. The template must exist, because a
        type written onto a template nobody can see is a write with no effect and
        no way to find it again.
        """
        record = self.store.get(template_id)
        if (
            record is None
            or record.get("collection") != TEMPLATE_COLLECTION
            or record.get("deleted_at")
        ):
            raise TriageError(f"template {template_id} not found")
        if "type" not in payload:
            raise TriageError(
                'type is required; send {"type": null} to stop categorising new workspaces'
            )
        self.store.update(
            template_id, {"type": as_text(payload.get("type")) or None}, actor=actor, source=source
        )
        data = record.get("data") or {}
        return {
            "id": template_id,
            "name": first_text(data, ("name", "title", "label")) or template_id,
            "type": as_text(payload.get("type")) or None,
        }

    # -- dynamic sections ----------------------------------------------------- #

    def sections(self, room_id: str) -> dict[str, Any]:
        """Which of a workspace's sections are shown, and why.

        "**Dynamic workspaces:** Show or hide specific workspace sections based on
        what a customer has done in your product", over
        ``PATCH /v1/workspace-pages/{id}`` and
        ``PATCH /v1/workspace-sections/{id}``.

        A section with no rule is visible. A section whose rule does not match is
        hidden. A rule that names a section the workspace does not declare, or one
        that cannot be compiled, is reported in ``problems`` and the section is
        left visible - the same fail-open policy the filter engine uses, and for
        the same reason: a broken rule must not take the workspace's pages with
        it.
        """
        workspace = self.store.get(room_id)
        if (
            workspace is None
            or workspace.get("collection") != "room"
            or workspace.get("deleted_at")
        ):
            raise TriageError(f"room {room_id} not found")

        index = JoinIndex(self.store)
        row = index.row_for(workspace)
        rules = self._section_rules(room_id)
        declared = declared_sections(workspace)
        declared_keys = {section["key"] for section in declared}

        problems: list[dict[str, Any]] = []
        for rule in rules.values():
            unknown = as_text(rule.get("section"))
            if unknown not in declared_keys:
                problems.append(
                    {
                        "kind": "unknown_section",
                        "section": unknown,
                        "detail": (
                            f"{unknown!r} is not a section this workspace declares, "
                            "so its rule does nothing"
                        ),
                    }
                )

        context = engine.FilterContext()
        listed: list[dict[str, Any]] = []
        for section in declared:
            rule = rules.get(section["key"])
            if rule is None:
                listed.append({**section, "visible": True, "reason": REASON_NO_RULE})
                continue
            local: list[dict[str, Any]] = []
            group = engine.compile_group(engine.DOMAIN, rule.get("visible_when"), local)
            problems.extend(local)
            if local:
                # Uncompilable rule: fail open and say so, rather than hiding a
                # section on the strength of a condition nobody could read.
                problems.append(
                    {
                        "kind": "unusable_rule",
                        "section": section["key"],
                        "detail": "this section's rule could not be read; the section is left visible",
                    }
                )
                listed.append(
                    {**section, "visible": True, "reason": REASON_NO_RULE, "problems": local}
                )
                continue
            visible = group.matches(row.values, context)
            listed.append(
                {
                    **section,
                    "visible": visible,
                    "reason": REASON_RULE_MATCHED if visible else REASON_RULE_NOT_MATCHED,
                    "rule": rule.get("visible_when"),
                }
            )
        return {
            "room_id": room_id,
            "sections": listed,
            "hidden": [entry["key"] for entry in listed if not entry["visible"]],
            "rules": len(rules),
            "problems": problems,
        }

    def set_section_rules(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Replace a workspace's section rules outright.

        The ruleset is replaced rather than merged, because the caller is editing
        a list it can see in full; a merge would leave a rule for a section the
        user just removed.
        """
        workspace = self.store.get(room_id)
        if (
            workspace is None
            or workspace.get("collection") != "room"
            or workspace.get("deleted_at")
        ):
            raise TriageError(f"room {room_id} not found")

        requested = payload.get("sections")
        if (
            requested is None
            or not isinstance(requested, Sequence)
            or isinstance(requested, (str, bytes))
        ):
            raise TriageError("sections must be a list of {section, visible_when} objects")

        declared = {section["key"] for section in declared_sections(workspace)}
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, entry in enumerate(requested):
            if not isinstance(entry, Mapping):
                raise TriageError(f"section rule {index} must be an object")
            key = as_text(entry.get("section"))
            if not key:
                raise TriageError(f"section rule {index} has no section key")
            if key not in declared:
                raise TriageError(
                    f"{key!r} is not a section of this workspace. It declares: "
                    f"{', '.join(sorted(declared)) or '(none)'}"
                )
            if key in seen:
                raise TriageError(f"section {key} has two rules; keep one per section")
            seen.add(key)
            if "visible_when" not in entry:
                raise TriageError(f"section rule for {key} needs a visible_when filter group")
            problems: list[dict[str, Any]] = []
            group = engine.compile_group(engine.DOMAIN, entry.get("visible_when"), problems)
            if problems:
                raise TriageError(
                    f"section rule for {key} is not readable: {problems[0]['detail']}"
                )
            prepared.append({"section": key, "visible_when": group.to_dict()})

        for existing in self.store.list(SECTION_RULE_COLLECTION, room_id=room_id, limit=LIST_LIMIT):
            self.store.delete(existing["id"], actor=actor, source=source)
        for rule in prepared:
            self.store.create(
                SECTION_RULE_COLLECTION, rule, room_id=room_id, actor=actor, source=source
            )
        return self.sections(room_id)

    # -- internals ------------------------------------------------------------ #

    def _live_record(self, view_id: str) -> dict[str, Any] | None:
        record = self.store.get(view_id)
        if (
            record is None
            or record.get("collection") != VIEW_COLLECTION
            or record.get("deleted_at")
        ):
            return None
        return record

    def _require_writable(self, view_id: str, actor: str | None) -> dict[str, Any]:
        """A view the actor may change, or a :class:`TriageError` that reads as 404.

        The same rule as reading, and that is an inference rather than a sourced
        fact - see ``public-views-are-team-editable`` in
        :mod:`dsr.triage.inferences`.
        """
        record = self._live_record(view_id)
        if record is None or not self.can_read(record.get("data") or {}, actor):
            raise TriageError(f"view {view_id} not found")
        return record

    def _state_record(self, actor: str) -> dict[str, Any] | None:
        found = self.store.find(DASHBOARD_STATE_COLLECTION, {"actor": actor}, limit=1)
        return found[0] if found else None

    def _section_rules(self, room_id: str) -> dict[str, dict[str, Any]]:
        rules: dict[str, dict[str, Any]] = {}
        for record in self.store.list(SECTION_RULE_COLLECTION, room_id=room_id, limit=LIST_LIMIT):
            key = as_text((record.get("data") or {}).get("section"))
            if key:
                rules[key] = record.get("data") or {}
        return rules

    def _visibility(self, payload: Mapping[str, Any]) -> str:
        value = as_text(payload.get("visibility"), vocab.PRIVATE).lower()
        if value not in vocab.VISIBILITIES:
            raise TriageError(
                f"visibility must be one of {list(vocab.VISIBILITIES)}, got {payload.get('visibility')!r}"
            )
        return value

    @staticmethod
    def can_read(data: Mapping[str, Any], actor: str | None) -> bool:
        """May ``actor`` see this view?

        A public view is for "your entire team", so anyone reading the board may
        read it. A private view is "for yourself", so only its owner may - and
        with no actor at all, nobody, including the owner recorded as ``None``.
        """
        if as_text(data.get("visibility"), vocab.PRIVATE) == vocab.PUBLIC:
            return True
        owner = as_text(data.get("owner"))
        return bool(owner) and owner == as_text(actor)

    def describe_column(self, key: str) -> dict[str, Any]:
        """Catalog metadata for a column, tolerating one this build has not seen.

        An unknown column is not an error: "the column/filter set is user-defined
        so new CRM fields flow through automatically", so a view may name a field
        before the field starts syncing. It is carried, flagged, and shown empty.
        """
        known = vocab.column(key)
        if known is not None:
            return {**known, "known": True, "unknown_columns": []}
        return {
            "key": key,
            "label": key,
            "group": vocab.group_of(key),
            "type": None,
            "sourced": False,
            "known": False,
            "unknown_columns": [key],
            "note": "This build publishes no field by this name. It is carried so a view can "
            "name a CRM field before it starts syncing; its value will be null until it does.",
        }

    def summarise(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A list line: the whole definition minus the filter bodies' verbosity.

        The filters are included because a view list where you cannot see what a
        view actually selects is a list you have to open eleven views to learn
        from.
        """
        data = record.get("data") or {}
        return {
            "id": record["id"],
            "name": as_text(data.get("name")) or record["id"],
            "owner": as_text(data.get("owner")) or None,
            "visibility": as_text(data.get("visibility"), vocab.PRIVATE),
            "base": data.get("base"),
            "cloned_from": data.get("cloned_from"),
            "match": as_text(data.get("match"), vocab.MATCH_ALL),
            "columns": list(data.get("columns") or []),
            "sort": dict(data.get("sort") or {}),
            "workspace_filters": data.get("workspace_filters") or {"join": "and", "conditions": []},
            "workspace_domain_filters": data.get("workspace_domain_filters")
            or {"join": "and", "conditions": []},
            "column_count": len(data.get("columns") or []),
            "dropped_duplicate_columns": data.get("dropped_duplicate_columns"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def describe(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """One view in full, with the problems its own filters would report."""
        view = self.summarise(record)
        plan = engine.compile_filters(
            view["workspace_filters"], view["workspace_domain_filters"], view["match"]
        )
        view["problems"] = list(plan.problems) + engine.unresolved_me_problems(plan)
        view["column_meta"] = [self.describe_column(key) for key in view["columns"]]
        return view

    @staticmethod
    def summarise_all(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return views
