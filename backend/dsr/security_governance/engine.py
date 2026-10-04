"""The writes: a governed link, a toggle in place, a page against a viewport, an attempt.

The engine is the only module in this package that writes. It holds the store and a
clock and nothing else, and it is built per request by the HTTP layer for exactly
that reason: both seams stay overridable in a test without hanging a long-lived
object off ``app.state``, which is a shared file this feature may not edit.

Every write below carries an ``actor`` and a ``source``, and both reach the audit log
in the same transaction as the change. The ``source`` is the route that served the
write, which is why no string in this module is a literal route: the feature module
builds each one from its own router, and ``tests/test_wf073.py`` asserts every source
this workflow can record names a concrete ``(method, path)`` the host mounted.

The order the rules are enforced in
----------------------------------

Validate the settings before writing anything, so a rejected request leaves no trace.
That is not a style choice. The audit log is this product's guarantee, and an audit
trail carrying a row for a request that changed nothing is a trail a reader has to
learn to discount.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.security_governance import rules, vocabulary as vocab
from dsr.store import RecordStore


class ConfidentialEngine:
    """Every write and read this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand.
    The confidential-view geometry is a statement about a viewport rather than about a
    time, so the clock matters here only for the stamps on attempts and links, but it
    is injected for the same reason it is everywhere else in this product: a test that
    cannot choose the instant cannot test a boundary.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- links -------------------------------------------------------------- #

    def create_link(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Create a governed link. Mirrors ``POST /v1/links``.

        Both flags default to the documented ``False`` when the payload omits them, and
        a ``preset_id`` seeds them first so a governed baseline is what a link starts
        from rather than something the seller has to remember to apply.

        Only the two flags this workflow owns are interpreted. Any other field in the
        payload is carried through untouched, because the store is schema-flexible by
        design and a link created here may be the same link another workflow's gate
        wrote to.
        """

        data = dict(payload or {})
        preset_id = data.get("preset_id")
        if preset_id:
            preset = self.store.get(str(preset_id))
            if preset is None or preset.get("collection") != vocab.PRESET_COLLECTION:
                raise rules.PresetNotFound(str(preset_id))
            baseline = rules.preset_baseline(preset.get("data") or {})
        else:
            baseline = dict(vocab.DEFAULTS)

        flags = rules.apply_flags(baseline, data, strict=True)

        body: dict[str, Any] = {
            rules.ROOM_REF: room_id,
            "title": data.get("title") or f"Confidential link for {room_id}",
            "preset_id": preset_id,
            "created_at": rules.stamp(self._now()),
            "revision_note": "created",
        }
        body.update(flags)
        for key, value in data.items():
            if key not in ("preset_id", "room_id"):
                body.setdefault(key, value)

        record = self.store.create(
            vocab.LINK_COLLECTION,
            body,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project(record)

    def links(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every governed link, optionally narrowed to one room."""
        records = self.store.list(vocab.LINK_COLLECTION, limit=200, order_by="created_at")
        if room_id:
            records = [
                record
                for record in records
                if rules.room_ref_of(record.get("data") or {}, record) == room_id
            ]
        return [self.project(record) for record in records]

    def read_link(self, link_id: str) -> dict[str, Any]:
        """One link, or :class:`~dsr.security_governance.rules.LinkNotFound`.

        A row that exists but is not one of this workflow's links is a 404 rather than
        a 500 or a projection of somebody else's record: a seller who passes the wrong
        id deserves to be told it does not name a governed link, and a feature may not
        read across into another workflow's collection.
        """

        record = self.store.get(link_id)
        if record is None or record.get("collection") != vocab.LINK_COLLECTION:
            raise rules.LinkNotFound(link_id)
        return self.project(record)

    def update_link(
        self,
        link_id: str,
        changes: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Toggle either flag in place. Mirrors ``PATCH /v1/links/{id}``.

        Tri-state, so this endpoint can both rotate a control and remove one: a field
        the body omits is left alone, a boolean sets it, and an explicit ``null``
        returns it to the documented default. Nothing else about the link moves - the
        id, the room and the URL are untouched, which is the specification's
        "the URL and existing viewers are unaffected".

        Only the two flags are read out of the body. A caller that sends other fields
        is not silently ignored either: they are carried through as stored JSON, which
        is what the schema-flexible store is for, and no rule in this package claims
        to have enforced them.
        """

        record = self._record(link_id)
        flags = rules.apply_flags(record.get("data") or {}, changes, strict=False)

        patch: dict[str, Any] = dict(flags)
        for key, value in changes.items():
            if key not in vocab.FLAGS:
                patch[key] = value

        updated = self.store.update(
            link_id,
            patch,
            actor=actor,
            source=source,
        )
        return self.project(updated)

    def _record(self, link_id: str) -> dict[str, Any]:
        """The stored link row, or :class:`~dsr.security_governance.rules.LinkNotFound`.

        Kept separate from :meth:`read_link` because a patch needs the payload as well
        as the projection: the projection carries the two flags as booleans, and a patch
        has to merge into the payload the rest of the link is stored in. Projecting
        first and writing the projection back would quietly drop every field this
        workflow does not interpret - which is exactly what the schema-flexible store
        exists to keep.
        """

        record = self.store.get(link_id)
        if record is None or record.get("collection") != vocab.LINK_COLLECTION:
            raise rules.LinkNotFound(link_id)
        return dict(record)

    def project(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A stored link as the API returns it.

        The projection is where the specification's own caution is enforced. Every
        response carries ``effect: "deterrent"`` and the limitation sentence beside the
        two controls, so a caller cannot read what these controls are set to without
        also reading what they are worth.
        """

        data = dict(record.get("data") or {})
        payload: dict[str, Any] = {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "title": data.get("title"),
            "preset_id": data.get("preset_id"),
            "created_at": data.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            "controls": rules.enabled_flags(data),
            "flags": {field: bool(data.get(field, vocab.DEFAULTS[field])) for field in vocab.FLAGS},
        }
        payload.update(rules.controls_summary(data))
        return payload

    # -- presets ------------------------------------------------------------ #

    def create_preset(
        self,
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Define a governance baseline every future link can be seeded from.

        "Both flags are ``preset_id``-coverable, so they can be governance baselines."
        Only the two flags are read out of ``fields``. Anything else in the payload is
        stored, because the other sixteen preset-covered fields belong to other
        workflows and this one enforces only what it researched.
        """

        data = dict(payload or {})
        fields = data.get("fields")
        if not isinstance(fields, Mapping):
            fields = {}
        record = self.store.create(
            vocab.PRESET_COLLECTION,
            {
                "name": data.get("name") or "Governance baseline",
                "description": data.get("description") or "",
                "fields": dict(fields),
                "flags": rules.preset_baseline(fields),
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return rules.project_preset(record)

    def presets(self) -> list[dict[str, Any]]:
        records = self.store.list(vocab.PRESET_COLLECTION, limit=200, order_by="created_at")
        return [rules.project_preset(record) for record in records]

    def read_preset(self, preset_id: str) -> dict[str, Any]:
        record = self.store.get(preset_id)
        if record is None or record.get("collection") != vocab.PRESET_COLLECTION:
            raise rules.PresetNotFound(preset_id)
        return rules.project_preset(record)

    # -- confidential view: the page against a viewport ---------------------- #

    def render_page(
        self,
        link_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve which band of one page is sharp, for one viewer's viewport.

        This is the endpoint the specification's third user-flow step describes:
        "Buyer scrolls the document; content only resolves inside the focus band, so a
        single screenshot cannot capture a whole page."

        Two things it deliberately does not do. It does not refuse anyone, because
        confidential view is a rendering transformation and not a gate, and gating a
        link is WF-069's domain. And it does not return the page: it returns the
        geometry and the one sharp band, which is the property the specification
        states as the reason this works at all - "the full-resolution page never
        reaches the client for out-of-band regions."

        A link whose confidential-view flag is off is answered with the whole page
        sharp and ``applied: False``, because the caller asked a question the link's
        own settings decline to apply. That is not an error, and returning geometry for
        it would imply the control was on.

        It takes no ``source`` and writes no audit row, because it changes nothing.
        Resolving a viewport against stored flags is a read, and a route that recorded
        a row on every read would fill this product's own guarantee - the audit log -
        with entries describing no change having been made.
        """

        link = self.read_link(link_id)
        body = dict(payload or {})
        page = body.get("page") or body.get("page_index") or 1

        page_height = body.get("page_height", 0)
        viewport_height = body.get("viewport_height", 0)
        viewport_top = body.get("viewport_top", 0)

        applied = bool(link["flags"][vocab.CONFIDENTIAL_VIEW])
        geometry = rules.band_geometry(page_height, viewport_height, viewport_top=viewport_top)
        if not applied:
            # One whole-page band. The geometry is still computed rather than
            # skipped, so the response shape is identical whether the control is on
            # or off and the page does not have to branch on a missing key.
            return {
                "link_id": link_id,
                "page": page,
                "applied": False,
                "reason": "confidential_view_off",
                "shape": geometry["shape"],
                "page_height": geometry["page_height"],
                "viewport_height": geometry["viewport_height"],
                "viewport_top": 0,
                "band_count": 1,
                "bands": [
                    {
                        "index": 0,
                        "top": 0,
                        "bottom": geometry["page_height"],
                        "sharp": True,
                        "text_length": 0,
                        "text": None,
                    }
                ],
                "sharp_index": 0,
                "sharp_top": 0,
                "sharp_bottom": geometry["page_height"],
                "sharp_fraction": 1.0,
                "blurred_count": 0,
                vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
                vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
            }

        resolved = [rules.resolve_band(geometry, band["index"]) for band in geometry["bands"]]
        for band in resolved:
            band["text_length"] = int(body.get("page_text_length") or 0) if band["sharp"] else 0

        return {
            "link_id": link_id,
            "page": page,
            "applied": True,
            "reason": "confidential_view_on",
            "shape": geometry["shape"],
            "page_height": geometry["page_height"],
            "viewport_height": geometry["viewport_height"],
            "viewport_top": geometry["viewport_top"],
            "band_count": len(resolved),
            "bands": resolved,
            "sharp_index": geometry["sharp_index"],
            "sharp_top": geometry["sharp_top"],
            "sharp_bottom": geometry["sharp_bottom"],
            "sharp_fraction": geometry["sharp_fraction"],
            "blurred_count": geometry["blurred_count"],
            vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
        }

    # -- screenshot protection: a reported attempt --------------------------- #

    def report_attempt(
        self,
        link_id: str,
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record one capture attempt the viewer's browser reported.

        This is a report, and the response says so in its ``state``. The specification's
        criticality note is that screenshot blocking "is largely unenforceable from a
        browser", so a row claiming a capture was prevented would be a claim the
        product cannot support. What is recorded is what the browser saw.

        A shortcut the derived list does not have is refused rather than stored with a
        blank name. The list is a derivation, so an unrecognised sequence most likely
        means this build's list is behind a platform change, and a governance log that
        silently accepted unknown keys would fill with rows nobody can interpret.

        ``outcome`` is either ``blocked`` for a shortcut a page can intercept or
        ``reported`` for one it cannot, and it is derived from the list rather than
        from the caller. Letting the caller choose would let a browser that wanted to
        look compliant report itself as blocked, which would make the whole log a
        self-assessment.
        """

        link = self.read_link(link_id)
        body = dict(payload or {})
        keys = body.get("keys")
        if keys is None:
            keys = body.get("shortcut")

        shortcut = rules.shortcut_for(keys)
        if shortcut is None:
            raise rules.UnknownShortcut(
                "This shortcut is not in the researched capture list. Report the keys "
                "pressed, in any order."
            )

        blockable = bool(shortcut.get("blockable", True))
        outcome = vocab.ATTEMPT_BLOCKED if blockable else vocab.ATTEMPT_REPORTED

        record = self.store.create(
            vocab.ATTEMPT_COLLECTION,
            {
                rules.ROOM_REF: link["room_id"],
                "link_id": link_id,
                "shortcut": shortcut["name"],
                "keys": list(shortcut["keys"]),
                "action": shortcut["action"],
                "platform": shortcut.get("platform", "other"),
                "blockable": blockable,
                "outcome": outcome,
                "at": rules.stamp(self._now()),
                "screenshot_protection": link["flags"][vocab.SCREENSHOT_PROTECTION],
                "reported_by": body.get("reported_by") or "viewer",
            },
            room_id=link["room_id"],
            actor=actor,
            source=source,
        )
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "link_id": link_id,
            "shortcut": data.get("shortcut"),
            "action": data.get("action"),
            "outcome": data.get("outcome"),
            "blockable": data.get("blockable"),
            "at": data.get("at"),
            "screenshot_protection_on": data.get("screenshot_protection"),
            "state": vocab.ATTEMPT_BLOCKED if blockable else vocab.ATTEMPT_REPORTED,
            vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
        }

    def attempts(
        self, room_id: str | None = None, link_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Reported attempts, newest first, optionally narrowed.

        Each row carries the effect and the limitation, so a row read out of this list
        on its own - in a log export, in a page that renders one attempt, in a test -
        says what the control is worth. An attempt row is exactly the row somebody would
        mistake for evidence that a capture was prevented, so that is where the caveat
        has to be.
        """

        records = self.store.list(
            vocab.ATTEMPT_COLLECTION, limit=200, order_by="updated_at", descending=True
        )
        rows = []
        for record in records:
            row = dict(record.get("data") or {})
            row["id"] = record.get("id")
            row[vocab.EFFECT_FIELD] = vocab.EFFECT_VALUE
            row[vocab.LIMITATION_FIELD] = vocab.LIMITATION_VALUE
            rows.append(row)
        if room_id:
            rows = [row for row in rows if row.get(rules.ROOM_REF) == room_id]
        if link_id:
            rows = [row for row in rows if row.get("link_id") == link_id]
        return rows

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The page's headline numbers, and the states that need a seller's attention.

        Read-only. Counts are read back from the store rather than accumulated, so the
        board cannot describe a state the store does not hold.
        """

        links = self.links(room_id)
        attempts = self.attempts(room_id)
        presets = self.presets()

        confidential = [link for link in links if link["flags"][vocab.CONFIDENTIAL_VIEW]]
        protected = [link for link in links if link["flags"][vocab.SCREENSHOT_PROTECTION]]
        both = [
            link
            for link in links
            if link["flags"][vocab.CONFIDENTIAL_VIEW] and link["flags"][vocab.SCREENSHOT_PROTECTION]
        ]
        neither = [
            link
            for link in links
            if not link["flags"][vocab.CONFIDENTIAL_VIEW]
            and not link["flags"][vocab.SCREENSHOT_PROTECTION]
        ]

        blocked = [row for row in attempts if row.get("outcome") == vocab.ATTEMPT_BLOCKED]
        reported = [row for row in attempts if row.get("outcome") == vocab.ATTEMPT_REPORTED]

        return {
            "links": len(links),
            "presets": len(presets),
            "confidential_view": len(confidential),
            "screenshot_protection": len(protected),
            "both_controls": len(both),
            "no_controls": len(neither),
            "attempts": len(attempts),
            "attempts_blockable": len(blocked),
            "attempts_unblockable": len(reported),
            "by_shortcut": _tally(attempts, "shortcut"),
            vocab.EFFECT_FIELD: vocab.EFFECT_VALUE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION_VALUE,
        }


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """How many rows carry each value for ``key``, largest first then alphabetical.

    Sorted so the page and the API agree on the order without either of them
    re-sorting, which is the kind of small disagreement that becomes a flaky test.
    """

    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
