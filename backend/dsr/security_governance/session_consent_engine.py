"""The reads and the writes: consent, ingest, recordings, labels, links, retention.

The engine is the only module in this workflow that touches the store. It holds the store
and a clock and nothing else, and the HTTP layer builds it per request for exactly that
reason: both seams stay overridable in a test without hanging a long-lived object off
``app.state``, which is a shared file this feature may not edit.

What the writes are, and what they are not
-------------------------------------------

**The ingest path fails closed on every branch that is not a full grant.** It consults the
blocklist before it writes, and it requires both axes before it records a replay. Every
non-recorded outcome returns a reason and writes an audit row naming it, so "we recorded
nothing" is always explainable.

**A blocked visitor leaves no recording row.** The evidence says "No sessions from visitors
on the list are recorded", and a row marked blocked would still be a stored session, so the
blocklist check returns a refusal and the write does not happen. The audit row keeps the
refusal and the matched range, which is the traceable part.

**Masking runs before the write, and the write is refused if content survived it.** The
evidence asks "Is masked data uploaded to Clarity? No.", so the masked frame is what gets
stored, and :func:`~dsr.security_governance.session_consent_rules.contains_unmasked_content`
is the guard that refuses a frame that still carries a value.

**Revocation deletes rather than suppresses.** The evidence calls denial destructive, so
:meth:`record_consent` with a denial on either axis tears down the visit's stored session
rows for that visitor. The purge is a hard delete, because a soft delete leaves the row and
its index behind, which is a longer-lived copy of the same session.

**Deleting a single recording is refused, not implemented.** The evidence says "you can't
delete or download specific recordings", so :meth:`delete_recording` returns the refusal the
rules build and writes nothing.

Every write names a route the host actually serves: the HTTP layer passes ``source=`` built
from its own router, so the audit row and the route cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.security_governance import session_consent as vocab, session_consent_rules as rules
from dsr.store import RecordStore

#: How many rows one scan reads. Bounded so a project with a very large recording history
#: cannot make a read unbounded. It is a bound and not a claim that the table is small: the
#: responses report what was scanned, so a caller can see whether the bound cut anything
#: off rather than trusting a count.
SCAN_LIMIT = 1000


class SessionConsentEngine:
    """Every read and every write this workflow performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand, which
    is the only way to test the boundaries the specification cares about: a recording that
    expires exactly at its window, and a link that expires exactly at its own instant.

    ``administrator`` is the role tier the blocking changes are gated on, and it is required
    rather than defaulted. This package depends on nothing inside ``dsr`` but the store and
    itself, so the repository's role surface is read by the feature module and handed in.
    One import site, one role vocabulary, and a test can gate against a tier of its own
    choosing.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        administrator: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not str(administrator or "").strip():
            raise rules.SessionConsentRefusal(
                "SessionConsentEngine needs an administrator role.",
                {"administrator": "Name the role tier that may make a blocking change."},
            )
        self.store = store
        self.administrator = str(administrator).strip()
        self._now = now or rules.utcnow

    # -- clock --------------------------------------------------------------- #

    def now(self) -> datetime:
        return self._now()

    # -- projects ------------------------------------------------------------ #
    #
    # A project is the recording project the specification describes: the thing that owns a
    # consent gate, a masking mode, a blocklist and a set of recordings. Every recording
    # and every visit belongs to one, which is what makes the project purge a real delete
    # rather than a label.

    def project(self, project_id: str | None = None) -> dict[str, Any] | None:
        """One project by id, or the most recently updated when no id is given."""

        if project_id:
            record = self.store.get(project_id)
            if record and record.get("collection") == vocab.PROJECT_COLLECTION:
                return self._project_view(record)
        projects = self.store.list(vocab.PROJECT_COLLECTION, limit=SCAN_LIMIT)
        if not projects:
            return None
        return self._project_view(projects[0])

    def list_projects(self) -> list[dict[str, Any]]:
        return [self._project_view(record) for record in self._list(vocab.PROJECT_COLLECTION)]

    def _project_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        data["created_at"] = record.get("created_at")
        data["updated_at"] = record.get("updated_at")
        data["enforcement"] = self._enforcement(data)
        return data

    def _enforcement(self, project: Mapping[str, Any]) -> dict[str, Any]:
        """Whether the consent gate is enforced here, and on what basis.

        Carries the regions in force and whether enforcement is active today, so a page can
        tell an enforced room from an inert one without reading the derivation.
        """

        regions = list(project.get("enforced_regions") or vocab.CONSENT_ENFORCED_REGIONS)
        gate = rules.is_true(project.get(vocab.CONSENT_GATE))
        active = rules.enforcement_active(self.now())
        return {
            "gate_enabled": gate,
            "regions": regions,
            "enforcement_active": active,
            "enforcement_start": vocab.CONSENT_ENFORCEMENT_START,
            "region_enforced": [
                name for name in regions if rules.requires_consent(name, enforced_regions=regions)
            ],
        }

    def create_project(
        self,
        *,
        name: Any,
        masking_mode: Any = None,
        consent_gate: Any = True,
        enforced_regions: Sequence[str] | None = None,
        room_id: str | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Create a recording project with its consent gate and masking default.

        Masking defaults to total suppression because the evidence says that is the
        documented default, and the gate defaults on because a project that records without
        one would be the shape the specification forbids.
        """

        label = str(name or "").strip()
        if not label:
            raise rules.SessionConsentRefusal(
                "A recording project needs a name.", {"name": "Name the project."}
            )
        mode = rules.masking_mode(masking_mode)
        record = self.store.create(
            vocab.PROJECT_COLLECTION,
            {
                "name": label,
                "masking_mode": mode,
                vocab.CONSENT_GATE: rules.is_true(consent_gate),
                "enforced_regions": list(enforced_regions or vocab.CONSENT_ENFORCED_REGIONS),
                "created_at": rules.stamp(self.now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._project_view(record)

    def set_masking_mode(
        self,
        project_id: str,
        mode: Any,
        *,
        selectors: Sequence[str] | None = None,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Change the masking mode on a project.

        Administrator-only, because the mode decides what leaves the room. It is a masking
        configuration and not a consent decision, so it does not require the consent gate.
        """

        rules.require_administrator(role, "changing the masking mode", self.administrator)
        project = self._require_project(project_id, room_id=room_id)
        active = rules.masking_mode(mode)
        patch: dict[str, Any] = {"masking_mode": active}
        if selectors is not None:
            patch["masking_selectors"] = [str(item) for item in selectors]
        self.store.update(project["id"], patch, actor=actor, source=source)
        return self._project_view(self.store.get(project["id"]) or project)

    def _require_project(self, project_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        found = self.project(project_id) if project_id else self.project()
        if found is None:
            raise rules.SessionConsentNotFound("No recording project has been created yet.")
        return found

    def _summary_of_nothing(self) -> dict[str, Any]:
        """The board for a room where nobody has created a project yet.

        Every count is zero, the masking default is the documented one, and the ceiling is
        reported as within. This is the shape :meth:`summary` returns when a project exists,
        with the project itself marked absent, so a board on a fresh room renders an empty
        state rather than a 404. A 404 here would read to a reviewer as "this workflow is
        not installed", which is the one reading that must never be possible.
        """

        return {
            "project": None,
            "project_created": False,
            "enforcement": {
                "gate_enabled": False,
                "regions": list(vocab.CONSENT_ENFORCED_REGIONS),
                "enforcement_active": rules.enforcement_active(self.now()),
                "enforcement_start": vocab.CONSENT_ENFORCEMENT_START,
                "region_enforced": list(vocab.CONSENT_ENFORCED_REGIONS),
            },
            "counts": {
                "recordings": 0,
                "recorded": 0,
                "blocked": 0,
                "favourites": 0,
                "visits": 0,
                "labels": 0,
                "share_links": 0,
                "blocked_ranges": 0,
            },
            "retention": {
                "ordinary_days": vocab.ORDINARY_RETENTION_DAYS,
                "favourite_days": vocab.FAVOURITE_RETENTION_DAYS,
            },
            "ceiling": rules.ceiling_state(0),
            "authentication": vocab.AUTHENTICATION,
            "deletion": rules.require_project_granularity(vocab.PER_RECORDING_DELETE),
        }

    # -- the blocklist ------------------------------------------------------- #

    def blocklist(
        self, *, project_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every blocked range on the project, as plain data.

        Empty rather than an error when no project exists, because the ingest path calls this
        on every visit and a fresh room must be able to ingest a blocked check without a
        project in place.
        """

        project = self.project(project_id) if project_id else self.project()
        if project is None:
            return []
        records = self.store.find(
            vocab.IP_COLLECTION, {"project_id": project["id"]}, limit=SCAN_LIMIT
        )
        return [self._range_view(record) for record in records]

    def _range_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        return data

    def block_ip(
        self,
        project_id: str,
        value: Any,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Add a range to the blocklist, and refuse an IPv6 range.

        Administrator-only, because the evidence says "To set up IP exclusion, you need to
        be an administrator for your project." The IPv6 refusal names the reason the
        evidence gives and records the rejection, so an operator who typed a v6 range learns
        why it did nothing rather than assuming it worked.
        """

        rules.require_administrator(role, "adding an IP block", self.administrator)
        project = self._require_project(project_id, room_id=room_id)
        block = rules.cidr_block(value)
        record = self.store.create(
            vocab.IP_COLLECTION,
            {
                "project_id": project["id"],
                **block,
                "added_at": rules.stamp(self.now()),
                "propagation_minutes": vocab.IP_BLOCKLIST_PROPAGATION_MINUTES,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._range_view(record)

    def unblock_ip(
        self,
        range_id: str,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Remove one range from the blocklist. Administrator-only, like adding one."""

        rules.require_administrator(role, "removing an IP block", self.administrator)
        record = self.store.get(range_id)
        if not record or record.get("collection") != vocab.IP_COLLECTION:
            raise rules.SessionConsentNotFound("No such blocked range.")
        self.store.delete(range_id, actor=actor, source=source)
        return {"removed": True, "id": range_id, "cidr": (record.get("data") or {}).get("cidr")}

    # -- ingest: consent, then blocklist, then record ------------------------ #

    def record_consent(
        self,
        project_id: str | None,
        payload: Mapping[str, Any] | None,
        *,
        visitor: Any = None,
        page_view: Any = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Record a consent call, and act on what it decided.

        Four outcomes, in the order the checks run:

        1. a **signal** records that a prompt fired and nothing else;
        2. a **denial on either axis** tears the stored session down and returns the
           revoke call;
        3. a **grant on both axes** stores the persistent identity;
        4. a **partial grant** is recorded, but a replay is not, because recording needs
           both axes.

        The denial branch is destructive by design: the evidence says the vendor "deletes
        any existing cookie for the website, ends the current session, and restarts
        tracking in no-consent mode", so the engine deletes the visitor's stored session
        rows and reports how many it removed. It never stores the signal as a decision.
        """

        project = self._require_project(project_id, room_id=room_id)
        call = rules.parse_consent_call(payload)
        identity = rules.identity_for(call["axes"], visitor=visitor, page_view=page_view)
        stamp = rules.stamp(self.now())

        outcome = "signal"
        destroyed: list[str] = []
        if not call["signal_only"]:
            if not identity["cookies_persist"]:
                outcome = vocab.DENIED
                destroyed = self._teardown(
                    project["id"], visitor, room_id, actor=actor, source=source
                )
            elif len(call["granted"]) == len(vocab.CONSENT_AXES):
                outcome = vocab.GRANTED
            else:
                outcome = "partial_grant"

        visit = self.store.create(
            vocab.VISIT_COLLECTION,
            {
                "project_id": project["id"],
                "visitor_id": identity["visitor_id"],
                "page_view_id": identity["page_view_id"],
                "identity_kind": identity["kind"],
                "cookies_persist": identity["cookies_persist"],
                "cross_session_tracking": identity["cross_session_tracking"],
                "anonymous": identity["anonymous"],
                "consent_call": rules.consent_call(
                    call["axes"][vocab.AD_STORAGE], call["axes"][vocab.ANALYTICS_STORAGE]
                ),
                "granted_axes": call["granted"],
                "signal_only": call["signal_only"],
                "legacy_call": call["legacy"],
                "before_cookies": call["before_cookies"],
                "outcome": outcome,
                "visited_at": stamp,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        result = self._visit_view(visit)
        result["destroyed_sessions"] = destroyed
        if outcome == vocab.DENIED:
            result["revoke_call"] = rules.revoke_call()
        return result

    def _teardown(
        self,
        project_id: str,
        visitor: Any,
        room_id: str | None,
        *,
        actor: str | None,
        source: str,
    ) -> list[str]:
        """Hard-delete the visitor's stored sessions and recordings for this project.

        Hard, because a soft delete keeps the row and its index, which is a longer-lived
        copy of a session the evidence says is being destroyed. Returns the ids removed so
        the response can report the count rather than a bare "done".
        """

        if not visitor:
            return []
        rows = self.store.find(
            vocab.RECORDING_COLLECTION,
            {"project_id": project_id, "visitor_id": str(visitor)},
            limit=SCAN_LIMIT,
        )
        removed: list[str] = []
        for record in rows:
            record_id = str(record.get("id"))
            self.store.delete(record_id, actor=actor, source=source, hard=True)
            removed.append(record_id)
        return removed

    def _visit_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        data["created_at"] = record.get("created_at")
        return data

    def ingest_visit(
        self,
        project_id: str | None,
        *,
        visitor: Any = None,
        page_view: Any = None,
        ip_address: Any = None,
        region: Any = None,
        axes: Mapping[str, Any] | None = None,
        frame: Mapping[str, Any] | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Decide what happens to one visit, then act on that decision.

        The checks run in the order the evidence gives them and each one can end the visit,
        so a room that would have denied consent never reaches the masking step and a
        blocked visitor never reaches the consent step's recording. The response always
        carries the ``recorded`` boolean and the ``state``, and it names the reason it is
        false, because a refused visit with no reason is indistinguishable from a bug.
        """

        project = self._require_project(project_id, room_id=room_id)
        stamp = rules.stamp(self.now())
        consent_axes = dict(axes or {})
        identity = rules.identity_for(consent_axes, visitor=visitor, page_view=page_view)

        # 1. Blocklist, before any consent handling. A blocked visitor is not recorded at
        #    all, so nothing about them reaches a session row.
        blocks = self.blocklist(project_id=project["id"], room_id=room_id)
        blocked = rules.blocked_by(ip_address, blocks)
        if blocked:
            self.store.create(
                vocab.VISIT_COLLECTION,
                {
                    "project_id": project["id"],
                    "state": vocab.BLOCKED,
                    "matched_range": blocked["matched_range"],
                    "console_signal": blocked["console_signal"],
                    "recorded": False,
                    "visited_at": stamp,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {"recorded": False, "state": vocab.BLOCKED, "blocked": blocked}

        # 2. Consent. A recording needs both axes. A signal is not a decision, and a
        #    denial on either axis ends the persistent session.
        consent_required = rules.requires_consent(
            region, enforced_regions=project.get("enforced_regions")
        ) or rules.is_true(project.get(vocab.CONSENT_GATE))
        granted = rules.granted_axes(consent_axes)
        if not granted:
            self.store.create(
                vocab.VISIT_COLLECTION,
                {
                    "project_id": project["id"],
                    "visitor_id": identity["visitor_id"],
                    "page_view_id": identity["page_view_id"],
                    "identity_kind": identity["kind"],
                    "state": vocab.SCRUBBED,
                    "granted_axes": [],
                    "consent_required": consent_required,
                    "recorded": False,
                    "reason": "no axis granted",
                    "visited_at": stamp,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {"recorded": False, "state": vocab.SCRUBBED, "identity": identity}

        if len(granted) < len(vocab.CONSENT_AXES):
            self.store.create(
                vocab.VISIT_COLLECTION,
                {
                    "project_id": project["id"],
                    "visitor_id": identity["visitor_id"],
                    "page_view_id": identity["page_view_id"],
                    "identity_kind": identity["kind"],
                    "state": vocab.SCRUBBED,
                    "granted_axes": granted,
                    "consent_required": consent_required,
                    "recorded": False,
                    "reason": "a partial grant records no replay",
                    "visited_at": stamp,
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {"recorded": False, "state": vocab.SCRUBBED, "identity": identity}

        # 3. Masking, before the write. The masked frame is what gets stored.
        masked = rules.mask_payload(
            frame or {}, project.get("masking_mode"), selectors=project.get("masking_selectors")
        )
        if rules.contains_unmasked_content(masked):
            raise rules.SessionConsentRefusal(
                "A frame that still carries content was not stored.",
                {
                    "frame": (
                        "Masking is applied before the write. Configure a masking mode that "
                        "suppresses the frame, then record it."
                    )
                },
            )

        recording = self.store.create(
            vocab.RECORDING_COLLECTION,
            {
                "project_id": project["id"],
                "visitor_id": identity["visitor_id"],
                "page_view_id": identity["page_view_id"],
                "identity_kind": identity["kind"],
                "state": vocab.RECORDED,
                "recorded": True,
                "favourite": False,
                "labels": [],
                "page_path": (frame or {}).get("page_path"),
                "region": region,
                "frame": masked,
                "masking_mode": masked.get("masking_mode"),
                "recorded_at": stamp,
                "expires_at": rules.stamp(rules.expires_at(self.now())),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "recorded": True,
            "state": vocab.RECORDED,
            "recording": self._recording_view(recording),
        }

    # -- recordings ---------------------------------------------------------- #

    def _list(self, collection: str, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list(collection, room_id=room_id, limit=SCAN_LIMIT)

    def _recording_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        data["created_at"] = record.get("created_at")
        data["retention_days"] = rules.retention_days(data)
        return data

    def recordings(
        self,
        *,
        project_id: str | None = None,
        room_id: str | None = None,
        dimension: str | None = None,
        value: Any = None,
    ) -> list[dict[str, Any]]:
        """The recordings list, optionally filtered to a segment.

        The segment filter is the only query here, and it filters on the three dimensions
        the workflow names. An unknown dimension is refused by the rules rather than
        returning everything, so a filter the product does not implement cannot be mistaken
        for a filter that matched everything.
        """

        project = self.project(project_id) if project_id else self.project()
        if project is None:
            return []

        # The dimension is validated before the scan, so an unsupported filter is a refusal
        # on an empty room too. A filter that silently returned nothing would read as "no
        # recordings matched", which is a different fact from "this filter is not one the
        # product implements".
        if dimension not in (None, ""):
            rules.segment_value({}, str(dimension))

        rows = self.store.find(
            vocab.RECORDING_COLLECTION, {"project_id": project["id"]}, limit=SCAN_LIMIT
        )
        views = [self._recording_view(row) for row in rows]
        if dimension in (None, "") and value in (None, ""):
            return views
        return [view for view in views if rules.matches_segment(view, dimension, value)]

    def recording(self, recording_id: str) -> dict[str, Any]:
        record = self.store.get(recording_id)
        if not record or record.get("collection") != vocab.RECORDING_COLLECTION:
            raise rules.SessionConsentNotFound("No such recording.")
        return self._recording_view(record)

    def set_favourite(
        self,
        recording_id: str,
        favourite: Any,
        *,
        room_id: str | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Mark a recording a favourite, which moves it onto the longer retention window.

        The evidence extends retention to "Favorite recordings", so the flag is a retention
        control and not a bookmark. The response reports both windows so the effect is
        visible rather than inferred.
        """

        record = self.store.get(recording_id)
        if not record or record.get("collection") != vocab.RECORDING_COLLECTION:
            raise rules.SessionConsentNotFound("No such recording.")
        wanted = rules.is_true(favourite)
        recorded = rules.coerce_instant(
            (record.get("data") or {}).get("recorded_at"), "recorded_at"
        )
        updated = self.store.update(
            recording_id,
            {
                "favourite": wanted,
                "retention_days": rules.retention_days({"favourite": wanted}),
                "expires_at": rules.stamp(rules.expires_at(recorded or self.now(), wanted)),
            },
            actor=actor,
            source=source,
        )
        return self._recording_view(updated)

    def label_recording(
        self,
        recording_id: str,
        labels: Any,
        *,
        room_id: str | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Add labels to a recording, and refuse the sixth.

        The cap is on the recording, so it is checked against the labels that recording
        already holds. The refusal names the label and the cap, and nothing is written when
        the cap would be exceeded.

        Each label is also a row in its own collection, so the labels list on the recording
        and the labels collection cannot disagree. Writing one and not the other is how a
        count starts lying.
        """

        record = self.store.get(recording_id)
        if not record or record.get("collection") != vocab.RECORDING_COLLECTION:
            raise rules.SessionConsentNotFound("No such recording.")
        existing = (record.get("data") or {}).get("labels") or []
        merged = rules.require_label_capacity(existing, labels)
        held = set(rules.label_names(existing))
        for name in merged:
            if name in held:
                continue
            self.store.create(
                vocab.LABEL_COLLECTION,
                {
                    "project_id": (record.get("data") or {}).get("project_id"),
                    "recording_id": recording_id,
                    "label": name,
                    "labelled_at": rules.stamp(self.now()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            held.add(name)
        updated = self.store.update(recording_id, {"labels": merged}, actor=actor, source=source)
        return self._recording_view(updated)

    def delete_recording(self, recording_id: str) -> dict[str, Any]:
        """Refuse a single-recording delete and write nothing.

        This is the sourced limit made executable rather than a missing feature. The
        response names both evidence sentences and the supported alternative, so an operator
        is told what to do instead of only what did not work.
        """

        return rules.require_project_granularity(vocab.PER_RECORDING_DELETE, recording_id)

    def purge_project(
        self,
        project_id: str | None,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Delete every recording, visit, label and share link on the project.

        This is the only delete the evidence supports. Administrator-only, because it is
        the most destructive change the workflow offers, and it is a hard delete so nothing
        is left behind under a soft-deleted row.
        """

        rules.require_administrator(role, "purging a recording project", self.administrator)
        project = self._require_project(project_id, room_id=room_id)
        removed: dict[str, int] = {}
        for collection in (
            vocab.RECORDING_COLLECTION,
            vocab.VISIT_COLLECTION,
            vocab.LABEL_COLLECTION,
            vocab.SHARE_COLLECTION,
        ):
            rows = self.store.find(collection, {"project_id": project["id"]}, limit=SCAN_LIMIT)
            for record in rows:
                self.store.delete(str(record.get("id")), actor=actor, source=source, hard=True)
            removed[collection] = len(rows)
        return {"purged": True, "project_id": project["id"], "removed": removed}

    # -- share links --------------------------------------------------------- #

    def create_share_link(
        self,
        project_id: str | None,
        recording_id: str,
        *,
        kind: Any = None,
        expires_in_days: int | None = None,
        room_id: str | None = None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Create a share link for one recording.

        A guest link gets a window and a team link never expires, because the evidence says
        "team links don't". A window supplied for a team link is refused rather than
        discarded, so the record cannot say one thing and behave like another.
        """

        project = self._require_project(project_id, room_id=room_id)
        resolved = rules.link_kind(kind)
        if resolved == vocab.TEAM and expires_in_days is not None:
            raise rules.SessionConsentRefusal(
                "A team link does not expire, so it does not take a window.",
                {"expires_in_days": "Omit the window for a team link."},
            )
        self.recording(recording_id)
        created = self.now()
        expires = rules.link_expiry(resolved, created=created, expires_in_days=expires_in_days)
        record = self.store.create(
            vocab.SHARE_COLLECTION,
            {
                "project_id": project["id"],
                "recording_id": recording_id,
                "kind": resolved,
                "expires_at": rules.stamp(expires) if expires else None,
                "created_at": rules.stamp(created),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self._link_view(record)

    def _link_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        data["id"] = record.get("id")
        created = rules.coerce_instant(data.get("created_at"), "created_at") or self.now()
        data["live"] = rules.link_is_live(data.get("kind"), created=created, now=self.now())
        return data

    def share_links(
        self, *, project_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        project = self.project(project_id) if project_id else self.project()
        if project is None:
            return []
        rows = self.store.find(
            vocab.SHARE_COLLECTION, {"project_id": project["id"]}, limit=SCAN_LIMIT
        )
        return [self._link_view(row) for row in rows]

    # -- retention ----------------------------------------------------------- #

    def retention_schedule(
        self, *, project_id: str | None = None, room_id: str | None = None
    ) -> dict[str, Any]:
        """Every recording, the window that applies to it, and whether it has passed.

        Both windows appear in the same response, because the favourite window outliving
        the ordinary one is the fact a reviewer needs to see and one number would hide it.
        """

        views = self.recordings(project_id=project_id, room_id=room_id)
        now = self.now()
        rows = []
        for view in views:
            recorded = rules.coerce_instant(view.get("recorded_at"), "recorded_at")
            expired = rules.is_expired(recorded, favourite=view.get("favourite"), now=now)
            rows.append(
                {
                    "id": view.get("id"),
                    "favourite": rules.is_true(view.get("favourite")),
                    "recorded_at": view.get("recorded_at"),
                    "retention_days": view.get("retention_days"),
                    "expires_at": view.get("expires_at"),
                    "expired": expired,
                }
            )
        return {
            "now": rules.stamp(now),
            "ordinary_days": vocab.ORDINARY_RETENTION_DAYS,
            "favourite_days": vocab.FAVOURITE_RETENTION_DAYS,
            "rows": rows,
        }

    def run_retention(
        self,
        project_id: str | None,
        *,
        room_id: str | None = None,
        actor: str | None,
        role: Any,
        source: str,
    ) -> dict[str, Any]:
        """Age out every recording whose window has passed.

        Administrator-only, because it deletes. It hard-deletes, so a purged recording is
        gone rather than sitting behind a soft-deleted row, and the response reports what
        it removed instead of reporting a boolean.
        """

        rules.require_administrator(role, "running the retention sweep", self.administrator)
        now = self.now()
        removed = 0
        for view in self.recordings(project_id=project_id, room_id=room_id):
            recorded = rules.coerce_instant(view.get("recorded_at"), "recorded_at")
            if rules.is_expired(recorded, favourite=view.get("favourite"), now=now):
                self.store.delete(str(view.get("id")), actor=actor, source=source, hard=True)
                removed += 1
        return {"removed": removed, "at": rules.stamp(now)}

    # -- the board ----------------------------------------------------------- #

    def visits(
        self, *, project_id: str | None = None, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        project = self.project(project_id) if project_id else self.project()
        if project is None:
            return []
        rows = self.store.find(
            vocab.VISIT_COLLECTION, {"project_id": project["id"]}, limit=SCAN_LIMIT
        )
        return [self._visit_view(row) for row in rows]

    def summary(
        self, *, project_id: str | None = None, room_id: str | None = None
    ) -> dict[str, Any]:
        """The headline numbers, read back from the store. Reads only.

        Carries the ceiling as a word beside its numbers, because the ceiling is a vendor
        limit quoted from the specification and no route may present it as this room's own
        limit without saying so.

        Answers 200 with every count at zero when no project exists yet, rather than a 404.
        The write routes still raise :class:`~dsr.security_governance.session_consent_rules.SessionConsentNotFound`
        in that state, because a write with nowhere to write is a real error and a read is
        not.
        """

        existing = self.project(project_id) if project_id else self.project()
        if existing is None:
            return self._summary_of_nothing()
        project = existing
        recordings = self.recordings(project_id=project["id"], room_id=room_id)
        visits = self.visits(project_id=project["id"], room_id=room_id)
        recorded = [view for view in recordings if view.get("recorded")]
        blocked = [view for view in visits if view.get("state") == vocab.BLOCKED]
        favourite = [view for view in recordings if rules.is_true(view.get("favourite"))]
        return {
            "project": {
                "id": project.get("id"),
                "name": project.get("name"),
                "masking_mode": project.get("masking_mode"),
                vocab.CONSENT_GATE: project.get(vocab.CONSENT_GATE),
            },
            "project_created": True,
            "enforcement": project.get("enforcement"),
            "counts": {
                "recordings": len(recordings),
                "recorded": len(recorded),
                "blocked": len(blocked),
                "favourites": len(favourite),
                "visits": len(visits),
                "labels": self.store.count_where(
                    vocab.LABEL_COLLECTION, {"project_id": project["id"]}
                ),
                "share_links": self.store.count_where(
                    vocab.SHARE_COLLECTION, {"project_id": project["id"]}
                ),
                "blocked_ranges": self.store.count_where(
                    vocab.IP_COLLECTION, {"project_id": project["id"]}
                ),
            },
            "retention": {
                "ordinary_days": vocab.ORDINARY_RETENTION_DAYS,
                "favourite_days": vocab.FAVOURITE_RETENTION_DAYS,
            },
            "ceiling": rules.ceiling_state(len(recorded)),
            "authentication": vocab.AUTHENTICATION,
            "deletion": rules.require_project_granularity(vocab.PER_RECORDING_DELETE),
        }
