"""The join: Dock workspaces + engagement metrics + CRM objects + order forms.

The researched data flow, transcribed::

    Dock workspace metadata + engagement metrics + CRM-synced opportunity/deal
    fields -> joined rows in a configurable table -> sorted/filtered slice used
    for pipeline triage

Everything is joined **on read**. No metric is written as a row and no CRM value
is copied onto the workspace, which is what the research means by "CRM sync
keeps the joined columns current without user action": there is nothing to
refresh, because nothing was cached. It is also what the product's
schema-flexibility rule requires - a team that starts syncing
``hubspot.deal_amount`` tomorrow gets a populated column with no migration and no
change to this file.

Provider gating
---------------
A ``salesforce.*`` column is ``null`` for a workspace linked to HubSpot, and a
``hubspot.*`` column is ``null`` for a workspace linked to Salesforce. That is the
correct answer rather than a convenience: a Salesforce Opportunity Stage shown
against a HubSpot deal is a wrong number on a pipeline a rep is about to act on.

Type inheritance
----------------
"Workspace ``type`` is inherited from the template so 'Any future workspaces
created from that template will be automatically categorized'." The effective
type is resolved on read, workspace override first, then the template. The
inference that records the reading - and the alternative - is
``type-inheritance-is-live-not-copied`` in :mod:`dsr.triage.inferences`.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Iterable, Mapping

from dsr.store import RecordStore
from dsr.triage import vocabulary as vocab
from dsr.triage.fields import (
    ACTIVITY_ACTION_PATHS,
    CRM_DEAL_ID_PATHS,
    CRM_FIELDS,
    CRM_OPPORTUNITY_ID_PATHS,
    CRM_PROVIDER_PATHS,
    DOCK_ACCOUNT_PATHS,
    DOCK_NAME_PATHS,
    DOCK_OWNER_PATHS,
    DOCK_STAGE_PATHS,
    DOCK_TEAM_PATHS,
    DOCK_TEMPLATE_PATHS,
    ORDER_FORM_DEAL_TYPE_PATHS,
    ORDER_FORM_STATUS_PATHS,
    TIME_KEYS,
    as_text,
    dig,
    first_number,
    first_text,
    first_time,
    is_view_action,
    iso,
    normalise_provider,
    to_list,
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Named here, and not repeated as string literals. None of these is a table and
# none of this is a schema: a CRM link is an ordinary JSON payload in ``data``,
# so a team can add a CRM field with no migration.

ROOM_COLLECTION = "room"
ACTIVITY_COLLECTION = "activity"
TEMPLATE_COLLECTION = "workspace_template"
CRM_LINK_COLLECTION = "workspace_crm"
ORDER_FORM_COLLECTION = "order_form"

#: ``AuditedDatabase.list`` pages with LIMIT/OFFSET, so scanning in pages of this
#: size keeps a join correct well past the per-call cap. Same constant, same
#: reason, as :mod:`dsr.analytics`.
_PAGE = 1000

#: The ``object`` a joined row reports, matching the shape the researched API
#: uses for a resource envelope.
ROW_OBJECT = "workspace"


def _all(store: RecordStore, collection: str) -> list[dict[str, Any]]:
    """Every live record in a collection, paged so the cap is not the ceiling."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(collection, limit=_PAGE, offset=offset, order_by="id", descending=False)
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


# --------------------------------------------------------------------------- #
# Engagement
# --------------------------------------------------------------------------- #


@dataclass
class Engagement:
    """The three researched engagement columns, computed from activity records.

    ``views`` counts the activity records whose action is a view kind,
    ``actions`` counts every activity record, and ``last_client_view`` is when a
    buyer last viewed anything. A workspace with no activity at all has zeros and
    a ``None`` timestamp, which is a real and different state from "viewed once,
    long ago" - and one a triage table has to be able to show.
    """

    views: int = 0
    actions: int = 0
    last_client_view: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "views": self.views,
            "actions": self.actions,
            "last_client_view": self.last_client_view,
        }


EMPTY_ENGAGEMENT = Engagement()


def aggregate_engagement(records: Iterable[Mapping[str, Any]]) -> Engagement:
    """Summarise a workspace's activity records.

    An activity with an unparseable timestamp still counts towards ``actions``
    and towards ``views`` if its action is a view kind - it is a real event - but
    it cannot contribute to ``last_client_view``, because a time nobody can read
    is not a time.
    """
    views = 0
    actions = 0
    last: str | None = None
    for record in records:
        actions += 1
        data = record.get("data") or {}
        action = first_text(data, ACTIVITY_ACTION_PATHS)
        if not is_view_action(action):
            continue
        views += 1
        moment = iso(first_time(data, TIME_KEYS))
        if moment is not None and (last is None or moment > last):
            last = moment
    return Engagement(views=views, actions=actions, last_client_view=last)


# --------------------------------------------------------------------------- #
# The joined row
# --------------------------------------------------------------------------- #


@dataclass
class WorkspaceRow:
    """One workspace, with every researched column resolved against it."""

    id: str
    values: dict[str, Any] = dataclass_field(default_factory=dict)
    meta: dict[str, Any] = dataclass_field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"/api/wf-022/rooms/{self.id}/row"

    def envelope(self) -> dict[str, Any]:
        """The three fields the researched API returns when ``properties`` is
        omitted: "only the resource's `id`, `object`, and `url`"."""
        return {"id": self.id, "object": ROW_OBJECT, "url": self.url}

    def selected(self, properties: Iterable[str] | None) -> tuple[dict[str, Any], list[str]]:
        """The envelope plus the requested properties, and what was not found.

        The envelope is always present even when properties are requested,
        because a caller that selected fields still has to be able to act on the
        row it got back. ``unknown`` is returned rather than raised: the
        extensibility claim in the research is that new CRM fields flow through,
        so a property this build has never heard of has to be a reportable miss
        and not a 400.
        """
        base = self.envelope()
        if properties is None:
            return base, []
        unknown: list[str] = []
        out = dict(base)
        for key in properties:
            text = as_text(key)
            if not text:
                continue
            if text in ("id", "object", "url"):
                continue
            if text in self.values:
                out[text] = self.values[text]
            else:
                unknown.append(text)
        return out, unknown

    def table_row(self, columns: Iterable[str]) -> dict[str, Any]:
        """One line of the triage table: the view's columns, in the view's order.

        A column the view asks for but the row cannot supply is present with a
        ``null`` value rather than absent, so the table's columns line up and a
        rep is not left guessing whether a blank cell means "no" or "not shown".
        """
        out = self.envelope()
        for key in columns:
            out[key] = self.values.get(key)
        out["meta"] = dict(self.meta)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {**self.envelope(), **self.values, "meta": dict(self.meta)}


# --------------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------------- #


class JoinIndex:
    """Every joined source, loaded once and grouped in memory.

    A view listing loads this once and then filters and sorts in Python. That is
    the right trade here: the join spans four collections with no common
    ``room_id`` on two of them, and the alternative - a query per workspace - is
    N+1 over the one request a triage table is made of.
    """

    def __init__(self, store: RecordStore) -> None:
        self.workspaces: list[dict[str, Any]] = _all(store, ROOM_COLLECTION)
        self.templates: dict[str, dict[str, Any]] = {
            record["id"]: record for record in _all(store, TEMPLATE_COLLECTION)
        }
        self.crm_links: dict[str, list[dict[str, Any]]] = _group_by_room(
            _all(store, CRM_LINK_COLLECTION)
        )
        self.order_forms: dict[str, list[dict[str, Any]]] = _group_by_room(
            _all(store, ORDER_FORM_COLLECTION)
        )
        self.engagement: dict[str, Engagement] = _engagement_by_room(
            _all(store, ACTIVITY_COLLECTION)
        )

    def crm_link(self, room_id: str) -> dict[str, Any] | None:
        links = self.crm_links.get(room_id) or []
        return links[0] if links else None

    def order_form(self, room_id: str) -> dict[str, Any] | None:
        forms = self.order_forms.get(room_id) or []
        return forms[0] if forms else None

    def rows(self) -> list[WorkspaceRow]:
        """Every workspace, joined. No filter, no order - that is the view's job."""
        return [self.row_for(workspace) for workspace in self.workspaces]

    def row_for(self, workspace: Mapping[str, Any]) -> WorkspaceRow:
        room_id = str(workspace.get("id"))
        data = workspace.get("data") or {}
        engagement = self.engagement.get(room_id, EMPTY_ENGAGEMENT)
        crm = self.crm_link(room_id)
        order_form = self.order_form(room_id)

        type_value, type_source, template_id = self.effective_type(data)
        values: dict[str, Any] = {
            "dock.name": first_text(data, DOCK_NAME_PATHS) or None,
            "dock.account": first_text(data, DOCK_ACCOUNT_PATHS) or None,
            "dock.owner": first_text(data, DOCK_OWNER_PATHS) or None,
            "dock.team": first_text(data, DOCK_TEAM_PATHS) or None,
            "dock.stage": first_text(data, DOCK_STAGE_PATHS) or None,
            "dock.type": type_value,
            "dock.created_at": iso(workspace.get("created_at")),
            "engagement.views": engagement.views,
            "engagement.actions": engagement.actions,
            "engagement.last_client_view": engagement.last_client_view,
        }

        provider = normalise_provider(first_text((crm or {}).get("data") or {}, CRM_PROVIDER_PATHS))
        values.update(self._crm_values(crm, provider))
        values.update(self._order_form_values(order_form))

        opportunity_id = first_text((crm or {}).get("data") or {}, CRM_OPPORTUNITY_ID_PATHS) or None
        deal_id = first_text((crm or {}).get("data") or {}, CRM_DEAL_ID_PATHS) or None

        return WorkspaceRow(
            id=room_id,
            values=values,
            meta={
                "type_source": type_source,
                "template_id": template_id,
                "crm_provider": provider or None,
                "crm_linked": bool(provider),
                "opportunity_id": opportunity_id,
                "deal_id": deal_id,
                "has_order_form": order_form is not None,
                "order_form_id": order_form.get("id") if order_form else None,
                "engagement": engagement.to_dict(),
            },
        )

    def effective_type(self, data: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
        """``(type, where it came from, template id)`` for one workspace.

        The workspace's own ``type`` wins. Only when it has none does the
        template's apply, which is what makes the inheritance a default rather
        than a re-categorisation: a workspace somebody typed by hand is never
        overwritten by a later change to the template.
        """
        own = first_text(data, ("type", "workspace_type", "workspaceType"))
        if own:
            return own, "workspace", None
        template_id = first_text(data, DOCK_TEMPLATE_PATHS) or None
        if template_id:
            template = self.templates.get(template_id)
            if template is not None:
                inherited = first_text(
                    template.get("data") or {}, ("type", "workspace_type", "workspaceType")
                )
                if inherited:
                    return inherited, "template", template_id
        return None, None, template_id

    def _crm_values(self, crm: Mapping[str, Any] | None, provider: str) -> dict[str, Any]:
        """The four Salesforce and four HubSpot columns, gated on the provider.

        Every key in the catalog is produced, present-or-not, so a row's shape
        does not change with the data: a Salesforce-linked workspace has
        ``hubspot.deal_stage: null`` rather than no such key at all.
        """
        out: dict[str, Any] = {}
        data = (crm or {}).get("data") or {}
        for key in vocab.ALL_COLUMN_KEYS:
            group, _, name = key.partition(".")
            if group not in (vocab.SALESFORCE_GROUP, vocab.HUBSPOT_GROUP):
                continue
            paths = CRM_FIELDS.get(group, {}).get(name)
            column = vocab.column(key)
            if group != provider or not paths or column is None:
                out[key] = None
                continue
            out[key] = _typed_value(column["type"], data, paths)
        return out

    def _order_form_values(self, order_form: Mapping[str, Any] | None) -> dict[str, Any]:
        data = (order_form or {}).get("data") or {}
        return {
            "order_form.status": first_text(data, ORDER_FORM_STATUS_PATHS) or None,
            "order_form.deal_type": first_text(data, ORDER_FORM_DEAL_TYPE_PATHS) or None,
        }


def _typed_value(kind: str, data: Mapping[str, Any], paths: Iterable[str]) -> Any:
    if kind == vocab.DATE:
        return iso(first_time(data, paths))
    if kind == vocab.NUMBER:
        return _money(first_number(data, paths))
    return first_text(data, paths) or None


def _money(value: float | None) -> Any:
    """An integral amount stays an ``int``, so 120000 does not render as 120000.0."""
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


def _group_by_room(records: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        room_id = record.get("room_id")
        if not room_id:
            continue
        grouped.setdefault(str(room_id), []).append(dict(record))
    return grouped


def _engagement_by_room(records: Iterable[Mapping[str, Any]]) -> dict[str, Engagement]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        room_id = record.get("room_id")
        if not room_id:
            continue
        grouped.setdefault(str(room_id), []).append(record)
    return {room_id: aggregate_engagement(rows) for room_id, rows in grouped.items()}


# --------------------------------------------------------------------------- #
# Workspace sections
# --------------------------------------------------------------------------- #

#: Where a workspace may declare the sections its pages are made of. A section is
#: an arbitrary string key: the research names the *pattern* ("Show or hide
#: specific workspace sections based on what a customer has done in your
#: product") and not a fixed set of section names, so there is no enum here.
SECTION_PATHS = ("sections", "workspace_sections", "pages")
SECTION_KEY_PATHS = ("key", "id", "name", "slug")
SECTION_LABEL_PATHS = ("label", "title", "name")


def declared_sections(workspace: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The sections a workspace declares, in its own order.

    A workspace that declares none has no sections to show or hide, and the
    endpoint says so rather than inventing a list.
    """
    if workspace is None:
        return []
    data = workspace.get("data") or {}
    raw: Any = None
    for path in SECTION_PATHS:
        found = dig(data, path)
        if found:
            raw = found
            break
    sections: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in to_list(raw):
        if isinstance(entry, str):
            key, label = entry.strip(), entry.strip()
        elif isinstance(entry, Mapping):
            key = first_text(entry, SECTION_KEY_PATHS)
            label = first_text(entry, SECTION_LABEL_PATHS) or key
        else:
            key = as_text(entry)
            label = key
        if not key or key in seen:
            continue
        seen.add(key)
        sections.append({"key": key, "label": label})
    return sections
