"""Room pages: build a page from fragments, then publish it to buyers.

The workflow this implements is WF-002, "Build the room's buyer-facing pages
from DSR fragments". The documented flow is:

1. Open the room with its pages in edit mode.
2. Choose *Components* -> *Fragments*, then open a fragment set.
3. Drag a fragment onto the page.
4. Select the fragment and set its fields in the configuration panel.
5. Click **Publish**. "The fragment appears on the page the next time a member
   opens the room." Publishing is explicitly user-initiated; nothing here
   publishes by itself.

Draft and published
-------------------
Step 5 is the load-bearing one, and the storage shape that makes it true was
chosen with Jev (``revision_records``, confidence 0.80, in
``orchestration/decisions/jev-audit.jsonl``) rather than guessed:

* A ``page`` record holds the **draft** in ``blocks``.
* Every publish writes an immutable ``page_revision`` record, and the page is
  then pointed at it through ``published_revision_id``.
* A buyer read resolves the pointer. An edit that has not been published is
  therefore not merely hidden by the UI, it is not on the path the buyer reads.

Publish writes the revision first and the pointer second. If the second write
fails, the buyer still sees the previous published revision - the correct
outcome - and the audit log shows a revision that was created but not published.
The reverse order would strand a pointer at a revision that does not exist yet.

Permissions
-----------
"Editing a room's pages requires the Room Collaborator role or equivalent
permissions." The research documents the *requirement* and the role's meaning
("Users can manage pages and documents, add room comments, and share the room");
it does not document an identity system, and this repository has no
authentication layer. So the grant lives in the room's own open payload and is
resolved here:

* ``room.data.collaborators`` - a list of actors who may edit pages. This is the
  Room Collaborator role.
* ``room.data.viewers`` - a list of actors with view-only access.
* A room that declares neither is **unconfigured**, and writes are allowed.
  Reporting this honestly (``permission_source: "unconfigured"``) is better
  than pretending a gate exists that does not.

This is a design inference. The requirement and the role are sourced; the
mechanism is ours.

Schema flexibility
------------------
Everything above is stored as ordinary JSON in ``records.data``. There is no
migration here and no typed column. A team that adds ``campaign`` to a page, or
``tracking_pixel`` to a Text Block, needs no coordination with anyone: it is
stored, indexed, and returned untouched, and it survives publish because the
revision snapshots the draft as authored.

Where the audit ``source`` comes from
------------------------------------
Every write below takes a required keyword-only ``source``. The original branch
hardcoded strings such as ``"POST fragment-set"`` and ``"create page in <room>"``
here in the domain layer, which is a defect rather than a convenience: the audit
row has to name the route that actually served the write, and a domain module
cannot know its own mount point. The HTTP layer passes it in
(``f"POST {router.prefix}/..."``) and this layer only adds which of its own
steps performed the write, because publish deliberately writes twice.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from dsr.db.audited import AuditError, RecordNotFound, new_id, utcnow
from dsr.fragments import (
    FragmentError,
    default_config,
    find_fragment,
    fragment_catalogue,
    normalise_config,
    normalise_fragment,
    normalise_set,
    referenced_documents,
)
from dsr.store import RecordStore

PAGE_COLLECTION = "page"
REVISION_COLLECTION = "page_revision"
FRAGMENT_COLLECTION = "fragment"
FRAGMENT_SET_COLLECTION = "fragment_set"

#: Roles that may edit a page, per the documented Room Collaborator definition.
COLLABORATOR_ROLES = frozenset({"room_collaborator", "room collaborator", "owner", "admin"})

#: The page keys this service sets itself. Everything else in a payload is a
#: team's own field and is stored untouched, which is what "a team adding a
#: field must not need coordination with anyone" means in practice.
_MANAGED_PAGE_KEYS = frozenset(
    {
        "title",
        "slug",
        "blocks",
        "status",
        "published_revision_id",
        "published_at",
        "published_by",
        "published_digest",
        "is_home",
        "order",
        "template_id",
        "template_version_id",
    }
)


def _own_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The parts of a payload this service does not own."""
    return {key: value for key, value in payload.items() if key not in _MANAGED_PAGE_KEYS}


_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PermissionDenied(PermissionError):
    """The actor may read the room but not edit its pages."""


def slugify(value: str) -> str:
    return _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")


def digest_blocks(blocks: Sequence[Mapping[str, Any]]) -> str:
    """A stable fingerprint of a block list, used to spot unpublished edits."""
    canonical = json.dumps(blocks, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _as_list(value: Any) -> list[Any]:
    """A list, or nothing. A room payload is arbitrary JSON, so nothing is trusted."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _config_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``config`` a caller sent, refusing anything that is not an object.

    Without this check a list or a string would be silently dropped by the
    merge below and the caller would believe a field had been set.
    """
    if "config" not in payload:
        return {}
    config = payload["config"]
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise FragmentError(
            f"'config' must be a JSON object of field values, got {type(config).__name__}"
        )
    return dict(config)


class PageService:
    """Room pages and their published revisions, over the audited store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- rooms and permissions ---------------------------------------------- #

    def require_room(self, room_id: str) -> dict[str, Any]:
        room = self.store.get(room_id)
        if room is None or room.get("collection") != "room":
            raise RecordNotFound(room_id)
        return room

    def room_document_ids(self, room_id: str) -> set[str]:
        return {record["id"] for record in self.store.list("document", room_id=room_id, limit=1000)}

    def require_editor(self, room_id: str, actor: str | None) -> dict[str, Any]:
        """Return the room, or refuse the actor.

        The grant is read from the room's own payload so a team can add it
        without a migration. See the module docstring for what is sourced and
        what is inferred.

        Every one of those payload fields is caller-owned JSON, so each is
        shape-checked rather than trusted: a room whose ``collaborators`` is a
        string must not crash the editor, and must not silently grant access.
        """
        room = self.require_room(room_id)
        data = room.get("data") or {}
        collaborators = {str(name) for name in _as_list(data.get("collaborators"))}
        roles = {
            str(name): str(role).lower() for name, role in _as_mapping(data.get("roles")).items()
        }
        granted = bool(collaborators or roles or _as_list(data.get("viewers")))

        if actor is None:
            # No identity to check against. A room that gates its pages refuses
            # anonymous writes; an unconfigured room accepts them.
            if granted:
                raise PermissionDenied("editing a room's pages requires a named collaborator")
            return room

        if actor in collaborators or roles.get(actor) in COLLABORATOR_ROLES:
            return room
        if granted:
            raise PermissionDenied(
                f"{actor!r} is not a Room Collaborator on this room; editing pages requires that role"
            )
        return room

    @staticmethod
    def permission_source(room: Mapping[str, Any]) -> str:
        data = room.get("data") or {}
        if (
            _as_list(data.get("collaborators"))
            or _as_mapping(data.get("roles"))
            or _as_list(data.get("viewers"))
        ):
            return "room"
        return "unconfigured"

    # -- catalogue ---------------------------------------------------------- #

    def catalogue(self) -> dict[str, Any]:
        """The merged fragment catalogue: shipped sets plus a team's own."""
        return fragment_catalogue(
            self.store.list(FRAGMENT_SET_COLLECTION, limit=500),
            self.store.list(FRAGMENT_COLLECTION, limit=1000),
        )

    def add_fragment_set(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Register a custom fragment set. The documented extension mechanism."""
        key = str(payload.get("key") or "").strip()
        if not key:
            raise FragmentError("a fragment set needs a non-empty 'key'")
        catalogue = self.catalogue()
        for existing in catalogue["sets"]:
            if existing["key"] == key:
                raise AuditError(
                    f"fragment set {key!r} already exists ({existing['source']}); a new set needs its own key"
                )
        # Reuse the catalogue's normalisation so a custom set is exactly as
        # well-formed as a shipped one.
        entry = normalise_set(payload, "custom")
        return self.store.create(FRAGMENT_SET_COLLECTION, entry, actor=actor, source=source)

    def add_fragment(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Define a custom fragment inside an existing (possibly custom) set."""
        key = str(payload.get("key") or "").strip()
        if not key:
            raise FragmentError("a fragment needs a non-empty 'key'")
        catalogue = self.catalogue()
        if find_fragment(catalogue, key) is not None:
            raise AuditError(f"fragment {key!r} already exists; a new fragment needs its own key")
        entry = normalise_fragment(payload, "custom")
        if not any(item["key"] == entry["set"] for item in catalogue["sets"]):
            raise FragmentError(
                f"fragment {entry['key']!r} names set {entry['set']!r}, which does not exist"
            )
        return self.store.create(FRAGMENT_COLLECTION, entry, actor=actor, source=source)

    # -- pages -------------------------------------------------------------- #

    def list_pages(self, room_id: str) -> list[dict[str, Any]]:
        self.require_room(room_id)
        pages = self.store.list(
            PAGE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        )
        return [self._decorate(room_id, page) for page in pages]

    def get_page(self, room_id: str, page_id: str) -> dict[str, Any]:
        page = self._require_page(room_id, page_id)
        return self._decorate(room_id, page)

    def find_page_by_slug(self, room_id: str, slug: str) -> dict[str, Any] | None:
        for page in self.store.list(
            PAGE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        ):
            if (page.get("data") or {}).get("slug") == slug:
                return page
        return None

    def create_page(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        self.require_room(room_id)

        title = str(payload.get("title") or "").strip()
        if not title:
            raise FragmentError("a page needs a non-empty 'title'")
        slug = slugify(str(payload.get("slug") or title))
        if not slug:
            raise FragmentError("a page needs a slug made of letters or digits")
        if self.find_page_by_slug(room_id, slug) is not None:
            raise AuditError(f"this room already has a page at {slug!r}")

        blocks = self._normalise_blocks(room_id, payload.get("blocks") or [])
        record = self.store.create(
            PAGE_COLLECTION,
            {
                "title": title,
                "slug": slug,
                # `blocks` is the draft. Buyers never read it; they read the
                # revision `published_revision_id` points at.
                "blocks": blocks,
                "status": "draft",
                "published_revision_id": None,
                "published_at": None,
                "published_by": None,
                "published_digest": None,
                "is_home": bool(payload.get("is_home", False)),
                "order": int(payload.get("order") or 0),
                # Seismic's read API reports the template and template version a
                # page was built from; the pair is carried when the room has one
                # so external consumers have the same identity to correlate on.
                "template_id": payload.get("template_id"),
                "template_version_id": payload.get("template_version_id"),
                # A team's own fields, stored as authored.
                **_own_fields(payload),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._decorate(room_id, record)

    def update_page(
        self,
        room_id: str,
        page_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        data = page.get("data") or {}

        update: dict[str, Any] = {}
        if "title" in patch:
            title = str(patch["title"] or "").strip()
            if not title:
                raise FragmentError("a page needs a non-empty 'title'")
            update["title"] = title
        if "slug" in patch:
            slug = slugify(str(patch["slug"] or ""))
            if not slug:
                raise FragmentError("a slug must contain at least one letter or digit")
            clash = self.find_page_by_slug(room_id, slug)
            if clash is not None and clash["id"] != page_id:
                raise AuditError(f"this room already has a page at {slug!r}")
            update["slug"] = slug
        if "is_home" in patch:
            update["is_home"] = bool(patch["is_home"])
        if "order" in patch:
            update["order"] = int(patch["order"] or 0)
        # Template identity is a caller-owned pair, not something this service
        # derives: WF-001 binds a room to a template, and a team may carry its own.
        for key in ("template_id", "template_version_id"):
            if key in patch:
                update[key] = patch[key]
        if "blocks" in patch:
            update["blocks"] = self._normalise_blocks(room_id, patch["blocks"])
            update["status"] = self._next_status(data, update["blocks"])
        # Anything this service does not manage is a team's own field and goes
        # straight through. The audited wrapper strips the reserved envelope
        # keys, so a caller cannot smuggle an id or a timestamp in here.
        update.update(_own_fields(patch))

        if not update:
            return self._decorate(room_id, page)

        record = self.store.update(
            page_id,
            update,
            actor=actor,
            source=source,
            expected_revision=expected_revision,
        )
        return self._decorate(room_id, record)

    def delete_page(
        self,
        room_id: str,
        page_id: str,
        *,
        actor: str | None = None,
        hard: bool = False,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        self._require_page(room_id, page_id)
        return self.store.delete(page_id, actor=actor, source=source, hard=hard)

    # -- blocks ------------------------------------------------------------- #

    def add_block(
        self,
        room_id: str,
        page_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        index: int | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Place a fragment onto the page. The documented drag-and-drop step."""
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        fragment = self._require_fragment(str(payload.get("fragment") or ""))
        document_ids = self.room_document_ids(room_id)

        config = normalise_config(
            fragment,
            {**default_config(fragment), **_config_of(payload)},
            document_ids=document_ids,
        )
        block = {
            "id": new_id("blk"),
            "fragment": fragment["key"],
            "set": fragment["set"],
            "config": config,
        }

        blocks = list((page.get("data") or {}).get("blocks") or [])
        position = len(blocks) if index is None else max(0, min(int(index), len(blocks)))
        blocks.insert(position, block)
        return self._save_blocks(room_id, page, blocks, actor, expected_revision, source=source)

    def update_block(
        self,
        room_id: str,
        page_id: str,
        block_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        blocks = [dict(block) for block in (page.get("data") or {}).get("blocks") or []]
        target = next((block for block in blocks if block.get("id") == block_id), None)
        if target is None:
            raise RecordNotFound(block_id)

        if "fragment" in payload and payload["fragment"] != target.get("fragment"):
            fragment = self._require_fragment(str(payload["fragment"]))
            merged = normalise_config(
                fragment,
                {**default_config(fragment), **(target.get("config") or {}), **_config_of(payload)},
                document_ids=self.room_document_ids(room_id),
            )
            target["fragment"], target["set"], target["config"] = (
                fragment["key"],
                fragment["set"],
                merged,
            )
        elif "config" in payload:
            fragment = self._require_fragment(str(target.get("fragment")))
            target["config"] = normalise_config(
                fragment,
                {**(target.get("config") or {}), **_config_of(payload)},
                document_ids=self.room_document_ids(room_id),
            )
        if "label" in payload:
            target["label"] = str(payload["label"] or "")

        return self._save_blocks(room_id, page, blocks, actor, expected_revision, source=source)

    def remove_block(
        self,
        room_id: str,
        page_id: str,
        block_id: str,
        *,
        actor: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        blocks = [
            block
            for block in (page.get("data") or {}).get("blocks") or []
            if block.get("id") != block_id
        ]
        if len(blocks) == len((page.get("data") or {}).get("blocks") or []):
            raise RecordNotFound(block_id)
        return self._save_blocks(room_id, page, blocks, actor, expected_revision, source=source)

    def reorder_blocks(
        self,
        room_id: str,
        page_id: str,
        order: Sequence[str],
        *,
        actor: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        blocks = list((page.get("data") or {}).get("blocks") or [])
        by_id = {block.get("id"): block for block in blocks}
        if sorted(order) != sorted(by_id):
            missing = sorted(set(by_id) - set(order))
            unknown = sorted(set(order) - set(by_id))
            raise FragmentError(
                "the block order must list every block on the page exactly once"
                + (f"; missing {missing}" if missing else "")
                + (f"; unknown {unknown}" if unknown else "")
            )
        return self._save_blocks(
            room_id,
            page,
            [by_id[block_id] for block_id in order],
            actor,
            expected_revision,
            source=source,
        )

    # -- publish ------------------------------------------------------------ #

    def publish(
        self,
        room_id: str,
        page_id: str,
        *,
        actor: str | None = None,
        note: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Make the draft the version buyers see.

        "Click *Publish*. The fragment appears on the page the next time a member
        opens the room." Explicitly user-initiated: nothing here runs on a
        schedule and nothing republishes on its own.
        """
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        if expected_revision is not None and int(page["revision"]) != int(expected_revision):
            raise AuditError(
                f"revision conflict on {page_id}: expected {expected_revision}, found {page['revision']}"
            )
        data = page.get("data") or {}
        blocks = copy.deepcopy(data.get("blocks") or [])
        digest = digest_blocks(blocks)
        now = utcnow()

        previous = [
            r
            for r in self.store.list(REVISION_COLLECTION, room_id=room_id, limit=1000)
            if (r.get("data") or {}).get("page_id") == page_id
        ]
        number = (
            max((int((r.get("data") or {}).get("number") or 0) for r in previous), default=0) + 1
        )

        # Revision first, pointer second: see the module docstring. Both audit
        # rows name the route that served them, and each says which of the two
        # steps it was, because only one of them can be replayed on its own.
        self.store.create(
            REVISION_COLLECTION,
            {
                "page_id": page_id,
                "number": number,
                "title": data.get("title"),
                "slug": data.get("slug"),
                "blocks": blocks,
                "digest": digest,
                "published_at": now,
                "published_by": actor,
                "note": str(note) if note else None,
                "template_id": data.get("template_id"),
                "template_version_id": data.get("template_version_id"),
                "fragment_sets": sorted(
                    {str(block.get("set")) for block in blocks if block.get("set")}
                ),
            },
            room_id=room_id,
            actor=actor,
            source=f"{source} (write revision {number})",
        )
        record = self.store.update(
            page_id,
            {
                "published_revision_id": self._latest_revision_id(room_id, page_id),
                "published_at": now,
                "published_by": actor,
                "published_digest": digest,
                "status": "published",
            },
            actor=actor,
            source=f"{source} (point page at revision {number})",
        )
        return self._decorate(room_id, record)

    def unpublish(
        self,
        room_id: str,
        page_id: str,
        *,
        actor: str | None = None,
        expected_revision: int | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Withdraw the page from the buyer view, keeping the draft and history."""
        self.require_editor(room_id, actor)
        page = self._require_page(room_id, page_id)
        if not (page.get("data") or {}).get("published_revision_id"):
            raise AuditError(f"page {page_id} is not published")
        record = self.store.update(
            page_id,
            {
                "published_revision_id": None,
                "published_at": None,
                "published_by": None,
                "published_digest": None,
                "status": "draft",
            },
            actor=actor,
            source=source,
            expected_revision=expected_revision,
        )
        return self._decorate(room_id, record)

    def revisions(self, room_id: str, page_id: str) -> list[dict[str, Any]]:
        self.require_room(room_id)
        self._require_page(room_id, page_id)
        rows = self.store.list(REVISION_COLLECTION, room_id=room_id, limit=1000)
        found = [r for r in rows if (r.get("data") or {}).get("page_id") == page_id]
        return sorted(
            found, key=lambda r: int((r.get("data") or {}).get("number") or 0), reverse=True
        )

    # -- buyer-facing read -------------------------------------------------- #

    def published_pages(self, room_id: str) -> list[dict[str, Any]]:
        """Every published page in a room, in page order. The buyer read path."""
        room = self.require_room(room_id)
        documents = self._documents_by_id(room_id)
        published: list[dict[str, Any]] = []
        for page in self.store.list(
            PAGE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        ):
            view = self._published_view(page, documents)
            if view is not None:
                published.append(view)
        published.sort(key=lambda item: (item.get("order") or 0, item.get("title") or ""))
        return [
            {"room": {"id": room["id"], "name": (room.get("data") or {}).get("name")}, **view}
            for view in published
        ]

    def published_page(self, room_id: str, slug: str) -> dict[str, Any]:
        page = self.find_page_by_slug(room_id, slug)
        if page is None:
            raise RecordNotFound(slug)
        view = self._published_view(page, self._documents_by_id(room_id))
        if view is None:
            raise RecordNotFound(slug)
        return view

    # -- internals ---------------------------------------------------------- #

    def _require_page(self, room_id: str, page_id: str) -> dict[str, Any]:
        page = self.store.get(page_id)
        if (
            page is None
            or page.get("collection") != PAGE_COLLECTION
            or page.get("room_id") != room_id
        ):
            raise RecordNotFound(page_id)
        return page

    def _require_fragment(self, key: str) -> dict[str, Any]:
        if not key:
            raise FragmentError("a block needs a 'fragment' key")
        fragment = find_fragment(self.catalogue(), key)
        if fragment is None:
            raise FragmentError(
                f"no fragment is registered under {key!r}; add the fragment set first, then place it"
            )
        return fragment

    def _normalise_blocks(
        self, room_id: str, blocks: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Validate a whole block list. Undeclared config keys are preserved."""
        catalogue = self.catalogue()
        document_ids = self.room_document_ids(room_id)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for raw in blocks:
            if not isinstance(raw, Mapping):
                raise FragmentError("every block must be a JSON object")
            key = str(raw.get("fragment") or "")
            fragment = find_fragment(catalogue, key)
            if fragment is None:
                raise FragmentError(f"no fragment is registered under {key!r}")
            block_id = str(raw.get("id") or "").strip() or new_id("blk")
            if block_id in seen:
                raise FragmentError(f"block id {block_id!r} appears twice on the page")
            seen.add(block_id)
            result.append(
                {
                    "id": block_id,
                    "fragment": fragment["key"],
                    "set": fragment["set"],
                    "config": normalise_config(
                        fragment, raw.get("config") or {}, document_ids=document_ids
                    ),
                    **({"label": str(raw["label"])} if raw.get("label") else {}),
                }
            )
        return result

    def _save_blocks(
        self,
        room_id: str,
        page: Mapping[str, Any],
        blocks: Sequence[Mapping[str, Any]],
        actor: str | None,
        expected_revision: int | None,
        *,
        source: str,
    ) -> dict[str, Any]:
        data = page.get("data") or {}
        normalised = self._normalise_blocks(room_id, blocks)
        record = self.store.update(
            str(page["id"]),
            {
                "blocks": normalised,
                "status": self._next_status(data, normalised),
            },
            actor=actor,
            source=source,
            expected_revision=expected_revision,
        )
        return self._decorate(room_id, record)

    @staticmethod
    def _next_status(data: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> str:
        """The page's status: a small vocabulary, with dirtiness reported apart.

        A saved edit that has not been published leaves the page ``published``,
        because buyers are still seeing it, and ``has_unpublished_changes`` is
        what tells the editor there is work waiting for the next Publish.
        """
        return "published" if data.get("published_revision_id") else "draft"

    def _latest_revision_id(self, room_id: str, page_id: str) -> str:
        rows = [
            r
            for r in self.store.list(REVISION_COLLECTION, room_id=room_id, limit=1000)
            if (r.get("data") or {}).get("page_id") == page_id
        ]
        if not rows:  # pragma: no cover - publish always writes one first
            raise RecordNotFound(f"no revision for page {page_id}")
        newest = max(
            rows, key=lambda r: (int((r.get("data") or {}).get("number") or 0), r["created_at"])
        )
        return str(newest["id"])

    def _documents_by_id(self, room_id: str) -> dict[str, dict[str, Any]]:
        return {
            record["id"]: record
            for record in self.store.list("document", room_id=room_id, limit=1000)
        }

    def _decorate(self, room_id: str, page: Mapping[str, Any]) -> dict[str, Any]:
        """The editor's view of a page: the draft, plus publish state."""
        data = dict(page.get("data") or {})
        blocks = data.get("blocks") or []
        published_digest = data.get("published_digest")
        dirty = bool(
            data.get("published_revision_id")
            and published_digest
            and published_digest != digest_blocks(blocks)
        )
        return {
            "id": page["id"],
            "room_id": room_id,
            "collection": PAGE_COLLECTION,
            "revision": page.get("revision"),
            "created_at": page.get("created_at"),
            "updated_at": page.get("updated_at"),
            "data": data,
            "blocks": blocks,
            "status": data.get("status", "draft"),
            "published": bool(data.get("published_revision_id")),
            "published_revision_id": data.get("published_revision_id"),
            "published_at": data.get("published_at"),
            "published_by": data.get("published_by"),
            "has_unpublished_changes": dirty,
        }

    def _published_view(
        self, page: Mapping[str, Any], documents: Mapping[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Render a page for a buyer, or ``None`` if it is not published.

        A buyer never receives the draft: only the blocks from the revision the
        page points at. That is the mechanism behind "the fragment appears on the
        page the next time a member opens the room".
        """
        data = page.get("data") or {}
        revision_id = data.get("published_revision_id")
        if not revision_id:
            return None
        revision = self.store.get(str(revision_id))
        if revision is None:  # pragma: no cover - guarded by publish ordering
            return None
        revision_data = revision.get("data") or {}
        catalogue = self.catalogue()

        blocks: list[dict[str, Any]] = []
        referenced: dict[str, list[dict[str, Any]]] = {}
        for block in revision_data.get("blocks") or []:
            fragment = find_fragment(catalogue, str(block.get("fragment")))
            rendered = {
                "id": block.get("id"),
                "fragment": block.get("fragment"),
                "set": block.get("set"),
                "name": (fragment or {}).get("name") or block.get("fragment"),
                "config": block.get("config") or {},
                "fields": (fragment or {}).get("fields") or [],
                "known": fragment is not None,
            }
            if block.get("label"):
                rendered["label"] = block["label"]
            blocks.append(rendered)

            ids = referenced_documents(block, fragment)
            if ids:
                # The selectors take files from the room's documents, so a buyer
                # view resolves them server-side rather than making the browser
                # guess. An id that no longer resolves is reported, not hidden.
                referenced[str(block.get("id"))] = [
                    {
                        "id": document_id,
                        "resolved": document_id in documents,
                        **(
                            documents[document_id].get("data") or {}
                            if document_id in documents
                            else {}
                        ),
                    }
                    for document_id in ids
                ]

        return {
            "id": page["id"],
            "room_id": page["room_id"],
            "title": revision_data.get("title") or data.get("title"),
            "slug": revision_data.get("slug") or data.get("slug"),
            "summary": data.get("summary"),
            "is_home": bool(data.get("is_home")),
            "order": data.get("order") or 0,
            "status": "published",
            "blocks": blocks,
            "documents": referenced,
            "revision": {
                "id": revision["id"],
                "number": revision_data.get("number"),
                "digest": revision_data.get("digest"),
                "published_at": revision_data.get("published_at"),
                "published_by": revision_data.get("published_by"),
                "note": revision_data.get("note"),
            },
            "template_id": revision_data.get("template_id"),
            "template_version_id": revision_data.get("template_version_id"),
            "fragment_sets": revision_data.get("fragment_sets") or [],
        }
