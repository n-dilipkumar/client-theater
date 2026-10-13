"""WF-047: mirror room documents into CRM files.

The workflow the research describes, in the order it describes it:

1. A rep uploads a deck or a proposal into the room, or an asset is generated.
2. The rep toggles **Push to CRM** on the asset, or the connection mirrors every
   room document.
3. The room uploads the binary to the CRM files endpoint with
   ``multipart/form-data``, then writes the returned file id onto the CRM record
   that owns it.
4. The room stores ``crm_file_id`` on the asset row, so re-uploads are skipped and
   revisions create a new file.
5. The rep sees the CRM attachment from the record's Files panel or note timeline.

Two writes, and they belong in one transaction
----------------------------------------------
Steps 3 and 4 are one outcome: a file id the CRM never stores, or a stored id
nobody wrote onto the asset row, are both a state a rep cannot act on. The mirror
runs the vendor calls first and then commits the mirror row and the asset's
``crm_file_id`` in a single ``AuditedDatabase.transaction``, so a crash between
them cannot leave one without the other.

A vendor call inside a transaction is a deliberate ordering choice, not an
oversight. The call is made *before* the transaction opens, so the transaction
holds only two local writes and never a network round trip, and the alternative -
opening the transaction first - would hold the single writer lock across the
network, which stalls every other write in the product for the length of the
upload.

The reconciliation pass
-----------------------
"Mirror is event-driven (asset created/updated) and can be a scheduled
reconciliation pass ('find room assets with no ``crm_file_id`` and push them')."

The rule is quoted and implemented in :meth:`CrmFileSyncEngine.reconcile`. The
cadence is not, because the research does not give one; see
:func:`~dsr.crm_file_sync.assets.needs_mirror` and the
``reconcile-is-routed-not-scheduled`` inference. Nothing in this package starts a
thread. The pass is a method, exposed as a route, and a scheduler decides when to
call it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.crm_file_sync import assets as asset_rules, connectors, vocabulary
from dsr.crm_file_sync.connectors import Attachment, UploadedFile
from dsr.crm_file_sync.errors import (
    DuplicateConnection,
    NoOwnerRecord,
    UnknownAsset,
    UnknownConnection,
    UnknownMirror,
    UnsupportedSystem,
    attach_mode_for,
    owner_object_for,
    system_for,
)
from dsr.store import RecordStore


def _utcnow() -> str:
    """A timestamp in the store's own format, from one place."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _as_data(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A record's payload with its envelope fields folded in, or ``None``.

    The caller usually wants ``crm_file_id`` or ``connection_id``, which live in
    the payload, but also wants ``id`` and ``revision``, which live in the
    envelope. Folding them in once means no caller has to remember which is which.
    """
    if row is None:
        return None
    return dict(row.get("data") or {}) | {
        "id": row.get("id"),
        "collection": row.get("collection"),
        "room_id": row.get("room_id"),
        "revision": row.get("revision"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class CrmFileSyncEngine:
    """The whole workflow, over the process-wide audited store.

    Built per request from the store handle rather than kept on ``app.state``,
    which would be the edit to the shared ``dsr/api.py`` the feature host exists to
    make unnecessary. The engine holds nothing but the store, so a test builds one
    the same way the route does.
    """

    def __init__(self, store: RecordStore, *, rng: Any = None) -> None:
        self.store = store
        self.rng = rng

    # ----------------------------------------------------------------- #
    # Vocabulary
    # ----------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published word this workflow uses, served as data.

        A client renders its pickers from this rather than from a list compiled
        into a page, so a state added here reaches every client at once.
        """
        return {
            "ticket": "WF-047",
            "systems": list(vocabulary.SYSTEMS),
            "file_capable_systems": list(vocabulary.FILE_CAPABLE_SYSTEMS),
            "unsourced_systems": dict(vocabulary.UNSOURCED_SYSTEMS),
            "upload_path": f"/{vocabulary.UPLOAD_PATH}",
            "upload_content_type": vocabulary.UPLOAD_CONTENT_TYPE,
            "upload_required_fields": list(vocabulary.UPLOAD_REQUIRED_FIELDS),
            "upload_optional_fields": list(vocabulary.UPLOAD_OPTIONAL_FIELDS),
            "folder_recommendation": vocabulary.FOLDER_RECOMMENDATION,
            "folder_caveat": vocabulary.FOLDER_CAVEAT,
            "attach_modes": list(vocabulary.ATTACH_MODES),
            "default_attach_mode": vocabulary.DEFAULT_ATTACH_MODE,
            "attach_mode_notes": dict(vocabulary.ATTACH_MODE_NOTES),
            "owner_objects": list(vocabulary.OWNER_OBJECTS),
            "default_owner_object": vocabulary.DEFAULT_OWNER_OBJECT,
            "mirror_states": list(vocabulary.MIRROR_STATES),
            "mirror_state_notes": dict(vocabulary.MIRROR_STATE_NOTES),
            "mirror_outcomes": list(vocabulary.MIRROR_OUTCOMES),
            "reconcile_rule": vocabulary.RECONCILE_RULE,
            "default_poll_interval_seconds": vocabulary.DEFAULT_POLL_INTERVAL_SECONDS,
            "min_poll_interval_seconds": vocabulary.MIN_POLL_INTERVAL_SECONDS,
            "connector_methods": ["upload", "attach"],
            "digest_algorithm": asset_rules.DIGEST_ALGORITHM,
            "file_id_key": vocabulary.ASSET_FILE_ID_KEY,
        }

    def vendors(self) -> dict[str, Any]:
        """Each vendor's file capability, with the gap named where there is one."""
        reports = connectors.vendors()
        return {
            "count": len(reports),
            "vendors": reports,
            "sourced": sum(1 for entry in reports if entry["status"] == "sourced"),
            "unsourced": sum(1 for entry in reports if entry["status"] == "unsourced"),
            "connector_methods": ["upload", "attach"],
        }

    # ----------------------------------------------------------------- #
    # Connections
    # ----------------------------------------------------------------- #

    def save_connection(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Create or update the connection a room pushes its documents through.

        The connection names the vendor, the CRM record that owns the attachment,
        the attach mode, and an optional folder id. A folder is optional because
        the vendor says so: "While a specific folder ID is not required at upload,
        it's recommended to upload files into a folder and not the root directory."
        The row records ``folder_recommendation_followed`` either way, so a page
        can say what the vendor recommends without refusing the tenant.

        A second connection for the same room, system and owning record is refused
        with a 409. Two connections pushing the same document into the same record
        is the duplicate-file problem the idempotency rule exists to prevent.
        """
        wanted = dict(payload)
        connection_id = str(wanted.get("id") or "").strip()
        if connection_id:
            # Merge onto the stored payload *before* validating, so a caller that
            # changes one field does not have to resend the whole connection. A
            # field this build does not know survives the patch untouched, which is
            # the schema-flexibility rule rather than a convenience.
            current = self._row(vocabulary.CONNECTIONS, connection_id)
            if current is None:
                raise UnknownConnection(f"no connection carries id {connection_id!r}")
            wanted = dict(current["data"]) | wanted

        system = system_for(str(wanted.get("system") or wanted.get("vendor") or ""))
        attach_mode = attach_mode_for(str(wanted.get("attach_mode") or ""))
        owner_object = owner_object_for(str(wanted.get("owner_object") or ""))
        property_name = str(wanted.get("file_property") or "").strip()

        if attach_mode == vocabulary.ATTACH_FILE_PROPERTY and not property_name:
            raise NoOwnerRecord(
                "a file_property connection must name the property to write the file id into. "
                "The vendor documents that a file-type property 'Stores a file ID', but a tenant "
                "that has not created one has nothing to write into."
            )

        record_id = str(wanted.get("crm_record_id") or wanted.get("record_id") or "").strip()
        for existing in self.connections(room_id):
            if (
                existing["system"] == system
                and existing["owner_object"] == owner_object
                and existing["crm_record_id"] == record_id
                and existing["id"] != connection_id
            ):
                raise DuplicateConnection(
                    f"connection {existing['id']} already pushes {system} documents to "
                    f"{owner_object} {record_id or '(no record)'} for this room"
                )

        body = dict(wanted)
        body["system"] = system
        body["attach_mode"] = attach_mode
        body["owner_object"] = owner_object
        body["file_property"] = property_name
        body["crm_record_id"] = record_id
        body["folder_id"] = str(wanted.get("folder_id") or "").strip()
        body["folder_recommendation_followed"] = bool(body["folder_id"])
        body["mirror_all"] = bool(wanted.get("mirror_all", False))
        body["poll_interval_seconds"] = self._poll_interval(wanted.get("poll_interval_seconds"))
        body["last_reconciled_at"] = str(wanted.get("last_reconciled_at") or "")
        body["simulated_connector"] = bool(body.get("simulated_connector", True))

        connection_id = str(body.get("id") or "").strip()
        if connection_id:
            row = self.store.update(connection_id, body, actor=actor, source=source)
        else:
            row = self.store.create(
                vocabulary.CONNECTIONS, body, actor=actor, source=source, room_id=room_id
            )
        return self.read_connection(row["id"], room_id=room_id)

    def read_connection(self, connection_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One connection, with the assets and mirrors that hang off it."""
        row = self._row(vocabulary.CONNECTIONS, connection_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownConnection(f"no connection carries id {connection_id!r}")
        payload = _as_data(row) or {}
        assets = [
            asset_rules.summarise(entry, payload)
            for entry in self.assets_for_connection(connection_id)
        ]
        return {
            **payload,
            "room_id": row["room_id"],
            "asset_count": len(assets),
            "assets": assets,
            "connector_interface": ["upload", "attach"],
        }

    def connections(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """Every connection, optionally for one room."""
        rows = self.store.list(
            vocabulary.CONNECTIONS, room_id=room_id, limit=200, order_by="created_at"
        )
        return [_as_data(row) or {} for row in rows]

    def delete_connection(
        self,
        connection_id: str,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Soft-delete a connection, and say what its assets become.

        A soft delete, so the cancellation is audited and a restore is possible.
        The assets are deliberately left alone: an asset holding a
        ``crm_file_id`` is a fact about the CRM, and deleting it would make the
        room forget that a file exists which a buyer may already have seen.
        """
        row = self._row(vocabulary.CONNECTIONS, connection_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownConnection(f"no connection carries id {connection_id!r}")
        removed = self.store.delete(connection_id, actor=actor, source=source)
        return {
            **removed,
            "assets_kept": len(self.assets_for_connection(connection_id)),
            "note": (
                "the connection is gone; its assets keep their crm_file_id, because that id is a "
                "fact about the CRM rather than about the connection"
            ),
        }

    # ----------------------------------------------------------------- #
    # Assets
    # ----------------------------------------------------------------- #

    def register_asset(
        self,
        payload: Mapping[str, Any],
        *,
        connection_id: str | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        mirror: bool = False,
    ) -> dict[str, Any]:
        """Register a room asset, and optionally mirror it now.

        The user flow's step one, and optionally its step three. ``mirror`` is
        False by default: registering a document is not the same decision as
        publishing it to the CRM, and a caller that wants both asks for both.
        """
        connection = self._connection_payload(connection_id, room_id)
        body = asset_rules.normalise_asset(payload)
        body["connection_id"] = str(connection.get("id") or "")
        body.setdefault(vocabulary.ASSET_SOURCE_ID_KEY, str(payload.get("source_id") or ""))
        if "push_to_crm" not in body:
            body["push_to_crm"] = bool(connection.get("mirror_all", False))

        row = self.store.create(
            vocabulary.ASSETS, body, actor=actor, source=source, room_id=room_id
        )
        if mirror:
            # The mirror's own answer carries the asset state *after* the mirror.
            # Returning the summary computed here would report ``pending`` beside a
            # result that says ``mirrored``, which is a response that contradicts
            # itself in the same object.
            result = self.mirror_asset(
                row["id"],
                room_id=room_id,
                actor=actor,
                source=source,
            )
            return {"asset": result["asset"], "mirror": result}
        return {"asset": asset_rules.summarise(_as_data(row) or {}, connection)}

    def read_asset(self, asset_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """One asset, with its chip state and its mirror history."""
        row = self._row(vocabulary.ASSETS, asset_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownAsset(f"no asset carries id {asset_id!r}")
        payload = _as_data(row) or {}
        connection = self._connection_payload(payload.get("connection_id"), room_id)
        return {
            **asset_rules.summarise(payload, connection),
            "mirrors": [self.read_mirror(m["id"]) for m in self.mirrors_for_asset(asset_id)],
        }

    def list_assets(
        self,
        *,
        room_id: str | None = None,
        connection_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's assets, with their chips.

        ``state`` filters on the derived chip, which is a read of a value no
        column holds, so the filter is applied here rather than through the
        dynamic index. The indexed filters - ``room_id``, ``connection_id`` - are
        pushed down.
        """
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = str(connection_id)
        rows = (
            self.store.find(vocabulary.ASSETS, where, limit=1000)
            if where
            else self.store.list(vocabulary.ASSETS, room_id=room_id, limit=1000)
        )
        listed: list[dict[str, Any]] = []
        for row in rows:
            if room_id is not None and row["room_id"] != room_id:
                continue
            payload = _as_data(row) or {}
            connection = self._connection_payload(payload.get("connection_id"), room_id)
            entry = asset_rules.summarise(payload, connection)
            if state and entry["state"] != state:
                continue
            listed.append(entry)
        listed.sort(key=lambda entry: (entry["filename"], entry["id"]))
        return listed[: max(1, min(int(limit), 1000))]

    def assets_for_connection(self, connection_id: str) -> list[dict[str, Any]]:
        """Every asset on one connection, as raw payloads."""
        return [
            _as_data(row) or {}
            for row in self.store.find(
                vocabulary.ASSETS, {"connection_id": str(connection_id)}, limit=1000
            )
        ]

    def set_push(
        self,
        asset_id: str,
        push_to_crm: bool,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """The researched toggle: "Rep toggles **Push to CRM** on the asset".

        Switching it off does not withdraw anything from the CRM. The file the
        room already uploaded stays where it is, because the vendor has no delete
        in the API this workflow uses, and a row that claimed otherwise would be
        lying about the vendor.
        """
        row = self._row(vocabulary.ASSETS, asset_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownAsset(f"no asset carries id {asset_id!r}")
        updated = self.store.update(
            asset_id,
            {"push_to_crm": bool(push_to_crm)},
            actor=actor,
            source=source,
        )
        payload = _as_data(updated) or {}
        connection = self._connection_payload(payload.get("connection_id"), room_id)
        return {
            **asset_rules.summarise(payload, connection),
            "note": (
                "the file already in the CRM stays there; this toggle only decides whether the "
                "room pushes the next change"
            ),
        }

    def replace_content(
        self,
        asset_id: str,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        mirror: bool = False,
    ) -> dict[str, Any]:
        """Replace an asset's bytes, which is how a revision arrives.

        The digest moves, so the chip becomes ``stale`` and the next mirror creates
        a new CRM file. The old ``crm_file_id`` is kept on the row until that
        mirror runs, so a reader can see which file the CRM still holds.

        With ``mirror`` set, the returned asset is the one *after* the mirror, for
        the same reason :meth:`register_asset` does it that way: a response that
        says ``stale`` beside a mirror that says ``mirrored`` contradicts itself.
        """
        """Replace an asset's bytes, which is how a revision arrives.

        The digest moves, so the chip becomes ``stale`` and the next mirror creates
        a new CRM file. The old ``crm_file_id`` is kept on the row until that
        mirror runs, so a reader can see which file the CRM still holds.
        """
        row = self._row(vocabulary.ASSETS, asset_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownAsset(f"no asset carries id {asset_id!r}")
        incoming = asset_rules.normalise_asset(payload)
        current = dict(row["data"])
        # Keep the sync keys. normalise_asset sets the digest, and the digest that
        # matters for the mirror is the mirrored one, which this workflow owns.
        for key in (
            vocabulary.ASSET_FILE_ID_KEY,
            vocabulary.ASSET_MIRRORED_DIGEST_KEY,
            "connection_id",
            vocabulary.ASSET_SOURCE_ID_KEY,
            "push_to_crm",
        ):
            if key in current and key not in incoming:
                incoming[key] = current[key]
        updated = self.store.update(asset_id, incoming, actor=actor, source=source)
        stored = _as_data(updated) or {}
        connection = self._connection_payload(stored.get("connection_id"), room_id)
        result = {
            "asset": asset_rules.summarise(stored, connection),
            "was_a_revision": asset_rules.is_revision(stored),
        }
        if mirror:
            result["mirror"] = self.mirror_asset(
                asset_id,
                connection_id=stored.get("connection_id"),
                room_id=room_id,
                actor=actor,
                source=source,
            )
            result["asset"] = result["mirror"]["asset"]
        return result

    # ----------------------------------------------------------------- #
    # The mirror: the researched steps three and four
    # ----------------------------------------------------------------- #

    def mirror_asset(
        self,
        asset_id: str,
        *,
        connection_id: str | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        force: bool = False,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Upload one asset and write the returned file id onto the owning record.

        The whole researched flow in order: check, upload, attach, store. The
        order of the checks matters as much as the calls:

        * a switch that is off is ``withheld`` and nothing is sent;
        * unchanged bytes are ``skipped_unchanged`` and the vendor is not asked,
          which is the researched "re-uploads are skipped";
        * a connection with no owning record is ``no_owner_record``, which is the
          dependency the issue names, refused rather than invented.

        The vendor calls happen before the transaction opens. See this module's
        docstring for why.
        """
        row = self._row(vocabulary.ASSETS, asset_id)
        if row is None or (room_id is not None and row["room_id"] != room_id):
            raise UnknownAsset(f"no asset carries id {asset_id!r}")

        payload = _as_data(row) or {}
        connection = self._connection_payload(
            connection_id or payload.get("connection_id"), room_id
        )
        stamp = now or _utcnow()

        # Whether this was a revision is answered from the row *before* the
        # patch, because the patch moves the mirrored digest onto the current one
        # and a revision measured after it would always read as a first mirror.
        was_revision = asset_rules.is_revision(payload)

        outcome, error_code, error_detail, request, uploaded, attachment = self._attempt(
            payload, connection, force=force
        )

        patch: dict[str, Any] = {"last_outcome": outcome, "last_mirrored_at": stamp}
        # The store's update is a merge patch, so an empty string clears the field
        # rather than leaving the previous refusal on a row that has since worked.
        patch["last_error"] = error_detail if error_code else ""
        if uploaded is not None:
            patch[vocabulary.ASSET_FILE_ID_KEY] = uploaded.file_id
            patch[vocabulary.ASSET_MIRRORED_DIGEST_KEY] = payload.get(
                vocabulary.ASSET_DIGEST_KEY, ""
            )
            patch["last_error"] = ""

        mirror_payload = {
            "connection_id": str(connection.get("id") or ""),
            "asset_id": str(asset_id),
            "room_id": row["room_id"] or "",
            "system": str(connection.get("system") or ""),
            "outcome": outcome,
            "is_revision": was_revision,
            "error_code": error_code,
            "error_detail": error_detail,
            "request": request,
            "uploaded": uploaded.to_dict() if uploaded is not None else {},
            "attachment": attachment.to_dict() if attachment is not None else {},
            "folder_id": str(connection.get("folder_id") or ""),
            "folder_recommendation_followed": bool(connection.get("folder_id")),
            "simulated": bool(uploaded.simulated if uploaded is not None else False),
            "connector": str(connection.get("system") or ""),
            "at": stamp,
        }

        # One transaction, two rows. Either the mirror row and the stored file id
        # both land, or neither does.
        with self.store.db.transaction(
            actor=actor or connection.get("id"), source=source
        ) as writer:
            writer.update(asset_id, patch)
            mirror_row = writer.create(
                vocabulary.MIRRORS, mirror_payload, room_id=row["room_id"] or None
            )

        fresh = self._row(vocabulary.ASSETS, asset_id) or {}
        stored = _as_data(fresh) or {}
        return {
            "outcome": outcome,
            "error_code": error_code,
            "error_detail": error_detail,
            "mirror_id": mirror_row["id"],
            "request": request,
            "uploaded": uploaded.to_dict() if uploaded is not None else {},
            "attachment": attachment.to_dict() if attachment is not None else {},
            "simulated": mirror_payload["simulated"],
            "asset": asset_rules.summarise(stored, connection),
        }

    def _attempt(
        self,
        payload: Mapping[str, Any],
        connection: Mapping[str, Any],
        *,
        force: bool,
    ) -> tuple[str, str, str, dict[str, Any], UploadedFile | None, Attachment | None]:
        """The decision half of a mirror: what to send, if anything.

        Returns the outcome, an error code and detail when the outcome is a
        refusal, the request that would go over the wire, and the vendor's two
        answers.

        Which refusals are written to the row and which are raised is the point of
        this split. A refusal this workflow itself owns is *recorded*: a switch
        that is off, unchanged bytes, a connection with no owning record, and a
        system whose file API the research could not source. The researched
        product surface is a "Push to CRM" chip per file, so a tenant on an
        unsourced vendor must see a failed chip naming the gap rather than a stack
        trace and a document with no trace at all.

        A refusal the *vendor* raised is *raised*: an upload the CRM declined or a
        folder it no longer accepts is a 502, because the caller's request was
        correct and the state that has to change is not in the room.
        """
        if not asset_rules.push_enabled(payload, connection):
            return (
                vocabulary.OUTCOME_WITHHELD,
                "",
                "push_to_crm is off for this asset",
                {},
                None,
                None,
            )

        if not force and not asset_rules.needs_mirror(payload, connection):
            return (
                vocabulary.OUTCOME_SKIPPED_UNCHANGED,
                "",
                "the stored crm_file_id matches the current content digest, so nothing was sent",
                {},
                None,
                None,
            )

        record_id = str(connection.get("crm_record_id") or "")
        if not record_id:
            return (
                vocabulary.OUTCOME_NO_OWNER_RECORD,
                "no_owner_record",
                (
                    "this connection names no CRM record to own the attachment, so the returned "
                    "file id would have nowhere to be written. WF-042 provisions the room's deal, "
                    "account and contact records; a connection without one cannot mirror."
                ),
                {},
                None,
                None,
            )

        # Resolving the connector can refuse: a system whose file API the research
        # could not source has no connector, and the refusal names the gap.
        try:
            connector = connectors.connector_for(
                self.store, str(connection.get("system") or ""), rng=self.rng
            )
        except UnsupportedSystem as exc:
            return vocabulary.OUTCOME_FAILED, exc.code, exc.detail, {}, None, None
        content = asset_rules.decode_content(payload.get("content_base64"))
        upload = connectors.UploadRequest(
            filename=str(payload.get("filename") or ""),
            mime_type=str(payload.get("mime_type") or ""),
            content=content,
            folder_id=str(connection.get("folder_id") or ""),
        )
        request = upload.request()
        request["connector"] = connectors.describe_connector(connector)

        uploaded = connector.upload(
            content,
            filename=upload.filename,
            mime_type=upload.mime_type,
            folder_id=upload.folder_id,
        )
        attachment = connector.attach(
            uploaded.file_id,
            record_id,
            mode=str(connection.get("attach_mode") or vocabulary.DEFAULT_ATTACH_MODE),
            property_name=str(connection.get("file_property") or ""),
        )
        return vocabulary.OUTCOME_MIRRORED, "", "", request, uploaded, attachment

    # ----------------------------------------------------------------- #
    # Reconciliation: the researched rule
    # ----------------------------------------------------------------- #

    def reconcile(
        self,
        room_id: str,
        *,
        actor: str | None = None,
        source: str | None = None,
        now: str | None = None,
        interval_seconds: int | None = None,
        force: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find room assets with no ``crm_file_id`` and push them.

        The quoted rule, and the mirror's answer to it. Assets that already hold a
        file id whose digest has moved are included too, because a revision the
        event path missed is exactly what a reconciliation is for; see the
        ``reconcile-covers-stale-assets`` inference.

        The floor is a floor, not a timer. Nothing here starts a thread. A
        scheduler that calls this route more often than
        :data:`~dsr.crm_file_sync.vocabulary.DEFAULT_POLL_INTERVAL_SECONDS` is told
        ``not_due`` and no vendor is asked for anything. ``force`` skips the floor,
        because a room that restarted has not been respecting anybody's interval.
        """
        stamp = now or _utcnow()
        connections = self.connections(room_id)
        due = [entry for entry in connections if self._is_due(entry, stamp, interval_seconds)]
        # The floor this run actually applied, not a constant. A scheduler that
        # passed an override needs to read back the number it got, and a run that
        # did not needs to read the per-connection floors it used.
        applied = (
            self._poll_interval(interval_seconds)
            if interval_seconds is not None
            else vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
        )
        floors = {
            str(entry["id"]): self._poll_interval(
                interval_seconds
                if interval_seconds is not None
                else entry.get("poll_interval_seconds")
            )
            for entry in connections
        }
        if not due and not force:
            return {
                "room_id": room_id,
                "outcome": "not_due",
                "note": (
                    "no connection was due. The reconciliation floor is a floor a scheduler "
                    "respects, not a timer this package runs."
                ),
                "interval_seconds": applied,
                "connection_intervals": floors,
                "mirrored": 0,
                "results": [],
                "candidates": 0,
            }

        candidates = self._reconcile_candidates(room_id, limit)
        results: list[dict[str, Any]] = []
        for asset_id in candidates:
            result = self.mirror_asset(
                asset_id,
                room_id=room_id,
                actor=actor,
                source=source,
                now=stamp,
            )
            results.append(
                {
                    "asset_id": asset_id,
                    "outcome": result["outcome"],
                    "error_code": result["error_code"],
                    "crm_file_id": result["uploaded"].get("file_id", ""),
                }
            )

        for entry in due:
            self.store.update(
                entry["id"],
                {"last_reconciled_at": stamp},
                actor=actor,
                source=source,
            )

        return {
            "room_id": room_id,
            "outcome": "reconciled",
            "rule": vocabulary.RECONCILE_RULE,
            "mirrored": sum(1 for r in results if r["outcome"] == vocabulary.OUTCOME_MIRRORED),
            "candidates": len(candidates),
            "results": results,
            "interval_seconds": applied,
            "connection_intervals": floors,
            "at": stamp,
        }

    def _reconcile_candidates(self, room_id: str, limit: int) -> list[str]:
        """The assets this pass would push, in a stable order.

        Two rules, both researched: an asset with no ``crm_file_id`` is a
        candidate, and an asset whose digest moved since its last mirror is a
        revision waiting. An asset whose push is off is never a candidate, because
        the rep switched it off.

        This reads the **raw** rows rather than the summaries :meth:`list_assets`
        returns. The summary renames the two digest fields for the page's benefit,
        so a candidate test written against it would compare ``digest`` with
        ``mirrored_content_digest``, find nothing equal, and quietly never report a
        revision the event path missed. That is the whole job of this method.
        """
        ordered: list[str] = []
        rows = self.store.list(vocabulary.ASSETS, room_id=room_id, limit=1000)
        for row in rows:
            payload = _as_data(row) or {}
            connection = self._connection_payload(payload.get("connection_id"), room_id)
            if not asset_rules.push_enabled(payload, connection):
                continue
            if asset_rules.missing_file_id(payload) or asset_rules.is_revision(payload):
                ordered.append(str(row["id"]))
        return ordered[: max(1, min(int(limit), 1000))]

    def _is_due(self, connection: Mapping[str, Any], stamp: str, override: int | None) -> bool:
        """Whether one connection has passed the reconciliation floor."""
        floor = self._poll_interval(
            override if override is not None else connection.get("poll_interval_seconds")
        )
        last = str(connection.get("last_reconciled_at") or "")
        if not last:
            return True
        try:
            elapsed = (datetime.fromisoformat(stamp) - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            return True
        return elapsed >= floor

    @staticmethod
    def _poll_interval(raw: Any) -> int:
        """The floor at which a reconciliation run is allowed to ask again.

        Defaults to the same 300 seconds :mod:`dsr.crm_backfill.plan` derives for
        this product's other polled vendor loops, so one number governs them all.
        """
        if raw is None or raw == "":
            return vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            return vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
        if seconds <= 0:
            # A zero floor is a floor nobody set, and a negative one is not a time.
            # Both read as "unset" rather than being silently clamped to the
            # minimum, because a caller who wrote 0 meant "use your default".
            return vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
        return max(vocabulary.MIN_POLL_INTERVAL_SECONDS, seconds)

    # ----------------------------------------------------------------- #
    # Mirrors: the log
    # ----------------------------------------------------------------- #

    def read_mirror(self, mirror_id: str) -> dict[str, Any]:
        """One mirror attempt, with the request it made and the vendor's answers."""
        row = self._row(vocabulary.MIRRORS, mirror_id)
        if row is None:
            raise UnknownMirror(f"no mirror carries id {mirror_id!r}")
        return _as_data(row) or {}

    def mirrors_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        """Every mirror attempt for one asset, newest first."""
        rows = self.store.find(vocabulary.MIRRORS, {"asset_id": str(asset_id)}, limit=200)
        return [_as_data(row) or {} for row in rows]

    def list_mirrors(
        self,
        *,
        room_id: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's mirror log, newest first, filtered through the dynamic index."""
        where: dict[str, Any] = {}
        if outcome:
            where["outcome"] = str(outcome)
        rows = (
            self.store.find(vocabulary.MIRRORS, where, limit=1000)
            if where
            else self.store.list(vocabulary.MIRRORS, room_id=room_id, limit=1000)
        )
        listed = [
            _as_data(row) or {} for row in rows if room_id is None or row["room_id"] == room_id
        ]
        if outcome:
            listed = [entry for entry in listed if entry.get("outcome") == outcome]
        return listed[: max(1, min(int(limit), 1000))]

    # ----------------------------------------------------------------- #
    # Panels
    # ----------------------------------------------------------------- #

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for the room: assets by chip state, mirrors by outcome."""
        assets = self.list_assets(room_id=room_id, limit=1000)
        by_state = {state: 0 for state in vocabulary.MIRROR_STATES}
        for asset in assets:
            by_state[asset["state"]] = by_state.get(asset["state"], 0) + 1
        mirrors = self.list_mirrors(room_id=room_id, limit=1000)
        by_outcome = {outcome: 0 for outcome in vocabulary.MIRROR_OUTCOMES}
        for mirror in mirrors:
            by_outcome[str(mirror.get("outcome"))] = (
                by_outcome.get(str(mirror.get("outcome")), 0) + 1
            )
        return {
            "room_id": room_id,
            "connections": len(self.connections(room_id)),
            "assets": len(assets),
            "assets_by_state": by_state,
            "mirrors": len(mirrors),
            "mirrors_by_outcome": by_outcome,
            "reconcile_rule": vocabulary.RECONCILE_RULE,
            "interval_seconds": vocabulary.DEFAULT_POLL_INTERVAL_SECONDS,
            "simulated_mirrors": sum(1 for m in mirrors if m.get("simulated")),
        }

    def documents_panel(self, room_id: str) -> dict[str, Any]:
        """The Documents page: every asset with its "Push to CRM" chip.

        The researched product surface: "Room **Documents** page with a 'Push to
        CRM' status chip per file".
        """
        assets = self.list_assets(room_id=room_id, limit=1000)
        return {
            "room_id": room_id,
            "count": len(assets),
            "states": list(vocabulary.MIRROR_STATES),
            "state_notes": dict(vocabulary.MIRROR_STATE_NOTES),
            "assets": assets,
        }

    def crm_files_panel(self, room_id: str) -> dict[str, Any]:
        """The CRM Files panel: what the room has pushed, and where.

        The second researched product surface, "CRM **Files** panel", reached
        through the room rather than through the vendor, because the room holds the
        vendor-agnostic ``crm_file_id`` for each one.
        """
        entries: list[dict[str, Any]] = []
        for asset in self.list_assets(room_id=room_id, limit=1000):
            file_id = str(asset.get("crm_file_id") or "")
            if not file_id:
                continue
            connection = self._connection_payload(asset.get("connection_id"), room_id)
            entries.append(
                {
                    "crm_file_id": file_id,
                    "filename": asset["filename"],
                    "mime_type": asset["mime_type"],
                    "byte_size": asset["byte_size"],
                    "asset_id": asset["id"],
                    "system": str(connection.get("system") or ""),
                    "owner_object": str(connection.get("owner_object") or ""),
                    "crm_record_id": str(connection.get("crm_record_id") or ""),
                    "attach_mode": str(connection.get("attach_mode") or ""),
                    "folder_id": str(connection.get("folder_id") or ""),
                    "simulated": bool(connection.get("simulated_connector", True)),
                }
            )
        entries.sort(key=lambda entry: (entry["filename"], entry["crm_file_id"]))
        return {
            "room_id": room_id,
            "count": len(entries),
            "attach_modes": list(vocabulary.ATTACH_MODES),
            "files": entries,
        }

    def remote_files(self, folder_id: str | None = None) -> dict[str, Any]:
        """The simulated vendor's own file storage, for a reviewer to read.

        Served by the same connector the mirror uses, so what a reviewer sees here
        is what the connector actually holds rather than a second copy of it built
        for display.
        """
        connector = connectors.SimulatedConnector(self.store)
        rows = [
            {k: v for k, v in row.items() if k != "content_base64"}
            for row in connector.file_rows(folder_id)
        ]
        return {
            "count": len(rows),
            "simulated": True,
            "folder_recommendation": vocabulary.FOLDER_RECOMMENDATION,
            "folder_caveat": vocabulary.FOLDER_CAVEAT,
            "files": rows,
        }

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        from dsr.crm_file_sync.inferences import INFERENCES

        return {"count": len(INFERENCES), "inferences": list(INFERENCES)}

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _row(self, collection: str, record_id: str) -> dict[str, Any] | None:
        return self.store.get(str(record_id))

    def _connection_payload(self, connection_id: Any, room_id: str | None) -> dict[str, Any]:
        """One connection's payload, or an empty one.

        An empty one rather than a refusal, because the asset routes accept a
        payload that names no connection: registering an asset before a connection
        exists is the ordinary order of operations, and the refusal belongs on the
        mirror, where it becomes a named outcome instead of a lost document.
        """
        if not connection_id:
            return {}
        row = self._row(vocabulary.CONNECTIONS, str(connection_id))
        if row is None:
            return {}
        if room_id is not None and row["room_id"] not in (None, room_id):
            return {}
        return _as_data(row) or {}
