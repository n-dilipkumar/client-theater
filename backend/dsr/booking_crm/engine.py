"""The engine: execute a flow's nodes against a booking, in declared order.

This is where the researched rules stop being text and start being behaviour.
:meth:`BookingWriteback.writeback` walks a validated node list and produces, for
every node, a :class:`NodeResult` - always one, including for a node that never
ran. The run record is written whatever happens, because the failure *is* the
record:

    [sourced] "If the Event is successfully created, we will show when it
    happened. If the Event failed to be created, we will also show when it
    happened, alongside the detailed error."

So a run produces two things, and they are deliberately different shapes:

* a **run** in :data:`RUN_COLLECTION` - the whole flow, every node's outcome, the
  record it matched or created, the related object it chose *and why*, and the
  single actionable error a rep is shown;
* one **Events History** row per Event or Engagement the run attempted, in
  :data:`HISTORY_COLLECTION` - including the child Events, and including the ones
  that failed, because "[sourced] Admin later retries any failed CRM Event from
  Meetings Activity → Events History" needs a row to retry.

The ordering rule is enforced at declaration, not here. ``writeback`` refuses a
node list it has not seen validated, so a flow cannot reach the engine out of
order even if a caller builds one by hand.

Nothing falls through
---------------------
The rule that matters most here is the one a run cannot skip: if the create node
matched nothing and no create branch produced a record, then **every** node after
it is skipped with a named reason rather than quietly succeeding. A flow whose
fourth node has no record to write to is a bug someone hits in production, and
this is the module that has to make it visible.

Every method that writes takes ``source`` as a **required keyword**. That is not
a style choice: this programme has shipped a feature whose audit log kept naming
a route the app had stopped serving, and a required keyword cannot be forgotten.
The route passes ``f"{router.prefix}/..."``; nothing in this module contains a URL.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.booking_crm.errors import InvalidConfig, NotConfigured, NotFound
from dsr.booking_crm.flow import (
    ANCHOR_NODES,
    EVENT_NODE,
    EVENT_RECORD_TYPE,
    FIELD_NODE,
    MATCH_ORDER,
    RELATED_REQUIRES_CONTACT_QUOTE,
    SKIP_MEETING_TYPE_SYNC_OFF,
    SKIP_NO_RECORD,
    SKIP_RETRY_NOT_FAILED,
    describe_plan,
    normalise_nodes,
    normalise_path,
    plan_order,
    related_requires_contact_message,
    sync_enabled,
    sync_toggle_message,
    validate_nodes,
)
from dsr.booking_crm.local_crm import (
    CRM_RECORD_COLLECTION,
    TYPE_ACCOUNT,
    TYPE_CAMPAIGN,
    TYPE_CAMPAIGN_MEMBER,
    TYPE_CONTACT,
    TYPE_LEAD,
    CrmRefused,
    LocalCrm,
    parse_date,
)
from dsr.booking_crm.vocabulary import (
    ACTIVITY_ASSIGNED_TO_ASSIGNEE,
    ACTIVITY_ASSIGNED_TO_BOOKER,
    ACTIVITY_ASSIGNED_TO_HOST,
    CAMPAIGN_MEMBER_STATUS,
    CREATE_ALWAYS_LEAD,
    CREATE_LEAD,
    CREATE_NONE,
    DELETE_EVENT_ON_FAILURE,
    DELETE_EVENT_ON_RETRY,
    HISTORY_RETRY_QUOTE,
    ORDERING_QUOTE,
    OWNER_ASSIGNEE,
    OWNER_BOOKER,
    OWNER_FALLBACK_ATTRIBUTE_RULES,
    OWNER_HOST,
    RELATED_SELECTION_QUOTE,
    SALESFORCE,
    SYNC_TOGGLE_ORG_WIDE_QUOTE,
    UPDATE_MATCHED_LEAD,
    VENDORS,
)
from dsr.db.audited import new_id, utcnow
from dsr.store import RecordStore

#: The five collections this workflow owns, plus the CRM's own. Prefixed
#: ``crm_booking_`` so no other feature can claim one, and named so
#: ``/api/collections`` reads as one feature's rows rather than a shared pile.
CONNECTOR_COLLECTION = "crm_booking_connector"
MEETING_TYPE_COLLECTION = "crm_booking_meeting_type"
FLOW_COLLECTION = "crm_booking_flow"
RUN_COLLECTION = "crm_booking_run"
HISTORY_COLLECTION = "crm_booking_history"

COLLECTIONS: tuple[str, ...] = (
    CONNECTOR_COLLECTION,
    MEETING_TYPE_COLLECTION,
    FLOW_COLLECTION,
    RUN_COLLECTION,
    HISTORY_COLLECTION,
    CRM_RECORD_COLLECTION,
)

#: The room collection is the store's own, not this feature's.
ROOM_COLLECTION = "room"

# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #

#: What one node did. A run that produced a step per node is a run whose shape a
#: client can rely on; a missing step means the engine never got that far, which
#: it never does without raising.
OUTCOME_APPLIED = "applied"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

OUTCOMES: tuple[str, ...] = (OUTCOME_APPLIED, OUTCOME_SKIPPED, OUTCOME_FAILED)

#: The named reasons a node can skip or fail. Every one is a *state*, never a
#: silent drop, and each carries the rule that caused it in ``message``.
REASON_MATCHED = "matched_by_email"
REASON_CREATED = "create_branch_applied"
REASON_UPDATED = "update_branch_applied"
REASON_ALWAYS_LEAD = "always_create_lead"
REASON_NO_CREATE = "nothing_matched_and_no_create_branch"
REASON_UPDATE_SKIPPED_LEAD_ONLY = "update_matched_lead_only"
REASON_DEFAULT_RELATION = "default_relation_to_the_matched_record"
REASON_CHILD_EVENT = "child_event_per_additional_guest"
REASON_NO_GUESTS = "no_additional_guests"
REASON_CAMPAIGN_MEMBER = "campaign_member_upsert"
REASON_OWNERSHIP = "owner_reassigned"
REASON_OWNER_FALLBACK = "owner_resolved_by_fallback_mode"
REASON_OWNER_UNCHANGED = "owner_unchanged"
REASON_OWNER_NONE = "no_owner_resolved"
REASON_OWNER_SKIPPED = "skip_contact_owner"
REASON_NO_CANDIDATE = "no_candidate_matched_the_selection_rule"
REASON_FIELDS_WRITTEN = "selected_fields_updated"
REASON_DELETED = "deleted_by_delete_event_setting"
REASON_MEETING_TYPE_SYNC_OFF = SKIP_MEETING_TYPE_SYNC_OFF
REASON_NO_RECORD = SKIP_NO_RECORD
REASON_RETRY_NOT_FAILED = SKIP_RETRY_NOT_FAILED

NO_RECORD_MESSAGE = (
    "this flow matched nothing and created nothing, so every node after the "
    "create node has no record to write to. Each one is skipped with this reason "
    "rather than reported as a success."
)


# --------------------------------------------------------------------------- #
# The booking
# --------------------------------------------------------------------------- #


def normalise_booking(payload: Mapping[str, Any], *, default_path: str) -> dict[str, Any]:
    """Read a booking, and say what is missing rather than inventing it.

    The research's data flow starts "meeting + guest form Data Fields", so a
    booking is: an identity to match on (the booker's email), the meeting itself,
    the additional guests a child Event is made per, the three identities the
    ownership and Activity Assigned To nodes choose between, and the Data Fields
    a field map can read.

    An email is required, because "[sourced] matched by email" is the only match
    key there is - a booking with no email cannot be matched, and pretending
    otherwise would produce a Lead with no way to find it again.
    """
    if not isinstance(payload, Mapping):
        raise InvalidConfig("a booking must be an object")
    booker = _person(payload.get("booker") or payload.get("guest"), "booker")
    if not booker.get("email"):
        raise InvalidConfig(
            "the booking has no booker email, and [sourced] the record is 'matched by "
            "email' - there is no other key to match on"
        )
    guests: list[dict[str, Any]] = []
    raw_guests = payload.get("guests") or payload.get("additional_guests") or []
    if not isinstance(raw_guests, Sequence) or isinstance(raw_guests, (str, bytes)):
        raise InvalidConfig("guests must be a list of people")
    for index, guest in enumerate(raw_guests):
        person = _person(guest, f"guests[{index}]")
        if not person.get("email"):
            raise InvalidConfig(
                f"guests[{index}] has no email, and a child Event is made 'per "
                "additional guest' - a guest with no address cannot be invited"
            )
        guests.append(person)
    data_fields = payload.get("data_fields")
    if data_fields is not None and not isinstance(data_fields, Mapping):
        raise InvalidConfig("data_fields must be an object of names to values")
    account = payload.get("account")
    if account is not None and not isinstance(account, Mapping):
        raise InvalidConfig("account must be an object")
    return {
        "booking_ref": str(payload.get("booking_ref") or payload.get("meeting_id") or ""),
        "subject": str(payload.get("subject") or payload.get("title") or ""),
        "starts_at": payload.get("starts_at") or payload.get("start"),
        "ends_at": payload.get("ends_at") or payload.get("end"),
        "path": normalise_path(payload.get("path") or default_path),
        "booker": booker,
        "guests": guests,
        "host": _person(payload.get("host"), "host"),
        "assignee": _person(payload.get("assignee"), "assignee"),
        "data_fields": dict(data_fields or {}),
        "account": dict(account or {}),
    }


def _person(value: Any, label: str) -> dict[str, Any]:
    """One identity: an email, optionally a name. Missing is ``{}``, not an error."""
    if value is None:
        return {}
    if isinstance(value, str):
        return {"email": value.strip()} if value.strip() else {}
    if not isinstance(value, Mapping):
        raise InvalidConfig(f"{label} must be an object with an email")
    return {
        "email": str(value.get("email") or "").strip(),
        "name": str(value.get("name") or "").strip(),
    }


def identity_for(booking: Mapping[str, Any], which: str) -> dict[str, Any]:
    """The identity an ownership or Activity Assigned To setting names.

    [sourced] the three the research enumerates are Host, Booker and Assignee, and
    the same three appear in the owner sentence ("record Owner reassigned to the
    assignee", "ownership can be transferred to whoever took the meeting"). So one
    lookup serves both, and a setting naming an identity the booking does not
    carry produces an empty result rather than a write to nobody.
    """
    if which in (OWNER_ASSIGNEE, ACTIVITY_ASSIGNED_TO_ASSIGNEE):
        key = "assignee"
    elif which in (OWNER_HOST, ACTIVITY_ASSIGNED_TO_HOST):
        key = "host"
    elif which in (OWNER_BOOKER, ACTIVITY_ASSIGNED_TO_BOOKER):
        key = "booker"
    else:
        return {}
    return dict(booking.get(key) or {})


# --------------------------------------------------------------------------- #
# One node's result
# --------------------------------------------------------------------------- #


class NodeResult:
    """What one node did, and why.

    Always produced, including for a node that never ran, so a run's shape does
    not depend on how far it got. ``reason`` and ``message`` are separate because
    they answer different questions: ``reason`` is a code a client can branch on,
    ``message`` is the sentence a rep reads.
    """

    __slots__ = (
        "node",
        "position",
        "outcome",
        "reason",
        "message",
        "crm_id",
        "resolved",
        "history_ids",
    )

    def __init__(
        self,
        node: str,
        position: int,
        outcome: str,
        *,
        reason: str = "",
        message: str = "",
        crm_id: str = "",
        resolved: Mapping[str, Any] | None = None,
        history_ids: Sequence[str] = (),
    ) -> None:
        self.node = node
        self.position = position
        self.outcome = outcome
        self.reason = reason
        self.message = message
        self.crm_id = crm_id
        self.resolved: dict[str, Any] = dict(resolved or {})
        self.history_ids = list(history_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "position": self.position,
            "outcome": self.outcome,
            "reason": self.reason,
            "message": self.message,
            "crm_id": self.crm_id,
            "resolved": self.resolved,
            "history_ids": list(self.history_ids),
        }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class BookingWriteback:
    """Runs a declared flow against a booking, and records what happened.

    Holds a :class:`~dsr.store.RecordStore` and nothing else. The HTTP surface is
    ``dsr.features.wf065_write_the_booking_back_into_the_crm``, which builds one of
    these per request - which also leaves the CRM a seam a test can override, so
    the suite can run the whole flow without a network.
    """

    def __init__(self, store: RecordStore, *, crm: LocalCrm | None = None) -> None:
        self.store = store
        self._crm = crm

    def crm(self, room_id: str) -> LocalCrm:
        """The CRM for this room.

        A fresh :class:`LocalCrm` per room when none was injected, so its fault
        table is per call and a test's injected fault does not leak into the next
        run through a shared instance.
        """
        if self._crm is not None:
            return self._crm
        return LocalCrm(self.store, room_id=room_id)

    # -- lookups ------------------------------------------------------------ #

    def require_room(self, room_id: str) -> dict[str, Any]:
        """The room, or a refusal naming the id that did not resolve.

        Every room-scoped route starts here, so a typo in a room id is a 404
        naming the room rather than an empty list that looks like a room with
        nothing in it.
        """
        record = self.store.get(str(room_id))
        if record is None or record["collection"] != ROOM_COLLECTION:
            raise NotFound("room", room_id)
        return record

    def _live(
        self, collection: str, record_id: str, room_id: str | None = None
    ) -> dict[str, Any] | None:
        record = self.store.get(str(record_id))
        if record is None or record["collection"] != collection:
            return None
        if room_id is not None and record["room_id"] not in (None, str(room_id)):
            return None
        return record

    def meeting_type(self, meeting_type_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._live(MEETING_TYPE_COLLECTION, meeting_type_id, room_id)
        if record is None:
            raise NotFound("meeting_type", meeting_type_id, room_id)
        return record

    def flow(self, flow_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        record = self._live(FLOW_COLLECTION, flow_id, room_id)
        if record is None:
            raise NotFound("flow", flow_id, room_id)
        return record

    def run(self, room_id: str, run_id: str) -> dict[str, Any]:
        record = self._live(RUN_COLLECTION, run_id, room_id)
        if record is None:
            raise NotFound("run", run_id, room_id)
        return record

    def history_row(self, room_id: str, history_id: str) -> dict[str, Any]:
        record = self._live(HISTORY_COLLECTION, history_id, room_id)
        if record is None:
            raise NotFound("history", history_id, room_id)
        return record

    # -- meeting types ------------------------------------------------------ #

    def create_meeting_type(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register a meeting type and its Sync Meeting Type toggle.

        The toggle is the one setting a run cannot override, because the research
        says it "is applied to all users in your org" - so it is stored here, on
        the meeting type, and nowhere else. It defaults to **off**: the research
        says the settings are admin-defined, so a meeting type nobody has opted in
        writes nothing, and the run says so.
        """
        self.require_room(room_id)
        data = _meeting_type_payload(payload)
        return self.store.create(
            MEETING_TYPE_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )

    def update_meeting_type(
        self,
        room_id: str,
        meeting_type_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self.meeting_type(meeting_type_id, room_id=room_id)
        patch = _meeting_type_payload(payload, partial=True)
        if not patch:
            return record
        return self.store.update(record["id"], patch, actor=actor, source=source)

    def delete_meeting_type(
        self, room_id: str, meeting_type_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.meeting_type(meeting_type_id, room_id=room_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    def list_meeting_types(self, room_id: str) -> list[dict[str, Any]]:
        self.require_room(room_id)
        return self.store.list(MEETING_TYPE_COLLECTION, room_id=str(room_id), limit=1000)

    # -- flows -------------------------------------------------------------- #

    def create_flow(
        self, room_id: str, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Declare a flow for one router path, and check it against the ordering rule.

        The check happens here, at declaration, so a flow that could never run is
        never stored. That is deliberate: a stored flow that cannot run is a row in
        the room's list that a rep clicks and gets nothing from, and the research's
        sentence is unambiguous enough to enforce while the admin is still editing.
        """
        self.require_room(room_id)
        data = _flow_payload(room_id, payload, self)
        return self.store.create(
            FLOW_COLLECTION, data, room_id=str(room_id), actor=actor, source=source
        )

    def update_flow(
        self,
        room_id: str,
        flow_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        record = self.flow(flow_id, room_id=room_id)
        patch = _flow_payload(room_id, payload, self, partial=True)
        if not patch:
            return record
        return self.store.update(record["id"], patch, actor=actor, source=source)

    def delete_flow(
        self, room_id: str, flow_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.flow(flow_id, room_id=room_id)
        return self.store.delete(record["id"], actor=actor, source=source)

    def list_flows(
        self, room_id: str, *, path: str | None = None, meeting_type_id: str | None = None
    ) -> list[dict[str, Any]]:
        """A room's flows, newest first, filterable by path and meeting type.

        Both filters are JSON paths in each row's own payload, resolved through
        the dynamic index, so a fourth router path needs no change here.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if path:
            where["path"] = normalise_path(path)
        if meeting_type_id:
            where["meeting_type_id"] = str(meeting_type_id)
        return self._scoped(FLOW_COLLECTION, room_id, where, limit=1000)

    def flow_for(
        self, room_id: str, *, path: str, meeting_type_id: str | None = None
    ) -> dict[str, Any]:
        """The flow that serves a path, or a refusal naming the path it looked for.

        This is the researched automation: "writes fire on the scheduled,
        not-scheduled and disqualified paths automatically". A path with no flow is
        a real state - a tenant that only wired up the scheduled path - and saying
        so is the difference between a rep learning it now and learning it from a
        booking that silently wrote nothing.
        """
        self.require_room(room_id)
        wanted = normalise_path(path)
        candidates = self.list_flows(room_id, path=wanted, meeting_type_id=meeting_type_id)
        if not candidates:
            detail = f" for meeting type {meeting_type_id}" if meeting_type_id else ""
            raise NotConfigured(
                f"no flow is declared for the {wanted!r} path{detail}. [sourced] writes "
                "fire on the scheduled, not-scheduled and disqualified paths "
                "automatically, so a path with no flow is a path with no write - declare "
                "one, or do not route that path"
            )
        # Newest first, because a deployment that adds a second flow for a path
        # means to use it, and the newest is the one it just added.
        return candidates[0]

    # -- connectors -------------------------------------------------------- #

    def create_connector(
        self, payload: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Register the global CRM connection.

        [sourced] "Global Salesforce connection required for Event details in
        Events History." So a connector is a *global* row, not a room-scoped one,
        and Events History wants one to show what became of an Event. It is not
        required to run: a room's own local CRM still works without it, and the
        history says the details are unavailable rather than refusing.
        """
        data = _connector_payload(payload)
        return self.store.create(CONNECTOR_COLLECTION, data, actor=actor, source=source)

    def list_connectors(self) -> list[dict[str, Any]]:
        return [row for row in self.store.list(CONNECTOR_COLLECTION, limit=1000)]

    def connector_summary(self, connector: Mapping[str, Any]) -> dict[str, Any]:
        """A connector with its token masked, because the token *is* the header.

        A read never returns the credential; it answers ``has_token`` and the last
        four characters, which is enough to tell two connectors apart and not
        enough to use one.
        """
        data = dict(connector.get("data") or connector)
        token = str(data.get("token") or "")
        data["has_token"] = bool(token)
        data["token_hint"] = f"…{token[-4:]}" if token else ""
        data.pop("token", None)
        for key in (
            "id",
            "collection",
            "room_id",
            "revision",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            data.pop(key, None)
        return data

    def events_history_available(self) -> dict[str, Any]:
        """Whether Events History can show Event *details*, and why not if it cannot.

        [sourced] "Global Salesforce connection required for Event details in
        Events History." A failure's timestamp and its detailed error are shown
        either way - "[sourced] If the Event failed to be created, we will also
        show when it happened, alongside the detailed error" - so the gate is on
        the *details* alone, and the history keeps working without a connection.
        """
        connectors = [
            row
            for row in self.store.list(CONNECTOR_COLLECTION, limit=1000)
            if str(row["data"].get("vendor")) == SALESFORCE
        ]
        return {
            "event_details_available": any(
                bool(row["data"].get("global", True)) for row in connectors
            ),
            "vendor": SALESFORCE,
            "note": (
                "[sourced] Global Salesforce connection required for Event details in "
                "Events History. Without one the history still shows when each Event was "
                "attempted and the detailed error on a failure."
            ),
        }

    # -- the writeback ------------------------------------------------------ #

    def writeback(
        self,
        room_id: str,
        flow_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        crm: LocalCrm | None = None,
    ) -> dict[str, Any]:
        """Run a declared flow against a booking, and write one run record.

        Always writes the run. A refusal *before* the first node - no room, no flow,
        a node list that was never validated - writes nothing, because nothing was
        attempted and there is nothing to audit.
        """
        room = self.require_room(room_id)
        flow = self.flow(flow_id, room_id=room_id)
        nodes = list(flow["data"].get("nodes") or [])
        validate_nodes(nodes)
        booking = normalise_booking(payload, default_path=str(flow["data"].get("path") or ""))
        if booking["path"] != flow["data"].get("path"):
            raise InvalidConfig(
                f"the booking took the {booking['path']!r} path but the flow is declared "
                f"for {flow['data'].get('path')!r}. [sourced] each router path carries its "
                "own nodes, so a flow belongs to one path and the two must agree"
            )
        meeting_type = self.meeting_type(
            str(flow["data"].get("meeting_type_id") or ""), room_id=room_id
        )
        if payload.get("sync_to_crm") is not None:
            raise InvalidConfig(
                "a run cannot carry its own sync_to_crm. [sourced] the toggle 'is applied "
                f'to all users in your org" - "{SYNC_TOGGLE_ORG_WIDE_QUOTE}" - so it belongs '
                "to the meeting type and a per-run value would make it per-user"
            )
        vendor = str(flow["data"].get("vendor") or SALESFORCE)
        return self._run(
            room=room,
            flow=flow,
            nodes=nodes,
            booking=booking,
            meeting_type=meeting_type,
            vendor=vendor,
            actor=actor,
            source=source,
            crm=crm or self.crm(room_id),
        )

    def writeback_for_path(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        crm: LocalCrm | None = None,
        meeting_type_id: str | None = None,
    ) -> dict[str, Any]:
        """The researched automation: find the flow for the path, then run it.

        "writes fire on the scheduled, not-scheduled and disqualified paths
        automatically" - so a caller that only knows the booking does not have to
        know which flow serves it. The path comes from the booking, and a path
        with no flow is a :class:`NotConfigured`, not a silent no-op.
        """
        if not isinstance(payload, Mapping):
            raise InvalidConfig("a booking must be an object")
        path = normalise_path(payload.get("path"))
        wanted_type = meeting_type_id or payload.get("meeting_type_id")
        flow = self.flow_for(
            room_id, path=path, meeting_type_id=str(wanted_type) if wanted_type else None
        )
        return self.writeback(
            room_id, str(flow["id"]), payload, actor=actor, source=source, crm=crm
        )

    def _run(
        self,
        *,
        room: Mapping[str, Any],
        flow: Mapping[str, Any],
        nodes: Sequence[Mapping[str, Any]],
        booking: Mapping[str, Any],
        meeting_type: Mapping[str, Any],
        vendor: str,
        actor: str | None,
        source: str,
        crm: LocalCrm,
    ) -> dict[str, Any]:
        """The whole run: every node, in declared order, and one record of it."""
        started = utcnow()
        room_id = str(room["id"])
        # The nodes and the history rows need the meeting type's own id, and the
        # run record needs its name and its Cal event type, so the data the run
        # carries is the record's payload plus its id. Carrying the id inside the
        # payload is what lets every downstream write name the meeting type it
        # belongs to without a second lookup.
        meeting_type_data = {**dict(meeting_type.get("data") or {}), "id": str(meeting_type["id"])}
        steps: list[NodeResult] = []

        if not sync_enabled(meeting_type_data):
            message = sync_toggle_message(str(meeting_type_data.get("name") or ""))
            steps.append(
                NodeResult(
                    "sync_to_crm",
                    -1,
                    OUTCOME_SKIPPED,
                    reason=REASON_MEETING_TYPE_SYNC_OFF,
                    message=message,
                )
            )
            return self._write_run(
                run_id=new_id(RUN_COLLECTION),
                room_id=room_id,
                flow=flow,
                booking=booking,
                meeting_type=meeting_type_data,
                vendor=vendor,
                steps=steps,
                record=None,
                related={},
                created_events=[],
                deleted_events=[],
                ok=False,
                actionable={
                    "reason": REASON_MEETING_TYPE_SYNC_OFF,
                    "message": message,
                    "node": "sync_to_crm",
                },
                started=started,
                actor=actor,
                source=source,
            )

        by_name = {str(node.get("node")): node for node in nodes}
        record: dict[str, Any] | None = None
        related: dict[str, Any] = {}
        created_events: list[str] = []
        # The run's id is minted before the first node so every Events History row
        # the nodes write can point at the run that produced it. The run *record*
        # is still written once, at the end - a run row that appeared before the
        # work and was then updated would put two audit rows on every run and make
        # a reader wonder which of them described the work.
        run_id = new_id(RUN_COLLECTION)

        for position, name in enumerate(plan_order(nodes)):
            node = by_name[name]
            if name in ANCHOR_NODES:
                result, record = self._run_anchor(
                    node=node,
                    position=position,
                    booking=booking,
                    vendor=vendor,
                    crm=crm,
                    source=source,
                    room_id=room_id,
                    actor=actor,
                )
            elif name == "related_object":
                result, related = self._run_related(
                    node=node,
                    position=position,
                    record=record,
                    booking=booking,
                    crm=crm,
                    source=source,
                )
            elif name in EVENT_NODE.values():
                result, made = self._run_event(
                    node=node,
                    position=position,
                    record=record,
                    related=related,
                    booking=booking,
                    meeting_type=meeting_type_data,
                    vendor=vendor,
                    crm=crm,
                    source=source,
                    room_id=room_id,
                    actor=actor,
                    run_ref=run_id,
                    flow_id=str(flow["id"]),
                )
                created_events.extend(made)
            elif name in FIELD_NODE.values():
                result = self._run_fields(
                    node=node,
                    position=position,
                    record=record,
                    booking=booking,
                    crm=crm,
                    source=source,
                )
            elif name == "add_to_campaign":
                result = self._run_campaign(
                    node=node, position=position, record=record, crm=crm, source=source
                )
            elif name == "update_ownership":
                result = self._run_ownership(
                    node=node,
                    position=position,
                    record=record,
                    booking=booking,
                    crm=crm,
                    source=source,
                )
            else:  # pragma: no cover - validate_nodes refuses anything else
                result = NodeResult(
                    name,
                    position,
                    OUTCOME_SKIPPED,
                    reason="unknown_node",
                    message=f"node {name!r} is not one this build executes",
                )
            steps.append(result)

        deleted_events = self._honour_delete_event(
            event_node=by_name.get(EVENT_NODE[vendor]),
            steps=steps,
            created_events=created_events,
            crm=crm,
            source=source,
        )
        # A run that produced no record is not ok, however gracefully every node
        # skipped. That is the whole point of the catch-all: a flow whose fourth
        # node has nothing to write to must not report success, or the rep finds
        # out when the CRM has no meeting on it.
        ok = all(step.outcome != OUTCOME_FAILED for step in steps) and _produced_record(
            record, nodes
        )
        return self._write_run(
            run_id=run_id,
            room_id=room_id,
            flow=flow,
            booking=booking,
            meeting_type=meeting_type_data,
            vendor=vendor,
            steps=steps,
            record=record,
            related=related,
            created_events=created_events,
            deleted_events=deleted_events,
            ok=ok,
            actionable=_actionable(steps),
            started=started,
            actor=actor,
            source=source,
        )

    # -- the anchor node ---------------------------------------------------- #

    def _run_anchor(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        booking: Mapping[str, Any],
        vendor: str,
        crm: LocalCrm,
        source: str,
        room_id: str,
        actor: str | None,
    ) -> tuple[NodeResult, dict[str, Any] | None]:
        """The create-or-update node: match by email, then update and/or create.

        [sourced] "Chooses whether to **update the matched record** (Update matched
        Contact or Lead / Only update matched Lead) and whether to **create** (Create
        Contact or Lead / Create Lead / Always create Lead)."

        The three-way reading of those six labels is the whole of this method:

        * ``update`` decides whether the *matched* record is written to, and
          ``matched_lead_only`` narrows that to a Lead.
        * ``create`` decides what happens when *nothing* matched - and
          ``always_lead`` is the one branch that also fires when something did.
        """
        email = str(booking["booker"].get("email") or "")
        order = MATCH_ORDER[vendor]
        match = crm.match_by_email(email, record_types=order, source=source)
        matched: dict[str, Any] | None = match["matched"]
        matched_type = str(match.get("matched_type") or "")
        resolved: dict[str, Any] = {
            "email": email,
            "searched": list(match["searched"]),
            "match_reason": match["reason"],
            "matched_type": matched_type,
            "update_branch": str(node.get("update")),
            "create_branch": str(node.get("create")),
            "l2a": bool(node.get("l2a")),
        }

        if matched is not None and matched_type == TYPE_LEAD and node.get("l2a"):
            l2a = self._apply_l2a(matched, booking=booking, vendor=vendor, crm=crm, source=source)
            resolved["l2a"] = l2a
            if l2a.get("account_crm_id"):
                matched = (
                    crm.update(
                        matched["data"]["crm_id"],
                        {"account_id": l2a["account_crm_id"]},
                        source=source,
                    )
                    or matched
                )
        elif node.get("l2a"):
            # The flow asked for L2A and it did not happen. Saying so beats leaving
            # `l2a: true` on a run that never applied it.
            resolved["l2a"] = {
                "applied": False,
                "reason": (
                    "l2a_is_a_salesforce_rule"
                    if vendor != SALESFORCE
                    else "l2a_needs_a_matched_lead"
                ),
            }

        # -- the update branch
        wrote: list[dict[str, Any]] = []
        if matched is None:
            resolved["update_outcome"] = "nothing_matched"
        elif str(node.get("update")) == UPDATE_MATCHED_LEAD and matched_type != TYPE_LEAD:
            # [sourced] "Only update matched Lead" - a Contact match is not updated.
            resolved["update_outcome"] = "skipped_lead_only"
            resolved["updated"] = []
        else:
            wrote = self._write_fields(
                matched, node.get("fields") or [], booking=booking, crm=crm, source=source
            )
            resolved["updated"] = wrote
            resolved["update_outcome"] = "applied"

        # -- the create branch
        create_branch = str(node.get("create") or CREATE_NONE)
        if matched is None and create_branch != CREATE_NONE:
            try:
                matched = self._create_record(
                    node=node, booking=booking, vendor=vendor, crm=crm, source=source
                )
            except CrmRefused as exc:
                return (
                    NodeResult(
                        str(node.get("node")),
                        position,
                        OUTCOME_FAILED,
                        reason=exc.code,
                        message=str(exc.detail),
                        resolved=resolved,
                    ),
                    None,
                )
            resolved["create_outcome"] = "created"
            resolved["created"] = dict(matched["data"]) if matched else {}
            resolved["written_on_create"] = self._write_fields(
                matched, node.get("fields") or [], booking=booking, crm=crm, source=source
            )
        elif matched is not None and create_branch == CREATE_ALWAYS_LEAD:
            # [sourced] "Always create Lead" - a Lead even though something matched.
            # This is the branch lost by collapsing it into "Create Lead", so it
            # gets its own outcome and its own test.
            try:
                created = self._create_record(
                    node=node, booking=booking, vendor=vendor, crm=crm, source=source
                )
            except CrmRefused as exc:
                return (
                    NodeResult(
                        str(node.get("node")),
                        position,
                        OUTCOME_FAILED,
                        reason=exc.code,
                        message=str(exc.detail),
                        resolved=resolved,
                    ),
                    matched,
                )
            resolved["create_outcome"] = "always_created"
            resolved["always_lead"] = dict(created["data"])
            matched = created
        elif matched is None:
            resolved["create_outcome"] = "nothing_matched"

        if matched is None:
            # The catch-all this workflow cannot skip: no record, so every node
            # after this one has nothing to write to. Reported as a skip with the
            # reason, not as a success and not as a crash.
            return (
                NodeResult(
                    str(node.get("node")),
                    position,
                    OUTCOME_SKIPPED,
                    reason=REASON_NO_CREATE,
                    message=(
                        "nothing matched by email and no create branch produced a record. "
                        + NO_RECORD_MESSAGE
                    ),
                    resolved=resolved,
                ),
                None,
            )

        resolved["crm_id"] = matched["data"].get("crm_id")
        resolved["type"] = matched["data"].get("type")
        resolved["fields_written"] = wrote
        if resolved.get("create_outcome") in ("created", "always_created"):
            reason = (
                REASON_ALWAYS_LEAD
                if resolved["create_outcome"] == "always_created"
                else REASON_CREATED
            )
        elif resolved.get("update_outcome") == "applied":
            reason = REASON_UPDATED
        else:
            reason = REASON_MATCHED
        return (
            NodeResult(
                str(node.get("node")),
                position,
                OUTCOME_APPLIED,
                reason=reason,
                crm_id=str(resolved["crm_id"]),
                resolved=resolved,
            ),
            matched,
        )

    def _apply_l2a(
        self,
        lead: Mapping[str, Any],
        *,
        booking: Mapping[str, Any],
        vendor: str,
        crm: LocalCrm,
        source: str,
    ) -> dict[str, Any]:
        """Salesforce's Lead-to-Account matching, applied to a matched Lead.

        [sourced] "matched by email; Salesforce L2A matching applied". L2A is the
        Lead *to Account* conversion, and what it does to this workflow is make an
        Account exist for the Lead - which is what lets a Related Object of
        ``Account`` resolve off a Lead, and what makes the research's "If we have
        found a contact" reachable without changing the match order.

        Salesforce's conversion is manual and irreversible in the product; this
        build does the part the research names and says which part that is.
        """
        if vendor != SALESFORCE:
            return {"applied": False, "reason": "l2a_is_a_salesforce_rule"}
        company = str(lead["data"].get("company") or booking.get("account", {}).get("name") or "")
        if not company:
            return {"applied": False, "reason": "no_company_on_the_lead_to_convert"}
        existing = next(
            (
                row
                for row in crm.of_type(TYPE_ACCOUNT)
                if str(row["data"].get("name") or "").strip().lower() == company.strip().lower()
            ),
            None,
        )
        if existing is not None:
            return {
                "applied": True,
                "matched_existing": True,
                "account_crm_id": existing["data"].get("crm_id"),
                "reason": "l2a_matched_an_existing_account",
            }
        account = crm.create(
            TYPE_ACCOUNT, {"name": company, "source": "l2a"}, source=source, vendor=vendor
        )
        return {
            "applied": True,
            "matched_existing": False,
            "account_crm_id": account["data"].get("crm_id"),
            "reason": "l2a_created_an_account",
        }

    def _create_record(
        self,
        *,
        node: Mapping[str, Any],
        booking: Mapping[str, Any],
        vendor: str,
        crm: LocalCrm,
        source: str,
    ) -> dict[str, Any]:
        """Create the record a create branch names.

        The branch decides the *type* and the node's ``record_type`` says which of
        "Contact or Lead" it is - see the ``create-branches`` inference for why that
        label is read as a branch rather than as a third record type.
        """
        branch = str(node.get("create"))
        if branch in (CREATE_ALWAYS_LEAD, CREATE_LEAD):
            record_type = TYPE_LEAD
        else:
            record_type = TYPE_CONTACT if str(node.get("record_type")) == "contact" else TYPE_LEAD
        return crm.create(
            record_type,
            {
                "name": str(booking["booker"].get("name") or ""),
                "email": str(booking["booker"].get("email") or ""),
                "company": str(booking.get("account", {}).get("name") or ""),
                "status": "Open",
                "source": "create_or_update",
            },
            source=source,
            vendor=vendor,
        )

    def _write_fields(
        self,
        record: Mapping[str, Any],
        fields: Sequence[Mapping[str, Any]],
        *,
        booking: Mapping[str, Any],
        crm: LocalCrm,
        source: str,
    ) -> list[dict[str, Any]]:
        """Write a field map onto a CRM record, resolving Data Field references.

        [sourced] "selected fields updated (e.g. ``Contact.Status = "Sales
        Qualified"``)" and the extensibility claim "Data Fields can be mapped to
        custom CRM fields". So a value is either a literal or the name of a Data
        Field on the booking, and the target property is arbitrary JSON - a team
        adding a field ships a payload, not a migration.
        """
        written: list[dict[str, Any]] = []
        data_fields = dict(booking.get("data_fields") or {})
        merged: dict[str, Any] = {}
        for entry in fields:
            target = str(entry.get("field"))
            if entry.get("from_data_field"):
                key = str(entry["from_data_field"])
                if key not in data_fields:
                    # A missing Data Field is reported, not defaulted. A field map
                    # that silently wrote "" would be indistinguishable from one
                    # that legitimately mapped an empty value.
                    written.append(
                        {
                            "field": target,
                            "from_data_field": key,
                            "written": False,
                            "reason": "data_field_not_on_the_booking",
                        }
                    )
                    continue
                value = data_fields[key]
            else:
                value = entry.get("value")
            merged[target] = value
            written.append(
                {
                    "field": target,
                    "value": value,
                    "from_data_field": entry.get("from_data_field", ""),
                    "written": True,
                }
            )
        if merged:
            crm.update(
                record["data"]["crm_id"],
                {"fields": _merge_fields(record, merged)},
                source=source,
            )
        return written

    # -- the related object node ------------------------------------------- #

    def _run_related(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        record: Mapping[str, Any] | None,
        booking: Mapping[str, Any],
        crm: LocalCrm,
        source: str,
    ) -> tuple[NodeResult, dict[str, Any]]:
        """Resolve the Related Object, and record which rule chose it.

        [sourced] "If we have found a contact, you can additionally relate the Event
        to an **Account**, **Case**, **Opportunity**, or **Campaign**."

        The gate is a *Contact*, not "a record". A matched Lead is not enough, and
        that is the constraint most easily lost, because the paragraph reads
        naturally as though the second relation is merely optional. So a Lead match
        gets a skip naming the sentence, and the Event still gets the researched
        default relation to the Lead.
        """
        object_name = str(node.get("object"))
        record_type = str(node.get("record_type") or object_name)
        if record is None:
            return (
                NodeResult(
                    "related_object",
                    position,
                    OUTCOME_SKIPPED,
                    reason=REASON_NO_RECORD,
                    message=(
                        "there is no record, so there is nothing to relate an Event to. "
                        f"A {object_name} relation needs the record first."
                    ),
                    resolved={"object": object_name},
                ),
                {},
            )
        if str(record["data"].get("type")) != TYPE_CONTACT:
            return (
                NodeResult(
                    "related_object",
                    position,
                    OUTCOME_SKIPPED,
                    reason=RELATED_REQUIRES_CONTACT_QUOTE,
                    message=related_requires_contact_message(str(record["data"].get("type"))),
                    resolved={"object": object_name, "required": TYPE_CONTACT},
                ),
                {},
            )

        if object_name == "Campaign":
            selection = self._select_campaign(str(node.get("campaign") or ""), crm=crm)
        else:
            selection = crm.select_related(
                record_type,
                record=record["data"],
                on=parse_date(booking.get("starts_at")),
                source=source,
            )
        chosen = selection.get("chosen")
        if chosen is None:
            return (
                NodeResult(
                    "related_object",
                    position,
                    OUTCOME_SKIPPED,
                    reason=REASON_NO_CANDIDATE,
                    message=(
                        f'[sourced] "{RELATED_SELECTION_QUOTE}" - this build applied the '
                        f"{selection.get('rule')} rule to {record_type} and found nothing "
                        f"({selection.get('reason')}). The Event keeps the researched "
                        "default relation to the Contact."
                    ),
                    resolved=_selection_summary(selection, object_name),
                ),
                {},
            )
        return (
            NodeResult(
                "related_object",
                position,
                OUTCOME_APPLIED,
                reason=str(selection.get("rule")),
                message=(
                    f'[sourced] "{RELATED_SELECTION_QUOTE}" - chose '
                    f"{chosen['data'].get('crm_id')} by the {selection.get('rule')} rule "
                    f"({selection.get('reason')})."
                ),
                crm_id=str(chosen["data"].get("crm_id")),
                resolved=_selection_summary(selection, object_name),
            ),
            {
                "object": object_name,
                "record_type": record_type,
                "rule": str(selection.get("rule")),
                "reason": str(selection.get("reason")),
                "crm_id": str(chosen["data"].get("crm_id")),
                "candidates": selection.get("candidates", 0),
                "considered": selection.get("considered", 0),
            },
        )

    def _select_campaign(self, wanted: str, *, crm: LocalCrm) -> dict[str, Any]:
        """The Campaign a Related Object of ``Campaign`` names.

        The research gives a selection rule for Cases and for Opportunities and
        names none for Campaigns, and a Campaign has neither an Open status nor a
        Close Date to be near - so the node names one explicitly and this looks it
        up. Anything else would be inventing a rule the research does not state.
        """
        candidates = crm.of_type(TYPE_CAMPAIGN)
        chosen = next(
            (
                row
                for row in candidates
                if wanted
                in (str(row["data"].get("crm_id") or ""), str(row["data"].get("name") or ""))
            ),
            None,
        )
        return {
            "record_type": TYPE_CAMPAIGN,
            "rule": "named_explicitly",
            "chosen": chosen,
            "reason": "named_explicitly" if chosen else "no_campaign_with_that_name",
            "candidates": len(candidates),
            "considered": 1 if chosen else 0,
        }

    # -- the event node ----------------------------------------------------- #

    def _run_event(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        record: Mapping[str, Any] | None,
        related: Mapping[str, Any],
        booking: Mapping[str, Any],
        meeting_type: Mapping[str, Any],
        vendor: str,
        crm: LocalCrm,
        source: str,
        room_id: str,
        actor: str | None,
        run_ref: str,
        flow_id: str,
    ) -> tuple[NodeResult, list[str]]:
        """Create the Event or Engagement, plus a child per additional guest.

        [sourced] "All created Events will be related to the Contact or Lead **by
        default**", and "[sourced] Optionally configures ``Create child Event`` per
        additional guest". The default relation is unconditional - it is the
        record, whether that record is a Contact or a Lead - and the Related
        Object the previous node resolved is attached *on top* of it.

        Every Event, primary or child, gets its own Events History row, because
        "[sourced] Admin later retries any failed CRM Event" is per Event: a child
        that failed has to be retryable on its own, or a rep who fixes one guest's
        address has to re-run the whole booking and duplicate the Events that
        already succeeded.
        """
        if record is None:
            return (
                NodeResult(
                    str(node.get("node")),
                    position,
                    OUTCOME_SKIPPED,
                    reason=REASON_NO_RECORD,
                    message=(
                        "[sourced] All created Events will be related to the Contact or "
                        "Lead by default - and there is no record, so there is nothing to "
                        "create an Event for."
                    ),
                    resolved={"events": []},
                ),
                [],
            )
        event_type = EVENT_RECORD_TYPE[vendor]
        assigned_to = str(node.get("activity_assigned_to") or "")
        owner_identity = identity_for(booking, assigned_to) if assigned_to else {}
        children = list(booking.get("guests") or []) if node.get("child_events") else []
        attendees = [dict(booking["booker"]), *children]
        events: list[dict[str, Any]] = []
        history_ids: list[str] = []

        for index, guest in enumerate(attendees):
            event = self._create_event(
                node=node,
                record=record,
                related=related,
                guest=guest,
                guest_index=index,
                is_child=index > 0,
                booking=booking,
                meeting_type=meeting_type,
                vendor=vendor,
                event_type=event_type,
                owner_identity=owner_identity,
                assigned_to=assigned_to,
                crm=crm,
                source=source,
                room_id=room_id,
                actor=actor,
                run_ref=run_ref,
                flow_id=flow_id,
            )
            events.append(event)
            if event.get("history_id"):
                history_ids.append(str(event["history_id"]))
            if (
                event.get("status") == "failed"
                and str(node.get("delete_event")) == DELETE_EVENT_ON_RETRY
            ):
                # An Event that could not be created leaves nothing to clean, but
                # a *partial* create might. Recorded either way so the history
                # says the setting was in force.
                event["cleaned"] = self._clean(crm_id=event.get("crm_id"), crm=crm, source=source)

        applied = [item for item in events if item.get("status") == "created"]
        failed = [item for item in events if item.get("status") == "failed"]
        created_ids = [str(item["crm_id"]) for item in applied if item.get("crm_id")]
        resolved: dict[str, Any] = {
            "events": events,
            "created": len(applied),
            "failed": len(failed),
            "child_events": len(children),
            "related_object": dict(related),
            "default_relation": REASON_DEFAULT_RELATION,
            "delete_event": str(node.get("delete_event")),
        }
        if assigned_to:
            resolved["activity_assigned_to"] = assigned_to
            resolved["activity_assigned_to_email"] = str(owner_identity.get("email") or "")
        if node.get("child_events") and not children:
            resolved["child_note"] = REASON_NO_GUESTS
        if failed:
            return (
                NodeResult(
                    str(node.get("node")),
                    position,
                    OUTCOME_FAILED,
                    reason=str(failed[0].get("error_code") or "event_creation_failed"),
                    message=str(failed[0].get("error") or ""),
                    crm_id=created_ids[0] if created_ids else "",
                    resolved=resolved,
                    history_ids=history_ids,
                ),
                created_ids,
            )
        return (
            NodeResult(
                str(node.get("node")),
                position,
                OUTCOME_APPLIED,
                reason=REASON_CHILD_EVENT if children else REASON_DEFAULT_RELATION,
                message=(
                    f"created {len(applied)} {event_type} row(s) related to "
                    f"{record['data'].get('crm_id')}"
                    + (f" and {related.get('crm_id')}" if related.get("crm_id") else "")
                ),
                crm_id=created_ids[0] if created_ids else "",
                resolved=resolved,
                history_ids=history_ids,
            ),
            created_ids,
        )

    def _create_event(
        self,
        *,
        node: Mapping[str, Any],
        record: Mapping[str, Any],
        related: Mapping[str, Any],
        guest: Mapping[str, Any],
        guest_index: int,
        is_child: bool,
        booking: Mapping[str, Any],
        meeting_type: Mapping[str, Any],
        vendor: str,
        event_type: str,
        owner_identity: Mapping[str, Any],
        assigned_to: str,
        crm: LocalCrm,
        source: str,
        room_id: str,
        actor: str | None,
        run_ref: str,
        flow_id: str,
    ) -> dict[str, Any]:
        """One Event, and its Events History row - whatever the outcome.

        The history row is written whether the Event was created or refused,
        because that is the researched behaviour and it is the reason a retry is
        possible at all: "[sourced] If the Event failed to be created, we will also
        show when it happened, alongside the detailed error."
        """
        when = utcnow()
        payload = {
            "subject": str(node.get("subject") or booking.get("subject") or ""),
            "start": node.get("start") or booking.get("starts_at"),
            "end": node.get("end") or booking.get("ends_at"),
            "attendee": str(guest.get("email") or ""),
            "related_to": str(record["data"].get("crm_id") or ""),
            "related_object_crm_id": str(related.get("crm_id") or ""),
            "related_object": str(related.get("object") or ""),
            "is_child": is_child,
            "guest_index": guest_index,
            "activity_assigned_to": assigned_to,
            "activity_assigned_to_email": str(owner_identity.get("email") or ""),
            "booking_ref": str(booking.get("booking_ref") or ""),
            "run_ref": run_ref,
        }
        status = "created"
        error = ""
        error_code = ""
        crm_id = ""
        try:
            created = crm.create(event_type, payload, source=source, vendor=vendor)
            crm_id = str(created["data"].get("crm_id") or "")
        except CrmRefused as exc:
            status = "failed"
            error = str(exc.detail)
            error_code = exc.code

        history = self.store.create(
            HISTORY_COLLECTION,
            {
                "when": when,
                "status": status,
                "event_type": event_type,
                "event_type_id": str(meeting_type.get("event_type_id") or ""),
                "meeting_type_id": str(meeting_type.get("id") or ""),
                "meeting_type_name": str(meeting_type.get("name") or ""),
                "vendor": vendor,
                "node": str(node.get("node")),
                "guest": str(guest.get("email") or ""),
                "guest_name": str(guest.get("name") or ""),
                "is_child": is_child,
                "guest_index": guest_index,
                "subject": str(payload["subject"]),
                "start": payload["start"],
                "end": payload["end"],
                "crm_id": crm_id,
                "related_to": payload["related_to"],
                "related_object": payload["related_object"],
                "related_object_crm_id": payload["related_object_crm_id"],
                "activity_assigned_to": assigned_to,
                "activity_assigned_to_email": payload["activity_assigned_to_email"],
                "booking_ref": payload["booking_ref"],
                "run_ref": run_ref,
                "flow_id": flow_id,
                "attempt": 1,
                "retried_from": "",
                "deleted": False,
                "error_code": error_code,
                "error": error,
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return {
            "history_id": history["id"],
            "status": status,
            "crm_id": crm_id,
            "guest": payload["attendee"],
            "guest_name": str(guest.get("name") or ""),
            "is_child": is_child,
            "error": error,
            "error_code": error_code,
            "cleaned": [],
        }

    def _clean(self, *, crm_id: Any, crm: LocalCrm, source: str) -> list[str]:
        """Soft-delete one CRM record, and return the ids it removed.

        Soft, so the Events History row still resolves to the record it names. "The
        Event was created and then deleted" is a different conversation from "the
        Event was never created", and a history that cannot tell them apart is the
        thing this workflow exists to prevent.

        The caller annotates the history row; this only does the removal, so the two
        compensations - on_failure and on_retry - can each say what they removed
        without duplicating the write.
        """
        identifier = str(crm_id or "")
        if not identifier:
            return []
        crm.delete(identifier, source=source)
        return [identifier]

    def _honour_delete_event(
        self,
        *,
        event_node: Mapping[str, Any] | None,
        steps: Sequence[NodeResult],
        created_events: Sequence[str],
        crm: LocalCrm,
        source: str,
    ) -> list[str]:
        """``delete_event: on_failure`` - undo this run's Events if a later node failed.

        The researched name is given but its trigger is not, and this is one of the
        two readings this build can reach from its own evidence; see the
        ``delete-event-trigger`` inference. The compensation is reported, never
        silent, because a run whose Events vanished is otherwise indistinguishable
        from a run that never made them.
        """
        if event_node is None or str(event_node.get("delete_event")) != DELETE_EVENT_ON_FAILURE:
            return []
        failed = [step for step in steps if step.outcome == OUTCOME_FAILED]
        if not failed or not created_events:
            return []
        deleted: list[str] = []
        for crm_id in created_events:
            deleted.extend(self._clean(crm_id=crm_id, crm=crm, source=source))
        # The history rows are annotated so Events History shows the meeting was
        # taken away rather than quietly losing it.
        history_ids = [str(history_id) for step in steps for history_id in step.history_ids]
        for history_id in history_ids:
            row = self.store.get(history_id)
            if row is None or row["collection"] != HISTORY_COLLECTION:
                continue
            if str(row["data"].get("status")) != "created":
                continue
            self.store.update(
                history_id,
                {
                    "deleted": True,
                    "deleted_crm_id": ", ".join(deleted),
                    "deleted_reason": REASON_DELETED,
                },
                actor="crm",
                source=source,
            )
        return deleted

    # -- the field, campaign and ownership nodes ---------------------------- #

    def _run_fields(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        record: Mapping[str, Any] | None,
        booking: Mapping[str, Any],
        crm: LocalCrm,
        source: str,
    ) -> NodeResult:
        if record is None:
            return NodeResult(
                str(node.get("node")),
                position,
                OUTCOME_SKIPPED,
                reason=REASON_NO_RECORD,
                message="Update Field / Update Property writes to the record the create node produced, and there is none.",
            )
        written = self._write_fields(
            record, node.get("fields") or [], booking=booking, crm=crm, source=source
        )
        unwriteable = [item for item in written if not item.get("written")]
        return NodeResult(
            str(node.get("node")),
            position,
            OUTCOME_APPLIED,
            reason=REASON_FIELDS_WRITTEN,
            message=f"wrote {len(written) - len(unwriteable)} of {len(written)} field(s)",
            crm_id=str(record["data"].get("crm_id") or ""),
            resolved={"written": written},
        )

    def _run_campaign(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        record: Mapping[str, Any] | None,
        crm: LocalCrm,
        source: str,
    ) -> NodeResult:
        """Add a CampaignMember, or update the one that is already there.

        [sourced] "CampaignMember created/updated with status ``Booked``". The
        status is fixed by the research, and the flow's normaliser refuses any other
        value rather than storing one the CRM would not agree with.
        """
        if record is None:
            return NodeResult(
                "add_to_campaign",
                position,
                OUTCOME_SKIPPED,
                reason=REASON_NO_RECORD,
                message="a CampaignMember is a relation between a Campaign and a record, and there is no record.",
            )
        existing = self._find_campaign_member(crm, record, node)
        fields = {
            "member_crm_id": str(record["data"].get("crm_id") or ""),
            "status": CAMPAIGN_MEMBER_STATUS,
            "campaign": str(node.get("campaign") or ""),
        }
        try:
            if existing is not None:
                updated = crm.update(existing["data"]["crm_id"], fields, source=source)
                outcome = "updated"
            else:
                updated = crm.create(TYPE_CAMPAIGN_MEMBER, fields, source=source)
                outcome = "created"
        except CrmRefused as exc:
            return NodeResult(
                "add_to_campaign",
                position,
                OUTCOME_FAILED,
                reason=exc.code,
                message=str(exc.detail),
            )
        return NodeResult(
            "add_to_campaign",
            position,
            OUTCOME_APPLIED,
            reason=REASON_CAMPAIGN_MEMBER,
            message=f"{outcome} CampaignMember with status {CAMPAIGN_MEMBER_STATUS}",
            crm_id=str(updated["data"].get("crm_id") or ""),
            resolved={**fields, "outcome": outcome},
        )

    def _find_campaign_member(
        self, crm: LocalCrm, record: Mapping[str, Any], node: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """The CampaignMember this flow already made, if any - the "updated" half.

        Keyed on the member record and the campaign, because that is the pair
        Salesforce's CampaignMember is unique on. A second booking for the same
        person in the same campaign updates the member rather than creating a
        duplicate - which is what "[sourced] created/**updated**" asks for.
        """
        wanted_campaign = str(node.get("campaign") or "")
        member_id = str(record["data"].get("crm_id") or "")
        for row in crm.of_type(TYPE_CAMPAIGN_MEMBER):
            data = row["data"]
            if str(data.get("member_crm_id") or "") != member_id:
                continue
            if wanted_campaign and str(data.get("campaign") or "") != wanted_campaign:
                continue
            return row
        return None

    def _run_ownership(
        self,
        *,
        node: Mapping[str, Any],
        position: int,
        record: Mapping[str, Any] | None,
        booking: Mapping[str, Any],
        crm: LocalCrm,
        source: str,
    ) -> NodeResult:
        """Reassign the record's Owner, with Cal's two researched fallbacks.

        [sourced] "record Owner reassigned to the assignee" and "ownership can be
        transferred to whoever took the meeting", so the assignee is the default.

        Then Cal's two settings, which the research names but does not define:

        * ``routing.skipContactOwner`` - "Whether to skip contact owner assignment
          from CRM integration". True means the CRM's own owner is not consulted
          and the node writes the assignee outright.
        * ``crmRecordOwnerFallbackMode`` - ``relationship`` takes the owner from
          the record the matched record hangs off; ``attributeRules`` takes the
          first rule whose field matches. When neither resolves, the assignee is
          the last resort - and a run that resolved nothing says so rather than
          writing nobody.
        """
        if record is None:
            return NodeResult(
                "update_ownership",
                position,
                OUTCOME_SKIPPED,
                reason=REASON_NO_RECORD,
                message="Update Ownership reassigns the record the create node produced, and there is none.",
            )
        resolved: dict[str, Any] = {
            "assign_to": str(node.get("assign_to")),
            "fallback_mode": str(node.get("fallback_mode")),
            "skip_contact_owner": bool(node.get("skip_contact_owner")),
            "crm_app_slug": str(node.get("crm_app_slug") or ""),
            "crm_owner_record_type": str(node.get("crm_owner_record_type") or ""),
        }
        identity = identity_for(booking, str(node.get("assign_to")))
        resolved["crm_owner_considered"] = str(record["data"].get("owner") or "")
        if bool(node.get("skip_contact_owner")):
            resolved["branch"] = REASON_OWNER_SKIPPED
            resolved["note"] = (
                "[sourced] routing.skipContactOwner - 'Whether to skip contact owner "
                "assignment from CRM integration' - so the CRM's own owner was not "
                "consulted and the node wrote the assignee outright."
            )
            chosen = str(identity.get("email") or "")
        elif identity.get("email"):
            # [sourced] "record Owner reassigned to the assignee". The assignee is
            # the *first* answer, not the last: a fallback that outranked it would
            # mean the researched sentence never happened on any record that has a
            # related one, which is most of them.
            resolved["branch"] = str(node.get("assign_to"))
            chosen = str(identity["email"])
        else:
            fallback = self._fallback_owner(node, record=record, crm=crm)
            resolved["fallback"] = fallback
            if fallback.get("owner"):
                resolved["branch"] = f"fallback_{node.get('fallback_mode')}"
                chosen = str(fallback["owner"])
            elif record["data"].get("owner"):
                resolved["branch"] = "crm_existing_owner"
                chosen = str(record["data"]["owner"])
            else:
                chosen = ""

        if not chosen:
            return NodeResult(
                "update_ownership",
                position,
                OUTCOME_SKIPPED,
                reason=REASON_OWNER_NONE,
                message=(
                    "no owner resolved: the fallback matched nothing, the record has no "
                    "owner, and the booking carries no identity with that email."
                ),
                crm_id=str(record["data"].get("crm_id") or ""),
                resolved=resolved,
            )
        if chosen == str(record["data"].get("owner") or ""):
            return NodeResult(
                "update_ownership",
                position,
                OUTCOME_SKIPPED,
                reason=REASON_OWNER_UNCHANGED,
                message=f"{chosen} already owns this record, so no write was made.",
                crm_id=str(record["data"].get("crm_id") or ""),
                resolved={**resolved, "owner": chosen},
            )
        crm.update(record["data"]["crm_id"], {"owner": chosen}, source=source)
        return NodeResult(
            "update_ownership",
            position,
            OUTCOME_APPLIED,
            reason=REASON_OWNER_FALLBACK
            if "fallback_" in resolved.get("branch", "")
            else REASON_OWNERSHIP,
            message=f"owner set to {chosen} by the {resolved.get('branch')} branch",
            crm_id=str(record["data"].get("crm_id") or ""),
            resolved={**resolved, "owner": chosen},
        )

    def _fallback_owner(
        self,
        node: Mapping[str, Any],
        *,
        record: Mapping[str, Any],
        crm: LocalCrm,
    ) -> dict[str, Any]:
        """Cal's ``crmRecordOwnerFallbackMode``, both branches.

        ``attributeRules`` walks the declared rules in order and takes the first
        whose ``equals`` matches a field on the record. ``relationship`` reads the
        owner of the record the matched record hangs off - its Account, or its
        Company on HubSpot.

        Neither is a fallback to "nobody": when both come back empty the assignee
        is the last resort, which is the researched behaviour ("ownership can be
        transferred to whoever took the meeting").
        """
        mode = str(node.get("fallback_mode"))
        if mode == OWNER_FALLBACK_ATTRIBUTE_RULES:
            fields = dict(record["data"].get("fields") or {})
            for rule in node.get("attribute_rules") or []:
                target = str(rule.get("field"))
                expected = rule.get("equals")
                if target in fields and (expected is None or fields[target] == expected):
                    return {
                        "owner": str(rule.get("owner")),
                        "via": "attributeRules",
                        "rule": {"field": target, "equals": expected},
                    }
            return {"owner": "", "via": "attributeRules", "reason": "no_rule_matched"}
        related_id = str(record["data"].get("account_id") or "")
        if not related_id:
            return {"owner": "", "via": "relationship", "reason": "no_related_record"}
        related = crm.get(related_id)
        if related is None:
            return {"owner": "", "via": "relationship", "reason": "related_record_not_found"}
        owner = str(related["data"].get("owner") or "")
        return {
            "owner": owner,
            "via": "relationship",
            "related_crm_id": related_id,
            "reason": "related_record_owner" if owner else "related_record_has_no_owner",
        }

    # -- writing the run ---------------------------------------------------- #

    def _write_run(
        self,
        *,
        run_id: str,
        room_id: str,
        flow: Mapping[str, Any],
        booking: Mapping[str, Any],
        meeting_type: Mapping[str, Any],
        vendor: str,
        steps: Sequence[NodeResult],
        record: Mapping[str, Any] | None,
        related: Mapping[str, Any],
        created_events: Sequence[str],
        deleted_events: Sequence[str],
        ok: bool,
        actionable: Mapping[str, Any] | None,
        started: str,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """One run record, with a count per outcome and the single error shown."""
        counts = {outcome: 0 for outcome in OUTCOMES}
        for step in steps:
            counts[step.outcome] = counts.get(step.outcome, 0) + 1
        data = {
            "flow_id": str(flow["id"]),
            "flow_name": str(flow["data"].get("name") or ""),
            "vendor": vendor,
            "path": str(booking["path"]),
            "meeting_type_id": str(meeting_type.get("id") or ""),
            "meeting_type_name": str(meeting_type.get("name") or ""),
            "event_type_id": str(meeting_type.get("event_type_id") or ""),
            "booking_ref": str(booking.get("booking_ref") or ""),
            "booking": dict(booking),
            "ok": bool(ok),
            "counts": counts,
            "record": {
                "crm_id": str((record or {}).get("data", {}).get("crm_id") or ""),
                "type": str((record or {}).get("data", {}).get("type") or ""),
            },
            "related": dict(related),
            "steps": [step.to_dict() for step in steps],
            "created_events": list(created_events),
            "deleted_events": list(deleted_events),
            "actionable_error": dict(actionable) if actionable else None,
            "started_at": started,
            "ordering_quote": ORDERING_QUOTE,
        }
        return self.store.create(
            RUN_COLLECTION, data, record_id=run_id, room_id=room_id, actor=actor, source=source
        )

    # -- the run log -------------------------------------------------------- #

    def runs(
        self,
        room_id: str,
        *,
        path: str | None = None,
        meeting_type_id: str | None = None,
        ok: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Every run attempted for this room, newest first.

        A failed run is a row, not a gap: the failure is the thing a rep has to
        read. The filters are JSON paths in each row's own payload, resolved
        through the dynamic index, so a new router path needs no change here.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if path:
            where["path"] = normalise_path(path)
        if meeting_type_id:
            where["meeting_type_id"] = str(meeting_type_id)
        if ok is not None:
            where["ok"] = bool(ok)
        return self._scoped(RUN_COLLECTION, room_id, where, limit=limit)

    # -- Events History ----------------------------------------------------- #

    def history(
        self,
        room_id: str,
        *,
        status: str | None = None,
        event_type_id: str | None = None,
        meeting_type_id: str | None = None,
        booking_ref: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Meetings Activity → Events History, newest first.

        [sourced] "If the Event is successfully created, we will show when it
        happened. If the Event failed to be created, we will also show when it
        happened, alongside the detailed error." So ``when`` is on every row
        regardless of ``status``, and a failure carries its ``error`` beside it.

        The filters are the researched ones: Cal's sync-error list is per event
        type, so ``event_type_id`` scopes it the way
        ``GET /v2/event-types/{id}/crm-sync-errors`` does.
        """
        self.require_room(room_id)
        where: dict[str, Any] = {}
        if status:
            where["status"] = str(status)
        if event_type_id:
            where["event_type_id"] = str(event_type_id)
        if meeting_type_id:
            where["meeting_type_id"] = str(meeting_type_id)
        if booking_ref:
            where["booking_ref"] = str(booking_ref)
        return self._scoped(HISTORY_COLLECTION, room_id, where, limit=limit)

    def sync_errors(self, room_id: str, event_type_id: str) -> list[dict[str, Any]]:
        """Cal's per-event-type CRM sync errors.

        [sourced] ``GET /v2/event-types/{id}/crm-sync-errors`` - "List CRM sync
        errors for an event type". The same rows as the history, narrowed to the
        failures, because that is what the vendor's endpoint returns and the
        research names it as *the* error surface rather than a second one.
        """
        if not event_type_id:
            raise InvalidConfig("an event type is required to list its CRM sync errors")
        return self.history(room_id, status="failed", event_type_id=event_type_id)

    def retry(
        self,
        room_id: str,
        history_id: str,
        *,
        actor: str | None,
        source: str,
        crm: LocalCrm | None = None,
    ) -> dict[str, Any]:
        """Retry one failed Event, from Meetings Activity → Events History.

        [sourced] "Admin later retries any failed CRM Event". The retry re-runs the
        Event for **that** Event only - a child that failed is retryable on its own,
        without re-running the booking and duplicating the Events that already
        succeeded.

        It appends a new history row rather than rewriting the old one. A history
        that rewrites itself is not a history: "[sourced] If the Event failed to be
        created, we will also show when it happened, alongside the detailed error" is
        a statement about the *first* attempt, and the retry's own ``when`` is the
        second.
        """
        row = self.history_row(room_id, history_id)
        data = dict(row["data"])
        if str(data.get("status")) != "failed":
            # The research offers retry on a failure and says nothing about
            # retrying a success, so this refuses rather than inventing a
            # "re-create" the vendor does not document.
            return {
                "retried": False,
                "reason": REASON_RETRY_NOT_FAILED,
                "message": (
                    f'[sourced] "{HISTORY_RETRY_QUOTE}" - Events History offers retry on a '
                    "failure, and this Event was created. Re-running it would create a "
                    "second Event, which is not a retry."
                ),
                "history": row,
            }
        run_record = self.run(room_id, str(data.get("run_ref") or ""))
        flow = self.flow(str(data.get("flow_id") or ""), room_id=room_id)
        self.meeting_type(str(data.get("meeting_type_id") or ""), room_id=room_id)
        vendor = str(flow["data"].get("vendor") or data.get("vendor") or SALESFORCE)
        event_type = str(data.get("event_type") or EVENT_RECORD_TYPE[vendor])
        client = crm or self.crm(room_id)
        event_node = _event_node_of(flow, vendor)
        cleaned = self._clean_on_retry(data, node=event_node, crm=client, source=source)

        when = utcnow()
        created_id = ""
        error = ""
        error_code = ""
        status = "created"
        try:
            created = client.create(
                event_type,
                {
                    "subject": str(data.get("subject") or ""),
                    "start": data.get("start"),
                    "end": data.get("end"),
                    "attendee": str(data.get("guest") or ""),
                    "related_to": str(data.get("related_to") or ""),
                    "related_object_crm_id": str(data.get("related_object_crm_id") or ""),
                    "is_child": bool(data.get("is_child")),
                    "activity_assigned_to": str(data.get("activity_assigned_to") or ""),
                    "activity_assigned_to_email": str(data.get("activity_assigned_to_email") or ""),
                    "source": "retry",
                },
                source=source,
                vendor=vendor,
            )
            created_id = str(created["data"].get("crm_id") or "")
        except CrmRefused as exc:
            status = "failed"
            error = str(exc.detail)
            error_code = exc.code

        attempt = int(data.get("attempt") or 1) + 1
        new_row = self.store.create(
            HISTORY_COLLECTION,
            {
                **{key: value for key, value in data.items() if key != "deleted"},
                "when": when,
                "status": status,
                "crm_id": created_id,
                "attempt": attempt,
                "retried_from": str(row["id"]),
                "deleted": False,
                "cleaned_previous": cleaned,
                "error": error,
                "error_code": error_code,
                "run_ref": str(run_record["id"]),
                "booking_ref": str(
                    run_record["data"].get("booking_ref") or data.get("booking_ref") or ""
                ),
            },
            room_id=str(row["room_id"] or room_id),
            actor=actor,
            source=source,
        )
        if status == "failed":
            # The original failure row now knows it was retried and failed again.
            self.store.update(
                str(row["id"]),
                {"retried": True, "last_attempt": attempt},
                actor=actor,
                source=source,
            )
        return {
            "retried": True,
            "status": status,
            "attempt": attempt,
            "retried_from": str(row["id"]),
            "cleaned_previous": cleaned,
            "crm_id": created_id,
            "error": error,
            "error_code": error_code,
            "history": new_row,
        }

    def _clean_on_retry(
        self,
        data: Mapping[str, Any],
        *,
        node: Mapping[str, Any] | None,
        crm: LocalCrm,
        source: str,
    ) -> list[str]:
        """Clean up after a failed attempt, when the flow asked for it.

        ``delete_event: on_retry`` means the retry deletes whatever the failed
        attempt left before it creates its own. A failure that left nothing behind -
        the normal case, because the refusal is at creation - has nothing to clean,
        and the answer is an empty list rather than an error.
        """
        if node is None or str(node.get("delete_event")) != DELETE_EVENT_ON_RETRY:
            return []
        return self._clean(crm_id=data.get("crm_id"), crm=crm, source=source)

    # -- shared scoping helper ---------------------------------------------- #

    def _scoped(
        self, collection: str, room_id: str, where: Mapping[str, Any], *, limit: int
    ) -> list[dict[str, Any]]:
        """Rows of one collection for one room, filtered by indexed JSON paths.

        ``find()`` resolves dotted paths through the dynamic index but has no room
        filter, so the room is applied here. A run, a history row or a flow is
        always read through a room, and a row whose ``room_id`` is null is only
        visible to the room that created it.
        """
        if where:
            rows = self.store.find(collection, dict(where), limit=limit)
            return [row for row in rows if row["room_id"] in (None, str(room_id))]
        return self.store.list(collection, room_id=str(room_id), limit=limit)


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def _meeting_type_payload(payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name and not partial:
        raise InvalidConfig("a meeting type needs a name")
    data: dict[str, Any] = {}
    if name or not partial:
        data["name"] = name
    for key in ("event_type_id", "vendor", "notes"):
        if key in payload:
            data[key] = payload[key]
    if "sync_to_crm" in payload:
        value = payload["sync_to_crm"]
        if not isinstance(value, bool):
            raise InvalidConfig(
                "sync_to_crm must be true or false. [sourced] it is a per-meeting-type "
                'toggle "applied to all users in your org", not a per-run value'
            )
        data["sync_to_crm"] = value
    elif not partial:
        # Off by default: the research says the settings are admin-defined, so a
        # meeting type nobody opted in writes nothing - visibly, not silently.
        data["sync_to_crm"] = False
    vendor = str(data.get("vendor") or "")
    if vendor and vendor not in VENDORS:
        raise InvalidConfig(f"vendor is {vendor!r}; this build writes to {list(VENDORS)}")
    return data


def _connector_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    vendor = str(payload.get("vendor") or "").strip()
    if vendor not in VENDORS:
        raise InvalidConfig(
            f"a connector needs a vendor this build writes to; known vendors are {list(VENDORS)}"
        )
    data: dict[str, Any] = {
        "vendor": vendor,
        "name": str(payload.get("name") or f"{vendor} connection").strip(),
        "base_url": str(payload.get("base_url") or ""),
        "api_version": str(payload.get("api_version") or ""),
        "global": bool(payload.get("global", True)),
        "enabled": bool(payload.get("enabled", True)),
    }
    if payload.get("token"):
        data["token"] = str(payload["token"])
    return data


def _flow_payload(
    room_id: str, payload: Mapping[str, Any], engine: BookingWriteback, *, partial: bool = False
) -> dict[str, Any]:
    """Validate a declared flow, including the researched ordering rule."""
    data: dict[str, Any] = {}
    vendor = str(payload.get("vendor") or "").strip()
    if vendor and vendor not in VENDORS:
        raise InvalidConfig(f"vendor is {vendor!r}; this build writes to {list(VENDORS)}")
    if not partial:
        if not vendor:
            raise InvalidConfig(
                "a flow needs a vendor: the router's node names differ per CRM, so a "
                "flow that does not say which one cannot be checked against the palette"
            )
        if "path" not in payload:
            raise InvalidConfig("a flow needs the router path it serves")
        if "meeting_type_id" not in payload:
            raise InvalidConfig(
                "a flow needs a meeting type. [sourced] Sync Meeting Type to the CRM is a "
                "meeting-type setting, so the flow that honours it hangs off one"
            )
    if vendor:
        data["vendor"] = vendor
    if "name" in payload:
        data["name"] = str(payload["name"]).strip()
    if "notes" in payload:
        data["notes"] = payload["notes"]
    if "path" in payload:
        data["path"] = normalise_path(payload["path"])
    if "meeting_type_id" in payload:
        meeting_type = engine.meeting_type(str(payload["meeting_type_id"]), room_id=room_id)
        data["meeting_type_id"] = str(payload["meeting_type_id"])
        data["meeting_type_name"] = str(meeting_type["data"].get("name") or "")
        data["event_type_id"] = str(meeting_type["data"].get("event_type_id") or "")
        declared = str(meeting_type["data"].get("vendor") or "")
        if vendor and declared and declared != vendor:
            raise InvalidConfig(
                f"the flow writes to {vendor} but meeting type "
                f"{meeting_type['data'].get('name')!r} is registered to {declared!r}; the "
                "node names differ per CRM, so a flow cannot mix them"
            )
    if "nodes" in payload:
        target_vendor = vendor or str(data.get("vendor") or SALESFORCE)
        nodes = normalise_nodes(payload.get("nodes"), vendor=target_vendor)
        data["nodes"] = nodes
        data["plan"] = describe_plan(nodes, vendor=target_vendor)
    if not partial and not data:
        raise InvalidConfig("a flow needs at least a name, a path, a meeting type or nodes")
    return data


def _selection_summary(selection: Mapping[str, Any], object_name: str) -> dict[str, Any]:
    """The related-object selection, minus the row itself, for the run record."""
    return {
        "object": object_name,
        "record_type": selection.get("record_type"),
        "rule": selection.get("rule"),
        "reason": selection.get("reason"),
        "candidates": selection.get("candidates", 0),
        "considered": selection.get("considered", 0),
    }


def _event_node_of(flow: Mapping[str, Any], vendor: str) -> Mapping[str, Any] | None:
    name = EVENT_NODE[vendor]
    for node in flow["data"].get("nodes") or []:
        if str(node.get("node")) == name:
            return node
    return None


def _actionable(
    steps: Sequence[NodeResult], reason: str = "", message: str = ""
) -> dict[str, Any] | None:
    """The single error a rep is shown for one run.

    A failure outranks a skip, because a failure is the thing that went wrong and a
    skip is often the right answer. When nothing failed but the create node produced
    no record, *that* is the error: it is why nothing was written, and the five
    skips beneath it are consequences. The research says nothing about which of
    several problems to surface, so this build surfaces the earliest in declared
    order, and every per-node outcome stays on the run.
    """
    if reason:
        return {"reason": reason, "message": message, "node": ""}
    for step in steps:
        if step.outcome == OUTCOME_FAILED:
            return {"reason": step.reason, "message": step.message, "node": step.node}
    for step in steps:
        if step.reason == REASON_NO_CREATE:
            return {"reason": step.reason, "message": step.message, "node": step.node}
    return None


def _produced_record(record: Mapping[str, Any] | None, nodes: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the run has the record its downstream nodes needed.

    A flow with no create node at all cannot produce one, and the ordering rule
    means a flow with any other node must have one - so a flow declaring only the
    anchor is the ordinary case and ``record is not None`` is the answer. A flow
    declaring *nothing* is legal and vacuously fine.
    """
    if not any(str(node.get("node")) in ANCHOR_NODES for node in nodes):
        return True
    return record is not None


def _merge_fields(record: Mapping[str, Any], merged: Mapping[str, Any]) -> dict[str, Any]:
    """A CRM record's own ``fields`` object, updated rather than replaced.

    A tenant that has put ``Rating`` on the record keeps it when a flow writes
    ``Status``; a merge that dropped the untouched keys would make a field write
    destructive, which is not what "Update Field" says.
    """
    current = dict(record["data"].get("fields") or {})
    current.update(dict(merged))
    return current


__all__ = [
    "COLLECTIONS",
    "CONNECTOR_COLLECTION",
    "FLOW_COLLECTION",
    "HISTORY_COLLECTION",
    "MEETING_TYPE_COLLECTION",
    "NO_RECORD_MESSAGE",
    "OUTCOMES",
    "OUTCOME_APPLIED",
    "OUTCOME_FAILED",
    "OUTCOME_SKIPPED",
    "RUN_COLLECTION",
    "REASON_MEETING_TYPE_SYNC_OFF",
    "REASON_NO_RECORD",
    "REASON_RETRY_NOT_FAILED",
    "BookingWriteback",
    "NodeResult",
    "identity_for",
    "normalise_booking",
]
