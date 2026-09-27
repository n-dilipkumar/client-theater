"""Collection reads this workflow needs, assembled from the store's public API.

The branch added three methods to ``dsr/store.py`` - ``scan``, ``count`` and
``audit_count`` - and this module is where they live in the port instead.
``dsr/store.py`` is a shared file, and twelve workflows each appending to it is
the exact collision the feature host exists to remove, so the port cannot carry
those edits. The three helpers are reconstructed here on top of the API the store
already exposes, which means:

* every read still goes through :class:`~dsr.store.RecordStore` and therefore
  through the audited connection. Nothing here opens the database, and a read
  still writes no audit row, because the audit log describes changes;
* the behaviour matches what the branch's versions did. ``scan`` is every live
  record in a collection in ``id`` order, which is what makes an offset paging
  cursor safe: ``updated_at`` is neither unique nor stable, so paging over it can
  overlap or skip rows.

The one real difference is that :meth:`RecordStore.list` caps a single call at
1000 rows, so :func:`scan` pages with ``offset`` until it runs out. That is why
this is a function and not a one-liner, and it is why a library larger than 1000
documents is still searched in full.
"""

from __future__ import annotations

from typing import Any

#: The store clamps every ``limit`` to this, so paging in steps of it walks the
#: whole collection without a special case.
PAGE = 1000


def scan(
    store: Any,
    collection: str,
    *,
    room_id: str | None = None,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    """Every record in a collection, in stable ``id`` order.

    ``room_id`` scopes the read to one room. ``include_deleted`` is off by
    default, matching the store: a soft-deleted document is not something a
    seller should find in the library.
    """
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.list(
            collection,
            room_id=room_id,
            include_deleted=include_deleted,
            order_by="id",
            descending=False,
            limit=PAGE,
            offset=offset,
        )
        found.extend(page)
        if len(page) < PAGE:
            return found
        offset += len(page)


def count(
    store: Any,
    collection: str,
    *,
    room_id: str | None = None,
    include_deleted: bool = False,
) -> int:
    """How many live records a collection holds."""
    return len(scan(store, collection, room_id=room_id, include_deleted=include_deleted))


def audit_count(store: Any, **filters: Any) -> int:
    """How many audit rows match ``filters``.

    ``AuditedDatabase.audit_count`` does this in one ``COUNT(*)``; the store does
    not expose it, so this counts the rows the store will hand back. Exact rather
    than fast, which is the right trade for a test assertion and the wrong one for
    a dashboard. If a page ever needs this on a hot path, promoting the existing
    ``AuditedDatabase.audit_count`` through ``RecordStore`` is a one-line shared
    change - it is raised in this port's report rather than smuggled in here.
    """
    return len(store.audit(**{"limit": PAGE, **filters}))
